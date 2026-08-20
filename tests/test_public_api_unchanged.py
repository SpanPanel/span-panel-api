"""Guard: the public surface only moves on purpose.

The HA integration pins span-panel-api and imports these names directly, so a
failure here means either an accidental break or a deliberate one whose record
belongs in the same commit. The set below is a two-way pin — it fails on both
removals and additions — and editing it is how a break gets acknowledged.

Phase 0 held it fixed. Phase 1 deliberately breaks it (3.0): the three
flat-schema names below were removed, because the bootstrap can no longer import
a parsing implementation to re-export.
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
    # Added 2026-08-10: v1.0 surfaces the islanding authority as its own device.
    # Purely additive -- no flat panel publishes a MID, so nothing existing changes.
    "SpanMidSnapshot",
    "SpanPVSnapshot",
    "SpanPanelSnapshot",
    # Added 2026-08-19: the enclosure's Power Control System (UL 3141 import
    # limiting). Purely additive for the same reason as the MID -- no flat panel
    # publishes `energy.ebus.capability.pcs`, so `SpanPanelSnapshot.pcs` is
    # `None` on every existing consumer's data and nothing that reads the
    # snapshot today changes.
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
    "SpanPanelAdapterMissingError",
    "SpanPanelAuthError",
    "SpanPanelSchemaVersionError",
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
