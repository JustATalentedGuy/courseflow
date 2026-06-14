import base64
import hashlib
import inspect
import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

from groq import AsyncGroq
from pydantic import BaseModel, Field, ValidationError as PydanticValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.config import settings
from app.core.exceptions import (
    DiagramQuotaWaitError,
    GroqQuotaWaitError,
    PermanentAPIError,
    TemporaryAPIError,
    UserIsolationError,
)
from app.models.course import Course
from app.models.diagram import CloudflareUsageEvent, DiagramAsset
from app.models.groq import GroqUsageEvent
from app.models.notes import Notes
from app.models.video import Video
from app.schemas.diagram import (
    DiagramAssetResponse,
    DiagramGenerateResponse,
    DiagramStatusResponse,
)
from app.services.diagram_quota import CloudflareQuotaManager
from app.services.image_provider import CloudflareImageProvider
from app.services.mermaid_renderer import render_mermaid, validate_mermaid
from app.services.object_storage import (
    delete_object,
    generate_presigned_url,
    read_object,
    upload_object,
)
from app.services.quota import QuotaManager

DIAGRAM_PATTERN = re.compile(r"\{\{DIAGRAM:\s*(?P<caption>[^{}]+?)\s*\}\}")
TECHNICAL_TERMS = {
    "architecture",
    "b-tree",
    "binary",
    "chaining",
    "collision",
    "database",
    "denormalized",
    "disk",
    "flow",
    "graph",
    "hash",
    "index",
    "join",
    "mapreduce",
    "network",
    "node",
    "pipeline",
    "probing",
    "queue",
    "relational",
    "replication",
    "sequential",
    "sequence",
    "sql",
    "stream",
    "system",
    "tree",
    "write-ahead",
}


@dataclass(frozen=True)
class DiagramMarker:
    index: int
    caption: str
    context: str
    fingerprint: str


class DiagramSpec(BaseModel):
    mode: str = Field(pattern=r"^(structured|illustrative)$")
    detailed_prompt: str = Field(min_length=40, max_length=5000)
    negative_prompt: str = Field(default="", max_length=2000)
    alt_text: str = Field(min_length=20, max_length=500)
    mermaid_source: str | None = Field(default=None, max_length=12_000)


def _prefer_structured(asset: DiagramAsset) -> bool:
    terms = set(
        re.findall(
            r"[a-z0-9-]+",
            f"{asset.original_caption} {asset.context_snapshot}".lower(),
        )
    )
    return bool(terms.intersection(TECHNICAL_TERMS))


def _normalise(value: str) -> str:
    return " ".join(value.lower().split())


def _section_context(markdown: str, position: int) -> str:
    before = markdown[:position]
    heading_matches = list(re.finditer(r"^#{1,3}\s+.+$", before, flags=re.MULTILINE))
    start = heading_matches[-1].start() if heading_matches else max(0, position - 1200)
    next_heading = re.search(r"^#{1,3}\s+.+$", markdown[position:], flags=re.MULTILINE)
    end = position + next_heading.start() if next_heading else min(len(markdown), position + 1200)
    context = DIAGRAM_PATTERN.sub("", markdown[start:end])
    return " ".join(context.split())[:3000]


def parse_diagram_markers(markdown: str) -> list[DiagramMarker]:
    markers: list[DiagramMarker] = []
    for index, match in enumerate(DIAGRAM_PATTERN.finditer(markdown)):
        caption = " ".join(match.group("caption").split())
        context = _section_context(markdown, match.start())
        fingerprint = hashlib.sha256(
            f"{_normalise(caption)}\n{_normalise(context)}".encode("utf-8")
        ).hexdigest()
        markers.append(DiagramMarker(index, caption, context, fingerprint))
    return markers


