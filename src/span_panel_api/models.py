"""Unified data models for SPAN Panel transports.

These models provide a transport-agnostic view of panel state, satisfiable
by both Gen2 (OpenAPI/HTTP) and Gen3 (gRPC) clients.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Flag, StrEnum, auto


class PanelGeneration(StrEnum):
    """Identifies which panel hardware generation a client connects to."""

    GEN2 = "gen2"
    GEN3 = "gen3"


class PanelCapability(Flag):
    """Bitmask of features a panel transport implementation supports.

    Use these flags at setup time to enable/disable entity platforms:

        caps = client.capabilities
        if PanelCapability.RELAY_CONTROL in caps:
            platforms.append("switch")
        if PanelCapability.BATTERY in caps:
            platforms.append("battery_sensor")
    """

    NONE = 0
    RELAY_CONTROL = auto()  # Can open/close circuit relays (switch entities)
    PRIORITY_CONTROL = auto()  # Can set circuit load priorities (select entities)
    ENERGY_HISTORY = auto()  # Reports Wh accumulation data
    BATTERY = auto()  # Exposes battery/storage state of energy
    AUTHENTICATION = auto()  # Supports/requires JWT auth
    SOLAR = auto()  # Has solar inverter / feedthrough tab data
    DSM_STATE = auto()  # Demand-side management state
    HARDWARE_STATUS = auto()  # Door state, detailed hardware info
    PUSH_STREAMING = auto()  # Delivers push updates via callback

    # Convenience composites
    GEN2_FULL = (
        RELAY_CONTROL | PRIORITY_CONTROL | ENERGY_HISTORY | BATTERY | AUTHENTICATION | SOLAR | DSM_STATE | HARDWARE_STATUS
    )
    GEN3_INITIAL = PUSH_STREAMING  # Expand as Gen3 API matures


@dataclass
class SpanCircuitSnapshot:
    """Transport-agnostic snapshot of a single circuit's state and metrics."""

    circuit_id: str
    name: str
    power_w: float
    voltage_v: float
    current_a: float
    is_on: bool
    # Gen2-only (None for Gen3)
    relay_state: str | None = None
    priority: str | None = None
    tabs: list[int] | None = None
    energy_produced_wh: float | None = None
    energy_consumed_wh: float | None = None
    # Gen3-only
    apparent_power_va: float | None = None
    reactive_power_var: float | None = None
    frequency_hz: float | None = None
    power_factor: float | None = None
    is_dual_phase: bool = False


@dataclass
class SpanPanelSnapshot:
    """Transport-agnostic snapshot of the full panel state.

    Fields that are None were not reported by the transport (e.g. Gen3 does
    not report energy history, battery SOE, or DSM state).
    """

    panel_generation: PanelGeneration
    serial_number: str = ""
    firmware_version: str = ""
    circuits: dict[str, SpanCircuitSnapshot] = field(default_factory=dict)
    main_power_w: float = 0.0
    # Gen2-only
    main_relay_state: str | None = None
    grid_power_w: float | None = None
    battery_soe: float | None = None
    dsm_state: str | None = None
    dsm_grid_state: str | None = None
    # Gen3-only
    main_voltage_v: float | None = None
    main_current_a: float | None = None
    main_frequency_hz: float | None = None
