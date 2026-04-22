"""Build transport-agnostic field metadata from the Homie schema.

Maps every Homie property that ``HomieDeviceConsumer._build_snapshot()``
reads to a snapshot field path, then looks up the schema-declared unit
and datatype for each. The result is a dict the integration can consume
without any Homie knowledge.

Field path convention: ``{snapshot_type}.{field_name}``
  - ``panel`` — SpanPanelSnapshot
  - ``circuit`` — SpanCircuitSnapshot
  - ``battery`` — SpanBatterySnapshot
  - ``pv`` — SpanPVSnapshot
  - ``evse`` — SpanEvseSnapshot
"""

from __future__ import annotations

import logging

from ..models import FieldMetadata, HomieSchemaTypes
from .const import (
    TYPE_BESS,
    TYPE_CIRCUIT,
    TYPE_CORE,
    TYPE_EVSE,
    TYPE_LUGS,
    TYPE_LUGS_DOWNSTREAM,
    TYPE_LUGS_UPSTREAM,
    TYPE_POWER_FLOWS,
    TYPE_PV,
)

_LOGGER = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Static mapping: (node_type, property_id) → snapshot field path
#
# This encodes the library's internal knowledge of how _build_snapshot()
# maps Homie properties to snapshot dataclass fields. The mapping must be
# kept in sync with homie.py.
# ---------------------------------------------------------------------------

_PROPERTY_FIELD_MAP: tuple[tuple[str, str, str], ...] = (
    # --- Core node → panel.* -------------------------------------------------
    (TYPE_CORE, "software-version", "panel.firmware_version"),
    (TYPE_CORE, "door", "panel.door_state"),
    (TYPE_CORE, "relay", "panel.main_relay_state"),
    (TYPE_CORE, "ethernet", "panel.eth0_link"),
    (TYPE_CORE, "wifi", "panel.wlan_link"),
    (TYPE_CORE, "vendor-cloud", "panel.vendor_cloud"),
    (TYPE_CORE, "dominant-power-source", "panel.dominant_power_source"),
    (TYPE_CORE, "grid-islandable", "panel.grid_islandable"),
    (TYPE_CORE, "l1-voltage", "panel.l1_voltage"),
    (TYPE_CORE, "l2-voltage", "panel.l2_voltage"),
    (TYPE_CORE, "breaker-rating", "panel.main_breaker_rating_a"),
    (TYPE_CORE, "wifi-ssid", "panel.wifi_ssid"),
    # --- Upstream lugs → panel.* (main meter) --------------------------------
    (TYPE_LUGS_UPSTREAM, "active-power", "panel.instant_grid_power_w"),
    (TYPE_LUGS_UPSTREAM, "imported-energy", "panel.main_meter_energy_consumed_wh"),
    (TYPE_LUGS_UPSTREAM, "exported-energy", "panel.main_meter_energy_produced_wh"),
    (TYPE_LUGS_UPSTREAM, "l1-current", "panel.upstream_l1_current_a"),
    (TYPE_LUGS_UPSTREAM, "l2-current", "panel.upstream_l2_current_a"),
    # --- Downstream lugs → panel.* (feedthrough) -----------------------------
    (TYPE_LUGS_DOWNSTREAM, "active-power", "panel.feedthrough_power_w"),
    (TYPE_LUGS_DOWNSTREAM, "imported-energy", "panel.feedthrough_energy_consumed_wh"),
    (TYPE_LUGS_DOWNSTREAM, "exported-energy", "panel.feedthrough_energy_produced_wh"),
    (TYPE_LUGS_DOWNSTREAM, "l1-current", "panel.downstream_l1_current_a"),
    (TYPE_LUGS_DOWNSTREAM, "l2-current", "panel.downstream_l2_current_a"),
    # --- Circuit → circuit.* -------------------------------------------------
    (TYPE_CIRCUIT, "active-power", "circuit.instant_power_w"),
    (TYPE_CIRCUIT, "exported-energy", "circuit.consumed_energy_wh"),
    (TYPE_CIRCUIT, "imported-energy", "circuit.produced_energy_wh"),
    (TYPE_CIRCUIT, "name", "circuit.name"),
    (TYPE_CIRCUIT, "relay", "circuit.relay_state"),
    (TYPE_CIRCUIT, "shed-priority", "circuit.priority"),
    (TYPE_CIRCUIT, "current", "circuit.current_a"),
    (TYPE_CIRCUIT, "breaker-rating", "circuit.breaker_rating_a"),
    (TYPE_CIRCUIT, "space", "circuit.tabs"),
    (TYPE_CIRCUIT, "sheddable", "circuit.is_sheddable"),
    (TYPE_CIRCUIT, "never-backup", "circuit.is_never_backup"),
    (TYPE_CIRCUIT, "always-on", "circuit.always_on"),
    (TYPE_CIRCUIT, "dipole", "circuit.is_240v"),
    (TYPE_CIRCUIT, "relay-requester", "circuit.relay_requester"),
    # --- BESS → battery.* ----------------------------------------------------
    (TYPE_BESS, "soc", "battery.soe_percentage"),
    (TYPE_BESS, "soe", "battery.soe_kwh"),
    (TYPE_BESS, "vendor-name", "battery.vendor_name"),
    (TYPE_BESS, "product-name", "battery.product_name"),
    (TYPE_BESS, "model", "battery.model"),
    (TYPE_BESS, "serial-number", "battery.serial_number"),
    (TYPE_BESS, "software-version", "battery.software_version"),
    (TYPE_BESS, "nameplate-capacity", "battery.nameplate_capacity_kwh"),
    (TYPE_BESS, "connected", "battery.connected"),
    (TYPE_BESS, "grid-state", "panel.grid_state"),
    # --- PV → pv.* -----------------------------------------------------------
    (TYPE_PV, "vendor-name", "pv.vendor_name"),
    (TYPE_PV, "product-name", "pv.product_name"),
    (TYPE_PV, "nameplate-capacity", "pv.nameplate_capacity_w"),
    (TYPE_PV, "feed", "pv.feed_circuit_id"),
    (TYPE_PV, "relative-position", "pv.relative_position"),  # IN_PANEL | UPSTREAM | DOWNSTREAM
    # --- EVSE → evse.* -------------------------------------------------------
    (TYPE_EVSE, "status", "evse.status"),
    (TYPE_EVSE, "lock-state", "evse.lock_state"),
    (TYPE_EVSE, "advertised-current", "evse.advertised_current_a"),
    (TYPE_EVSE, "vendor-name", "evse.vendor_name"),
    (TYPE_EVSE, "product-name", "evse.product_name"),
    (TYPE_EVSE, "part-number", "evse.part_number"),
    (TYPE_EVSE, "serial-number", "evse.serial_number"),
    (TYPE_EVSE, "software-version", "evse.software_version"),
    (TYPE_EVSE, "feed", "evse.feed_circuit_id"),
    # --- Power flows → panel.* -----------------------------------------------
    (TYPE_POWER_FLOWS, "pv", "panel.power_flow_pv"),
    (TYPE_POWER_FLOWS, "battery", "panel.power_flow_battery"),
    (TYPE_POWER_FLOWS, "grid", "panel.power_flow_grid"),
    (TYPE_POWER_FLOWS, "site", "panel.power_flow_site"),
)

