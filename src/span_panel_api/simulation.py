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
            # Solar production pattern
            if 6 <= current_hour <= 18:
                # Daylight hours - use sine curve
                hour_angle = (current_hour - 6) * math.pi / 12
                production_factor = math.sin(hour_angle) ** 2
                return base_power * production_factor
            return 0.0  # No solar at night

        if current_hour in peak_hours:
            # Peak usage hours
            return base_power * 1.3
        if current_hour >= 22 or current_hour <= 6:
            # Overnight hours
            return base_power * 0.3
        # Normal hours
        return base_power

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
        return max_charge_power * solar_intensity

    def _get_discharge_power(self, battery_config: dict[str, Any], current_hour: int) -> float:
        """Get discharging power for the current hour."""
        max_discharge_power: float = battery_config.get("max_discharge_power", 2500.0)
        demand_factor = self._get_demand_factor_from_config(current_hour, battery_config)
        return max_discharge_power * demand_factor

    def _get_idle_power(self, battery_config: dict[str, Any]) -> float:
        """Get idle power (minimal power flow during low activity hours)."""
        idle_range: list[float] = battery_config.get("idle_power_range", [-100.0, 100.0])
        return random.uniform(idle_range[0], idle_range[1])  # nosec B311

    def _get_solar_intensity_from_config(self, hour: int, battery_config: dict[str, Any]) -> float:
        """Get solar intensity from YAML configuration."""
        solar_profile: dict[int, float] = battery_config.get("solar_intensity_profile", {})
        return solar_profile.get(hour, 0.1)  # Default to minimal intensity

    def _get_demand_factor_from_config(self, hour: int, battery_config: dict[str, Any]) -> float:
        """Get demand factor from YAML configuration."""
        demand_profile: dict[int, float] = battery_config.get("demand_factor_profile", {})
        return demand_profile.get(hour, 0.3)  # Default to low demand


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

        circuits_data = {}
        total_power = 0.0
        total_produced_energy = 0.0
        total_consumed_energy = 0.0

        current_time = time.time()

        for circuit_def in self._config["circuits"]:
            template_name = circuit_def["template"]
            template = self._config["circuit_templates"][template_name]

            # Apply overrides
            final_template = deepcopy(template)
            if "overrides" in circuit_def:
                final_template.update(circuit_def["overrides"])  # type: ignore[typeddict-item]

            # Generate realistic power using behavior engine
            behavior_engine = RealisticBehaviorEngine(current_time, self._config)
            base_power = behavior_engine.get_circuit_power(circuit_def["id"], final_template, current_time)

            # Check if this circuit uses synchronized tabs
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

            # Calculate accumulated energy based on power and time elapsed
            # For synchronized circuits, use shared energy calculation
            if sync_config and sync_config.get("energy_sync", False):
                # Use the first tab for energy synchronization reference
                first_tab = circuit_tabs[0]
                produced_energy, consumed_energy = self._synchronize_energy_for_tab(
                    first_tab, circuit_def["id"], instant_power, current_time
                )
            else:
                produced_energy, consumed_energy = self._calculate_accumulated_energy(
                    circuit_def["id"], instant_power, current_time
                )

            circuit_data = {
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

            # Apply any dynamic overrides (including relay state changes)
            self._apply_dynamic_overrides(circuit_def["id"], circuit_data)

            circuits_data[circuit_def["id"]] = circuit_data

            # Aggregate for panel totals
            total_power += instant_power
            total_produced_energy += produced_energy
            total_consumed_energy += consumed_energy

        # Panel power calculation needs to account for all circuit power
        # Virtual circuits for unmapped tabs are created by the client, not here

        return {
            "circuits": {"circuits": circuits_data},
            "panel": self._generate_panel_data(total_power, total_produced_energy, total_consumed_energy),
            "status": self._generate_status_data(),
            "soe": {"stateOfEnergy": 0.75},
        }

    def _generate_panel_data(
        self, total_power: float, total_produced_energy: float, total_consumed_energy: float
    ) -> dict[str, Any]:
        """Generate panel data aggregated from circuit data."""
        if not self._config:
            raise SimulationConfigurationError("Configuration not loaded")

        # Panel grid power should exactly match the total of all circuit power
        # Negative values indicate production (solar), positive indicates consumption
        grid_power = total_power

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
                    produced_energy, consumed_energy = self._synchronize_energy_for_tab(
                        tab_num, circuit_id, baseline_power, current_time
                    )
                else:
                    # Use regular energy calculation
                    produced_energy, consumed_energy = self._calculate_accumulated_energy(
                        circuit_id, baseline_power, current_time
                    )

                # For unmapped tabs:
                # - Solar production (negative power) -> imported energy represents production
                # - Consumption (positive power) -> exported energy represents consumption
                if baseline_power < 0:
                    # Solar production
                    imported_energy = produced_energy
                    exported_energy = consumed_energy
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
        """Calculate dynamic SOE based on current time and battery behavior."""
        current_time = time.time()
        current_hour = int((current_time % 86400) / 3600)  # Hour of day (0-23)

        # Find battery circuits to determine charging/discharging state
        total_battery_power = 0.0
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
                        # Calculate what the battery power would be at this time
                        behavior_engine = RealisticBehaviorEngine(current_time, self._config)
                        # Convert dict to CircuitTemplateExtended for type compatibility
                        template_extended: CircuitTemplateExtended = template_dict  # type: ignore[assignment]
                        battery_power = behavior_engine.get_circuit_power(circuit_def["id"], template_extended, current_time)
                        total_battery_power += battery_power
                        battery_count += 1

        if battery_count == 0:
            return 75.0  # Default if no batteries

        # Calculate SOE based on time of day and battery activity
        base_soe = self._get_time_based_soe(current_hour)

        # Adjust based on current battery activity
        avg_battery_power = total_battery_power / battery_count

        if avg_battery_power < -1000:  # Significant charging
            # Battery is charging - higher SOE
            return min(95.0, base_soe + 10.0)
        if avg_battery_power > 1000:  # Significant discharging
            # Battery is discharging - lower SOE
            return max(15.0, base_soe - 15.0)
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
        self, circuit_id: str, instant_power: float, current_time: float
    ) -> tuple[float, float]:
        """Calculate accumulated energy for a circuit based on power and time elapsed.

        Args:
            circuit_id: Circuit identifier
            instant_power: Current power in watts (negative for production)
            current_time: Current timestamp

        Returns:
            Tuple of (produced_energy_wh, consumed_energy_wh)
        """
        # Initialize energy state if not exists
        if circuit_id not in self._circuit_energy_states:
            self._circuit_energy_states[circuit_id] = {"produced_wh": 0.0, "consumed_wh": 0.0, "last_update": current_time}

        energy_state = self._circuit_energy_states[circuit_id]
        last_update = energy_state["last_update"]
        time_elapsed_hours = (current_time - last_update) / 3600.0  # Convert seconds to hours

        # Calculate energy increment based on current power
        if instant_power > 0:
            # Positive power = consumption
            energy_increment = instant_power * time_elapsed_hours
            energy_state["consumed_wh"] += energy_increment
        elif instant_power < 0:
            # Negative power = production
            energy_increment = abs(instant_power) * time_elapsed_hours
            energy_state["produced_wh"] += energy_increment

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
    ) -> tuple[float, float]:
        """Calculate synchronized energy for tabs in the same sync group."""
        try:
            sync_config = self._get_tab_sync_config(tab_num)
        except SimulationConfigurationError:
            # Fallback to regular energy calculation if no sync config
            return self._calculate_accumulated_energy(circuit_id, instant_power, current_time)

        if not sync_config or not sync_config.get("energy_sync", False):
            # Fallback to regular energy calculation if sync not enabled
            return self._calculate_accumulated_energy(circuit_id, instant_power, current_time)

        # Use a shared energy state for all tabs in the sync group
        sync_group_id = self._tab_sync_groups.get(tab_num)
        if not sync_group_id:
            # Fallback to regular energy calculation if no sync group
            return self._calculate_accumulated_energy(circuit_id, instant_power, current_time)

        shared_circuit_id = f"sync_group_{sync_group_id}"

        # Calculate energy using the total power for the sync group
        total_power = self._sync_group_power.get(sync_group_id, instant_power * len(sync_config["tabs"]))

        return self._calculate_accumulated_energy(shared_circuit_id, total_power, current_time)
