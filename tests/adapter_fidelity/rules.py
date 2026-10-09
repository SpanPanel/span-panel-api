"""What is right for the MAIN 32 cells, and the code that makes each entry so.

Every citation names a symbol in ``span_panel_api_schema_1`` (or in
``span_panel_api`` where it says so) rather than a line, so a citation outlives
the edit that moves the code.

The transform table covers every row of the library's own `_PROPERTY_FIELD_MAP`
and the three lugs tables, which `test_every_mapped_field_has_a_documented_transform`
holds it to: a field the library starts mapping cannot reach a snapshot without a
transform documented here. The derivations are only the ones these captures
need, which `test_every_derivation_is_needed` holds them to: a justification no
leaf needs is how a list of reasons turns into an allowlist.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Final

from span_panel_api.models import FEEDS_ROLES, PROTECTION_FUNCTIONS
from span_panel_api_schema_1.const import PANEL_POSITIONS_BY_MODEL

from .harness import ANY_LEAF, OUTSIDE_PANEL_ROLE, CellRules, Derivation, DropReason, RowKey, Transform, TransformKind

_IDENTITY: Final = TransformKind.IDENTITY
_NEGATE: Final = TransformKind.NEGATE
_INT: Final = TransformKind.INT
_LITERAL: Final = TransformKind.LITERAL
_BOOL: Final = TransformKind.BOOL
_DERIVED: Final = TransformKind.DERIVED

BESS_METER_DISCHARGE_POSITIVE_FROM_BUILD: Final = 202639
"""Mirrors `devices.BESS_METER_DISCHARGE_POSITIVE_FROM_BUILD`, restated so the rule is checked rather than borrowed."""


def _number(raw: str) -> float | None:
    try:
        return float(raw)
    except ValueError:
        return None


def _two_poles_or_more(raw: str, value: object) -> bool:
    poles = _number(raw)
    return value is (poles is not None and poles >= 2)


def _not_true(raw: str, value: object) -> bool:
    return value is (raw.strip().lower() != "true")


def _is_connected_cloud(raw: str, value: object) -> bool:
    return value is (raw == "CONNECTED")


def _is_ok_link(raw: str, value: object) -> bool:
    return value is (raw == "OK")


def _listed_spaces(raw: str, value: object) -> bool:
    """The integers in a comma-separated list, in order; a part that is not one is skipped."""
    spaces: list[int] = []
    for part in raw.split(","):
        try:
            spaces.append(int(part.strip()))
        except ValueError:
            continue
    return value == tuple(spaces)


def _model_or_none(raw: str, value: object) -> bool:
    """`UNKNOWN` (or nothing) names no model and reads as None; a named model reads as published."""
    return value == (None if raw.strip().upper() in ("", "UNKNOWN") else raw)


def _position(index: int) -> Callable[[str, object], bool]:
    """The model's first (0) or last (1) position from the table; for a model it lacks, the occupied range decides."""

    def holds(raw: str, value: object) -> bool:
        known = PANEL_POSITIONS_BY_MODEL.get(raw.strip().upper())
        if known is None:
            return value is None or isinstance(value, int)
        return value == known[index]

    return holds


def _feeds_role(raw: str, value: object) -> bool:
    return value == (raw if raw in FEEDS_ROLES else None)


def _protection_functions(raw: str, value: object) -> bool:
    listed = [part.strip() for part in raw.split(",")]
    return value == tuple(part for part in listed if part in PROTECTION_FUNCTIONS)


