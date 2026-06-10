from datetime import UTC, datetime, timedelta
from uuid import uuid4

from fastapi import HTTPException, status
from jose import JWTError, jwt
from passlib.context import CryptContext

from app.core.config import settings


pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def hash_password(plain: str) -> str:
    return pwd_context.hash(plain)


def verify_password(plain: str, hashed: str) -> bool:
    return pwd_context.verify(plain, hashed)


def _create_token(user_id: str, token_type: str, expires_delta: timedelta) -> str:
    now = datetime.now(UTC)
    payload = {
        "sub": user_id,
        "type": token_type,
        "iat": now,
        "exp": now + expires_delta,
        "jti": str(uuid4()),
    }
    return jwt.encode(payload, settings.secret_key, algorithm=settings.algorithm)


def create_access_token(user_id: str, expires_delta: timedelta | None = None) -> str:
    delta = expires_delta or timedelta(minutes=settings.access_token_expire_minutes)
    return _create_token(user_id=user_id, token_type="access", expires_delta=delta)


def create_refresh_token(user_id: str) -> str:
    return _create_token(
        user_id=user_id,
        token_type="refresh",
        expires_delta=timedelta(days=settings.refresh_token_expire_days),
    )


def verify_token(token: str, expected_type: str = "access") -> str:
    return str(decode_token(token, expected_type)["sub"])


def decode_token(token: str, expected_type: str) -> dict:
    credentials_error = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(token, settings.secret_key, algorithms=[settings.algorithm])
        user_id = payload.get("sub")
        token_type = payload.get("type")
    except JWTError as exc:
        raise credentials_error from exc

    if not user_id or token_type != expected_type or not payload.get("jti"):
        raise credentials_error
    return payload
