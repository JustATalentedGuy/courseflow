from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.deps import get_current_user, get_db
from app.models.user import User
from app.schemas.quota import QuotaUsageResponse
from app.services.quota import QuotaManager

router = APIRouter(prefix="/quota", tags=["quota"])


@router.get("/usage", response_model=QuotaUsageResponse)
async def get_quota_usage(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> QuotaUsageResponse:
    quota = QuotaManager()
    try:
        models = [
            await quota.model_usage(db, settings.groq_auto_model),
            await quota.model_usage(db, settings.groq_high_quality_model),
        ]
    finally:
        await quota.close()
    return QuotaUsageResponse(models=models)
