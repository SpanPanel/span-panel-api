"""Map the BESS, PV and EVSE devices onto their snapshot dataclasses.

Two mappings here are deliberately *not* the obvious one, because v1.0 changed
what a name means rather than only where it lives. Both would produce a
plausible value that silently means something else:

**The BESS model/part-number swap.** Flat ``bess/model`` was the SKU
(``1232100-00-E``); v1.0's ``info/model`` is the human designation
(``Powerwall 2 AC``) and the SKU moved to the new ``info/part-number``. Mapping
``info/model`` onto ``battery.model`` would keep the entity and change what it
displays, so the SKU is taken from ``part-number`` and the designation from
``model``.

**Battery connectivity moved off the battery.** ``battery.connected`` is now the
panel-side owner's ``connection/*-device-status``, not anything the BESS
publishes about itself. The BESS's own ``status/communication-state`` looks like
the right property and is a different signal — the migration guide warns
against conflating them. Both are now carried, in separate fields
(``connected`` and ``communication_state``), because they answer different
questions: the panel's view of the link, and the publisher's view of its own.

**Battery power is discharge-positive, and the wire's frame for it changed at r202639.**
``battery.power_w`` is positive while the battery *discharges*: the frame the eBus
specification asks for from a device's own meter, and deliberately not the
into-the-device rule ``build_circuit`` follows.

Through r202633 the BESS ``meter/active-power`` is charge-positive, carrying the
same sign as the enclosure's ``power-flows/battery``, so ``build_battery`` negates
it. Measured rather than reasoned: driving the producer into self-consumption
with the grid at zero forces the direction. PV 4181 W plus battery 1917 W meeting
a 6099 W load leaves nothing ambiguous: the battery is discharging. The wire
publishes ``-1917.49`` and this module reports ``+1917.49``.

From r202639 the BESS meter is published discharge-positive, the negation of
``power-flows/battery`` (whose own sign did not change), so it passes through.
The frame is chosen from the enclosure's release build number
(``firmware.release_build``), never from the respin suffix after it. Where the
version does not parse, the two wire properties decide instead: one nonzero sign
on both is the old frame, opposite signs the new one. Where either is zero or
absent the old frame is assumed, because that is what every earlier release of
this package applied and what the reference capture (``ebus-panel-sim`` 0.8.0,
which publishes no SPAN version string) carries. A battery at rest reads zero in
both frames, so the assumption only decides a nonzero BESS meter beside a missing
or zero flow figure.

On either firmware ``panel.power_flow_battery`` (passed through untouched, and
charge-positive) and ``battery.power_w`` end up as each other's mirror, so a
consumer showing both sees one convention after negating either one.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from span_panel_api.models import SpanBatterySnapshot, SpanEvseSnapshot, SpanMidSnapshot, SpanPVSnapshot
from span_panel_api_schema_1.charge_limit import ChargeLimitProperty, ChargeLimitSurface, resolve_charge_limit
from span_panel_api_schema_1.const import (
    NODE_CONNECTION,
    NODE_GRID,
    NODE_INFO,
    NODE_METER,
    NODE_SOC,
    NODE_STATUS,
    NODE_SWITCH,
    PROP_ACTIVE_POWER,
    PROP_COMMUNICATION_STATE,
    PROP_FED_BY_DEVICE_ID,
    PROP_FED_BY_DEVICE_STATUS,
    PROP_FEEDS_DEVICE_ID,
    PROP_FEEDS_DEVICE_STATUS,
    PROP_FIRMWARE_VERSION,
    PROP_HARDWARE_VERSION,
    PROP_MODEL,
    PROP_SERIAL_NUMBER,
    PROP_VENDOR_NAME,
    UNKNOWN,
)
from span_panel_api_schema_1.description import declared_settable, node_properties
from span_panel_api_schema_1.firmware import release_build
from span_panel_api_schema_1.panel import integer, number, resolve_grid_forming_device_name, text
from span_panel_api_schema_1.transport import control_target

if TYPE_CHECKING:
    from collections.abc import Mapping

    from ebus_sdk.homie import DiscoveredDevice

# The `info` properties only a sub-device carries. The five the enclosure
# publishes too -- vendor name, model, serial, firmware and hardware revision --
# are imported from `const` above rather than restated here: they name one wire
# property each, and two spellings of one property is how a rename reaches one
# reader and not the other.
PROP_NAMEPLATE_CAPACITY = "nameplate-capacity"
PROP_NOMINAL_POWER = "nominal-power"
PROP_PART_NUMBER = "part-number"

PROP_SOC = "soc"
PROP_SOE = "soe"

PROP_ADVERTISED_CURRENT = "advertised-current"
PROP_LOCK_STATE = "lock-state"
PROP_STATUS = "status"

STATUS_OK = "OK"


def _optional(value: str) -> str | None:
    """Empty means the panel did not publish it, which is not the same as ''."""
    return value or None


def feed_circuit_ids(circuits: list[DiscoveredDevice]) -> dict[str, str]:
    """Map each fed device id to the circuit that feeds it.

    v1.0 states the relationship on the *circuit* (``connection/feeds-device-id``)
    rather than on the DER, so it is read once here and handed to whichever
    device needs it. A device fed by more than one circuit (the specification
    allows it) maps to the circuit with the lowest device id, so the answer, and
    every key built on it, does not depend on discovery order.
    """
    feeds: dict[str, str] = {}
    for circuit in sorted(circuits, key=lambda device: device.device_id):
        fed = text(circuit, NODE_CONNECTION, PROP_FEEDS_DEVICE_ID)
        if fed and fed not in feeds:
            feeds[fed] = circuit.device_id
    return feeds


def connection_status_for(device_id: str, owners: list[DiscoveredDevice]) -> str | None:
    """The connection status a panel-side owner reports *about* `device_id`.

    This is where ``battery.connected`` comes from. Returns None when nothing
    claims the device, which is the honest answer for a panel whose owner has
    not announced yet — distinct from a device that is known-disconnected.
    """
    for owner in owners:
        if text(owner, NODE_CONNECTION, PROP_FED_BY_DEVICE_ID) == device_id:
            return _optional(text(owner, NODE_CONNECTION, PROP_FED_BY_DEVICE_STATUS))
    return None


def feed_connection_statuses(circuits: list[DiscoveredDevice]) -> dict[str, str]:
    """The enclosure's view of the link to each circuit-fed device, by device id.

    The other half of the record ``feed_circuit_ids`` reads, and read alongside
    it for the same reason: v1.0 states the relationship on the *circuit*, so a
    DER's link health is published by whichever circuit feeds it rather than by
    the DER. Same fact as ``connection_status_for``, opposite direction — that
    one scans owners for a ``fed-by-*`` record naming the device, this one
    indexes every ``feeds-*`` record a circuit publishes.

    A circuit is absent from the result unless it publishes *both* halves. An
    id with no status cannot say anything about the link, and a status with no
    id names no device to say it about; the enclosure model
    (``distribution-enclosure.md``) makes an unpublished property the panel's
    way of saying it does not know, so absence here is what a caller turns into
    `None` rather than into a fault.

    Most circuits publish neither. A mixed-load or unsurveyed circuit feeds no
    commissioned DER, so it has no connection record to publish — the spec calls
    that normal, which is why nothing here treats a missing record as an error.

    A device fed by more than one circuit takes the status of the circuit
    ``feed_circuit_ids`` chose for it, so its link health and its feed come from
    one record whatever the discovery order. That circuit's silence is not
    filled from the other feed: it leaves the device absent here, as any
    circuit publishing only one half does.
    """
    by_id = {circuit.device_id: circuit for circuit in circuits}
    statuses: dict[str, str] = {}
    for fed, circuit_id in feed_circuit_ids(circuits).items():
        status = text(by_id[circuit_id], NODE_CONNECTION, PROP_FEEDS_DEVICE_STATUS)
        if status:
            statuses[fed] = status
    return statuses


def _connected(status: str | None) -> bool | None:
    """Collapse a ``connection`` status enum to the snapshot's boolean.

    `None` stays `None`, so "nobody has said" remains distinct from "not OK".
    The enum is ``OK,LOST,DEGRADED`` with no UNKNOWN member, so absence of the
    property is the only unknown the wire can express and this is the one place
    that decides what it means.

    DEGRADED collapses to `False` deliberately: the question a consumer asks of
    this field is "can the enclosure talk to the device", and a degraded link is
    not a working one. The distinction survives where it is a device's own
    report — `battery.communication_state` keeps the enum string — but here it
    is the panel's view, and the panel publishes no richer field for a consumer
    to fall back on.
    """
    return None if status is None else status == STATUS_OK


BESS_METER_DISCHARGE_POSITIVE_FROM_BUILD = 202639
"""The first release build whose BESS ``meter/active-power`` is discharge-positive."""


def _bess_meter_is_charge_positive(
    firmware_version: str | None,
    raw_power_w: float | None,
    power_flow_battery: float | None,
) -> bool:
    """Whether the BESS meter on the wire is in the pre-r202639 frame.

    The enclosure's release build decides when it parses. Otherwise the BESS
    meter is compared with ``power-flows/battery``, which kept its sign across
    the change: equal signs mean the old frame, opposite signs the new one. With
    either one zero or absent the comparison says nothing, and the old frame is
    assumed (see the module docstring for why).
    """
    build = release_build(firmware_version)
    if build is not None:
        return build < BESS_METER_DISCHARGE_POSITIVE_FROM_BUILD
    if raw_power_w is None or power_flow_battery is None or raw_power_w == 0.0 or power_flow_battery == 0.0:
        return True
    return (raw_power_w > 0.0) == (power_flow_battery > 0.0)


def _discharge_positive(raw_power_w: float | None, *, charge_positive_on_wire: bool) -> float | None:
    """Bring the BESS meter into the device's own frame, discharge-positive.

    Positive means power flowing *out of the battery*, which is discharging, and
    which is what the eBus specification asks of a device's own meter. A wire
    value already in that frame passes through; one in the older charge-positive
    frame is negated.

    `None` stays `None`: a BESS that publishes no `meter` node has no power
    reading, which is not the same as zero. The `0.0` guard is `build_circuit`'s,
    for the same reason: negating `0.0` yields `-0.0`, which compares equal to
    `0.0` and formats as `"-0.0"`. A wire `-0.0` is normalized on the
    pass-through path too.
    """
    if raw_power_w is None:
        return None
    if raw_power_w == 0.0:
        return 0.0
    return -raw_power_w if charge_positive_on_wire else raw_power_w


def build_battery(
    bess: DiscoveredDevice | None,
    owners: list[DiscoveredDevice],
    *,
    feeds: Mapping[str, str] | None = None,
    feed_statuses: Mapping[str, str] | None = None,
    firmware_version: str | None = None,
    power_flow_battery: float | None = None,
) -> SpanBatterySnapshot:
    """Build the battery snapshot. A tree with no battery yields the empty one, marked absent.

    `firmware_version` is the enclosure's ``info/firmware-version`` and
    `power_flow_battery` its ``power-flows/battery``; together they pick the
    BESS meter's wire frame. Omitted, they leave the old frame assumed.

    The link status is the owner's ``fed-by-*`` record where a device claims
    the battery that way, as the upstream lugs do for a battery ahead of them.
    Otherwise it is the record of the circuit that feeds the battery, from
    `feeds` and `feed_statuses` (``feed_circuit_ids`` and
    ``feed_connection_statuses``), as it is for an inverter or a charger.
    """
    if bess is None:
        return SpanBatterySnapshot(present=False)

    feed_circuit_id = (feeds or {}).get(bess.device_id)
    status = connection_status_for(bess.device_id, owners)
    if status is None:
        status = (feed_statuses or {}).get(bess.device_id)
    raw_power_w = number(bess, NODE_METER, PROP_ACTIVE_POWER)
    charge_positive_on_wire = _bess_meter_is_charge_positive(firmware_version, raw_power_w, power_flow_battery)

    return SpanBatterySnapshot(
        # Historically misnamed in the snapshot and kept that way: `soe_percentage`
        # holds the percentage (`soc/soc`) and `soe_kwh` the energy (`soc/soe`).
        # Renaming would break dashboards for a cosmetic gain.
        soe_percentage=number(bess, NODE_SOC, PROP_SOC),
        soe_kwh=number(bess, NODE_SOC, PROP_SOE),
        vendor_name=_optional(text(bess, NODE_INFO, PROP_VENDOR_NAME)),
        # No crossover any more. The snapshot speaks v1.0's vocabulary, so
        # `info/model` is the designation and `info/part-number` is the SKU, on every
        # device class. `schema_0` translates flat's irregular naming into this shape.
        model=_optional(text(bess, NODE_INFO, PROP_MODEL)),
        part_number=_optional(text(bess, NODE_INFO, PROP_PART_NUMBER)),
        serial_number=_optional(text(bess, NODE_INFO, PROP_SERIAL_NUMBER)),
        software_version=_optional(text(bess, NODE_INFO, PROP_FIRMWARE_VERSION)),
        nameplate_capacity_kwh=number(bess, NODE_INFO, PROP_NAMEPLATE_CAPACITY),
        # None when unclaimed, so "nobody has said" stays distinct from "not OK".
        connected=_connected(status),
        power_w=_discharge_positive(raw_power_w, charge_positive_on_wire=charge_positive_on_wire),
        # The BESS's own link health, kept as the published enum string rather
        # than collapsed to a bool: DEGRADED is neither OK nor LOST, and a bool
        # would have to pick one.
        communication_state=_optional(text(bess, NODE_STATUS, PROP_COMMUNICATION_STATE)),
        present=True,
        feed_circuit_id=feed_circuit_id,
    )


def pv_inverter_key(pv: DiscoveredDevice, feeds: Mapping[str, str]) -> str:
    """The inverter's `pv_inverters` key: its feeding circuit's id, else its device id.

    The circuit first because its id survives firmware r202639, which renumbers
    every PV device on a panel with more than one inverter. A circuit feeds at
    most one device, so two circuit-fed inverters cannot share a key. An
    inverter no circuit feeds keeps its device id, the only handle it has.
    """
    return feeds.get(pv.device_id) or pv.device_id


def build_pv(
    pv: DiscoveredDevice | None,
    feeds: dict[str, str],
    upstream_lugs: DiscoveredDevice | None = None,
    downstream_lugs: DiscoveredDevice | None = None,
    *,
    feed_statuses: dict[str, str],
) -> SpanPVSnapshot:
    """Build one inverter's PV snapshot. An uncommissioned panel yields the empty one.

    `info/nominal-power` is the array's DC size as recorded at installation,
    carried for display and never a limit on what the inverter produces.
    """
    if pv is None:
        return SpanPVSnapshot()

    return SpanPVSnapshot(
        connected=_connected(feed_statuses.get(pv.device_id)),
        vendor_name=_optional(text(pv, NODE_INFO, PROP_VENDOR_NAME)),
        model=_optional(text(pv, NODE_INFO, PROP_MODEL)),
        serial_number=_optional(text(pv, NODE_INFO, PROP_SERIAL_NUMBER)),
        software_version=_optional(text(pv, NODE_INFO, PROP_FIRMWARE_VERSION)),
        nameplate_capacity_w=number(pv, NODE_INFO, PROP_NOMINAL_POWER),
        feed_circuit_id=feeds.get(pv.device_id),
        device_id=pv.device_id,
        node_id=pv_inverter_key(pv, feeds),
        # Retired as a property in v1.0 and derived instead, per the enclosure model's
        # own replacement rule. `None` where no owner references the DER, because the
        # integration gates whether a control entity exists on this value.
        relative_position=resolve_relative_position(pv.device_id, feeds, upstream_lugs, downstream_lugs),
    )


def _declared_format(device: DiscoveredDevice, node: str, prop: str) -> tuple[str, ...] | None:
    """The members an enum property's declaration lists in `$format`, or None where it lists none."""
    declaration = node_properties(device, node).get(prop)
    listed = None if declaration is None else declaration.get("format")
    if not isinstance(listed, str) or not listed:
        return None
    return tuple(member.strip() for member in listed.split(",") if member.strip())


