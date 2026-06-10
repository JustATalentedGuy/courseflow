from celery import Celery
from celery.schedules import crontab

from app.core.config import settings
from app.core.logging import configure_logging

settings.validate_runtime()
configure_logging()

celery_app = Celery(
    "courseflow",
    broker=settings.redis_url,
    backend=settings.redis_url,
    include=["app.tasks.video_tasks", "app.tasks.maintenance"],
)

celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    enable_utc=True,
    task_acks_late=True,
    worker_prefetch_multiplier=1,
    task_track_started=True,
    task_reject_on_worker_lost=True,
    broker_connection_retry_on_startup=True,
)

celery_app.conf.beat_schedule = {
    "cleanup-stale-processing": {
        "task": "app.tasks.maintenance.cleanup_stale_processing_videos",
        "schedule": crontab(minute=0, hour="*/1"),
    },
}
