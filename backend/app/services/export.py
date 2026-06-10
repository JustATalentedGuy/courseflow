import asyncio
import hashlib
import html
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from uuid import UUID

import genanki
import markdown2
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import UserIsolationError
from app.models.course import Course
from app.models.notes import Notes
from app.models.srs import ConceptCard
from app.models.video import Video
from app.services.notes_service import get_video_for_user
from app.services.object_storage import generate_presigned_url

PDF_CSS = """
@page { size: A4; margin: 22mm 18mm; }
body { font-family: sans-serif; color: #1e293b; line-height: 1.6; font-size: 11pt; }
h1, h2, h3 { color: #0f172a; page-break-after: avoid; }
h1 { font-size: 24pt; } h2 { font-size: 17pt; margin-top: 24pt; }
pre { white-space: pre-wrap; background: #f1f5f9; padding: 10pt; border-radius: 6pt; }
code { font-family: monospace; }
img { max-width: 100%; height: auto; }
table { border-collapse: collapse; width: 100%; }
th, td { border: 1px solid #cbd5e1; padding: 6pt; text-align: left; }
"""


def _render_pdf_document(document: str) -> bytes:
    with tempfile.TemporaryDirectory(prefix="courseflow-pdf-") as temp_dir:
        temp_path = Path(temp_dir)
        html_path = temp_path / "notes.html"
        pdf_path = temp_path / "notes.pdf"
        html_path.write_text(document, encoding="utf-8")
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "app.services.pdf_renderer",
                str(html_path),
                str(pdf_path),
            ],
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
        if pdf_path.exists():
            content = pdf_path.read_bytes()
            if content.startswith(b"%PDF"):
                return content
        detail = result.stderr.strip() or result.stdout.strip() or "Unknown PDF renderer error"
        raise RuntimeError(f"PDF rendering failed: {detail}")


async def export_notes_markdown(
    video_id: UUID,
    user_id: UUID,
    db: AsyncSession,
) -> bytes:
    video = await get_video_for_user(db, user_id, video_id)
    if video.notes is None:
        raise UserIsolationError("Notes not found")
    return video.notes.full_markdown.encode("utf-8")


async def refresh_presigned_image_urls(markdown: str) -> str:
    pattern = re.compile(r"(!\[[^\]]*]\()(?P<uri>minio://[^)]+)(\))")
    matches = list(pattern.finditer(markdown))
    if not matches:
        return markdown

    refreshed = markdown
    for match in reversed(matches):
        url = await generate_presigned_url(match.group("uri"), expires_seconds=3600)
        refreshed = refreshed[: match.start("uri")] + url + refreshed[match.end("uri") :]
    return refreshed


async def export_notes_pdf(
    video_id: UUID,
    user_id: UUID,
    db: AsyncSession,
) -> bytes:
    markdown_bytes = await export_notes_markdown(video_id, user_id, db)
    refreshed_markdown = await refresh_presigned_image_urls(markdown_bytes.decode("utf-8"))
    body = markdown2.markdown(
        refreshed_markdown,
        extras=["fenced-code-blocks", "tables", "strike", "task_list"],
    )
    document = (
        "<!doctype html><html><head><meta charset='utf-8'>"
        f"<style>{PDF_CSS}</style></head><body>{body}</body></html>"
    )

    return await asyncio.to_thread(_render_pdf_document, document)


def _stable_anki_id(value: str) -> int:
    return int(hashlib.sha256(value.encode("utf-8")).hexdigest()[:8], 16)


async def export_anki_deck(
    course_id: UUID,
    user_id: UUID,
    db: AsyncSession,
) -> bytes:
    course = await db.scalar(
        select(Course).where(Course.id == course_id, Course.user_id == user_id)
    )
    if course is None:
        raise UserIsolationError("Course not found")

    rows = (
        await db.execute(
            select(ConceptCard, Notes)
            .join(Video, Video.id == ConceptCard.video_id)
            .join(Notes, Notes.video_id == Video.id)
            .where(
                Video.course_id == course_id,
                Video.user_id == user_id,
                ConceptCard.user_id == user_id,
                Notes.user_id == user_id,
            )
            .order_by(Video.position.asc(), ConceptCard.concept.asc())
        )
    ).all()

    model = genanki.Model(
        _stable_anki_id("courseflow-basic-model"),
        "CourseFlow Basic",
        fields=[{"name": "Front"}, {"name": "Back"}],
        templates=[
            {
                "name": "Card 1",
                "qfmt": "{{Front}}",
                "afmt": "{{FrontSide}}<hr id='answer'>{{Back}}",
            }
        ],
    )
    deck = genanki.Deck(_stable_anki_id(f"courseflow:{course_id}"), course.title)
    for card, notes in rows:
        deck.add_note(
            genanki.Note(
                model=model,
                fields=[html.escape(card.concept), html.escape(notes.summary)],
            )
        )

    with tempfile.TemporaryDirectory(prefix="courseflow-anki-") as temp_dir:
        output_path = Path(temp_dir) / "courseflow.apkg"
        await asyncio.to_thread(genanki.Package(deck).write_to_file, str(output_path))
        return output_path.read_bytes()
