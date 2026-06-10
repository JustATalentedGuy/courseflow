from collections.abc import AsyncGenerator
from uuid import UUID

from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.security import verify_token
from app.db.session import get_session
from app.models.user import User


oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/login")


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async for session in get_session():
        yield session


async def get_redis() -> AsyncGenerator[Redis, None]:
    redis = Redis.from_url(settings.redis_url, decode_responses=True)
    try:
        yield redis
    finally:
        await redis.aclose()


async def get_current_user(
    token: str = Depends(oauth2_scheme),
    db: AsyncSession = Depends(get_db),
) -> User:
    user_id = verify_token(token, expected_type="access")
    user = await db.get(User, UUID(user_id))
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found")
    return user
