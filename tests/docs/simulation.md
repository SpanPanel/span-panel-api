# SPAN Panel API Simulation Mode

## Overview

The SPAN Panel API client provides a simulation mode that allows testing and development without requiring a physical SPAN panel.
This mode uses pre-recorded response fixtures and applies dynamic variations to simulate realistic energy system behavior.

## Test Organization

The simulation mode is comprehensively tested through organized test suites:

### Core Test Files

- `test_simulation_mode.py` - Primary simulation engine functionality and variations
- `test_panel_circuit_alignment.py` - Panel-level and circuit-level data alignment validation
- `test_client_caching.py` - Client cache behavior and hit/miss logic
- `test_client_retry_properties.py` - Retry configuration and property validation
- `test_client_simulation_errors.py` - Simulation engine error conditions
- `test_yaml_validation.py` - YAML configuration validation

### Test Categories

Tests are organized by functionality rather than coverage targets:

- **Simulation Engine**: Core behavior, variations, time-based accumulation
- **Data Alignment**: Panel-circuit power/energy consistency
- **Client Features**: Caching, retry logic, error handling
- **Configuration**: YAML validation and loading

## Enabling Simulation Mode

Initialize the client with `simulation_mode=True`:

### Host Parameter Behavior

**In Simulation Mode**: The `host` parameter becomes the **panel serial number** rather than an IP address:

- Used to identify the simulated panel instance
- Appears in status responses as the panel serial number
- Allows testing with specific serial numbers for different scenarios

**In Live Mode**: The `host` parameter is the actual **IP address** of the physical SPAN panel.

```python
from span_panel_api import SpanPanelClient

# Simulation mode client
client = SpanPanelClient(
    host="SPAN-DEMO-123456",  # Host becomes the panel serial number in simulation mode
    simulation_mode=True
)

async with client:
    status = await client.get_status()
    print(status.system.serial)  # Outputs: "SPAN-DEMO-123456"

# Live mode client (default)
live_client = SpanPanelClient(
    host="192.168.1.100",  # Host is the actual panel IP address
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

### 4. Panel-Circuit Data Alignment

Panel-level data exactly matches the aggregation of circuit-level data:

- **Power Consistency**: Panel grid power equals the sum of all circuit powers
- **Energy Consistency**: Panel energy totals exactly match circuit energy totals
- **Shared Caching**: Both `get_panel_state()` and `get_circuits()` use the same cached dataset to ensure consistency
- **Test Coverage**: Comprehensive tests verify alignment in `test_panel_circuit_alignment.py`

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

### 1. Panel-Circuit Data Alignment

```python
# Test power alignment between panel and circuit totals
client = SpanPanelClient(
    host="test-alignment",
    simulation_mode=True,
    simulation_config_path="examples/simple_test_config.yaml"
)

async with client:
    panel_state = await client.get_panel_state()
    circuits = await client.get_circuits()

    # Calculate circuit power total
    total_circuit_power = sum(
        circuits.circuits[cid].instant_power_w
        for cid in circuits.circuits.additional_keys
    )

    # Verify alignment (current tolerance: 2000W due to known issues)
    power_diff = abs(panel_state.instant_grid_power_w - total_circuit_power)
    assert power_diff <= 2000.0
```

### 2. Energy Accumulation Testing

```python
# Test energy totals alignment
async with client:
    panel_state = await client.get_panel_state()
    circuits = await client.get_circuits()

    total_produced = sum(
        circuits.circuits[cid].produced_energy_wh
        for cid in circuits.circuits.additional_keys
    )
    total_consumed = sum(
        circuits.circuits[cid].consumed_energy_wh
        for cid in circuits.circuits.additional_keys
    )

    # Document current behavior (should be exact in ideal implementation)
    print(f"Panel produced: {panel_state.main_meter_energy.produced_energy_wh}")
    print(f"Circuit total: {total_produced}")
```

### 3. Circuit Failure Testing

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

### 4. Caching Behavior Testing

```python
# Test cache hit/miss behavior
client = SpanPanelClient(
    host="test-cache",
    simulation_mode=True,
    cache_window=5.0  # 5-second cache window
)

async with client:
    # First call - cache miss
    circuits1 = await client.get_circuits()

    # Second call within cache window - cache hit
    circuits2 = await client.get_circuits()

    # Verify identical data from cache
    assert circuits1 == circuits2
```

### 5. Panel Emergency States

```python
# Simulate grid outage
panel = await client.get_panel_state(panel_variations={
    "main_relay_state": "OPEN",
    "dsm_state": "DSM_OFF_GRID",
    "dsm_grid_state": "DSM_GRID_DOWN"
})
```

### 6. High Load Scenarios

```python
# Simulate high power consumption
circuits = await client.get_circuits(variations={
    "ev_charger_1": {"power_variation": 1.0},  # 100% variation
    "ev_charger_2": {"power_variation": 1.0},
    "hvac_system": {"power_variation": 0.8}
})
```

### 7. Network and Hardware Issues

```python
# Simulate connectivity problems
status = await client.get_status(variations={
    "eth0_link": False,
    "wlan_link": False,
    "door_state": "OPEN"
})
```

## Live Mode Behavior

When `simulation_mode=False` (default):

- The `host` parameter is used as the actual IP address of the physical SPAN panel
- All variation parameters are completely ignored (calls go to real hardware)
- Data comes directly from the physical panel, not simulation fixtures

```python
live_client = SpanPanelClient(host="192.168.1.100", simulation_mode=False)

# These variations are ignored in live mode - data comes from real panel
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

## Known Issues and Limitations

### 1. ✅ Panel-Circuit Data Alignment (FIXED)

**Current Behavior**: Panel and circuit data are now properly aligned:

- **Power Alignment**: Panel grid power exactly matches the sum of all circuit powers
- **Energy Alignment**: Panel energy totals exactly match the sum of all circuit energies
- **Consistency**: Single data generation call with shared caching ensures consistency
- **Implementation**: Both `get_panel_state()` and `get_circuits()` use the same cached dataset

**Test Coverage**: All alignment tests in `test_panel_circuit_alignment.py` verify this behavior

### 2. Cache Timing Dependencies

Some tests may be sensitive to cache timing, particularly when testing data consistency across multiple API calls.

## Limitations

- Simulation data is based on static fixtures with dynamic variations applied
- No actual hardware interaction
- Circuit state changes don't persist between client instances
- **Timestamp Consistency**: Minor timestamp variations may occur between API calls (but data consistency is maintained via shared caching)
