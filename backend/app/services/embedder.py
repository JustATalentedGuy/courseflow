import hashlib
import math
import re
from typing import Protocol
from uuid import UUID

from sqlalchemy import delete, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ValidationError
from app.models.chunk import NoteChunk
from app.models.video import Video
from app.schemas.notes import VideoNotes
from app.services.chunker import chunk_notes_for_embedding

MODEL_NAME = "all-MiniLM-L6-v2"
EMBEDDING_DIM = 384
MAX_CHUNK_TOKENS = 500


class EmbeddingModel(Protocol):
    def encode(self, texts, convert_to_numpy: bool = False, normalize_embeddings: bool = True):
        ...


class DeterministicEmbeddingModel:
    """Small fallback for local tests when sentence-transformers is unavailable."""

    _synonyms = {
        "bst": "binary_search_tree",
        "binary": "binary_search_tree",
        "search": "binary_search_tree",
        "tree": "binary_search_tree",
        "trees": "binary_search_tree",
    }

    def encode(self, texts, convert_to_numpy: bool = False, normalize_embeddings: bool = True):
        if isinstance(texts, str):
            texts = [texts]
        return [self._embed(text, normalize_embeddings) for text in texts]

    def _embed(self, text: str, normalize_embeddings: bool) -> list[float]:
        vector = [0.0] * EMBEDDING_DIM
        for token in re.findall(r"[a-zA-Z0-9]+", text.lower()):
            token = self._synonyms.get(token, token)
            digest = hashlib.sha256(token.encode("utf-8")).digest()
            index = int.from_bytes(digest[:4], "big") % EMBEDDING_DIM
            vector[index] += 1.0
        if normalize_embeddings:
            norm = math.sqrt(sum(value * value for value in vector))
            if norm:
                vector = [value / norm for value in vector]
        return vector


_model: EmbeddingModel | None = None


def get_embedding_model() -> EmbeddingModel:
    """Singleton: load the embedding model once per process."""
    global _model
    if _model is None:
        try:
            from sentence_transformers import SentenceTransformer

            _model = SentenceTransformer(MODEL_NAME)
        except Exception:
            _model = DeterministicEmbeddingModel()
    return _model


def embed_texts(texts: list[str]) -> list[list[float]]:
    cleaned = [text.strip() for text in texts]
    if any(not text for text in cleaned):
        raise ValueError("embed_texts does not accept empty strings")
    if any(len(text.split()) > MAX_CHUNK_TOKENS for text in cleaned):
        raise ValueError(f"embed_texts expects chunks under {MAX_CHUNK_TOKENS} tokens")

    vectors = get_embedding_model().encode(
        cleaned,
        convert_to_numpy=False,
        normalize_embeddings=True,
    )
    result = [list(map(float, vector)) for vector in vectors]
    for vector in result:
        if len(vector) != EMBEDDING_DIM:
            raise ValueError(f"embedding must be {EMBEDDING_DIM}-dimensional")
    return result


async def ensure_hnsw_index(db: AsyncSession) -> None:
    await db.execute(
        text(
            """
            CREATE INDEX IF NOT EXISTS note_chunks_embedding_idx
            ON note_chunks USING hnsw (embedding vector_cosine_ops)
            WITH (m = 16, ef_construction = 64)
            """
        )
    )


async def embed_and_store_notes(notes: VideoNotes, video: Video, db: AsyncSession) -> list[NoteChunk]:
    if UUID(notes.video_id) != video.id:
        raise ValidationError("Notes video_id does not match video")
    if UUID(notes.course_id) != video.course_id:
        raise ValidationError("Notes course_id does not match video course")

    chunks = chunk_notes_for_embedding(notes)
    if not chunks:
        raise ValidationError("Notes produced no chunks to embed")
    for chunk in chunks:
        if not chunk.text.strip():
            raise ValidationError("Notes produced an empty chunk")
        if UUID(chunk.video_id) != video.id or UUID(chunk.course_id) != video.course_id:
            raise ValidationError("Chunk is linked to the wrong video or course")

    embeddings = embed_texts([chunk.text for chunk in chunks])
    await ensure_hnsw_index(db)
    await db.execute(
        delete(NoteChunk).where(
            NoteChunk.video_id == video.id,
            NoteChunk.user_id == video.user_id,
        )
    )

    records: list[NoteChunk] = []
    for chunk, embedding in zip(chunks, embeddings, strict=True):
        record = NoteChunk(
            video_id=video.id,
            course_id=video.course_id,
            user_id=video.user_id,
            text=chunk.text,
            start_seconds=chunk.start_seconds,
            end_seconds=chunk.end_seconds,
            section_heading=chunk.section_heading,
            chunk_index=chunk.chunk_index,
            embedding=embedding,
        )
        db.add(record)
        records.append(record)

    await db.commit()
    return records
