"""Custom exceptions for the SPAN Panel API client."""


class SpanPanelError(Exception):
    """Base exception for all SPAN Panel API errors."""

    pass


class SpanPanelConnectionError(SpanPanelError):
    """Raised when connection to SPAN Panel fails."""

    pass


class SpanPanelAuthError(SpanPanelError):
    """Raised when authentication with SPAN Panel fails."""

    pass


class SpanPanelTimeoutError(SpanPanelError):
    """Raised when a request to SPAN Panel times out."""

    pass


class SpanPanelAPIError(SpanPanelError):
    """Raised when SPAN Panel API returns an error response."""

    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class SpanPanelServerError(SpanPanelAPIError):
    """Raised when SPAN Panel returns a 5xx server error (non-retriable)."""

    pass


class SpanPanelRetriableError(SpanPanelAPIError):
    """Raised when SPAN Panel returns a retriable server error (502, 503, 504)."""

    pass
