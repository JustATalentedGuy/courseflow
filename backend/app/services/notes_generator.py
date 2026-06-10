import re

from groq import AsyncGroq

from app.schemas.chunk import TranscriptChunk
from app.services.external_api import call_external_async

NOTES_SYSTEM_PROMPT = """
You are an expert academic note-taker. Given a video transcript chunk, produce structured
study notes in Markdown format.

Rules:
- Use ## for main topics, ### for subtopics
- Extract key concepts as a bullet list under each section
- Use **bold** for technical terms on first use
- Include concrete examples from the transcript
- Do NOT hallucinate information not present in the transcript
- Do NOT include filler content, meta-commentary, or references to the speaker
- If a concept requires a diagram, write: {{DIAGRAM: brief description}} as a placeholder
- Output only valid Markdown, no preamble or postamble
- End each section with a "Key Concepts:" bullet list of 3-5 extractable terms
""".strip()


def _deterministic_notes(chunk: TranscriptChunk, chunk_index: int) -> str:
    words = chunk.text.split()
    title_terms = " ".join(words[:5]) if words else "Lecture Concepts"
    concepts = [word.strip(".,:;!?").lower() for word in words if len(word.strip(".,:;!?")) > 4]
    concepts = list(dict.fromkeys(concepts))[:5] or ["concept", "example", "practice"]
    bullets = "\n".join(f"- {concept}" for concept in concepts[:5])
    return (
        f"## Part {chunk_index + 1}: {title_terms}\n\n"
        f"{chunk.text}\n\n"
        "Key Concepts:\n"
        f"{bullets}\n"
    )


def _completion_text(response) -> str:
    choice = response.choices[0]
    message = choice.get("message") if isinstance(choice, dict) else choice.message
    if isinstance(message, dict):
        return message.get("content", "")
    return getattr(message, "content", "")


async def generate_notes_for_chunk(
    chunk: TranscriptChunk,
    chunk_index: int,
    total_chunks: int,
    previous_summary: str | None,
    groq_client: AsyncGroq | None,
) -> str:
    if groq_client is None:
        return _deterministic_notes(chunk, chunk_index)

    context = ""
    if chunk_index > 0 and previous_summary:
        context = f"Previous section covered: {previous_summary}. Continue notes without repeating it.\n\n"

    user_prompt = (
        f"{context}"
        f"This is part {chunk_index + 1} of {total_chunks} of the lecture.\n\n"
        f"Transcript chunk:\n{chunk.text}"
    )
    async def create_completion():
        return await groq_client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[
                {"role": "system", "content": NOTES_SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.2,
        )

    response = await call_external_async(create_completion, "Groq notes")
    return _completion_text(response).strip()


def _strip_markdown(text: str) -> str:
    text = re.sub(r"#+\s*", "", text)
    text = re.sub(r"\*\*(.*?)\*\*", r"\1", text)
    text = re.sub(r"[-*]\s+", "", text)
    return " ".join(text.split())


def _summary_from_markdown(markdown: str) -> str:
    plain = _strip_markdown(markdown)
    sentences = re.split(r"(?<=[.!?])\s+", plain)
    selected = [sentence.strip() for sentence in sentences if sentence.strip()][:3]
    if not selected:
        return "This video introduces the key ideas from the transcript."
    summary = " ".join(selected)
    if not re.search(r"[.!?]$", summary):
        summary += "."
    return summary


def _normalise_heading_hierarchy(markdown: str) -> str:
    lines = markdown.splitlines()
    seen_h2 = False
    output: list[str] = []
    for line in lines:
        if line.startswith("## ") and not line.startswith("### "):
            seen_h2 = True
        if line.startswith("### ") and not seen_h2:
            output.append("## Lecture Notes")
            seen_h2 = True
        output.append(line)
    return "\n".join(output).strip()


async def stitch_chunk_notes(chunk_notes: list[str], video_title: str) -> str:
    merged_lines: list[str] = []
    seen_h2: set[str] = set()

    for markdown in chunk_notes:
        for line in markdown.splitlines():
            if line.startswith("## ") and not line.startswith("### "):
                heading_key = line.lower().strip()
                if heading_key in seen_h2:
                    continue
                seen_h2.add(heading_key)
            merged_lines.append(line)

    body = _normalise_heading_hierarchy("\n".join(merged_lines))
    if "## " not in body:
        body = f"## Lecture Notes\n\n{body}"
    summary = _summary_from_markdown(body)
    final_markdown = f"# {video_title}\n\n## Summary\n\n{summary}\n\n{body}".strip()
    if "{{" in final_markdown and "}}" not in final_markdown:
        raise ValueError("Malformed Markdown placeholder token")
    return final_markdown
