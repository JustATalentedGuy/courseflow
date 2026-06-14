class CourseFlowError(Exception):
    """Base exception for expected CourseFlow failures."""


class ValidationError(CourseFlowError):
    pass


class TranscriptValidationError(ValidationError):
    pass


class TranscriptExtractionError(CourseFlowError):
    pass


class NotesValidationError(ValidationError):
    pass


class ManualChunkIndexError(ValidationError):
    pass


class QuotaExhaustedError(CourseFlowError):
    def __init__(self, message: str, retry_after: int | None = None) -> None:
        super().__init__(message)
        self.retry_after = retry_after


class GroqQuotaWaitError(CourseFlowError):
    def __init__(
        self,
        message: str,
        *,
        model: str,
        window: str,
        retry_after: int,
        dimension: str | None = None,
    ) -> None:
        super().__init__(message)
        self.model = model
        self.window = window
        self.retry_after = retry_after
        self.dimension = dimension or window


class DiagramQuotaWaitError(CourseFlowError):
    def __init__(self, message: str, *, retry_after: int, daily: bool = False) -> None:
        super().__init__(message)
        self.retry_after = retry_after
        self.daily = daily


class ExternalAPIError(CourseFlowError):
    pass


class TemporaryAPIError(ExternalAPIError):
    pass


class PermanentAPIError(ExternalAPIError):
    pass


class UserIsolationError(CourseFlowError):
    pass


class QuizSessionExpiredError(CourseFlowError):
    pass