def build_evse(
    evse: DiscoveredDevice, feeds: dict[str, str], *, node_id: str, feed_statuses: dict[str, str]
) -> SpanEvseSnapshot:
    """Build one EVSE snapshot.

    `node_id` is supplied rather than taken from `evse.device_id`: it feeds the
    integration's device-registry `identifiers`, so it has to be the harmonised
    key, not the v1.0 device id. See `_harmonised_evse_keys`.

    Both lookups are keyed on `evse.device_id`, the v1.0 id, and not on
    `node_id`: a connection record names the device the way the tree does, and a
    panel with two chargers has two records to tell apart. Keying the status on
    the harmonised serial would find nothing on every panel and, worse, would
    find the *wrong* charger the moment two of them harmonised alike.

    The charge-current pair is read through `resolve_charge_limit` rather than
    from named constants, because which node and properties carry it is a
    question only this charger's `$description` can answer. See
    `span_panel_api_schema_1.charge_limit`.
    """
    limit = resolve_charge_limit(evse)
    return SpanEvseSnapshot(
        node_id=node_id,
        feed_circuit_id=feeds.get(evse.device_id, ""),
        connected=_connected(feed_statuses.get(evse.device_id)),
        status=text(evse, NODE_STATUS, PROP_STATUS, UNKNOWN),
        lock_state=text(evse, NODE_SWITCH, PROP_LOCK_STATE, UNKNOWN),
        lock_control=(
            control_target(evse.device_id, NODE_SWITCH, PROP_LOCK_STATE)
            if declared_settable(node_properties(evse, NODE_SWITCH).get(PROP_LOCK_STATE))
            else None
        ),
        lock_state_options=_declared_format(evse, NODE_SWITCH, PROP_LOCK_STATE),
        advertised_current_a=number(evse, NODE_METER, PROP_ADVERTISED_CURRENT),
        vendor_name=_optional(text(evse, NODE_INFO, PROP_VENDOR_NAME)),
        model=_optional(text(evse, NODE_INFO, PROP_MODEL)),
        part_number=_optional(text(evse, NODE_INFO, PROP_PART_NUMBER)),
        serial_number=_optional(text(evse, NODE_INFO, PROP_SERIAL_NUMBER)),
        software_version=_optional(text(evse, NODE_INFO, PROP_FIRMWARE_VERSION)),
        charge_current_limit_a=_limit_value(evse, limit, limit.limit) if limit else None,
        charge_current_ceiling_a=_limit_value(evse, limit, limit.ceiling) if limit else None,
        charge_current_limit_target_a=_limit_target(evse, limit),
        charge_current_limit_settable=limit is not None and limit.limit is not None and limit.limit.settable,
    )


