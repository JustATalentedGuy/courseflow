import pytest
from sqlalchemy import select

from app.models.course import Course
from app.models.edge import YouTubeEdgeJob
from app.models.transcript import Transcript
from app.models.video import Video


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
async def test_edge_token_is_shown_once_and_can_claim_requeued_transcript(client, db_session):
    access_token = await register_and_login(client, "edge@example.com")
    token_response = await client.post(
        "/api/v1/edge/tokens",
        headers={"Authorization": f"Bearer {access_token}"},
        json={"name": "Laptop"},
    )
    assert token_response.status_code == 201
    edge_token = token_response.json()["token"]

    listed = await client.get(
        "/api/v1/edge/tokens",
        headers={"Authorization": f"Bearer {access_token}"},
    )
    assert listed.status_code == 200
    assert "token" not in listed.json()[0]

    course = Course(
        user_id=token_response.json()["id"],  # overwritten below after loading user-owned course
        title="Edge Course",
        playlist_url="https://www.youtube.com/playlist?list=PLedge",
        playlist_id="PLedge",
        video_count=1,
        status="processing",
    )
    # Use the token row's user by reading the course owner from a normal API-created user.
    from app.models.edge import EdgeFetcherToken

    token_row = await db_session.scalar(select(EdgeFetcherToken))
    course.user_id = token_row.user_id
    db_session.add(course)
    await db_session.flush()
    video = Video(
        course_id=course.id,
        user_id=course.user_id,
        youtube_video_id="video-edge",
        title="Edge video",
        position=0,
        status="failed",
        error_message="YouTube blocks this server IP",
    )
    db_session.add(video)
    await db_session.commit()

    requeue = await client.post(
        f"/api/v1/courses/{course.id}/transcripts/requeue",
        headers={"Authorization": f"Bearer {access_token}"},
    )
    assert requeue.status_code == 200
    assert requeue.json()["queued"] == 1

    claim = await client.post(
        "/api/v1/edge/jobs/claim",
        headers={"Authorization": f"Bearer {edge_token}"},
        json={"worker_id": "test-worker", "limit": 1},
    )
    assert claim.status_code == 200
    job = claim.json()["jobs"][0]
    assert job["job_type"] == "video_transcript"
    assert job["youtube_video_id"] == "video-edge"


@pytest.mark.asyncio
async def test_edge_caption_submission_stores_transcript_and_completes_job(client, db_session, monkeypatch):
    access_token = await register_and_login(client, "edge-captions@example.com")
    token_response = await client.post(
        "/api/v1/edge/tokens",
        headers={"Authorization": f"Bearer {access_token}"},
        json={"name": "Laptop"},
    )
    edge_token = token_response.json()["token"]

    from app.models.edge import EdgeFetcherToken

    token_row = await db_session.scalar(select(EdgeFetcherToken))
    course = Course(
        user_id=token_row.user_id,
        title="Edge Course",
        playlist_url="https://www.youtube.com/playlist?list=PLedge",
        playlist_id="PLedge",
        video_count=1,
        status="processing",
    )
    db_session.add(course)
    await db_session.flush()
    video = Video(
        course_id=course.id,
        user_id=course.user_id,
        youtube_video_id="video-edge",
        title="Edge video",
        position=0,
        status="waiting_for_transcript",
    )
    db_session.add(video)
    await db_session.flush()
    db_session.add(
        YouTubeEdgeJob(
            user_id=course.user_id,
            course_id=course.id,
            video_id=video.id,
            job_type="video_transcript",
            state="pending",
            youtube_url="https://www.youtube.com/watch?v=video-edge",
            youtube_video_id="video-edge",
        )
    )
    await db_session.commit()

    dispatched = {}
    monkeypatch.setattr(
        "app.tasks.video_tasks.process_video_task.delay",
        lambda video_id, user_id: dispatched.update(video_id=video_id, user_id=user_id),
    )

    claim = await client.post(
        "/api/v1/edge/jobs/claim",
        headers={"Authorization": f"Bearer {edge_token}"},
        json={"worker_id": "test-worker", "limit": 1},
    )
    job = claim.json()["jobs"][0]
    submit = await client.post(
        f"/api/v1/edge/jobs/{job['id']}/captions",
        headers={"Authorization": f"Bearer {edge_token}"},
        json={
            "lease_token": job["lease_token"],
            "language": "en",
            "idempotency_key": "caption-once",
            "segments": [
                {"start": 0, "duration": 2, "text": "Hello course."},
                {"start": 2, "duration": 3, "text": "We learn edge fetching today."},
            ],
        },
    )
    assert submit.status_code == 204

    transcript = await db_session.scalar(select(Transcript).where(Transcript.video_id == video.id))
    await db_session.refresh(video)
    job_row = await db_session.get(YouTubeEdgeJob, job["id"])
    assert transcript is not None
    assert transcript.word_count == 7
    assert video.status == "pending"
    assert job_row.state == "completed"
    assert dispatched["video_id"] == str(video.id)
