# SPAN Panel API Simulation Mode

## Overview

The SPAN Panel API client provides a simulation mode that allows testing and development without requiring a physical SPAN panel.
This mode uses pre-recorded response fixtures and applies dynamic variations to simulate realistic energy system behavior.

## Enabling Simulation Mode

Initialize the client with `simulation_mode=True`:

```python
from span_panel_api import SpanPanelClient

# Simulation mode client
client = SpanPanelClient(
    host="localhost",  # Host is ignored in simulation mode
    simulation_mode=True
)

# Live mode client (default)
live_client = SpanPanelClient(
    host="192.168.1.100",
    simulation_mode=False  # Default
)
```

## Core Features

### 1. Time-Based Energy Accumulation

Energy values (`consumedEnergyWh`, `producedEnergyWh`) automatically accumulate over time based on current power consumption, just like a real energy system.

### 2. Dynamic Power Fluctuation

Power values (`instantPowerW`) fluctuate realistically based on appliance type:

- **EV Chargers**: High power when charging, zero when idle
- **HVAC Systems**: Cyclical on/off behavior
- **Lights**: Stable power when on
- **Refrigerators**: Compressor cycling patterns

### 3. Per-Item Variation Control

Each circuit, branch, or status field can be individually controlled with specific variations.

## API Methods and Variations

### Circuits API

```python
async def get_circuits(
    variations: dict[str, CircuitVariation] | None = None,
    global_power_variation: float | None = None,
    global_energy_variation: float | None = None
) -> CircuitsOut
```

#### CircuitVariation Structure

```python
class CircuitVariation(TypedDict, total=False):
    power_variation: float      # Percentage variation (0.1 = 10%)
    energy_variation: float     # Percentage variation for energy values
    relay_state: str           # "OPEN" or "CLOSED"
    priority: str              # "MUST_HAVE", "NICE_TO_HAVE", "NON_ESSENTIAL"
```

#### Examples

```python
# Global variations applied to all circuits
circuits = await client.get_circuits(
    global_power_variation=0.2,  # 20% power variation
    global_energy_variation=0.1  # 10% energy variation
)

# Per-circuit specific variations
circuit_variations = {
    "0dad2f16cd514812ae1807b0457d473e": {  # Lights Dining Room
        "power_variation": 0.05,
        "relay_state": "OPEN"
    },
    "617059df47bb49bd8545a36a6b6b23d2": {  # Spa
        "power_variation": 0.5,
        "priority": "NON_ESSENTIAL"
    }
}

circuits = await client.get_circuits(variations=circuit_variations)

# Mixed approach: global + specific overrides
circuits = await client.get_circuits(
    variations={
        "specific_circuit_id": {"power_variation": 0.8}
    },
    global_power_variation=0.2  # Applied to all other circuits
)
```

### Panel State API

```python
async def get_panel_state(
    variations: dict[int, BranchVariation] | None = None,
    panel_variations: PanelVariation | None = None,
    global_power_variation: float | None = None
) -> PanelState
```

#### Variation Structures

```python
class BranchVariation(TypedDict, total=False):
    power_variation: float      # Percentage variation for branch power
    relay_state: str           # "OPEN" or "CLOSED"

class PanelVariation(TypedDict, total=False):
    main_relay_state: str      # "OPEN" or "CLOSED"
    dsm_grid_state: str        # "DSM_GRID_UP" or "DSM_GRID_DOWN"
    dsm_state: str             # "DSM_ON_GRID" or "DSM_OFF_GRID"
    instant_grid_power_variation: float  # Grid power variation
```

#### Code Examples

```python
# Panel-level variations
panel_variations = {
    "main_relay_state": "OPEN",
    "dsm_grid_state": "DSM_GRID_DOWN"
}

# Branch-specific variations
branch_variations = {
    1: {"power_variation": 0.3, "relay_state": "OPEN"},
    5: {"power_variation": 0.8}
}

panel = await client.get_panel_state(
    variations=branch_variations,
    panel_variations=panel_variations
)
```

### Status API

```python
async def get_status(
    variations: StatusVariation | None = None
) -> StatusOut
```

#### StatusVariation Structure

```python
class StatusVariation(TypedDict, total=False):
    door_state: str           # "OPEN", "CLOSED", "UNKNOWN"
    main_relay_state: str     # "OPEN", "CLOSED"
    proximity_proven: bool
    eth0_link: bool
    wlan_link: bool
    wwwan_link: bool
```

