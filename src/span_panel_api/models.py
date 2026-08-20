"""Transport-agnostic snapshot models for SPAN Panel state.

These dataclasses represent panel state as produced by the MQTT/Homie
transport. Energy and power sign conventions are normalized at the
transport boundary — consumers see a consistent view.

All snapshots are immutable (frozen) and memory-efficient (slots).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TypeAlias

# Homie schema type: {type_name: {property_name: {attribute: value}}}
# Values are heterogeneous JSON (str, int, bool, nested dicts).
HomieSchemaTypes: TypeAlias = dict[str, dict[str, object]]


@dataclass(frozen=True, slots=True)
class SpanCircuitSnapshot:
    """Transport-agnostic circuit state."""

    circuit_id: str  # UUID (dashless, normalized)
    name: str
    relay_state: str  # OPEN | CLOSED | UNKNOWN
    instant_power_w: float  # Positive = consumption
    produced_energy_wh: float  # Generation/backfeed (Wh)
    consumed_energy_wh: float  # Consumption (Wh)
    tabs: list[int]
    priority: str  # v1: MUST_HAVE | NICE_TO_HAVE | NON_ESSENTIAL | UNKNOWN
    #                 v2: NEVER | SOC_THRESHOLD | OFF_GRID | UNKNOWN
    is_user_controllable: bool  # v1: Circuit.isUserControllable | v2: not always_on
    is_sheddable: bool  # v1: Circuit.isSheddable | v2: circuit/sheddable
    is_never_backup: bool  # v1: Circuit.isNeverBackup | v2: circuit/never-backup
    device_type: str = "circuit"  # "circuit" | "pv" | "evse"
    relative_position: str = ""  # PV/EVSE: "IN_PANEL" | "UPSTREAM" | "DOWNSTREAM"
    is_240v: bool = False
    current_a: float | None = None
    breaker_rating_a: float | None = None
    always_on: bool = False  # v2 new: circuit/always-on
    relay_requester: str = "UNKNOWN"  # v2 new: circuit/relay-requester
    energy_accum_update_time_s: int = 0  # v1: poll timestamp | v2: MQTT arrival time
    instant_power_update_time_s: int = 0  # v1: poll timestamp | v2: MQTT arrival time
    relay_state_target: str | None = None  # v2: $target for relay (desired state)
    priority_target: str | None = None  # v2: $target for shed-priority (desired state)

    # This circuit's *participation* in the enclosure's Power Control System —
    # `energy.ebus.capability.pcs` 0.3, the half a circuit publishes. The
    # system half (the effective limit and its arbitration) is on the enclosure
    # and lands on `SpanPanelSnapshot.pcs`; a circuit says only whether the PCS
    # manages it and where it sits in the shed order.
    #
    # `None` on both, never `False`/`0`, because both are `MAY` and a circuit
    # that says nothing is not the same as one that says no: priority `0` is a
    # legal ranking, and "unmanaged" is a claim the panel has to make.
    #
    # Distinct from `priority`/`is_sheddable`, which are `load-shed` — a
    # different policy on the same relay. The catalog keeps them apart because
    # they answer different questions (limit site import versus preserve backup
    # runtime) and a circuit may participate in one, both, or neither.
    pcs_managed: bool | None = None  # v2: circuit pcs/managed
    pcs_priority: int | None = None  # v2: circuit pcs/priority


@dataclass(frozen=True, slots=True)
class SpanPVSnapshot:
    """PV inverter metadata — populated only when a PV node is commissioned."""

    vendor_name: str | None = None  # pv/vendor-name
    model: str | None = None  # human designation (v1.0 info/model; flat pv/product-name)
    nameplate_capacity_w: float | None = None  # pv/nameplate-capacity (W)
    feed_circuit_id: str | None = None  # pv/feed (normalized circuit ID)
    relative_position: str | None = None  # pv/relative-position (IN_PANEL | UPSTREAM | DOWNSTREAM)
    software_version: str | None = None
    """`info/firmware-version`, named as on `SpanBatterySnapshot` and `SpanEvseSnapshot`.

    Sub-devices share a spelling because a consumer builds all of them the same way —
    into `DeviceInfo(sw_version=...)`. Only the enclosure calls it `firmware_version`,
    where it is the panel's own and predates the sub-device types.
    """


@dataclass(frozen=True, slots=True)
class SpanMidSnapshot:
    """Microgrid Interconnect Device — the islanding authority. v1.0 only.

    The MID is the device that decides whether the enclosure is islanded, and the
    enclosure model puts `grid` on it deliberately: "Grid connection state, islanding
    state, and grid-forming-entity identity, published on the enclosure-integrated MID
    (the enclosure device itself does not publish them)."

    **Purely additive.** No flat panel publishes a MID node — not the frozen simulator,
    not the live panel — so nothing here can orphan an entity a user already has. That
    is why this is the benign cell of the absorb-or-surface policy: surfacing a new
    device cannot break an automation that never referenced it.

    **Adding this device does not, on its own, fix anything a user sees.** The
    integration renders no entity from `panel.grid_state` — checked, there is no such
    sensor. What it does render is `dsm_state`, its `dsm_grid_state` alias,
    `current_run_config`, `dominant_power_source` and `grid_islandable`, and on v1.0
    four of those five are currently `UNKNOWN` or absent. The MID is where their inputs
    moved, so mapping it back into those fields is the work; this type is what makes
    that possible, plus the option of rendering the MID as hardware in its own right.

    Which raises a design question this type does not settle: if the MID's islanding
    state is also surfaced directly, a user sees the same fact twice. Duplicating an
    existing state entity is not the benign cell of the absorb-or-surface policy.
    """

    node_id: str
    """Stable identity, and the device-registry identifier a consumer builds from.

    The serial where published, falling back to the Homie device id — the same choice
    as `SpanEvseSnapshot`, for the reason `devices/proxy.md` gives: a proxied device id
    is not stable across the proxy-to-native transition, so identity belongs on
    `info/serial-number`.
    """

    serial_number: str | None = None
    vendor_name: str | None = None
    model: str | None = None
    islanding_state: str | None = None
    """`grid/islanding-state` — ON_GRID / OFF_GRID. MUST on a MID, per the enclosure model."""
    grid_state: str | None = None
    """`grid/grid-state` — whether utility power is present, distinct from islanding."""
    software_version: str | None = None
    """`info/firmware-version`, spelled as on the other sub-devices — see `SpanPVSnapshot`."""
    hardware_version: str | None = None
    """`info/hardware-version`. The MID is the first device to carry one into a snapshot.

    r202633 documents it on the MID's `info` node, and a consumer has a field for it
    (`DeviceInfo(hw_version=...)`). Without it the MID's device card shows a model and a
    serial and nothing else, beside a battery showing all three.
    """
    grid_forming_entity: str | None = None
    """`grid/grid-forming-entity` — the raw wire value: `GRID`, or a Homie device id."""
    grid_forming_device_name: str | None = None
    """The forming device's display name, or `None` when the grid itself is forming.

    The raw value above is a Homie device id, which means nothing on a dashboard — it is
    not a Home Assistant device id, and an opaque string is worse than none. This is the
    device's own `$description.name` (`Battery`, `Solar`, `SPAN Drive - Garage`), which
    is the part a person can read. The literal stays available beside it.
    """


@dataclass(frozen=True, slots=True)
class SpanPcsSnapshot:
    """The enclosure's Power Control System — UL 3141 import limiting. v1.0 only.

    A `pcs` node runs one physical actuator and two roles, per
    `capabilities/pcs.md` 0.3: it is the premises-equipment protection (the Firm
    Service Rating), and it is the arbitrator that reconciles *every* active
    import constraint to one enforced current limit. The constraints arrive in
    different native units on different capabilities — amps here, watts on
    `doe`, volts on `voltage-response` — and `pcs` does not re-publish them as
    amps copies. **What it publishes is the result**: the effective
    `import-limit` and the `binding-constraint` naming which class won the
    `min()`.

    That sentence is the shape of this type. `import_limit_a` and
    `binding_constraint` are the answer; the four `{feed,operator,off_grid,
    requested}_import_limit_*` families are the inputs that produced it, kept
    beside the answer so a consumer can explain a number rather than only show
    it.

    **A nested type rather than sixteen optional fields on the panel**, for the
    reason `SpanMidSnapshot` is one: presence is `snapshot.pcs is not None`,
    with nothing to infer from a sentinel. `capabilities/pcs.md` states the
    absence rule outright — "absence of the `pcs` node means the device does not
    run (or participate in) a Power Control System" — so there is a real
    distinction between a panel with no PCS and a PCS reporting zeros, and
    sixteen `None`s on the enclosure could not carry it.

    **Flat is the absence case, not a translation problem.** No flat panel
    publishes `energy.ebus.capability.pcs` at all, so nothing here can orphan an
    entity a user already has.

    Every member is optional because every property in the catalog is `SHOULD`
    or `MAY`: a conformant publisher populates whichever constraint classes
    apply to its equipment and omits the rest. `None` therefore means "this
    panel does not report it", which is a different statement from a limit of
    `0.0` — and `0.0` is a legal, meaningful reading (no import permitted), so
    no field may default to it.
    """

    enabled: bool | None = None
    """`pcs/enabled` — is the PCS enabled on this enclosure at all?"""
    active: bool | None = None
    """`pcs/active` — is it limiting import *right now*?

    Distinct from `enabled`: a configured PCS spends most of its life enabled
    and inactive, and this is the transition an automation triggers on.
    """
    import_limit_a: float | None = None
    """`pcs/import-limit` (A) — the effective enforced limit, the `min()` result.

    The single number that summarises the capability, and the only one that
    reflects the reconciled `doe` and `voltage-response` constraints as well as
    the amps-native families below.
    """
    binding_constraint: str | None = None
    """`pcs/binding-constraint` — which class currently sets `import_limit_a`.

    The catalog enum is `FSR`, `DOE`, `VOLTAGE`, `OFF_GRID`, `REQUESTED`,
    `OPERATOR`, `NONE`, `UNKNOWN`, and publishers **MAY extend it** through the
    property's Homie `$format`. Kept as the raw wire string for that reason: a
    re-encoding onto a closed set defined here would drop a vendor's extension
    on the floor, and this is the property whose whole job is naming a source.
    """

    feed_import_limit_a: float | None = None
    """`pcs/feed-import-limit` (A) — the FSR: the commissioned, always-on floor.

    May be below the main-breaker rating where the service feed is smaller than
    the panel; the catalog's example is a 200 A panel on a 100 A feed.
    """
    feed_import_limit_enablement: str | None = None
    """`pcs/feed-import-limit-enablement` — `UNSPECIFIED`, `UNCONFIGURED`, `DISABLED`, `ENABLED`."""
    feed_import_limit_active: bool | None = None
    """`pcs/feed-import-limit-active` — is this constraint enforcing?

    Distinct from `binding_constraint`, and deliberately: several constraints
    can be active at once, and only the most restrictive is binding.
    """

    operator_import_limit_a: float | None = None
    """`pcs/operator-import-limit` (A) — an externally imposed fleet/aggregator cap.

    Set over the vendor's management API and persisting until the operator
    changes it — not the standardised IEEE 2030.5 watts envelope, which lives
    on `doe`.
    """
    operator_import_limit_enablement: str | None = None
    """`pcs/operator-import-limit-enablement` — same enum domain as the feed family."""
    operator_import_limit_active: bool | None = None
    """`pcs/operator-import-limit-active` — is the operator cap enforcing?"""

    off_grid_import_limit_a: float | None = None
    """`pcs/off-grid-import-limit` (A) — the import cap while islanded."""
    off_grid_import_limit_enablement: str | None = None
    """`pcs/off-grid-import-limit-enablement` — same enum domain as the feed family."""
    off_grid_import_limit_active: bool | None = None
    """`pcs/off-grid-import-limit-active` — typically true only while islanded."""

    requested_import_limit_a: float | None = None
    """`pcs/requested-import-limit` (A) — a voluntary, self-revocable user limit.

    Requested by the homeowner or installer through the vendor's app. Distinct
    from the operator cap, which the site cannot revoke.
    """
    requested_import_limit_enablement: str | None = None
    """`pcs/requested-import-limit-enablement` — same enum domain as the feed family."""
    requested_import_limit_active: bool | None = None
    """`pcs/requested-import-limit-active` — is the voluntary limit enforcing?"""


@dataclass(frozen=True, slots=True)
class SpanEvseSnapshot:
    """EV Charger (EVSE) state — populated when EVSE node is commissioned."""

    node_id: str  # Homie node ID (for unique identification)
    feed_circuit_id: str  # Normalized circuit ID this EVSE is connected to
    status: str = "UNKNOWN"
    lock_state: str = "UNKNOWN"  # LOCKED | UNLOCKED | UNKNOWN
    advertised_current_a: float | None = None  # Amps offered to EV
    # Device metadata — flows into HA DeviceInfo, not separate entities
    vendor_name: str | None = None
    model: str | None = None  # human designation (v1.0 info/model; flat evse/product-name)
    part_number: str | None = None  # SKU
    serial_number: str | None = None
    software_version: str | None = None


@dataclass(frozen=True, slots=True)
class SpanBatterySnapshot:
    """Battery state — populated only when BESS node is commissioned."""

    soe_percentage: float | None = None
    # Note: field name is historically misnamed (soe = kWh, soc = %).
    # Name is preserved to avoid entity/dashboard breaks in the integration.
    soe_kwh: float | None = None  # bess/soe (kWh) — new v2 field, no v1 equivalent

    # BESS metadata
    vendor_name: str | None = None  # bess/vendor-name
    model: str | None = None  # human designation (v1.0 info/model; flat bess/product-name)
    part_number: str | None = None  # SKU (v1.0 info/part-number; flat bess/model)
    serial_number: str | None = None  # bess/serial-number
    software_version: str | None = None  # bess/software-version
    nameplate_capacity_kwh: float | None = None  # bess/nameplate-capacity (kWh)
    connected: bool | None = None  # bess/connected

    # The BESS's own `meter/active-power`, v1.0 only. **Charge-positive**, which
    # is a sign flip away from the wire: the enclosure meters the BESS the way it
    # meters a circuit, so a charging battery reads negative there and positive
    # here, exactly as `SpanCircuitSnapshot.instant_power_w` reports a load's
    # consumption positive. The snapshot's rule across every power field is that
    # positive means power flowing *into* the metered device.
    #
    # Distinct from `SpanPanelSnapshot.power_flow_battery`, which is the
    # enclosure's own arbitrated flow figure and is passed through in the
    # publisher's discharge-positive frame. The two describe the same physical
    # power in opposite frames, so a consumer rendering both must negate one of
    # them; this one is already negated.
    power_w: float | None = None  # v2: bess meter/active-power (W), charge-positive

    # `status/communication-state`, v1.0 only: the BESS publisher's report of its
    # own link health (OK/DEGRADED/LOST/UNKNOWN). **Not** `connected`, which is
    # the enclosure's `connection/fed-by-device-status` view of the same device.
    # One is the device speaking about itself, the other the panel speaking about
    # it, and the migration guide warns against conflating them.
    communication_state: str | None = None  # v2: bess status/communication-state


@dataclass(frozen=True, slots=True)
class FieldMetadata:
    """Schema-derived metadata for a single snapshot field.

    Exposed by the client in a dict keyed by snapshot field path
    (e.g. ``"panel.instant_grid_power_w"``). The integration compares
    these values against its sensor definitions for unit validation.
    """

    unit: str | None  # "W", "A", "V", "%", "kWh", None
    datatype: str  # "float", "integer", "enum", "string", "boolean"
    resolved: bool = True
    """Whether a device declaring this field was actually found.

    Three-way contract with consumers:

    - entry present, ``resolved=True`` — the field is produced; ``unit`` is meaningful
    - entry present, ``resolved=False`` — a device of the mapped type is in the
      tree but does not declare the property. A real gap; ``unit`` is None.
    - **no entry** — no device of that type, or none identifiable for that role.
      Nothing will populate the field.

    The second half of that last case is the lugs pair. Both devices declare the
    same type and the same ``meter`` node, so which one feeds ``panel.upstream_*``
    and which feeds ``panel.feedthrough_*`` / ``panel.downstream_*`` is decided by
    the ``info/direction`` value they publish. A lugs device that publishes no
    direction fills neither role, and gets no entry rather than an unresolved one
    — deliberately, because the snapshot mapper resolves the pair through the same
    call and populates nothing for it either. An unresolved entry would promise a
    field that is degraded; there is no such field to degrade.

    Defaulted so existing construction sites are unaffected. This is a
    bootstrap dataclass, not a ``SchemaAdapter`` member, so adding it does not
    invalidate built adapter wheels or bump ``ADAPTER_CONTRACT_VERSION``.
    """


@dataclass(frozen=True, slots=True)
class V2AuthResponse:
    """Response from POST /api/v2/auth/register."""

    access_token: str
    token_type: str
    iat_ms: int
    ebus_broker_username: str
    ebus_broker_password: str  # Use this for MQTT, NOT hop_passphrase
    ebus_broker_host: str
    ebus_broker_mqtts_port: int
    ebus_broker_ws_port: int
    ebus_broker_wss_port: int
    hostname: str
    serial_number: str
    hop_passphrase: str  # For REST auth only; will diverge from broker password


@dataclass(frozen=True, slots=True)
class V2StatusInfo:
    """Response from GET /api/v2/status."""

    serial_number: str
    firmware_version: str
    proximity_proven: bool | None = None  # Added in firmware 202609; None on older panels


_CIRCUIT_TYPE_KEY = "energy.ebus.device.circuit"


@dataclass(frozen=True, slots=True)
class V2HomieSchema:
    """Response from GET /api/v2/homie/schema."""

    firmware_version: str
    types_schema_hash: str  # SHA-256, first 16 hex chars
    types: HomieSchemaTypes
    # The flat-vs-parent/child discriminator, and the reason this endpoint is
    # fetched before MQTT is opened rather than during connect(). Absent on flat
    # firmware (r202603-r202627) and present from r202633, which SPAN confirmed
    # is a reliable signal over REST — the same one MQTT publishes as
    # ``info/data-model-version``. Defaulted so a caller constructing this model
    # directly still describes a flat panel, which is what every panel in the
    # field is today.
    data_model_version: str | None = None

    @property
    def panel_size(self) -> int:
        """Extract panel size from the circuit ``space`` property format.

        The Homie schema defines ``space`` with ``"format": "min:max:step"``
        (e.g. ``"1:32:1"``). The *max* value is the number of breaker spaces
        in the panel.

        Raises:
            ValueError: If the space format is missing or unparseable.
        """
        circuit_type = self.types.get(_CIRCUIT_TYPE_KEY, {})
        space_prop = circuit_type.get("space")
        if not isinstance(space_prop, dict):
            raise ValueError(f"Schema missing '{_CIRCUIT_TYPE_KEY}/space' property")
        fmt = space_prop.get("format")
        if not isinstance(fmt, str):
            raise ValueError(f"Schema '{_CIRCUIT_TYPE_KEY}/space' has no format string")
        parts = fmt.split(":")
        if len(parts) != 3:
            raise ValueError(f"Unexpected space format '{fmt}', expected 'min:max:step'")
        try:
            return int(parts[1])
        except ValueError as exc:
            raise ValueError(f"Cannot parse max from space format '{fmt}'") from exc


@dataclass(frozen=True, slots=True)
class SpanPanelSnapshot:
    """Complete panel state — single point-in-time view."""

    serial_number: str
    firmware_version: str

    # Panel-level power and energy
    main_relay_state: str
    instant_grid_power_w: float
    feedthrough_power_w: float
    main_meter_energy_consumed_wh: float
    main_meter_energy_produced_wh: float
    feedthrough_energy_consumed_wh: float
    feedthrough_energy_produced_wh: float

    # v1 field names preserved — MQTT transport derives these from v2 data
    dsm_state: str  # v1: direct | v2: multi-signal heuristic
    current_run_config: str  # v1: direct | v2: tri-state from grid_state + islandable + DPS

    # Hardware status — v1 field names preserved
    door_state: str  # v1: direct | v2: core/door
    proximity_proven: bool  # v1: proximity sensor | v2: MQTT auth + $state==ready
    uptime_s: int  # v1: panel uptime | v2: connection uptime since $state==ready
    eth0_link: bool  # v1: direct | v2: core/ethernet
    wlan_link: bool  # v1: direct | v2: core/wifi
    wwan_link: bool  # v1: direct | v2: vendor-cloud == "CONNECTED"
    panel_size: int  # Total breaker spaces (from Homie schema space format)

    # v2-native fields — None for REST transport
    dominant_power_source: str | None = None  # v2: core/dominant-power-source (settable)
    grid_state: str | None = None  # v2: bess/grid-state (None = no BESS or REST)
    grid_islandable: bool | None = None  # v2: core/grid-islandable
    l1_voltage: float | None = None  # v2: core/l1-voltage (V)
    l2_voltage: float | None = None  # v2: core/l2-voltage (V)
    main_breaker_rating_a: int | None = None  # v2: core/breaker-rating (A)
    wifi_ssid: str | None = None  # v2: core/wifi-ssid
    vendor_cloud: str | None = None  # v2: core/vendor-cloud

    # Power flows (None when node not present)
    power_flow_pv: float | None = None  # v2: power-flows/pv (W)
    power_flow_battery: float | None = None  # v2: power-flows/battery (W)
    power_flow_grid: float | None = None  # v2: power-flows/grid (W)
    power_flow_site: float | None = None  # v2: power-flows/site (W)

    # Backup-planning forecast (`shed-forecast`, v1.0 only; None when the
    # enclosure publishes no such node). Minutes, as the capability declares —
    # `int` rather than `float` because the wire datatype is `integer` and a
    # forecast is not measured to a fraction of a minute.
    #
    # `None` is load-bearing on all five: a panel that does not publish the node
    # must produce no entity, and zero is a legitimate reading ("shedding
    # starts now"). Defaulting any of these to 0 would say exactly that.
    shed_time_to_priority_shed_min: int | None = None
    """`shed-forecast/time-to-priority-shed` — minutes until the next priority tier sheds."""
    shed_total_time_remaining_min: int | None = None
    """`shed-forecast/total-time-remaining` — minutes until every sheddable circuit is shed."""
    shed_full_charge_time_to_priority_shed_min: int | None = None
    """`shed-forecast/full-charge-time-to-priority-shed` — the same estimate from a full BESS.

    A capability figure, not a countdown: it answers "what would this
    installation give me if the battery were full", so it moves when the
    hardware or the load profile changes rather than as the battery drains.
    """
    shed_full_charge_total_time_remaining_min: int | None = None
    """`shed-forecast/full-charge-total-time-remaining` — total runtime from a full BESS."""
    shed_forecast_confidence: str | None = None
    """`shed-forecast/confidence` — LOW | MEDIUM | HIGH, the algorithm's self-assessment.

    Kept as the raw wire string. It qualifies the four times rather than
    standing alone, and a consumer that shows it beside them needs the value the
    catalog's enum defines, not a re-encoding of it.
    """

    # Upstream lugs per-phase current (None when not available)
    upstream_l1_current_a: float | None = None  # v2: upstream-lugs/l1-current (A)
    upstream_l2_current_a: float | None = None  # v2: upstream-lugs/l2-current (A)

    # Downstream lugs per-phase current (None when not available)
    downstream_l1_current_a: float | None = None  # v2: downstream-lugs/l1-current (A)
    downstream_l2_current_a: float | None = None  # v2: downstream-lugs/l2-current (A)

    # Collections
    circuits: dict[str, SpanCircuitSnapshot] = field(default_factory=dict)
    battery: SpanBatterySnapshot = field(default_factory=SpanBatterySnapshot)
    pv: SpanPVSnapshot = field(default_factory=SpanPVSnapshot)
    evse: dict[str, SpanEvseSnapshot] = field(default_factory=dict)  # keyed by serial (see SpanEvseSnapshot.node_id)
    mid: SpanMidSnapshot | None = None
    """The islanding authority, when the panel publishes one. v1.0 only.

    `None` rather than an empty instance, deliberately. `has_bess` has to guess
    presence from `soe_percentage is not None` because the battery field is always
    there, and its own docstring records that only that one field is a reliable
    signal. A new optional device should not inherit that: presence is
    `snapshot.mid is not None`, with nothing to infer.
    """
    pcs: SpanPcsSnapshot | None = None
    """The enclosure's Power Control System, when it publishes a `pcs` node. v1.0 only.

    `None` follows `mid` for the same reason, and here the capability states the
    rule itself: "absence of the `pcs` node means the device does not run (or
    participate in) a Power Control System". A panel with no PCS and a PCS
    holding zeros are different facts, and only a nullable member can tell them
    apart — every limit in this capture is a legal `0.0`.
    """
