from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
import re
from uuid import uuid4

import structlog
from redis.asyncio import Redis
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.exceptions import GroqQuotaWaitError
from app.models.groq import GroqUsageEvent

logger = structlog.get_logger()


@dataclass(frozen=True)
class ModelQuotaProfile:
    model: str
    rpm: int
    rpd: int
    tpm: int = 0
    tpd: int = 0
    ash: int = 0
    asd: int = 0

    @property
    def daily_token_target(self) -> int:
        reserve = max(0, min(settings.groq_daily_reserve_percent, 99))
        return self.tpd * (100 - reserve) // 100

    @property
    def is_audio(self) -> bool:
        return self.ash > 0


MODEL_QUOTAS = {
    settings.groq_auto_model: ModelQuotaProfile(
        model=settings.groq_auto_model,
        rpm=settings.groq_scout_rpm,
        rpd=settings.groq_scout_rpd,
        tpm=settings.groq_scout_tpm,
        tpd=settings.groq_scout_tpd,
    ),
    settings.groq_high_quality_model: ModelQuotaProfile(
        model=settings.groq_high_quality_model,
        rpm=settings.groq_70b_rpm,
        rpd=settings.groq_70b_rpd,
        tpm=settings.groq_70b_tpm,
        tpd=settings.groq_70b_tpd,
    ),
    settings.groq_whisper_model: ModelQuotaProfile(
        model=settings.groq_whisper_model,
        rpm=settings.groq_whisper_rpm,
        rpd=settings.groq_whisper_rpd,
        ash=settings.groq_whisper_ash,
        asd=settings.groq_whisper_asd,
    ),
}

RESERVATION_TTL_SECONDS = 300

RESERVE_SCRIPT = """
local now = tonumber(ARGV[1])
local reservation_id = ARGV[2]
local expires_at = tonumber(ARGV[3])
local requested_requests = tonumber(ARGV[4])
local requested_tokens = tonumber(ARGV[5])
local requested_audio = tonumber(ARGV[6])

local blocked_until = tonumber(redis.call('GET', KEYS[9]) or '0')
if blocked_until > now then
  return {'blocked', blocked_until - now}
end

redis.call('ZREMRANGEBYSCORE', KEYS[4], '-inf', now - 60)
redis.call('ZREMRANGEBYSCORE', KEYS[5], '-inf', now - 60)
redis.call('ZREMRANGEBYSCORE', KEYS[6], '-inf', now - 3600)

local reservations = redis.call('HGETALL', KEYS[7])
local reserved_requests = 0
local reserved_tokens = 0
local reserved_audio = 0
for i = 1, #reservations, 2 do
  local field = reservations[i]
  local value = reservations[i + 1]
  local expiration, req, tokens, audio =
    string.match(value, '([^:]+):([^:]+):([^:]+):([^:]+)')
  if tonumber(expiration) <= now then
    redis.call('HDEL', KEYS[7], field)
  else
    reserved_requests = reserved_requests + tonumber(req)
    reserved_tokens = reserved_tokens + tonumber(tokens)
    reserved_audio = reserved_audio + tonumber(audio)
  end
end

local rolling_requests = tonumber(redis.call('ZCARD', KEYS[4]) or '0')
local rolling_tokens = 0
for _, member in ipairs(redis.call('ZRANGE', KEYS[5], 0, -1)) do
  local amount = string.match(member, '|([^|]+)$')
  rolling_tokens = rolling_tokens + tonumber(amount or '0')
end
local rolling_audio = 0
for _, member in ipairs(redis.call('ZRANGE', KEYS[6], 0, -1)) do
  local amount = string.match(member, '|([^|]+)$')
  rolling_audio = rolling_audio + tonumber(amount or '0')
end

local day_requests = tonumber(redis.call('GET', KEYS[1]) or '0')
local day_tokens = tonumber(redis.call('GET', KEYS[2]) or '0')
local day_audio = tonumber(redis.call('GET', KEYS[3]) or '0')
local server_requests = tonumber(redis.call('GET', KEYS[10]) or '0')
local server_tokens = tonumber(redis.call('GET', KEYS[11]) or '0')
local legacy_requests = tonumber(redis.call('GET', KEYS[12]) or '0')
local legacy_tokens = tonumber(redis.call('GET', KEYS[13]) or '0')
local request_day_limit = tonumber(redis.call('GET', KEYS[14]) or ARGV[8])
local token_minute_limit = tonumber(redis.call('GET', KEYS[15]) or ARGV[9])

day_requests = math.max(day_requests, server_requests)
rolling_requests = math.max(rolling_requests, legacy_requests)
rolling_tokens = math.max(rolling_tokens, server_tokens, legacy_tokens)

if day_requests + reserved_requests + requested_requests > request_day_limit then
  return {'daily_requests', 0}
end
if tonumber(ARGV[10]) > 0 and
   day_tokens + reserved_tokens + requested_tokens > tonumber(ARGV[10]) then
  return {'daily_tokens', 0}
end
if tonumber(ARGV[12]) > 0 and
   day_audio + reserved_audio + requested_audio > tonumber(ARGV[12]) then
  return {'daily_audio', 0}
end
if rolling_requests + reserved_requests + requested_requests > tonumber(ARGV[7]) then
  return {'minute_requests', 0}
end
if token_minute_limit > 0 and
   rolling_tokens + reserved_tokens + requested_tokens > token_minute_limit then
  return {'minute_tokens', 0}
end
if tonumber(ARGV[11]) > 0 and
   rolling_audio + reserved_audio + requested_audio > tonumber(ARGV[11]) then
  return {'hour_audio', 0}
end

redis.call(
  'HSET',
  KEYS[7],
  reservation_id,
  expires_at .. ':' .. requested_requests .. ':' .. requested_tokens .. ':' .. requested_audio
)
redis.call('EXPIRE', KEYS[7], tonumber(ARGV[13]))
return {'ok', 0}
"""

