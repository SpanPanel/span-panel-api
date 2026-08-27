"""Transport-agnostic snapshot models for SPAN Panel state.

These dataclasses represent panel state as produced by the MQTT/Homie
transport. Energy and power sign conventions are normalized at the
transport boundary — consumers see a consistent view.

All snapshots are immutable (frozen) and memory-efficient (slots).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

# Homie schema type: {type_name: {property_name: {attribute: value}}}
# Values are heterogeneous JSON (str, int, bool, nested dicts).
type HomieSchemaTypes = dict[str, dict[str, object]]


@dataclass(frozen=True, slots=True)
class SpanCircuitSnapshot:
    """Transport-agnostic circuit state."""

    circuit_id: str  # UUID (dashless, normalized)
    name: str
    relay_state: str  # OPEN | CLOSED | UNKNOWN
    # `None` means the meter has not reported, which is not the same as a meter
    # reporting zero. A retained-topic replay delivers a device's description
    # before its values, so a circuit is known to exist for a window in which it
    # has said nothing; filling that window with `0.0` publishes a reading the
    # panel never made. On a cumulative counter that is destructive rather than
    # cosmetic — a consumer compensating for firmware counter resets reads the
    # fabricated zero as a reset and books the whole counter as an offset
    # (`SpanPanel/span#259`). A new circuit legitimately reads `0.0`, and the
    # two must stay tellable apart.
    instant_power_w: float | None  # Positive = consumption
    produced_energy_wh: float | None  # Generation/backfeed (Wh)
    consumed_energy_wh: float | None  # Consumption (Wh)
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

    connected: bool | None = None
    """The enclosure's view of the link to this PV, v1.0 only.

    The same fact `SpanBatterySnapshot.connected` carries and read the same way —
    from the enclosure-side owner's `connection` record, never from anything the
    inverter says about itself. Only the half of the record differs: a BESS is
    named by the upstream lugs' `fed-by-device-*`, a circuit-fed DER by its
    circuit's `feeds-device-*`.

    `None` means no owner has claimed this device, or the claiming owner
    published no status — which is the specification's own "unknown" signal
    (`capabilities/connection.md`: an unpublished property *is* how a panel says
    it does not know) and is deliberately distinct from `False`. The enum has
    three members, `OK,LOST,DEGRADED`, and no UNKNOWN, so absence is the only
    way to say it.
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

    charge_current_limit_a: int | None = None
    """The charge-current ceiling a user may lower, in amps. v1.0 only.

    The only settable property the v1.0 surface carries, and the one whose wire
    name is not settled: the reference tree declares it
    `config/user-max-charge-current`, the eBus catalog specifies
    `charge-limit/owner-limit`. The adapter reads whichever the charger's own
    `$description` declares (`schema_1.charge_limit`), so this field is named
    for the concept and no consumer has to know which spelling arrived.

    `None` means the charger declares no such property — `charge-limit.md` reads
    that as "no adjustable charge-current ceiling; it charges at a fixed rate" —
    or that it has not published a value yet.

    **Not `advertised_current_a`.** That is the current actually being offered
    to the vehicle, which the capability defines as the `min()` of this, the
    installer ceiling, any external controller's limit, and any PCS import limit
    on the feeding circuit. This is one input to that; that is the result.
    """

    charge_current_ceiling_a: int | None = None
    """The commissioned maximum `charge_current_limit_a` may not exceed, in amps.

    `config/max-charge-current` or `charge-limit/installer-max`, by the same
    resolution. Set at commissioning from the breaker rating and J1772 derating,
    and not settable — which is the single Homie attribute distinguishing it
    from the property above, so a consumer must never write it.
    """

    charge_current_limit_target_a: int | None = None
    """Homie `$target` for the charge-current limit — a command in flight, not a reading.

    Present between a write being accepted and the charger republishing the
    value, exactly as `SpanCircuitSnapshot.priority_target` is for a priority
    change. A consumer shows it as pending rather than treating it as state.
    """

    charge_current_limit_settable: bool = False
    """Whether the charger declares its charge-current limit writable.

    Read from `$settable` on the declaration, defaulting to **False**: absence
    means read-only here, the opposite of `load-shed/priority`, because the
    limit and the installer ceiling differ by this attribute alone. A consumer
    creates a control only where this is true, and the adapter refuses to name a
    set topic when it is not.
    """

    connected: bool | None = None
    """The enclosure's view of the link to this charger, v1.0 only.

    Documented on `SpanPVSnapshot.connected`, which carries the identical fact
    for the other circuit-fed DER class.

    **Not `status`.** That is the OCPP-style session state — whether a vehicle is
    plugged in and what it is doing — reported by the charger about the cable in
    front of it. This is the enclosure reporting whether it can talk to the
    charger at all. A charger with a car plugged in and a dead link publishes
    `status="CHARGING"` and `connected=False` at the same time, and a consumer
    that renders them as one entity is answering the wrong question in half the
    cases.
    """


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
    # positive means power flowing *out of* the battery, which is discharging.
    # That is the frame the eBus specification asks of a device's own meter, and
    # it is deliberately NOT the into-the-device rule the circuit fields follow:
    # the wire input is in the opposite frame, so one negation lands here rather
    # than there. Measured against a producer in self-consumption with the grid
    # at zero, where the direction cannot be argued.
    #
    # Distinct from `SpanPanelSnapshot.power_flow_battery`, which is the
    # enclosure's own arbitrated flow figure, passed through untouched and
    # charge-positive. The two describe the same physical power in opposite
    # frames, so a consumer rendering both must negate one of them; this one is
    # already negated.
    power_w: float | None = None  # v2: bess meter/active-power (W), discharge-positive

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