# Generic lugs type used by some firmware versions instead of typed variants.
# Properties are identical; only the node type differs.
_LUGS_FALLBACK: dict[str, str] = {
    TYPE_LUGS_UPSTREAM: TYPE_LUGS,
    TYPE_LUGS_DOWNSTREAM: TYPE_LUGS,
}


def _lookup_property(
    schema_types: HomieSchemaTypes,
    node_type: str,
    property_id: str,
) -> dict[str, object] | None:
    """Look up a property definition in the schema, with lugs fallback."""
    node_props = schema_types.get(node_type)
    if isinstance(node_props, dict):
        prop_def = node_props.get(property_id)
        if isinstance(prop_def, dict):
            return prop_def

    # Try generic lugs fallback
    fallback_type = _LUGS_FALLBACK.get(node_type)
    if fallback_type is not None:
        fb_props = schema_types.get(fallback_type)
        if isinstance(fb_props, dict):
            prop_def = fb_props.get(property_id)
            if isinstance(prop_def, dict):
                return prop_def

    return None


def build_field_metadata(
    schema_types: HomieSchemaTypes,
) -> dict[str, FieldMetadata]:
    """Build field metadata from the Homie schema.

    Iterates the static property-to-field mapping, looks up each property
    in the schema to get its declared unit and datatype, and returns a dict
    keyed by snapshot field path.

    Args:
        schema_types: The ``V2HomieSchema.types`` dict.

    Returns:
        Dict mapping field paths to ``FieldMetadata``.
    """
    result: dict[str, FieldMetadata] = {}

    for node_type, property_id, field_path in _PROPERTY_FIELD_MAP:
        prop_def = _lookup_property(schema_types, node_type, property_id)
        if prop_def is None:
            continue

        raw_unit = prop_def.get("unit")
        unit = str(raw_unit) if raw_unit is not None else None
        raw_datatype = prop_def.get("datatype")
        datatype = str(raw_datatype) if raw_datatype is not None else "string"

        result[field_path] = FieldMetadata(unit=unit, datatype=datatype)

    return result


def log_schema_drift(
    previous: HomieSchemaTypes,
    current: HomieSchemaTypes,
) -> None:
    """Log property-level differences between two schema versions.

    Called by the client when the schema hash changes between connections.
    All Homie-specific detail stays in this module — the integration never
    sees this output, only the transport-agnostic field metadata.
    """
    prev_types = set(previous.keys())
    curr_types = set(current.keys())

    for node_type in sorted(curr_types - prev_types):
        _LOGGER.debug("Schema drift: new node type '%s'", node_type)

    for node_type in sorted(prev_types - curr_types):
        _LOGGER.debug("Schema drift: removed node type '%s'", node_type)

    for node_type in sorted(prev_types & curr_types):
        prev_props = previous[node_type]
        curr_props = current[node_type]
        if not isinstance(prev_props, dict) or not isinstance(curr_props, dict):
            continue

        for prop_id in sorted(set(curr_props) - set(prev_props)):
            _LOGGER.debug("Schema drift: new property '%s/%s'", node_type, prop_id)

        for prop_id in sorted(set(prev_props) - set(curr_props)):
            _LOGGER.debug("Schema drift: removed property '%s/%s'", node_type, prop_id)

        for prop_id in sorted(set(prev_props) & set(curr_props)):
            prev_def = prev_props[prop_id]
            curr_def = curr_props[prop_id]
            if not isinstance(prev_def, dict) or not isinstance(curr_def, dict):
                continue
            for attr in ("datatype", "unit", "format"):
                old_val = prev_def.get(attr)
                new_val = curr_def.get(attr)
                if old_val != new_val:
                    _LOGGER.debug(
                        "Schema drift: '%s/%s' %s changed: '%s' → '%s'",
                        node_type,
                        prop_id,
                        attr,
                        old_val,
                        new_val,
                    )
