"""Transport-agnostic snapshot models for SPAN Panel state.

These dataclasses represent panel state regardless of how it was obtained
(REST polling or MQTT push). Energy and power sign conventions are
normalized at the transport boundary — consumers see a consistent view.

All snapshots are immutable (frozen) and memory-efficient (slots).
"""

from __future__ import annotations

from dataclasses import dataclass, field


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


@dataclass(frozen=True, slots=True)
class SpanPVSnapshot:
    """PV inverter metadata — populated only when a PV node is commissioned."""

    vendor_name: str | None = None  # pv/vendor-name
    product_name: str | None = None  # pv/product-name
    nameplate_capacity_kw: float | None = None  # pv/nameplate-capacity (kW)


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
    product_name: str | None = None
    part_number: str | None = None
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
    product_name: str | None = None  # bess/product-name
    model: str | None = None  # bess/model
    serial_number: str | None = None  # bess/serial-number
    software_version: str | None = None  # bess/software-version
    nameplate_capacity_kwh: float | None = None  # bess/nameplate-capacity (kWh)
    connected: bool | None = None  # bess/connected


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


@dataclass(frozen=True, slots=True)
class V2HomieSchema:
    """Response from GET /api/v2/homie/schema."""

    firmware_version: str
    types_schema_hash: str  # SHA-256, first 16 hex chars
    types: dict[str, dict[str, object]]  # {type_name: {prop_name: {attr: value}}}


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
    dsm_grid_state: str  # v1: direct | v2: multi-signal heuristic
    current_run_config: str  # v1: direct | v2: tri-state from grid_state + islandable + DPS

    # Hardware status — v1 field names preserved
    door_state: str  # v1: direct | v2: core/door
    proximity_proven: bool  # v1: proximity sensor | v2: MQTT auth + $state==ready
    uptime_s: int  # v1: panel uptime | v2: connection uptime since $state==ready
    eth0_link: bool  # v1: direct | v2: core/ethernet
    wlan_link: bool  # v1: direct | v2: core/wifi
    wwan_link: bool  # v1: direct | v2: vendor-cloud == "CONNECTED"

    # v2-native fields — None for REST transport
    dominant_power_source: str | None = None  # v2: core/dominant-power-source (settable)
    grid_state: str | None = None  # v2: bess/grid-state (None = no BESS or REST)
    grid_islandable: bool | None = None  # v2: core/grid-islandable
    l1_voltage: float | None = None  # v2: core/l1-voltage (V)
    l2_voltage: float | None = None  # v2: core/l2-voltage (V)
    main_breaker_rating_a: int | None = None  # v2: core/breaker-rating (A)
    wifi_ssid: str | None = None  # v2: core/wifi-ssid
    vendor_cloud: str | None = None  # v2: core/vendor-cloud
    panel_size: int | None = None  # v2: core/panel-size (total breaker spaces)

    # Power flows (None when node not present)
    power_flow_pv: float | None = None  # v2: power-flows/pv (W)
    power_flow_battery: float | None = None  # v2: power-flows/battery (W)
    power_flow_grid: float | None = None  # v2: power-flows/grid (W)
    power_flow_site: float | None = None  # v2: power-flows/site (W)

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
    evse: dict[str, SpanEvseSnapshot] = field(default_factory=dict)  # keyed by node_id
