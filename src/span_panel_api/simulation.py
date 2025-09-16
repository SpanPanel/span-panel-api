"""SPAN Panel API Simulation Engine.

This module provides dynamic simulation capabilities for the SPAN Panel API client,
allowing realistic testing without requiring physical hardware.
"""

from __future__ import annotations

import asyncio
from copy import deepcopy
from datetime import datetime
import math
from pathlib import Path
import random
import threading
import time
from typing import Any, NotRequired, TypedDict

import yaml

from span_panel_api.const import DSM_GRID_UP, DSM_ON_GRID, MAIN_RELAY_CLOSED, PANEL_ON_GRID
from span_panel_api.exceptions import SimulationConfigurationError


# New YAML configuration types
class PanelConfig(TypedDict):
    """Panel configuration."""

    serial_number: str
    total_tabs: int
    main_size: int  # Main breaker size in Amps


class CyclingPattern(TypedDict, total=False):
    """Cycling behavior configuration."""

    on_duration: int  # Seconds
    off_duration: int  # Seconds


class TimeOfDayProfile(TypedDict, total=False):
    """Time-based behavior configuration."""

    enabled: bool
    peak_hours: list[int]  # Hours of day for peak activity
    hour_factors: dict[int, float]  # Hour-specific production factors
    production_hours: list[int]  # Hours when solar should produce
    night_hours: list[int]  # Hours when solar should not produce
    peak_factor: float  # Peak production factor


class SmartBehavior(TypedDict, total=False):
    """Smart load behavior configuration."""

    responds_to_grid: bool
    max_power_reduction: float  # 0.0 to 1.0


class EnergyProfile(TypedDict):
    """Energy profile defining production/consumption behavior."""

    mode: str  # "consumer", "producer", "bidirectional"
    power_range: list[float]  # [min, max] in Watts (negative for production)
    typical_power: float  # Watts (negative for production)
    power_variation: float  # 0.0 to 1.0 (percentage)


class EnergyProfileExtended(EnergyProfile, total=False):
    """Extended energy profile with optional features."""

    efficiency: float  # Energy conversion efficiency (0.0 to 1.0)


class CircuitTemplate(TypedDict):
    """Circuit template configuration."""

    energy_profile: EnergyProfileExtended
    relay_behavior: str  # "controllable", "non_controllable"
    priority: str  # "MUST_HAVE", "NON_ESSENTIAL"


class CircuitTemplateExtended(CircuitTemplate, total=False):
    """Extended circuit template with optional behaviors."""

    cycling_pattern: CyclingPattern
    time_of_day_profile: TimeOfDayProfile
    smart_behavior: SmartBehavior


class CircuitDefinition(TypedDict):
    """Individual circuit definition."""

    id: str
    name: str
    template: str
    tabs: list[int]


class CircuitDefinitionExtended(CircuitDefinition, total=False):
    """Extended circuit definition with overrides."""

    overrides: dict[str, Any]


class TabSynchronization(TypedDict):
    """Tab synchronization configuration."""

    tabs: list[int]
    behavior: str  # e.g., "240v_split_phase", "generator_paralleled"
    power_split: str  # "equal", "primary_secondary", "custom_ratio"
    energy_sync: bool
    template: str  # Template name to apply to synchronized group


class SimulationParams(TypedDict, total=False):
    """Global simulation parameters."""

    update_interval: int  # Seconds
    time_acceleration: float  # Multiplier for time progression
    noise_factor: float  # Random noise percentage
    enable_realistic_behaviors: bool
    simulation_start_time: str  # ISO format datetime string (e.g., "2024-06-15T12:00:00")
    use_simulation_time: bool  # Whether to use simulation time vs system time


class SimulationConfig(TypedDict):
    """Complete simulation configuration."""

    panel_config: PanelConfig
    circuit_templates: dict[str, CircuitTemplateExtended]
    circuits: list[CircuitDefinitionExtended]
    unmapped_tabs: list[int]
    simulation_params: SimulationParams
    unmapped_tab_templates: NotRequired[dict[str, CircuitTemplateExtended]]
    tab_synchronizations: NotRequired[list[TabSynchronization]]


