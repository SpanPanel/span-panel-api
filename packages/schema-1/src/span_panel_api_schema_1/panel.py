"""Map the v1.0 device tree onto the panel-level fields of ``SpanPanelSnapshot``.

Where the flat schema kept everything on one device's nodes, v1.0 spreads the
same information across the panel and its children: the service connection is the
upstream lugs device, feedthrough is the downstream lugs device, and grid state
lives on the MID.

**The upstream lugs are not always the utility connection point.** A BESS wired
ahead of the main lugs, or an enclosure fed by another enclosure, sits between
the utility and this meter, so the lugs read panel-side flow while the grid
differs by whatever that device contributes or absorbs. `power-flows` 0.3
qualified its own negation table to say so and named the detection mechanism, and
`lugs_at_service_entrance` carries the answer to the snapshot -- without it a
consumer sees `instant_grid_power_w` and `power_flow_grid` disagree and cannot
tell a topology from a fault.

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

import json
import logging
from typing import TYPE_CHECKING, NamedTuple

from span_panel_api.models import SpanCircuitSnapshot, SpanPcsSnapshot
from span_panel_api_schema_1.const import (
    CLOUD_CONNECTED,
    NODE_BREAKER,
    NODE_CONNECTION,
    NODE_DOOR,
    NODE_GRID,
    NODE_GRID_FORMING,
    NODE_INFO,
    NODE_METER,
    NODE_PCS,
    NODE_POWER_FLOWS,
    NODE_SHED,
    NODE_SHED_FORECAST,
    NODE_STATUS,
    PANEL_SIZE_BY_MODEL,
    PCS_ACTIVE_SUFFIX,
    PCS_ENABLEMENT_SUFFIX,
    PCS_LIMIT_SUFFIX,
    PROP_ACTIVE,
    PROP_ACTIVE_POWER,
    PROP_ASSERTED_ISLANDING_STATE,
    PROP_BINDING_CONSTRAINT,
    PROP_CAPABLE,
    PROP_CLOUD_CONNECTION,
    PROP_CONFIDENCE,
    PROP_ENABLED,
    PROP_ETHERNET,
    PROP_EXPORTED_ENERGY,
    PROP_FED_BY_DEVICE_ID,
    PROP_FIRMWARE_VERSION,
    PROP_FULL_CHARGE_TIME_TO_PRIORITY_SHED,
    PROP_FULL_CHARGE_TOTAL_TIME_REMAINING,
    PROP_GRID_FORMING_ENTITY,
    PROP_HARDWARE_VERSION,
    PROP_IMPORT_LIMIT,
    PROP_IMPORTED_ENERGY,
    PROP_MODEL,
    PROP_POLICY,
    PROP_RATING,
    PROP_RELAY,
    PROP_SERIAL_NUMBER,
    PROP_STATE,
    PROP_TIME_TO_PRIORITY_SHED,
    PROP_TOTAL_TIME_REMAINING,
    PROP_VENDOR_NAME,
    PROP_VOLTAGE_A,
    PROP_VOLTAGE_B,
    PROP_WIFI,
    PROP_WIFI_SSID,
    SHED_POLICY_SOC_PRIORITY_V1,
    TYPE_BESS,
    TYPE_PV,
    UNKNOWN,
    UNMAPPED_TAB_PREFIX,
)

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

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


def integer(device: DiscoveredDevice | None, node: str, prop: str) -> int | None:
    """A property the tree declares as `integer`, or `None` when it is not published.

    Separate from `number` rather than casting its result at the call site,
    because the two answer different questions. `number` exists for `float`
    properties and returns `float`; a caller that wanted an `int` would have to
    remember that `int(float(...))` truncates, and a truncating conversion
    written once per call site is one that eventually gets written wrong.

    Parsed through `float` first so a publisher that sends `3037.0` for an
    integer property still resolves — the datatype is a declaration about the
    quantity, and rejecting a decimal point would turn a formatting choice into
    a missing entity. A value that is not a number at all yields `None`, which
    is the same answer as not publishing: neither is a reading.
    """
    raw = number(device, node, prop)
    return None if raw is None else int(raw)


def flag(device: DiscoveredDevice | None, node: str, prop: str) -> bool:
    return text(device, node, prop).strip().lower() == "true"


def optional_flag(device: DiscoveredDevice | None, node: str, prop: str) -> bool | None:
    """`flag`, but distinguishing "published false" from "not published".

    `flag` collapses both to `False`, which is right for link-state properties where a
    missing value means down. It is wrong for a static capability: reporting "cannot form
    a grid" for a device that has not said would turn a gap into a claim.
    """
    raw = text(device, node, prop).strip().lower()
    if raw == "":
        return None
    return raw == "true"


def declares_node(device: DiscoveredDevice | None, node: str) -> bool:
    """Whether a device's `$description` declares a capability node at all.

    The presence question a value cannot answer. A capability whose properties
    are every one of them legally zero — `pcs` is the worked example — cannot be
    detected by reading them, and a consumer that gates entity creation on a
    value would delete a switched-off PCS's entities rather than showing it
    switched off.

    The `$description` is the right place to ask, per the migration guide's rule
    that "the authoritative property set for any capability node is always
    declared in that device's `$description`". A node declared with no
    properties still counts as declared: that is a degraded publisher, which
    `build_field_metadata` reports as `resolved=False`, not absent hardware.
    """
    if device is None:
        return False
    description: dict[str, object] = device.description or {}
    nodes = description.get("nodes")
    return isinstance(nodes, dict) and node in nodes


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


class _ShedPolicy(NamedTuple):
    """What `shed/policy` says, as far as this reader understands it.

    Three fields rather than a parsed document, because a consumer renders three
    values beside the shed state: which algorithm is in force, and the two SoC
    thresholds that make its behaviour predictable.
    """

    algorithm: str | None
    soc_threshold_shed_percent: int | None
    soc_threshold_release_percent: int | None


_NO_SHED_POLICY = _ShedPolicy(None, None, None)


def _shed_policy(raw: str | None) -> _ShedPolicy:
    """Parse `shed/policy`, degrading rather than raising at every step.

    The property is a `json` document whose Homie `$format` is the JSON Schema
    it conforms to, and that schema is versioned in its own `$id`
    (`soc-priority.v1`). Versioning the document rather than the property is the
    publisher's way of saying a different algorithm may arrive, so a reader that
    assumed this one would misreport the day one did.

    Hence the shape here: the algorithm name is taken from whatever parses, and
    the two thresholds only from a document that says it is `soc-priority.v1`.
    An unrecognised algorithm keeps its name and yields no thresholds, and the
    raw string is retained beside this by the caller -- a consumer can still show
    what the panel said, which is strictly more than an exception leaves it.

    Every failure lands on the same answer as "not published", because to a
    consumer they are the same event: there is nothing here it can render.
    """
    if not raw:
        return _NO_SHED_POLICY
    try:
        document = json.loads(raw)
    except ValueError:
        _LOGGER.debug("shed/policy is not JSON, keeping the raw value: %r", raw)
        return _NO_SHED_POLICY
    if not isinstance(document, dict):
        return _NO_SHED_POLICY

    algorithm = document.get("algorithm")
    algorithm = algorithm if isinstance(algorithm, str) and algorithm else None
    if algorithm != SHED_POLICY_SOC_PRIORITY_V1:
        # A named algorithm nothing here knows how to read is still worth
        # naming: it tells a consumer why the thresholds are absent.
        return _ShedPolicy(algorithm, None, None)

    parameters = document.get("parameters")
    if not isinstance(parameters, dict):
        return _ShedPolicy(algorithm, None, None)
    return _ShedPolicy(
        algorithm,
        _percent(parameters.get("soc-threshold-shed")),
        _percent(parameters.get("soc-threshold-release")),
    )


def _percent(value: object) -> int | None:
    """A declared-`integer` SoC threshold, or `None` for anything that is not one.

    `bool` is excluded explicitly: it is an `int` in Python, and a policy
    document carrying `true` would otherwise read as a 1% threshold.
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
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
        # The enclosure's own build identity, for the device card a consumer
        # renders. `None` rather than a default when the panel does not publish
        # one: the consumer owns the fallback text it has always shown, and a
        # default invented here would replace it with a different invention.
        self.vendor_name = text(panel, NODE_INFO, PROP_VENDOR_NAME) or None
        self.model = text(panel, NODE_INFO, PROP_MODEL) or None
        self.hardware_version = text(panel, NODE_INFO, PROP_HARDWARE_VERSION) or None
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

        # Whether the upstream lugs are the utility connection point, which is not
        # a given: a BESS wired ahead of the main lugs, or a panel fed by another
        # panel, puts a device between the utility and this meter. Read from the
        # lugs' own `connection/fed-by-device-id`, the mechanism `power-flows` 0.3
        # names when it qualifies the `grid` row of its negation table. Empty
        # string is the absence -- `text` defaults to it -- and absence is the
        # ordinary case.
        self.lugs_at_service_entrance = not text(upstream_lugs, NODE_CONNECTION, PROP_FED_BY_DEVICE_ID)

        # No sign flip: the enclosure frame already reports import-positive, which
        # is what consumption means here.
        #
        # The name says grid, and that is only true when the lugs are the service
        # entrance. Where they are not, this is the panel's own feed and
        # `power_flow_grid` is the site-level figure; `lugs_at_service_entrance`
        # above is how a consumer tells the two apart. The reading itself is
        # correct in either topology -- it is the label that is conditional.
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

        # `dominant-power-source` split into grid-forming-entity plus
        # asserted-islanding-state — two controls on two devices, not one field
        # moved — so there is no drop-in successor and this stays None until the
        # decided replacement lands.
        self.dominant_power_source: str | None = None
        # `grid_islandable` is answered by `resolve_grid_islandable` over the BESS's
        # inverter children, not from the panel, so it is not a PanelFields concern.
        # Kept as an attribute only so nothing that reads it breaks; the snapshot
        # takes the resolver's answer.
        self.grid_islandable: bool | None = None
        # `status/wifi-ssid`, the same value flat published as `core/wifi-ssid`.
        # Read here rather than left `None` because the integration surfaces it
        # as an attribute today: a v1.0 panel that did not read it lost that
        # attribute on upgrade, silently, while every conformance check agreed
        # nothing was wrong.
        self.wifi_ssid = text(panel, NODE_STATUS, PROP_WIFI_SSID) or None

        # `shed/policy` -- the algorithm the panel sheds by, and its parameters.
        self.shed_policy = text(panel, NODE_SHED, PROP_POLICY) or None
        policy = _shed_policy(self.shed_policy)
        self.shed_policy_algorithm = policy.algorithm
        self.shed_soc_threshold_shed_percent = policy.soc_threshold_shed_percent
        self.shed_soc_threshold_release_percent = policy.soc_threshold_release_percent

        # Backup-planning forecast. Every field stays `None` when the panel
        # publishes no `shed-forecast` node, which is what lets a consumer gate
        # entity creation on presence instead of showing a fabricated zero.
        self.shed_time_to_priority_shed_min = integer(panel, NODE_SHED_FORECAST, PROP_TIME_TO_PRIORITY_SHED)
        self.shed_total_time_remaining_min = integer(panel, NODE_SHED_FORECAST, PROP_TOTAL_TIME_REMAINING)
        self.shed_full_charge_time_to_priority_shed_min = integer(
            panel, NODE_SHED_FORECAST, PROP_FULL_CHARGE_TIME_TO_PRIORITY_SHED
        )
        self.shed_full_charge_total_time_remaining_min = integer(
            panel, NODE_SHED_FORECAST, PROP_FULL_CHARGE_TOTAL_TIME_REMAINING
        )
        self.shed_forecast_confidence = text(panel, NODE_SHED_FORECAST, PROP_CONFIDENCE) or None


