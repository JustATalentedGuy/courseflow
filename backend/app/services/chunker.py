import re
from uuid import uuid4

from app.schemas.chunk import TextChunk, TranscriptChunk
from app.schemas.notes import VideoNotes
from app.schemas.transcript import NormalisedTranscript

TARGET_NOTES_CHUNK_TOKENS = 3500
SHORT_TRANSCRIPT_TOKENS = 300
EMBEDDING_CHUNK_TOKENS = 500
SENTENCE_OVERLAP = 2


def estimate_model_tokens(text: str) -> int:
    # A conservative tokenizer-free estimate for Llama-family prompts.
    return max(1, (len(text) + 3) // 4)


def _word_count(text: str) -> int:
    return len(text.split())


def _split_sentences(text: str) -> list[str]:
    sentences = re.split(r"(?<=[.!?])\s+", text.strip())
    return [sentence.strip() for sentence in sentences if sentence.strip()]


def _split_long_sentence(
    sentence: str,
    max_tokens: int = TARGET_NOTES_CHUNK_TOKENS,
    *,
    model_tokens: bool = True,
) -> list[str]:
    count_tokens = estimate_model_tokens if model_tokens else _word_count
    if count_tokens(sentence) <= max_tokens:
        return [sentence]

    parts = [part.strip() for part in sentence.split(",") if part.strip()]
    chunks: list[str] = []
    current: list[str] = []
    current_tokens = 0

    for part in parts:
        part_tokens = count_tokens(part)
        if current and current_tokens + part_tokens > max_tokens:
            chunks.append(", ".join(current).strip())
            current = []
            current_tokens = 0
        current.append(part)
        current_tokens += part_tokens

    if current:
        chunks.append(", ".join(current).strip())

    bounded_chunks: list[str] = []
    for chunk in chunks:
        if count_tokens(chunk) <= max_tokens:
            bounded_chunks.append(chunk)
            continue
        words = chunk.split()
        words_per_chunk = max_tokens if not model_tokens else max(1, max_tokens * 3 // 4)
        bounded_chunks.extend(
            " ".join(words[index : index + words_per_chunk])
            for index in range(0, len(words), words_per_chunk)
        )
    return bounded_chunks


def _sentences_with_times(transcript: NormalisedTranscript) -> list[tuple[str, float, float]]:
    timed_sentences: list[tuple[str, float, float]] = []
    for segment in transcript.segments:
        sentences = _split_sentences(segment.text)
        if not sentences:
            continue
        for sentence in sentences:
            for part in _split_long_sentence(sentence):
                timed_sentences.append((part, segment.start, segment.end))
    return timed_sentences


def chunk_transcript_for_notes(transcript: NormalisedTranscript) -> list[TranscriptChunk]:
    if estimate_model_tokens(transcript.full_text) < SHORT_TRANSCRIPT_TOKENS:
        return [
            TranscriptChunk(
                text=transcript.full_text,
                start_seconds=transcript.segments[0].start,
                end_seconds=transcript.segments[-1].end,
                chunk_index=0,
            )
        ]

    timed_sentences = _sentences_with_times(transcript)
    chunks: list[TranscriptChunk] = []
    current: list[tuple[str, float, float]] = []
    current_tokens = 0

    index = 0
    while index < len(timed_sentences):
        sentence, start, end = timed_sentences[index]
        sentence_tokens = estimate_model_tokens(sentence)
        if current and current_tokens + sentence_tokens > TARGET_NOTES_CHUNK_TOKENS:
            chunks.append(_build_transcript_chunk(current, len(chunks)))
            current = current[-SENTENCE_OVERLAP:] if len(current) > SENTENCE_OVERLAP else current[:]
            current_tokens = sum(estimate_model_tokens(item[0]) for item in current)
            continue

        current.append((sentence, start, end))
        current_tokens += sentence_tokens
        index += 1

    if current:
        chunks.append(_build_transcript_chunk(current, len(chunks)))

    return chunks


def _build_transcript_chunk(sentences: list[tuple[str, float, float]], chunk_index: int) -> TranscriptChunk:
    text = " ".join(sentence for sentence, _, _ in sentences).strip()
    return TranscriptChunk(
        text=text,
        start_seconds=sentences[0][1],
        end_seconds=sentences[-1][2],
        chunk_index=chunk_index,
    )


def _split_paragraphs_for_embedding(text: str) -> list[str]:
    paragraphs = [paragraph.strip() for paragraph in re.split(r"\n\s*\n", text) if paragraph.strip()]
    bounded_paragraphs: list[str] = []
    for paragraph in paragraphs or [text]:
        if _word_count(paragraph) <= EMBEDDING_CHUNK_TOKENS:
            bounded_paragraphs.append(paragraph)
            continue
        sentences = _split_sentences(paragraph) or [paragraph]
        for sentence in sentences:
            bounded_paragraphs.extend(
                _split_long_sentence(
                    sentence,
                    EMBEDDING_CHUNK_TOKENS,
                    model_tokens=False,
                )
            )

    chunks: list[str] = []
    current: list[str] = []
    current_tokens = 0
    for paragraph in bounded_paragraphs:
        paragraph_tokens = _word_count(paragraph)
        if current and current_tokens + paragraph_tokens > EMBEDDING_CHUNK_TOKENS:
            chunks.append("\n\n".join(current))
            current = []
            current_tokens = 0
        current.append(paragraph)
        current_tokens += paragraph_tokens
    if current:
        chunks.append("\n\n".join(current))
    return chunks


def chunk_notes_for_embedding(notes: VideoNotes) -> list[TextChunk]:
    chunks: list[TextChunk] = []
    for section in notes.sections:
        section_text = f"{'#' * section.level} {section.heading}\n\n{section.content}".strip()
        for piece in _split_paragraphs_for_embedding(section_text):
            chunks.append(
                TextChunk(
                    chunk_id=str(uuid4()),
                    video_id=notes.video_id,
                    course_id=notes.course_id,
                    user_id="",
                    text=piece,
                    start_seconds=0.0,
                    end_seconds=1.0,
                    section_heading=section.heading,
                    embedding=[0.0] * 384,
                    chunk_index=len(chunks),
                )
            )
    return chunks
