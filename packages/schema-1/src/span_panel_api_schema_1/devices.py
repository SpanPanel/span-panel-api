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
against conflating them.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from span_panel_api.models import SpanBatterySnapshot, SpanEvseSnapshot, SpanMidSnapshot, SpanPVSnapshot
from span_panel_api_schema_1.const import (
    NODE_CONNECTION,
    NODE_GRID,
    NODE_INFO,
    NODE_METER,
    NODE_SOC,
    NODE_STATUS,
    NODE_SWITCH,
    UNKNOWN,
)
from span_panel_api_schema_1.panel import number, resolve_grid_forming_device_name, text

if TYPE_CHECKING:
    from collections.abc import Mapping

    from ebus_sdk.homie import DiscoveredDevice

PROP_FIRMWARE_VERSION = "firmware-version"
PROP_HARDWARE_VERSION = "hardware-version"
PROP_MODEL = "model"
PROP_NAMEPLATE_CAPACITY = "nameplate-capacity"
PROP_NOMINAL_POWER = "nominal-power"
PROP_PART_NUMBER = "part-number"
PROP_SERIAL_NUMBER = "serial-number"
PROP_VENDOR_NAME = "vendor-name"

PROP_SOC = "soc"
PROP_SOE = "soe"

PROP_ADVERTISED_CURRENT = "advertised-current"
PROP_LOCK_STATE = "lock-state"
PROP_STATUS = "status"

PROP_FEEDS_DEVICE_ID = "feeds-device-id"
PROP_FED_BY_DEVICE_ID = "fed-by-device-id"
PROP_FED_BY_DEVICE_STATUS = "fed-by-device-status"

STATUS_OK = "OK"


def _optional(value: str) -> str | None:
    """Empty means the panel did not publish it, which is not the same as ''."""
    return value or None


def feed_circuit_ids(circuits: list[DiscoveredDevice]) -> dict[str, str]:
    """Map each fed device id to the circuit that feeds it.

    v1.0 states the relationship on the *circuit* (``connection/feeds-device-id``)
    rather than on the DER, so it is read once here and handed to whichever
    device needs it.
    """
    feeds: dict[str, str] = {}
    for circuit in circuits:
        fed = text(circuit, NODE_CONNECTION, PROP_FEEDS_DEVICE_ID)
        if fed:
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


def build_battery(bess: DiscoveredDevice | None, owners: list[DiscoveredDevice]) -> SpanBatterySnapshot:
    """Build the battery snapshot. An uncommissioned panel yields the empty one."""
    if bess is None:
        return SpanBatterySnapshot()

    status = connection_status_for(bess.device_id, owners)

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
        connected=None if status is None else status == STATUS_OK,
    )


def build_pv(
    pv: DiscoveredDevice | None,
    feeds: dict[str, str],
    upstream_lugs: DiscoveredDevice | None = None,
    downstream_lugs: DiscoveredDevice | None = None,
) -> SpanPVSnapshot:
    """Build the PV snapshot. An uncommissioned panel yields the empty one."""
    if pv is None:
        return SpanPVSnapshot()

    return SpanPVSnapshot(
        vendor_name=_optional(text(pv, NODE_INFO, PROP_VENDOR_NAME)),
        model=_optional(text(pv, NODE_INFO, PROP_MODEL)),
        software_version=_optional(text(pv, NODE_INFO, PROP_FIRMWARE_VERSION)),
        nameplate_capacity_w=number(pv, NODE_INFO, PROP_NOMINAL_POWER),
        feed_circuit_id=feeds.get(pv.device_id),
        # Retired as a property in v1.0 and derived instead, per the enclosure model's
        # own replacement rule. `None` where no owner references the DER, because the
        # integration gates whether a control entity exists on this value.
        relative_position=resolve_relative_position(pv.device_id, feeds, upstream_lugs, downstream_lugs),
    )


def build_evse(evse: DiscoveredDevice, feeds: dict[str, str], *, node_id: str) -> SpanEvseSnapshot:
    """Build one EVSE snapshot.

    `node_id` is supplied rather than taken from `evse.device_id`: it feeds the
    integration's device-registry `identifiers`, so it has to be the harmonised
    key, not the v1.0 device id. See `_harmonised_evse_keys`.
    """
    return SpanEvseSnapshot(
        node_id=node_id,
        feed_circuit_id=feeds.get(evse.device_id, ""),
        status=text(evse, NODE_STATUS, PROP_STATUS, UNKNOWN),
        lock_state=text(evse, NODE_SWITCH, PROP_LOCK_STATE, UNKNOWN),
        advertised_current_a=number(evse, NODE_METER, PROP_ADVERTISED_CURRENT),
        vendor_name=_optional(text(evse, NODE_INFO, PROP_VENDOR_NAME)),
        model=_optional(text(evse, NODE_INFO, PROP_MODEL)),
        part_number=_optional(text(evse, NODE_INFO, PROP_PART_NUMBER)),
        serial_number=_optional(text(evse, NODE_INFO, PROP_SERIAL_NUMBER)),
        software_version=_optional(text(evse, NODE_INFO, PROP_FIRMWARE_VERSION)),
    )


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
