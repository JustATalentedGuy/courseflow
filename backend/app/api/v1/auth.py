from fastapi import APIRouter, Depends, Request, Response, status
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_current_user, get_db, get_redis
from app.core.limiter import limiter
from app.models.user import User
from app.schemas.auth import (
    AccessToken,
    LoginRequest,
    LogoutRequest,
    RefreshRequest,
    RegisterRequest,
    TokenPair,
    UserResponse,
)
from app.services.auth_service import (
    authenticate_user,
    issue_token_pair,
    logout_refresh_token,
    refresh_access_token,
    register_user,
)

router = APIRouter(prefix="/auth", tags=["auth"])


def to_user_response(user: User) -> UserResponse:
    return UserResponse(
        user_id=str(user.id),
        email=user.email,
        created_at=user.created_at.isoformat() if user.created_at else None,
    )


@router.post("/register", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
async def register(payload: RegisterRequest, db: AsyncSession = Depends(get_db)) -> UserResponse:
    user = await register_user(db, payload.email, payload.password)
    return to_user_response(user)


@router.post("/login", response_model=TokenPair)
@limiter.limit("10/minute")
async def login(
    request: Request,
    payload: LoginRequest,
    db: AsyncSession = Depends(get_db),
    redis: Redis = Depends(get_redis),
) -> TokenPair:
    user = await authenticate_user(db, payload.email, payload.password)
    access_token, refresh_token = await issue_token_pair(redis, user)
    return TokenPair(access_token=access_token, refresh_token=refresh_token)


@router.post("/refresh", response_model=AccessToken)
async def refresh(payload: RefreshRequest, redis: Redis = Depends(get_redis)) -> AccessToken:
    access_token = await refresh_access_token(redis, payload.refresh_token)
    return AccessToken(access_token=access_token)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(payload: LogoutRequest, redis: Redis = Depends(get_redis)) -> Response:
    await logout_refresh_token(redis, payload.refresh_token)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/me", response_model=UserResponse)
async def me(current_user: User = Depends(get_current_user)) -> UserResponse:
    return to_user_response(current_user)
