from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from redis.asyncio import Redis
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.exceptions import DiagramQuotaWaitError
from app.models.diagram import CloudflareUsageEvent

RESERVATION_TTL_SECONDS = 300

RESERVE_SCRIPT = """
local now = tonumber(ARGV[1])
local reservation_id = ARGV[2]
local expires_at = tonumber(ARGV[3])
local amount = tonumber(ARGV[4])
local budget = tonumber(ARGV[5])
local concurrency = tonumber(ARGV[6])
local ttl = tonumber(ARGV[7])

local blocked_until = tonumber(redis.call('GET', KEYS[3]) or '0')
if blocked_until > now then
  return {'blocked', blocked_until - now}
end

local values = redis.call('HGETALL', KEYS[2])
local reserved = 0
local active = 0
for i = 1, #values, 2 do
  local field = values[i]
  local expiration, value = string.match(values[i + 1], '([^:]+):([^:]+)')
  if tonumber(expiration) <= now then
    redis.call('HDEL', KEYS[2], field)
  else
    active = active + 1
    reserved = reserved + tonumber(value)
  end
end

if active >= concurrency then
  return {'concurrency', 0}
end
local used = tonumber(redis.call('GET', KEYS[1]) or '0')
if used + reserved + amount > budget then
  return {'daily', 0}
end
redis.call('HSET', KEYS[2], reservation_id, expires_at .. ':' .. amount)
redis.call('EXPIRE', KEYS[2], ttl)
return {'ok', 0}
"""

RECONCILE_SCRIPT = """
redis.call('HDEL', KEYS[2], ARGV[1])
redis.call('INCRBY', KEYS[1], tonumber(ARGV[2]))
redis.call('EXPIRE', KEYS[1], tonumber(ARGV[3]))
return 1
"""


@dataclass(frozen=True)
class CloudflareReservation:
    reservation_id: str
    estimated_neurons: int


class CloudflareQuotaManager:
    def __init__(self, redis: Redis | None = None) -> None:
        self.redis = redis or Redis.from_url(settings.redis_url, decode_responses=True)
        self._owns_redis = redis is None

    async def close(self) -> None:
        if self._owns_redis:
            await self.redis.aclose()

    @staticmethod
    def seconds_until_midnight_utc() -> int:
        now = datetime.now(UTC)
        midnight = (now + timedelta(days=1)).replace(
            hour=0,
            minute=0,
            second=0,
            microsecond=0,
        )
        return max(1, int((midnight - now).total_seconds()))

    def _keys(self) -> tuple[str, str, str, str]:
        day = datetime.now(UTC).date().isoformat()
        prefix = "cloudflare:images"
        return (
            f"{prefix}:neurons:day:{day}",
            f"{prefix}:reservations",
            f"{prefix}:blocked-until",
            f"{prefix}:state-ready:{day}",
        )

    async def ensure_counter(self, db: AsyncSession) -> None:
        used_key, _, _, ready_key = self._keys()
        if await self.redis.exists(ready_key):
            return
        day_start = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
        used = int(
            await db.scalar(
                select(func.coalesce(func.sum(CloudflareUsageEvent.estimated_neurons), 0)).where(
                    CloudflareUsageEvent.created_at >= day_start
                )
            )
            or 0
        )
        ttl = self.seconds_until_midnight_utc()
        async with self.redis.pipeline(transaction=True) as pipe:
            pipe.set(used_key, used, ex=ttl, nx=True)
            pipe.set(ready_key, "1", ex=ttl)
            await pipe.execute()

    async def reserve(self, db: AsyncSession, estimated_neurons: int) -> CloudflareReservation:
        await self.ensure_counter(db)
        keys = self._keys()
        now = int(datetime.now(UTC).timestamp())
        reservation_id = str(uuid4())
        result = await self.redis.eval(
            RESERVE_SCRIPT,
            3,
            *keys[:3],
            now,
            reservation_id,
            now + RESERVATION_TTL_SECONDS,
            max(1, estimated_neurons),
            settings.cloudflare_daily_neuron_budget,
            max(1, settings.cloudflare_image_concurrency),
            RESERVATION_TTL_SECONDS,
        )
        reason = result[0].decode() if isinstance(result[0], bytes) else str(result[0])
        if reason == "ok":
            return CloudflareReservation(reservation_id, max(1, estimated_neurons))
        if reason == "daily":
            raise DiagramQuotaWaitError(
                "Cloudflare image daily allowance reached",
                retry_after=self.seconds_until_midnight_utc(),
                daily=True,
            )
        retry_after = max(1, int(result[1] or 5)) if reason == "blocked" else 5
        raise DiagramQuotaWaitError(
            "Cloudflare image generation is temporarily busy",
            retry_after=retry_after,
        )

    async def release(self, reservation: CloudflareReservation) -> None:
        await self.redis.hdel(self._keys()[1], reservation.reservation_id)

    async def reconcile(self, reservation: CloudflareReservation) -> None:
        keys = self._keys()
        await self.redis.eval(
            RECONCILE_SCRIPT,
            2,
            keys[0],
            keys[1],
            reservation.reservation_id,
            reservation.estimated_neurons,
            self.seconds_until_midnight_utc(),
        )

    async def block(self, retry_after: int) -> None:
        key = self._keys()[2]
        until = int(datetime.now(UTC).timestamp()) + max(1, retry_after)
        current = int(await self.redis.get(key) or 0)
        if until > current:
            await self.redis.set(key, until, ex=max(1, retry_after) + 5)