def _relevant_transcript_context(video: Video, marker: DiagramMarker) -> str:
    transcript = video.transcript
    if transcript is None:
        return ""
    query_terms = {
        word
        for word in re.findall(r"[a-z0-9-]{4,}", f"{marker.caption} {marker.context}".lower())
    }
    scored: list[tuple[int, int, str]] = []
    for index, segment in enumerate(transcript.segments_json or []):
        text = " ".join(str(segment.get("text", "")).split())
        score = len(query_terms.intersection(re.findall(r"[a-z0-9-]{4,}", text.lower())))
        if score:
            scored.append((score, index, text))
    selected = sorted(sorted(scored, reverse=True)[:5], key=lambda item: item[1])
    return "\n".join(text for _, _, text in selected)


async def discover_diagrams_for_notes(
    db: AsyncSession,
    notes: Notes,
    video: Video,
) -> list[DiagramAsset]:
    source = notes.source_markdown or notes.full_markdown
    markers = parse_diagram_markers(source)
    current = list(
        await db.scalars(
            select(DiagramAsset)
            .where(
                DiagramAsset.notes_id == notes.id,
                DiagramAsset.note_version == notes.content_version,
            )
            .order_by(DiagramAsset.marker_index)
        )
    )
    if len(current) == len(markers) and all(
        row.marker_fingerprint == marker.fingerprint for row, marker in zip(current, markers)
    ):
        return current

    all_rows = list(
        await db.scalars(
            select(DiagramAsset)
            .where(DiagramAsset.notes_id == notes.id)
            .order_by(DiagramAsset.created_at.desc())
        )
    )
    available: dict[str, list[DiagramAsset]] = {}
    for row in all_rows:
        if row.state == "completed":
            available.setdefault(row.marker_fingerprint, []).append(row)
        if row.note_version != notes.content_version and row.state != "stale":
            row.state = "stale"

    rows: list[DiagramAsset] = []
    for marker in markers:
        existing = next(
            (
                row
                for row in current
                if row.marker_index == marker.index
                and row.marker_fingerprint == marker.fingerprint
            ),
            None,
        )
        if existing is not None:
            rows.append(existing)
            continue
        reusable = (available.get(marker.fingerprint) or [None]).pop(0)
        if reusable is not None:
            reusable.note_version = notes.content_version
            reusable.marker_index = marker.index
            reusable.context_snapshot = marker.context
            reusable.state = "completed"
            rows.append(reusable)
            continue
        row = DiagramAsset(
            notes_id=notes.id,
            video_id=notes.video_id,
            course_id=notes.course_id,
            user_id=notes.user_id,
            note_version=notes.content_version,
            marker_index=marker.index,
            marker_fingerprint=marker.fingerprint,
            original_caption=marker.caption,
            context_snapshot=marker.context,
            state="pending",
        )
        db.add(row)
        rows.append(row)
    await db.commit()
    for row in rows:
        await db.refresh(row)
    return rows


async def reconcile_diagrams_after_note_update(
    db: AsyncSession,
    notes: Notes,
    video: Video,
) -> None:
    await discover_diagrams_for_notes(db, notes, video)


def _callout(asset: DiagramAsset | None, caption: str) -> str:
    if asset is None:
        label = "Diagram not generated"
    elif asset.state == "failed":
        label = "Diagram generation failed"
    elif asset.state == "rate_limited":
        label = "Diagram waiting for quota"
    elif asset.state == "skipped":
        label = "Diagram removed"
    else:
        label = "Diagram pending"
    return f"> **{label}:** {caption}"


