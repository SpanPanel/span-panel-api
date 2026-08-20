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

**Two kinds of row, one map.** Alongside the curated rows this builds a
discovery row for every property the tree declares that this adapter addresses
nowhere, namespaced so a consumer can partition the two before it reads either.
See `build_discovery`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from span_panel_api.models import DiscoveredMetadata, FieldMetadata, discovery_path
from span_panel_api_schema_1.charge_limit import ChargeLimitProperty, resolve_charge_limit
from span_panel_api_schema_1.const import (
    DEVICE_TYPE_PREFIX,
    NODE_BREAKER,
    NODE_CONNECTION,
    NODE_DOOR,
    NODE_GRID,
    NODE_INFO,
    NODE_LOAD_SHED,
    NODE_METER,
    NODE_PCS,
    NODE_POWER_FLOWS,
    NODE_SHED,
    NODE_SHED_FORECAST,
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
    TYPE_MID,
    TYPE_PANEL,
    TYPE_PV,
)
from span_panel_api_schema_1.description import nodes as declared_nodes, optional_str, properties as declared_properties
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
    # The SSID, not the link. Flat carries the same row (`core/wifi-ssid`), which
    # is what makes this a plain both-adapters declaration on the consumer side
    # rather than a schema-conditional one -- and what makes its absence here a
    # regression rather than a new feature.
    (TYPE_PANEL, NODE_STATUS, "wifi-ssid", "panel.wifi_ssid"),
    (TYPE_PANEL, NODE_STATUS, "cloud-connection", "panel.vendor_cloud"),
    (TYPE_PANEL, NODE_METER, "voltage-a", "panel.l1_voltage"),
    (TYPE_PANEL, NODE_METER, "voltage-b", "panel.l2_voltage"),
    (TYPE_PANEL, NODE_BREAKER, "rating", "panel.main_breaker_rating_a"),
    (TYPE_PANEL, NODE_POWER_FLOWS, "pv", "panel.power_flow_pv"),
    (TYPE_PANEL, NODE_POWER_FLOWS, "battery", "panel.power_flow_battery"),
    (TYPE_PANEL, NODE_POWER_FLOWS, "grid", "panel.power_flow_grid"),
    (TYPE_PANEL, NODE_POWER_FLOWS, "site", "panel.power_flow_site"),
    # Only the two live estimates. The `full-charge-*` pair and `confidence`
    # are read too, but a consumer renders them beside these rather than as
    # readings of their own, so a unit row for them would advertise a surface
    # that is not there. The pair below is what carries `min`, and with it the
    # declared-gap signal: a panel that publishes the node while omitting one of
    # these reports it as degradation rather than as absent hardware.
    (TYPE_PANEL, NODE_SHED_FORECAST, "time-to-priority-shed", "panel.shed_time_to_priority_shed_min"),
    (TYPE_PANEL, NODE_SHED_FORECAST, "total-time-remaining", "panel.shed_total_time_remaining_min"),
    # --- Panel `pcs` → pcs.* -------------------------------------------------
    # Only the three the capability calls "the result", plus the state that
    # decides whether there is a result at all. `capabilities/pcs.md` is
    # explicit that `pcs` does not re-publish the other regimes' constraints:
    # "what `pcs` publishes is the **result**: the effective `import-limit` and
    # the `binding-constraint`". Those are what a consumer renders as readings,
    # so those are what carry a unit row — `import-limit` in particular, whose
    # `A` is validated against the sensor's declared unit.
    #
    # The four constraint families and `enabled` are read into the snapshot too
    # and deliberately have no row: they qualify the effective limit rather than
    # standing as readings, exactly as the `shed-forecast` full-charge pair
    # does, and a unit row for them would advertise a surface that is not there.
    (TYPE_PANEL, NODE_PCS, "import-limit", "pcs.import_limit_a"),
    (TYPE_PANEL, NODE_PCS, "binding-constraint", "pcs.binding_constraint"),
    (TYPE_PANEL, NODE_PCS, "active", "pcs.active"),
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
    # --- Circuit `connection` -> the DER the circuit feeds ---------------------
    # The one place a row's device type and its field path deliberately disagree.
    # v1.0 states the enclosure/DER relationship on the *circuit*, so the panel's
    # view of the link to a PV or a charger is published by whichever circuit
    # feeds it -- `build_pv` and `build_evse` read it through
    # `feed_connection_statuses`, and the field it fills belongs to the DER.
    #
    # Two rows for one property, because one circuit's record describes a PV and
    # another's describes a charger. That is what the property *is* on this
    # device class; which DER a given instance names is a value, and a metadata
    # row describes neither values nor instances.
    #
    # `feeds-device-id` carries no row on purpose: it is topology the mapper
    # consumes into `feed_circuit_id`, `device_type` and `relative_position`, and
    # a unit for a device id would describe a reading nothing renders.
    (TYPE_CIRCUIT, NODE_CONNECTION, "feeds-device-status", "evse.connected"),
    (TYPE_CIRCUIT, NODE_CONNECTION, "feeds-device-status", "pv.connected"),
    # --- BESS ----------------------------------------------------------------
    (TYPE_BESS, NODE_SOC, "soc", "battery.soe_percentage"),
    (TYPE_BESS, NODE_SOC, "soe", "battery.soe_kwh"),
    (TYPE_BESS, NODE_INFO, "vendor-name", "battery.vendor_name"),
    (TYPE_BESS, NODE_INFO, "model", "battery.model"),
    (TYPE_BESS, NODE_INFO, "part-number", "battery.part_number"),
    (TYPE_BESS, NODE_INFO, "serial-number", "battery.serial_number"),
    (TYPE_BESS, NODE_INFO, "firmware-version", "battery.software_version"),
    (TYPE_BESS, NODE_INFO, "nameplate-capacity", "battery.nameplate_capacity_kwh"),
    # The BESS's own meter and its own link health. `battery.power_w` carries a
    # sign flip (`build_battery` reports charge-positive, the wire is
    # charge-negative), which does not affect the unit or the datatype this row
    # describes — a row states what the property *is*, not what the mapper does
    # with it.
    (TYPE_BESS, NODE_METER, "active-power", "battery.power_w"),
    (TYPE_BESS, NODE_STATUS, "communication-state", "battery.communication_state"),
    # --- PV ------------------------------------------------------------------
    (TYPE_PV, NODE_INFO, "vendor-name", "pv.vendor_name"),
    (TYPE_PV, NODE_INFO, "model", "pv.model"),
    (TYPE_PV, NODE_INFO, "nominal-power", "pv.nameplate_capacity_w"),
    # --- EVSE ----------------------------------------------------------------
    (TYPE_EVSE, NODE_STATUS, "status", "evse.status"),
    (TYPE_EVSE, NODE_SWITCH, "lock-state", "evse.lock_state"),
    (TYPE_EVSE, NODE_METER, "advertised-current", "evse.advertised_current_a"),
    # The charger's SKU. Flat maps `evse/part-number` to the same field, so this
    # row is what lifts `evse.part_number` out of one-adapter exemption and into
    # a declaration the producible gate covers on both.
    (TYPE_EVSE, NODE_INFO, "part-number", "evse.part_number"),
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
        for node_id, node in declared_nodes(description).items():
            present_type_nodes.add((device_type, node_id))
            for property_id, definition in declared_properties(node).items():
                declared[f"{device_type}|{node_id}|{property_id}"] = (
                    optional_str(definition.get("unit")),
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
    metadata.update(_charge_limit_metadata(devices))
    # Namespaced, so a consumer partitions them out before it reads the map as
    # an inventory of produced fields. See `build_discovery`.
    metadata.update(build_discovery(devices))
    return metadata


def _charge_limit_metadata(devices: list[DiscoveredDevice]) -> dict[str, FieldMetadata]:
    """Metadata for the EVSE charge-current pair, resolved the way the value is.

    The table above cannot describe these, for the same reason it cannot
    describe the lugs meter: it is keyed `(device type, node, property)`, and
    the node and the property are precisely what a charger gets to choose here.
    A row would have to name one spelling, which is the guess `charge_limit`
    exists to avoid — and naming both would let a charger that declares neither
    resolve through a row written for the other.

    So it goes through `resolve_charge_limit`, the same call `build_evse` makes,
    which is what keeps the unit a field advertises and the value that fills it
    describing the same property.

    The first charger declaring a surface answers for the path, matching
    `_lookup`'s rule for every other type-keyed row: a field path is per snapshot
    field, not per device, and two chargers on one panel declare one property
    set each. A surface that declares only one of the pair leaves the other
    `resolved=False` — the node is there and the property is not, which is a gap
    rather than absent hardware.
    """
    for device in devices:
        if not declared_type(device).startswith(TYPE_EVSE):
            continue
        surface = resolve_charge_limit(device)
        if surface is None:
            continue
        return {
            "evse.charge_current_limit_a": _charge_limit_entry(surface.limit),
            "evse.charge_current_ceiling_a": _charge_limit_entry(surface.ceiling),
        }
    return {}


def _charge_limit_entry(declaration: ChargeLimitProperty | None) -> FieldMetadata:
    if declaration is None:
        return FieldMetadata(unit=None, datatype="unknown", resolved=False)
    return FieldMetadata(unit=declaration.unit, datatype=declaration.datatype)


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

    meter = declared_nodes(lugs.description or {}).get(NODE_METER)
    if meter is None:
        return {}

    declared = declared_properties(meter)
    found: dict[str, FieldMetadata] = {}
    for property_id, field_path in fields:
        definition = declared.get(property_id)
        if definition is None:
            found[field_path] = FieldMetadata(unit=None, datatype="unknown", resolved=False)
            continue
        found[field_path] = FieldMetadata(
            unit=optional_str(definition.get("unit")),
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


_CONSUMED_WITHOUT_A_ROW: tuple[tuple[str, str, str], ...] = (
    # Properties the snapshot mapper reads that carry no `_PROPERTY_FIELD_MAP`
    # row, and never will: a row exists to state a *reading's* unit and
    # datatype, and none of these is a reading. They are build identity, the
    # topology the mapper resolves roles from, and the qualifiers a consumer
    # renders beside a reading rather than as one.
    #
    # Without this table `build_discovery` would report all of them as
    # unaddressed, because the only enumeration of what schema_1 reads was the
    # metadata map — which is a third of it. Every entry is proved by
    # experiment: `test_schema_one_discovery` republishes each one against the
    # reference tree and fails if the snapshot does not move, so an entry that
    # stops being true is a red build rather than a property that quietly
    # disappears from discovery.
    #
    # --- Panel identity, read by the snapshot's panel fields -----------------
    (TYPE_PANEL, NODE_INFO, "hardware-version"),
    (TYPE_PANEL, NODE_INFO, "model"),
    (TYPE_PANEL, NODE_INFO, "serial-number"),
    (TYPE_PANEL, NODE_INFO, "vendor-name"),
    # --- The PCS arbitration's inputs ---------------------------------------
    # `import-limit`, `binding-constraint` and `active` are the result and carry
    # rows; these thirteen explain the result. See the `pcs` rows above.
    (TYPE_PANEL, NODE_PCS, "enabled"),
    (TYPE_PANEL, NODE_PCS, "feed-import-limit"),
    (TYPE_PANEL, NODE_PCS, "feed-import-limit-active"),
    (TYPE_PANEL, NODE_PCS, "feed-import-limit-enablement"),
    (TYPE_PANEL, NODE_PCS, "off-grid-import-limit"),
    (TYPE_PANEL, NODE_PCS, "off-grid-import-limit-active"),
    (TYPE_PANEL, NODE_PCS, "off-grid-import-limit-enablement"),
    (TYPE_PANEL, NODE_PCS, "operator-import-limit"),
    (TYPE_PANEL, NODE_PCS, "operator-import-limit-active"),
    (TYPE_PANEL, NODE_PCS, "operator-import-limit-enablement"),
    (TYPE_PANEL, NODE_PCS, "requested-import-limit"),
    (TYPE_PANEL, NODE_PCS, "requested-import-limit-active"),
    (TYPE_PANEL, NODE_PCS, "requested-import-limit-enablement"),
    # --- The shed policy document and the forecast's refinements -------------
    (TYPE_PANEL, NODE_SHED, "policy"),
    (TYPE_PANEL, NODE_SHED_FORECAST, "confidence"),
    (TYPE_PANEL, NODE_SHED_FORECAST, "full-charge-time-to-priority-shed"),
    (TYPE_PANEL, NODE_SHED_FORECAST, "full-charge-total-time-remaining"),
    # --- Circuit topology and PCS membership --------------------------------
    (TYPE_CIRCUIT, NODE_CONNECTION, "feeds-device-id"),
    (TYPE_CIRCUIT, NODE_PCS, "managed"),
    (TYPE_CIRCUIT, NODE_PCS, "priority"),
    # --- Lugs direction and the upstream device's link -----------------------
    # `info/direction` decides which lugs device is the main meter and which is
    # the feedthrough, so it moves ten panel fields without being one.
    (TYPE_LUGS, NODE_CONNECTION, "fed-by-device-id"),
    (TYPE_LUGS, NODE_CONNECTION, "fed-by-device-status"),
    (TYPE_LUGS, NODE_INFO, "direction"),
    # --- MID ------------------------------------------------------------------
    # The islanding authority. Every field it feeds is device-card identity or
    # a state string, so the whole device is here rather than in the map.
    (TYPE_MID, NODE_GRID, "grid-forming-entity"),
    (TYPE_MID, NODE_GRID, "grid-state"),
    (TYPE_MID, NODE_GRID, "islanding-state"),
    (TYPE_MID, NODE_INFO, "firmware-version"),
    (TYPE_MID, NODE_INFO, "hardware-version"),
    (TYPE_MID, NODE_INFO, "model"),
    (TYPE_MID, NODE_INFO, "serial-number"),
    (TYPE_MID, NODE_INFO, "vendor-name"),
    # --- DER identity ---------------------------------------------------------
    (TYPE_EVSE, NODE_INFO, "firmware-version"),
    (TYPE_EVSE, NODE_INFO, "model"),
    (TYPE_EVSE, NODE_INFO, "serial-number"),
    (TYPE_EVSE, NODE_INFO, "vendor-name"),
    (TYPE_PV, NODE_INFO, "firmware-version"),
)
"""Declarations the mapper reads into the snapshot without a metadata row.

The charge-current pair is deliberately absent: which node and which property
carry it is the charger's choice, so `build_discovery` resolves it through
`resolve_charge_limit` exactly as `_charge_limit_metadata` does, rather than
naming one spelling here and leaving the other reported as unaddressed.
"""

_CONSUMED_OFF_SNAPSHOT: dict[tuple[str, str, str], str] = {
    (TYPE_PANEL, NODE_INFO, "data-model-version"): (
        "tier-1 adapter dispatch (span_panel_api.dispatch) — it chooses which adapter "
        "parses the tree, so it is consumed before any snapshot exists"
    ),
    (TYPE_PANEL, NODE_SHED, "asserted-islanding-state"): (
        "tier 2 of resolve_islanding_state (panel.py), shadowed wherever a MID answers "
        "at tier 1, and the write target of set_dominant_power_source_topic"
    ),
    (TYPE_LUGS, NODE_CONNECTION, "feeds-device-id"): (
        "the downstream-lugs feedthrough branch of resolve_relative_position "
        "(devices.py), which no producer currently reaches"
    ),
}
"""Declarations this library reads by a route no snapshot field can show.

The category the republish experiment cannot measure, and therefore the one at
risk of becoming an allowlist. It is held to the opposite assertion instead:
`test_an_off_snapshot_route_that_became_observable_must_be_retired` fails the
moment one of these does move a snapshot field, because at that point the route
is no longer the only thing consuming it and the entry is hiding a real reader.

Three, and each names the code that reads it. The consumer-side mirror is
`_INTERNAL_ROUTES` in the integration's `test_declared_but_unread`; they agree
because they are answering the same question of the same tree, not because
either copies the other.
"""

_ADDRESSED: frozenset[tuple[str, str, str]] = (
    frozenset((device_type, node_id, property_id) for device_type, node_id, property_id, _ in _PROPERTY_FIELD_MAP)
    | frozenset(
        (TYPE_LUGS, NODE_METER, property_id) for property_id, _ in (*_UPSTREAM_LUGS_FIELDS, *_DOWNSTREAM_LUGS_FIELDS)
    )
    | frozenset(_CONSUMED_WITHOUT_A_ROW)
    | frozenset(_CONSUMED_OFF_SNAPSHOT)
)
"""Every ``(device type, node, property)`` this library addresses, from all four tables.

Derived rather than restated, so a new `_PROPERTY_FIELD_MAP` row leaves
discovery without anyone remembering to. The charge-current pair is added per
charger at build time; see `build_discovery`.
"""


def build_discovery(devices: list[DiscoveredDevice]) -> dict[str, DiscoveredMetadata]:
    """Metadata rows for every property this tree declares that nothing here reads.

    The runtime half of the declared-but-unread question. A vendored capture can
    only answer it for the panel that was captured; a panel in the field that
    starts publishing a property fails nothing and tells nobody until someone
    recaptures. These rows put the same answer in front of a maintainer for the
    panel actually in front of the user.

    Keyed under `DISCOVERY_NAMESPACE`, never as a snapshot field path, because a
    consumer's curated inventories are keyed by field path and a discovered row
    reaching one of them would read as a produced field nothing renders — which
    is the shape of a real defect. The namespace makes the partition one string
    test applied once.

    **Declarations only.** A row carries the property's declared unit and
    datatype and whether a value has arrived. It never carries the value: these
    rows are built to be forwarded in consumer diagnostics, which leave the
    machine they were generated on.

    Emitted by this adapter alone. schema_0 builds its metadata from the REST
    ``types`` document, which the migration guide describes as the superset
    across all hardware rather than what one panel has — so "declared and
    unaddressed" there would describe the schema document and could not answer
    the question this exists to ask.
    """
    addressed = set(_ADDRESSED)
    for device in devices:
        evse_type = declared_type(device)
        if not evse_type.startswith(TYPE_EVSE):
            continue
        surface = resolve_charge_limit(device)
        if surface is None:
            continue
        for declaration in (surface.limit, surface.ceiling):
            if declaration is not None:
                addressed.add((evse_type, surface.node, declaration.property_id))

    declarations: dict[str, tuple[str | None, str]] = {}
    valued: set[str] = set()
    for device in devices:
        device_type = declared_type(device)
        if not device_type:
            continue
        for node_id, node in declared_nodes(device.description or {}).items():
            for property_id, definition in declared_properties(node).items():
                if _addressed_by(addressed, device_type, node_id, property_id):
                    continue
                path = discovery_path(_short_type(device_type), node_id, property_id)
                declarations.setdefault(
                    path,
                    (optional_str(definition.get("unit")), str(definition.get("datatype") or "string")),
                )
                if device.get_property(node_id, property_id) is not None:
                    valued.add(path)

    return {
        path: DiscoveredMetadata(unit=unit, datatype=datatype, retained=path in valued)
        for path, (unit, datatype) in declarations.items()
    }


def _short_type(device_type: str) -> str:
    """The device type as the capability catalog and the gap inventories spell it."""
    if device_type.startswith(DEVICE_TYPE_PREFIX):
        return device_type[len(DEVICE_TYPE_PREFIX) :]
    return device_type


def _addressed_by(addressed: set[tuple[str, str, str]], device_type: str, node_id: str, property_id: str) -> bool:
    """Whether any addressed row covers this declaration, subtypes included.

    Carries `_lookup`'s subtype rule for the same reason it exists there: eBus
    device types are hierarchical and a subtype carries its parent's properties,
    so a device typed ``X.Y`` is served by a row written for ``X``. Without it a
    panel that subtypes its lugs devices would report every mapped lugs property
    as newly discovered — which is the false positive that would teach a
    maintainer to stop reading this.
    """
    return any(
        node == node_id and prop == property_id and (device_type == mapped_type or device_type.startswith(f"{mapped_type}."))
        for mapped_type, node, prop in addressed
    )
