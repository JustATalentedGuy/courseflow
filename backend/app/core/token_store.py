import bcrypt

from redis.asyncio import Redis

from app.core.config import settings
from app.core.security import decode_token


def hash_token(token: str) -> str:
    return bcrypt.hashpw(token.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def refresh_token_key(user_id: str, token_id: str) -> str:
    return f"auth:refresh:{user_id}:{token_id}"


def refresh_blacklist_key(user_id: str) -> str:
    return f"auth:refresh_blacklist:{user_id}:tokens"


async def store_refresh_token(redis: Redis, token: str, user_id: str) -> None:
    token_id = str(decode_token(token, "refresh")["jti"])
    token_hash = hash_token(token)
    ttl = settings.refresh_token_expire_days * 24 * 60 * 60
    await redis.set(refresh_token_key(user_id, token_id), token_hash, ex=ttl)


async def is_refresh_token_active(redis: Redis, token: str, user_id: str) -> bool:
    token_id = str(decode_token(token, "refresh")["jti"])
    if await redis.sismember(refresh_blacklist_key(user_id), token_id):
        return False
    stored_hash = await redis.get(refresh_token_key(user_id, token_id))
    if not stored_hash:
        return False
    return bcrypt.checkpw(token.encode("utf-8"), stored_hash.encode("utf-8"))


async def blacklist_refresh_token(redis: Redis, token: str, user_id: str) -> None:
    token_id = str(decode_token(token, "refresh")["jti"])
    ttl = settings.refresh_token_expire_days * 24 * 60 * 60
    await redis.delete(refresh_token_key(user_id, token_id))
    await redis.sadd(refresh_blacklist_key(user_id), token_id)
    await redis.expire(refresh_blacklist_key(user_id), ttl)