#### Dynamic Simulation Examples

```python
# Simulate door open and network issues
status_variations = {
    "door_state": "OPEN",
    "eth0_link": False,
    "proximity_proven": True
}

status = await client.get_status(variations=status_variations)
```

### Storage State of Energy API

```python
async def get_storage_soe(
    soe_variation: float | None = None
) -> BatteryStorage
```

#### Example

```python
# Simulate battery level variation
storage = await client.get_storage_soe(soe_variation=0.1)  # 10% variation
```

## Testing Scenarios

### 1. Circuit Failure Testing

```python
# Test individual circuit outage
circuits = await client.get_circuits(variations={
    "internet_circuit_id": {"relay_state": "OPEN"}
})

# Test multiple circuit failures
circuits = await client.get_circuits(variations={
    "circuit_1": {"relay_state": "OPEN"},
    "circuit_2": {"relay_state": "OPEN", "power_variation": 0.0}
})
```

### 2. Panel Emergency States

```python
# Simulate grid outage
panel = await client.get_panel_state(panel_variations={
    "main_relay_state": "OPEN",
    "dsm_state": "DSM_OFF_GRID",
    "dsm_grid_state": "DSM_GRID_DOWN"
})
```

### 3. High Load Scenarios

```python
# Simulate high power consumption
circuits = await client.get_circuits(variations={
    "ev_charger_1": {"power_variation": 1.0},  # 100% variation
    "ev_charger_2": {"power_variation": 1.0},
    "hvac_system": {"power_variation": 0.8}
})
```

### 4. Network and Hardware Issues

```python
# Simulate connectivity problems
status = await client.get_status(variations={
    "eth0_link": False,
    "wlan_link": False,
    "door_state": "OPEN"
})
```

## Live Mode Behavior

When `simulation_mode=False` (default), all variation parameters are completely ignored:

```python
live_client = SpanPanelClient(host="192.168.1.100", simulation_mode=False)

# These variations are ignored in live mode
circuits = await live_client.get_circuits(variations={
    "any_circuit": {"power_variation": 999}  # Completely ignored
})
```

## Home Assistant Integration

The simulation mode is particularly useful for Home Assistant integration testing:

```python
# Test energy dashboard with varying loads
circuits = await client.get_circuits(global_power_variation=0.3)

# Test automation triggers for circuit failures
circuits = await client.get_circuits(variations={
    "critical_circuit": {"relay_state": "OPEN"}
})

# Test battery monitoring
storage = await client.get_storage_soe(soe_variation=0.2)
```

## Best Practices

### 1. Realistic Variation Ranges

- **Power Variation**: 0.1-0.5 (10%-50%) for most appliances
- **Energy Variation**: 0.05-0.2 (5%-20%) for gradual changes
- **EV Chargers**: 0.8-1.0 (80%-100%) for high variability

### 2. Circuit-Specific Behavior

- **Lights**: Low variation (0.05-0.1)
- **HVAC**: Medium variation (0.3-0.5) with on/off cycles
- **EV Chargers**: High variation (0.8-1.0) with frequent on/off
- **Refrigerators**: Moderate variation (0.2-0.3) for compressor cycling

### 3. Testing Patterns

```python
# Start with baseline data
baseline = await client.get_circuits()

# Apply specific test scenario
test_circuits = await client.get_circuits(variations={
    "target_circuit": {"relay_state": "OPEN"}
})

# Verify expected behavior
assert test_circuits.circuits["target_circuit"].relay_state == "OPEN"
```

## Fixture Data

The simulation mode uses pre-recorded response fixtures from real SPAN panels:

- `tests/simulation_fixtures/circuits.response.txt`
- `tests/simulation_fixtures/panel.response.txt`
- `tests/simulation_fixtures/status.response.txt`
- `tests/simulation_fixtures/soe.response.txt`

These fixtures provide realistic baseline data that gets modified by the simulation engine.

## Error Handling

Simulation mode maintains the same error handling patterns as live mode:

```python
try:
    circuits = await client.get_circuits()
except SpanPanelAPIError as e:
    # Handle API errors (can still occur in simulation)
    pass
```

## Performance Considerations

- Simulation mode is faster than live API calls
- Energy accumulation calculations are lightweight
- Fixture data is loaded once at initialization
- Variation calculations are applied per-call

## Limitations

- Simulation data is based on static fixtures
- No actual hardware interaction
- Authentication is bypassed in simulation mode
- Circuit state changes don't persist between client instances
