from datetime import UTC, datetime
from uuid import UUID

import pytest
from sqlalchemy import select

from app.core.exceptions import TranscriptValidationError
from app.models.course import Course
from app.models.transcript import Transcript
from app.models.video import Video
from app.schemas.transcript import NormalisedTranscript, TranscriptSegment
from app.services.transcript import (
    clean_transcript_text,
    fetch_youtube_captions,
    store_transcript,
)


def playlist_info() -> dict:
    return {
        "title": "Tiny Course",
        "entries": [
            {"id": "video-one", "title": "Intro", "duration": 120},
            None,
            {"id": "video-two", "title": "Next Steps"},
            {"id": "video-one", "title": "Duplicate Intro", "duration": 120},
        ],
    }


async def register_and_login(client, email: str) -> str:
    await client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": "password123"},
    )
    response = await client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": "password123"},
    )
    return response.json()["access_token"]


@pytest.mark.asyncio
async def test_valid_playlist_url_creates_course_and_videos(client, monkeypatch):
    monkeypatch.setattr("app.services.ingestion._extract_youtube_info", lambda url: playlist_info())
    token = await register_and_login(client, "phase2@example.com")

    response = await client.post(
        "/api/v1/courses",
        headers={"Authorization": f"Bearer {token}"},
        json={"playlist_url": "https://www.youtube.com/playlist?list=PL123"},
    )

    assert response.status_code == 201
    course = response.json()
    assert course["title"] == "Tiny Course"
    assert course["video_count"] == 2

    detail = await client.get(
        f"/api/v1/courses/{course['id']}",
        headers={"Authorization": f"Bearer {token}"},
    )
    videos = detail.json()["videos"]
    assert [video["youtube_video_id"] for video in videos] == ["video-one", "video-two"]
    assert all(video["status"] == "pending" for video in videos)


@pytest.mark.asyncio
async def test_single_video_url_creates_single_video_course(client, monkeypatch):
    monkeypatch.setattr(
        "app.services.ingestion._extract_youtube_info",
        lambda url: {"id": "abc123", "title": "Single Lesson", "duration": 42},
    )
    token = await register_and_login(client, "single@example.com")

    response = await client.post(
        "/api/v1/courses",
        headers={"Authorization": f"Bearer {token}"},
        json={"playlist_url": "https://youtu.be/abc123"},
    )

    assert response.status_code == 201
    body = response.json()
    assert body["playlist_id"] == "single:abc123"
    assert body["video_count"] == 1


@pytest.mark.asyncio
async def test_invalid_url_returns_422(client):
    token = await register_and_login(client, "invalid@example.com")

    bad_text = await client.post(
        "/api/v1/courses",
        headers={"Authorization": f"Bearer {token}"},
        json={"playlist_url": "not a url"},
    )
    bad_domain = await client.post(
        "/api/v1/courses",
        headers={"Authorization": f"Bearer {token}"},
        json={"playlist_url": "https://vimeo.com/123"},
    )

    assert bad_text.status_code in {400, 422}
    assert bad_domain.status_code in {400, 422}
    assert "YouTube" in bad_domain.json()["detail"]


@pytest.mark.asyncio
async def test_empty_playlist_returns_400(client, monkeypatch):
    monkeypatch.setattr(
        "app.services.ingestion._extract_youtube_info",
        lambda url: {"title": "Empty", "entries": []},
    )
    token = await register_and_login(client, "empty@example.com")

    response = await client.post(
        "/api/v1/courses",
        headers={"Authorization": f"Bearer {token}"},
        json={"playlist_url": "https://www.youtube.com/playlist?list=PLEMPTY"},
    )

    assert response.status_code == 400
    assert "playlist contains no videos" in response.json()["detail"]


def test_youtube_captions_normalise_correctly(monkeypatch):
    class FakeTranscript:
        language_code = "en"

        def fetch(self):
            return [
                {"start": 0.0, "duration": 3.0, "text": "Hello [Music] learners."},
                {
                    "start": 3.0,
                    "duration": 4.0,
                    "text": "This caption has enough words to satisfy transcript quality checks.",
                },
            ]

    class FakeTranscriptList:
        def find_manually_created_transcript(self, languages):
            return FakeTranscript()

    monkeypatch.setattr(
        "app.services.transcript.YouTubeTranscriptApi.list_transcripts",
        lambda video_id: FakeTranscriptList(),
    )

    transcript = fetch_youtube_captions("abc123")

    assert transcript is not None
    assert "[Music]" not in transcript.full_text
    assert transcript.word_count == len(transcript.full_text.split())
    assert all(segment.end > segment.start for segment in transcript.segments)


