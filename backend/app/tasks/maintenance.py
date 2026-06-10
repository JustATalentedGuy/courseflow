import asyncio
import threading
import time
from datetime import UTC, datetime, timedelta

import structlog
from sqlalchemy import select

from app.db.session import AsyncSessionLocal
from app.models.video import Video
from app.workers.celery_app import celery_app

logger = structlog.get_logger()


def _run(coro):
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)

    result = {}

    def runner():
        try:
            result["value"] = asyncio.run(coro)
        except Exception as exc:
            result["error"] = exc

    thread = threading.Thread(target=runner)
    thread.start()
    thread.join()
    if "error" in result:
        raise result["error"]
    return result.get("value")


async def _cleanup_stale_processing_videos() -> int:
    threshold = datetime.now(UTC) - timedelta(minutes=30)
    async with AsyncSessionLocal() as db:
        result = await db.scalars(
            select(Video).where(
                Video.status == "processing",
                Video.updated_at < threshold,
            )
        )
        videos = list(result)
        for video in videos:
            video.status = "pending"
            video.celery_task_id = None
        await db.commit()
        return len(videos)


@celery_app.task(bind=True, name="app.tasks.maintenance.cleanup_stale_processing_videos")
def cleanup_stale_processing_videos(self):
    started = time.perf_counter()
    try:
        count = _run(_cleanup_stale_processing_videos())
        logger.info(
            "maintenance.cleanup.finished",
            task_id=self.request.id,
            duration_s=round(time.perf_counter() - started, 3),
            outcome="completed",
            reset_count=count,
        )
        return count
    except Exception:
        logger.exception(
            "maintenance.cleanup.finished",
            task_id=self.request.id,
            duration_s=round(time.perf_counter() - started, 3),
            outcome="failed",
        )
        raise
