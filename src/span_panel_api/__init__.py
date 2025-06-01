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
    SpanPanelTimeoutError,
)

__version__ = "0.1.0"
__all__ = [
    # High-level client
    "SpanPanelClient",
    # Exceptions
    "SpanPanelError",
    "SpanPanelConnectionError",
    "SpanPanelAuthError",
    "SpanPanelTimeoutError",
    "SpanPanelAPIError",
]
