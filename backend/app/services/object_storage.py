import asyncio
from datetime import timedelta
from urllib.parse import urlparse
from uuid import UUID

from minio import Minio

from app.core.config import settings
from app.core.exceptions import ValidationError


def get_minio_client() -> Minio:
    return Minio(
        settings.minio_endpoint,
        access_key=settings.minio_access_key,
        secret_key=settings.minio_secret_key,
        secure=settings.minio_secure,
    )


async def generate_presigned_url(uri: str, expires_seconds: int = 3600) -> str:
    parsed = urlparse(uri)
    if parsed.scheme != "minio":
        return uri
    object_name = "/".join(part for part in (parsed.netloc, parsed.path.lstrip("/")) if part)
    path_parts = object_name.split("/")
    if len(path_parts) < 2:
        raise ValidationError("MinIO object paths must start with a user ID")
    try:
        UUID(path_parts[0])
    except ValueError as exc:
        raise ValidationError("MinIO object paths must start with a user ID") from exc
    if expires_seconds != 3600:
        raise ValidationError("MinIO presigned URLs must use a one-hour expiry")
    client = get_minio_client()
    return await asyncio.to_thread(
        client.presigned_get_object,
        settings.minio_bucket,
        object_name,
        expires=timedelta(seconds=expires_seconds),
    )
