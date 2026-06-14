from functools import lru_cache

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    database_url: str = "postgresql+asyncpg://courseflow:password@localhost:5432/courseflow"
    redis_url: str = "redis://localhost:6379/0"

    minio_endpoint: str = "localhost:9000"
    minio_access_key: str = "minioadmin"
    minio_secret_key: str = "minioadmin"
    minio_bucket: str = "courseflow"
    minio_secure: bool = False
    diagram_renderer_url: str = "http://diagram-renderer:3010"

    secret_key: str = "changeme_in_production_use_openssl_rand_hex_32"
    access_token_expire_minutes: int = 60
    refresh_token_expire_days: int = 7
    algorithm: str = "HS256"

    groq_api_key: str = "your_groq_key_here"
    groq_batch_enabled: bool = False
    groq_auto_model: str = "meta-llama/llama-4-scout-17b-16e-instruct"
    groq_high_quality_model: str = "llama-3.3-70b-versatile"
    groq_daily_reserve_percent: int = 0
    groq_scout_rpm: int = 30
    groq_scout_rpd: int = 1000
    groq_scout_tpm: int = 30_000
    groq_scout_tpd: int = 500_000
    groq_70b_rpm: int = 30
    groq_70b_rpd: int = 1000
    groq_70b_tpm: int = 12_000
    groq_70b_tpd: int = 100_000
    groq_whisper_model: str = "whisper-large-v3-turbo"
    groq_whisper_rpm: int = 20
    groq_whisper_rpd: int = 2000
    groq_whisper_ash: int = 7200
    groq_whisper_asd: int = 28_800
    youtube_audio_download_min_timeout_seconds: int = 120
    youtube_audio_download_max_timeout_seconds: int = 900
    youtube_audio_download_timeout_seconds_per_minute: int = 6
    anthropic_api_key: str = ""
    cloudflare_account_id: str = ""
    cloudflare_api_token: str = ""
    cloudflare_image_model: str = "@cf/black-forest-labs/flux-2-klein-4b"
    cloudflare_daily_neuron_budget: int = 8_000
    cloudflare_image_estimated_neurons: int = 400
    cloudflare_image_concurrency: int = 1

    environment: str = "development"
    log_level: str = "INFO"
    cors_origins: list[str] = Field(default_factory=lambda: ["http://localhost:5173"])

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    @field_validator("cors_origins", mode="before")
    @classmethod
    def parse_cors_origins(cls, value: str | list[str]) -> list[str]:
        if isinstance(value, str):
            return [origin.strip() for origin in value.split(",") if origin.strip()]
        return value

    def validate_runtime(self) -> None:
        if self.environment.lower() != "production":
            return
        if self.secret_key in {
            "changeme_in_production_use_openssl_rand_hex_32",
            "development_only_change_me",
        } or len(self.secret_key) < 32:
            raise RuntimeError("SECRET_KEY must be a unique value of at least 32 characters")
        if not self.cors_origins or "*" in self.cors_origins:
            raise RuntimeError("CORS_ORIGINS must explicitly list the production frontend URL")


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
