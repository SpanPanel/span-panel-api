"""SPAN Panel API exceptions."""


class SpanPanelError(Exception):
    """Base exception for SPAN Panel API errors."""


class SpanPanelAuthError(SpanPanelError):
    """Authentication failed."""


class SpanPanelConnectionError(SpanPanelError):
    """Connection to SPAN panel failed."""


class SpanPanelTimeoutError(SpanPanelError):
    """Request timed out."""


class SpanPanelValidationError(SpanPanelError):
    """Data validation failed."""


class SpanPanelAPIError(SpanPanelError):
    """General API error."""

    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class SpanPanelServerError(SpanPanelAPIError):
    """Server error (500)."""


class SpanPanelStaleDataError(SpanPanelError):
    """Raised when get_snapshot() is called while the client isn't live.

    Distinct from SpanPanelConnectionError: this means the client is running
    but data cannot be trusted right now (broker disconnected, or the Homie
    device has declared $state=disconnected/lost).
    """


class SpanPanelAdapterMissingError(SpanPanelError):
    """No installed adapter covers the schema this panel publishes."""

    def __init__(self, needed: str, reason: str, available: list[str]) -> None:
        self.needed = needed
        self.reason = reason
        self.available = available
        super().__init__(
            f"Panel requires adapter {needed!r} (reason: {reason}); "
            f"installed adapters: {sorted(available)}. "
            "Update the integration or install the missing adapter package."
        )
