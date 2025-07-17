# SPAN Panel API Example Configurations

This directory contains example YAML configurations for the SPAN Panel API simulation system. These configurations are used both for demonstrations and as test fixtures to ensure consistent behavior across the test suite.

## Configuration Files

### Basic Configurations

- **`minimal_config.yaml`** - Minimal working configuration with basic settings
- **`simple_test_config.yaml`** - Simple configuration for general testing
- **`validation_test_config.yaml`** - Basic configuration for validation testing and error scenarios

### Specialized Configurations

- **`battery_test_config.yaml`** - Battery system with bidirectional energy flow for testing battery behavior
- **`producer_test_config.yaml`** - Producer energy profiles (solar, generators) for testing energy generation
- **`behavior_test_config.yaml`** - Demonstrates cycling, time-of-day, and smart behaviors
- **`error_test_config.yaml`** - Minimal configuration for error testing scenarios

### Workshop and Demo Configurations

- **`simulation_config_8_tab_workshop.yaml`** - 8-tab panel configuration for workshop demonstrations
- **`simulation_config_32_circuit.yaml`** - 32-circuit panel with tab synchronization and unmapped tabs
- **`simulation_config_40_circuit_with_battery.yaml`** - Large panel configuration with battery storage

## Test Integration

These configurations are designed to be used as test fixtures, ensuring that:

1. **Tests use real configurations** - No scattered embedded YAML data in test files
2. **Examples are validated** - Using configurations in tests ensures they work correctly
3. **Consistency** - All tests use the same known-good configurations
4. **Maintainability** - Changes to configuration format only need to be made in example files

## Usage in Tests

Tests import configurations like this:

```python
import yaml
from pathlib import Path

config_path = Path(__file__).parent.parent / "examples" / "validation_test_config.yaml"
with open(config_path) as f:
    config = yaml.safe_load(f)
```

## Energy Profile Templates

The configurations demonstrate various energy profile types:

- **Consumer profiles** - Standard electrical loads (lights, appliances)
- **Producer profiles** - Energy generation (solar, generators, wind)
- **Bidirectional profiles** - Battery storage systems
- **Time-of-day patterns** - Solar production and lighting schedules
- **Cycling behaviors** - HVAC and appliance cycling
- **Smart behaviors** - Grid-responsive loads

## Demo Scripts

Several demo scripts use these configurations:

- `clean_api_demo.py` - Clean API demonstration
- `simulation_demo.py` - Full simulation features
- `battery_behavior_demo.py` - Battery-specific behaviors
- `test_multi_energy_sources.py` - Multiple energy source types
- `test_simulation_time.py` - Time control features
