from datetime import UTC, datetime, timedelta

import structlog
from redis.asyncio import Redis

from app.core.config import settings

logger = structlog.get_logger()


class QuotaManager:
    DAILY_LIMITS = {
        "whisper_requests": 2000,
        "llm_requests": 1000,
        "llm_tokens_day": float("inf"),
    }

    HOURLY_LIMITS = {
        "whisper_seconds": 7200,
    }

    MINUTE_LIMITS = {
        "llm_tokens": 6000,
    }

    def __init__(self, redis: Redis | None = None) -> None:
        self.redis = redis or Redis.from_url(settings.redis_url, decode_responses=True)
        self._owns_redis = redis is None

    async def close(self) -> None:
        if self._owns_redis:
            await self.redis.aclose()

    def seconds_until_midnight_utc(self) -> int:
        now = datetime.now(UTC)
        next_midnight = (now + timedelta(days=1)).replace(
            hour=0,
            minute=0,
            second=0,
            microsecond=0,
        )
        return int((next_midnight - now).total_seconds())

    def _counter_key(self, user_id: str, key: str) -> str:
        if key == "llm_tokens":
            return f"groq:quota:{user_id}:llm_tokens_minute"
        if key == "whisper_seconds":
            return f"groq:quota:{user_id}:whisper_seconds_hour"
        return f"groq:quota:{user_id}:{key}"

    def _limit_for(self, key: str) -> int | float:
        if key in self.DAILY_LIMITS:
            return self.DAILY_LIMITS[key]
        if key in self.HOURLY_LIMITS:
            return self.HOURLY_LIMITS[key]
        return self.MINUTE_LIMITS[key]

    def _ttl_for(self, key: str) -> int:
        if key in self.MINUTE_LIMITS:
            return 60
        if key in self.HOURLY_LIMITS:
            return 3600
        return self.seconds_until_midnight_utc()

    async def can_process_video(self, user_id: str, estimated_tokens: int) -> bool:
        llm_requests = int(await self.redis.get(self._counter_key(user_id, "llm_requests")) or 0)
        llm_tokens = int(await self.redis.get(self._counter_key(user_id, "llm_tokens")) or 0)
        allowed = (
            llm_requests + 1 <= self.DAILY_LIMITS["llm_requests"]
            and llm_tokens + estimated_tokens <= self.MINUTE_LIMITS["llm_tokens"]
        )
        if not allowed:
            logger.warning(
                "quota.exhausted",
                user_id=user_id,
                llm_requests=llm_requests,
                llm_tokens=llm_tokens,
                estimated_tokens=estimated_tokens,
            )
        return allowed

    async def increment(self, user_id: str, key: str, amount: int = 1) -> None:
        redis_key = self._counter_key(user_id, key)
        async with self.redis.pipeline(transaction=True) as pipe:
            pipe.incrby(redis_key, amount)
            pipe.expire(redis_key, self._ttl_for(key))
            await pipe.execute()
        logger.info("quota.incremented", user_id=user_id, key=key, amount=amount)

    async def get_usage_summary(self, user_id: str) -> dict:
        keys = ["llm_requests", "llm_tokens", "whisper_requests", "whisper_seconds"]
        summary = {}
        for key in keys:
            used = int(await self.redis.get(self._counter_key(user_id, key)) or 0)
            limit = self._limit_for(key)
            summary[key] = {
                "used": used,
                "limit": limit,
                "remaining": max(int(limit - used), 0) if limit != float("inf") else None,
            }
        return summary

    async def remaining(self, user_id: str, key: str) -> int:
        limit = self._limit_for(key)
        if limit == float("inf"):
            return 2**31 - 1
        used = int(await self.redis.get(self._counter_key(user_id, key)) or 0)
        return max(int(limit - used), 0)
