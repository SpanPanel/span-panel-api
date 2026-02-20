"""Low-level data models for Gen3 gRPC panel data.

These models represent the raw gRPC-layer data structures — circuit topology
discovered via GetInstances and real-time metrics from the Subscribe stream.
The higher-level SpanPanelSnapshot / SpanCircuitSnapshot models (in
span_panel_api.models) are the transport-agnostic view built from these.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class CircuitInfo:
    """Static information about a circuit discovered from trait instances."""

    circuit_id: int
    name: str
    metric_iid: int  # trait 26 IID — used to match Subscribe stream notifications
    name_iid: int = 0  # trait 16 IID — used for GetRevision name lookups
    is_dual_phase: bool = False
    breaker_position: int = 0  # physical slot number (1-48) in the panel


@dataclass
class CircuitMetrics:
    """Real-time power metrics for a circuit from the gRPC Subscribe stream."""

    power_w: float = 0.0
    voltage_v: float = 0.0
    current_a: float = 0.0
    apparent_power_va: float = 0.0
    reactive_power_var: float = 0.0
    frequency_hz: float = 0.0
    power_factor: float = 0.0
    is_on: bool = True
    # Dual-phase per-leg values
    voltage_a_v: float = 0.0
    voltage_b_v: float = 0.0
    current_a_a: float = 0.0
    current_b_a: float = 0.0


@dataclass
class PanelData:
    """Aggregated panel data from gRPC discovery and streaming."""

    serial: str = ""
    firmware: str = ""
    panel_resource_id: str = ""
    circuits: dict[int, CircuitInfo] = field(default_factory=dict)
    metrics: dict[int, CircuitMetrics] = field(default_factory=dict)
    main_feed: CircuitMetrics = field(default_factory=CircuitMetrics)