async def materialize_notes_markdown(
    db: AsyncSession,
    notes: Notes,
    *,
    image_mode: str = "presigned",
) -> str:
    source = notes.source_markdown or notes.full_markdown
    markers = parse_diagram_markers(source)
    if not markers:
        return source
    rows = list(
        await db.scalars(
            select(DiagramAsset).where(
                DiagramAsset.notes_id == notes.id,
                DiagramAsset.note_version == notes.content_version,
            )
        )
    )
    by_index = {row.marker_index: row for row in rows}
    replacements: list[str] = []
    for marker in markers:
        asset = by_index.get(marker.index)
        if asset is None or asset.state != "completed" or not asset.object_uri:
            replacements.append(_callout(asset, marker.caption))
            continue
        alt = asset.alt_text or asset.original_caption
        if image_mode == "embedded":
            content = await read_object(asset.object_uri)
            mime = "image/png" if asset.object_uri.endswith(".png") else "image/webp"
            uri = f"data:{mime};base64,{base64.b64encode(content).decode('ascii')}"
        elif image_mode == "private":
            uri = asset.object_uri
        else:
            uri = await generate_presigned_url(asset.object_uri)
        replacements.append(f"![{alt}]({uri})")

    iterator = iter(replacements)
    return DIAGRAM_PATTERN.sub(lambda _: next(iterator), source)


async def get_video_diagrams(
    db: AsyncSession,
    user_id: UUID,
    video_id: UUID,
) -> list[DiagramAssetResponse]:
    video = await db.scalar(
        select(Video)
        .options(selectinload(Video.notes), selectinload(Video.transcript))
        .where(Video.id == video_id, Video.user_id == user_id)
    )
    if video is None or video.notes is None:
        raise UserIsolationError("Video notes not found")
    rows = await discover_diagrams_for_notes(db, video.notes, video)
    responses: list[DiagramAssetResponse] = []
    for row in rows:
        payload = DiagramAssetResponse.model_validate(row)
        if row.object_uri and row.state == "completed":
            payload.image_url = await generate_presigned_url(row.object_uri)
        responses.append(payload)
    return responses


async def get_course_diagram_status(
    db: AsyncSession,
    user_id: UUID,
    course_id: UUID,
) -> DiagramStatusResponse:
    course = await db.scalar(
        select(Course).where(Course.id == course_id, Course.user_id == user_id)
    )
    if course is None:
        raise UserIsolationError("Course not found")
    rows = list(
        await db.scalars(
            select(DiagramAsset).where(
                DiagramAsset.course_id == course_id,
                DiagramAsset.user_id == user_id,
                DiagramAsset.state != "stale",
            )
        )
    )
    counts = {state: 0 for state in (
        "pending", "spec_generating", "rendering", "rate_limited",
        "completed", "failed", "skipped", "stale",
    )}
    for row in rows:
        counts[row.state] = counts.get(row.state, 0) + 1
    return DiagramStatusResponse(
        course_id=course_id,
        discovered=len(rows),
        pending=counts["pending"],
        processing=counts["spec_generating"] + counts["rendering"],
        waiting=counts["rate_limited"],
        completed=counts["completed"],
        failed=counts["failed"],
        skipped=counts["skipped"],
        stale=counts["stale"],
    )


def _deterministic_spec(asset: DiagramAsset) -> DiagramSpec:
    caption = asset.original_caption
    words = set(re.findall(r"[a-z0-9-]+", caption.lower()))
    structured = bool(words.intersection(TECHNICAL_TERMS))
    if structured:
        return _structured_fallback_spec(asset)
    return DiagramSpec(
        mode="illustrative",
        detailed_prompt=(
            f"Create a clean educational illustration of {caption}. "
            "Use a simple composition, high contrast, and no embedded text."
        ),
        negative_prompt="photorealistic clutter, illegible text, watermark, logo",
        alt_text=f"Educational illustration showing {caption}.",
    )


