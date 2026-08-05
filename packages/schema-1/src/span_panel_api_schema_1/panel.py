"""Map the v1.0 device tree onto the panel-level fields of ``SpanPanelSnapshot``.

Where the flat schema kept everything on one device's nodes, v1.0 spreads the
same information across the panel and its children: the grid connection is the
upstream lugs device, feedthrough is the downstream lugs device, and grid state
lives on the MID.

**Direction is per-device, and the two rules are opposites.** Everything is
stated in the enclosure's reference frame — power flowing *into* the panel is
positive — so:

* **Circuits** need flipping (see ``circuits.py``): the panel exports to a load,
  so a load reads negative and accumulates ``exported-energy``.
* **Lugs** do not: the panel imports from the grid, so drawing from the grid
  reads positive and accumulates ``imported-energy``, which is already the
  house's consumption.

Reading the lugs with the circuit rule would inverting every grid figure while
leaving it plausible, which is why the two are separated here rather than
sharing a helper.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from span_panel_api_schema_1.const import (
    CLOUD_CONNECTED,
    NODE_BREAKER,
    NODE_DOOR,
    NODE_GRID,
    NODE_INFO,
    NODE_METER,
    NODE_POWER_FLOWS,
    NODE_STATUS,
    PROP_ACTIVE_POWER,
    PROP_CLOUD_CONNECTION,
    PROP_ETHERNET,
    PROP_EXPORTED_ENERGY,
    PROP_FIRMWARE_VERSION,
    PROP_IMPORTED_ENERGY,
    PROP_RATING,
    PROP_RELAY,
    PROP_SERIAL_NUMBER,
    PROP_STATE,
    PROP_VOLTAGE_A,
    PROP_VOLTAGE_B,
    PROP_WIFI,
    UNKNOWN,
)

if TYPE_CHECKING:
    from ebus_sdk.homie import DiscoveredDevice

_LOGGER = logging.getLogger(__name__)

# Lugs `meter` exposes per-phase current under these ids; circuits expose a
# single `current`. Same capability type, different property set — v1.0 defines
# capabilities as a semantic namespace rather than a fixed contract.
PROP_CURRENT_A = "current-a"
PROP_CURRENT_B = "current-b"

PROP_GRID_STATE = "grid-state"
PROP_DIRECTION = "direction"
DIRECTION_UPSTREAM = "UPSTREAM"


def text(device: DiscoveredDevice | None, node: str, prop: str, default: str = "") -> str:
    if device is None:
        return default
    value = device.get_property(node, prop)
    return default if value is None else str(value)


def number(device: DiscoveredDevice | None, node: str, prop: str) -> float | None:
    if device is None:
        return None
    raw = device.get_property(node, prop)
    if raw is None or raw == "":
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def flag(device: DiscoveredDevice | None, node: str, prop: str) -> bool:
    return text(device, node, prop).strip().lower() == "true"


def panel_size_from_tabs(occupied: list[int]) -> int:
    """Best-effort panel size: the highest occupied breaker space.

    **This is a lower bound, not the panel's size**, and v1.0 gives us nothing
    better. The flat schema carried the true size in the Homie schema's `space`
    format (`"1:32:1"`, max = 32). Its v1.0 successor, `info/spaces`, is a plain
    string with no format, the panel device publishes no size property, and the
    migration guide maps `space` to `spaces` without mentioning panel size at
    all.

    So a 40-space panel whose highest occupied slot is 36 reports 36. Anything
    that enumerates *unoccupied* slots — the flat parser's unmapped-tab
    synthesis — is therefore not reproducible from the wire under v1.0.

    Treated as a product question rather than papered over: see the v1.0
    user-visible entity and config deltas write-up. Deriving it from
    `info/model` (`"MAIN_40"`) would work on today's firmware and is exactly the
    kind of undocumented vendor parsing that breaks silently later.
    """
    if not occupied:
        return 0
    return max(occupied)


def find_lugs(devices: list[DiscoveredDevice], upstream: bool) -> DiscoveredDevice | None:
    """Locate a lugs device by its declared direction.

    Matched on `info/direction` rather than device id: the ids in the reference
    tree (`lugs-upstream`) are the simulator's naming, while the direction
    property is what the schema defines.
    """
    want = DIRECTION_UPSTREAM
    for device in devices:
        direction = text(device, NODE_INFO, PROP_DIRECTION).strip().upper()
        if not direction:
            continue
        if (direction == want) is upstream:
            return device
    return None


class PanelFields:
    """Panel-level values gathered from the tree, ready for the snapshot.

    A class rather than a long argument list because the caller assembles a
    frozen dataclass with ~30 fields, and passing them positionally is how a
    voltage ends up in a current.
    """

    def __init__(
        self,
        panel: DiscoveredDevice,
        upstream_lugs: DiscoveredDevice | None,
        downstream_lugs: DiscoveredDevice | None,
        mid: DiscoveredDevice | None,
    ) -> None:
        self.serial_number = text(panel, NODE_INFO, PROP_SERIAL_NUMBER, panel.device_id)
        self.firmware_version = text(panel, NODE_INFO, PROP_FIRMWARE_VERSION)
        self.main_relay_state = text(panel, NODE_STATUS, PROP_RELAY, UNKNOWN)
        self.door_state = text(panel, NODE_DOOR, PROP_STATE, UNKNOWN)

        self.eth0_link = flag(panel, NODE_STATUS, PROP_ETHERNET)
        self.wlan_link = flag(panel, NODE_STATUS, PROP_WIFI)
        self.vendor_cloud = text(panel, NODE_STATUS, PROP_CLOUD_CONNECTION) or None
        # v1 exposed a WWAN radio link; v2 has no such property, so the flat
        # adapter reported cloud reachability instead. Kept identical here so
        # the entity does not change meaning between adapters.
        self.wwan_link = self.vendor_cloud == CLOUD_CONNECTED

        self.l1_voltage = number(panel, NODE_METER, PROP_VOLTAGE_A)
        self.l2_voltage = number(panel, NODE_METER, PROP_VOLTAGE_B)
        rating = number(panel, NODE_BREAKER, PROP_RATING)
        self.main_breaker_rating_a = None if rating is None else int(rating)

        self.power_flow_pv = number(panel, NODE_POWER_FLOWS, "pv")
        self.power_flow_battery = number(panel, NODE_POWER_FLOWS, "battery")
        self.power_flow_grid = number(panel, NODE_POWER_FLOWS, "grid")
        self.power_flow_site = number(panel, NODE_POWER_FLOWS, "site")

        # Upstream lugs are the grid connection. No sign flip: the enclosure
        # frame already reports import-positive, which is what consumption
        # means here.
        self.instant_grid_power_w = number(upstream_lugs, NODE_METER, PROP_ACTIVE_POWER) or 0.0
        self.main_meter_energy_consumed_wh = number(upstream_lugs, NODE_METER, PROP_IMPORTED_ENERGY) or 0.0
        self.main_meter_energy_produced_wh = number(upstream_lugs, NODE_METER, PROP_EXPORTED_ENERGY) or 0.0
        self.upstream_l1_current_a = number(upstream_lugs, NODE_METER, PROP_CURRENT_A)
        self.upstream_l2_current_a = number(upstream_lugs, NODE_METER, PROP_CURRENT_B)

        self.feedthrough_power_w = number(downstream_lugs, NODE_METER, PROP_ACTIVE_POWER) or 0.0
        self.feedthrough_energy_consumed_wh = number(downstream_lugs, NODE_METER, PROP_IMPORTED_ENERGY) or 0.0
        self.feedthrough_energy_produced_wh = number(downstream_lugs, NODE_METER, PROP_EXPORTED_ENERGY) or 0.0
        self.downstream_l1_current_a = number(downstream_lugs, NODE_METER, PROP_CURRENT_A)
        self.downstream_l2_current_a = number(downstream_lugs, NODE_METER, PROP_CURRENT_B)

        # Grid state moved to the MID device, which is where islanding is
        # actually decided. Absent when the panel has no MID.
        self.grid_state = text(mid, NODE_GRID, PROP_GRID_STATE) or None

        # Retired in v1.0 with no drop-in successor, and deliberately left
        # None rather than substituted: `dominant-power-source` split into
        # grid-forming-entity plus asserted-islanding-state, and
        # `grid-islandable` was removed outright. Both are product decisions,
        # tracked in the entity and config deltas write-up.
        self.dominant_power_source: str | None = None
        self.grid_islandable: bool | None = None
        # Not published by v1.0 firmware.
        self.wifi_ssid: str | None = None
