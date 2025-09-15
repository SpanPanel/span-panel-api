"""Test 40-tab panel with unmapped tabs to achieve 100% coverage of unmapped tab logic."""

import pytest
from pathlib import Path
from span_panel_api import SpanPanelClient


class Test40TabUnmappedCoverage:
    """Test 40-tab panel configuration with intentionally unmapped tabs."""

    @pytest.mark.asyncio
    async def test_unmapped_tab_creation_with_40_tab_panel(self):
        """Test unmapped tab creation using 40-tab panel (covers lines 866-876)."""
        config_path = Path(__file__).parent.parent / "examples" / "simulation_config_40_circuit_with_battery.yaml"

        async with SpanPanelClient(
            host="40-tab-test", simulation_mode=True, simulation_config_path=str(config_path)
        ) as client:
            # Get circuits to trigger unmapped tab creation
            circuits = await client.get_circuits()

            # Should have 40 tabs total, with tabs 38-40 unmapped
            # This will hit lines 866-876 in client.py

            # Verify we have unmapped tabs
            unmapped_tabs = []
            for circuit_id in circuits.circuits.additional_properties.keys():
                if circuit_id.startswith("unmapped_tab_"):
                    tab_num = int(circuit_id.split("_")[-1])
                    unmapped_tabs.append(tab_num)

            # Should have unmapped tabs 38, 39, 40
            assert len(unmapped_tabs) >= 3, f"Expected at least 3 unmapped tabs, got {len(unmapped_tabs)}"
            assert 38 in unmapped_tabs, "Tab 38 should be unmapped"
            assert 39 in unmapped_tabs, "Tab 39 should be unmapped"
            assert 40 in unmapped_tabs, "Tab 40 should be unmapped"

            # Verify unmapped tab properties are properly set
            for tab_num in unmapped_tabs:
                circuit_id = f"unmapped_tab_{tab_num}"
                circuit = circuits.circuits.additional_properties[circuit_id]

                # These assertions test the virtual circuit creation in lines 866-876
                assert circuit.name is not None, f"Unmapped tab {tab_num} should have a name"
                # Power can be positive (consumer) or negative (producer)
                assert isinstance(circuit.instant_power_w, (int, float)), f"Unmapped tab {tab_num} should have numeric power"
                assert hasattr(circuit, "relay_state"), f"Unmapped tab {tab_num} should have relay_state"
                assert circuit.relay_state in [
                    "OPEN",
                    "CLOSED",
                    "UNKNOWN",
                ], f"Unmapped tab {tab_num} should have valid relay state"

    @pytest.mark.asyncio
    async def test_battery_systems_on_opposing_phases(self):
        """Test battery systems on opposing phased tabs (33/35, 34/36)."""
        config_path = Path(__file__).parent.parent / "examples" / "simulation_config_40_circuit_with_battery.yaml"

        async with SpanPanelClient(
            host="battery-test", simulation_mode=True, simulation_config_path=str(config_path)
        ) as client:
            # Get circuits to verify battery configurations
            circuits = await client.get_circuits()

            # Find battery systems
            battery_1 = circuits.circuits.additional_properties.get("battery_system_1")
            battery_2 = circuits.circuits.additional_properties.get("battery_system_2")

            assert battery_1 is not None, "Battery System 1 should exist"
            assert battery_2 is not None, "Battery System 2 should exist"

            # Verify battery systems can have positive or negative power (charge/discharge)
            # Battery power can be negative (charging) or positive (discharging)
            assert battery_1.instant_power_w >= -5000.0, "Battery 1 power should be within range"
            assert battery_1.instant_power_w <= 5000.0, "Battery 1 power should be within range"

            assert battery_2.instant_power_w >= -5000.0, "Battery 2 power should be within range"
            assert battery_2.instant_power_w <= 5000.0, "Battery 2 power should be within range"

    @pytest.mark.asyncio
    async def test_panel_circuit_alignment_with_40_tabs(self):
        """Test panel-circuit alignment with 40-tab configuration."""
        config_path = Path(__file__).parent.parent / "examples" / "simulation_config_40_circuit_with_battery.yaml"

        async with SpanPanelClient(
            host="alignment-test", simulation_mode=True, simulation_config_path=str(config_path)
        ) as client:
            # Get both panel and circuit data
            panel_state = await client.get_panel_state()
            circuits = await client.get_circuits()

            # Calculate total circuit power (all circuits now show positive values)
            total_circuit_power = 0.0
            for circuit in circuits.circuits.additional_properties.values():
                total_circuit_power += circuit.instant_power_w

            # Panel grid power should be reasonable (consumption - production)
            panel_grid_power = panel_state.instant_grid_power_w

            # Panel grid power should be less than total circuit power since production reduces net consumption
            # and should be positive (net import) or negative (net export)
            assert (
                abs(panel_grid_power) <= total_circuit_power
            ), f"Panel power ({panel_grid_power}W) should be reasonable compared to total circuit power ({total_circuit_power}W)"

            # Verify we have 40 branches in panel state
            assert len(panel_state.branches) == 40, f"Panel should have 40 branches, got {len(panel_state.branches)}"
