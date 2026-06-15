from youtube_transcript_api import YouTubeTranscriptApi
from youtube_transcript_api.proxies import GenericProxyConfig

from app.core.config import settings


YOUTUBE_BLOCK_MESSAGES = (
    "sign in to confirm you\u2019re not a bot",
    "sign in to confirm you're not a bot",
    "http error 429",
)


def build_transcript_api() -> YouTubeTranscriptApi:
    proxy_url = settings.youtube_proxy_url
    if not proxy_url:
        return YouTubeTranscriptApi()
    return YouTubeTranscriptApi(
        proxy_config=GenericProxyConfig(
            http_url=proxy_url,
            https_url=proxy_url,
        )
    )


def ytdlp_proxy_args() -> list[str]:
    if not settings.youtube_proxy_url:
        return []
    return ["--proxy", settings.youtube_proxy_url]


def is_youtube_block_error(message: str) -> bool:
    lowered = message.lower()
    return any(marker in lowered for marker in YOUTUBE_BLOCK_MESSAGES)


def redact_youtube_error(message: str) -> str:
    if settings.youtube_proxy_url:
        message = message.replace(settings.youtube_proxy_url, "<redacted-proxy>")
    return message


def youtube_block_message() -> str:
    if settings.youtube_proxy_url:
        return "YouTube blocked the configured proxy; retrying with a new proxy session"
    return (
        "YouTube blocks this server IP. Configure YOUTUBE_PROXY_URL with a rotating "
        "residential HTTP proxy and retry the video."
    )
