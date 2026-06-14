from datetime import datetime

from pydantic import BaseModel


class QuotaWindowUsage(BaseModel):
    used: int
    reserved: int
    limit: int
    remaining: int
    utilization_percent: float
    resets_at: datetime


class ModelQuotaUsage(BaseModel):
    model: str
    requests_minute: QuotaWindowUsage
    requests_day: QuotaWindowUsage
    tokens_minute: QuotaWindowUsage
    tokens_day: QuotaWindowUsage


class QuotaUsageResponse(BaseModel):
    models: list[ModelQuotaUsage]