def _structured_fallback_spec(asset: DiagramAsset) -> DiagramSpec:
    caption = re.sub(r'["\n\r]+', " ", asset.original_caption).strip()[:140]
    lower = caption.lower()
    if "hash" in lower or "collision" in lower:
        source = (
            "flowchart LR\n"
            '  A["Input key"] --> B["Hash function"]\n'
            '  B --> C["Bucket or index position"]\n'
            '  C --> D["Stored record"]\n'
            '  C --> E["Collision handling: chain or probe"]'
        )
    elif "b-tree" in lower or "child page" in lower or "node split" in lower:
        source = (
            "flowchart TD\n"
            '  A["Root page"] --> B["Internal page: lower key range"]\n'
            '  A --> C["Internal page: higher key range"]\n'
            '  B --> D["Ordered leaf pages"]\n'
            '  C --> E["Ordered leaf pages"]\n'
            '  D --> F["Range traversal"]\n'
            '  E --> F'
        )
    elif "denormalized" in lower or "non-relational" in lower:
        source = (
            "flowchart LR\n"
            '  A["Customer fields"] --> C["Order document"]\n'
            '  B["Product fields"] --> C\n'
            '  D["Address fields"] --> C\n'
            '  C --> E["Direct read with duplicated data"]'
        )
    elif "disk" in lower:
        source = (
            "flowchart LR\n"
            '  A["Lookup key"] --> B["Hash index"]\n'
            '  B --> C["Disk page 12"]\n'
            '  B --> D["Disk page 87"]\n'
            '  B --> E["Disk page 203"]\n'
            '  C --> F["Random access pattern"]\n'
            '  D --> F\n'
            '  E --> F'
        )
    else:
        source = (
            "flowchart LR\n"
            '  A["Starting concepts"] --> B["'
            f"{caption}"
            '"]\n'
            '  B --> C["Key relationship"]\n'
            '  C --> D["Result or learning outcome"]'
        )
    return DiagramSpec(
        mode="structured",
        detailed_prompt=(
            f"Create a precise instructional diagram explaining {asset.original_caption}. "
            "Show the principal components, directional relationships, and concise labels."
        ),
        alt_text=(
            f"Technical diagram explaining {asset.original_caption} "
            "and the relationships between its main components."
        ),
        mermaid_source=source,
    )


def _spec_messages(asset: DiagramAsset, video: Video, repair: str | None = None) -> list[dict]:
    transcript_context = _relevant_transcript_context(
        video,
        DiagramMarker(
            asset.marker_index,
            asset.original_caption,
            asset.context_snapshot,
            asset.marker_fingerprint,
        ),
    )
    system = (
        "You design accurate educational visuals. Return only JSON with keys mode, "
        "detailed_prompt, negative_prompt, alt_text, mermaid_source. Choose structured "
        "for architecture, data structures, flows, comparisons, and labeled technical "
        "relationships; choose illustrative only when a conceptual picture is genuinely "
        "better. Mermaid must use flowchart, sequenceDiagram, or stateDiagram; "
        "use short accurate labels and no links, click handlers, directives, icons, or HTML."
    )
    user = (
        f"Course: {video.course.title if video.course else ''}\n"
        f"Lesson: {video.title}\n"
        f"Marker: {asset.original_caption}\n"
        f"Notes context: {asset.context_snapshot}\n"
        f"Relevant transcript context: {transcript_context or 'Unavailable'}"
    )
    if asset.detailed_prompt:
        user += f"\nUser editing instruction: {asset.detailed_prompt}"
    if asset.render_mode:
        user += f"\nRequired mode: {asset.render_mode}"
    if repair:
        user += f"\n\nThe previous response was invalid: {repair}. Return corrected JSON only."
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def _response_text(response) -> str:
    message = response.choices[0].message
    return message.content or ""


def _parse_spec(text: str) -> DiagramSpec:
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = re.sub(r"^```(?:json)?\s*|\s*```$", "", stripped, flags=re.IGNORECASE)
    spec = DiagramSpec.model_validate(json.loads(stripped))
    if spec.mode == "structured":
        if not spec.mermaid_source:
            raise ValueError("structured diagrams require mermaid_source")
        validate_mermaid(spec.mermaid_source)
    return spec


