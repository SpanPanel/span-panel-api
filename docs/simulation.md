# SPAN Panel API Simulation Mode

## Overview

The SPAN Panel API client includes a simulation mode for testing without a physical SPAN panel.
The simulation engine loads YAML configuration files that define circuit templates, energy profiles, and behaviors,
then generates realistic power and energy data based on these profiles.

## Configuration-Based Simulation

The simulation uses YAML configuration files instead of pre-recorded fixtures. This allows you to define specific energy behaviors, production sources, and consumption patterns.

### Basic Usage

```python
from pathlib import Path
from span_panel_api.client import SpanPanelClient

config_path = Path("examples/simulation_config_32_circuit.yaml")

async with SpanPanelClient(
    host="demo-panel-001",
    simulation_mode=True,
    simulation_config_path=str(config_path)
) as client:
    circuits = await client.get_circuits()
    panel = await client.get_panel_state()
    status = await client.get_status()
```

### Host Parameter in Simulation Mode

When `simulation_mode=True`, the `host` parameter becomes the panel serial number instead of an IP address.
This serial number appears in status responses and allows testing with specific panel identifiers.

```python
# Simulation mode - host becomes serial number
client = SpanPanelClient(
    host="span-demo-1357911",  # host field serves as the simulation serial number override instead of YAML
    simulation_mode=True
)

# Live mode - host is IP address
client = SpanPanelClient(
    host="192.168.1.100",
    simulation_mode=False
)
```

## YAML Configuration Structure

### Circuit Templates

Define reusable energy profiles for different types of equipment:

```yaml
circuit_templates:
  residential_solar:
    energy_profile:
      mode: "producer"              # consumer, producer, bidirectional
      power_range: [-2000.0, 0.0]   # Watts (negative = production)
      typical_power: -1200.0        # Typical output in Watts
      power_variation: 0.3          # 30% variation around typical
    relay_behavior: "non_controllable"
    priority: "MUST_HAVE"

  ev_charger:
    energy_profile:
      mode: "consumer"
      power_range: [0.0, 11000.0]
      typical_power: 7200.0
      power_variation: 0.8          # High variation (on/off charging)
    relay_behavior: "controllable"
    priority: "NICE_TO_HAVE"
```

### Circuit Definitions

Map specific circuits to templates:

```yaml
circuits:
  - id: "solar_inverter_main"
    name: "Solar Inverter Main"
    tabs: [1, 3]                    # Physical panel tabs
    template: "residential_solar"

  - id: "ev_charger_garage"
    name: "EV Charger - Garage"
    tabs: [15]
    template: "ev_charger"
```

### Tab Synchronization

Coordinate multiple tabs for 240V systems or multi-phase equipment:

```yaml
tab_synchronizations:
  - tabs: [30, 32]                  # Tab numbers to synchronize
    behavior: "240v_split_phase"    # Description of coordination
    power_split: "equal"            # equal, primary_secondary
    energy_sync: true               # Share energy accumulation
    template: "residential_solar"   # Template to apply
```

### Unmapped Tabs

Define tabs that don't correspond to named circuits:

```yaml
unmapped_tab_templates:
  solar_production:
    energy_profile:
      mode: "producer"
      power_range: [-1000.0, 0.0]
      typical_power: -600.0
      power_variation: 0.2

# Tabs 30 and 32 will use solar_production template via tab_synchronizations
```

## Energy Profile Types

### Producer (Solar, Generator, Wind)

```yaml
energy_profile:
  mode: "producer"
  power_range: [-2000.0, 0.0]      # Negative values = production
  typical_power: -1200.0           # Negative = producing power
  power_variation: 0.3             # Varies with weather/fuel
```

### Consumer (Loads, Appliances)

```yaml
energy_profile:
  mode: "consumer"
  power_range: [0.0, 1500.0]       # Positive values = consumption
  typical_power: 800.0             # Positive = consuming power
  power_variation: 0.2             # Usage patterns
```

### Bidirectional (Battery Storage)

```yaml
energy_profile:
  mode: "bidirectional"
  power_range: [-5000.0, 5000.0]   # Can charge or discharge
  typical_power: 0.0               # Neutral when idle
  power_variation: 0.4             # Charging/discharging cycles
```

## Time-Based Simulation

### Simulation Time Control

Override the simulation start time programmatically:

```python
from datetime import datetime

async with SpanPanelClient(
    host="time-test-panel",
    simulation_mode=True,
    simulation_config_path=str(config_path),
    simulation_start_time=datetime(2025, 6, 15, 12, 0, 0)  # Noon on June 15
) as client:
    # Simulation operates at specified time instead of system clock
    circuits = await client.get_circuits()
```