class _LimitTriplet(NamedTuple):
    """One constraint class's `{limit, enablement, active}` triplet, as published.

    Named rather than a bare tuple because the three members are a float, a
    string and a boolean read from three sibling properties, and positional
    unpacking at four call sites is how an enablement ends up in an active flag.
    """

    limit_a: float | None
    enablement: str | None
    active: bool | None


def _limit_triplet(panel: DiscoveredDevice, source: str) -> _LimitTriplet:
    """Read one amps-native constraint class off the enclosure's `pcs` node.

    Every source in `PCS_LIMIT_SOURCES` publishes the identical
    `{<source>-import-limit, -enablement, -active}` shape, which the capability
    states as a rule rather than as a coincidence: a vendor "MAY publish further
    amps-native limits using the same triplet". Reading them through one
    function is what makes a fifth source a one-line addition instead of three.

    All three are optional independently. A publisher that reports a limit and
    no enablement is conformant, and reporting `UNCONFIGURED` on its behalf
    would invent a configuration state it never claimed.
    """
    prefix = f"{source}{PCS_LIMIT_SUFFIX}"
    return _LimitTriplet(
        limit_a=number(panel, NODE_PCS, prefix),
        enablement=text(panel, NODE_PCS, f"{prefix}{PCS_ENABLEMENT_SUFFIX}") or None,
        active=optional_flag(panel, NODE_PCS, f"{prefix}{PCS_ACTIVE_SUFFIX}"),
    )


