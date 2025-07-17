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


# Legacy variation types for backwards compatibility
class CircuitVariation(TypedDict, total=False):
    """Variation parameters for individual circuits."""

    power_variation: float
    energy_variation: float
    relay_state: str
    priority: str


class BranchVariation(TypedDict, total=False):
    """Variation parameters for panel branches."""

    power_variation: float
    relay_state: str


class PanelVariation(TypedDict, total=False):
    """Variation parameters for panel-level data."""

    main_relay_state: str
    dsm_grid_state: str
    dsm_state: str
    instant_grid_power_variation: float


class StatusVariation(TypedDict, total=False):
    """Variation parameters for status data."""

    door_state: str
    main_relay_state: str
    proximity_proven: bool
    eth0_link: bool
    wlan_link: bool
    wwwan_link: bool


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


class CircuitTemplate(TypedDict):
    """Circuit template configuration."""

    power_range: list[float]  # [min, max] in Watts
    energy_behavior: str  # "consume_only", "produce_only", "mixed"
    typical_power: float  # Watts
    power_variation: float  # 0.0 to 1.0 (percentage)
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


class SimulationParams(TypedDict, total=False):
    """Global simulation parameters."""

    update_interval: int  # Seconds
    time_acceleration: float  # Multiplier for time progression
    noise_factor: float  # Random noise percentage
    enable_realistic_behaviors: bool


