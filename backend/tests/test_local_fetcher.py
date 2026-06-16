import importlib.util
import time
from pathlib import Path


def load_fetcher_module():
    path = Path(__file__).resolve().parents[2] / "local-fetcher" / "edge_fetcher.py"
    spec = importlib.util.spec_from_file_location("courseflow_edge_fetcher", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class FakeClient:
    def __init__(self, module, caption_error: bool = False) -> None:
        self.module = module
        self.caption_error = caption_error
        self.requests: list[tuple[str, str, dict | None]] = []
        self.uploads: list[tuple[str, bytes, str]] = []

    def request(self, method: str, path: str, payload: dict | None = None):
        self.requests.append((method, path, payload))
        if path.endswith("/captions") and self.caption_error:
            raise self.module.EdgeHTTPError(
                method,
                path,
                400,
                '{"detail":"Transcript contains no valid speech segments"}',
            )
        if path.endswith("/audio-upload"):
            return {"upload_url": "https://upload.test/audio", "object_uri": "s3://user/video/audio.mp3"}
        return None

    def upload(self, upload_url: str, content: bytes, content_type: str) -> None:
        self.uploads.append((upload_url, content, content_type))


def transcript_job() -> dict:
    return {
        "id": "job-1",
        "lease_token": "lease-1",
        "job_type": "video_transcript",
        "course_id": "course-1",
        "video_id": "video-1",
        "youtube_url": "https://www.youtube.com/watch?v=video-edge",
        "youtube_video_id": "video-edge",
    }


def test_unusable_caption_upload_falls_back_to_audio(tmp_path, monkeypatch):
    module = load_fetcher_module()
    audio = tmp_path / "audio.mp3"
    audio.write_bytes(b"audio-bytes")
    monkeypatch.setattr(module, "fetch_captions", lambda video_id: ("en", [{"start": 0, "duration": 1, "text": "[Music]"}]))
    monkeypatch.setattr(module, "download_audio", lambda url, target_dir: audio)
    monkeypatch.setattr(module, "probe_duration", lambda path: 12.0)
    client = FakeClient(module, caption_error=True)

    module.process_job(client, "worker-1", transcript_job())

    paths = [path for _, path, _ in client.requests]
    assert "/edge/jobs/job-1/captions" in paths
    assert "/edge/jobs/job-1/audio-upload" in paths
    assert "/edge/jobs/job-1/audio-complete" in paths
    assert client.uploads


def test_transcripts_disabled_falls_back_to_audio(tmp_path, monkeypatch):
    module = load_fetcher_module()
    audio = tmp_path / "audio.mp3"
    audio.write_bytes(b"audio-bytes")

    def raise_disabled(video_id):
        raise module.TranscriptsDisabled(video_id)

    monkeypatch.setattr(module, "fetch_captions", raise_disabled)
    monkeypatch.setattr(module, "download_audio", lambda url, target_dir: audio)
    monkeypatch.setattr(module, "probe_duration", lambda path: 12.0)
    client = FakeClient(module)

    module.process_job(client, "worker-1", transcript_job())

    paths = [path for _, path, _ in client.requests]
    assert "/edge/jobs/job-1/audio-upload" in paths
    assert "/edge/jobs/job-1/audio-complete" in paths


def test_request_blocked_uses_long_retry(monkeypatch):
    module = load_fetcher_module()
    client = FakeClient(module)
    monkeypatch.setattr(
        client,
        "request",
        lambda method, path, payload=None: {"jobs": [transcript_job()]} if path.endswith("/claim") else None,
    )
    monkeypatch.setattr(module, "process_job", lambda client, worker_id, job: (_ for _ in ()).throw(module.RequestBlocked("video-edge")))
    recorded = []

    def record_request(method, path, payload=None):
        if path.endswith("/claim"):
            return {"jobs": [transcript_job()]}
        recorded.append(payload)
        return None

    monkeypatch.setattr(client, "request", record_request)

    assert module.run_once(client, "worker-1", 1, 0, 0) == 1
    assert 7200 <= recorded[0]["retry_after_seconds"] <= 21600
    assert recorded[0]["permanent"] is False


def test_heartbeat_loop_extends_active_lease():
    module = load_fetcher_module()
    client = FakeClient(module)
    with module.HeartbeatLoop(client, transcript_job(), "worker-1", interval_seconds=0.01):
        time.sleep(0.04)

    heartbeats = [path for _, path, _ in client.requests if path.endswith("/heartbeat")]
    assert heartbeats
