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

from span_panel_api.models import SpanBatterySnapshot, SpanEvseSnapshot, SpanPVSnapshot
from span_panel_api_schema_1.const import NODE_CONNECTION, NODE_INFO, NODE_METER, NODE_SOC, NODE_STATUS, NODE_SWITCH, UNKNOWN
from span_panel_api_schema_1.panel import number, text

if TYPE_CHECKING:
    from ebus_sdk.homie import DiscoveredDevice

PROP_FIRMWARE_VERSION = "firmware-version"
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
        # The swap: designation to product_name, SKU to model.
        product_name=_optional(text(bess, NODE_INFO, PROP_MODEL)),
        model=_optional(text(bess, NODE_INFO, PROP_PART_NUMBER)),
        serial_number=_optional(text(bess, NODE_INFO, PROP_SERIAL_NUMBER)),
        software_version=_optional(text(bess, NODE_INFO, PROP_FIRMWARE_VERSION)),
        nameplate_capacity_kwh=number(bess, NODE_INFO, PROP_NAMEPLATE_CAPACITY),
        # None when unclaimed, so "nobody has said" stays distinct from "not OK".
        connected=None if status is None else status == STATUS_OK,
    )


def build_pv(pv: DiscoveredDevice | None, feeds: dict[str, str]) -> SpanPVSnapshot:
    """Build the PV snapshot. An uncommissioned panel yields the empty one."""
    if pv is None:
        return SpanPVSnapshot()

    return SpanPVSnapshot(
        vendor_name=_optional(text(pv, NODE_INFO, PROP_VENDOR_NAME)),
        product_name=_optional(text(pv, NODE_INFO, PROP_MODEL)),
        nameplate_capacity_w=number(pv, NODE_INFO, PROP_NOMINAL_POWER),
        feed_circuit_id=feeds.get(pv.device_id),
        # `relative-position` is retired in v1.0 and the guide is explicit that
        # it is only "derivable from connection records (when present)". Left
        # None rather than guessed: the integration gates control entities on
        # it, so a wrong value creates or removes a control.
        relative_position=None,
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
        product_name=_optional(text(evse, NODE_INFO, PROP_MODEL)),
        part_number=_optional(text(evse, NODE_INFO, PROP_PART_NUMBER)),
        serial_number=_optional(text(evse, NODE_INFO, PROP_SERIAL_NUMBER)),
        software_version=_optional(text(evse, NODE_INFO, PROP_FIRMWARE_VERSION)),
    )
