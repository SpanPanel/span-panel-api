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
    TYPE_BESS,
    TYPE_CIRCUIT,
    TYPE_EVSE,
    TYPE_LUGS,
    TYPE_PANEL,
    TYPE_PV,
)

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
    # One row per property, not per direction: both lugs devices declare the
    # same type, and which is which comes from `info/direction` at read time.
    (TYPE_LUGS, NODE_METER, "active-power", "panel.instant_grid_power_w"),
    (TYPE_LUGS, NODE_METER, "imported-energy", "panel.main_meter_energy_consumed_wh"),
    (TYPE_LUGS, NODE_METER, "exported-energy", "panel.main_meter_energy_produced_wh"),
    (TYPE_LUGS, NODE_METER, "current-a", "panel.upstream_l1_current_a"),
    (TYPE_LUGS, NODE_METER, "current-b", "panel.upstream_l2_current_a"),
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
    (TYPE_BESS, NODE_INFO, "model", "battery.product_name"),
    (TYPE_BESS, NODE_INFO, "part-number", "battery.model"),
    (TYPE_BESS, NODE_INFO, "nameplate-capacity", "battery.nameplate_capacity_kwh"),
    # --- PV ------------------------------------------------------------------
    (TYPE_PV, NODE_INFO, "vendor-name", "pv.vendor_name"),
    (TYPE_PV, NODE_INFO, "model", "pv.product_name"),
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
    for device in devices:
        description: dict[str, object] = device.description or {}
        device_type = str(description.get("type") or "")
        if not device_type:
            continue
        for node_id, node in _nodes(description).items():
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
    return metadata


def _lookup(
    declared: dict[str, tuple[str | None, str]], device_type: str, node_id: str, property_id: str
) -> tuple[str | None, str] | None:
    """Find a declaration, allowing a device type to be a subtype of the mapped one.

    Lugs are the reason: firmware may declare `…device.lugs` or a subtyped
    `…device.lugs.upstream`, and both carry the same properties.
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
