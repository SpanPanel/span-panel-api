"""Test 32-circuit panel unmapped tabs 30 & 32 for solar production."""

import pytest
from span_panel_api import SpanPanelClient


@pytest.fixture
def client_32_circuit():
    """Create client with 32-circuit simulation."""
    config_path = "examples/simulation_config_32_circuit.yaml"
    return SpanPanelClient("32-circuit-host", simulation_mode=True, simulation_config_path=config_path)


class TestSolarUnmappedTabs:
    """Test solar production in unmapped tabs 30 & 32."""

    @pytest.mark.asyncio
    async def test_tabs_30_32_are_unmapped(self, client_32_circuit):
        """Test that tabs 30 and 32 are unmapped and show as virtual circuits."""
        circuits = await client_32_circuit.get_circuits()
        circuit_data = circuits.circuits.additional_properties

        # Verify unmapped_tab_30 and unmapped_tab_32 exist
        assert "unmapped_tab_30" in circuit_data, "Tab 30 should be unmapped"
        assert "unmapped_tab_32" in circuit_data, "Tab 32 should be unmapped"

        # Verify their names
        tab30_circuit = circuit_data["unmapped_tab_30"]
        tab32_circuit = circuit_data["unmapped_tab_32"]

        assert tab30_circuit.name == "Unmapped Tab 30"
        assert tab32_circuit.name == "Unmapped Tab 32"

    @pytest.mark.asyncio
    async def test_solar_production_power_ranges(self, client_32_circuit):
        """Test that tabs 30 & 32 show solar production (negative power)."""
        circuits = await client_32_circuit.get_circuits()
        circuit_data = circuits.circuits.additional_properties

        # Get panel state to check branch power too
        panel_state = await client_32_circuit.get_panel_state()

        print("\n=== Solar Production Test ===")

        for tab_num in [30, 32]:
            # Check circuit power
            circuit_id = f"unmapped_tab_{tab_num}"
            circuit = circuit_data[circuit_id]
            circuit_power = circuit.instant_power_w

            # Check branch power
            branch = panel_state.branches[tab_num - 1]  # 0-indexed
            branch_power = branch.instant_power_w

            print(f"Tab {tab_num}:")
            print(f"  Circuit Power: {circuit_power:.1f}W")
            print(f"  Branch Power: {branch_power:.1f}W")

            # Solar production should be negative (or 0 at night)
            assert circuit_power <= 0.0, f"Tab {tab_num} should show production (≤0W), got {circuit_power}W"
            assert branch_power <= 0.0, f"Tab {tab_num} branch should show production (≤0W), got {branch_power}W"

            # During daylight hours, should be producing (negative power)
            # At night, should be 0
            if circuit_power < 0:
                # Producing - should be within expected range
                assert (
                    -1500.0 <= circuit_power <= 0.0
                ), f"Tab {tab_num} production {circuit_power}W outside range -1500W to 0W"
                print(f"  ✓ Tab {tab_num} producing {abs(circuit_power):.1f}W solar power")
            else:
                # Not producing (night time)
                assert circuit_power == 0.0, f"Tab {tab_num} should be 0W at night, got {circuit_power}W"
                print(f"  ✓ Tab {tab_num} not producing (night time)")

    @pytest.mark.asyncio
    async def test_240v_solar_pair_behavior(self, client_32_circuit):
        """Test that tabs 30 & 32 behave as a 240V solar pair."""
        circuits = await client_32_circuit.get_circuits()
        circuit_data = circuits.circuits.additional_properties

        tab30_circuit = circuit_data["unmapped_tab_30"]
        tab32_circuit = circuit_data["unmapped_tab_32"]

        tab30_power = tab30_circuit.instant_power_w
        tab32_power = tab32_circuit.instant_power_w

        print(f"\n=== 240V Solar Pair ===")
        print(f"Tab 30 Power: {tab30_power:.1f}W")
        print(f"Tab 32 Power: {tab32_power:.1f}W")
        print(f"Combined Power: {tab30_power + tab32_power:.1f}W")

        # Both should be producing similar amounts (within 35% of each other)
        # Solar panels produce negative power (power generation)
        if tab30_power < 0 and tab32_power < 0:
            power_ratio = abs(tab30_power) / abs(tab32_power)
            assert 0.65 <= power_ratio <= 1.54, f"Solar tabs should produce similar power, ratio: {power_ratio:.2f}"
            print(f"✓ Solar tabs producing similar power (ratio: {power_ratio:.2f})")

            # Combined power for 240V circuit
            combined_power = tab30_power + tab32_power
            assert combined_power <= 0.0, "Combined solar production should be negative"
            print(f"✓ Combined 240V solar production: {abs(combined_power):.1f}W")
        else:
            print("✓ Both tabs not producing (night time)")

    @pytest.mark.asyncio
    async def test_other_unmapped_tabs_normal(self, client_32_circuit):
        """Test that other unmapped tabs (if any) show normal consumption."""
        circuits = await client_32_circuit.get_circuits()
        circuit_data = circuits.circuits.additional_properties

        # Find other unmapped tabs (not 30 or 32)
        other_unmapped = []
        for circuit_id in circuit_data.keys():
            if isinstance(circuit_id, str) and circuit_id.startswith("unmapped_tab_"):
                tab_num = int(circuit_id.split("_")[-1])
                if tab_num not in [30, 32]:
                    other_unmapped.append((circuit_id, tab_num))

        print(f"\n=== Other Unmapped Tabs ===")
        print(f"Found {len(other_unmapped)} other unmapped tabs")

        for circuit_id, tab_num in other_unmapped:
            circuit = circuit_data[circuit_id]
            power = circuit.instant_power_w
            print(f"Tab {tab_num}: {power:.1f}W")

            # Should be positive consumption power
            assert power >= 0.0, f"Non-solar unmapped tab {tab_num} should consume power (≥0W), got {power}W"
            assert 10.0 <= power <= 200.0, f"Tab {tab_num} power {power}W should be in normal range 10-200W"