def _transforms() -> dict[RowKey, dict[str, Transform]]:
    table: dict[RowKey, dict[str, Transform]] = {}

    def add(
        role: str,
        node_property: str,
        family: str,
        kind: TransformKind,
        cite: str,
        holds: Callable[[str, object], bool] | None = None,
    ) -> None:
        table.setdefault(RowKey(role, node_property), {})[family] = Transform(kind, cite, holds=holds)

    # --- panel -------------------------------------------------------------
    panel_fields = "panel.PanelFields"
    add("panel", "info/firmware-version", "panel.firmware_version", _LITERAL, panel_fields)
    add(
        "panel",
        "info/firmware-version",
        "battery.power_w",
        _DERIVED,
        "devices.build_battery: the release build in the firmware version picks the BESS meter's frame",
    )
    add("panel", "info/vendor-name", "panel.vendor_name", _LITERAL, panel_fields)
    add(
        "panel",
        "info/model",
        "panel.model",
        _DERIVED,
        f"{panel_fields}: an UNKNOWN model names no model and reads as None",
        _model_or_none,
    )
    add("panel", "info/model", "panel.panel_size", _DERIVED, "panel.panel_size_from_model and const.PANEL_SIZE_BY_MODEL")
    for index, position in enumerate(("first_position", "last_position")):
        add(
            "panel",
            "info/model",
            f"panel.{position}",
            _DERIVED,
            "panel.panel_positions and const.PANEL_POSITIONS_BY_MODEL; the occupied spaces where the table lacks the model",
            _position(index),
        )
    add("panel", "info/hardware-version", "panel.hardware_version", _LITERAL, panel_fields)
    add("panel", "info/serial-number", "panel.serial_number", _LITERAL, panel_fields)
    add("panel", "status/relay", "panel.main_relay_state", _LITERAL, panel_fields)
    add("panel", "door/state", "panel.door_state", _LITERAL, panel_fields)
    add("panel", "status/ethernet", "panel.eth0_link", _BOOL, panel_fields)
    add("panel", "status/wifi", "panel.wlan_link", _BOOL, panel_fields)
    add("panel", "status/wifi-ssid", "panel.wifi_ssid", _LITERAL, panel_fields)
    add("panel", "status/cloud-connection", "panel.vendor_cloud", _LITERAL, panel_fields)
    add(
        "panel",
        "status/cloud-connection",
        "panel.wwan_link",
        _DERIVED,
        "panel.PanelFields: the cloud link is CONNECTED, as flat reported it",
        _is_connected_cloud,
    )
    add("panel", "meter/voltage-a", "panel.l1_voltage", _IDENTITY, panel_fields)
    add("panel", "meter/voltage-b", "panel.l2_voltage", _IDENTITY, panel_fields)
    add("panel", "breaker/rating", "panel.main_breaker_rating_a", _INT, panel_fields)
    add("panel", "meter/busbar-current", "panel.busbar_current_a", _IDENTITY, panel_fields)
    add("panel", "meter/frequency", "panel.frequency_hz", _IDENTITY, panel_fields)
    for flow in ("pv", "battery", "grid", "site"):
        add(
            "panel",
            f"power-flows/{flow}",
            f"panel.power_flow_{flow}",
            _IDENTITY,
            f"{panel_fields}: passed through untouched",
        )
    add(
        "panel",
        "power-flows/battery",
        "battery.power_w",
        _DERIVED,
        "devices.build_battery: compared in sign with the BESS meter where the firmware names no release build",
    )
    add(
        "panel",
        "power-flows/grid",
        ANY_LEAF,
        _DERIVED,
        "panel.resolve_islanding_state: grid power decides on-grid only where no MID is published",
    )
    add("panel", "shed/policy", "panel.shed_policy", _LITERAL, panel_fields)
    for policy_field in ("shed_policy_algorithm", "shed_soc_threshold_shed_percent", "shed_soc_threshold_release_percent"):
        add("panel", "shed/policy", f"panel.{policy_field}", _DERIVED, "panel._shed_policy parses the policy document")
    add(
        "panel",
        "shed/asserted-islanding-state",
        ANY_LEAF,
        _DERIVED,
        "panel.resolve_islanding_state: an assertion in force outranks the MID",
    )
    for forecast in (
        "time-to-priority-shed",
        "total-time-remaining",
        "full-charge-time-to-priority-shed",
        "full-charge-total-time-remaining",
    ):
        add("panel", f"shed-forecast/{forecast}", f"panel.shed_{forecast.replace('-', '_')}_min", _INT, panel_fields)
    add("panel", "shed-forecast/confidence", "panel.shed_forecast_confidence", _LITERAL, panel_fields)
    pcs = "panel.build_pcs"
    add("panel", "pcs/enabled", "pcs.enabled", _BOOL, pcs)
    add("panel", "pcs/active", "pcs.active", _BOOL, pcs)
    add("panel", "pcs/import-limit", "pcs.import_limit_a", _IDENTITY, pcs)
    add("panel", "pcs/binding-constraint", "pcs.binding_constraint", _LITERAL, pcs)
    for source in ("feed", "operator", "off-grid", "requested"):
        name = source.replace("-", "_")
        add(
            "panel", f"pcs/{source}-import-limit", f"pcs.{name}_import_limit_a", _IDENTITY, f"panel._limit_triplet via {pcs}"
        )
        add(
            "panel",
            f"pcs/{source}-import-limit-enablement",
            f"pcs.{name}_import_limit_enablement",
            _LITERAL,
            f"panel._limit_triplet via {pcs}",
        )
        add(
            "panel",
            f"pcs/{source}-import-limit-active",
            f"pcs.{name}_import_limit_active",
            _BOOL,
            f"panel._limit_triplet via {pcs}",
        )

    # --- circuit -----------------------------------------------------------
    circuit = "circuits.build_circuit"
    add(
        "circuit",
        "meter/active-power",
        "circuit.instant_power_w",
        _NEGATE,
        f"{circuit}: a load reads negative in the enclosure frame",
    )
    add("circuit", "meter/imported-energy", "circuit.produced_energy_wh", _IDENTITY, f"{circuit}: the accumulators swap")
    add("circuit", "meter/exported-energy", "circuit.consumed_energy_wh", _IDENTITY, f"{circuit}: the accumulators swap")
    add("circuit", "meter/current", "circuit.current_a", _IDENTITY, circuit)
    add("circuit", "breaker/rating", "circuit.breaker_rating_a", _IDENTITY, circuit)
    add("circuit", "breaker/poles", "circuit.is_240v", _DERIVED, f"{circuit}: two poles or more", _two_poles_or_more)
    add("circuit", "info/name", "circuit.name", _LITERAL, circuit)
    add("circuit", "info/spaces", "circuit.tabs", _DERIVED, "circuits._tabs parses the list of spaces", _listed_spaces)
    for position in ("first_position", "last_position"):
        add(
            "circuit",
            "info/spaces",
            f"panel.{position}",
            _DERIVED,
            "panel.panel_positions: the occupied spaces decide the range where the table lacks the model",
        )
    add("circuit", "info/nominal-voltage", "circuit.nominal_voltage_v", _IDENTITY, circuit)
    add(
        "circuit",
        "breaker/protection-functions",
        "circuit.protection_functions",
        _DERIVED,
        "circuits._protection_functions: the listed members of PROTECTION_FUNCTIONS, in published order",
        _protection_functions,
    )
    add(
        "circuit",
        "connection/feeds-role",
        "circuit.feeds_role",
        _DERIVED,
        "circuits._feeds_role: a member of FEEDS_ROLES as published, else None",
        _feeds_role,
    )
    for node, shared in (("meter", "circuit.meter_shared_with"), ("switch", "circuit.relay_shared_with")):
        add(
            "circuit",
            f"{node}/shared-with-device-ids",
            shared,
            _DERIVED,
            "circuits._shared_with: the listed ids naming another circuit of the snapshot, in device_id_order",
        )
    add("circuit", "switch/relay", "circuit.relay_state", _LITERAL, circuit)
    add("circuit", "switch/relay-requester", "circuit.relay_requester", _LITERAL, circuit)
    add("circuit", "switch/relay-controllable", "circuit.is_user_controllable", _BOOL, circuit)
    add(
        "circuit",
        "switch/relay-controllable",
        "circuit.always_on",
        _DERIVED,
        f"{circuit}: not relay-controllable",
        _not_true,
    )
    add(
        "circuit",
        "switch/relay-controllable",
        "circuit.is_sheddable",
        _DERIVED,
        f"{circuit}: a shed priority and a controllable relay",
    )
    add("circuit", "load-shed/priority", "circuit.priority", _LITERAL, circuit)
    add(
        "circuit",
        "load-shed/priority",
        "circuit.is_sheddable",
        _DERIVED,
        f"{circuit}: a shed priority and a controllable relay",
    )
    add("circuit", "pcs/managed", "circuit.pcs_managed", _BOOL, circuit)
    add("circuit", "pcs/priority", "circuit.pcs_priority", _INT, circuit)
    add(
        "circuit",
        "connection/feeds-device-id",
        ANY_LEAF,
        _DERIVED,
        "snapshot.build_snapshot and devices.feed_circuit_ids: the circuit's label, and the fed DER's feeding circuit, key and position",
    )
    for der in ("battery", "evse", "pv", "pv_inverter"):
        add(
            "circuit",
            "connection/feeds-device-status",
            f"{der}.connected",
            _DERIVED,
            "devices.feed_connection_statuses and devices._connected: OK is a working link "
            "(for a battery, where no device claims it through a fed-by record)",
            _is_ok_link,
        )

    # --- a circuit-typed device with no breaker space -----------------------
    outside = "circuits.build_circuit: a meter outside the panel reads import-positive, as published"
    add(OUTSIDE_PANEL_ROLE, "meter/active-power", "circuit.instant_power_w", _IDENTITY, outside)
    add(
        OUTSIDE_PANEL_ROLE,
        "meter/imported-energy",
        "circuit.consumed_energy_wh",
        _IDENTITY,
        f"{outside}: imported is consumed",
    )
    add(
        OUTSIDE_PANEL_ROLE,
        "meter/exported-energy",
        "circuit.produced_energy_wh",
        _IDENTITY,
        f"{outside}: exported is produced",
    )
    add(OUTSIDE_PANEL_ROLE, "meter/current", "circuit.current_a", _IDENTITY, outside)
    add(
        OUTSIDE_PANEL_ROLE,
        "meter/shared-with-device-ids",
        "circuit.meter_shared_with",
        _DERIVED,
        "circuits._shared_with: the listed ids naming another circuit of the snapshot, in device_id_order",
    )

    # --- lugs --------------------------------------------------------------
    lugs_meter = "panel.PanelFields: the lugs read import-positive and pass through"
    for role, power, consumed, produced, current_a, current_b in (
        (
            "lugs-upstream",
            "panel.instant_grid_power_w",
            "panel.main_meter_energy_consumed_wh",
            "panel.main_meter_energy_produced_wh",
            "panel.upstream_l1_current_a",
            "panel.upstream_l2_current_a",
        ),
        (
            "lugs-downstream",
            "panel.feedthrough_power_w",
            "panel.feedthrough_energy_consumed_wh",
            "panel.feedthrough_energy_produced_wh",
            "panel.downstream_l1_current_a",
            "panel.downstream_l2_current_a",
        ),
    ):
        add(role, "meter/active-power", power, _IDENTITY, lugs_meter)
        add(role, "meter/imported-energy", consumed, _IDENTITY, lugs_meter)
        add(role, "meter/exported-energy", produced, _IDENTITY, lugs_meter)
        add(role, "meter/current-a", current_a, _IDENTITY, lugs_meter)
        add(role, "meter/current-b", current_b, _IDENTITY, lugs_meter)
        add(role, "info/direction", ANY_LEAF, _DERIVED, "panel.find_lugs: the direction decides which lugs are which")
        add(
            role,
            "connection/fed-by-device-id",
            ANY_LEAF,
            _DERIVED,
            "panel.PanelFields (lugs_at_service_entrance), devices.connection_status_for and devices.resolve_relative_position",
        )
        add(
            role,
            "connection/fed-by-device-status",
            "battery.connected",
            _DERIVED,
            "devices.connection_status_for and devices._connected: OK is a working link",
            _is_ok_link,
        )
        add(
            role,
            "connection/feeds-device-id",
            ANY_LEAF,
            _DERIVED,
            "devices.resolve_relative_position: a DER fed through the feedthrough is in the panel",
        )
    add(
        "lugs-upstream",
        "connection/overcurrent-protection",
        "panel.upstream_protection_rating_a",
        _INT,
        "panel.PanelFields: the upstream lugs' protection rating",
    )

    # --- BESS --------------------------------------------------------------
    battery = "devices.build_battery"
    add("bess", "soc/soc", "battery.soe_percentage", _IDENTITY, battery)
    add("bess", "soc/soe", "battery.soe_kwh", _IDENTITY, battery)
    add("bess", "info/vendor-name", "battery.vendor_name", _LITERAL, battery)
    add("bess", "info/model", "battery.model", _LITERAL, battery)
    add("bess", "info/part-number", "battery.part_number", _LITERAL, battery)
    add("bess", "info/serial-number", "battery.serial_number", _LITERAL, battery)
    add("bess", "info/firmware-version", "battery.software_version", _LITERAL, battery)
    add("bess", "info/nameplate-capacity", "battery.nameplate_capacity_kwh", _IDENTITY, battery)
    add("bess", "status/communication-state", "battery.communication_state", _LITERAL, battery)
    table.setdefault(RowKey("bess", "meter/active-power"), {})["battery.power_w"] = Transform(
        _IDENTITY,
        f"{battery}: discharge-positive, and the wire is too from release build "
        f"{BESS_METER_DISCHARGE_POSITIVE_FROM_BUILD}; charge-positive before it, so negated",
        earlier=(BESS_METER_DISCHARGE_POSITIVE_FROM_BUILD, _NEGATE),
    )

    # --- MID ---------------------------------------------------------------
    mid = "devices.build_mid"
    add("mid", "info/vendor-name", "mid.vendor_name", _LITERAL, mid)
    add("mid", "info/model", "mid.model", _LITERAL, mid)
    add("mid", "info/serial-number", "mid.serial_number", _LITERAL, mid)
    add("mid", "info/serial-number", "mid.node_id", _LITERAL, f"{mid}: the serial where one is published")
    add("mid", "info/firmware-version", "mid.software_version", _LITERAL, mid)
    add("mid", "info/hardware-version", "mid.hardware_version", _LITERAL, mid)
    add("mid", "grid/islanding-state", "mid.islanding_state", _LITERAL, mid)
    add("mid", "grid/islanding-state", "panel.grid_state", _LITERAL, "panel.PanelFields: islanding-state, not grid-state")
    add(
        "mid",
        "grid/islanding-state",
        ANY_LEAF,
        _DERIVED,
        "panel.resolve_islanding_state, panel.resolve_dsm_state and panel.resolve_run_config",
    )
    add("mid", "grid/grid-state", "mid.grid_state", _LITERAL, mid)
    add("mid", "grid/grid-forming-entity", "mid.grid_forming_entity", _LITERAL, mid)
    add(
        "mid",
        "grid/grid-forming-entity",
        ANY_LEAF,
        _DERIVED,
        "panel.resolve_run_config, panel.resolve_dominant_power_source and panel.resolve_grid_forming_device_name",
    )

    # --- PV ----------------------------------------------------------------
    for subject in ("pv", "pv_inverter"):
        pv = f"devices.build_pv ({'the lone inverter' if subject == 'pv' else 'each inverter'})"
        add("pv", "info/vendor-name", f"{subject}.vendor_name", _LITERAL, pv)
        add("pv", "info/model", f"{subject}.model", _LITERAL, pv)
        add("pv", "info/serial-number", f"{subject}.serial_number", _LITERAL, pv)
        add("pv", "info/firmware-version", f"{subject}.software_version", _LITERAL, pv)
        add("pv", "info/nominal-power", f"{subject}.nameplate_capacity_w", _IDENTITY, pv)

    # --- EVSE --------------------------------------------------------------
    evse = "devices.build_evse"
    add("evse", "status/status", "evse.status", _LITERAL, evse)
    add("evse", "switch/lock-state", "evse.lock_state", _LITERAL, evse)
    add("evse", "meter/advertised-current", "evse.advertised_current_a", _IDENTITY, evse)
    add("evse", "info/vendor-name", "evse.vendor_name", _LITERAL, evse)
    add("evse", "info/model", "evse.model", _LITERAL, evse)
    add("evse", "info/part-number", "evse.part_number", _LITERAL, evse)
    add("evse", "info/serial-number", "evse.serial_number", _LITERAL, evse)
    add(
        "evse", "info/serial-number", ANY_LEAF, _DERIVED, "snapshot.harmonised_evse_keys: the charger is keyed by its serial"
    )
    add("evse", "info/firmware-version", "evse.software_version", _LITERAL, evse)
    # The charge-current pair, in either spelling the charger's description declares.
    charge_limit = f"{evse} through charge_limit.resolve_charge_limit, read as an integer"
    for node, ceiling, limit in (
        ("config", "max-charge-current", "user-max-charge-current"),
        ("charge-limit", "installer-max", "owner-limit"),
    ):
        add("evse", f"{node}/{ceiling}", "evse.charge_current_ceiling_a", _INT, charge_limit)
        add("evse", f"{node}/{limit}", "evse.charge_current_limit_a", _INT, charge_limit)
    return table