DISCOVERY_NAMESPACE = "discovered"
"""Field-path namespace for properties an adapter declares and does not address.

Rows under this namespace are **not** curated fields. They name a wire property
the panel's own ``$description`` declares and that the running adapter maps to
no snapshot field and reads nowhere — the runtime half of the
declared-but-unread question, asked of the panel in front of the user rather
than of a vendored capture.

Namespaced rather than flagged because the failure this prevents is a *silent*
one. A consumer's curated inventories are keyed by snapshot field path
(``panel.``, ``circuit.``, ``battery.``, …), and a discovered row that reached
one of them would be read as a produced field nothing renders, which is the
shape of a real defect. A distinct prefix means the partition is a string test
any consumer can apply once, before any other question is asked of the map, and
that a discovered row landing in a curated set is a visible error rather than an
extra entry nobody notices.

The path body is ``{device type}/{node}/{property}``, the same rendering the
capability catalogs and the consumer-side gap inventories use, so a maintainer
reading a row can look it up without translating it.
"""

_DISCOVERY_PREFIX = f"{DISCOVERY_NAMESPACE}."


def discovery_path(device_type: str, node_id: str, property_id: str) -> str:
    """The namespaced field path for one declared-but-unaddressed property.

    `device_type` is the eBus type with its common ``energy.ebus.device.``
    prefix already stripped by the caller — the adapter owns that vocabulary,
    and this function owns only the namespace.
    """
    return f"{_DISCOVERY_PREFIX}{device_type}/{node_id}/{property_id}"


def is_discovery_path(field_path: str) -> bool:
    """Whether `field_path` names a discovered property rather than a curated field."""
    return field_path.startswith(_DISCOVERY_PREFIX)


@dataclass(frozen=True, slots=True)
class DiscoveredMetadata(FieldMetadata):
    """A metadata row for a property the panel declares and the adapter does not read.

    Only ever appears under `DISCOVERY_NAMESPACE`. Carries the declaration and
    nothing else: the property's declared ``unit`` and ``datatype``, and whether
    the panel has published a value for it — never the value. These rows exist
    to be forwarded to a maintainer through consumer diagnostics, which leave
    the machine they were generated on, so the type deliberately has no member a
    reading could be put in.

    ``resolved`` is always True here and says nothing new: a discovered row
    exists *because* a device declared the property, so the device is found by
    construction. `retained` is the question that has an answer.
    """

    retained: bool = False
    """Whether any device declaring this property has published a value for it.

    False is the declared-but-never-valued case panelbench's
    ``test_declared_but_unvalued`` looks for from the producer side — a property
    the firmware advertises and never fills. Distinguishing it matters for the
    only decision these rows inform: a declaration with no traffic behind it is
    not a surface worth curating yet.
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

    @classmethod
    def from_status_payload(cls, payload: Mapping[str, object]) -> V2StatusInfo:
        """Read one decoded ``/api/v2/status`` body.

        One reader, because there were two — the detector's, deciding whether the
        panel speaks v2 at all, and ``get_v2_status``'s, reading the same answer
        for a caller that already knows it does. They had already drifted: only
        the detector read ``proximityProven``, so the same panel reported
        "proximity unknown" or "proximity proven" depending on which of the two
        had asked.

        A field the panel omits reads as the empty string rather than as an
        error. This endpoint's whole job on the detection path is to answer for a
        panel that may not fully support it, so a partial body is information,
        not a failure.

        ``proximity_proven`` is the exception and stays ``None`` unless the panel
        published a real boolean. Absent and false are different facts there —
        firmware below 202609 does not report it at all — and coercing whatever
        arrived would turn a string ``"false"`` into ``True``.
        """
        raw_proximity = payload.get("proximityProven")
        return cls(
            serial_number=str(payload.get("serialNumber", "")),
            firmware_version=str(payload.get("firmwareVersion", "")),
            proximity_proven=raw_proximity if isinstance(raw_proximity, bool) else None,
        )


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


ADOPTION_IDENTITY_NODE = "info"
"""The node whose properties are a device's build identity, never entities.

