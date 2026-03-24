"""span-panel-api - SPAN Panel API Client Library.

A modern, type-safe Python client library for the SPAN Panel API,
supporting MQTT/Homie (v2) transport.
"""

from .auth import (
    delete_fqdn,
    download_ca_cert,
    get_fqdn,
    get_homie_schema,
    get_v2_status,
    regenerate_passphrase,
    register_fqdn,
    register_v2,
)
from .detection import DetectionResult, detect_api_version
from .exceptions import (
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
    FieldMetadata,
    SpanBatterySnapshot,
    SpanCircuitSnapshot,
    SpanEvseSnapshot,
    SpanPanelSnapshot,
    SpanPVSnapshot,
    V2AuthResponse,
    V2HomieSchema,
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
from .protocol import (
    CircuitControlProtocol,
    PanelCapability,
    PanelControlProtocol,
    SpanPanelClientProtocol,
    StreamingCapableProtocol,
)

__version__ = "2.3.2"
# fmt: off
__all__ = [  # noqa: RUF022
    # Protocols
    "CircuitControlProtocol",
    "PanelCapability",
    "PanelControlProtocol",
    "SpanPanelClientProtocol",
    "StreamingCapableProtocol",
    # Metadata
    "FieldMetadata",
    # Snapshots
    "SpanBatterySnapshot",
    "SpanCircuitSnapshot",
    "SpanEvseSnapshot",
    "SpanPVSnapshot",
    "SpanPanelSnapshot",
    # Factory
    "create_span_client",
    # Detection
    "DetectionResult",
    "detect_api_version",
    # v2 auth
    "V2AuthResponse",
    "V2HomieSchema",
    "V2StatusInfo",
    "delete_fqdn",
    "download_ca_cert",
    "get_fqdn",
    "get_homie_schema",
    "get_v2_status",
    "register_fqdn",
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
    "SpanPanelAPIError",
    "SpanPanelAuthError",
    "SpanPanelConnectionError",
    "SpanPanelError",
    "SpanPanelServerError",
    "SpanPanelTimeoutError",
    "SpanPanelValidationError",
]
# fmt: on
