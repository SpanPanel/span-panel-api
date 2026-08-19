"""Build transport-agnostic field metadata from the v1.0 device tree.

Maps every property the snapshot mapper reads to a snapshot field path, then
takes the declared unit and datatype for each from the tree itself. The result
is a dict the integration consumes without any Homie knowledge, keyed
``{snapshot_type}.{field_name}``.

**Read from each device's ``$description``, not from the REST schema.** The
migration guide is explicit that "the authoritative property set for any
capability node is always declared in that device's ``$description``", because
the same capability type exposes different properties on different device
classes — ``meter`` on the panel is voltage, on a circuit is power and energy,
on a lugs device is both currents. The REST ``deviceClasses`` document is the
superset across all hardware; the description is what *this* panel actually has.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from span_panel_api.models import FieldMetadata
from span_panel_api_schema_1.const import (
    NODE_BREAKER,
    NODE_DOOR,
    NODE_INFO,
    NODE_LOAD_SHED,
    NODE_METER,
    NODE_POWER_FLOWS,
    NODE_SOC,
    NODE_STATUS,
    NODE_SWITCH,
    PROP_ACTIVE_POWER,
    PROP_EXPORTED_ENERGY,
    PROP_IMPORTED_ENERGY,
    TYPE_BESS,
    TYPE_CIRCUIT,
    TYPE_EVSE,
    TYPE_LUGS,
    TYPE_PANEL,
    TYPE_PV,
)
from span_panel_api_schema_1.panel import PROP_CURRENT_A, PROP_CURRENT_B, find_lugs
from span_panel_api_schema_1.snapshot import device_type as declared_type

if TYPE_CHECKING:
    from ebus_sdk.homie import DiscoveredDevice

# (device type, node, property) → snapshot field path.
#
# Encodes how the mapper reads the tree, so it has to move with it. Where the
# mapper deliberately declines a value — dsm_state, current_run_config,
# dominant_power_source, grid_islandable, relative_position — there is no row,
# because metadata for a field nothing populates would advertise a unit for a
# reading that never arrives.
_PROPERTY_FIELD_MAP: tuple[tuple[str, str, str, str], ...] = (
    # --- Panel ---------------------------------------------------------------
    (TYPE_PANEL, NODE_INFO, "firmware-version", "panel.firmware_version"),
    (TYPE_PANEL, NODE_DOOR, "state", "panel.door_state"),
    (TYPE_PANEL, NODE_STATUS, "relay", "panel.main_relay_state"),
    (TYPE_PANEL, NODE_STATUS, "ethernet", "panel.eth0_link"),
    (TYPE_PANEL, NODE_STATUS, "wifi", "panel.wlan_link"),
    (TYPE_PANEL, NODE_STATUS, "cloud-connection", "panel.vendor_cloud"),
    (TYPE_PANEL, NODE_METER, "voltage-a", "panel.l1_voltage"),
    (TYPE_PANEL, NODE_METER, "voltage-b", "panel.l2_voltage"),
    (TYPE_PANEL, NODE_BREAKER, "rating", "panel.main_breaker_rating_a"),
    (TYPE_PANEL, NODE_POWER_FLOWS, "pv", "panel.power_flow_pv"),
    (TYPE_PANEL, NODE_POWER_FLOWS, "battery", "panel.power_flow_battery"),
    (TYPE_PANEL, NODE_POWER_FLOWS, "grid", "panel.power_flow_grid"),
    (TYPE_PANEL, NODE_POWER_FLOWS, "site", "panel.power_flow_site"),
    # --- Lugs → panel.* ------------------------------------------------------
    # Deliberately absent. Which device a lugs property belongs to comes from
    # `info/direction` at read time, and a table keyed on (type, node, property)
    # cannot express that — see `_DOWNSTREAM_LUGS_FIELDS` and `_lugs_metadata`.
    # --- Circuit -------------------------------------------------------------
    (TYPE_CIRCUIT, NODE_INFO, "name", "circuit.name"),
    (TYPE_CIRCUIT, NODE_INFO, "spaces", "circuit.tabs"),
    (TYPE_CIRCUIT, NODE_SWITCH, "relay", "circuit.relay_state"),
    (TYPE_CIRCUIT, NODE_SWITCH, "relay-requester", "circuit.relay_requester"),
    (TYPE_CIRCUIT, NODE_SWITCH, "relay-controllable", "circuit.is_user_controllable"),
    (TYPE_CIRCUIT, NODE_LOAD_SHED, "priority", "circuit.priority"),
    (TYPE_CIRCUIT, NODE_METER, "active-power", "circuit.instant_power_w"),
    (TYPE_CIRCUIT, NODE_METER, "current", "circuit.current_a"),
    (TYPE_CIRCUIT, NODE_METER, "imported-energy", "circuit.produced_energy_wh"),
    (TYPE_CIRCUIT, NODE_METER, "exported-energy", "circuit.consumed_energy_wh"),
    (TYPE_CIRCUIT, NODE_BREAKER, "rating", "circuit.breaker_rating_a"),
    (TYPE_CIRCUIT, NODE_BREAKER, "poles", "circuit.is_240v"),
    # --- BESS ----------------------------------------------------------------
    (TYPE_BESS, NODE_SOC, "soc", "battery.soe_percentage"),
    (TYPE_BESS, NODE_SOC, "soe", "battery.soe_kwh"),
    (TYPE_BESS, NODE_INFO, "vendor-name", "battery.vendor_name"),
    (TYPE_BESS, NODE_INFO, "model", "battery.model"),
    (TYPE_BESS, NODE_INFO, "part-number", "battery.part_number"),
    (TYPE_BESS, NODE_INFO, "serial-number", "battery.serial_number"),
    (TYPE_BESS, NODE_INFO, "firmware-version", "battery.software_version"),
    (TYPE_BESS, NODE_INFO, "nameplate-capacity", "battery.nameplate_capacity_kwh"),
    # --- PV ------------------------------------------------------------------
    (TYPE_PV, NODE_INFO, "vendor-name", "pv.vendor_name"),
    (TYPE_PV, NODE_INFO, "model", "pv.model"),
    (TYPE_PV, NODE_INFO, "nominal-power", "pv.nameplate_capacity_w"),
    # --- EVSE ----------------------------------------------------------------
    (TYPE_EVSE, NODE_STATUS, "status", "evse.status"),
    (TYPE_EVSE, NODE_SWITCH, "lock-state", "evse.lock_state"),
    (TYPE_EVSE, NODE_METER, "advertised-current", "evse.advertised_current_a"),
)


def build_field_metadata(devices: list[DiscoveredDevice]) -> dict[str, FieldMetadata]:
    """Collect metadata for every mapped field the tree actually declares.

    A field with no declaring device is omitted rather than defaulted: the
    integration compares these against its own sensor definitions, so an
    invented unit would validate a reading the panel never sends.
    """
    declared: dict[str, tuple[str | None, str]] = {}
    # Presence is a (device type, node) question, not a device question. The
    # power-flows rows are (TYPE_PANEL, NODE_POWER_FLOWS, ...) and the panel
    # device is always present, so a device-level test would mark every
    # panel.power_flow_* path unresolved on a panel that simply has no
    # power-flows node.
    #
    # Collected from the node structure rather than from `declared`, because a
    # node that declares no properties at all is exactly the degradation this
    # is here to catch, and it contributes no `declared` keys to read back.
    present_type_nodes: set[tuple[str, str]] = set()
    for device in devices:
        description: dict[str, object] = device.description or {}
        device_type = str(description.get("type") or "")
        if not device_type:
            continue
        for node_id, node in _nodes(description).items():
            present_type_nodes.add((device_type, node_id))
            for property_id, definition in _properties(node).items():
                declared[f"{device_type}|{node_id}|{property_id}"] = (
                    _optional_str(definition.get("unit")),
                    str(definition.get("datatype") or "string"),
                )

    metadata: dict[str, FieldMetadata] = {}
    for device_type, node_id, property_id, field_path in _PROPERTY_FIELD_MAP:
        found = _lookup(declared, device_type, node_id, property_id)
        if found is not None:
            unit, datatype = found
            metadata[field_path] = FieldMetadata(unit=unit, datatype=datatype)
        elif _node_declared(present_type_nodes, device_type, node_id):
            # The node is here and does not declare the property: a real gap,
            # distinct from the hardware simply not being installed.
            metadata[field_path] = FieldMetadata(unit=None, datatype="unknown", resolved=False)
    metadata.update(_lugs_metadata(devices, upstream=True, fields=_UPSTREAM_LUGS_FIELDS))
    metadata.update(_lugs_metadata(devices, upstream=False, fields=_DOWNSTREAM_LUGS_FIELDS))
    return metadata


def _node_declared(present_type_nodes: set[tuple[str, str]], device_type: str, node_id: str) -> bool:
    """Whether any present device of this type declares this node.

    Mirrors `_lookup`'s subtype rule, and has to: presence and lookup must
    agree about which devices answer for a row, or a subtyped device that
    dropped a property would resolve through one and misclassify through the
    other. Both are now exercised on non-lugs types, lugs having moved to a
    direction-resolved lookup of their own.
    """
    return any(
        node == node_id and (declared_device_type == device_type or declared_device_type.startswith(f"{device_type}."))
        for declared_device_type, node in present_type_nodes
    )


# The ten fields `_PROPERTY_FIELD_MAP` cannot address, and why it cannot.
#
# The table is keyed `(device type, node, property)`, and the two lugs devices
# match on all three — same `energy.ebus.device.lugs`, same `meter` node, same
# property names — differing only in the `info/direction` value they publish. A
# table keyed that way cannot hold two different answers, so it cannot describe
# these ten fields at all: it can only describe *a* lugs device and label the
# result with one direction's field paths.
#
# Doing that was wrong in both directions at once. Whichever device `_lookup`
# reached first answered for the `upstream_*` paths, so a property the upstream
# device had dropped came back `resolved=True`, with a real unit, on the strength
# of the downstream device declaring it — and with no upstream device present at
# all, the downstream one described the whole main meter as working hardware that
# was not installed. Both are the false `resolved=True` this metadata exists to
# make impossible, and the silent kind: the integration validates against a unit
# for a reading that never arrives, and nothing anywhere reports a fault.
#
# The snapshot mapper never had the problem, because it resolves the pair by
# direction and reads each (`panel.py`, `PanelFields.__init__`). Resolving the
# metadata the same way is what keeps the two from disagreeing about which device
# is which — the property a field's unit describes is now the same property whose
# value fills it.
_UPSTREAM_LUGS_FIELDS: tuple[tuple[str, str], ...] = (
    (PROP_ACTIVE_POWER, "panel.instant_grid_power_w"),
    (PROP_IMPORTED_ENERGY, "panel.main_meter_energy_consumed_wh"),
    (PROP_EXPORTED_ENERGY, "panel.main_meter_energy_produced_wh"),
    (PROP_CURRENT_A, "panel.upstream_l1_current_a"),
    (PROP_CURRENT_B, "panel.upstream_l2_current_a"),
)

_DOWNSTREAM_LUGS_FIELDS: tuple[tuple[str, str], ...] = (
    (PROP_ACTIVE_POWER, "panel.feedthrough_power_w"),
    (PROP_IMPORTED_ENERGY, "panel.feedthrough_energy_consumed_wh"),
    (PROP_EXPORTED_ENERGY, "panel.feedthrough_energy_produced_wh"),
    (PROP_CURRENT_A, "panel.downstream_l1_current_a"),
    (PROP_CURRENT_B, "panel.downstream_l2_current_a"),
)


def _lugs_metadata(
    devices: list[DiscoveredDevice], *, upstream: bool, fields: tuple[tuple[str, str], ...]
) -> dict[str, FieldMetadata]:
    """Metadata for one lugs device, resolved by direction rather than by type.

    Uses the same `find_lugs` the snapshot mapper uses, so the metadata and the
    value can never disagree about which device is which.

    Carries the same three-way contract as the table-driven loop, on the same
    (device, node) granularity: no lugs device in this direction, or no `meter`
    node on it, means no entry, while a `meter` node that omits a property is a
    declared gap. Both directions run through here so the two halves of
    `panel.*` cannot drift into answering to different rules.

    A lugs device that publishes no `info/direction` is invisible to `find_lugs`
    and so yields no entry, which is deliberate: the mapper reads its values
    through the same call, so nothing would populate those fields either.
    """
    lugs = find_lugs([d for d in devices if declared_type(d).startswith(TYPE_LUGS)], upstream=upstream)
    if lugs is None:
        return {}

    meter = _nodes(lugs.description or {}).get(NODE_METER)
    if meter is None:
        return {}

    declared = _properties(meter)
    found: dict[str, FieldMetadata] = {}
    for property_id, field_path in fields:
        definition = declared.get(property_id)
        if definition is None:
            found[field_path] = FieldMetadata(unit=None, datatype="unknown", resolved=False)
            continue
        found[field_path] = FieldMetadata(
            unit=_optional_str(definition.get("unit")),
            datatype=str(definition.get("datatype") or "string"),
        )
    return found


def _lookup(
    declared: dict[str, tuple[str | None, str]], device_type: str, node_id: str, property_id: str
) -> tuple[str | None, str] | None:
    """Find a declaration, allowing a device type to be a subtype of the mapped one.

    eBus device types are hierarchical and a subtype carries its parent's
    properties, so a device typed `X.Y` satisfies a row written for `X`.

    Lugs were the observed instance — `…device.lugs` versus a subtyped
    `…device.lugs.upstream` — and they no longer come through here, because
    which lugs device a property belongs to is a direction question the table
    cannot ask. The rule is kept for every other mapped type rather than
    retired with its first user: the same subtyping applies to all of them, and
    `_LUGS_FALLBACK` in the flat adapter is evidence SPAN does ship it.
    """
    exact = declared.get(f"{device_type}|{node_id}|{property_id}")
    if exact is not None:
        return exact
    suffix = f"|{node_id}|{property_id}"
    for key, value in declared.items():
        if key.endswith(suffix) and key[: -len(suffix)].startswith(device_type):
            return value
    return None


def _nodes(description: dict[str, object]) -> dict[str, dict[str, object]]:
    nodes = description.get("nodes")
    if not isinstance(nodes, dict):
        return {}
    return {str(k): v for k, v in nodes.items() if isinstance(v, dict)}


def _properties(node: dict[str, object]) -> dict[str, dict[str, object]]:
    properties = node.get("properties")
    if not isinstance(properties, dict):
        return {}
    return {str(k): v for k, v in properties.items() if isinstance(v, dict)}


def _optional_str(value: object) -> str | None:
    if value is None:
        return None
    text = str(value)
    return text or None