`info/model`, `info/serial-number`, `info/firmware-version` and their siblings
describe the thing rather than report a reading. On a curated device they already
land on the device card -- `bess_device_info` has read them that way since v1.0 --
and an adopted device gets the same treatment for the same reason.
"""

ADOPTION_TOPOLOGY_NODE = "connection"
"""The node that says what a device hangs off, never entities.

`connection` answers a device-tree question: which device feeds this one, which
one it feeds, and the health of that link. That is `via_device` and the registry,
not a sensor -- a panel publishing its own wiring should not arrive as a handful
of entities holding opaque device ids.

The partition is by node rather than by property name deliberately. The eBus
catalogs carry no marker for "this string is a device reference", so a consumer
that wants one has to hard-code the property names, and that list goes stale:
`ebus-sdk`'s own `topology.py` covers `feeds-device-id` and `fed-by-device-id`
and silently omits `grid-forming-entity`, which lives on the `grid` capability.
A node is what the vocabulary defines, so keying on it cannot go stale that way.
"""


@dataclass(frozen=True, slots=True)
class ControlTarget:
    """Where a control command goes, and which property will report it landing.

    Produced by the adapter and by nothing else. Verifying a write means
    watching the property that reports it, and the transport holds only a topic
    string -- it cannot derive the triple from that without learning two
    schemas' topic grammars, which is exactly the wire knowledge the bootstrap
    is supposed to be free of. The two are also schema-private and differ:
    flat's relay is `(serial, circuit_id, "relay")`, v1.0's is
    `(circuit_id, "switch", "relay")`.

    One value rather than two calls, so the topic a command is published to and
    the property watched for its effect cannot come from different resolutions
    of the same request -- which is how a control ends up confirming itself
    against the wrong charger the day a harmonisation rule changes.

    `device_id`, `node_id` and `property_id` are the triple
    `SchemaAdapter.register_property_callback` reports values under, and must be
    spelled exactly as that stream spells them or nothing will ever match.
    """

    topic: str
    """The topic the command is published to, ready to use."""

    device_id: str
    """The Homie device, as the observation stream names it.

    Under the flat schema every property belongs to the panel, so this is the
    panel serial. Under parent/child it is whichever device owns the node.
    """

    node_id: str
    """The Homie node the property lives on."""

    property_id: str
    """The Homie property that reports this control's value."""


