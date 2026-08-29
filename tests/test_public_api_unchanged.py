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

import inspect

import span_panel_api
from span_panel_api import exceptions

# Source of truth: src/span_panel_api/__init__.py __all__ (transcribed in full,
# not trimmed, per Phase 0 Task 7's instruction to reconcile against the real file
# rather than an earlier hand-transcribed listing).
EXPECTED_PUBLIC_API = {
    # Protocols
    "CircuitControlProtocol",
    # Added 2026-08-19: EVSE charge-current control. Purely additive -- the only
    # settable property the v1.0 catch-up surfaces, and one no flat panel
    # publishes, so nothing existing changes.
    "EvseControlProtocol",
    "PanelCapability",
    "PanelControlProtocol",
    "SpanPanelClientProtocol",
    "StreamingCapableProtocol",
    # Metadata
    "FieldMetadata",
    "HomieSchemaTypes",
    # Added 2026-08-20: runtime discovery -- the namespace an adapter puts
    # declared-but-unaddressed properties under, the row type it puts there, and
    # the predicate a consumer partitions with. Purely additive: an adapter that
    # emits none of these rows is indistinguishable from one built before the
    # namespace existed, and a consumer that never partitions sees exactly the
    # curated rows it saw before.
    "DISCOVERY_NAMESPACE",
    "DiscoveredMetadata",
    # Added 2026-08-20: device-scoped adoption -- the two nodes whose properties
    # resolve to a device card and a device link rather than to entities, and the
    # pair of records an adapter reports an unmodelled device with. Additive for
    # the same reason: `SpanPanelSnapshot.adopted_devices` defaults empty, so an
    # adapter that adopts nothing and a consumer that never reads the field are
    # both unaffected. Deliberately not `SchemaAdapter` members -- the protocol
    # derives its required set from itself, so a member there would be required
    # of every adapter package and would invalidate built wheels.
    "ADOPTION_IDENTITY_NODE",
    "ADOPTION_TOPOLOGY_NODE",
    "AdoptedDevice",
    "AdoptedProperty",
    # Added 2026-08-25 (3.1.0): the adapter answers a control request with the
    # topic *and* the property that reports it, in one value.
    "ControlTarget",
    "ExtensionProperty",
    "ExtensionSubject",
    "AdoptedControlProtocol",
    # Added 2026-08-25 (3.1.0): one veto/observe point for every control
    # command, the consumer-side half of its authorisation gate. A protocol of
    # its own so the four control protocols are not broken twice in one release.
    "ControlInterceptionProtocol",
    "is_discovery_path",
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
    # Added 2026-08-25 with CA pinning (3.1.0). Deliberate additions: the
    # integration builds the same SSL context for its own HTTPS calls and stores
    # and compares the same fingerprint string, so both live here rather than
    # being reimplemented on the far side of the pin where they could drift.
    "build_panel_ssl_context",
    "ca_fingerprint",
    # Added 2026-08-28: the hostname half of verification, needed by a caller
    # that built a relaxed context to decide *which* host to talk to and must
    # still establish the name binding. Here for the same reason as the two
    # above -- a hand-written SAN matcher reimplemented on the far side of the
    # pin is the drift this module exists to prevent.
    "leaf_names_host",
    # Added 2026-08-28 (3.3.0): the transport's report that the pinned CA
    # validates the broker's certificate and that certificate names somewhere
    # other than the address configured. Here rather than in the transport
    # section because it is produced by the same module as the two above and
    # carries the names that module read. Additive -- a consumer that registers
    # no callback never receives one.
    "LeafNameMismatch",
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
    # Added 2026-08-25 (3.1.0): the control-outcome vocabulary. Additive for
    # callers -- a call site that ignores the return value is unaffected -- and
    # breaking for anything type-checked against the control protocols with
    # `-> None`, which the release notes name.
    "ControlCommand",
    "ControlDeadlines",
    "ControlInterceptor",
    "PublishOutcome",
    "PublishState",
    # Phase validation
    "PhaseDistribution",
    "are_tabs_opposite_phase",
    "get_phase_distribution",
    "get_tab_phase",
    "suggest_balanced_pairing",
    "validate_solar_tabs",
    # Exceptions
    "SpanPanelAPIError",
    # Added 3.0.1: omitted from `__all__` in 3.0.0 while the README and the
    # changelog both documented it as a top-level export, and while
    # `SpanMqttClient.connect` names it in its own docstring as something a
    # caller receives. Purely additive -- the class and its raise sites are
    # unchanged, only the path a consumer imports it by.
    "SpanPanelAdapterIncompatibleError",
    "SpanPanelAdapterMissingError",
    "SpanPanelAuthError",
    # Added 2026-08-25 (3.1.0): the one connection failure this library will not
    # retry, because retrying it means waiting to succeed against whatever is
    # answering. Additive -- nothing raised it before, so no caller's except
    # clause changes meaning.
    "SpanPanelCAChangedError",
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


def test_every_public_exception_is_exported() -> None:
    """Every exception this package defines is reachable from the top level.

    The pin above compares `__all__` against a hand-transcribed set, so it only
    catches the two drifting apart. It cannot catch a name omitted from *both* --
    which is exactly how `SpanPanelAdapterIncompatibleError` shipped in 3.0.0
    documented as a top-level export but importable only from
    `span_panel_api.exceptions`.

    This set is derived from the module instead of transcribed, so a new
    exception class is exported or this fails. That matters because a consumer
    cannot catch what it cannot import, and `resolve_adapter` raises these into
    caller hands rather than logging them.
    """
    defined = {
        name
        for name, obj in vars(exceptions).items()
        if not name.startswith("_")
        and inspect.isclass(obj)
        and issubclass(obj, Exception)
        and obj.__module__ == exceptions.__name__
    }
    assert defined, "no exception classes found; this guard would pass vacuously"

    unexported = defined - set(span_panel_api.__all__)
    assert not unexported, (
        f"defined in span_panel_api.exceptions but not exported from the package: {sorted(unexported)}. "
        "A consumer cannot catch what it cannot import."
    )
