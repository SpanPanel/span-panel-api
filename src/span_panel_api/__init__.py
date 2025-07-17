"""span-panel-api - SPAN Panel API Client Library.

A modern, type-safe Python client library for the SPAN Panel REST API.
"""

# Import our high-level client and exceptions
from .client import SpanPanelClient, set_async_delay_func
from .exceptions import (
    SimulationConfigurationError,
    SpanPanelAPIError,
    SpanPanelAuthError,
    SpanPanelConnectionError,
    SpanPanelError,
    SpanPanelRetriableError,
    SpanPanelServerError,
    SpanPanelTimeoutError,
    SpanPanelValidationError,
)

# Import phase validation utilities
from .phase_validation import (
    PhaseDistribution,
    are_tabs_opposite_phase,
    get_phase_distribution,
    get_tab_phase,
    get_valid_tabs_from_branches,
    get_valid_tabs_from_panel_data,
    suggest_balanced_pairing,
    validate_solar_tabs,
)

__version__ = "1.0.0"
# fmt: off
__all__ = [
    "PhaseDistribution",
    "SimulationConfigurationError",
    "SpanPanelAPIError",
    "SpanPanelAuthError",
    "SpanPanelClient",
    "SpanPanelConnectionError",
    "SpanPanelError",
    "SpanPanelRetriableError",
    "SpanPanelServerError",
    "SpanPanelTimeoutError",
    "SpanPanelValidationError",
    "are_tabs_opposite_phase",
    "get_phase_distribution",
    "get_tab_phase",
    "get_valid_tabs_from_branches",
    "get_valid_tabs_from_panel_data",
    "set_async_delay_func",
    "suggest_balanced_pairing",
    "validate_solar_tabs",
]
# fmt: on
