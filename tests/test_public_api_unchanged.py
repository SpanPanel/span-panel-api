"""Guard: Phase 0 is a restructure, so the public surface must not move.

The HA integration pins span-panel-api and imports these names directly. If this
test fails, the change is no longer Phase 0 — it is a breaking release.
"""

from __future__ import annotations

import span_panel_api

# Source of truth: src/span_panel_api/__init__.py __all__ (transcribed in full,
# not trimmed, per Phase 0 Task 7's instruction to reconcile against the real file
# rather than an earlier hand-transcribed listing).
EXPECTED_PUBLIC_API = {
    # Protocols
    "CircuitControlProtocol",
    "PanelCapability",
    "PanelControlProtocol",
    "SpanPanelClientProtocol",
    "StreamingCapableProtocol",
    # Metadata
    "FieldMetadata",
    "HomieSchemaTypes",
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
    "HomieLifecycle",
    "HomiePropertyAccumulator",
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
    "SpanPanelStaleDataError",
    "SpanPanelTimeoutError",
    "SpanPanelValidationError",
}


def test_all_is_unchanged() -> None:
    missing = EXPECTED_PUBLIC_API - set(span_panel_api.__all__)
    assert not missing, f"Phase 0 removed public names: {sorted(missing)}"

    extra = set(span_panel_api.__all__) - EXPECTED_PUBLIC_API
    assert not extra, f"Phase 0 added undocumented public names: {sorted(extra)}"


def test_every_exported_name_is_importable() -> None:
    for name in span_panel_api.__all__:
        assert hasattr(span_panel_api, name), f"{name} is in __all__ but not importable"
