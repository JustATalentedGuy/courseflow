from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_current_user, get_db
from app.models.user import User
from app.schemas.search import SearchRequest, SearchResult
from app.services.search import semantic_search, suggest_search_terms

router = APIRouter(prefix="/search", tags=["search"])


@router.post("", response_model=list[SearchResult])
async def search_notes(
    payload: SearchRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[SearchResult]:
    return await semantic_search(
        query=payload.query,
        user_id=str(current_user.id),
        course_id=str(payload.course_id) if payload.course_id else None,
        top_k=payload.top_k,
        db=db,
    )


@router.get("/suggest", response_model=list[str])
async def suggest(
    q: str = Query(default="", max_length=500),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[str]:
    return await suggest_search_terms(q, str(current_user.id), db)