RECONCILE_SCRIPT = """
redis.call('HDEL', KEYS[7], ARGV[1])
local now = tonumber(ARGV[2])
local requests = tonumber(ARGV[3])
local tokens = tonumber(ARGV[4])
local audio = tonumber(ARGV[5])
local event_id = ARGV[6]

if requests > 0 then
  redis.call('INCRBY', KEYS[1], requests)
  redis.call('ZADD', KEYS[4], now, event_id)
end
if tokens > 0 then
  redis.call('INCRBY', KEYS[2], tokens)
  redis.call('ZADD', KEYS[5], now, event_id .. '|' .. tokens)
end
if audio > 0 then
  redis.call('INCRBY', KEYS[3], audio)
  redis.call('ZADD', KEYS[6], now, event_id .. '|' .. audio)
end
redis.call('EXPIRE', KEYS[1], tonumber(ARGV[7]))
redis.call('EXPIRE', KEYS[2], tonumber(ARGV[7]))
redis.call('EXPIRE', KEYS[3], tonumber(ARGV[7]))
redis.call('EXPIRE', KEYS[4], 120)
redis.call('EXPIRE', KEYS[5], 120)
redis.call('EXPIRE', KEYS[6], 3700)
return 1
"""

MAX_OBSERVED_SCRIPT = """
local current = tonumber(redis.call('GET', KEYS[1]) or '0')
local observed = tonumber(ARGV[1])
if observed > current then
  redis.call('SET', KEYS[1], observed, 'EX', tonumber(ARGV[2]))
elseif current > 0 then
  redis.call('EXPIRE', KEYS[1], math.max(redis.call('TTL', KEYS[1]), tonumber(ARGV[2])))
end
return math.max(current, observed)
"""


@dataclass(frozen=True)
class QuotaReservation:
    reservation_id: str
    model: str
    estimated_tokens: int = 0
    audio_seconds: int = 0


def _model_key(model: str) -> str:
    return model.replace("/", "_").replace(".", "_")


def _parse_duration_seconds(value: str | None, default: int) -> int:
    if not value:
        return default
    stripped = value.strip().lower()
    try:
        return max(1, int(float(stripped)) + (0 if float(stripped).is_integer() else 1))
    except ValueError:
        pass
    total = 0.0
    for number, unit in re.findall(r"(\d+(?:\.\d+)?)\s*(ms|s|m|h|d)", stripped):
        multiplier = {"ms": 0.001, "s": 1, "m": 60, "h": 3600, "d": 86400}[unit]
        total += float(number) * multiplier
    return max(1, int(total) + (0 if total.is_integer() else 1)) if total else default


