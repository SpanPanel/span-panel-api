"""SPAN Panel API Simulation Engine.

This module provides dynamic simulation capabilities for the SPAN Panel API client,
allowing realistic testing without requiring physical hardware.
"""

from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import time
from typing import Any, TypedDict

import numpy as np


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


class DynamicSimulationEngine:
    """Dynamic simulation engine for SPAN Panel API responses."""

    def __init__(self, serial_number: str | None = None) -> None:
        """Initialize the simulation engine.

        Args:
            serial_number: Custom serial number for the simulated panel.
                          If None, uses default "sim-serial-123".
        """
        self._base_data = self._load_fixtures()
        self._simulation_start_time = time.time()
        self._last_update_times: dict[str, float] = {}
        self._circuit_states: dict[str, dict[str, Any]] = {}
        self._serial_number = serial_number or "sim-serial-123"

    def _load_fixtures(self) -> dict[str, dict[str, Any]]:
        """Load fixture data from response files."""
        fixtures_dir = Path(__file__).parent.parent.parent / "tests" / "simulation_fixtures"

        fixtures = {}

        # Load circuits fixture
        circuits_file = fixtures_dir / "circuits.response.txt"
        if circuits_file.exists():
            with circuits_file.open() as f:
                # Skip HTTP headers, get JSON content
                lines = f.readlines()
                json_line = lines[-1].strip()
                fixtures["circuits"] = json.loads(json_line)

        # Load panel fixture
        panel_file = fixtures_dir / "panel.response.txt"
        if panel_file.exists():
            with panel_file.open() as f:
                lines = f.readlines()
                json_line = lines[-1].strip()
                fixtures["panel"] = json.loads(json_line)

        # Load status fixture
        status_file = fixtures_dir / "status.response.txt"
        if status_file.exists():
            with status_file.open() as f:
                lines = f.readlines()
                json_line = lines[-1].strip()
                fixtures["status"] = json.loads(json_line)

        # Load storage SOE fixture
        soe_file = fixtures_dir / "soe.response.txt"
        if soe_file.exists():
            with soe_file.open() as f:
                lines = f.readlines()
                json_line = lines[-1].strip()
                fixtures["soe"] = json.loads(json_line)

        return fixtures

    def get_circuits_data(
        self,
        variations: dict[str, CircuitVariation] | None = None,
        global_power_variation: float | None = None,
        global_energy_variation: float | None = None,
    ) -> dict[str, Any]:
        """Get circuits data with dynamic variations."""
        if "circuits" not in self._base_data:
            raise ValueError("Circuits fixture data not available")

        current_time = time.time()
        circuits_data = deepcopy(self._base_data["circuits"])

        for circuit_id, circuit in circuits_data["circuits"].items():
            # Get variations for this specific circuit
            circuit_variation = variations.get(circuit_id, {}) if variations else {}

            # Apply time-based energy accumulation
            self._update_energy_accumulation(circuit_id, circuit, current_time)

            # Apply power variation (specific > global > default)
            power_var = circuit_variation.get("power_variation", global_power_variation)
            self._apply_power_variation(circuit, power_var)

            # Apply energy variation (specific > global > default)
            energy_var = circuit_variation.get("energy_variation", global_energy_variation)
            if energy_var is not None:
                self._apply_energy_variation(circuit, energy_var)

            # Apply relay state override
            if "relay_state" in circuit_variation:
                circuit["relayState"] = circuit_variation["relay_state"]

            # Apply priority override
            if "priority" in circuit_variation:
                circuit["priority"] = circuit_variation["priority"]

            # Update timestamps
            circuit["instantPowerUpdateTimeS"] = int(current_time)
            circuit["energyAccumUpdateTimeS"] = int(current_time)

        return circuits_data

    def get_panel_state_data(
        self,
        variations: dict[int, BranchVariation] | None = None,
        panel_variations: PanelVariation | None = None,
        global_power_variation: float | None = None,
    ) -> dict[str, Any]:
        """Get panel state data with dynamic variations."""
        if "panel" not in self._base_data:
            raise ValueError("Panel fixture data not available")

        panel_data = deepcopy(self._base_data["panel"])

        # Apply panel-level variations
        if panel_variations:
            if "main_relay_state" in panel_variations:
                panel_data["mainRelayState"] = panel_variations["main_relay_state"]

            if "dsm_grid_state" in panel_variations:
                panel_data["dsmGridState"] = panel_variations["dsm_grid_state"]

            if "dsm_state" in panel_variations:
                panel_data["dsmState"] = panel_variations["dsm_state"]

            if "instant_grid_power_variation" in panel_variations:
                base_power = panel_data["instantGridPowerW"]
                variation = panel_variations["instant_grid_power_variation"]
                panel_data["instantGridPowerW"] = base_power * (1 + variation)

        # Apply branch-level variations
        for branch in panel_data["branches"]:
            branch_id = branch["id"]
            branch_variation = variations.get(branch_id, {}) if variations else {}

            # Apply power variation (specific > global > default)
            power_var = branch_variation.get("power_variation", global_power_variation)
            if power_var is not None:
                base_power = branch["instantPowerW"]
                variation_amount = np.random.uniform(-power_var, power_var)
                branch["instantPowerW"] = base_power * (1 + variation_amount)

            # Apply relay state override
            if "relay_state" in branch_variation:
                branch["relayState"] = branch_variation["relay_state"]

            # Update timestamps
            branch["measureStartTsMs"] = int(time.time() * 1000)

        return panel_data

    def get_status_data(self, variations: StatusVariation | None = None) -> dict[str, Any]:
        """Get status data with field variations."""
        if "status" not in self._base_data:
            raise ValueError("Status fixture data not available")

        status_data = deepcopy(self._base_data["status"])

        # Override serial number with configured value
        if "system" in status_data:
            status_data["system"]["serial"] = self._serial_number

        if variations:
            # Apply system variations
            if "door_state" in variations:
                status_data["system"]["doorState"] = variations["door_state"]

            if "main_relay_state" in variations and "mainRelayState" in status_data.get("system", {}):
                # Note: This might need adjustment based on actual status structure
                status_data["system"]["mainRelayState"] = variations["main_relay_state"]

            if "proximity_proven" in variations:
                status_data["system"]["proximityProven"] = variations["proximity_proven"]

            # Apply network variations
            if "eth0_link" in variations:
                status_data["network"]["eth0Link"] = variations["eth0_link"]

            if "wlan_link" in variations:
                status_data["network"]["wlanLink"] = variations["wlan_link"]

            if "wwwan_link" in variations:
                status_data["network"]["wwanLink"] = variations["wwwan_link"]

        return status_data

    def get_storage_soe_data(self, soe_variation: float | None = None) -> dict[str, Any]:
        """Get storage state of energy data with variation."""
        if "soe" not in self._base_data:
            raise ValueError("Storage SOE fixture data not available")

        soe_data = deepcopy(self._base_data["soe"])

        if soe_variation is not None:
            base_percentage = soe_data["soe"]["percentage"]
            variation_amount = np.random.uniform(-soe_variation, soe_variation)
            new_percentage = base_percentage * (1 + variation_amount)

            # Clamp to valid range
            new_percentage = max(0, min(100, new_percentage))
            soe_data["soe"]["percentage"] = new_percentage

        return soe_data

    def _update_energy_accumulation(self, circuit_id: str, circuit: dict[str, Any], current_time: float) -> None:
        """Update energy values based on time elapsed and current power."""
        last_update = self._last_update_times.get(circuit_id, self._simulation_start_time)
        time_delta_hours = (current_time - last_update) / 3600  # Convert to hours

        # Get current power (negative = consuming, positive = producing)
        current_power = circuit["instantPowerW"]

        # Calculate energy delta (Wh = W * hours)
        energy_delta = abs(current_power) * time_delta_hours

        if current_power < 0:  # Consuming power
            circuit["consumedEnergyWh"] += energy_delta
        else:  # Producing power (rare for circuits, but possible)
            circuit["producedEnergyWh"] += energy_delta

        self._last_update_times[circuit_id] = current_time

    def _apply_power_variation(self, circuit: dict[str, Any], power_variation: float | None) -> None:
        """Apply realistic power fluctuations based on circuit type."""
        if power_variation is None:
            power_variation = 0.1  # Default 10% variation

        base_power = circuit["instantPowerW"]
        circuit_name = circuit["name"].lower()

        # Circuit-specific behavior patterns
        if "ev" in circuit_name:
            # EV charging: either off or high power with occasional fluctuations
            if np.random.random() < 0.7:  # 70% chance it's off
                circuit["instantPowerW"] = 0.0
            else:
                # When charging, vary around high power consumption
                variation = np.random.uniform(-power_variation, power_variation)
                circuit["instantPowerW"] = base_power * (1 + variation)

        elif "air conditioner" in circuit_name or "furnace" in circuit_name:
            # HVAC: cyclical on/off behavior
            if np.random.random() < 0.4:  # 40% chance it's running
                variation = np.random.uniform(-power_variation * 0.5, power_variation * 0.5)
                circuit["instantPowerW"] = base_power * (1 + variation)
            else:
                circuit["instantPowerW"] = 0.0

        elif "lights" in circuit_name:
            # Lights: relatively stable when on
            if base_power != 0:  # Only vary if lights are on
                variation = np.random.uniform(-power_variation * 0.2, power_variation * 0.2)
                circuit["instantPowerW"] = base_power * (1 + variation)

        elif "refrigerator" in circuit_name:
            # Refrigerator: compressor cycling
            variation = np.random.uniform(-power_variation * 0.3, power_variation * 0.3)
            circuit["instantPowerW"] = base_power * (1 + variation)

        else:
            # General appliances: moderate variation
            variation = np.random.uniform(-power_variation, power_variation)
            circuit["instantPowerW"] = base_power * (1 + variation)

    def _apply_energy_variation(self, circuit: dict[str, Any], energy_variation: float) -> None:
        """Apply variation to energy values."""
        # Apply variation to consumed energy
        consumed_variation = np.random.uniform(-energy_variation, energy_variation)
        circuit["consumedEnergyWh"] *= 1 + consumed_variation

        # Apply variation to produced energy
        produced_variation = np.random.uniform(-energy_variation, energy_variation)
        circuit["producedEnergyWh"] *= 1 + produced_variation

        # Ensure non-negative values
        circuit["consumedEnergyWh"] = max(0, circuit["consumedEnergyWh"])
        circuit["producedEnergyWh"] = max(0, circuit["producedEnergyWh"])