def build_pcs(panel: DiscoveredDevice) -> SpanPcsSnapshot | None:
    """The enclosure's Power Control System, or `None` when it publishes no `pcs` node.

    Gated on the **declaration**, not on any value, because the capability
    defines absence that way: "absence of the `pcs` node means the device does
    not run (or participate in) a Power Control System". Every limit in the
    reference capture is `0.0` with `UNCONFIGURED` enablement — a PCS that
    exists and is switched off — and a value-based gate could not tell that from
    a panel with no PCS at all. One is a capability reporting its state; the
    other is hardware that is not there.

    Every field stays `None` where the node omits the property. The catalog
    marks the system surface `SHOULD` and three of the four constraint classes
    `MAY`, so a partial node is conformant firmware rather than a fault, and a
    limit defaulted to `0.0` would read as "no import permitted" — the most
    alarming reading the property has.

    Enablement and `binding-constraint` are kept as raw wire strings. Both are
    enums the publisher may extend through its Homie `$format`, and
    `binding-constraint` exists precisely to name a source, so normalising it
    onto a set fixed here would discard the extension it was designed to carry.
    """
    if not declares_node(panel, NODE_PCS):
        return None

    feed = _limit_triplet(panel, "feed")
    operator = _limit_triplet(panel, "operator")
    off_grid = _limit_triplet(panel, "off-grid")
    requested = _limit_triplet(panel, "requested")

    return SpanPcsSnapshot(
        enabled=optional_flag(panel, NODE_PCS, PROP_ENABLED),
        active=optional_flag(panel, NODE_PCS, PROP_ACTIVE),
        import_limit_a=number(panel, NODE_PCS, PROP_IMPORT_LIMIT),
        binding_constraint=text(panel, NODE_PCS, PROP_BINDING_CONSTRAINT) or None,
        feed_import_limit_a=feed.limit_a,
        feed_import_limit_enablement=feed.enablement,
        feed_import_limit_active=feed.active,
        operator_import_limit_a=operator.limit_a,
        operator_import_limit_enablement=operator.enablement,
        operator_import_limit_active=operator.active,
        off_grid_import_limit_a=off_grid.limit_a,
        off_grid_import_limit_enablement=off_grid.enablement,
        off_grid_import_limit_active=off_grid.active,
        requested_import_limit_a=requested.limit_a,
        requested_import_limit_enablement=requested.enablement,
        requested_import_limit_active=requested.active,
    )


