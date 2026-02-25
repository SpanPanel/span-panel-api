"""span-panel-api - SPAN Panel API Client Library.

A modern, type-safe Python client library for the SPAN Panel API,
supporting MQTT/Homie (v2) transport.
"""

from .auth import download_ca_cert, get_homie_schema, regenerate_passphrase, register_v2
from .detection import DetectionResult, detect_api_version
from .exceptions import (
    SimulationConfigurationError,
    SpanPanelAPIError,
    SpanPanelAuthError,
    SpanPanelConnectionError,
    SpanPanelError,
    SpanPanelServerError,
    SpanPanelTimeoutError,
    SpanPanelValidationError,
)
from .factory import create_span_client
from .models import (
    SpanBatterySnapshot,
    SpanBranchSnapshot,
    SpanCircuitSnapshot,
    SpanPanelSnapshot,
    V2AuthResponse,
    V2StatusInfo,
)
from .mqtt import MqttClientConfig, SpanMqttClient
from .phase_validation import (
    PhaseDistribution,
    are_tabs_opposite_phase,
    get_phase_distribution,
    get_tab_phase,
    suggest_balanced_pairing,
    validate_solar_tabs,
)
from .protocol import CircuitControlProtocol, PanelCapability, SpanPanelClientProtocol, StreamingCapableProtocol
from .simulation import DynamicSimulationEngine

__version__ = "2.0.0"
# fmt: off
__all__ = [  # noqa: RUF022
    # Protocols
    "CircuitControlProtocol",
    "PanelCapability",
    "SpanPanelClientProtocol",
    "StreamingCapableProtocol",
    # Snapshots
    "SpanBatterySnapshot",
    "SpanBranchSnapshot",
    "SpanCircuitSnapshot",
    "SpanPanelSnapshot",
    # Factory
    "create_span_client",
    # Detection
    "DetectionResult",
    "detect_api_version",
    # v2 auth
    "V2AuthResponse",
    "V2StatusInfo",
    "download_ca_cert",
    "get_homie_schema",
    "regenerate_passphrase",
    "register_v2",
    # Transport
    "MqttClientConfig",
    "SpanMqttClient",
    # Phase validation
    "PhaseDistribution",
    "are_tabs_opposite_phase",
    "get_phase_distribution",
    "get_tab_phase",
    "suggest_balanced_pairing",
    "validate_solar_tabs",
    # Exceptions
    "SimulationConfigurationError",
    "SpanPanelAPIError",
    "SpanPanelAuthError",
    "SpanPanelConnectionError",
    "SpanPanelError",
    "SpanPanelServerError",
    "SpanPanelTimeoutError",
    "SpanPanelValidationError",
    # Simulation
    "DynamicSimulationEngine",
]
# fmt: on
