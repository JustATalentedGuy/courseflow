import asyncio
import threading
from datetime import UTC, datetime, timedelta
from uuid import UUID

import structlog
from sqlalchemy import select

from app.core.exceptions import (
    DiagramQuotaWaitError,
    GroqQuotaWaitError,
    PermanentAPIError,
    TemporaryAPIError,
)
from app.db.session import AsyncSessionLocal
from app.models.diagram import DiagramAsset
from app.services.diagram_service import process_diagram
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


async def _set_state(
    diagram_id: str,
    state: str,
    error: str,
    retry_at: datetime | None = None,
) -> None:
    async with AsyncSessionLocal() as db:
        asset = await db.scalar(
            select(DiagramAsset).where(DiagramAsset.id == UUID(diagram_id))
        )
        if asset is not None:
            asset.state = state
            asset.error_message = error
            asset.retry_at = retry_at
            await db.commit()


@celery_app.task(
    bind=True,
    max_retries=2,
    name="app.tasks.diagram_tasks.process_diagram_task",
    queue="diagrams",
)
def process_diagram_task(self, diagram_id: str):
    try:
        async def run():
            async with AsyncSessionLocal() as db:
                return await process_diagram(db, UUID(diagram_id))

        return _run(run())
    except GroqQuotaWaitError as exc:
        retry_at = datetime.now(UTC) + timedelta(seconds=max(1, exc.retry_after))
        _run(_set_state(diagram_id, "rate_limited", str(exc), retry_at))
        if not celery_app.conf.task_always_eager:
            process_diagram_task.apply_async(args=[diagram_id], eta=retry_at, queue="diagrams")
        return "rate_limited"
    except DiagramQuotaWaitError as exc:
        retry_at = datetime.now(UTC) + timedelta(seconds=max(1, exc.retry_after))
        _run(_set_state(diagram_id, "rate_limited", str(exc), retry_at))
        if not celery_app.conf.task_always_eager:
            process_diagram_task.apply_async(args=[diagram_id], eta=retry_at, queue="diagrams")
        return "rate_limited"
    except TemporaryAPIError as exc:
        if "uncertain" in str(exc).lower() or self.request.retries >= self.max_retries:
            _run(_set_state(diagram_id, "failed", str(exc)))
            return "failed"
        raise self.retry(exc=exc, countdown=min(30 * (2**self.request.retries), 300))
    except PermanentAPIError as exc:
        _run(_set_state(diagram_id, "failed", str(exc)))
        return "failed"
    except Exception as exc:
        logger.exception("diagram.processing.failed", diagram_id=diagram_id)
        _run(_set_state(diagram_id, "failed", str(exc)))
        return "failed"
