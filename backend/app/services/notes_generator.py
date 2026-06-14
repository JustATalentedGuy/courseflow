import re
import inspect
from dataclasses import dataclass

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
- If a concept genuinely benefits from a diagram, write one placeholder in this exact form:
  {{DIAGRAM: learning goal; entities/components; relationships and arrow directions; essential labels}}
- Diagram descriptions must be detailed enough for a separate renderer to recreate the visual
- Prefer diagrams for architecture, data structures, flows, and comparisons; do not request decorative images
- Output only valid Markdown, no preamble or postamble
- End each section with a "Key Concepts:" bullet list of 3-5 extractable terms
""".strip()

MAX_COMPLETION_TOKENS = 1200


@dataclass(frozen=True)
class GroqChunkResult:
    markdown: str
    model: str
    prompt_tokens: int
    completion_tokens: int
    cached_tokens: int
    charged_tokens: int
    request_id: str | None
    headers: dict[str, str]


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


def build_notes_messages(
    chunk: TranscriptChunk,
    chunk_index: int,
    total_chunks: int,
    previous_summary: str | None,
) -> list[dict[str, str]]:
    context = ""
    if chunk_index > 0 and previous_summary:
        context = f"Previous section covered: {previous_summary}. Continue notes without repeating it.\n\n"
    user_prompt = (
        f"{context}"
        f"This is part {chunk_index + 1} of {total_chunks} of the lecture.\n\n"
        f"Transcript chunk:\n{chunk.text}"
    )
    return [
        {"role": "system", "content": NOTES_SYSTEM_PROMPT},
        {"role": "user", "content": user_prompt},
    ]


def _usage_value(usage, name: str) -> int:
    if usage is None:
        return 0
    if isinstance(usage, dict):
        return int(usage.get(name) or 0)
    return int(getattr(usage, name, 0) or 0)


def _cached_tokens(usage) -> int:
    direct = _usage_value(usage, "cached_tokens")
    details = usage.get("prompt_tokens_details") if isinstance(usage, dict) else getattr(
        usage,
        "prompt_tokens_details",
        None,
    )
    if isinstance(details, dict):
        return direct or int(details.get("cached_tokens") or 0)
    return direct or int(getattr(details, "cached_tokens", 0) or 0)


async def generate_groq_notes_for_chunk(
    chunk: TranscriptChunk,
    chunk_index: int,
    total_chunks: int,
    previous_summary: str | None,
    groq_client: AsyncGroq,
    model: str,
) -> GroqChunkResult:
    messages = build_notes_messages(chunk, chunk_index, total_chunks, previous_summary)

    async def create_completion():
        return await groq_client.chat.completions.with_raw_response.create(
            model=model,
            messages=messages,
            temperature=0.2,
            max_completion_tokens=MAX_COMPLETION_TOKENS,
        )

    raw_response = await call_external_async(
        create_completion,
        "Groq notes",
        passthrough_status_codes={429},
    )
    response = raw_response.parse()
    if inspect.isawaitable(response):
        response = await response
    headers = {key.lower(): value for key, value in raw_response.headers.items()}
    usage = getattr(response, "usage", None)
    prompt_tokens = _usage_value(usage, "prompt_tokens")
    completion_tokens = _usage_value(usage, "completion_tokens")
    cached_tokens = _cached_tokens(usage)
    total_tokens = prompt_tokens + completion_tokens
    return GroqChunkResult(
        markdown=_completion_text(response).strip(),
        model=model,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        cached_tokens=cached_tokens,
        charged_tokens=max(total_tokens - cached_tokens, 0),
        request_id=headers.get("x-request-id") or getattr(response, "id", None),
        headers=headers,
    )


async def generate_notes_for_chunk(
    chunk: TranscriptChunk,
    chunk_index: int,
    total_chunks: int,
    previous_summary: str | None,
    groq_client: AsyncGroq | None,
) -> str:
    if groq_client is None:
        return _deterministic_notes(chunk, chunk_index)

    async def create_completion():
        return await groq_client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=build_notes_messages(chunk, chunk_index, total_chunks, previous_summary),
            temperature=0.2,
            max_completion_tokens=MAX_COMPLETION_TOKENS,
        )

    response = await call_external_async(create_completion, "Groq notes")
    return _completion_text(response).strip()


def _strip_markdown(text: str) -> str:
    text = re.sub(r"#+\s*", "", text)
    text = re.sub(r"\*\*(.*?)\*\*", r"\1", text)
    text = re.sub(r"[-*]\s+", "", text)
    return " ".join(text.split())


def _summary_from_markdown(markdown: str) -> str:
    narrative_lines = []
    for line in markdown.splitlines():
        stripped = line.strip()
        if (
            not stripped
            or stripped.startswith(("#", "-", "*", "+"))
            or stripped.lower().startswith("key concepts:")
        ):
            continue
        narrative_lines.append(stripped)
    plain = _strip_markdown(" ".join(narrative_lines) or markdown)
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