def _limit_value(evse: DiscoveredDevice, surface: ChargeLimitSurface, declaration: ChargeLimitProperty | None) -> int | None:
    """One half of the resolved charge-limit pair, or None where it is not declared."""
    if declaration is None:
        return None
    return integer(evse, surface.node, declaration.property_id)


def _limit_target(evse: DiscoveredDevice, surface: ChargeLimitSurface | None) -> int | None:
    """The pending write the charger is echoing on `$target`, if any.

    Parsed to `int` rather than passed through as the string the circuit targets
    carry, because this one is compared against a number: a consumer showing
    "pending 24 A" beside a reading of 32 has to know both are amps. A `$target`
    that is not a number is not a pending amperage, so it reads as no pending
    command rather than as a value the caller has to re-parse.
    """
    if surface is None or surface.limit is None:
        return None
    raw = evse.get_property_target(surface.node, surface.limit.property_id)
    if raw is None or raw == "":
        return None
    try:
        return int(float(raw))
    except TypeError, ValueError:
        return None


PROP_ISLANDING_STATE = "islanding-state"
PROP_GRID_STATE = "grid-state"
PROP_GRID_FORMING_ENTITY = "grid-forming-entity"


def build_mid(mid: DiscoveredDevice | None, device_names: Mapping[str, str]) -> SpanMidSnapshot | None:
    """Build the MID snapshot, or `None` when the panel publishes no MID.

    `None` is the presence signal, so there is nothing for a consumer to infer from a
    sentinel field. Every value is optional except identity: the enclosure model makes
    `islanding-state` MUST on a MID, but a device mid-discovery has a description and
    no values yet, and reporting that as `ON_GRID` would be worse than reporting it as
    unknown.

    Identity follows `SpanEvseSnapshot`: the serial where published, the Homie device
    id otherwise. Here the device id is `<bess-id>-mid`, so it inherits the BESS's
    proxied form and the instability `devices/proxy.md` warns about — the serial is the
    part that survives a proxy-to-native transition.
    """
    if mid is None:
        return None
    serial = _optional(text(mid, NODE_INFO, PROP_SERIAL_NUMBER))
    return SpanMidSnapshot(
        node_id=serial or mid.device_id,
        serial_number=serial,
        vendor_name=_optional(text(mid, NODE_INFO, PROP_VENDOR_NAME)),
        model=_optional(text(mid, NODE_INFO, PROP_MODEL)),
        software_version=_optional(text(mid, NODE_INFO, PROP_FIRMWARE_VERSION)),
        hardware_version=_optional(text(mid, NODE_INFO, PROP_HARDWARE_VERSION)),
        islanding_state=_optional(text(mid, NODE_GRID, PROP_ISLANDING_STATE)),
        grid_state=_optional(text(mid, NODE_GRID, PROP_GRID_STATE)),
        grid_forming_entity=_optional(text(mid, NODE_GRID, PROP_GRID_FORMING_ENTITY)),
        grid_forming_device_name=resolve_grid_forming_device_name(mid, device_names),
    )