@dataclass(frozen=True, slots=True)
class AdoptedProperty:
    """One property of a device this library models no snapshot field for.

    The counterpart to `DiscoveredMetadata`, and deliberately not the same type.
    A discovered row describes a property on a device the adapter *does* model
    and exists to be forwarded in diagnostics, so it carries no value by
    construction. An adopted property belongs to a device nothing here models at
    all, and its whole purpose is to reach a consumer as a reading -- so it
    carries the value, and must never be put in diagnostics.
    """

    node_id: str
    """The Homie node, e.g. `meter`.

    Never `info` or `connection`: those two resolve to the device card and the
    device tree before this type is built.
    """

    property_id: str
    """The Homie property, e.g. `active-power`."""

    datatype: str
    """The declared Homie datatype -- `float`, `integer`, `boolean`, `enum`, `string`.

    What a consumer parses the value with, and half of what it picks a platform
    with.
    """

    unit: str | None = None
    """The declared unit, verbatim.

    `None` when the declaration carries none, which is the normal case for a
    `boolean` or an `enum`.
    """

    format: str | None = None
    """The declared Homie `$format`: an option list for an `enum`, a
    `min:max:step` range for a number.

    Load-bearing for a settable property, because it is the value domain. A
    select with no option list and a number with no bounds are not controls a
    consumer can build, so its absence is what makes a settable property surface
    read-only rather than as a control.
    """

    settable: bool = False
    """Whether the panel accepts a write to this property."""

    value: str | None = None
    """The retained value as published, unparsed.

    `None` when the property is declared and nothing has arrived.
    """

    set_topic: str | None = None
    """The topic a write to this property is published to, or None.

    Populated **only** for a settable property on an adopted device, and that
    scoping is the authorisation rather than a check somebody has to remember.

    The alternative -- a generic `set_property_topic(device, node, property)` on
    the adapter -- would be a back door around every curated control, and the
    bypass would skip real work: schema_1 has to translate `GRID` into `ON_GRID`
    for the islanding assertion, and `evse_charge_limit_payload` *refuses* a
    value above the commissioned ceiling because publishing past it is the one
    write with a physical consequence. A topic that can only ever exist on a
    device nothing models cannot be aimed at either.

    It also keeps this additive. A member on `SchemaAdapter` becomes required of
    every adapter package, so an install carrying an older adapter wheel would
    fail at *discovery* -- the whole integration, not one feature.
    """

    @property
    def path(self) -> str:
        """`{node}/{property}` -- how the capability catalogs spell it."""
        return f"{self.node_id}/{self.property_id}"


@dataclass(frozen=True, slots=True)
class AdoptedDevice:
    """A device on the tree whose type this library models no fields for.

    Adoption is scoped to a whole device rather than to a property, and the
    distinction is the design. A new property on a device we *do* model is a
    curation task with a short turnaround, and minting an entity for it spends an
    entity id permanently on a shape a human would likely have chosen differently
    -- the sixteen `pcs` properties that curation collapsed into one entity and
    thirteen attributes are the worked example. A device type nothing here models
    is the opposite case: no curation is coming, so surfacing it is strictly
    better than the silence that ships today.

    Extra instances of a *modelled* type are deliberately not adopted. A second
    BESS is a multiplicity limitation, not an unmodelled device, and adopting it
    would put a machine-named device card beside a curated one describing the
    same class of hardware.
    """

    device_id: str
    """The device's own id on the wire.

    Opaque, and per the eBus proxy rule (`{proxier-id}-{proxied-id}`) not
    comparable across enclosures -- the same physical device carries different
    ids under different proxiers by design. Usable as this panel's local handle,
    never as a cross-panel identity.
    """

    device_type: str
    """The declared `$type`, e.g. `energy.ebus.device.generator`, verbatim."""

    name: str | None = None
    """The device's declared Homie `name`, when it publishes one."""

    vendor_name: str | None = None
    """`info/vendor-name` -- for the device card."""

    model: str | None = None
    """`info/model` -- for the device card."""

    serial_number: str | None = None
    """`info/serial-number` -- for the device card.

    Deliberately *not* an identity-anchor decision made here. A consumer that
    keys a device registry on an anchor must freeze it at first sighting: a
    serial arriving on a device already adopted under its wire id is new
    information for the card and nothing else, because re-deriving the anchor
    turns an upgrade into a device replacement and takes the entities with it.
    """

    software_version: str | None = None
    """`info/firmware-version` -- for the device card."""

    hardware_version: str | None = None
    """`info/hardware-version` -- for the device card."""

    parent: str | None = None
    """The device id this device declares as its parent, verbatim.

    Carried rather than acted on. An adopted device is registered under the
    enclosure like every other sub-device this library's consumers build, so this
    field changes no topology today -- it exists so that the first real panel
    carrying a *proxied* unmodelled device tells us its shape instead of having
    it flattened away.

    That case is not hypothetical: the reference tree's own `bess-mid` declares
    `parent: bess`, which is the specification's `{proxier-id}-{proxied-id}`
    naming (`devices/proxy.md`). A vendor gateway proxying its own sub-devices
    would arrive the same way, and the parent link is the only structural
    information about how they relate.

    Not acted on *yet*, deliberately. `ebus-sdk` 0.21.0 introduced `DeviceSpec`
    and `DeviceTreeBuilder` (python-sdk#57) and the maintainer's stated next step
    is reconciling the existing graph builder against it rather than landing
    both, so the tree model is being reshaped upstream. Building nesting
    semantics against a shape under active reconciliation would be building
    against a moving target; carrying the field costs nothing and captures the
    evidence for when it settles.
    """

    proxied: bool = False
    """Whether this device is proxied by a peer rather than by the enclosure.

    True when the declared `parent` is a device other than the tree root. The
    distinction the raw `parent` cannot express on its own, because a consumer
    holding one device has no way to tell the enclosure's id from a sibling's --
    ids are opaque by design, and per python-sdk#49 a proxied id's prefix is the
    *proxier's* id, so the same physical device carries different ids under
    different enclosures.
    """

    properties: tuple[AdoptedProperty, ...] = ()
    """Everything outside `info` and `connection`, in declaration order."""