class RealisticBehaviorEngine:
    """Engine for realistic circuit behaviors."""

    def __init__(self, simulation_start_time: float, config: SimulationConfig) -> None:
        """Initialize the behavior engine.

        Args:
            simulation_start_time: When simulation started (Unix timestamp)
            config: Simulation configuration
        """
        self._start_time = simulation_start_time
        self._config = config
        self._circuit_cycle_states: dict[str, dict[str, Any]] = {}

    def get_circuit_power(
        self, circuit_id: str, template: CircuitTemplateExtended, current_time: float, relay_state: str = "CLOSED"
    ) -> float:
        """Get realistic power for a circuit based on its template and current conditions.

        Args:
            circuit_id: Circuit identifier
            template: Circuit template configuration
            current_time: Current simulation time
            relay_state: Current relay state

        Returns:
            Power in watts (negative for production)
        """
        if relay_state == "OPEN":
            return 0.0

        energy_profile = template["energy_profile"]
        base_power = energy_profile["typical_power"]

        # Apply time-of-day modulation
        if template.get("time_of_day_profile", {}).get("enabled", False):
            base_power = self._apply_time_of_day_modulation(base_power, template, current_time)
        elif template["energy_profile"]["mode"] == "producer":
            # Solar producers should always have day/night cycle, even without time_of_day_profile enabled
            base_power = self._apply_solar_day_night_cycle(base_power, current_time, template)

        # Apply cycling behavior
        if "cycling_pattern" in template:
            base_power = self._apply_cycling_behavior(circuit_id, base_power, template, current_time)

        # Apply battery behavior
        battery_behavior = template.get("battery_behavior", {})
        if isinstance(battery_behavior, dict) and battery_behavior.get("enabled", False):
            base_power = self._apply_battery_behavior(base_power, template, current_time)

        # Apply smart behavior
        if template.get("smart_behavior", {}).get("responds_to_grid", False):
            base_power = self._apply_smart_behavior(base_power, template, current_time)

        # Add random variation
        variation = energy_profile.get("power_variation", 0.1)
        noise_factor = self._config["simulation_params"].get("noise_factor", 0.02)
        total_variation = variation + noise_factor

        power_multiplier = 1.0 + random.uniform(-total_variation, total_variation)  # nosec B311
        final_power = base_power * power_multiplier

        # Clamp to template range
        min_power, max_power = energy_profile["power_range"]
        if energy_profile["mode"] == "producer":
            # For producers, convert negative range to positive range
            # [-4000, 0] becomes [0, 4000]
            # [0, -4000] becomes [0, 4000]
            original_min, original_max = min_power, max_power
            min_power = 0.0  # Minimum production is 0
            # Find the maximum absolute value from the original range
            max_power = max(abs(original_min), abs(original_max))
        final_power = max(min_power, min(max_power, final_power))

        return final_power

    def _apply_time_of_day_modulation(
        self, base_power: float, template: CircuitTemplateExtended, current_time: float
    ) -> float:
        """Apply time-of-day power modulation."""
        # Use local time for hour calculation instead of UTC-based modulo
        current_hour = datetime.fromtimestamp(current_time).hour

        profile = template.get("time_of_day_profile", {})
        peak_hours = profile.get("peak_hours", [])

        if template["energy_profile"]["mode"] == "producer":
            # Solar production pattern - use template configuration
            return self._apply_solar_day_night_cycle(base_power, current_time, template)

        if current_hour in peak_hours:
            # Peak usage hours
            return base_power * 1.3
        if current_hour >= 22 or current_hour <= 6:
            # Overnight hours
            return base_power * 0.3
        # Normal hours
        return base_power

    def _apply_solar_day_night_cycle(
        self, base_power: float, current_time: float, template: CircuitTemplateExtended
    ) -> float:
        """Apply solar day/night cycle to producer circuits using template configuration.

        Args:
            base_power: Base power from template (should be negative for solar)
            current_time: Current simulation time
            template: Circuit template with time_of_day_profile configuration

        Returns:
            Power in watts (positive for production, 0 at night)
        """
        # Get production factor from template configuration
        production_factor = self._get_solar_production_factor_from_profile(template, current_time)

        # Convert negative base_power to positive production
        return abs(base_power) * production_factor

    def _apply_cycling_behavior(
        self, circuit_id: str, base_power: float, template: CircuitTemplateExtended, current_time: float
    ) -> float:
        """Apply cycling on/off behavior (like HVAC)."""
        cycling = template.get("cycling_pattern", {})
        on_duration = cycling.get("on_duration", 900)  # 15 minutes default
        off_duration = cycling.get("off_duration", 1800)  # 30 minutes default

        cycle_length = on_duration + off_duration
        cycle_position = (current_time - self._start_time) % cycle_length

        # Initialize cycle state if needed
        if circuit_id not in self._circuit_cycle_states:
            self._circuit_cycle_states[circuit_id] = {"last_cycle_start": self._start_time, "is_on": True}

        # Determine if we're in on or off phase
        is_on_phase = cycle_position < on_duration

        return base_power if is_on_phase else 0.0

    def _apply_smart_behavior(self, base_power: float, template: CircuitTemplateExtended, current_time: float) -> float:
        """Apply smart load behavior (like EV chargers responding to grid)."""
        smart = template.get("smart_behavior", {})
        max_reduction = smart.get("max_power_reduction", 0.5)

        # Simulate grid stress during peak hours (5-9 PM)
        current_hour = int((current_time % 86400) / 3600)
        if 17 <= current_hour <= 21:
            # Grid stress - reduce power
            reduction_factor = 1.0 - max_reduction
            return base_power * reduction_factor

        return base_power

    def _apply_battery_behavior(self, base_power: float, template: CircuitTemplateExtended, current_time: float) -> float:
        """Apply time-based battery behavior from YAML configuration."""
        # Convert timestamp to datetime for hour extraction
        dt = datetime.fromtimestamp(current_time)
        current_hour = dt.hour

        battery_config_raw = template.get("battery_behavior", {})
        if not isinstance(battery_config_raw, dict):
            return base_power
        battery_config: dict[str, Any] = battery_config_raw

        # Check if battery behavior is enabled
        if not battery_config.get("enabled", True):
            return base_power

        charge_hours: list[int] = battery_config.get("charge_hours", [])
        discharge_hours: list[int] = battery_config.get("discharge_hours", [])
        idle_hours: list[int] = battery_config.get("idle_hours", [])

        if current_hour in charge_hours:
            return self._get_charge_power(battery_config, current_hour)

        if current_hour in discharge_hours:
            return self._get_discharge_power(battery_config, current_hour)

        if current_hour in idle_hours:
            return self._get_idle_power(battery_config)

        # Transition hours - gradual change
        return base_power * 0.1

    def _get_charge_power(self, battery_config: dict[str, Any], current_hour: int) -> float:
        """Get charging power for the current hour."""
        max_charge_power: float = battery_config.get("max_charge_power", -3000.0)
        solar_intensity = self._get_solar_intensity_from_config(current_hour, battery_config)
        # Convert negative charge power to positive (charging is positive power consumption)
        return abs(max_charge_power) * solar_intensity

    def _get_discharge_power(self, battery_config: dict[str, Any], current_hour: int) -> float:
        """Get discharging power for the current hour."""
        max_discharge_power: float = battery_config.get("max_discharge_power", 2500.0)
        demand_factor = self._get_demand_factor_from_config(current_hour, battery_config)
        # Discharging is positive power production
        return abs(max_discharge_power) * demand_factor

    def _get_idle_power(self, battery_config: dict[str, Any]) -> float:
        """Get idle power (minimal power flow during low activity hours)."""
        idle_range: list[float] = battery_config.get("idle_power_range", [-100.0, 100.0])
        # Convert idle range to positive values (idle is minimal consumption)
        min_idle = abs(idle_range[0]) if idle_range[0] < 0 else idle_range[0]
        max_idle = abs(idle_range[1]) if idle_range[1] < 0 else idle_range[1]
        return random.uniform(min_idle, max_idle)  # nosec B311

    def _get_solar_intensity_from_config(self, hour: int, battery_config: dict[str, Any]) -> float:
        """Get solar intensity from YAML configuration."""
        solar_profile: dict[int, float] = battery_config.get("solar_intensity_profile", {})
        return solar_profile.get(hour, 0.1)  # Default to minimal intensity

    def _get_demand_factor_from_config(self, hour: int, battery_config: dict[str, Any]) -> float:
        """Get demand factor from YAML configuration."""
        demand_profile: dict[int, float] = battery_config.get("demand_factor_profile", {})
        return demand_profile.get(hour, 0.3)  # Default to low demand

    def _get_solar_production_factor_from_profile(self, template: CircuitTemplateExtended, current_time: float) -> float:
        """Get solar production factor from template configuration.

        Args:
            template: Circuit template with time_of_day_profile configuration
            current_time: Current simulation time

        Returns:
            Production factor (0.0 to 1.0) based on template configuration
        """
        time_profile = template.get("time_of_day_profile", {})
        if not time_profile.get("enabled", False):
            # Fall back to hardcoded day/night cycle if no profile configured
            return self._get_default_solar_factor(current_time)

        current_hour = datetime.fromtimestamp(current_time).hour

        # Check for hour-specific production factors
        hour_factors_raw = time_profile.get("hour_factors", {})
        if isinstance(hour_factors_raw, dict) and current_hour in hour_factors_raw:
            return float(hour_factors_raw[current_hour])

        # Check for production hours (hours when solar should produce)
        production_hours_raw = time_profile.get("production_hours", [])
        if isinstance(production_hours_raw, list) and production_hours_raw and current_hour in production_hours_raw:
            # Use peak factor if specified, otherwise default to 1.0
            peak_factor_raw = time_profile.get("peak_factor", 1.0)
            return float(peak_factor_raw)

        # Check for peak hours (alternative name for production hours)
        peak_hours_raw = time_profile.get("peak_hours", [])
        if isinstance(peak_hours_raw, list) and peak_hours_raw and current_hour in peak_hours_raw:
            # Use peak factor if specified, otherwise default to 1.0
            peak_factor_raw = time_profile.get("peak_factor", 1.0)
            return float(peak_factor_raw)

        # Check for night hours (hours when solar should not produce)
        night_hours_raw = time_profile.get("night_hours", [])
        if isinstance(night_hours_raw, list) and night_hours_raw and current_hour in night_hours_raw:
            return 0.0

        # Default to no production if not explicitly configured
        return 0.0

    def _get_default_solar_factor(self, current_time: float) -> float:
        """Get default solar production factor using hardcoded day/night cycle.

        Args:
            current_time: Current simulation time

        Returns:
            Production factor (0.0 to 1.0) using sine curve for daylight hours
        """
        current_hour = datetime.fromtimestamp(current_time).hour

        if 6 <= current_hour <= 18:
            # Daylight hours - use sine curve for realistic solar production
            hour_angle = (current_hour - 6) * math.pi / 12
            production_factor = math.sin(hour_angle) ** 2
            return production_factor
        return 0.0  # No solar production at night