async def _generate_spec_with_groq(
    db: AsyncSession,
    asset: DiagramAsset,
    video: Video,
) -> DiagramSpec:
    configured = settings.groq_api_key not in {"", "your_groq_key_here"}
    if not configured:
        return _deterministic_spec(asset)
    quota = QuotaManager()
    client = AsyncGroq(api_key=settings.groq_api_key, max_retries=0)
    repair: str | None = None
    try:
        for attempt in range(2):
            messages = _spec_messages(asset, video, repair)
            estimated_tokens = sum(len(item["content"]) // 3 for item in messages) + 1800
            reservation = await quota.reserve(db, settings.groq_auto_model, estimated_tokens)
            try:
                raw = await client.chat.completions.with_raw_response.create(
                    model=settings.groq_auto_model,
                    messages=messages,
                    temperature=0.1,
                    max_completion_tokens=1800,
                    response_format={"type": "json_object"},
                )
                response = raw.parse()
                if inspect.isawaitable(response):
                    response = await response
                headers = {str(k).lower(): str(v) for k, v in raw.headers.items()}
            except Exception as exc:
                if getattr(exc, "status_code", None) == 429:
                    await quota.release(reservation)
                    response_headers = getattr(getattr(exc, "response", None), "headers", {})
                    raise await quota.wait_from_headers(
                        settings.groq_auto_model,
                        response_headers,
                        str(exc),
                    ) from exc
                await quota.release(reservation)
                raise
            usage = getattr(response, "usage", None)
            prompt_tokens = int(getattr(usage, "prompt_tokens", 0) or 0)
            completion_tokens = int(getattr(usage, "completion_tokens", 0) or 0)
            cached_tokens = int(
                getattr(getattr(usage, "prompt_tokens_details", None), "cached_tokens", 0)
                or 0
            )
            charged = max(prompt_tokens + completion_tokens - cached_tokens, 0)
            await quota.reconcile(reservation, charged, headers)
            request_id = headers.get("x-request-id") or getattr(response, "id", None)
            db.add(
                GroqUsageEvent(
                    model=settings.groq_auto_model,
                    user_id=asset.user_id,
                    video_id=asset.video_id,
                    course_id=asset.course_id,
                    mode="diagram_spec",
                    prompt_tokens=prompt_tokens,
                    completion_tokens=completion_tokens,
                    cached_tokens=cached_tokens,
                    charged_tokens=charged,
                    request_id=request_id,
                )
            )
            await db.commit()
            try:
                spec = _parse_spec(_response_text(response))
                if spec.mode == "illustrative" and _prefer_structured(asset):
                    raise ValueError(
                        "This technical marker requires structured Mermaid, not an illustration"
                    )
                return spec
            except (ValueError, json.JSONDecodeError, PydanticValidationError) as exc:
                repair = str(exc)[:500]
                if attempt == 1:
                    if _prefer_structured(asset):
                        return _structured_fallback_spec(asset)
                    raise PermanentAPIError(f"Invalid diagram specification: {repair}") from exc
        raise PermanentAPIError("Diagram specification could not be generated")
    finally:
        await client.close()
        await quota.close()


async def process_diagram(db: AsyncSession, diagram_id: UUID) -> str:
    asset = await db.get(DiagramAsset, diagram_id)
    if asset is None:
        raise PermanentAPIError("Diagram not found")
    if asset.state == "completed" and asset.object_uri:
        return "completed"
    notes = await db.get(Notes, asset.notes_id)
    if notes is None or asset.note_version != notes.content_version:
        asset.state = "stale"
        await db.commit()
        return "stale"
    video = await db.scalar(
        select(Video)
        .options(selectinload(Video.transcript), selectinload(Video.course))
        .where(Video.id == asset.video_id)
    )
    if video is None:
        raise PermanentAPIError("Diagram video not found")

    if asset.render_mode == "illustrative" and _prefer_structured(asset):
        asset.detailed_prompt = None
        asset.negative_prompt = None
        asset.alt_text = None
        asset.render_mode = None
        asset.mermaid_source = None

    reusable_spec = bool(
        asset.detailed_prompt
        and asset.render_mode
        and (asset.render_mode == "illustrative" or asset.mermaid_source)
    )
    asset.state = "rendering" if reusable_spec else "spec_generating"
    asset.retry_at = None
    asset.error_message = None
    await db.commit()
    if reusable_spec:
        spec = DiagramSpec(
            mode=asset.render_mode,
            detailed_prompt=asset.detailed_prompt,
            negative_prompt=asset.negative_prompt or "",
            alt_text=asset.alt_text or f"Educational diagram showing {asset.original_caption}.",
            mermaid_source=asset.mermaid_source,
        )
    else:
        spec = await _generate_spec_with_groq(db, asset, video)
        asset.detailed_prompt = spec.detailed_prompt
        asset.negative_prompt = spec.negative_prompt
        asset.alt_text = spec.alt_text
        asset.render_mode = spec.mode
        asset.mermaid_source = spec.mermaid_source
        asset.state = "rendering"
        await db.commit()

    quota: CloudflareQuotaManager | None = None
    reservation = None
    if spec.mode == "structured":
        try:
            image = await render_mermaid(spec.mermaid_source or "")
        except (PermanentAPIError, ValueError) as exc:
            asset.mermaid_source = None
            asset.detailed_prompt = (
                f"{spec.detailed_prompt}\nThe previous Mermaid failed to render: {str(exc)[:500]}. "
                "Use a simple flowchart and quote every label."
            )
            asset.state = "spec_generating"
            await db.commit()
            repaired = await _generate_spec_with_groq(db, asset, video)
            if repaired.mode != "structured" or not repaired.mermaid_source:
                raise PermanentAPIError(
                    "Mermaid repair did not return a structured diagram"
                ) from exc
            asset.detailed_prompt = repaired.detailed_prompt
            asset.negative_prompt = repaired.negative_prompt
            asset.alt_text = repaired.alt_text
            asset.render_mode = repaired.mode
            asset.mermaid_source = repaired.mermaid_source
            asset.state = "rendering"
            await db.commit()
            try:
                image = await render_mermaid(repaired.mermaid_source)
            except (PermanentAPIError, ValueError):
                repaired = _structured_fallback_spec(asset)
                asset.detailed_prompt = repaired.detailed_prompt
                asset.negative_prompt = repaired.negative_prompt
                asset.alt_text = repaired.alt_text
                asset.render_mode = repaired.mode
                asset.mermaid_source = repaired.mermaid_source
                await db.commit()
                image = await render_mermaid(repaired.mermaid_source)
        extension = "png"
        provider = "mermaid"
        model = "mermaid-cli"
    else:
        if not settings.cloudflare_account_id or not settings.cloudflare_api_token:
            asset.state = "failed"
            asset.provider = "cloudflare"
            asset.model = settings.cloudflare_image_model
            asset.error_message = "provider_unavailable"
            await db.commit()
            return "provider_unavailable"
        quota = CloudflareQuotaManager()
        try:
            reservation = await quota.reserve(db, settings.cloudflare_image_estimated_neurons)
        except Exception:
            await quota.close()
            raise
        try:
            image = await CloudflareImageProvider().generate(
                spec.detailed_prompt,
                spec.negative_prompt,
            )
        except Exception as exc:
            if getattr(exc, "status_code", None) == 429:
                await quota.release(reservation)
                await quota.close()
                retry_after = int(getattr(exc, "retry_after", 60))
                await quota.block(retry_after)
                raise DiagramQuotaWaitError(
                    "Cloudflare image rate limit reached",
                    retry_after=retry_after,
                ) from exc
            await quota.release(reservation)
            await quota.close()
            raise
        await quota.reconcile(reservation)
        await quota.close()
        extension = "webp"
        provider = "cloudflare"
        model = settings.cloudflare_image_model

    previous_object_uri = asset.object_uri
    revision = asset.revision + 1
    object_uri = (
        f"minio://{asset.user_id}/{asset.video_id}/diagrams/"
        f"{asset.id}/{revision}.{extension}"
    )
    await upload_object(object_uri, image.content, image.content_type)
    asset.object_uri = object_uri
    asset.width = image.width
    asset.height = image.height
    asset.checksum = hashlib.sha256(image.content).hexdigest()
    asset.revision = revision
    asset.request_id = image.request_id
    asset.provider = provider
    asset.model = model
    asset.state = "completed"
    asset.error_message = None
    completed_count = int(
        len(
            list(
                await db.scalars(
                    select(DiagramAsset.id).where(
                        DiagramAsset.notes_id == asset.notes_id,
                        DiagramAsset.note_version == asset.note_version,
                        DiagramAsset.state == "completed",
                        DiagramAsset.id != asset.id,
                    )
                )
            )
        )
    ) + 1
    notes.has_images = completed_count > 0
    notes.image_count = completed_count
    if provider == "cloudflare":
        db.add(
            CloudflareUsageEvent(
                diagram_id=asset.id,
                user_id=asset.user_id,
                estimated_neurons=settings.cloudflare_image_estimated_neurons,
                request_id=image.request_id,
            )
        )
    await db.commit()
    if previous_object_uri and previous_object_uri != object_uri:
        try:
            await delete_object(previous_object_uri)
        except Exception:
            pass
    return "completed"


async def queue_course_diagrams(
    db: AsyncSession,
    user_id: UUID,
    course_id: UUID,
) -> tuple[DiagramGenerateResponse, list[UUID]]:
    course = await db.scalar(
        select(Course)
        .options(
            selectinload(Course.videos).selectinload(Video.notes),
            selectinload(Course.videos).selectinload(Video.transcript),
        )
        .where(Course.id == course_id, Course.user_id == user_id)
    )
    if course is None:
        raise UserIsolationError("Course not found")
    rows: list[DiagramAsset] = []
    for video in course.videos:
        if video.notes is not None:
            rows.extend(await discover_diagrams_for_notes(db, video.notes, video))
    queue_ids = [
        row.id
        for row in rows
        if row.state in {"pending", "failed", "rate_limited"}
    ]
    for row in rows:
        if row.id in queue_ids:
            row.state = "pending"
            row.retry_at = None
            row.error_message = None
    await db.commit()
    return (
        DiagramGenerateResponse(
            course_id=course_id,
            discovered=len(rows),
            queued=len(queue_ids),
        ),
        queue_ids,
    )


async def update_diagram_for_regeneration(
    db: AsyncSession,
    user_id: UUID,
    diagram_id: UUID,
    *,
    prompt: str | None,
    mode: str | None,
) -> DiagramAsset:
    asset = await db.scalar(
        select(DiagramAsset).where(
            DiagramAsset.id == diagram_id,
            DiagramAsset.user_id == user_id,
            DiagramAsset.state != "stale",
        )
    )
    if asset is None:
        raise UserIsolationError("Diagram not found")
    if prompt is not None:
        asset.detailed_prompt = prompt.strip() or None
        if asset.render_mode == "structured":
            asset.mermaid_source = None
    if mode is not None:
        asset.render_mode = mode
        asset.mermaid_source = None
    asset.state = "pending"
    asset.retry_at = None
    asset.error_message = None
    await db.commit()
    await db.refresh(asset)
    return asset


async def skip_diagram(db: AsyncSession, user_id: UUID, diagram_id: UUID) -> None:
    asset = await db.scalar(
        select(DiagramAsset).where(
            DiagramAsset.id == diagram_id,
            DiagramAsset.user_id == user_id,
        )
    )
    if asset is None:
        raise UserIsolationError("Diagram not found")
    asset.state = "skipped"
    asset.retry_at = None
    asset.error_message = None
    notes = await db.get(Notes, asset.notes_id)
    if notes is not None:
        completed = list(
            await db.scalars(
                select(DiagramAsset.id).where(
                    DiagramAsset.notes_id == asset.notes_id,
                    DiagramAsset.note_version == asset.note_version,
                    DiagramAsset.state == "completed",
                    DiagramAsset.id != asset.id,
                )
            )
        )
        notes.image_count = len(completed)
        notes.has_images = bool(completed)
    await db.commit()