### Time-Dependent Behaviors

Energy profiles can vary based on time of day. The simulation engine calculates power output based on the current simulation time:

- Solar production follows daylight patterns
- HVAC usage varies with temperature cycles
- Lighting follows occupancy patterns

## Dynamic Overrides

Temporarily modify circuit behavior for testing scenarios:

### Circuit-Specific Overrides

```python
# Override specific circuit power and state
await client.set_circuit_overrides({
    "ev_charger_garage": {
        "power_override": 11000.0,      # Maximum charging power
        "relay_state": "CLOSED"
    }
})

# Clear overrides to return to YAML behavior
await client.clear_circuit_overrides()
```

### Global Overrides

```python
# Apply multiplier to all circuit power values
await client.set_circuit_overrides(global_overrides={
    "power_multiplier": 2.0         # Double all power consumption
})
```

## Energy Accumulation

### Automatic Energy Tracking

The simulation automatically calculates accumulated energy based on power consumption over time:

```text
Energy (Wh) = Power (W) × Time (hours)
```

- `consumedEnergyWh`: Accumulates when power is positive (consumption)
- `producedEnergyWh`: Accumulates when power is negative (production)
- Energy values increase monotonically over the simulation lifetime

### Synchronized Energy for Multi-Tab Systems

When `energy_sync: true` in tab synchronizations, all tabs in the group share the same energy accumulation state. This prevents double-counting energy across phases of the same 240V system.

## API Methods

### Basic Data Retrieval

```python
# Get all circuit data
circuits = await client.get_circuits()

# Get panel-level data
panel = await client.get_panel_state()

# Get system status
status = await client.get_status()

# Get battery state of energy
storage = await client.get_storage_soe()
```

### Data Consistency

Panel-level data automatically aggregates from circuit-level data:

- Panel grid power = sum of all circuit powers
- Panel energy totals = sum of all circuit energies
- Both APIs use the same cached dataset to ensure consistency

## Testing Scenarios

### Energy Production Testing

```python
# Test solar production at different times
config_path = Path("examples/simulation_config_32_circuit.yaml")

# Dawn - low production
async with SpanPanelClient(
    host="solar-test",
    simulation_mode=True,
    simulation_config_path=str(config_path),
    simulation_start_time=datetime(2025, 6, 15, 6, 0, 0)
) as client:
    circuits = await client.get_circuits()
    solar_power = circuits.circuits["solar_inverter_main"].instant_power_w
    print(f"Dawn solar: {solar_power}W")

# Noon - peak production
async with SpanPanelClient(
    host="solar-test",
    simulation_mode=True,
    simulation_config_path=str(config_path),
    simulation_start_time=datetime(2025, 6, 15, 12, 0, 0)
) as client:
    circuits = await client.get_circuits()
    solar_power = circuits.circuits["solar_inverter_main"].instant_power_w
    print(f"Noon solar: {solar_power}W")
```

### Tab Synchronization Testing

```python
# Verify synchronized tabs show coordinated behavior
circuits = await client.get_circuits()

# Check unmapped tabs 30 and 32 (configured as synchronized solar)
tabs_data = await client.get_panel_state()
tab_30_power = tabs_data.branches[30].instant_power_w
tab_32_power = tabs_data.branches[32].instant_power_w

# Both tabs should show equal power (split-phase solar)
assert abs(tab_30_power - tab_32_power) < 1.0  # Should be nearly identical
```

### Circuit Failure Testing

```python
# Test individual circuit outage
await client.set_circuit_overrides({
    "ev_charger_garage": {"relay_state": "OPEN"}
})

circuits = await client.get_circuits()
assert circuits.circuits["ev_charger_garage"].relay_state == "OPEN"
assert circuits.circuits["ev_charger_garage"].instant_power_w == 0.0
```

### High Load Scenarios

```python
# Simulate peak consumption
await client.set_circuit_overrides(global_overrides={
    "power_multiplier": 3.0  # Triple all loads
})

panel = await client.get_panel_state()
print(f"Peak grid power: {panel.instant_grid_power_w}W")
```

## Error Handling and Exceptions

The SPAN Panel API client raises specific exceptions for different error conditions. All exceptions inherit from `SpanPanelError`.

### Public Exception Types