def _headers_dict(headers: object | None) -> dict[str, str]:
    if headers is None:
        return {}
    items = headers.items() if hasattr(headers, "items") else []
    return {str(key).lower(): str(value) for key, value in items}


class QuotaManager:
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
        return max(1, int((next_midnight - now).total_seconds()))

    def profile(self, model: str) -> ModelQuotaProfile:
        try:
            return MODEL_QUOTAS[model]
        except KeyError as exc:
            raise ValueError(f"No Groq quota profile configured for {model}") from exc

    def _keys(self, model: str, now: datetime) -> list[str]:
        prefix = f"groq:quota:{_model_key(model)}"
        day = now.date().isoformat()
        minute = int(now.timestamp() // 60)
        return [
            f"{prefix}:requests:day:{day}",
            f"{prefix}:tokens:day:{day}",
            f"{prefix}:audio:day:{day}",
            f"{prefix}:requests:rolling",
            f"{prefix}:tokens:rolling",
            f"{prefix}:audio:rolling",
            f"{prefix}:reservations",
            f"{prefix}:state-ready:{day}",
            f"{prefix}:blocked-until",
            f"{prefix}:server:requests-used",
            f"{prefix}:server:tokens-used",
            f"{prefix}:requests:minute:{minute}",
            f"{prefix}:tokens:minute:{minute}",
            f"{prefix}:server:requests-limit",
            f"{prefix}:server:tokens-limit",
        ]

    def _window_keys(self, model: str, now: datetime) -> list[str]:
        keys = self._keys(model, now)
        return [keys[0], keys[1], keys[11], keys[12], keys[6]]

    async def ensure_counters(self, db: AsyncSession, model: str) -> None:
        now = datetime.now(UTC)
        keys = self._keys(model, now)
        if await self.redis.exists(keys[7]):
            return
        day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        hour_start = now - timedelta(hours=1)
        minute_start = now - timedelta(minutes=1)
        rows = (
            await db.execute(
                select(
                    GroqUsageEvent.id,
                    GroqUsageEvent.charged_tokens,
                    GroqUsageEvent.audio_seconds,
                    GroqUsageEvent.created_at,
                ).where(
                    GroqUsageEvent.model == model,
                    GroqUsageEvent.created_at >= day_start,
                )
            )
        ).all()
        day_requests = len(rows)
        day_tokens = sum(int(row.charged_tokens or 0) for row in rows)
        day_audio = sum(int(row.audio_seconds or 0) for row in rows)
        ttl = self.seconds_until_midnight_utc()
        async with self.redis.pipeline(transaction=True) as pipe:
            pipe.set(keys[0], day_requests, ex=ttl, nx=True)
            pipe.set(keys[1], day_tokens, ex=ttl, nx=True)
            pipe.set(keys[2], day_audio, ex=ttl, nx=True)
            for row in rows:
                created_at = row.created_at
                if created_at is None:
                    continue
                timestamp = created_at.timestamp()
                event_id = str(row.id)
                if created_at >= minute_start:
                    pipe.zadd(keys[3], {event_id: timestamp})
                    if row.charged_tokens:
                        pipe.zadd(keys[4], {f"{event_id}|{row.charged_tokens}": timestamp})
                if created_at >= hour_start and row.audio_seconds:
                    pipe.zadd(keys[5], {f"{event_id}|{row.audio_seconds}": timestamp})
            pipe.set(keys[7], "1", ex=ttl)
            await pipe.execute()

    async def ensure_daily_counters(self, db: AsyncSession, model: str) -> None:
        await self.ensure_counters(db, model)

    async def reserve(
        self,
        db: AsyncSession,
        model: str,
        estimated_tokens: int = 0,
        *,
        audio_seconds: int = 0,
    ) -> QuotaReservation:
        await self.ensure_counters(db, model)
        profile = self.profile(model)
        now = datetime.now(UTC)
        keys = self._keys(model, now)
        reservation_id = str(uuid4())
        result = await self.redis.eval(
            RESERVE_SCRIPT,
            len(keys),
            *keys,
            int(now.timestamp()),
            reservation_id,
            int(now.timestamp()) + RESERVATION_TTL_SECONDS,
            1,
            max(0, estimated_tokens),
            max(0, audio_seconds),
            profile.rpm,
            profile.rpd,
            profile.tpm,
            profile.daily_token_target,
            profile.ash,
            profile.asd,
            RESERVATION_TTL_SECONDS,
        )
        reason = result[0].decode() if isinstance(result[0], bytes) else str(result[0])
        if reason == "ok":
            return QuotaReservation(
                reservation_id=reservation_id,
                model=model,
                estimated_tokens=max(0, estimated_tokens),
                audio_seconds=max(0, audio_seconds),
            )

        retry_after = await self._retry_after(model, reason, now, result)
        daily = reason.startswith("daily")
        logger.warning(
            "quota.exhausted",
            model=model,
            window="daily" if daily else "short",
            dimension=reason,
            estimated_tokens=estimated_tokens,
            audio_seconds=audio_seconds,
            retry_after=retry_after,
        )
        raise GroqQuotaWaitError(
            f"Groq {reason.replace('_', ' ')} limit reached",
            model=model,
            window="daily" if daily else "minute",
            retry_after=retry_after,
            dimension=reason,
        )

    async def reserve_whisper(
        self,
        db: AsyncSession,
        audio_seconds: int,
    ) -> QuotaReservation:
        return await self.reserve(
            db,
            settings.groq_whisper_model,
            audio_seconds=max(10, audio_seconds),
        )

    async def _retry_after(
        self,
        model: str,
        reason: str,
        now: datetime,
        result: list,
    ) -> int:
        if reason == "blocked" and len(result) > 1:
            return max(1, int(result[1]))
        if reason.startswith("daily"):
            return self.seconds_until_midnight_utc()
        keys = self._keys(model, now)
        if reason == "hour_audio":
            oldest = await self.redis.zrange(keys[5], 0, 0, withscores=True)
            return max(1, int(oldest[0][1] + 3600 - now.timestamp()) + 1) if oldest else 60
        zset_key = keys[3] if reason == "minute_requests" else keys[4]
        oldest = await self.redis.zrange(zset_key, 0, 0, withscores=True)
        return max(1, int(oldest[0][1] + 60 - now.timestamp()) + 1) if oldest else 60

    async def release(self, reservation: QuotaReservation) -> None:
        key = self._keys(reservation.model, datetime.now(UTC))[6]
        await self.redis.hdel(key, reservation.reservation_id)

    async def reconcile(
        self,
        reservation: QuotaReservation,
        charged_tokens: int = 0,
        headers: dict[str, str] | None = None,
        *,
        audio_seconds: int | None = None,
    ) -> None:
        now = datetime.now(UTC)
        keys = self._keys(reservation.model, now)
        actual_audio = reservation.audio_seconds if audio_seconds is None else max(0, audio_seconds)
        await self.redis.eval(
            RECONCILE_SCRIPT,
            len(keys),
            *keys,
            reservation.reservation_id,
            int(now.timestamp()),
            1,
            max(0, charged_tokens),
            actual_audio,
            str(uuid4()),
            self.seconds_until_midnight_utc(),
        )
        await self.observe_headers(reservation.model, headers)

    async def observe_headers(
        self,
        model: str,
        headers: dict[str, str] | None,
    ) -> None:
        headers = _headers_dict(headers)
        if not headers:
            return
        now = datetime.now(UTC)
        keys = self._keys(model, now)
        request_limit = self._header_int(
            headers,
            "x-ratelimit-limit-requests",
            self.profile(model).rpd,
        )
        token_limit = self._header_int(
            headers,
            "x-ratelimit-limit-tokens",
            self.profile(model).tpm,
        )
        remaining_requests = self._header_int(
            headers,
            "x-ratelimit-remaining-requests",
            None,
        )
        remaining_tokens = self._header_int(
            headers,
            "x-ratelimit-remaining-tokens",
            None,
        )
        if request_limit and remaining_requests is not None:
            ttl = _parse_duration_seconds(
                headers.get("x-ratelimit-reset-requests"),
                self.seconds_until_midnight_utc(),
            )
            await self.redis.eval(
                MAX_OBSERVED_SCRIPT,
                1,
                keys[9],
                max(request_limit - remaining_requests, 0),
                ttl,
            )
            await self.redis.set(keys[13], request_limit, ex=ttl)
        if token_limit and remaining_tokens is not None:
            ttl = _parse_duration_seconds(
                headers.get("x-ratelimit-reset-tokens"),
                60,
            )
            await self.redis.eval(
                MAX_OBSERVED_SCRIPT,
                1,
                keys[10],
                max(token_limit - remaining_tokens, 0),
                ttl,
            )
            await self.redis.set(keys[14], token_limit, ex=ttl)

    @staticmethod
    def _header_int(
        headers: dict[str, str],
        key: str,
        default: int | None,
    ) -> int | None:
        value = headers.get(key)
        if value is None:
            return default
        try:
            return int(float(value))
        except ValueError:
            return default

    async def wait_from_headers(
        self,
        model: str,
        headers: dict[str, str] | None,
        error_body: str | None = None,
    ) -> GroqQuotaWaitError:
        headers = _headers_dict(headers)
        await self.observe_headers(model, headers)
        profile = self.profile(model)
        message = (error_body or "").lower()
        remaining_requests = self._header_int(
            headers,
            "x-ratelimit-remaining-requests",
            None,
        )
        remaining_tokens = self._header_int(
            headers,
            "x-ratelimit-remaining-tokens",
            None,
        )

        if "audio seconds per day" in message or "asd" in message:
            dimension = "daily_audio"
        elif "audio seconds per hour" in message or "ash" in message:
            dimension = "hour_audio"
        elif "tokens per day" in message or "tpd" in message:
            dimension = "daily_tokens"
        elif "requests per day" in message or "rpd" in message or remaining_requests == 0:
            dimension = "daily_requests"
        elif "requests per minute" in message or "rpm" in message:
            dimension = "minute_requests"
        elif "tokens per minute" in message or "tpm" in message or remaining_tokens == 0:
            dimension = "minute_tokens"
        elif profile.is_audio:
            dimension = "hour_audio"
        else:
            dimension = "minute_tokens"

        reset_header = (
            headers.get("retry-after")
            or (
                headers.get("x-ratelimit-reset-requests")
                if dimension == "daily_requests"
                else headers.get("x-ratelimit-reset-tokens")
            )
        )
        default = (
            self.seconds_until_midnight_utc()
            if dimension.startswith("daily")
            else 3600 if dimension == "hour_audio" else 60
        )
        retry_after = _parse_duration_seconds(reset_header, default)
        daily = dimension.startswith("daily")
        blocked_until = int(datetime.now(UTC).timestamp()) + retry_after
        key = self._keys(model, datetime.now(UTC))[8]
        current = int(await self.redis.get(key) or 0)
        if blocked_until > current:
            await self.redis.set(key, blocked_until, ex=retry_after + 5)
        return GroqQuotaWaitError(
            f"Groq {dimension.replace('_', ' ')} rate limit reached",
            model=model,
            window="daily" if daily else "minute",
            retry_after=retry_after,
            dimension=dimension,
        )

    async def model_usage(self, db: AsyncSession, model: str) -> dict:
        await self.ensure_counters(db, model)
        profile = self.profile(model)
        now = datetime.now(UTC)
        keys = self._keys(model, now)
        await self.redis.zremrangebyscore(keys[3], "-inf", now.timestamp() - 60)
        await self.redis.zremrangebyscore(keys[4], "-inf", now.timestamp() - 60)
        values = await self.redis.mget(keys[:3])
        observed_limits = await self.redis.mget(keys[13], keys[14])
        request_day_limit = int(observed_limits[0] or profile.rpd)
        token_minute_limit = int(observed_limits[1] or profile.tpm)
        reservation_values = await self.redis.hvals(keys[6])
        reserved_requests = 0
        reserved_tokens = 0
        reserved_audio = 0
        for value in reservation_values:
            if isinstance(value, bytes):
                value = value.decode()
            expires, requests, tokens, audio = value.split(":", 3)
            if int(expires) > int(now.timestamp()):
                reserved_requests += int(requests)
                reserved_tokens += int(tokens)
                reserved_audio += int(audio)
        rolling_requests = await self.redis.zcard(keys[3])
        rolling_tokens = await self._sum_zset_amounts(keys[4])

        def window(used: int, reserved: int, limit: int, reset: datetime) -> dict:
            remaining = max(limit - used - reserved, 0)
            return {
                "used": used,
                "reserved": reserved,
                "limit": limit,
                "remaining": remaining,
                "utilization_percent": round(((used + reserved) / limit) * 100, 2)
                if limit
                else 0,
                "resets_at": reset,
            }

        day_reset = now + timedelta(seconds=self.seconds_until_midnight_utc())
        minute_reset = now + timedelta(seconds=60)
        return {
            "model": model,
            "requests_day": window(
                int(values[0] or 0),
                reserved_requests,
                request_day_limit,
                day_reset,
            ),
            "tokens_day": window(
                int(values[1] or 0),
                reserved_tokens,
                profile.daily_token_target,
                day_reset,
            ),
            "requests_minute": window(
                int(rolling_requests),
                reserved_requests,
                profile.rpm,
                minute_reset,
            ),
            "tokens_minute": window(
                rolling_tokens,
                reserved_tokens,
                token_minute_limit,
                minute_reset,
            ),
        }

    async def whisper_usage(self, db: AsyncSession) -> dict:
        model = settings.groq_whisper_model
        await self.ensure_counters(db, model)
        profile = self.profile(model)
        now = datetime.now(UTC)
        keys = self._keys(model, now)
        await self.redis.zremrangebyscore(keys[5], "-inf", now.timestamp() - 3600)
        values = await self.redis.mget(keys[:3])
        observed_request_limit = int(await self.redis.get(keys[13]) or profile.rpd)
        reservations = await self.redis.hvals(keys[6])
        reserved_requests = 0
        reserved_audio = 0
        for value in reservations:
            expires, requests, _, audio = value.split(":", 3)
            if int(expires) > int(now.timestamp()):
                reserved_requests += int(requests)
                reserved_audio += int(audio)
        hour_audio = await self._sum_zset_amounts(keys[5])
        return {
            "model": model,
            "requests_day": {
                "used": int(values[0] or 0),
                "reserved": reserved_requests,
                "limit": observed_request_limit,
                "remaining": max(
                    observed_request_limit - int(values[0] or 0) - reserved_requests,
                    0,
                ),
            },
            "audio_hour": {
                "used": hour_audio,
                "reserved": reserved_audio,
                "limit": profile.ash,
                "remaining": max(profile.ash - hour_audio - reserved_audio, 0),
            },
            "audio_day": {
                "used": int(values[2] or 0),
                "reserved": reserved_audio,
                "limit": profile.asd,
                "remaining": max(profile.asd - int(values[2] or 0) - reserved_audio, 0),
            },
        }

    async def _sum_zset_amounts(self, key: str) -> int:
        total = 0
        for member in await self.redis.zrange(key, 0, -1):
            if isinstance(member, bytes):
                member = member.decode()
            try:
                total += int(member.rsplit("|", 1)[1])
            except (IndexError, ValueError):
                continue
        return total

    async def remaining(self, user_id: str, key: str) -> int:
        del user_id
        if key == "whisper_requests":
            usage = await self.redis.get(
                self._keys(settings.groq_whisper_model, datetime.now(UTC))[0]
            )
            return max(settings.groq_whisper_rpd - int(usage or 0), 0)
        if key == "whisper_seconds":
            keys = self._keys(settings.groq_whisper_model, datetime.now(UTC))
            return max(settings.groq_whisper_ash - await self._sum_zset_amounts(keys[5]), 0)
        return 0

    async def can_process_video(self, user_id: str, estimated_tokens: int) -> bool:
        del user_id
        return estimated_tokens <= self.profile(settings.groq_high_quality_model).tpm
