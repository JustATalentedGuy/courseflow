from io import BytesIO
from uuid import uuid4

import pytest

from app.core.config import settings
from app.services.object_storage import (
    build_object_uri,
    delete_object,
    generate_presigned_url,
    read_object,
    upload_object,
)


class FakeS3Client:
    def __init__(self):
        self.calls: list[tuple[str, dict]] = []

    def generate_presigned_url(self, operation, **kwargs):
        self.calls.append((operation, kwargs))
        return "https://s3.test/presigned"

    def put_object(self, **kwargs):
        self.calls.append(("put_object", kwargs))

    def get_object(self, **kwargs):
        self.calls.append(("get_object", kwargs))
        return {"Body": BytesIO(b"stored-image")}

    def delete_object(self, **kwargs):
        self.calls.append(("delete_object", kwargs))


@pytest.mark.asyncio
async def test_s3_storage_uses_iam_compatible_client(monkeypatch):
    user_id = uuid4()
    fake = FakeS3Client()
    monkeypatch.setattr(settings, "storage_backend", "s3")
    monkeypatch.setattr(settings, "aws_s3_bucket", "courseflow-storage-test")
    monkeypatch.setattr("app.services.object_storage.get_s3_client", lambda: fake)

    uri = build_object_uri(f"{user_id}/video/diagrams/asset/1.webp")
    await upload_object(uri, b"image", "image/webp")
    content = await read_object(uri)
    url = await generate_presigned_url(uri)
    await delete_object(uri)

    assert uri.startswith(f"s3://{user_id}/")
    assert content == b"stored-image"
    assert url == "https://s3.test/presigned"
    assert [name for name, _ in fake.calls] == [
        "put_object",
        "get_object",
        "get_object",
        "delete_object",
    ]
    assert all(
        call["Bucket"] == "courseflow-storage-test"
        for _, call in fake.calls
        if "Bucket" in call
    )
