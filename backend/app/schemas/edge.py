from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field


class EdgeTokenCreate(BaseModel):
    name: str = Field(default="Local transcript fetcher", min_length=1, max_length=80)


class EdgeTokenCreated(BaseModel):
    id: UUID
    name: str
    token: str
    token_prefix: str
    created_at: datetime | None


class EdgeTokenResponse(BaseModel):
    id: UUID
    name: str
    token_prefix: str
    revoked: bool
    last_seen_at: datetime | None
    created_at: datetime | None


class EdgeClaimRequest(BaseModel):
    worker_id: str = Field(min_length=1, max_length=120)
    limit: int = Field(default=1, ge=1, le=3)


class EdgeClaimedJob(BaseModel):
    id: UUID
    lease_token: str
    job_type: str
    course_id: UUID
    video_id: UUID | None
    youtube_url: str
    youtube_video_id: str | None
    title: str | None = None
    position: int | None = None


class EdgeClaimResponse(BaseModel):
    jobs: list[EdgeClaimedJob]
    poll_after_seconds: int = 20


class EdgeHeartbeatRequest(BaseModel):
    lease_token: str
    worker_id: str = Field(min_length=1, max_length=120)


class EdgePlaylistEntry(BaseModel):
    youtube_video_id: str = Field(min_length=6, max_length=32)
    title: str
    position: int
    duration_seconds: int | None = None


class EdgeMetadataSubmit(BaseModel):
    lease_token: str
    course_title: str
    playlist_id: str
    entries: list[EdgePlaylistEntry]
    idempotency_key: str


class EdgeRawCaptionSegment(BaseModel):
    start: float
    duration: float
    text: str


class EdgeCaptionSubmit(BaseModel):
    lease_token: str
    language: str = "en"
    segments: list[EdgeRawCaptionSegment]
    idempotency_key: str


class EdgeAudioUploadInit(BaseModel):
    lease_token: str
    content_type: str
    size_bytes: int
    sha256: str = Field(min_length=64, max_length=64)
    duration_seconds: float | None = None
    idempotency_key: str


class EdgeAudioUploadInitResponse(BaseModel):
    upload_url: str
    object_uri: str
    max_size_bytes: int
    expires_seconds: int


class EdgeAudioUploadComplete(BaseModel):
    lease_token: str
    object_uri: str
    size_bytes: int
    sha256: str = Field(min_length=64, max_length=64)
    duration_seconds: float | None = None
    idempotency_key: str


class EdgeFailureSubmit(BaseModel):
    lease_token: str
    error_message: str = Field(min_length=1, max_length=2000)
    retry_after_seconds: int | None = Field(default=None, ge=1, le=86400)
    permanent: bool = False


class EdgeStatusResponse(BaseModel):
    pending: int
    leased: int
    retrying: int
    failed: int
    last_seen_at: datetime | None


class RequeueTranscriptsResponse(BaseModel):
    queued: int
    skipped: int