POSITION_IN_PANEL = "IN_PANEL"
POSITION_UPSTREAM = "UPSTREAM"


def resolve_relative_position(
    device_id: str,
    feeds: dict[str, str],
    upstream_lugs: DiscoveredDevice | None,
    downstream_lugs: DiscoveredDevice | None,
) -> str | None:
    """Where a DER sits relative to the enclosure, from the connection records.

    v1.0 removed `relative-position` as a property deliberately, and the enclosure model
    says what replaces it: "The position of a DER relative to the enclosure is derivable
    from which enclosure-side connection-owner references the DER."

    | owner referencing the DER | position |
    | --- | --- |
    | a circuit's `connection/feeds-device-id` | `IN_PANEL` |
    | the downstream lugs' `connection/feeds-device-id` | `IN_PANEL`, via feedthrough |
    | the upstream lugs' `connection/fed-by-device-id` | `UPSTREAM` |
    | nothing | `None` — not commissioned to this enclosure, or not yet announced |

    Verified against the paired captures rather than reasoned: flat publishes
    `pv/relative-position = IN_PANEL` and `bess/relative-position = UPSTREAM`, and this
    derives exactly those from a circuit feeding the PV and the upstream lugs being fed
    by the BESS.

    `None`, not a guess. The integration gates whether a *control entity exists at all*
    on this value, so inventing one creates or removes a control. The guide is explicit
    that where no owner references the DER, position is not derivable.

    The feedthrough branch is unreachable against every producer available today: no lugs
    device can publish `connection/feeds-*` at all, which is
    electrification-bus/distribution-enclosure-simulator#30. It is written because the
    rule has three cases and omitting one would read as a claim that it cannot happen.
    """
    if device_id in feeds:
        return POSITION_IN_PANEL
    if downstream_lugs is not None and text(downstream_lugs, NODE_CONNECTION, PROP_FEEDS_DEVICE_ID) == device_id:
        return POSITION_IN_PANEL
    if upstream_lugs is not None and text(upstream_lugs, NODE_CONNECTION, PROP_FED_BY_DEVICE_ID) == device_id:
        return POSITION_UPSTREAM
    return None
