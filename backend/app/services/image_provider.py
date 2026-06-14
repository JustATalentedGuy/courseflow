import base64
from dataclasses import dataclass
from io import BytesIO

import httpx
from PIL import Image

from app.core.config import settings
from app.core.exceptions import PermanentAPIError, TemporaryAPIError


@dataclass(frozen=True)
class GeneratedImage:
    content: bytes
    content_type: str
    width: int
    height: int
    request_id: str | None


class ImageProvider:
    async def generate(self, prompt: str, negative_prompt: str | None) -> GeneratedImage:
        raise NotImplementedError


class CloudflareImageProvider(ImageProvider):
    async def generate(self, prompt: str, negative_prompt: str | None) -> GeneratedImage:
        if not settings.cloudflare_account_id or not settings.cloudflare_api_token:
            raise PermanentAPIError("provider_unavailable")
        url = (
            "https://api.cloudflare.com/client/v4/accounts/"
            f"{settings.cloudflare_account_id}/ai/run/{settings.cloudflare_image_model}"
        )
        fields: dict[str, tuple[None, str]] = {
            "prompt": (None, prompt),
            "width": (None, "1024"),
            "height": (None, "768"),
            "steps": (None, "8"),
        }
        if negative_prompt:
            fields["negative_prompt"] = (None, negative_prompt)
        try:
            async with httpx.AsyncClient(timeout=120) as client:
                response = await client.post(
                    url,
                    headers={"Authorization": f"Bearer {settings.cloudflare_api_token}"},
                    files=fields,
                )
        except (httpx.TimeoutException, httpx.NetworkError) as exc:
            raise TemporaryAPIError("Cloudflare request outcome is uncertain; retry explicitly") from exc
        if response.status_code == 429:
            error = TemporaryAPIError("Cloudflare image rate limit reached")
            error.status_code = 429
            error.retry_after = int(float(response.headers.get("retry-after", "60")))
            raise error
        if response.status_code >= 500:
            raise TemporaryAPIError(f"Cloudflare image request failed with {response.status_code}")
        if response.status_code >= 400:
            detail = response.text.strip()[:500]
            raise PermanentAPIError(
                f"Cloudflare image request failed with {response.status_code}: {detail}"
            )
        try:
            body = response.json()
            encoded = body["result"]["image"]
            raw = base64.b64decode(encoded, validate=True)
        except Exception as exc:
            raise PermanentAPIError("Cloudflare returned an invalid image payload") from exc
        if len(raw) > 20 * 1024 * 1024:
            raise PermanentAPIError("Cloudflare image exceeded the 20 MB limit")
        try:
            with Image.open(BytesIO(raw)) as source:
                source.load()
                image = source.convert("RGB")
                image.thumbnail((1024, 768))
                canvas = Image.new("RGB", (1024, 768), "white")
                left = (1024 - image.width) // 2
                top = (768 - image.height) // 2
                canvas.paste(image, (left, top))
                output = BytesIO()
                canvas.save(output, "WEBP", quality=90, method=6)
        except Exception as exc:
            raise PermanentAPIError("Cloudflare returned an unreadable image") from exc
        return GeneratedImage(
            content=output.getvalue(),
            content_type="image/webp",
            width=1024,
            height=768,
            request_id=response.headers.get("cf-ray"),
        )
