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


class SpanPanelSchemaVersionError(SpanPanelError):
    """The panel reports a data-model-version this library cannot interpret.

    Distinct from SpanPanelAdapterMissingError, because the remedy differs. A
    missing adapter is a known schema with no installed parser — install or
    update the adapter package. This is a schema whose *major cannot even be
    determined*, so no adapter can be named. That is a panel this library has
    never seen, and the honest response is to say so.

    Absence is not this error: a panel that publishes no data-model-version at
    all is speaking the flat schema, which is a real and supported signal.
    """

    def __init__(self, data_model_version: str) -> None:
        self.data_model_version = data_model_version
        super().__init__(
            f"Cannot determine a schema major from data-model-version {data_model_version!r}. "
            "Expected MAJOR.MINOR[.PATCH]. Refusing to guess — parsing this panel with the "
            "wrong schema would produce plausible but incorrect power and energy values. "
            "Please report this value."
        )


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