_DERIVATIONS: Final = (
    Derivation("circuit.circuit_id", "circuits.build_circuit: the circuit's device id"),
    Derivation(
        "circuit.device_type",
        "snapshot.build_snapshot labels a circuit by the PV or EVSE its connection/feeds-device-id names, else 'circuit'",
        values=("circuit",),
    ),
    Derivation(
        "circuit.is_never_backup",
        "circuits.priority_is_settable: `$settable` on the load-shed/priority declaration, not a value",
    ),
    Derivation(
        "circuit.is_sheddable",
        "circuits.build_circuit: not sheddable when the published priority and relay-controllable rule it out together",
        values=(False,),
        inputs=("load-shed/priority", "switch/relay-controllable"),
    ),
    Derivation(
        "ctl.priority_target",
        "adapter.SchemaOneAdapter.set_circuit_priority_target: a topic, offered where the declaration is `$settable`",
    ),
    Derivation(
        "ctl.dps_target",
        "adapter.SchemaOneAdapter.set_dominant_power_source_target: a topic, offered where the declaration is `$settable`",
    ),
    Derivation("battery.present", "devices.build_battery: the tree declares a battery, before any of its values"),
    Derivation(
        "circuit.measures_outside_panel",
        "circuits.declares_spaces: whether the description declares info/spaces, not a value",
    ),
    Derivation(
        "panel.publishes_solar_roles",
        "snapshot.build_snapshot: whether any circuit's description declares connection/feeds-role, not a value",
    ),
    Derivation("pv.device_id", "devices.build_pv: the inverter's device id"),
    Derivation("pv.node_id", "devices.pv_inverter_key: the feeding circuit's id, else the inverter's device id"),
    Derivation("pv_inverter.device_id", "devices.build_pv: the inverter's device id"),
    Derivation("pv_inverter.node_id", "devices.pv_inverter_key: the feeding circuit's id, else the inverter's device id"),
)

