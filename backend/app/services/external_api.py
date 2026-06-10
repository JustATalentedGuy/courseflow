import asyncio
import time
from collections.abc import Awaitable, Callable
from typing import TypeVar

from app.core.exceptions import PermanentAPIError, TemporaryAPIError

T = TypeVar("T")

TEMPORARY_STATUS_CODES = {408, 409, 425, 429, 500, 502, 503, 504}
PERMANENT_STATUS_CODES = {400, 401, 403, 404, 422}


def classify_external_error(exc: Exception, service: str) -> Exception:
    status_code = getattr(exc, "status_code", None)
    message = f"{service} request failed"
    if status_code in TEMPORARY_STATUS_CODES:
        return TemporaryAPIError(f"{message} with status {status_code}")
    if status_code in PERMANENT_STATUS_CODES:
        return PermanentAPIError(f"{message} with status {status_code}")

    name = type(exc).__name__.lower()
    if any(term in name for term in ("timeout", "connection", "rate", "serviceunavailable")):
        return TemporaryAPIError(message)
    return PermanentAPIError(message)


async def call_external_async(
    operation: Callable[[], Awaitable[T]],
    service: str,
    max_attempts: int = 3,
    base_delay_seconds: float = 1.0,
) -> T:
    for attempt in range(max_attempts):
        try:
            return await operation()
        except (TemporaryAPIError, PermanentAPIError):
            raise
        except Exception as exc:
            classified = classify_external_error(exc, service)
            if isinstance(classified, PermanentAPIError) or attempt == max_attempts - 1:
                raise classified from exc
            await asyncio.sleep(base_delay_seconds * (2**attempt))
    raise TemporaryAPIError(f"{service} request failed")


def call_external_sync(
    operation: Callable[[], T],
    service: str,
    max_attempts: int = 3,
    base_delay_seconds: float = 1.0,
) -> T:
    for attempt in range(max_attempts):
        try:
            return operation()
        except (TemporaryAPIError, PermanentAPIError):
            raise
        except Exception as exc:
            classified = classify_external_error(exc, service)
            if isinstance(classified, PermanentAPIError) or attempt == max_attempts - 1:
                raise classified from exc
            time.sleep(base_delay_seconds * (2**attempt))
    raise TemporaryAPIError(f"{service} request failed")