- `AuthenticationError`: Raised for authentication failures (invalid token, login required, etc.)
- `ConnectionError`: Raised for network errors, server errors, or API errors
- `TimeoutError`: Raised when a request times out
- `ValidationError`: Raised for data validation errors (invalid input, schema mismatch)
- `SimulationConfigurationError`: Raised for invalid or missing simulation configuration (see below)

### Example: Handling Authentication and Connection Errors

```python
from span_panel_api import SpanPanelClient, AuthenticationError, ConnectionError, TimeoutError, ValidationError

try:
    async with SpanPanelClient(host="192.168.1.100") as client:
        circuits = await client.get_circuits()
except AuthenticationError as e:
    print(f"Authentication failed: {e}")
except ConnectionError as e:
    print(f"Connection or API error: {e}")
except TimeoutError as e:
    print(f"Request timed out: {e}")
except ValidationError as e:
    print(f"Validation error: {e}")
```

## Error Conditions

### SimulationConfigurationError

The simulation engine raises `SimulationConfigurationError` when configuration is invalid or missing required data for simulation features.

```python
from span_panel_api import SimulationConfigurationError

try:
    client = SpanPanelClient(
        host="test",
        simulation_mode=True,
        simulation_config_path="invalid_config.yaml"
    )
    await client.initialize_async()
except SimulationConfigurationError as e:
    print(f"Simulation configuration error: {e}")
```

### Configuration Validation

The simulation engine validates YAML configuration on startup and raises `SimulationConfigurationError` for:

- Missing or invalid simulation configuration
- Invalid simulation start time format
- Missing circuits in configuration
- Missing tab synchronization configuration when energy sync is requested
- Invalid tab synchronization setup

### Missing Templates

```yaml
circuits:
  - id: "test_circuit"
    template: "nonexistent_template"  # Will raise SimulationConfigurationError
```

### Invalid Energy Profiles

```yaml
circuit_templates:
  invalid_template:
    energy_profile:
      mode: "invalid_mode"  # Must be consumer, producer, or bidirectional
```

### Invalid Simulation Time

```python
# Invalid time format will raise SimulationConfigurationError
client.override_simulation_start_time("invalid-time-format")
```

### Missing Tab Synchronization

When energy synchronization is requested for a tab but no synchronization configuration is found:

```python
# This will raise SimulationConfigurationError if tab 33 has no sync config
# but energy_sync is requested
produced, consumed = engine._synchronize_energy_for_tab(33, "test_circuit", 100.0, time.time())
```

## Performance Characteristics

- Configuration loading occurs once at client initialization
- Energy calculations are performed per API call
- Power variations use lightweight random number generation
- Memory usage scales with number of defined circuits and tabs

## Live Mode vs Simulation Mode

### Live Mode (`simulation_mode=False`)

- `host` parameter is IP address of physical panel
- All data comes from actual hardware
- Override methods have no effect
- Configuration parameters are ignored

### Simulation Mode (`simulation_mode=True`)

- `host` parameter becomes panel serial number
- Data generated from YAML configuration
- Override methods modify simulated behavior
- No network communication with physical hardware

## Configuration Examples

### Minimal Configuration

```yaml
panel_config:
  serial: "SPAN-SIM-001"
  model: "SPAN-PANEL-32"
  max_amps: 200

circuit_templates:
  basic_load:
    energy_profile:
      mode: "consumer"
      power_range: [0.0, 1000.0]
      typical_power: 500.0
      power_variation: 0.1
    relay_behavior: "controllable"
    priority: "NICE_TO_HAVE"

circuits:
  - id: "test_circuit"
    name: "Test Load"
    tabs: [1]
    template: "basic_load"

simulation_params:
  update_interval: 5
  time_acceleration: 1.0
  noise_factor: 0.02
  enable_realistic_behaviors: true
```

### Multi-Source Energy Configuration

```yaml
circuit_templates:
  solar_array:
    energy_profile:
      mode: "producer"
      power_range: [-8000.0, 0.0]
      typical_power: -5000.0
      power_variation: 0.4

  backup_generator:
    energy_profile:
      mode: "producer"
      power_range: [-12000.0, 0.0]
      typical_power: -8000.0
      power_variation: 0.1

  battery_storage:
    energy_profile:
      mode: "bidirectional"
      power_range: [-10000.0, 10000.0]
      typical_power: 0.0
      power_variation: 0.3

circuits:
  - id: "solar_main"
    template: "solar_array"
    tabs: [1, 3]

  - id: "generator_main"
    template: "backup_generator"
    tabs: [5, 7]

  - id: "battery_inverter"
    template: "battery_storage"
    tabs: [9, 11]
```