class SimulationConfig(TypedDict):
    """Complete simulation configuration."""

    panel_config: PanelConfig
    circuit_templates: dict[str, CircuitTemplateExtended]
    circuits: list[CircuitDefinitionExtended]
    unmapped_tabs: list[int]
    simulation_params: SimulationParams
    unmapped_tab_templates: NotRequired[dict[str, CircuitTemplateExtended]]


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

        base_power = template["typical_power"]

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
        variation = template.get("power_variation", 0.1)
        noise_factor = self._config["simulation_params"].get("noise_factor", 0.02)
        total_variation = variation + noise_factor

        power_multiplier = 1.0 + random.uniform(-total_variation, total_variation)  # nosec B311
        final_power = base_power * power_multiplier

        # Clamp to template range
        min_power, max_power = template["power_range"]
        final_power = max(min_power, min(max_power, final_power))

        return final_power

    def _apply_time_of_day_modulation(
        self, base_power: float, template: CircuitTemplateExtended, current_time: float
    ) -> float:
        """Apply time-of-day power modulation."""
        current_hour = int((current_time % 86400) / 3600)  # Hour of day (0-23)

        profile = template.get("time_of_day_profile", {})
        peak_hours = profile.get("peak_hours", [])

        if template.get("energy_behavior") == "produce_only":
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
            return base_power  # pragma: no cover
        battery_config: dict[str, Any] = battery_config_raw

        charge_hours: list[int] = battery_config.get("charge_hours", [])
        discharge_hours: list[int] = battery_config.get("discharge_hours", [])
        idle_hours: list[int] = battery_config.get("idle_hours", [])

        max_charge_power: float = battery_config.get("max_charge_power", -3000.0)
        max_discharge_power: float = battery_config.get("max_discharge_power", 2500.0)

        if current_hour in charge_hours:
            # Solar hours - battery charges (negative power)
            solar_intensity = self._get_solar_intensity_from_config(current_hour, battery_config)  # pragma: no cover
            charge_power = max_charge_power * solar_intensity  # pragma: no cover
            return charge_power  # pragma: no cover

        if current_hour in discharge_hours:
            # Peak demand hours - battery discharges (positive power)
            demand_factor = self._get_demand_factor_from_config(current_hour, battery_config)  # pragma: no cover
            discharge_power = max_discharge_power * demand_factor  # pragma: no cover
            return discharge_power  # pragma: no cover

        if current_hour in idle_hours:
            # Low activity hours - minimal power flow
            idle_range: list[float] = battery_config.get("idle_power_range", [-100.0, 100.0])
            return random.uniform(idle_range[0], idle_range[1])  # nosec B311

        # Transition hours - gradual change  # pragma: no cover
        return base_power * 0.1  # pragma: no cover

    def _get_solar_intensity_from_config(self, hour: int, battery_config: dict[str, Any]) -> float:
        """Get solar intensity from YAML configuration."""
        solar_profile: dict[int, float] = battery_config.get("solar_intensity_profile", {})  # pragma: no cover
        return solar_profile.get(hour, 0.1)  # Default to minimal intensity  # pragma: no cover

    def _get_demand_factor_from_config(self, hour: int, battery_config: dict[str, Any]) -> float:
        """Get demand factor from YAML configuration."""
        demand_profile: dict[int, float] = battery_config.get("demand_factor_profile", {})  # pragma: no cover
        return demand_profile.get(hour, 0.3)  # Default to low demand  # pragma: no cover


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
        self._last_update_times: dict[str, float] = {}
        self._circuit_states: dict[str, dict[str, Any]] = {}
        self._behavior_engine: RealisticBehaviorEngine | None = None
        self._dynamic_overrides: dict[str, Any] = {}
        self._global_overrides: dict[str, Any] = {}

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
            if self._base_data is not None:  # pragma: no cover
                return  # pragma: no cover

            # Load configuration
            await self._load_config_async()

            # Generate data from YAML config (required)
            if not self._config:  # pragma: no cover
                raise ValueError("YAML configuration is required")  # pragma: no cover

            self._base_data = await self._generate_base_data_from_config()
            self._behavior_engine = RealisticBehaviorEngine(self._simulation_start_time, self._config)

    async def _load_config_async(self) -> None:
        """Load simulation configuration asynchronously."""
        if self._config_data:
            # Validate provided config data
            self._validate_yaml_config(self._config_data)
            self._config = self._config_data  # pragma: no cover
        elif self._config_path and self._config_path.exists():  # pragma: no cover
            loop = asyncio.get_event_loop()  # pragma: no cover
            self._config = await loop.run_in_executor(None, self._load_yaml_config, self._config_path)  # pragma: no cover
        else:  # pragma: no cover
            # No config provided - simulation cannot start
            raise ValueError(
                "Simulation mode requires either config_data or a valid config_path with YAML configuration"
            )  # pragma: no cover

        # Override serial number if provided
        if self._serial_number_override and self._config:
            self._config["panel_config"]["serial_number"] = self._serial_number_override

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

        # Validate panel_config
        panel_config = config_data["panel_config"]
        if not isinstance(panel_config, dict):
            raise ValueError("panel_config must be a dictionary")

        required_panel_fields = ["serial_number", "total_tabs", "main_size"]
        for field in required_panel_fields:
            if field not in panel_config:
                raise ValueError(f"Missing required panel_config field: {field}")

        # Validate circuit_templates
        circuit_templates = config_data["circuit_templates"]
        if not isinstance(circuit_templates, dict):
            raise ValueError("circuit_templates must be a dictionary")

        if not circuit_templates:
            raise ValueError("At least one circuit template must be defined")

        for template_name, template in circuit_templates.items():
            if not isinstance(template, dict):
                raise ValueError(f"Circuit template '{template_name}' must be a dictionary")

            required_template_fields = [
                "power_range",
                "energy_behavior",
                "typical_power",
                "power_variation",
                "relay_behavior",
                "priority",
            ]
            for field in required_template_fields:
                if field not in template:
                    raise ValueError(f"Missing required field '{field}' in circuit template '{template_name}'")

        # Validate circuits
        circuits = config_data["circuits"]
        if not isinstance(circuits, list):
            raise ValueError("circuits must be a list")

        if not circuits:
            raise ValueError("At least one circuit must be defined")

        for i, circuit in enumerate(circuits):
            if not isinstance(circuit, dict):
                raise ValueError(f"Circuit {i} must be a dictionary")

            required_circuit_fields = ["id", "name", "template", "tabs"]
            for field in required_circuit_fields:
                if field not in circuit:
                    raise ValueError(f"Missing required field '{field}' in circuit {i}")

            # Validate template reference
            template_name = circuit["template"]
            if template_name not in circuit_templates:
                raise ValueError(f"Circuit {i} references unknown template '{template_name}'")

    async def _generate_base_data_from_config(self) -> dict[str, dict[str, Any]]:
        """Generate base simulation data from YAML configuration."""
        if not self._config or not self._config["circuits"]:
            raise ValueError("YAML configuration with circuits is required")

        return await self._generate_from_config()

    async def _generate_from_config(self) -> dict[str, dict[str, Any]]:
        """Generate simulation data from configuration."""
        if not self._config:
            raise ValueError("Configuration not loaded")

        circuits_data = {}
        total_power = 0.0
        total_produced_energy = 0.0
        total_consumed_energy = 0.0

        for circuit_def in self._config["circuits"]:
            template_name = circuit_def["template"]
            template = self._config["circuit_templates"][template_name]

            # Apply overrides
            final_template = deepcopy(template)
            if "overrides" in circuit_def:
                final_template.update(circuit_def["overrides"])  # type: ignore[typeddict-item]

            # Generate realistic power using behavior engine
            behavior_engine = RealisticBehaviorEngine(time.time(), self._config)
            instant_power = behavior_engine.get_circuit_power(circuit_def["id"], final_template, time.time())

            # Generate energy values based on power
            produced_energy = random.uniform(500, 2000) if instant_power < 0 else 0.0  # nosec B311
            consumed_energy = random.uniform(10000, 100000) if instant_power > 0 else 0.0  # nosec B311

            circuit_data = {
                "id": circuit_def["id"],
                "name": circuit_def["name"],
                "relayState": "CLOSED",
                "instantPowerW": instant_power,
                "instantPowerUpdateTimeS": int(time.time()),
                "producedEnergyWh": produced_energy,
                "consumedEnergyWh": consumed_energy,
                "energyAccumUpdateTimeS": int(time.time()),
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
            raise ValueError("Configuration not loaded")

        # Panel grid power should exactly match the total of all circuit power
        # Negative values indicate production (solar), positive indicates consumption
        grid_power = total_power

        return {
            "instantGridPowerW": grid_power,
            "instantPanelStateOfEnergyPercent": random.uniform(0.6, 0.9),  # nosec B311
            "serialNumber": self._config["panel_config"]["serial_number"],
            "mainRelayState": "CLOSED",
            "dsmGridState": "LIVE",
            "dsmState": "ON_GRID",
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
            "currentRunConfig": 1,
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
            current_time_ms = int(time.time() * 1000)

            # Handle unmapped tabs
            if tab_num not in mapped_tabs:
                # Check if this unmapped tab has a specific template defined
                unmapped_tab_config = self._config.get("unmapped_tab_templates", {}).get(str(tab_num))
                if unmapped_tab_config:
                    # Apply behavior engine to unmapped tab with its template
                    behavior_engine = RealisticBehaviorEngine(time.time(), self._config)
                    baseline_power = behavior_engine.get_circuit_power(
                        f"unmapped_tab_{tab_num}", unmapped_tab_config, time.time()
                    )
                else:
                    # Default unmapped tab baseline consumption (10-200W)
                    baseline_power = random.uniform(10.0, 200.0)  # nosec B311
            else:
                baseline_power = 0.0  # Mapped tabs get power from circuit definitions

            branch = {
                "id": f"branch_{tab_num}",
                "relayState": "CLOSED",
                "instantPowerW": baseline_power,
                "importedActiveEnergyWh": random.uniform(100, 1000),  # nosec B311
                "exportedActiveEnergyWh": 0.0,
                "measureStartTsMs": current_time_ms,
                "measureDurationMs": 5000,  # 5 second measurement window
                "isMeasureValid": True,
            }
            branches.append(branch)

        return branches

    def _generate_status_data(self) -> dict[str, Any]:
        """Generate status data from configuration."""
        if not self._config:  # pragma: no cover
            return {}  # pragma: no cover

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

        raise ValueError("No configuration loaded - serial number not available")  # pragma: no cover

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
                    template_raw: CircuitTemplateExtended | dict[Any, Any] = self._config["circuit_templates"].get(
                        template_name, {}
                    )
                    if not isinstance(template_raw, dict):
                        continue
                    template_dict: dict[str, Any] = template_raw  # type: ignore
                    if template_dict.get("battery_behavior", {}).get("enabled", False):
                        # Calculate what the battery power would be at this time
                        behavior_engine = RealisticBehaviorEngine(current_time, self._config)
                        # Convert dict to CircuitTemplateExtended for type compatibility
                        template_extended: CircuitTemplateExtended = template_dict  # type: ignore
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
            return min(95.0, base_soe + 10.0)  # pragma: no cover
        if avg_battery_power > 1000:  # Significant discharging
            # Battery is discharging - lower SOE
            return max(15.0, base_soe - 15.0)  # pragma: no cover
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
                circuit_info["priority"] = overrides["priority"]  # pragma: no cover

        # Apply global overrides
        if "power_multiplier" in self._global_overrides:
            circuit_info["instantPowerW"] *= self._global_overrides["power_multiplier"]  # pragma: no cover
