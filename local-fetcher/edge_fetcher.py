from __future__ import annotations

import argparse
import hashlib
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from youtube_transcript_api import NoTranscriptFound, TranscriptsDisabled, YouTubeTranscriptApi


def load_env(path: Path) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"'))


class EdgeClient:
    def __init__(self, api_url: str, token: str) -> None:
        self.api_url = api_url.rstrip("/")
        self.token = token

    def request(self, method: str, path: str, payload: dict | None = None) -> dict | None:
        body = None if payload is None else json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            f"{self.api_url}/api/v1{path}",
            data=body,
            method=method,
            headers={
                "Authorization": f"Bearer {self.token}",
                "Content-Type": "application/json",
                "User-Agent": "courseflow-edge-fetcher/1.0",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                data = response.read()
                if not data:
                    return None
                return json.loads(data.decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"{method} {path} failed with HTTP {exc.code}: {detail}") from exc

    def upload(self, upload_url: str, content: bytes, content_type: str) -> None:
        request = urllib.request.Request(
            upload_url,
            data=content,
            method="PUT",
            headers={"Content-Type": content_type},
        )
        with urllib.request.urlopen(request, timeout=600) as response:
            if response.status not in {200, 201, 204}:
                raise RuntimeError(f"Upload failed with HTTP {response.status}")


def parse_video_id(url: str) -> str | None:
    parsed = urllib.parse.urlparse(url)
    if parsed.netloc.endswith("youtu.be"):
        return parsed.path.strip("/") or None
    return urllib.parse.parse_qs(parsed.query).get("v", [None])[0]


def fetch_captions(youtube_video_id: str) -> tuple[str, list[dict]] | None:
    transcript_list = YouTubeTranscriptApi().list(youtube_video_id)
    try:
        transcript = transcript_list.find_manually_created_transcript(["en"])
    except NoTranscriptFound:
        try:
            transcript = transcript_list.find_generated_transcript(["en"])
        except NoTranscriptFound:
            try:
                transcript = next(iter(transcript_list))
            except StopIteration:
                return None
    fetched = transcript.fetch()
    captions = fetched.to_raw_data() if hasattr(fetched, "to_raw_data") else list(fetched)
    return transcript.language_code, captions


def fetch_playlist_metadata(url: str) -> dict:
    command = [
        sys.executable,
        "-m",
        "yt_dlp",
        "--dump-single-json",
        "--flat-playlist",
        "--skip-download",
        "--ignore-errors",
        "--ignore-no-formats-error",
        "--no-warnings",
        url,
    ]
    result = subprocess.run(command, capture_output=True, text=True, timeout=120, check=False)
    if not result.stdout.strip():
        raise RuntimeError(result.stderr.strip() or "yt-dlp returned no metadata")
    info = json.loads(result.stdout)
    entries = []
    seen = set()
    raw_entries = info.get("entries") or [info]
    for index, entry in enumerate(raw_entries):
        if not entry:
            continue
        youtube_video_id = entry.get("id") or entry.get("url")
        if youtube_video_id and "watch?v=" in youtube_video_id:
            youtube_video_id = parse_video_id(youtube_video_id)
        if not youtube_video_id or youtube_video_id in seen:
            continue
        seen.add(youtube_video_id)
        entries.append(
            {
                "youtube_video_id": str(youtube_video_id),
                "title": entry.get("title") or "Untitled video",
                "position": index,
                "duration_seconds": entry.get("duration"),
            }
        )
    if not entries:
        raise RuntimeError("Playlist contains no available videos")
    parsed = urllib.parse.urlparse(url)
    playlist_id = urllib.parse.parse_qs(parsed.query).get("list", [None])[0] or f"single:{entries[0]['youtube_video_id']}"
    return {
        "course_title": info.get("title") or entries[0]["title"] or "Untitled YouTube Course",
        "playlist_id": playlist_id,
        "entries": entries,
    }


def download_audio(youtube_url: str, target_dir: Path) -> Path:
    output = target_dir / "audio.%(ext)s"
    command = [
        sys.executable,
        "-m",
        "yt_dlp",
        "-x",
        "--audio-format",
        "mp3",
        "--audio-quality",
        "5",
        "--no-playlist",
        "--output",
        str(output),
        youtube_url,
    ]
    result = subprocess.run(command, capture_output=True, text=True, timeout=1800, check=False)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "audio download failed")
    matches = list(target_dir.glob("audio.*"))
    if not matches:
        raise RuntimeError("audio download produced no file")
    return matches[0]


def probe_duration(path: Path) -> float | None:
    result = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(path),
        ],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    try:
        return float(result.stdout.strip()) if result.returncode == 0 else None
    except ValueError:
        return None


