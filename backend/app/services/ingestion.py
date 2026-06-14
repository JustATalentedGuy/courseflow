import asyncio
import json
import subprocess
import sys
from dataclasses import dataclass
from urllib.parse import parse_qs, urlparse
from uuid import UUID

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ValidationError
from app.models.course import Course
from app.models.video import Video

logger = structlog.get_logger()

YOUTUBE_HOSTS = {"youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be"}


@dataclass(frozen=True)
class ParsedYouTubeUrl:
    url: str
    playlist_id: str
    video_id: str | None
    is_single_video: bool


def parse_youtube_url(url: str) -> ParsedYouTubeUrl:
    parsed = urlparse(url)
    host = parsed.netloc.lower()
    if host not in YOUTUBE_HOSTS:
        raise ValidationError("URL must be a YouTube playlist or video")

    query = parse_qs(parsed.query)
    playlist_id = query.get("list", [None])[0]
    video_id = query.get("v", [None])[0]

    if host == "youtu.be":
        video_id = parsed.path.strip("/") or video_id

    if parsed.path == "/playlist" and playlist_id:
        return ParsedYouTubeUrl(url=url, playlist_id=playlist_id, video_id=None, is_single_video=False)

    if video_id:
        return ParsedYouTubeUrl(
            url=url,
            playlist_id=playlist_id or f"single:{video_id}",
            video_id=video_id,
            is_single_video=playlist_id is None,
        )

    raise ValidationError("URL must contain a YouTube playlist ID or video ID")


def _extract_youtube_info(url: str) -> dict:
    try:
        result = subprocess.run(
            [
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
            ],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise ValidationError("YouTube metadata request timed out after 30 seconds") from exc
    if not result.stdout.strip():
        raise ValidationError("Unable to read YouTube URL. It may be private or unavailable.")
    try:
        info = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise ValidationError("YouTube returned invalid metadata") from exc
    if not isinstance(info, dict) or not info:
        raise ValidationError("Unable to read YouTube URL. It may be private or unavailable.")
    if result.returncode != 0:
        logger.warning("youtube.metadata.partial", url=url)
    return info


def _video_id_from_entry(entry: dict) -> str | None:
    value = entry.get("id") or entry.get("url")
    if not value:
        return None
    if "watch?v=" in value:
        parsed = urlparse(value)
        return parse_qs(parsed.query).get("v", [None])[0]
    return str(value)


def _normalise_entries(info: dict, parsed_url: ParsedYouTubeUrl) -> list[dict]:
    raw_entries = info.get("entries")
    if raw_entries is None:
        raw_entries = [info]

    entries: list[dict] = []
    seen: set[str] = set()
    skipped_deleted = 0
    skipped_duplicates = 0

    for entry in raw_entries:
        if entry is None:
            skipped_deleted += 1
            continue

        youtube_video_id = _video_id_from_entry(entry)
        if not youtube_video_id:
            skipped_deleted += 1
            continue
        if youtube_video_id in seen:
            skipped_duplicates += 1
            continue

        seen.add(youtube_video_id)
        entries.append(entry)

        if parsed_url.is_single_video:
            break

    if skipped_deleted:
        logger.info("playlist.entries.skipped_unavailable", count=skipped_deleted)
    if skipped_duplicates:
        logger.info("playlist.entries.skipped_duplicates", count=skipped_duplicates)
    if len(entries) > 200:
        logger.warning("playlist.large", count=len(entries))

    return entries


async def ingest_playlist(url: str, user_id: UUID, db: AsyncSession) -> Course:
    parsed_url = parse_youtube_url(url)
    info = await asyncio.to_thread(_extract_youtube_info, url)
    entries = _normalise_entries(info, parsed_url)

    if not entries:
        raise ValidationError("playlist contains no videos")

    course_title = info.get("title") or entries[0].get("title") or "Untitled YouTube Course"
    if parsed_url.is_single_video:
        course_title = entries[0].get("title") or course_title

    course = Course(
        user_id=user_id,
        title=course_title,
        playlist_url=url,
        playlist_id=parsed_url.playlist_id,
        video_count=len(entries),
        status="pending",
    )
    db.add(course)
    await db.flush()

    for position, entry in enumerate(entries):
        video = Video(
            course_id=course.id,
            user_id=user_id,
            youtube_video_id=_video_id_from_entry(entry),
            title=entry.get("title") or "Untitled video",
            position=position,
            duration_seconds=entry.get("duration"),
            status="pending",
        )
        db.add(video)

    await db.commit()
    await db.refresh(course)
    return course