class DynamicSimulationEngine:
    """Enhanced simulation engine with YAML configuration support."""

    def __init__(
        self,
        serial_number: str | None = None,
        config_path: Path | str | None = None,
        config_data: SimulationConfig | None = None,
    ) -> None:
        """Initialize the simulation engine.

        Args:
            serial_number: Custom serial number for the simulated panel.
                          If None, uses value from config.
            config_path: Path to YAML configuration file.
            config_data: Direct configuration data (overrides config_path).
        """
        self._base_data: dict[str, dict[str, Any]] | None = None
        self._config: SimulationConfig | None = None
        self._config_path = Path(config_path) if config_path else None
        self._config_data = config_data
        self._serial_number_override = serial_number
        self._fixture_loading_lock: asyncio.Lock | None = None
        self._lock_init_lock = threading.Lock()
        self._simulation_start_time = time.time()
        self._simulation_time_offset = 0.0  # Offset between real time and simulation time
        self._use_simulation_time = False
        self._simulation_start_time_override: str | None = None
        self._last_update_times: dict[str, float] = {}
        self._circuit_states: dict[str, dict[str, Any]] = {}
        self._behavior_engine: RealisticBehaviorEngine | None = None
        self._dynamic_overrides: dict[str, Any] = {}
        self._global_overrides: dict[str, Any] = {}
        # Energy accumulation tracking
        self._circuit_energy_states: dict[str, dict[str, float]] = {}
        self._last_energy_update_time = time.time()
        # Tab synchronization tracking
        self._tab_sync_groups: dict[int, str] = {}  # tab_number -> sync_group_id
        self._sync_group_power: dict[str, float] = {}  # sync_group_id -> total_power

    async def initialize_async(self) -> None:
        """Initialize the simulation engine asynchronously."""
        if self._base_data is not None:
            return

        # Thread-safe lazy initialization of the async lock
        if self._fixture_loading_lock is None:
            with self._lock_init_lock:
                if self._fixture_loading_lock is None:
                    self._fixture_loading_lock = asyncio.Lock()

        async with self._fixture_loading_lock:
            # Double-check after acquiring lock
            if self._base_data is not None:
                return

            # Load configuration
            await self._load_config_async()

            # Generate data from YAML config (required)
            if not self._config:
                raise ValueError("YAML configuration is required")

            self._initialize_tab_synchronizations()
            self._base_data = await self._generate_base_data_from_config()
            self._initialize_simulation_time()

            # Apply simulation start time override if set before initialization
            if self._simulation_start_time_override:
                self.override_simulation_start_time(self._simulation_start_time_override)
                self._simulation_start_time_override = None

            self._behavior_engine = RealisticBehaviorEngine(self._simulation_start_time, self._config)

    async def _load_config_async(self) -> None:
        """Load simulation configuration asynchronously."""
        if self._config_data:
            # Validate provided config data
            self._validate_yaml_config(self._config_data)
            self._config = self._config_data
        elif self._config_path and self._config_path.exists():
            loop = asyncio.get_event_loop()
            self._config = await loop.run_in_executor(None, self._load_yaml_config, self._config_path)
        else:
            # No config provided - simulation cannot start
            raise ValueError("YAML configuration is required")

        # Override serial number if provided
        if self._serial_number_override and self._config:
            self._config["panel_config"]["serial_number"] = self._serial_number_override

    def _initialize_simulation_time(self) -> None:
        """Initialize simulation time based on configuration."""
        if not self._config:
            raise SimulationConfigurationError("Simulation configuration is required for simulation time initialization.")

        sim_params = self._config.get("simulation_params", {})
        self._use_simulation_time = sim_params.get("use_simulation_time", False)

        if self._use_simulation_time:
            # Parse simulation start time if provided
            start_time_str = sim_params.get("simulation_start_time")
            if start_time_str:
                try:
                    # Parse ISO format datetime as local time (no timezone conversion)
                    if start_time_str.endswith("Z"):
                        # Remove Z suffix and treat as local time
                        start_time_str = start_time_str[:-1]
                    sim_start_dt = datetime.fromisoformat(start_time_str)
                    sim_start_timestamp = sim_start_dt.timestamp()

                    # Calculate offset from real time to simulation time
                    real_start_time = self._simulation_start_time
                    self._simulation_time_offset = sim_start_timestamp - real_start_time
                except (ValueError, TypeError) as exc:
                    raise SimulationConfigurationError(f"Invalid simulation_start_time: {start_time_str}") from exc

    def get_current_simulation_time(self) -> float:
        """Get current time for simulation (either real time or simulation time)."""
        current_real_time = time.time()

        if self._use_simulation_time:
            # Apply time acceleration if configured
            sim_params = self._config.get("simulation_params", {}) if self._config else {}
            time_acceleration = sim_params.get("time_acceleration", 1.0)

            # Calculate elapsed time since simulation start
            elapsed_real_time = current_real_time - self._simulation_start_time
            elapsed_sim_time = elapsed_real_time * time_acceleration

            # Return simulation time with offset
            return self._simulation_start_time + self._simulation_time_offset + elapsed_sim_time

        return current_real_time

    def override_simulation_start_time(self, start_time_str: str) -> None:
        """Override the simulation start time after initialization.

        Args:
            start_time_str: ISO format datetime string (e.g., "2024-06-15T12:00:00")
        """
        if not self._config:
            # If no config is loaded, just store the override for later use
            # This allows the method to be called before initialization
            self._simulation_start_time_override = start_time_str
            return

        # Enable simulation time and set the override
        self._use_simulation_time = True

        # Update the config to reflect the override
        if "simulation_params" not in self._config:
            self._config["simulation_params"] = {}

        self._config["simulation_params"]["use_simulation_time"] = True
        self._config["simulation_params"]["simulation_start_time"] = start_time_str

        try:
            # Parse ISO format datetime as local time (no timezone conversion)
            if start_time_str.endswith("Z"):
                # Remove Z suffix and treat as local time
                start_time_str = start_time_str[:-1]
            sim_start_dt = datetime.fromisoformat(start_time_str)
            sim_start_timestamp = sim_start_dt.timestamp()

            # Calculate offset from real time to simulation time
            real_start_time = self._simulation_start_time
            self._simulation_time_offset = sim_start_timestamp - real_start_time
        except (ValueError, TypeError):
            # Handle invalid datetime format gracefully - fall back to real time
            self._use_simulation_time = False
            return

    def _load_yaml_config(self, config_path: Path) -> SimulationConfig:
        """Load YAML configuration file synchronously."""
        with config_path.open() as f:
            config_data = yaml.safe_load(f)
            self._validate_yaml_config(config_data)
            return config_data  # type: ignore[no-any-return]

    def _validate_yaml_config(self, config_data: dict[str, Any] | SimulationConfig) -> None:
        """Validate YAML configuration structure and required fields."""
        if not isinstance(config_data, dict):
            raise ValueError("YAML configuration must be a dictionary")

        # Validate required top-level sections
        required_sections = ["panel_config", "circuit_templates", "circuits"]
        for section in required_sections:
            if section not in config_data:
                raise ValueError(f"Missing required section: {section}")

        self._validate_panel_config(config_data["panel_config"])
        self._validate_circuit_templates(config_data["circuit_templates"])
        self._validate_circuits(config_data["circuits"], config_data["circuit_templates"])

    def _validate_panel_config(self, panel_config: Any) -> None:
        """Validate panel configuration section."""
        if not isinstance(panel_config, dict):
            raise ValueError("panel_config must be a dictionary")

        required_panel_fields = ["serial_number", "total_tabs", "main_size"]
        for field in required_panel_fields:
            if field not in panel_config:
                raise ValueError(f"Missing required panel_config field: {field}")

    def _validate_circuit_templates(self, circuit_templates: Any) -> None:
        """Validate circuit templates section."""
        if not isinstance(circuit_templates, dict):
            raise ValueError("circuit_templates must be a dictionary")

        if not circuit_templates:
            raise ValueError("At least one circuit template must be defined")

        for template_name, template in circuit_templates.items():
            self._validate_single_template(template_name, template)

    def _validate_single_template(self, template_name: str, template: Any) -> None:
        """Validate a single circuit template."""
        if not isinstance(template, dict):
            raise ValueError(f"Circuit template '{template_name}' must be a dictionary")

        required_template_fields = [
            "energy_profile",
            "relay_behavior",
            "priority",
        ]
        for field in required_template_fields:
            if field not in template:
                raise ValueError(f"Missing required field '{field}' in circuit template '{template_name}'")

    def _validate_circuits(self, circuits: Any, circuit_templates: dict[str, Any]) -> None:
        """Validate circuits section."""
        if not isinstance(circuits, list):
            raise ValueError("circuits must be a list")

        if not circuits:
            raise ValueError("At least one circuit must be defined")

        for i, circuit in enumerate(circuits):
            self._validate_single_circuit(i, circuit, circuit_templates)

    def _validate_single_circuit(self, index: int, circuit: Any, circuit_templates: dict[str, Any]) -> None:
        """Validate a single circuit definition."""
        if not isinstance(circuit, dict):
            raise ValueError(f"Circuit {index} must be a dictionary")

        required_circuit_fields = ["id", "name", "template", "tabs"]
        for field in required_circuit_fields:
            if field not in circuit:
                raise ValueError(f"Missing required field '{field}' in circuit {index}")

        # Validate template reference
        template_name = circuit["template"]
        if template_name not in circuit_templates:
            raise ValueError(f"Circuit {index} references unknown template '{template_name}'")

    async def _generate_base_data_from_config(self) -> dict[str, dict[str, Any]]:
        """Generate base simulation data from YAML configuration."""
        if not self._config or not self._config["circuits"]:
            raise SimulationConfigurationError("YAML configuration with circuits is required for data generation")

        return await self._generate_from_config()

    async def _generate_from_config(self) -> dict[str, dict[str, Any]]:
        """Generate simulation data from configuration."""
        if not self._config:
            raise SimulationConfigurationError("Configuration not loaded for data generation")

        circuits_data, totals = await self._process_all_circuits()

        return {
            "circuits": {"circuits": circuits_data},
            "panel": self._generate_panel_data(
                totals["consumption"], totals["production"], totals["produced_energy"], totals["consumed_energy"]
            ),
            "status": self._generate_status_data(),
            "soe": {"stateOfEnergy": 0.75},
        }

    async def _process_all_circuits(self) -> tuple[dict[str, dict[str, Any]], dict[str, float]]:
        """Process all circuits and return circuit data with aggregated totals."""
        if not self._config:
            raise SimulationConfigurationError("Configuration not loaded for circuit processing")
        circuits_data = {}
        totals = {
            "consumption": 0.0,
            "production": 0.0,
            "produced_energy": 0.0,
            "consumed_energy": 0.0,
        }

        current_time = time.time()

        for circuit_def in self._config["circuits"]:
            circuit_data, power_values = await self._process_single_circuit(circuit_def, current_time)
            circuits_data[circuit_def["id"]] = circuit_data

            # Update totals
            totals["consumption"] += power_values["consumption"]
            totals["production"] += power_values["production"]
            totals["produced_energy"] += power_values["produced_energy"]
            totals["consumed_energy"] += power_values["consumed_energy"]

        return circuits_data, totals

    async def _process_single_circuit(
        self, circuit_def: CircuitDefinitionExtended, current_time: float
    ) -> tuple[dict[str, Any], dict[str, float]]:
        """Process a single circuit and return its data and power values."""
        if not self._config:
            raise SimulationConfigurationError("Configuration not loaded for circuit processing")
        template_name = circuit_def["template"]
        template = self._config["circuit_templates"][template_name]

        # Apply overrides
        final_template = deepcopy(template)
        if "overrides" in circuit_def:
            final_template.update(circuit_def["overrides"])  # type: ignore[typeddict-item]

        # Generate realistic power using behavior engine
        behavior_engine = RealisticBehaviorEngine(current_time, self._config)
        base_power = behavior_engine.get_circuit_power(circuit_def["id"], final_template, current_time)

        # Handle synchronization and power calculation
        instant_power = self._calculate_instant_power(circuit_def, base_power)

        # Calculate energy
        produced_energy, consumed_energy = self._calculate_circuit_energy(
            circuit_def, final_template, instant_power, current_time
        )

        # Create circuit data
        circuit_data = self._create_circuit_data(
            circuit_def, final_template, instant_power, produced_energy, consumed_energy, current_time
        )

        # Apply any dynamic overrides (including relay state changes)
        self._apply_dynamic_overrides(circuit_def["id"], circuit_data)

        # Calculate power values for aggregation
        power_values = self._calculate_power_values(template, instant_power, produced_energy, consumed_energy, current_time)

        return circuit_data, power_values

    def _calculate_instant_power(self, circuit_def: CircuitDefinitionExtended, base_power: float) -> float:
        """Calculate instant power for a circuit, handling synchronization if needed."""
        circuit_tabs = circuit_def["tabs"]
        sync_config = None
        for tab_num in circuit_tabs:
            tab_sync = self._get_tab_sync_config(tab_num)
            if tab_sync:
                sync_config = tab_sync
                break

        # Apply synchronization if needed
        if sync_config and len(circuit_tabs) > 1:
            # For multi-tab circuits, use the total power (don't split)
            instant_power = base_power
            # Store total power for synchronization with unmapped tabs in same group
            for tab_num in circuit_tabs:
                if tab_num in self._tab_sync_groups:
                    sync_group_id = self._tab_sync_groups[tab_num]
                    self._sync_group_power[sync_group_id] = base_power
        else:
            instant_power = base_power

        return instant_power

    def _calculate_circuit_energy(
        self,
        circuit_def: CircuitDefinitionExtended,
        final_template: CircuitTemplateExtended,
        instant_power: float,
        current_time: float,
    ) -> tuple[float, float]:
        """Calculate energy for a circuit, handling synchronization if needed."""
        circuit_tabs = circuit_def["tabs"]
        sync_config = None
        for tab_num in circuit_tabs:
            tab_sync = self._get_tab_sync_config(tab_num)
            if tab_sync:
                sync_config = tab_sync
                break

        # Calculate accumulated energy based on power and time elapsed
        # For synchronized circuits, use shared energy calculation
        if sync_config and sync_config.get("energy_sync", False):
            # Use the first tab for energy synchronization reference
            first_tab = circuit_tabs[0]
            if final_template["energy_profile"]["mode"] == "bidirectional":
                # Use specialized bidirectional energy calculation
                return self._calculate_bidirectional_energy(circuit_def["id"], instant_power, current_time, final_template)
            return self._synchronize_energy_for_tab(
                first_tab, circuit_def["id"], instant_power, current_time, final_template["energy_profile"]["mode"]
            )
        if final_template["energy_profile"]["mode"] == "bidirectional":
            # Use specialized bidirectional energy calculation
            return self._calculate_bidirectional_energy(circuit_def["id"], instant_power, current_time, final_template)
        return self._calculate_accumulated_energy(
            circuit_def["id"], instant_power, current_time, final_template["energy_profile"]["mode"]
        )

    def _create_circuit_data(
        self,
        circuit_def: CircuitDefinitionExtended,
        final_template: CircuitTemplateExtended,
        instant_power: float,
        produced_energy: float,
        consumed_energy: float,
        current_time: float,
    ) -> dict[str, Any]:
        """Create circuit data dictionary."""
        return {
            "id": circuit_def["id"],
            "name": circuit_def["name"],
            "relayState": "CLOSED",
            "instantPowerW": instant_power,
            "instantPowerUpdateTimeS": int(current_time),
            "producedEnergyWh": produced_energy,
            "consumedEnergyWh": consumed_energy,
            "energyAccumUpdateTimeS": int(current_time),
            "tabs": circuit_def["tabs"],
            "priority": final_template["priority"],
            "isUserControllable": final_template["relay_behavior"] == "controllable",
            "isSheddable": False,
            "isNeverBackup": False,
        }

    def _calculate_power_values(
        self,
        template: CircuitTemplateExtended,
        instant_power: float,
        produced_energy: float,
        consumed_energy: float,
        current_time: float,
    ) -> dict[str, float]:
        """Calculate power values for aggregation based on circuit type."""
        power_values = {
            "consumption": 0.0,
            "production": 0.0,
            "produced_energy": produced_energy,
            "consumed_energy": consumed_energy,
        }

        if template["energy_profile"]["mode"] == "producer":
            # Solar/producer circuits contribute to production
            power_values["production"] = instant_power
        elif template["energy_profile"]["mode"] == "bidirectional":
            # Battery circuits: determine if charging or discharging from profile configuration
            battery_state = self._get_battery_state_from_profile(template, current_time)
            if battery_state == "charging":
                # Battery is charging - positive power = consumption
                power_values["consumption"] = instant_power
            elif battery_state == "discharging":
                # Battery is discharging - positive power = production
                power_values["production"] = instant_power
            else:
                # Idle or unknown state - treat as minimal consumption
                power_values["consumption"] = instant_power
        else:
            # Consumer circuits contribute to consumption
            power_values["consumption"] = instant_power

        return power_values

    def _generate_panel_data(
        self, total_consumption: float, total_production: float, total_produced_energy: float, total_consumed_energy: float
    ) -> dict[str, Any]:
        """Generate panel data aggregated from circuit data."""
        if not self._config:
            raise SimulationConfigurationError("Configuration not loaded")

        # Panel grid power = consumption - production
        # Negative values indicate net export (production > consumption)
        # Positive values indicate net import (consumption > production)
        grid_power = total_consumption - total_production

        return {
            "instantGridPowerW": grid_power,
            "instantPanelStateOfEnergyPercent": random.uniform(0.6, 0.9),  # nosec B311
            "serialNumber": self._config["panel_config"]["serial_number"],
            "mainRelayState": MAIN_RELAY_CLOSED,
            "dsmGridState": DSM_GRID_UP,
            "dsmState": DSM_ON_GRID,
            "mainMeterEnergy": {
                "producedEnergyWh": total_produced_energy,
                "consumedEnergyWh": total_consumed_energy,
            },
            "feedthroughPowerW": 0.0,
            "feedthroughEnergy": {
                "producedEnergyWh": 0.0,
                "consumedEnergyWh": 0.0,
            },
            "gridSampleStartMs": int(time.time() * 1000),
            "gridSampleEndMs": int(time.time() * 1000),
            "currentRunConfig": PANEL_ON_GRID,
            "branches": self._generate_branches(),
        }

    def _generate_branches(self) -> list[dict[str, Any]]:
        """Generate branch data for all tabs in the panel."""
        if not self._config:
            return []

        total_tabs = self._config["panel_config"].get("total_tabs", 32)
        branches = []

        # Find which tabs are mapped to circuits
        mapped_tabs = set()
        for circuit_def in self._config.get("circuits", []):
            mapped_tabs.update(circuit_def.get("tabs", []))

        for tab_num in range(1, total_tabs + 1):
            # Create a branch for each tab with all required fields
            current_sim_time = self.get_current_simulation_time()
            current_time_ms = int(current_sim_time * 1000)

            # Handle unmapped tabs
            if tab_num not in mapped_tabs:
                # Check if this unmapped tab has a specific template defined
                unmapped_tab_config = self._config.get("unmapped_tab_templates", {}).get(str(tab_num))
                if unmapped_tab_config:
                    # Apply behavior engine to unmapped tab with its template
                    behavior_engine = RealisticBehaviorEngine(self._simulation_start_time, self._config)
                    current_sim_time = self.get_current_simulation_time()
                    base_power = behavior_engine.get_circuit_power(
                        f"unmapped_tab_{tab_num}", unmapped_tab_config, current_sim_time
                    )

                    # Apply tab synchronization if configured
                    sync_config = self._get_tab_sync_config(tab_num)
                    if sync_config:
                        baseline_power = self._get_synchronized_power(tab_num, base_power, sync_config)
                    else:
                        baseline_power = base_power
                else:
                    # Default unmapped tab baseline consumption (10-200W)
                    baseline_power = random.uniform(10.0, 200.0)  # nosec B311

                # Calculate accumulated energy for unmapped tabs with synchronization support
                current_time = self.get_current_simulation_time()
                circuit_id = f"unmapped_tab_{tab_num}"

                # Check if this tab has synchronization configuration
                sync_config = self._get_tab_sync_config(tab_num)
                if sync_config and sync_config.get("energy_sync", False):
                    # Use synchronized energy calculation
                    # Determine circuit mode for unmapped tabs
                    unmapped_mode = (
                        "producer"
                        if unmapped_tab_config and unmapped_tab_config.get("energy_profile", {}).get("mode") == "producer"
                        else "consumer"
                    )
                    produced_energy, consumed_energy = self._synchronize_energy_for_tab(
                        tab_num, circuit_id, baseline_power, current_time, unmapped_mode
                    )
                else:
                    # Use regular energy calculation
                    # Determine circuit mode for unmapped tabs
                    unmapped_mode = (
                        "producer"
                        if unmapped_tab_config and unmapped_tab_config.get("energy_profile", {}).get("mode") == "producer"
                        else "consumer"
                    )
                    produced_energy, consumed_energy = self._calculate_accumulated_energy(
                        circuit_id, baseline_power, current_time, unmapped_mode
                    )

                # For unmapped tabs:
                # - Solar production (positive power) -> exported energy represents production
                # - Consumption (positive power) -> imported energy represents consumption
                if unmapped_mode == "producer":
                    # Solar production
                    imported_energy = consumed_energy
                    exported_energy = produced_energy
                else:
                    # Consumption
                    imported_energy = consumed_energy
                    exported_energy = produced_energy
            else:
                baseline_power = 0.0  # Mapped tabs get power from circuit definitions
                imported_energy = 0.0
                exported_energy = 0.0

            branch = {
                "id": f"branch_{tab_num}",
                "relayState": "CLOSED",
                "instantPowerW": baseline_power,
                "importedActiveEnergyWh": imported_energy,
                "exportedActiveEnergyWh": exported_energy,
                "measureStartTsMs": current_time_ms,
                "measureDurationMs": 5000,  # 5 second measurement window
                "isMeasureValid": True,
            }
            branches.append(branch)

        return branches

    def _generate_status_data(self) -> dict[str, Any]:
        """Generate status data from configuration."""
        if not self._config:
            return {}

        return {
            "software": {"firmwareVersion": "sim/v1.0.0", "updateStatus": "idle", "env": "simulation"},
            "system": {
                "manufacturer": "Span",
                "serial": self._config["panel_config"]["serial_number"],
                "model": "00200",
                "doorState": "CLOSED",
                "proximityProven": True,
                "uptime": 3600000,
            },
            "network": {"eth0Link": True, "wlanLink": True, "wwanLink": False},
        }

    @property
    def serial_number(self) -> str:
        """Get the simulated panel serial number."""
        if self._config:
            return self._config["panel_config"]["serial_number"]
        if self._serial_number_override:
            return self._serial_number_override

        raise ValueError("No configuration loaded - serial number not available")

    async def get_panel_data(self) -> dict[str, dict[str, Any]]:
        """Get panel and circuit data."""
        return await self._generate_base_data_from_config()

    async def get_soe(self) -> dict[str, dict[str, float]]:
        """Get storage state of energy data with dynamic calculation."""
        if not self._config:
            return {"soe": {"percentage": 75.0}}

        # Calculate dynamic SOE based on battery activity
        current_soe = self._calculate_dynamic_soe()
        return {"soe": {"percentage": current_soe}}

    def _calculate_dynamic_soe(self) -> float:
        """Calculate dynamic SOE based on configured battery behavior."""
        current_time = self.get_current_simulation_time()

        # Find battery circuits and determine their configured behavior
        total_charging_power = 0.0
        total_discharging_power = 0.0
        battery_count = 0

        if self._config and "circuits" in self._config:
            for circuit_def in self._config["circuits"]:
                template_name = circuit_def.get("template", "")
                if template_name == "battery":
                    template_raw: Any = self._config["circuit_templates"].get(template_name, {})
                    if not isinstance(template_raw, dict):
                        continue
                    template_dict: dict[str, Any] = template_raw
                    if template_dict.get("battery_behavior", {}).get("enabled", False):
                        # Convert dict to CircuitTemplateExtended for type compatibility
                        template_extended: CircuitTemplateExtended = template_dict  # type: ignore[assignment]

                        # Get battery state from configured profile
                        battery_state = self._get_battery_state_from_profile(template_extended, current_time)

                        # Calculate what the battery power would be at this time
                        behavior_engine = RealisticBehaviorEngine(self._simulation_start_time, self._config)
                        battery_power = behavior_engine.get_circuit_power(circuit_def["id"], template_extended, current_time)

                        # Accumulate power based on configured battery state
                        if battery_state == "charging":
                            total_charging_power += battery_power
                        elif battery_state == "discharging":
                            total_discharging_power += battery_power

                        battery_count += 1

        if battery_count == 0:
            return 75.0  # Default if no batteries

        # Calculate SOE based on configured battery behavior
        base_soe = self._get_time_based_soe(datetime.fromtimestamp(current_time).hour)

        # Adjust SOE based on configured charging/discharging activity
        if total_charging_power > 1000:
            # High charging activity - increase SOE
            return min(95.0, base_soe + 15.0)
        if total_discharging_power > 1000:
            # High discharging activity - decrease SOE
            return max(15.0, base_soe - 20.0)
        if total_charging_power > 500:
            # Moderate charging activity - slight increase
            return min(90.0, base_soe + 8.0)
        if total_discharging_power > 500:
            # Moderate discharging activity - slight decrease
            return max(20.0, base_soe - 10.0)

        # Minimal activity - normal SOE
        return base_soe

    def _get_time_based_soe(self, hour: int) -> float:
        """Get base SOE based on time of day patterns."""
        # SOE typically follows this pattern:
        # Morning (6-8): Start moderate after overnight discharge
        # Day (9-16): Increasing due to solar charging
        # Evening (17-21): Decreasing due to peak discharge
        # Night (22-5): Slow decrease due to minimal discharge

        soe_profile = {
            0: 45.0,
            1: 40.0,
            2: 38.0,
            3: 35.0,
            4: 33.0,
            5: 30.0,  # Night discharge
            6: 32.0,
            7: 35.0,
            8: 40.0,  # Morning
            9: 45.0,
            10: 55.0,
            11: 65.0,
            12: 75.0,
            13: 80.0,
            14: 85.0,
            15: 88.0,
            16: 90.0,  # Solar charging
            17: 85.0,
            18: 80.0,
            19: 70.0,
            20: 60.0,
            21: 50.0,  # Peak discharge
            22: 48.0,
            23: 46.0,  # Evening wind-down
        }

        return soe_profile.get(hour, 50.0)  # Default to 50% if hour not found

    async def get_status(self) -> dict[str, Any]:
        """Get status data."""
        return self._generate_status_data()

    def set_dynamic_overrides(
        self, circuit_overrides: dict[str, dict[str, Any]] | None = None, global_overrides: dict[str, Any] | None = None
    ) -> None:
        """Set dynamic overrides for circuits and global parameters.

        Args:
            circuit_overrides: Dict mapping circuit_id to override parameters
            global_overrides: Global override parameters
        """
        if circuit_overrides:
            self._dynamic_overrides.update(circuit_overrides)

        if global_overrides:
            self._global_overrides.update(global_overrides)

    def clear_dynamic_overrides(self) -> None:
        """Clear all dynamic overrides."""
        self._dynamic_overrides.clear()
        self._global_overrides.clear()

    def _apply_dynamic_overrides(self, circuit_id: str, circuit_info: dict[str, Any]) -> None:
        """Apply dynamic overrides to a circuit."""
        # Apply circuit-specific overrides
        if circuit_id in self._dynamic_overrides:
            overrides = self._dynamic_overrides[circuit_id]

            if "power_override" in overrides:
                circuit_info["instantPowerW"] = overrides["power_override"]
            elif "power_multiplier" in overrides:
                circuit_info["instantPowerW"] *= overrides["power_multiplier"]

            if "relay_state" in overrides:
                circuit_info["relayState"] = overrides["relay_state"]
                if overrides["relay_state"] == "OPEN":
                    circuit_info["instantPowerW"] = 0.0

            if "priority" in overrides:
                circuit_info["priority"] = overrides["priority"]

        # Apply global overrides
        if "power_multiplier" in self._global_overrides:
            circuit_info["instantPowerW"] *= self._global_overrides["power_multiplier"]

    def _calculate_accumulated_energy(
        self, circuit_id: str, instant_power: float, current_time: float, circuit_mode: str = "consumer"
    ) -> tuple[float, float]:
        """Calculate accumulated energy for a circuit based on power and time elapsed.

        Args:
            circuit_id: Circuit identifier
            instant_power: Current power in watts (positive for both consumption and production)
            current_time: Current timestamp
            circuit_mode: Circuit mode ("consumer", "producer", "bidirectional")

        Returns:
            Tuple of (produced_energy_wh, consumed_energy_wh)
        """
        # Initialize energy state if not exists
        if circuit_id not in self._circuit_energy_states:
            self._circuit_energy_states[circuit_id] = {"produced_wh": 0.0, "consumed_wh": 0.0, "last_update": current_time}

        energy_state = self._circuit_energy_states[circuit_id]
        last_update = energy_state["last_update"]
        time_elapsed_hours = (current_time - last_update) / 3600.0  # Convert seconds to hours

        # Calculate energy increment based on current power and circuit mode
        if instant_power > 0:
            energy_increment = instant_power * time_elapsed_hours
            if circuit_mode == "producer":
                # Solar/producer circuits: positive power = production
                energy_state["produced_wh"] += energy_increment
            elif circuit_mode == "bidirectional":
                # Battery circuits: determine charging/discharging from profile configuration
                # This requires access to the template to check battery behavior configuration
                # For now, we'll handle this in the calling code that has access to the template
                # Default to consumption (charging) - this will be overridden by the calling code
                energy_state["consumed_wh"] += energy_increment
            else:
                # Consumer circuits: positive power = consumption
                energy_state["consumed_wh"] += energy_increment
        elif instant_power < 0:
            # Negative power (shouldn't happen with new logic, but handle for safety)
            energy_increment = abs(instant_power) * time_elapsed_hours
            if circuit_mode == "producer":
                energy_state["produced_wh"] += energy_increment
            else:
                energy_state["consumed_wh"] += energy_increment

        # Update last update time
        energy_state["last_update"] = current_time

        return energy_state["produced_wh"], energy_state["consumed_wh"]

    def _calculate_bidirectional_energy(
        self, circuit_id: str, instant_power: float, current_time: float, template: CircuitTemplateExtended
    ) -> tuple[float, float]:
        """Calculate accumulated energy for bidirectional circuits (batteries) based on profile configuration.

        Args:
            circuit_id: Circuit identifier
            instant_power: Current power in watts (positive for both consumption and production)
            current_time: Current timestamp
            template: Circuit template with battery behavior configuration

        Returns:
            Tuple of (produced_energy_wh, consumed_energy_wh)
        """
        # Initialize energy state if not exists
        if circuit_id not in self._circuit_energy_states:
            self._circuit_energy_states[circuit_id] = {"produced_wh": 0.0, "consumed_wh": 0.0, "last_update": current_time}

        energy_state = self._circuit_energy_states[circuit_id]
        last_update = energy_state["last_update"]
        time_elapsed_hours = (current_time - last_update) / 3600.0  # Convert seconds to hours

        # Calculate energy increment based on current power and battery state
        if instant_power > 0:
            energy_increment = instant_power * time_elapsed_hours

            # Determine battery state from profile configuration
            battery_state = self._get_battery_state_from_profile(template, current_time)

            if battery_state == "charging":
                # Battery is charging - positive power = consumption
                energy_state["consumed_wh"] += energy_increment
            elif battery_state == "discharging":
                # Battery is discharging - positive power = production
                energy_state["produced_wh"] += energy_increment
            elif battery_state == "idle":
                # Battery is idle - minimal consumption
                energy_state["consumed_wh"] += energy_increment
            else:
                # Unknown state - default to consumption
                energy_state["consumed_wh"] += energy_increment
        elif instant_power < 0:
            # Negative power (shouldn't happen with new logic, but handle for safety)
            energy_increment = abs(instant_power) * time_elapsed_hours
            energy_state["consumed_wh"] += energy_increment

        # Update last update time
        energy_state["last_update"] = current_time

        return energy_state["produced_wh"], energy_state["consumed_wh"]

    def _initialize_tab_synchronizations(self) -> None:
        """Initialize tab synchronization groups from configuration."""
        if not self._config:
            return

        tab_syncs = self._config.get("tab_synchronizations", [])

        for sync_config in tab_syncs:
            sync_group_id = f"sync_{sync_config['behavior']}_{hash(tuple(sync_config['tabs']))}"

            for tab_num in sync_config["tabs"]:
                self._tab_sync_groups[tab_num] = sync_group_id

        # Initialize power tracking for sync groups
        for sync_group_id in set(self._tab_sync_groups.values()):
            self._sync_group_power[sync_group_id] = 0.0

    def _get_synchronized_power(self, tab_num: int, base_power: float, sync_config: TabSynchronization) -> float:
        """Get synchronized power for a tab based on sync configuration."""
        sync_group_id = self._tab_sync_groups.get(tab_num)
        if not sync_group_id:
            return base_power

        # Store the total power for this sync group
        self._sync_group_power[sync_group_id] = base_power

        # Split power based on configuration
        if sync_config["power_split"] == "equal":
            num_tabs = len(sync_config["tabs"])
            return base_power / num_tabs
        if sync_config["power_split"] == "primary_secondary":
            # First tab gets full power, others get 0 (for data representation)
            if tab_num == sync_config["tabs"][0]:
                return base_power

            return 0.0

        return base_power

    def _get_tab_sync_config(self, tab_num: int) -> TabSynchronization | None:
        """Get synchronization configuration for a specific tab."""
        if not self._config:
            raise SimulationConfigurationError("Simulation configuration is required for tab synchronization.")

        tab_syncs = self._config.get("tab_synchronizations", [])

        for sync_config in tab_syncs:
            if tab_num in sync_config["tabs"]:
                return sync_config

        return None

    def _synchronize_energy_for_tab(
        self,
        tab_num: int,
        circuit_id: str,
        instant_power: float,
        current_time: float,
        circuit_mode: str = "consumer",
    ) -> tuple[float, float]:
        """Calculate synchronized energy for tabs in the same sync group."""
        try:
            sync_config = self._get_tab_sync_config(tab_num)
        except SimulationConfigurationError:
            # Fallback to regular energy calculation if no sync config
            return self._calculate_accumulated_energy(circuit_id, instant_power, current_time, circuit_mode)

        if not sync_config or not sync_config.get("energy_sync", False):
            # Fallback to regular energy calculation if sync not enabled
            return self._calculate_accumulated_energy(circuit_id, instant_power, current_time, circuit_mode)

        # Use a shared energy state for all tabs in the sync group
        sync_group_id = self._tab_sync_groups.get(tab_num)
        if not sync_group_id:
            # Fallback to regular energy calculation if no sync group
            return self._calculate_accumulated_energy(circuit_id, instant_power, current_time, circuit_mode)

        shared_circuit_id = f"sync_group_{sync_group_id}"

        # Calculate energy using the total power for the sync group
        total_power = self._sync_group_power.get(sync_group_id, instant_power * len(sync_config["tabs"]))

        return self._calculate_accumulated_energy(shared_circuit_id, total_power, current_time, circuit_mode)

    def _get_battery_state_from_profile(self, template: CircuitTemplateExtended, current_time: float) -> str:
        """Determine battery state (charging/discharging/idle) from profile configuration.

        Args:
            template: Circuit template with battery behavior configuration
            current_time: Current simulation time

        Returns:
            Battery state: 'charging', 'discharging', 'idle', or 'unknown'
        """
        battery_config_raw = template.get("battery_behavior", {})
        if not isinstance(battery_config_raw, dict):
            return "unknown"
        battery_config: dict[str, Any] = battery_config_raw

        # Check if battery behavior is enabled
        if not battery_config.get("enabled", True):
            return "unknown"

        current_hour = datetime.fromtimestamp(current_time).hour
        charge_hours: list[int] = battery_config.get("charge_hours", [])
        discharge_hours: list[int] = battery_config.get("discharge_hours", [])
        idle_hours: list[int] = battery_config.get("idle_hours", [])

        if current_hour in charge_hours:
            return "charging"
        if current_hour in discharge_hours:
            return "discharging"
        if current_hour in idle_hours:
            return "idle"

        return "unknown"