def idempotency_for(job: dict, suffix: str) -> str:
    base = f"{job['id']}:{job.get('youtube_video_id') or job.get('youtube_url')}:{suffix}"
    return hashlib.sha256(base.encode("utf-8")).hexdigest()


def process_job(client: EdgeClient, worker_id: str, job: dict) -> None:
    lease_token = job["lease_token"]
    if job["job_type"] == "playlist_metadata":
        metadata = fetch_playlist_metadata(job["youtube_url"])
        client.request(
            "POST",
            f"/edge/jobs/{job['id']}/metadata",
            {
                "lease_token": lease_token,
                "idempotency_key": idempotency_for(job, "metadata"),
                **metadata,
            },
        )
        print(f"Imported metadata for {metadata['course_title']} ({len(metadata['entries'])} videos)")
        return

    youtube_video_id = job.get("youtube_video_id") or parse_video_id(job["youtube_url"])
    if not youtube_video_id:
        raise RuntimeError("Transcript job has no YouTube video ID")

    try:
        captions = fetch_captions(youtube_video_id)
    except TranscriptsDisabled:
        captions = None

    if captions is not None:
        language, segments = captions
        client.request(
            "POST",
            f"/edge/jobs/{job['id']}/captions",
            {
                "lease_token": lease_token,
                "language": language,
                "segments": [
                    {
                        "start": float(segment.get("start", 0)),
                        "duration": float(segment.get("duration", 0)),
                        "text": segment.get("text", ""),
                    }
                    for segment in segments
                ],
                "idempotency_key": idempotency_for(job, f"captions:{language}"),
            },
        )
        print(f"Uploaded captions for {youtube_video_id} ({len(segments)} segments)")
        return

    with tempfile.TemporaryDirectory(prefix="courseflow-edge-audio-") as temp:
        audio_path = download_audio(job["youtube_url"], Path(temp))
        content = audio_path.read_bytes()
        digest = hashlib.sha256(content).hexdigest()
        duration = probe_duration(audio_path)
        init = client.request(
            "POST",
            f"/edge/jobs/{job['id']}/audio-upload",
            {
                "lease_token": lease_token,
                "content_type": "audio/mpeg",
                "size_bytes": len(content),
                "sha256": digest,
                "duration_seconds": duration,
                "idempotency_key": idempotency_for(job, "audio"),
            },
        )
        if init is None:
            raise RuntimeError("Audio upload initialization returned no payload")
        client.upload(init["upload_url"], content, "audio/mpeg")
        client.request(
            "POST",
            f"/edge/jobs/{job['id']}/audio-complete",
            {
                "lease_token": lease_token,
                "object_uri": init["object_uri"],
                "size_bytes": len(content),
                "sha256": digest,
                "duration_seconds": duration,
                "idempotency_key": idempotency_for(job, "audio"),
            },
        )
        print(f"Uploaded fallback audio for {youtube_video_id}")


def run_once(client: EdgeClient, worker_id: str, limit: int) -> int:
    response = client.request("POST", "/edge/jobs/claim", {"worker_id": worker_id, "limit": limit})
    jobs = [] if response is None else response.get("jobs", [])
    for job in jobs:
        try:
            process_job(client, worker_id, job)
            time.sleep(5)
        except Exception as exc:
            print(f"Job {job['id']} failed: {exc}", file=sys.stderr)
            client.request(
                "POST",
                f"/edge/jobs/{job['id']}/fail",
                {
                    "lease_token": job["lease_token"],
                    "error_message": str(exc),
                    "retry_after_seconds": 600,
                    "permanent": False,
                },
            )
    return len(jobs)


def main() -> int:
    parser = argparse.ArgumentParser(description="CourseFlow local YouTube edge fetcher")
    parser.add_argument("--env-file", default=".env.edge-fetcher")
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--poll-seconds", type=int, default=30)
    parser.add_argument("--limit", type=int, default=1)
    args = parser.parse_args()

    load_env(Path(args.env_file))
    api_url = os.environ.get("COURSEFLOW_API_URL", "").strip()
    token = os.environ.get("COURSEFLOW_EDGE_TOKEN", "").strip()
    if not api_url or not token:
        print("Set COURSEFLOW_API_URL and COURSEFLOW_EDGE_TOKEN in .env.edge-fetcher", file=sys.stderr)
        return 2

    client = EdgeClient(api_url, token)
    worker_id = f"{socket.gethostname()}:{os.getpid()}"
    while True:
        processed = run_once(client, worker_id, args.limit)
        if args.once:
            return 0
        time.sleep(5 if processed else args.poll_seconds)


if __name__ == "__main__":
    raise SystemExit(main())
