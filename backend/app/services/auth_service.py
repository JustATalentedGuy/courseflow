from fastapi import HTTPException, status
from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import (
    create_access_token,
    create_refresh_token,
    hash_password,
    verify_password,
    verify_token,
)
from app.core.token_store import (
    blacklist_refresh_token,
    is_refresh_token_active,
    store_refresh_token,
)
from app.models.user import User


async def register_user(db: AsyncSession, email: str, password: str) -> User:
    existing = await db.scalar(select(User).where(User.email == email.lower()))
    if existing:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Email already registered")

    user = User(email=email.lower(), hashed_password=hash_password(password))
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user


async def authenticate_user(db: AsyncSession, email: str, password: str) -> User:
    user = await db.scalar(select(User).where(User.email == email.lower()))
    if not user or not verify_password(password, user.hashed_password):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Incorrect email or password")
    return user


async def issue_token_pair(redis: Redis, user: User) -> tuple[str, str]:
    access_token = create_access_token(str(user.id))
    refresh_token = create_refresh_token(str(user.id))
    await store_refresh_token(redis, refresh_token, str(user.id))
    return access_token, refresh_token


async def refresh_access_token(redis: Redis, refresh_token: str) -> str:
    user_id = verify_token(refresh_token, expected_type="refresh")
    if not await is_refresh_token_active(redis, refresh_token, user_id):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Refresh token is not active")
    return create_access_token(user_id)


async def logout_refresh_token(redis: Redis, refresh_token: str) -> None:
    user_id = verify_token(refresh_token, expected_type="refresh")
    await blacklist_refresh_token(redis, refresh_token, user_id)