def test_clean_transcript_removes_annotations():
    raw = "Hello [Music] world [Applause] this is [Laughter] a test"
    cleaned = clean_transcript_text(raw)

    assert "[Music]" not in cleaned
    assert "[Applause]" not in cleaned
    assert "hello world this is a test" in cleaned.lower()


@pytest.mark.asyncio
async def test_transcript_validation_rejects_empty_text(db_session):
    video = await create_video_for_transcript_test(db_session)
    transcript = NormalisedTranscript.model_construct(
        video_id=video.youtube_video_id,
        source="youtube_captions",
        language="en",
        duration_seconds=10,
        segments=[],
        full_text="",
        word_count=0,
        fetched_at=datetime.now(UTC).isoformat(),
    )

    with pytest.raises(TranscriptValidationError):
        await store_transcript(transcript, video, db_session)


@pytest.mark.asyncio
async def test_transcript_validation_rejects_word_count_mismatch(db_session):
    video = await create_video_for_transcript_test(db_session)
    transcript = NormalisedTranscript.model_construct(
        video_id=video.youtube_video_id,
        source="youtube_captions",
        language="en",
        duration_seconds=10,
        segments=[TranscriptSegment(start=0, end=10, text="fifty words", speaker=None)],
        full_text="fifty words",
        word_count=100,
        fetched_at=datetime.now(UTC).isoformat(),
    )

    with pytest.raises(TranscriptValidationError):
        await store_transcript(transcript, video, db_session)


@pytest.mark.asyncio
async def test_user_cannot_access_another_users_course(client, monkeypatch):
    monkeypatch.setattr("app.services.ingestion._extract_youtube_info", lambda url: playlist_info())
    token_a = await register_and_login(client, "owner@example.com")
    token_b = await register_and_login(client, "intruder@example.com")

    created = await client.post(
        "/api/v1/courses",
        headers={"Authorization": f"Bearer {token_a}"},
        json={"playlist_url": "https://www.youtube.com/playlist?list=PL123"},
    )

    response = await client.get(
        f"/api/v1/courses/{created.json()['id']}",
        headers={"Authorization": f"Bearer {token_b}"},
    )

    assert response.status_code in {403, 404}


@pytest.mark.asyncio
async def test_delete_course_cascades(client, db_session, monkeypatch):
    monkeypatch.setattr("app.services.ingestion._extract_youtube_info", lambda url: playlist_info())
    token = await register_and_login(client, "delete@example.com")
    created = await client.post(
        "/api/v1/courses",
        headers={"Authorization": f"Bearer {token}"},
        json={"playlist_url": "https://www.youtube.com/playlist?list=PL123"},
    )
    course_id = UUID(created.json()["id"])
    video = await db_session.scalar(select(Video).where(Video.course_id == course_id))
    assert video is not None
    video_id = video.id

    transcript = valid_transcript(video.youtube_video_id)
    await store_transcript(transcript, video, db_session)

    response = await client.delete(
        f"/api/v1/courses/{course_id}",
        headers={"Authorization": f"Bearer {token}"},
    )

    db_session.expire_all()
    assert response.status_code == 204
    assert await db_session.scalar(select(Course).where(Course.id == course_id)) is None
    assert (await db_session.scalars(select(Video).where(Video.course_id == course_id))).all() == []
    assert (await db_session.scalars(select(Transcript).where(Transcript.video_id == video_id))).all() == []


async def create_video_for_transcript_test(db_session) -> Video:
    from app.models.user import User
    from app.core.security import hash_password

    user = User(email=f"transcript-{datetime.now(UTC).timestamp()}@example.com", hashed_password=hash_password("password123"))
    db_session.add(user)
    await db_session.flush()
    course = Course(
        user_id=user.id,
        title="Transcript Test",
        playlist_url="https://youtu.be/test",
        playlist_id="single:test",
        video_count=1,
    )
    db_session.add(course)
    await db_session.flush()
    video = Video(
        course_id=course.id,
        user_id=user.id,
        youtube_video_id="test-video",
        title="Transcript Test Video",
        position=0,
        status="pending",
    )
    db_session.add(video)
    await db_session.commit()
    await db_session.refresh(video)
    return video


def valid_transcript(youtube_video_id: str) -> NormalisedTranscript:
    segments = [
        TranscriptSegment(
            start=0,
            end=12,
            text="This is a valid transcript segment with enough useful words for downstream processing.",
            speaker=None,
        )
    ]
    full_text = " ".join(segment.text for segment in segments)
    return NormalisedTranscript(
        video_id=youtube_video_id,
        source="youtube_captions",
        language="en",
        duration_seconds=12,
        segments=segments,
        full_text=full_text,
        word_count=len(full_text.split()),
        fetched_at=datetime.now(UTC).isoformat(),
    )