# Matches `schema_0`'s epsilon so the no-MID heuristic answers identically on the two
# adapters — the tier exists precisely for panels where nothing authoritative is
# published, and disagreeing about the threshold would make it schema-dependent.
_GRID_POWER_EPSILON_W = 1.0

ISLANDING_ON_GRID = "ON_GRID"
ISLANDING_OFF_GRID = "OFF_GRID"
ASSERTION_NONE = "NONE"


def resolve_islanding_state(mid: DiscoveredDevice | None, panel: DiscoveredDevice) -> str | None:
    """Islanding state by the recorded precedence, or `None` when nothing can say.

    | tier | condition | source |
    | --- | --- | --- |
    | 1 | MID `$state` is `ready` and `islanding-state` present | sensed |
    | 2 | MID not `ready` | `shed/asserted-islanding-state`, when not `NONE` |
    | 3 | no MID at all | `power-flows/grid` heuristic |
    | 4 | none of the above | unknown |

    **Tier 2 is the reason the assertion control exists.** When comms to the BESS or MID
    are lost and the grid returns, the user asserts the grid is up so the BESS stops
    discharging. Declining to read it here would wire the control and then ignore it at
    exactly the moment it matters. Nothing is hidden by doing so: the MID is a device, so
    it goes *unavailable* in Home Assistant when it stops publishing, and the assertion is
    itself visible as the control the user set.

    **Tier 3 never answers `OFF_GRID`, and never asserts on-grid from a missing MID.** An
    earlier draft reasoned that no MID means no islanding authority means on-grid. That is
    wrong: a missing MID means *SPAN* is not the islanding authority, and says nothing
    about whether the site is islanded — a generator-fed island is the plain
    counterexample. Grid power flowing is positive evidence of being on-grid; its absence
    is not evidence of the opposite.
    """
    if mid is not None:
        if mid.state == "ready":
            sensed = text(mid, NODE_GRID, PROP_ISLANDING_STATE)
            if sensed:
                return sensed
        asserted = text(panel, NODE_SHED, PROP_ASSERTED_ISLANDING_STATE)
        if asserted and asserted != ASSERTION_NONE:
            return asserted
        return None

    grid_power = number(panel, NODE_POWER_FLOWS, "grid")
    if grid_power is not None and abs(grid_power) > _GRID_POWER_EPSILON_W:
        return ISLANDING_ON_GRID
    return None


