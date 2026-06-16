import asyncio
from datetime import timedelta
from io import BytesIO
from urllib.parse import urlparse
from uuid import UUID

import boto3
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


def get_s3_client():
    return boto3.client("s3", region_name=settings.aws_region)


def build_object_uri(object_name: str) -> str:
    scheme = "s3" if settings.storage_backend == "s3" else "minio"
    return f"{scheme}://{object_name.lstrip('/')}"


def _object_location(uri: str) -> tuple[str, str]:
    parsed = urlparse(uri)
    if parsed.scheme not in {"minio", "s3"}:
        raise ValidationError("Object URI must use minio:// or s3://")
    object_name = "/".join(part for part in (parsed.netloc, parsed.path.lstrip("/")) if part)
    parts = object_name.split("/")
    if len(parts) < 2:
        raise ValidationError("Object paths must start with a user ID")
    try:
        UUID(parts[0])
    except ValueError as exc:
        raise ValidationError("Object paths must start with a user ID") from exc
    return parsed.scheme, object_name


async def generate_presigned_url(uri: str, expires_seconds: int = 3600) -> str:
    parsed = urlparse(uri)
    if parsed.scheme not in {"minio", "s3"}:
        return uri
    scheme, object_name = _object_location(uri)
    if expires_seconds != 3600:
        raise ValidationError("Object presigned URLs must use a one-hour expiry")
    if scheme == "s3":
        return await asyncio.to_thread(
            get_s3_client().generate_presigned_url,
            "get_object",
            Params={"Bucket": settings.aws_s3_bucket, "Key": object_name},
            ExpiresIn=expires_seconds,
        )
    client = get_minio_client()
    return await asyncio.to_thread(
        client.presigned_get_object,
        settings.minio_bucket,
        object_name,
        expires=timedelta(seconds=expires_seconds),
    )


async def generate_presigned_upload_url(
    uri: str,
    content_type: str,
    expires_seconds: int = 900,
) -> str:
    scheme, object_name = _object_location(uri)
    if expires_seconds > 900:
        raise ValidationError("Upload presigned URLs cannot exceed 15 minutes")
    if scheme == "s3":
        return await asyncio.to_thread(
            get_s3_client().generate_presigned_url,
            "put_object",
            Params={
                "Bucket": settings.aws_s3_bucket,
                "Key": object_name,
                "ContentType": content_type,
            },
            ExpiresIn=expires_seconds,
        )
    return await asyncio.to_thread(
        get_minio_client().presigned_put_object,
        settings.minio_bucket,
        object_name,
        expires=timedelta(seconds=expires_seconds),
    )


async def upload_object(uri: str, content: bytes, content_type: str) -> None:
    scheme, object_name = _object_location(uri)
    if scheme == "s3":
        await asyncio.to_thread(
            get_s3_client().put_object,
            Bucket=settings.aws_s3_bucket,
            Key=object_name,
            Body=content,
            ContentType=content_type,
        )
        return
    client = get_minio_client()
    await asyncio.to_thread(
        client.put_object,
        settings.minio_bucket,
        object_name,
        BytesIO(content),
        len(content),
        content_type=content_type,
    )


async def read_object(uri: str) -> bytes:
    scheme, object_name = _object_location(uri)
    if scheme == "s3":
        response = await asyncio.to_thread(
            get_s3_client().get_object,
            Bucket=settings.aws_s3_bucket,
            Key=object_name,
        )

        def read_s3_body() -> bytes:
            body = response["Body"]
            try:
                return body.read()
            finally:
                body.close()

        return await asyncio.to_thread(read_s3_body)
    client = get_minio_client()

    def read() -> bytes:
        response = client.get_object(settings.minio_bucket, object_name)
        try:
            return response.read()
        finally:
            response.close()
            response.release_conn()

    return await asyncio.to_thread(read)


async def delete_object(uri: str) -> None:
    scheme, object_name = _object_location(uri)
    if scheme == "s3":
        await asyncio.to_thread(
            get_s3_client().delete_object,
            Bucket=settings.aws_s3_bucket,
            Key=object_name,
        )
        return
    await asyncio.to_thread(
        get_minio_client().remove_object,
        settings.minio_bucket,
        object_name,
    )