@dataclass(frozen=True, slots=True)
class ExtensionSubject:
    """Which modelled snapshot subject an extension property hangs off.

    The adapter already knows which wire device populated which snapshot subject
    -- that mapping is how `battery.power_w` gets a value. This type exposes the
    *subject* and never the mapping: a consumer needs "this belongs to the
    battery", not "this is how `battery.*` is assembled". Exporting the
    field-level map would freeze the adapter's internals as API; exporting the
    subject cannot, because it is one value per device drawn from a closed set.

    Resolution is by declared `$type` and is indifferent to proxying: the
    reference tree's own MID arrives proxied as `bess-mid` and still resolves to
    `mid`. A proxied device of an *unmodelled* type resolves to no subject at all
    and belongs to `AdoptedDevice`, which carries `parent` for that shape.
    """

    kind: str
    """One of: `panel`, `lugs`, `battery`, `mid`, `pv`, `evse`, `circuit`.

    `lugs` is separate from `panel` although its curated fields land in the panel
    snapshot, because a subject is an identity and the two lugs devices are two
    devices: they run the same firmware, so a vendor extension on one is the
    expected case of the same extension on both, and folding them into `panel`
    made two wire addresses one identity.
    """

    instance_key: str | None = None
    """The snapshot map key for multi-instance kinds, `None` for the singletons.

    The EVSE's `node_id` and the circuit's `circuit_id` -- the same keys
    `snapshot.evse` and `snapshot.circuits` use, so a consumer holding the
    snapshot resolves the subject with a lookup it already performs.
    """


@dataclass(frozen=True, slots=True)
class ExtensionProperty:
    """One property a *modelled* device declares that no snapshot field carries.

    The value-carrying counterpart to `DiscoveredMetadata`, and deliberately a
    third type rather than either neighbour. A discovered row exists to be
    forwarded in diagnostics -- payloads that leave the machine into issues and
    forum posts -- so it carries no value by construction. An `AdoptedProperty`
    belongs to a device nothing here models, and its `set_topic` scoping *is* a
    write authorisation this type must not inherit. This one belongs to a device
    the adapter does model, exists to reach a consumer as a reading, and is
    read-only by construction: no set topic, and no member a write path could be
    built from.

    **Not a `FieldMetadata`, and that is the diagnostics guarantee.**
    `partition()` walks `build_field_metadata()`; this type rides
    `build_snapshot()` instead, so there is no code path from here into
    `SchemaFindings` or a diagnostics payload. The same wire property appears in
    both surfaces on purpose -- as a declaration for the maintainer, as a value
    for the user -- joined by the `{node}/{property}` path body.

    **Read-only is not a policy this type states, it is a shape it has.** A
    settable extension property is carried with `settable=True` for curation
    triage and still surfaces as a reading: a control on a modelled device would
    sit beside curated controls that do real safety work (the EVSE limit refuses
    a value above the commissioned ceiling; schema_1 translates `GRID` into
    `ON_GRID`), and a generic write path would bypass both on the same wire.
    """

    subject: ExtensionSubject
    """The curated device this property hangs off."""

    node_id: str
    """The Homie node, e.g. `battery-2`. Never `info` or `connection`."""

    property_id: str
    """The Homie property, e.g. `cell-temperature`."""

    datatype: str
    """The declared Homie datatype -- `float`, `integer`, `boolean`, `enum`, `string`."""

    unit: str | None = None
    """The declared unit, verbatim. `None` is normal for a `boolean` or an `enum`."""

    format: str | None = None
    """The declared `$format`: an option list for an `enum`, `min:max:step` for a number."""

    settable: bool = False
    """Declaration fact, carried for curation triage.

    Deliberately not paired with a set topic. See the read-only note above: the
    absence of a write member is what makes the ruling structural rather than
    remembered.
    """

    value: str | None = None
    """The retained value as published, unparsed. `None` when declared and never valued."""

    node_has_curated_siblings: bool = False
    """Whether the adapter maps any *other* property of this node to a snapshot field.

    The one bit of the node-to-field mapping worth exporting: a vendor extending
    `meter` is probably extending the meter. Stamped in one pass over knowledge
    the adapter already holds, and it says nothing about *which* fields, so it
    freezes no internals. A weak signal -- Homie nodes are organisational rather
    than editorial -- and advisory only.
    """

    @property
    def path(self) -> str:
        """`{node}/{property}` -- how the capability catalogs spell it."""
        return f"{self.node_id}/{self.property_id}"