_DROPS: Final = {
    DropReason.OFF_SNAPSHOT: (
        "field_metadata._CONSUMED_OFF_SNAPSHOT names the code that reads each of these by a route no snapshot field shows"
    ),
    DropReason.IDENTITY_OR_TOPOLOGY: (
        "extension.build_extension_properties skips the info and connection nodes "
        "(ADOPTION_IDENTITY_NODE and ADOPTION_TOPOLOGY_NODE in span_panel_api.models): identity and topology are never readings"
    ),
    DropReason.EXTRA_INSTANCE: "snapshot.TreeRoles keeps the first BESS and the first MID; the snapshot holds one of each",
}

_DEFERRED: Final = {
    RowKey("lugs-upstream", "connection/fed-by-device-status"): (
        "devices.connection_status_for reads it only for the battery the upstream lugs name as their source; "
        "the status of a link to anything else, such as a panel feeding this one, reaches no snapshot field yet"
    ),
}

TRANSFORMS: Final = _transforms()
"""Every documented transform, shared by every cell: a transform states what the library does, whichever capture shows it."""

DROPS: Final = _DROPS

MAIN32_RULES: Final = CellRules(transforms=TRANSFORMS, derivations=_DERIVATIONS, drops=_DROPS, deferred=_DEFERRED)
