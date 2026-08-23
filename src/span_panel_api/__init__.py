"""span-panel-api - SPAN Panel API Client Library.

A modern, type-safe Python client library for the SPAN Panel API,
supporting MQTT/Homie (v2) transport.
"""

from importlib.metadata import version as _pkg_version

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
    SpanPanelAdapterIncompatibleError,
    SpanPanelAdapterMissingError,
    SpanPanelAPIError,
    SpanPanelAuthError,
    SpanPanelConnectionError,
    SpanPanelError,
    SpanPanelSchemaVersionError,
    SpanPanelServerError,
    SpanPanelStaleDataError,
    SpanPanelTimeoutError,
    SpanPanelValidationError,
)
from .factory import create_span_client
from .models import (
    ADOPTION_IDENTITY_NODE,
    ADOPTION_TOPOLOGY_NODE,
    DISCOVERY_NAMESPACE,
    AdoptedDevice,
    AdoptedProperty,
    DiscoveredMetadata,
    ExtensionProperty,
    ExtensionSubject,
    FieldMetadata,
    HomieSchemaTypes,
    SpanBatterySnapshot,
    SpanCircuitSnapshot,
    SpanEvseSnapshot,
    SpanMidSnapshot,
    SpanPanelSnapshot,
    SpanPcsSnapshot,
    SpanPVSnapshot,
    V2AuthResponse,
    V2HomieSchema,
    V2StatusInfo,
    is_discovery_path,
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
    AdoptedControlProtocol,
    CircuitControlProtocol,
    EvseControlProtocol,
    PanelCapability,
    PanelControlProtocol,
    SpanPanelClientProtocol,
    StreamingCapableProtocol,
)

__version__ = _pkg_version("span-panel-api")
# fmt: off
__all__ = [  # noqa: RUF022
    # Protocols
    "CircuitControlProtocol",
    # Added 2026-08-19: the charge-current ceiling on a commissioned EV charger,
    # the first settable property outside the panel and its circuits. Purely
    # additive -- a consumer that never asks for it is unaffected, and flat
    # firmware publishes no such property, so the flat adapter answers None and
    # the transport refuses.
    "EvseControlProtocol",
    # Added 2026-08-20 with device-scoped adoption: the first control whose
    # subject this library does not understand. Additive, and authorised by the
    # snapshot rather than by its arguments -- a device the adapter models
    # produces no AdoptedDevice and so cannot be addressed through it.
    "AdoptedControlProtocol",
    "PanelCapability",
    "PanelControlProtocol",
    "SpanPanelClientProtocol",
    "StreamingCapableProtocol",
    # Metadata
    "FieldMetadata",
    "HomieSchemaTypes",
    # Added 2026-08-20: runtime discovery. Purely additive -- an adapter that
    # emits no discovered rows is indistinguishable from one built before the
    # namespace existed, and a consumer that never partitions on the namespace
    # sees exactly the curated rows it saw before.
    "DISCOVERY_NAMESPACE",
    "DiscoveredMetadata",
    "is_discovery_path",
    # Added 2026-08-20: device-scoped adoption. Additive in the same way --
    # `SpanPanelSnapshot.adopted_devices` defaults empty, so an adapter that
    # adopts nothing and a consumer that reads the field are both unaffected.
    "ADOPTION_IDENTITY_NODE",
    "ADOPTION_TOPOLOGY_NODE",
    "AdoptedDevice",
    "AdoptedProperty",
    "ExtensionProperty",
    "ExtensionSubject",
    # Snapshots
    "SpanBatterySnapshot",
    "SpanCircuitSnapshot",
    "SpanEvseSnapshot",
    "SpanMidSnapshot",
    "SpanPVSnapshot",
    "SpanPanelSnapshot",
    "SpanPcsSnapshot",
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
    "SpanPanelAdapterIncompatibleError",
    "SpanPanelAdapterMissingError",
    "SpanPanelAuthError",
    "SpanPanelSchemaVersionError",
    "SpanPanelConnectionError",
    "SpanPanelError",
    "SpanPanelServerError",
    "SpanPanelStaleDataError",
    "SpanPanelTimeoutError",
    "SpanPanelValidationError",
]
# fmt: on