@dataclass(frozen=True, slots=True)
class SpanPanelSnapshot:
    """Complete panel state — single point-in-time view."""

    serial_number: str
    firmware_version: str

    # Panel-level power and energy. `None` for the same reason it appears on
    # `SpanCircuitSnapshot`, plus one more that is specific to these six: they
    # are read off the lugs devices, which are resolved by their `direction`
    # property. Until that property arrives there is no lugs device to read at
    # all, so these were the panel's whole import and export fabricated as zero.
    main_relay_state: str
    instant_grid_power_w: float | None
    feedthrough_power_w: float | None
    main_meter_energy_consumed_wh: float | None
    main_meter_energy_produced_wh: float | None
    feedthrough_energy_consumed_wh: float | None
    feedthrough_energy_produced_wh: float | None

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
    wifi_ssid: str | None = None  # v1.0: status/wifi-ssid | flat: core/wifi-ssid
    vendor_cloud: str | None = None  # v2: core/vendor-cloud

    # The enclosure's own build identity, for the device card rather than for an
    # entity. `None` when the panel publishes nothing, never a default string:
    # the consumer has shown its own text since before these were readable, and
    # a default invented here would silently replace it. v1.0 only -- flat
    # declares none of the three.
    vendor_name: str | None = None
    """`info/vendor-name` -- who made the enclosure."""
    model: str | None = None
    """`info/model` -- the enclosure's model designation, e.g. `MAIN_40`.

    The same property `panel_size` is derived from, kept as the string beside
    the derived integer: the size is what circuits are built against, the
    designation is what a device card shows. Spelled `model` to match
    `battery.model`, `pv.model`, `evse.model` and `mid.model`, all of which name
    the same `info/model` property on their own device.
    """
    hardware_version: str | None = None
    """`info/hardware-version` -- the enclosure's board revision.

    `hardware_version` rather than `hw_version`: the snapshot spells fields out,
    and `DeviceInfo(hw_version=...)` is the consumer's abbreviation, not ours.
    """

    # `shed/policy`, v1.0 only: how the panel decides what to shed, and the two
    # SoC thresholds that make its behaviour predictable. The wire carries one
    # `json` document; these are the parsed answer plus the document itself.
    shed_policy: str | None = None
    """`shed/policy` verbatim -- the JSON document as published.

    Kept beside the parsed members rather than discarded once parsed, because
    the document's schema is versioned in its own `$id` and a publisher may ship
    an algorithm this library does not know. The raw string is what lets a
    consumer still show what the panel said instead of showing nothing.
    """
    shed_policy_algorithm: str | None = None
    """The document's `algorithm` member, e.g. `soc-priority.v1`.

    `None` means the property was not published, did not parse, or named no
    algorithm -- to a consumer those are one event: there is nothing to render.
    A *recognised* name and an unrecognised one are both reported here; only the
    thresholds below are gated on recognising it.
    """
    shed_soc_threshold_shed_percent: int | None = None
    """`parameters.soc-threshold-shed` -- SoC percent below which SOC_THRESHOLD circuits shed.

    Populated only from a `soc-priority.v1` document, because it is that
    algorithm's parameter. `0` is a legal threshold, so `None` cannot be
    replaced by a default.
    """
    shed_soc_threshold_release_percent: int | None = None
    """`parameters.soc-threshold-release` -- SoC percent above which shed circuits restore."""

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
    adopted_devices: tuple[AdoptedDevice, ...] = ()
    """Devices on the tree whose type this library models no fields for.

    Empty for every adapter that does not answer the question. schema_0 never
    populates it: flat has no device tree to find an unmodelled device in, and
    panels upgrade to v1.0 and stay there, so adoption operates in the schema
    that is the terminus.

    A defaulted snapshot field rather than a `SchemaAdapter` member, on purpose.
    The protocol derives its required members from itself, so a new member is
    required of every adapter package and invalidates built wheels; a snapshot
    field that defaults empty is additive and costs neither.
    """

    extension_properties: tuple[ExtensionProperty, ...] = ()
    """Properties *modelled* devices declare that no snapshot field carries.

    The other half of vendor extensibility from `adopted_devices` above: that
    one covers a device type nothing models, this one a new property on a device
    something does. Until this existed the second case reached a consumer
    nowhere -- it became a `DiscoveredMetadata` row and stopped at diagnostics.

    Empty is deliberately ambiguous between "none declared" and "this adapter
    predates the field", and a consumer must not try to tell them apart: the
    older-wheel case is the normal partial-upgrade state, because the adapters
    are separately published packages that version independently of this core.
    A defaulted snapshot field rather than a protocol member for exactly the
    reason `adopted_devices` gives -- a required member would fail at
    *discovery*, taking down every install whose adapter lags by one release.

    schema_0 leaves it empty: flat has no device tree to find a declared-but
    -unmapped property in, and panels upgrade to v1.0 and stay there.
    """

    pcs: SpanPcsSnapshot | None = None
    """The enclosure's Power Control System, when it publishes a `pcs` node. v1.0 only.

    `None` follows `mid` for the same reason, and here the capability states the
    rule itself: "absence of the `pcs` node means the device does not run (or
    participate in) a Power Control System". A panel with no PCS and a PCS
    holding zeros are different facts, and only a nullable member can tell them
    apart — every limit in this capture is a legal `0.0`.
    """

    lugs_at_service_entrance: bool = True
    """Whether this enclosure's upstream lugs *are* the utility connection point.

    `False` means something sits between the utility and the main lugs, so the
    lugs measure flow on the panel side of that device while the utility side
    differs by whatever it contributes or absorbs. Two ordinary topologies do
    this: an **upstream DER**, a BESS wired ahead of the main lugs, and an
    **enclosure chain**, where this panel is fed by another panel rather than by
    the service.

    **What it is for.** `instant_grid_power_w` is the upstream lugs'
    `meter/active-power`. On a panel at the service entrance that reading *is*
    grid flow, which is why the field carries that name. On a panel where this is
    `False` it is the panel's own feed, and presenting it as grid power is wrong
    -- `power_flow_grid` is then the only site-level figure. The two will
    legitimately disagree, and without this a consumer seeing them disagree has
    no way to tell a topology from a fault.

    Sourced from the lugs device's `connection/fed-by-device-id`, which the
    specification names as the detection mechanism: `power-flows` 0.3 qualified
    its own negation table to say the `grid` row holds "only where the lugs are
    the utility connection point", and pointed consumers here. The property is
    read by this library already; before this field it was consumed for relative
    position and otherwise discarded, so no consumer could compute this for
    itself.

    Defaults `True`, and the default is a fact rather than an optimism: flat
    firmware predates enclosure chaining and publishes no way to express it, so a
    flat panel's lugs are its service entrance. schema_0 leaves it alone for that
    reason.

    A defaulted snapshot field rather than a `SchemaAdapter` member, for the
    reason `adopted_devices` gives above: the protocol derives its required
    members from itself, so a new member would be required of every adapter
    package and would invalidate built wheels.

    A boolean rather than the intervening device's id, because the id answers a
    question nobody downstream asks. What a consumer needs is whether to trust
    the lugs as grid; naming the device would invite a second, weaker inference
    about *what* is upstream, which the enclosure-chain case cannot support --
    the feeding device is another panel with its own tree, not a child of this
    one.
    """