def resolve_dsm_state(islanding: str | None) -> str:
    """`dsm_state` in flat's vocabulary, read rather than derived.

    Flat inferred this from `bess/grid-state`, then `dominant-power-source`, then grid
    power. v1.0 states it, so the heuristic tiers collapse into whatever
    `resolve_islanding_state` could establish. Kept for entity stability: it adds nothing
    over the MID's own value, and it is the entity a user already has.
    """
    if islanding == ISLANDING_ON_GRID:
        return "DSM_ON_GRID"
    if islanding == ISLANDING_OFF_GRID:
        return "DSM_OFF_GRID"
    return UNKNOWN


def resolve_run_config(
    mid: DiscoveredDevice | None,
    islanding: str | None,
    device_types: Mapping[str, str],
) -> str:
    """`current_run_config`, from the grid-forming entity where one is published.

    | condition | result |
    | --- | --- |
    | `grid-forming-entity == "GRID"` | `PANEL_ON_GRID` |
    | resolves to a device of class `bess` | `PANEL_BACKUP` |
    | resolves to any other device | `PANEL_OFF_GRID` |
    | absent, empty, or unresolvable | falls through below |

    This is the part that gets *better* than flat. Flat guessed `PANEL_BACKUP` versus
    `PANEL_OFF_GRID` from `dominant-power-source`; v1.0 names the forming device and its
    class is recoverable from the tree, so the distinction becomes authoritative.

    Falling through, the answer degrades honestly rather than guessing: an on-grid
    islanding answer still gives `PANEL_ON_GRID`, but off-grid cannot be split into
    backup versus off-grid without knowing what is forming the grid, so it reports
    unknown rather than picking one.
    """
    forming = text(mid, NODE_GRID, PROP_GRID_FORMING_ENTITY).strip()
    if forming:
        if forming.upper() == "GRID":
            return "PANEL_ON_GRID"
        resolved = device_types.get(forming)
        if resolved == TYPE_BESS:
            return "PANEL_BACKUP"
        if resolved is not None:
            return "PANEL_OFF_GRID"

    if islanding == ISLANDING_ON_GRID:
        return "PANEL_ON_GRID"
    return UNKNOWN


def resolve_grid_islandable(inverters: Sequence[DiscoveredDevice]) -> bool | None:
    """Whether any inverter can form a grid — flat's `grid-islandable`, relocated.

    `grid-forming/capable` is *"Static hardware capability: does this inverter support
    grid-forming operation at all?"*, the same kind of permanent statement flat made with
    *"Capable of operating with power while disconnected from the grid."* BESS model 0.14
    puts it on the `inverter` child, so the panel-level answer is the disjunction: a panel
    does not island, its DER does, and flat expressed a property of the DER as a property
    of the enclosure.

    `None`, not `False`, when nothing publishes it. Absence means unknown — reporting
    "cannot island" for a panel that simply has not told us would turn a gap into a claim,
    and the integration declines to create the entity on `None`, which is the honest
    outcome. No producer publishes this today: the emitter does not model the BESS child
    roles, so this reads `None` against every capture we have.
    """
    answers = [optional_flag(inverter, NODE_GRID_FORMING, PROP_CAPABLE) for inverter in inverters]
    known = [answer for answer in answers if answer is not None]
    if not known:
        return None
    return any(known)


# Flat's `dominant-power-source` enum, keyed by the device class v1.0 names instead.
# `GENERATOR` has no row because the device-type registry has no generator: flat's value
# came from the panel computing a source class, v1.0 names an actual device, and there is
# no generator device to name yet. One row when there is.
_POWER_SOURCE_BY_TYPE: dict[str, str] = {
    TYPE_BESS: "BATTERY",
    TYPE_PV: "PV",
}


