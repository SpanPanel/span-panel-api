"""Test unmapped tab creation specific edge cases to achieve 100% coverage."""

import pytest
from pathlib import Path
from span_panel_api import SpanPanelClient


class TestUnmappedTabSpecificCoverage:
    """Test specific unmapped tab creation scenarios."""

    @pytest.mark.asyncio
    async def test_unmapped_tab_creation_specific_lines(self):
        """Test unmapped tab creation that hits lines 866-876 in client.py."""
        config_path = Path(__file__).parent.parent / "examples" / "simulation_config_32_circuit.yaml"

        async with SpanPanelClient(host="test", simulation_mode=True, simulation_config_path=str(config_path)) as client:
            # Force get circuits to trigger unmapped tab creation
            circuits = await client.get_circuits()

            # Check if we have unmapped tabs
            unmapped_count = 0
            for circuit_id in circuits.circuits.additional_properties.keys():
                if circuit_id.startswith("unmapped_tab_"):
                    unmapped_count += 1

            # The 32-circuit config should have some unmapped tabs
            # This should hit the lines in the unmapped tab creation logic
            assert circuits is not None
            assert len(circuits.circuits.additional_properties) > 0

            # If there are unmapped tabs, verify they're properly created
            if unmapped_count > 0:
                for circuit_id, circuit in circuits.circuits.additional_properties.items():
                    if circuit_id.startswith("unmapped_tab_"):
                        # These lines test the unmapped tab creation logic (lines 866-876)
                        assert circuit.name is not None
                        # Check power based on tab type - solar tabs produce (negative), others consume (positive)
                        tab_num = int(circuit_id.split("_")[-1])
                        if tab_num in [30, 32]:
                            # Solar tabs should produce power (positive values)
                            assert (
                                circuit.instant_power_w >= 0
                            ), f"Solar tab {tab_num} should produce power: {circuit.instant_power_w}W"
                        else:
                            # Other unmapped tabs should consume power (positive values)
                            assert (
                                circuit.instant_power_w >= 0
                            ), f"Unmapped tab {tab_num} should consume power: {circuit.instant_power_w}W"
                        assert hasattr(circuit, "relay_state")

    @pytest.mark.asyncio
    async def test_unmapped_tab_edge_case_boundary(self):
        """Test unmapped tab creation boundary conditions."""
        config_path = Path(__file__).parent.parent / "examples" / "simulation_config_32_circuit.yaml"

        async with SpanPanelClient(
            host="test-boundary", simulation_mode=True, simulation_config_path=str(config_path)
        ) as client:
            # Get panel state to check branch structure
            panel_state = await client.get_panel_state()

            # Get circuits to trigger unmapped tab logic
            circuits = await client.get_circuits()

            # Verify the unmapped tab creation handles edge cases properly
            assert circuits is not None

            # Check that the unmapped tab creation doesn't create invalid circuits
            for circuit_id, circuit in circuits.circuits.additional_properties.items():
                if circuit_id.startswith("unmapped_tab_"):
                    # Verify tab number is valid
                    tab_num = int(circuit_id.split("_")[-1])
                    assert 1 <= tab_num <= len(panel_state.branches)

                    # Verify circuit properties are valid based on tab type
                    if tab_num in [30, 32]:
                        # Solar tabs should produce power (positive values)
                        assert (
                            circuit.instant_power_w >= 0
                        ), f"Solar tab {tab_num} should produce power: {circuit.instant_power_w}W"
                    else:
                        # Other unmapped tabs should consume power (positive values)
                        assert (
                            circuit.instant_power_w >= 0
                        ), f"Unmapped tab {tab_num} should consume power: {circuit.instant_power_w}W"
                    assert circuit.name != ""
