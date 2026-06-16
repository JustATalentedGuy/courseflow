from uuid import UUID

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.deps import get_current_edge_token, get_current_user, get_db
from app.models.edge import EdgeFetcherToken
from app.models.user import User
from app.schemas.edge import (
    EdgeAudioUploadComplete,
    EdgeAudioUploadInit,
    EdgeAudioUploadInitResponse,
    EdgeCaptionSubmit,
    EdgeClaimRequest,
    EdgeClaimResponse,
    EdgeFailureSubmit,
    EdgeHeartbeatRequest,
    EdgeMetadataSubmit,
    EdgeStatusResponse,
    EdgeTokenCreate,
    EdgeTokenCreated,
    EdgeTokenResponse,
)
from app.services.edge_fetcher import (
    claim_edge_jobs,
    complete_audio_upload,
    create_fetcher_token,
    edge_status,
    fail_edge_job,
    heartbeat_edge_job,
    init_audio_upload,
    list_fetcher_tokens,
    revoke_fetcher_token,
    submit_caption_transcript,
    submit_metadata,
)

router = APIRouter(prefix="/edge", tags=["edge-fetcher"])


def _token_response(row: EdgeFetcherToken) -> EdgeTokenResponse:
    return EdgeTokenResponse(
        id=row.id,
        name=row.name,
        token_prefix=row.token_prefix,
        revoked=row.revoked,
        last_seen_at=row.last_seen_at,
        created_at=row.created_at,
    )


@router.post("/tokens", response_model=EdgeTokenCreated, status_code=status.HTTP_201_CREATED)
async def create_token(
    payload: EdgeTokenCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> EdgeTokenCreated:
    row, token = await create_fetcher_token(db, current_user.id, payload.name)
    return EdgeTokenCreated(
        id=row.id,
        name=row.name,
        token=token,
        token_prefix=row.token_prefix,
        created_at=row.created_at,
    )


@router.get("/tokens", response_model=list[EdgeTokenResponse])
async def list_tokens(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[EdgeTokenResponse]:
    return [_token_response(row) for row in await list_fetcher_tokens(db, current_user.id)]


@router.delete("/tokens/{token_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_token(
    token_id: UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> None:
    await revoke_fetcher_token(db, current_user.id, token_id)


@router.post("/jobs/claim", response_model=EdgeClaimResponse)
async def claim_jobs(
    payload: EdgeClaimRequest,
    edge_token: EdgeFetcherToken = Depends(get_current_edge_token),
    db: AsyncSession = Depends(get_db),
) -> EdgeClaimResponse:
    jobs = await claim_edge_jobs(db, edge_token, payload.worker_id, payload.limit)
    return EdgeClaimResponse(jobs=jobs, poll_after_seconds=20 if jobs else 30)


@router.post("/jobs/{job_id}/heartbeat", status_code=status.HTTP_204_NO_CONTENT)
async def heartbeat_job(
    job_id: UUID,
    payload: EdgeHeartbeatRequest,
    edge_token: EdgeFetcherToken = Depends(get_current_edge_token),
    db: AsyncSession = Depends(get_db),
) -> None:
    await heartbeat_edge_job(db, edge_token, payload.lease_token, payload.worker_id)


@router.post("/jobs/{job_id}/metadata", status_code=status.HTTP_204_NO_CONTENT)
async def submit_job_metadata(
    job_id: UUID,
    payload: EdgeMetadataSubmit,
    edge_token: EdgeFetcherToken = Depends(get_current_edge_token),
    db: AsyncSession = Depends(get_db),
) -> None:
    await submit_metadata(db, edge_token, job_id, payload)


@router.post("/jobs/{job_id}/captions", status_code=status.HTTP_204_NO_CONTENT)
async def submit_job_captions(
    job_id: UUID,
    payload: EdgeCaptionSubmit,
    edge_token: EdgeFetcherToken = Depends(get_current_edge_token),
    db: AsyncSession = Depends(get_db),
) -> None:
    await submit_caption_transcript(db, edge_token, job_id, payload)


@router.post("/jobs/{job_id}/audio-upload", response_model=EdgeAudioUploadInitResponse)
async def init_job_audio_upload(
    job_id: UUID,
    payload: EdgeAudioUploadInit,
    edge_token: EdgeFetcherToken = Depends(get_current_edge_token),
    db: AsyncSession = Depends(get_db),
) -> EdgeAudioUploadInitResponse:
    upload_url, object_uri = await init_audio_upload(db, edge_token, job_id, payload)
    return EdgeAudioUploadInitResponse(
        upload_url=upload_url,
        object_uri=object_uri,
        max_size_bytes=settings.edge_audio_max_upload_mb * 1024 * 1024,
        expires_seconds=900,
    )


@router.post("/jobs/{job_id}/audio-complete", status_code=status.HTTP_204_NO_CONTENT)
async def complete_job_audio_upload(
    job_id: UUID,
    payload: EdgeAudioUploadComplete,
    edge_token: EdgeFetcherToken = Depends(get_current_edge_token),
    db: AsyncSession = Depends(get_db),
) -> None:
    await complete_audio_upload(db, edge_token, job_id, payload)


@router.post("/jobs/{job_id}/fail", status_code=status.HTTP_204_NO_CONTENT)
async def fail_job(
    job_id: UUID,
    payload: EdgeFailureSubmit,
    edge_token: EdgeFetcherToken = Depends(get_current_edge_token),
    db: AsyncSession = Depends(get_db),
) -> None:
    await fail_edge_job(
        db,
        edge_token,
        job_id,
        payload.lease_token,
        payload.error_message,
        payload.retry_after_seconds,
        payload.permanent,
    )


@router.get("/status", response_model=EdgeStatusResponse)
async def get_edge_status(
    edge_token: EdgeFetcherToken = Depends(get_current_edge_token),
    db: AsyncSession = Depends(get_db),
) -> EdgeStatusResponse:
    return EdgeStatusResponse(**await edge_status(db, edge_token))