def resolve_dominant_power_source(
    mid: DiscoveredDevice | None,
    device_types: Mapping[str, str],
) -> str | None:
    """Flat's `dominant_power_source`, from the MID's grid-forming entity.

    The integration's entity for this field is already named `grid_forming_entity`, so
    v1.0's `grid/grid-forming-entity` is the same concept it has always shown — not a
    successor to negotiate. What changed is the encoding: flat published a closed enum of
    source *classes*, v1.0 names the actual *device*. Dereferencing the id against the
    tree recovers the class, so the entity keeps its value space and nothing comparing
    against `BATTERY` stops matching.

    **Anything unresolvable becomes `UNKNOWN`, which is in flat's enum already.** A device
    id naming something outside this tree, or a class with no row above, cannot escape as
    a raw id — the device-type registry itself instructs consumers to tolerate unknown
    `$type` values, and this is what tolerating one looks like from a consumer.

    The precision v1.0 adds — *which* battery, distinguishable when a site has two — is
    not discarded, it is surfaced beside this rather than inside it, as
    `SpanMidSnapshot.grid_forming_device_name`. Absorb the change in the state entity that
    exists, surface the addition separately: a changed value breaks automations silently,
    a new field cannot.

    **No MID at all means `GRID`, and that is an elimination rather than a guess.**
    A commissioned MID is what SPAN has to island with, so its absence rules out every
    other value this field can take. `BATTERY` needs a BESS, and a BESS brings a MID.
    `PV` cannot form a grid on its own — anything that can is a grid-forming inverter,
    which is a MID. `NONE` describes a panel supplying nothing, which is a panel that is
    not publishing. That leaves a generator, which is two cases rather than one and only
    one of them reaches here. A generator wired through a MID is named by that MID, so
    the branch above answers and this one never runs. A generator with no MID interface
    is what SPAN treats as the grid, and it is the only generator an install with no MID
    can have. So the elimination holds now and keeps holding if MID-integrated generators
    arrive: they bring a MID, and a MID is answered above.

    A site genuinely running off-grid without storage is not a counterexample; it goes
    dark at sunset.

    This deliberately does **not** follow `resolve_islanding_state`, which refuses the
    same shortcut. The two answer different questions and the counterexample that defeats
    it there is the one that supports it here: a generator-fed island is islanded — so
    inferring on-grid from a missing MID would be wrong — while its grid-forming entity
    really is what SPAN calls the grid. Islanding is a safety fact about separation; this
    is a class of source.

    It is also no worse than flat, which is the bar. Flat could not see an uninterfaced
    generator either and published `GRID` regardless; a panel upgrading to v1.0 keeps the
    answer it has been giving rather than losing it to the loss of a property.

    `None` only when a MID exists and has not answered. That is genuinely unknown — there
    is an islanding authority and it has not said — and is distinct from there being none.
    """
    if mid is None:
        return "GRID"

    forming = text(mid, NODE_GRID, PROP_GRID_FORMING_ENTITY).strip()
    if not forming:
        return None
    if forming.upper() == "GRID":
        return "GRID"
    return _POWER_SOURCE_BY_TYPE.get(device_types.get(forming, ""), UNKNOWN)


def resolve_grid_forming_device_name(
    mid: DiscoveredDevice | None,
    device_names: Mapping[str, str],
) -> str | None:
    """The readable name of whatever is forming the grid, or `None` when it is the grid.

    The wire value is a Homie device id -- `sim-40t-001-SIM-BESS-40T-001`. That means
    nothing to someone reading a dashboard: it is not a Home Assistant device id, and an
    opaque string is worse than no string. Homie's `$description.name` is the device's
    own display name (`Battery`, `Solar`, `SPAN Drive - Garage`), which is what a person
    would recognise, so that is what gets surfaced.

    `None` when the grid is forming (there is no device to name), when no MID publishes
    an answer, or when the id resolves to nothing -- the raw id is still on
    `grid_forming_entity` for anyone who needs the literal value.
    """
    forming = text(mid, NODE_GRID, PROP_GRID_FORMING_ENTITY).strip()
    if not forming or forming.upper() == "GRID":
        return None
    return device_names.get(forming)
