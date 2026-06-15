from dataclasses import dataclass

from app.services.object_storage import build_object_uri


@dataclass(frozen=True)
class ImagePlaceholder:
    token: str
    position: int
    url: str = ""
    description: str = "lecture screenshot"


SCREENSHOT_HINTS = (
    "as you can see",
    "in this diagram",
    "this graph shows",
    "on the slide",
    "this chart",
)


def transcript_needs_screenshots(transcript_text: str) -> bool:
    lower = transcript_text.lower()
    return any(hint in lower for hint in SCREENSHOT_HINTS)


def extract_slide_screenshots(
    user_id: str,
    video_id: str,
    timestamps: list[float],
) -> list[ImagePlaceholder]:
    placeholders: list[ImagePlaceholder] = []
    for index, timestamp in enumerate(timestamps, start=1):
        token = f"{{{{IMG_{index:03d}}}}}"
        url = build_object_uri(f"{user_id}/{video_id}/frames/{timestamp:.2f}.jpg")
        placeholders.append(
            ImagePlaceholder(
                token=token,
                position=index - 1,
                url=url,
                description=f"Frame at {timestamp:.2f}s",
            )
        )
    return placeholders


def inject_placeholder_tokens(transcript_text: str, placeholders: list[ImagePlaceholder]) -> str:
    result = transcript_text
    offset = 0
    for placeholder in sorted(placeholders, key=lambda item: item.position):
        position = max(0, min(len(result), placeholder.position + offset))
        token_text = f" {placeholder.token} "
        result = result[:position] + token_text + result[position:]
        offset += len(token_text)
    return " ".join(result.split())


def _markdown_image(placeholder: ImagePlaceholder) -> str:
    if not placeholder.url:
        raise ValueError("Image placeholders require a user-prefixed object URL")
    url = placeholder.url
    return f"![{placeholder.description}]({url})"


def restore_images_in_notes(notes_markdown: str, placeholders: list[ImagePlaceholder]) -> str:
    restored = notes_markdown
    missing_images: list[str] = []

    for placeholder in placeholders:
        image_markdown = _markdown_image(placeholder)
        if placeholder.token not in restored:
            missing_images.append(image_markdown)
            continue

        restored = restored.replace(placeholder.token, image_markdown, 1)
        restored = restored.replace(placeholder.token, "")

    restored = "\n".join(line.rstrip() for line in restored.splitlines()).strip()
    if missing_images:
        restored = f"{restored}\n\n" + "\n\n".join(missing_images)
    return restored
