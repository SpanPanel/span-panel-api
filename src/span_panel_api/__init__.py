"""span-panel-api - SPAN Panel API Client Library.

A modern, type-safe Python client library for the SPAN Panel REST API.
"""

# Import our high-level client and exceptions
from .client import SpanPanelClient
from .exceptions import (
    SpanPanelAPIError,
    SpanPanelAuthError,
    SpanPanelConnectionError,
    SpanPanelError,
    SpanPanelRetriableError,
    SpanPanelServerError,
    SpanPanelTimeoutError,
)

__version__ = "1.0.0"
# fmt: off
__all__ = [
    "SpanPanelAPIError",
    "SpanPanelAuthError",
    "SpanPanelClient",
    "SpanPanelConnectionError",
    "SpanPanelError",
    "SpanPanelRetriableError",
    "SpanPanelServerError",
    "SpanPanelTimeoutError",
]
# fmt: on
