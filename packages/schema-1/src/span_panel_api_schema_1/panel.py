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

from span_panel_api.models import SpanCircuitSnapshot
from span_panel_api_schema_1.const import (
    CLOUD_CONNECTED,
    NODE_BREAKER,
    NODE_DOOR,
    NODE_GRID,
    NODE_INFO,
    NODE_METER,
    NODE_POWER_FLOWS,
    NODE_STATUS,
    PANEL_SIZE_BY_MODEL,
    PROP_ACTIVE_POWER,
    PROP_CLOUD_CONNECTION,
    PROP_ETHERNET,
    PROP_EXPORTED_ENERGY,
    PROP_FIRMWARE_VERSION,
    PROP_IMPORTED_ENERGY,
    PROP_MODEL,
    PROP_RATING,
    PROP_RELAY,
    PROP_SERIAL_NUMBER,
    PROP_STATE,
    PROP_VOLTAGE_A,
    PROP_VOLTAGE_B,
    PROP_WIFI,
    UNKNOWN,
    UNMAPPED_TAB_PREFIX,
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
# The MID's islanding answer, and the true successor of the flat schema's
# `bess/grid-state`: same ON_GRID/OFF_GRID vocabulary. Kept next to
# PROP_GRID_STATE deliberately, because the two are easy to confuse and only
# one of them is what an existing consumer means by "grid state".
PROP_ISLANDING_STATE = "islanding-state"
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


def panel_size_from_model(model: str) -> int:
    """Total breaker spaces for a panel model, or 0 when the model is unknown.

    `info/model` is the only place v1.0 states the panel's size. The flat schema
    carried it in the Homie schema's `space` format (`"1:32:1"`, max = 32); the
    successor `info/spaces` is a plain string with no format, and the panel
    device publishes no size property.

    The highest *occupied* space is not a substitute: it is a lower bound, so a
    40-space panel whose highest occupied slot is 36 would report 36 and every
    position above it would silently cease to exist. Since unoccupied positions
    are exactly `total - occupied`, that would delete the integration's
    unmapped-circuit sensors rather than merely miscount a display value.

    Unknown models return 0 and log, because inventing a size is worse than
    reporting none: a wrong total fabricates unmapped positions that are not
    there, or hides real ones.
    """
    size = PANEL_SIZE_BY_MODEL.get(model.strip().upper())
    if size is None:
        if model:
            _LOGGER.warning(
                "Unknown panel model %r; panel size unavailable and unmapped positions cannot be derived. Known models: %s",
                model,
                ", ".join(sorted(PANEL_SIZE_BY_MODEL)),
            )
        return 0
    return size


def panel_model_drift(panel: DiscoveredDevice) -> tuple[str, ...]:
    """Models the panel says are valid that we have no size for.

    The panel advertises the model enum as a Homie ``$format`` on
    ``info/model``, but nothing in the schema or the SDK states how many spaces
    each model has — that half is ours. So the panel can legitimately announce
    a model we cannot size, and this is how we find out at connect time rather
    than through a user reporting missing positions.

    Same reasoning as the flat adapter's schema-drift detection: the failure is
    a silent absence, so it needs a signal that does not depend on anyone
    noticing an absence.
    """
    definition = panel.get_node_properties(NODE_INFO).get(PROP_MODEL)
    if not isinstance(definition, dict):
        return ()
    advertised = str(definition.get("format", ""))
    if not advertised:
        return ()
    unknown = [
        value.strip()
        for value in advertised.split(",")
        if value.strip() and value.strip().upper() not in PANEL_SIZE_BY_MODEL
    ]
    if unknown:
        _LOGGER.warning(
            "Panel advertises model(s) %s that this adapter cannot size; "
            "unmapped positions would be wrong for such a panel. Known: %s",
            ", ".join(unknown),
            ", ".join(sorted(PANEL_SIZE_BY_MODEL)),
        )
    return tuple(unknown)


def build_unmapped_tabs(panel_size: int, occupied: set[int]) -> dict[str, SpanCircuitSnapshot]:
    """Synthesise a zero-power entry for every unoccupied breaker position.

    The integration surfaces these as unmapped-circuit sensors, gated by its
    own `enable_unmapped_circuit_sensors` option, and builds entity ids from
    the circuit id — so the `unmapped_tab_<n>` naming is a compatibility
    contract with entities that already exist, not an internal detail.

    Reproducible under v1.0 only because the model gives a true total: the tree
    itself lists occupied positions and says nothing about the rest. A panel
    whose model is unrecognised yields nothing rather than a guess.
    """
    unmapped: dict[str, SpanCircuitSnapshot] = {}
    for tab in range(1, panel_size + 1):
        if tab in occupied:
            continue
        circuit_id = f"{UNMAPPED_TAB_PREFIX}{tab}"
        unmapped[circuit_id] = SpanCircuitSnapshot(
            circuit_id=circuit_id,
            name=f"Unmapped Tab {tab}",
            relay_state="CLOSED",
            instant_power_w=0.0,
            produced_energy_wh=0.0,
            consumed_energy_wh=0.0,
            tabs=[tab],
            priority=UNKNOWN,
            is_user_controllable=False,
            is_sheddable=False,
            is_never_backup=False,
        )
    return unmapped


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
        #
        # **From `islanding-state`, not from `grid-state`.** The MID publishes
        # both, and the names invite exactly the wrong choice: the flat schema's
        # `grid_state` was the BESS's `grid-state`, an ON_GRID/OFF_GRID
        # islanding answer, and its v1.0 successor is `grid/islanding-state`
        # with that same value set. The MID's own `grid/grid-state` is a
        # different question — whether the utility supply is UP, DOWN or
        # DEGRADED — and is new in v1.0 with no flat equivalent. Matching on
        # the property name puts UP where consumers expect ON_GRID: the entity
        # keeps its id and its history, and every template comparing it simply
        # stops being true.
        self.grid_state = text(mid, NODE_GRID, PROP_ISLANDING_STATE) or None

        # Retired in v1.0 with no drop-in successor, and deliberately left
        # None rather than substituted: `dominant-power-source` split into
        # grid-forming-entity plus asserted-islanding-state, and
        # `grid-islandable` was removed outright. Both are product decisions,
        # tracked in the entity and config deltas write-up.
        self.dominant_power_source: str | None = None
        self.grid_islandable: bool | None = None
        # Not published by v1.0 firmware.
        self.wifi_ssid: str | None = None
