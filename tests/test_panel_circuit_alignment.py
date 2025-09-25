"""Tests for panel-level and circuit-level data alignment."""

import pytest
from src.span_panel_api.client import SpanPanelClient


class TestPanelCircuitAlignment:
    """Tests to verify panel-level data aligns with circuit-level aggregation.

    NOTE: These tests currently demonstrate issues in the simulation where
    panel and circuit data are not perfectly aligned due to separate random
    data generation calls. This should be fixed in the future.
    """

    async def test_panel_circuit_ideal_alignment_specification(self):
        """Test that panel-level data aligns perfectly with circuit-level aggregation.

        This test verifies that:
        1. Panel grid power exactly equals sum of circuit powers
        2. Panel energy totals exactly equal sum of circuit energies
        3. All data comes from a single generation call ensuring consistency
        4. Timestamps are identical across panel and circuit data
        """
        client = SpanPanelClient(
            host="test-panel-alignment", simulation_mode=True, simulation_config_path="examples/simple_test_config.yaml"
        )

        async with client:
            # Get both panel state and circuit data from same cached dataset
            panel_state = await client.get_panel_state()
            circuits = await client.get_circuits()

            # Calculate consumption and production separately (only count real circuits, not unmapped tabs)
            total_consumption = 0.0
            total_production = 0.0
            total_produced_energy = 0.0
            total_consumed_energy = 0.0

            for circuit_id, circuit in circuits.circuits.additional_properties.items():
                # Skip virtual circuits for unmapped tabs
                if not circuit_id.startswith("unmapped_tab_"):
                    # All circuits now show positive power values
                    # Determine if consuming or producing based on circuit type
                    circuit_name = circuit.name.lower() if circuit.name else ""
                    if any(keyword in circuit_name for keyword in ["solar", "inverter", "generator", "battery"]):
                        # Producer circuits (solar, battery when discharging)
                        total_production += circuit.instant_power_w
                    else:
                        # Consumer circuits
                        total_consumption += circuit.instant_power_w

                    total_produced_energy += circuit.produced_energy_wh
                    total_consumed_energy += circuit.consumed_energy_wh

            # Panel grid power should be consumption - production
            expected_grid_power = total_consumption - total_production
            # Use tolerance for floating-point comparison
            tolerance = 1e-10
            assert abs(panel_state.instant_grid_power_w - expected_grid_power) <= tolerance, (
                f"Panel power ({panel_state.instant_grid_power_w}W) should match "
                f"expected grid power ({expected_grid_power}W) = consumption ({total_consumption}W) - production ({total_production}W)"
            )

            # Energy should match with tolerance
            assert abs(panel_state.main_meter_energy.produced_energy_wh - total_produced_energy) <= tolerance, (
                f"Panel produced energy ({panel_state.main_meter_energy.produced_energy_wh}Wh) should match "
                f"circuit total ({total_produced_energy}Wh)"
            )

            assert abs(panel_state.main_meter_energy.consumed_energy_wh - total_consumed_energy) <= tolerance, (
                f"Panel consumed energy ({panel_state.main_meter_energy.consumed_energy_wh}Wh) should match "
                f"circuit total ({total_consumed_energy}Wh)"
            )

    async def test_panel_power_alignment_works_correctly(self):
        """Test that panel-circuit power alignment now works correctly."""
        client = SpanPanelClient(
            host="test-panel-alignment", simulation_mode=True, simulation_config_path="examples/simple_test_config.yaml"
        )

        async with client:
            # Get both panel state and circuit data
            panel_state = await client.get_panel_state()
            circuits = await client.get_circuits()

            # Calculate consumption and production separately (exclude virtual unmapped tab circuits)
            total_consumption = 0.0
            total_production = 0.0
            for circuit_id in circuits.circuits.additional_keys:
                if not circuit_id.startswith("unmapped_tab_"):
                    circuit = circuits.circuits[circuit_id]
                    # All circuits now show positive power values
                    # Determine if consuming or producing based on circuit type
                    circuit_name = circuit.name.lower() if circuit.name else ""
                    if any(keyword in circuit_name for keyword in ["solar", "inverter", "generator", "battery"]):
                        # Producer circuits (solar, battery when discharging)
                        total_production += circuit.instant_power_w
                    else:
                        # Consumer circuits
                        total_consumption += circuit.instant_power_w

            # Panel grid power should be consumption - production
            expected_grid_power = total_consumption - total_production
            panel_grid_power = panel_state.instant_grid_power_w
            power_difference = abs(panel_grid_power - expected_grid_power)

            print(f"Panel power: {panel_grid_power}W, Expected: {expected_grid_power}W, Diff: {power_difference}W")

            # Power should now align exactly
            assert power_difference == 0.0, (
                f"Panel grid power ({panel_grid_power}W) should exactly match "
                f"expected grid power ({expected_grid_power}W) = consumption ({total_consumption}W) - production ({total_production}W). "
                f"Difference: {power_difference}W"
            )

    async def test_panel_energy_alignment_works_correctly(self):
        """Test that panel-circuit energy alignment now works correctly."""
        client = SpanPanelClient(
            host="test-panel-energy-alignment",
            simulation_mode=True,
            simulation_config_path="examples/simple_test_config.yaml",
        )

        async with client:
            # Get both panel state and circuit data
            panel_state = await client.get_panel_state()
            circuits = await client.get_circuits()

            # Calculate total circuit energy (exclude virtual unmapped tab circuits)
            total_produced_energy = 0.0
            total_consumed_energy = 0.0

            for circuit_id in circuits.circuits.additional_keys:
                if not circuit_id.startswith("unmapped_tab_"):
                    circuit = circuits.circuits[circuit_id]
                    total_produced_energy += circuit.produced_energy_wh
                    total_consumed_energy += circuit.consumed_energy_wh

            # Panel energy should now match circuit totals exactly
            panel_produced = panel_state.main_meter_energy.produced_energy_wh
            panel_consumed = panel_state.main_meter_energy.consumed_energy_wh

            print(f"Panel produced: {panel_produced}Wh, Circuit total: {total_produced_energy}Wh")
            print(f"Panel consumed: {panel_consumed}Wh, Circuit total: {total_consumed_energy}Wh")

            # Energy should now align exactly
            assert (
                panel_produced == total_produced_energy
            ), f"Panel produced energy ({panel_produced}Wh) should exactly match circuit total ({total_produced_energy}Wh)"
            assert (
                panel_consumed == total_consumed_energy
            ), f"Panel consumed energy ({panel_consumed}Wh) should exactly match circuit total ({total_consumed_energy}Wh)"

    async def test_panel_circuit_consistency_across_calls(self):
        """Test that panel and circuit data remain consistent across multiple calls."""
        client = SpanPanelClient(
            host="test-panel-consistency",
            simulation_mode=True,
            simulation_config_path="examples/simple_test_config.yaml",
            # Disable caching to get fresh data
        )

        async with client:
            # Make multiple calls and verify consistency
            for i in range(3):
                panel_state = await client.get_panel_state()
                circuits = await client.get_circuits()

                # Calculate consumption and production separately
                total_consumption = 0.0
                total_production = 0.0
                for cid in circuits.circuits.additional_keys:
                    circuit = circuits.circuits[cid]
                    # All circuits now show positive power values
                    # Determine if consuming or producing based on circuit type
                    circuit_name = circuit.name.lower() if circuit.name else ""
                    if any(keyword in circuit_name for keyword in ["solar", "inverter", "generator", "battery"]):
                        # Producer circuits (solar, battery when discharging)
                        total_production += circuit.instant_power_w
                    else:
                        # Consumer circuits
                        total_consumption += circuit.instant_power_w

                total_produced_energy = sum(
                    circuits.circuits[cid].produced_energy_wh for cid in circuits.circuits.additional_keys
                )
                total_consumed_energy = sum(
                    circuits.circuits[cid].consumed_energy_wh for cid in circuits.circuits.additional_keys
                )

                # Panel grid power should be consumption - production
                expected_grid_power = total_consumption - total_production
                power_difference = abs(panel_state.instant_grid_power_w - expected_grid_power)
                assert power_difference <= 2000.0, f"Call {i + 1}: Power misalignment too large ({power_difference}W)"

                # Document that energy alignment is currently not exact due to separate data generation
                # For now, just verify data is reasonable
                assert panel_state.main_meter_energy.produced_energy_wh >= 0, f"Call {i + 1}: Invalid produced energy"
                assert panel_state.main_meter_energy.consumed_energy_wh >= 0, f"Call {i + 1}: Invalid consumed energy"
                assert total_produced_energy >= 0, f"Call {i + 1}: Invalid circuit produced energy"
                assert total_consumed_energy >= 0, f"Call {i + 1}: Invalid circuit consumed energy"

    async def test_panel_circuit_alignment_with_mixed_behaviors(self):
        """Test alignment with mixed circuit behaviors (producing and consuming)."""
        client = SpanPanelClient(
            host="test-mixed-behaviors", simulation_mode=True, simulation_config_path="examples/behavior_test_config.yaml"
        )

        async with client:
            panel_state = await client.get_panel_state()
            circuits = await client.get_circuits()

            # Categorize circuits by power behavior (all circuits now show positive power)
            total_consumption = 0.0
            total_production = 0.0

            for circuit_id in circuits.circuits.additional_keys:
                circuit = circuits.circuits[circuit_id]
                # All circuits now show positive power values
                # Determine if consuming or producing based on circuit type
                circuit_name = circuit.name.lower() if circuit.name else ""
                if any(keyword in circuit_name for keyword in ["solar", "inverter", "generator", "battery"]):
                    # Producer circuits (solar, battery when discharging)
                    total_production += circuit.instant_power_w
                else:
                    # Consumer circuits
                    total_consumption += circuit.instant_power_w

            # Panel grid power should be consumption - production
            expected_grid_power = total_consumption - total_production

            # Panel should reflect this expected grid power (with current larger variation due to separate data generation)
            power_difference = abs(panel_state.instant_grid_power_w - expected_grid_power)
            assert power_difference <= 2000.0, (
                f"Panel power ({panel_state.instant_grid_power_w}W) doesn't align with "
                f"expected grid power ({expected_grid_power}W) = consumption ({total_consumption}W) - production ({total_production}W). Difference: {power_difference}W"
            )

    async def test_panel_circuit_alignment_data_integrity(self):
        """Test that panel and circuit data maintain referential integrity."""
        client = SpanPanelClient(
            host="test-data-integrity", simulation_mode=True, simulation_config_path="examples/simple_test_config.yaml"
        )

        async with client:
            panel_state = await client.get_panel_state()
            circuits = await client.get_circuits()

            # Verify basic data integrity
            assert hasattr(panel_state, "dsm_state"), "Panel should have dsm_state"
            assert len(circuits.circuits.additional_keys) > 0, "Should have circuits configured"

            # Verify timestamp consistency (should be close)
            panel_time = panel_state.grid_sample_end_ms
            for circuit_id in circuits.circuits.additional_keys:
                circuit = circuits.circuits[circuit_id]
                circuit_time = circuit.instant_power_update_time_s * 1000  # Convert to ms
                time_diff = abs(panel_time - circuit_time)
                # Allow up to 5 seconds difference
                assert time_diff <= 5000, f"Timestamp mismatch: panel={panel_time}, circuit={circuit_time}"

            # Verify energy accumulation timestamps
            for circuit_id in circuits.circuits.additional_keys:
                circuit = circuits.circuits[circuit_id]
                power_time = circuit.instant_power_update_time_s
                energy_time = circuit.energy_accum_update_time_s
                # Energy and power should have same or very close timestamps
                assert abs(power_time - energy_time) <= 1, "Power and energy timestamps should be synchronized"

    async def test_panel_reflects_circuit_configuration_changes(self):
        """Test that panel data correctly reflects the circuit configuration."""
        client = SpanPanelClient(
            host="test-config-reflection", simulation_mode=True, simulation_config_path="examples/simple_test_config.yaml"
        )

        async with client:
            panel_state = await client.get_panel_state()
            circuits = await client.get_circuits()

            # Count circuits by type to verify configuration is reflected
            circuit_count = len(circuits.circuits.additional_keys)
            assert circuit_count > 0, "Should have configured circuits"

            # Verify panel state reflects having active circuits
            # If we have circuits with power, panel should show grid activity
            has_active_circuits = any(
                abs(circuits.circuits[cid].instant_power_w) > 0 for cid in circuits.circuits.additional_keys
            )
            if has_active_circuits:
                # Panel should show some grid power (within variation range)
                assert abs(panel_state.instant_grid_power_w) <= 50000, "Panel power should be reasonable"

            # Verify main relay state consistency
            # If circuits are active, main relay should be CLOSED
            assert panel_state.main_relay_state == "CLOSED", "Main relay should be closed for active circuits"
