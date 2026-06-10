import structlog
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

from app.api.v1.router import api_router
from app.core.config import settings
from app.core.limiter import limiter
from app.core.logging import configure_logging
from app.core.exceptions import (
    ManualChunkIndexError,
    NotesValidationError,
    QuizSessionExpiredError,
    QuotaExhaustedError,
    TranscriptExtractionError,
    UserIsolationError,
)
from app.core.exceptions import ValidationError as CourseFlowValidationError

settings.validate_runtime()
configure_logging()
logger = structlog.get_logger()

app = FastAPI(title="CourseFlow API")
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(QuotaExhaustedError)
async def quota_handler(request: Request, exc: QuotaExhaustedError) -> JSONResponse:
    return JSONResponse(
        status_code=429,
        content={"detail": str(exc), "retry_after": exc.retry_after},
    )


@app.exception_handler(UserIsolationError)
async def isolation_handler(request: Request, exc: UserIsolationError) -> JSONResponse:
    return JSONResponse(status_code=403, content={"detail": "Access denied"})


@app.exception_handler(CourseFlowValidationError)
async def validation_handler(request: Request, exc: CourseFlowValidationError) -> JSONResponse:
    return JSONResponse(status_code=400, content={"detail": str(exc)})


@app.exception_handler(ManualChunkIndexError)
async def manual_chunk_handler(request: Request, exc: ManualChunkIndexError) -> JSONResponse:
    return JSONResponse(status_code=422, content={"detail": str(exc)})


@app.exception_handler(TranscriptExtractionError)
async def transcript_extraction_handler(
    request: Request,
    exc: TranscriptExtractionError,
) -> JSONResponse:
    return JSONResponse(status_code=400, content={"detail": str(exc)})


@app.exception_handler(NotesValidationError)
async def notes_validation_handler(request: Request, exc: NotesValidationError) -> JSONResponse:
    return JSONResponse(status_code=400, content={"detail": str(exc)})


@app.exception_handler(QuizSessionExpiredError)
async def quiz_session_expired_handler(
    request: Request,
    exc: QuizSessionExpiredError,
) -> JSONResponse:
    return JSONResponse(status_code=404, content={"detail": str(exc)})


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


app.include_router(api_router)
