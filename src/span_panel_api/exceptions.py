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

    def __str__(self) -> str:
        return self.args[0] if self.args else ""


class SpanPanelRetriableError(SpanPanelAPIError):
    """Retriable server error (502, 503, 504)."""


class SpanPanelServerError(SpanPanelAPIError):
    """Server error (500)."""


class SimulationConfigurationError(SpanPanelError):
    """Simulation configuration is invalid or missing required data."""


class SpanPanelGrpcError(SpanPanelError):
    """Base class for gRPC transport errors."""


class SpanPanelGrpcConnectionError(SpanPanelGrpcError):
    """Failed to connect to panel via gRPC."""
