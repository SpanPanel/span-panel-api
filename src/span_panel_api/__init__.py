"""span-panel-api - SPAN Panel API Client Library.

A modern, type-safe Python client library for the SPAN Panel OpenAPI and gRPC APIs.
"""

# Import our high-level client and exceptions
from .client import SpanPanelClient, set_async_delay_func
from .exceptions import (
    SimulationConfigurationError,
    SpanPanelAPIError,
    SpanPanelAuthError,
    SpanPanelConnectionError,
    SpanPanelError,
    SpanPanelGrpcConnectionError,
    SpanPanelGrpcError,
    SpanPanelRetriableError,
    SpanPanelServerError,
    SpanPanelTimeoutError,
    SpanPanelValidationError,
)
from .factory import create_span_client

# Import unified transport models and capability flags
from .models import PanelCapability, PanelGeneration, SpanCircuitSnapshot, SpanPanelSnapshot

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

# Import transport protocols for type-safe dispatch
from .protocol import (
    AuthCapableProtocol,
    CircuitControlProtocol,
    EnergyCapableProtocol,
    SpanPanelClientProtocol,
    StreamingCapableProtocol,
)

__version__ = "1.0.0"
# fmt: off
__all__ = [  # noqa: RUF022
    # Client
    "SpanPanelClient",
    "set_async_delay_func",
    # Factory
    "create_span_client",
    # Models
    "PanelCapability",
    "PanelGeneration",
    "SpanCircuitSnapshot",
    "SpanPanelSnapshot",
    # Protocols
    "AuthCapableProtocol",
    "CircuitControlProtocol",
    "EnergyCapableProtocol",
    "SpanPanelClientProtocol",
    "StreamingCapableProtocol",
    # Exceptions
    "SimulationConfigurationError",
    "SpanPanelAPIError",
    "SpanPanelAuthError",
    "SpanPanelConnectionError",
    "SpanPanelError",
    "SpanPanelGrpcConnectionError",
    "SpanPanelGrpcError",
    "SpanPanelRetriableError",
    "SpanPanelServerError",
    "SpanPanelTimeoutError",
    "SpanPanelValidationError",
    # Phase validation
    "PhaseDistribution",
    "are_tabs_opposite_phase",
    "get_phase_distribution",
    "get_tab_phase",
    "get_valid_tabs_from_branches",
    "get_valid_tabs_from_panel_data",
    "suggest_balanced_pairing",
    "validate_solar_tabs",
]
# fmt: on
