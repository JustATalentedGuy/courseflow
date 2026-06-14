import re

import httpx

from app.core.config import settings
from app.core.exceptions import PermanentAPIError, TemporaryAPIError
from app.services.image_provider import GeneratedImage

MAX_MERMAID_LENGTH = 12_000
MAX_MERMAID_NODES = 80


def validate_mermaid(source: str) -> str:
    source = source.strip()
    if not source or len(source) > MAX_MERMAID_LENGTH:
        raise ValueError("Mermaid source is empty or too large")
    lowered = source.lower()
    if "%%{" in source or "click " in lowered or "http://" in lowered or "https://" in lowered:
        raise ValueError("Mermaid directives, links, and click handlers are not allowed")
    first = next((line.strip().lower() for line in source.splitlines() if line.strip()), "")
    allowed = ("flowchart ", "graph ", "sequenceDiagram", "stateDiagram")
    if not any(first.startswith(item.lower()) for item in allowed):
        raise ValueError("Unsupported Mermaid diagram type")
    node_candidates = set(re.findall(r"\b([A-Za-z][A-Za-z0-9_]*)\s*(?:\[|\(|\{)", source))
    if len(node_candidates) > MAX_MERMAID_NODES:
        raise ValueError("Mermaid diagram has too many nodes")
    return source


async def render_mermaid(source: str) -> GeneratedImage:
    source = validate_mermaid(source)
    try:
        async with httpx.AsyncClient(timeout=90) as client:
            response = await client.post(
                f"{settings.diagram_renderer_url.rstrip('/')}/render",
                json={"source": source, "width": 1600, "height": 900},
            )
    except (httpx.TimeoutException, httpx.NetworkError) as exc:
        raise TemporaryAPIError("Mermaid renderer is unavailable") from exc
    if response.status_code >= 500:
        raise TemporaryAPIError("Mermaid renderer failed temporarily")
    if response.status_code >= 400:
        raise PermanentAPIError(f"Mermaid renderer rejected the diagram: {response.text[:300]}")
    content_type = response.headers.get("content-type", "")
    if "image/png" not in content_type or not response.content.startswith(b"\x89PNG"):
        raise PermanentAPIError("Mermaid renderer returned invalid PNG data")
    return GeneratedImage(
        content=response.content,
        content_type="image/png",
        width=1600,
        height=900,
        request_id=response.headers.get("x-request-id"),
    )
