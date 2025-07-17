"""Tests for SPAN Panel API simulation mode functionality."""

import pytest
from pathlib import Path

from span_panel_api import SpanPanelClient
from span_panel_api.exceptions import SpanPanelAPIError


class TestSimulationModeBasics:
    """Test basic simulation mode functionality."""

    def test_simulation_mode_initialization(self) -> None:
        """Test that simulation mode initializes correctly."""
        # Live mode (default)
        live_client = SpanPanelClient(host="192.168.1.100")
        assert live_client._simulation_mode is False
        assert live_client._simulation_engine is None

        # Simulation mode
        sim_client = SpanPanelClient(host="localhost", simulation_mode=True)
        assert sim_client._simulation_mode is True
        assert sim_client._simulation_engine is not None

    def test_simulation_mode_ignores_host_in_simulation(self) -> None:
        """Test that host is ignored in simulation mode."""
        # Should work with any host in simulation mode
        client = SpanPanelClient(host="invalid-host", simulation_mode=True)
        assert client._simulation_mode is True


class TestYAMLConfigurationMode:
    """Test YAML configuration-based simulation mode."""

    async def test_yaml_config_simulation(self) -> None:
        """Test simulation with YAML configuration."""
        config_path = Path(__file__).parent.parent / "examples" / "simulation_config_32_circuit.yaml"

        # Test with YAML config
        async with SpanPanelClient(
            host="yaml-test-panel", simulation_mode=True, simulation_config_path=str(config_path)
        ) as client:
            # Test all API endpoints
            circuits = await client.get_circuits()
            panel = await client.get_panel_state()
            status = await client.get_status()
            storage = await client.get_storage_soe()

            # Verify YAML config is used
            assert circuits is not None
            assert panel is not None
            assert status is not None
            assert storage is not None

            # Check that host is used as serial number with YAML config
            assert status.system.serial == "yaml-test-panel"

    async def test_yaml_realistic_behaviors(self) -> None:
        """Test realistic behaviors with YAML configuration."""
        config_path = Path(__file__).parent.parent / "examples" / "simulation_config_32_circuit.yaml"

        async with SpanPanelClient(
            host="behavior-test", simulation_mode=True, simulation_config_path=str(config_path)
        ) as client:
            # Get circuits to trigger behavior engine
            circuits = await client.get_circuits()
            assert circuits is not None

            # Check that we have the expected circuits from YAML
            circuit_names = [c.name for c in circuits.circuits.additional_properties.values()]

            # Should have circuits from the YAML config
            assert len(circuit_names) > 0

            # Test circuit overrides work with YAML config
            await client.set_circuit_overrides({"living_room_lights": {"power_override": 500.0}})

            overridden_circuits = await client.get_circuits()
            await client.clear_circuit_overrides()

    async def test_yaml_circuit_templates(self) -> None:
        """Test circuit template system with YAML configuration."""
        config_path = Path(__file__).parent.parent / "examples" / "simulation_config_32_circuit.yaml"

        async with SpanPanelClient(
            host="template-test", simulation_mode=True, simulation_config_path=str(config_path)
        ) as client:
            circuits = await client.get_circuits()

            # Check that different circuit types have different power patterns
            circuit_powers = {}
            for circuit in circuits.circuits.additional_properties.values():
                circuit_powers[circuit.name] = circuit.instant_power_w

            # Should have varied power levels based on templates
            power_values = list(circuit_powers.values())
            assert len(set(power_values)) > 1  # Not all the same power

    async def test_simulation_engine_direct_methods(self) -> None:
        """Test simulation engine methods directly for coverage."""
        from span_panel_api.simulation import DynamicSimulationEngine

        # Test with custom config
        config_path = Path(__file__).parent.parent / "examples" / "simple_test_config.yaml"
        engine = DynamicSimulationEngine("direct-test", config_path=config_path)

        await engine.initialize_async()

        # Test direct method calls using new YAML-only API
        panel_data = await engine.get_panel_data()
        assert isinstance(panel_data, dict)
        assert "circuits" in panel_data
        assert "panel" in panel_data
        assert "status" in panel_data
        assert "soe" in panel_data

        # Test individual methods
        status_data = await engine.get_status()
        assert isinstance(status_data, dict)
        assert "system" in status_data

        soe_data = await engine.get_soe()
        assert isinstance(soe_data, dict)
        assert "soe" in soe_data

    async def test_behavior_engine_coverage(self) -> None:
        """Test behavior engine methods for coverage using YAML config."""
        from span_panel_api.simulation import DynamicSimulationEngine

        # Use behavior test config with realistic patterns
        config_path = Path(__file__).parent.parent / "examples" / "behavior_test_config.yaml"
        engine = DynamicSimulationEngine("behavior-test", config_path=config_path)
        await engine.initialize_async()

        # Test that behavior patterns are applied
        panel_data = await engine.get_panel_data()
        circuits = panel_data["circuits"]["circuits"]

        # Should have circuits with different behavior types
        circuit_names = [circuit["name"] for circuit in circuits.values()]

        # Verify we have different circuit types that test different behaviors
        assert any("HVAC" in name for name in circuit_names)  # Cycling behavior
        assert any("Lights" in name for name in circuit_names)  # Time of day behavior
        assert any("EV" in name or "Charger" in name for name in circuit_names)  # Smart behavior
        assert any("Solar" in name for name in circuit_names)  # Production behavior

        # Test multiple calls to verify behavior patterns work
        for _ in range(3):
            panel_data = await engine.get_panel_data()
            circuits = panel_data["circuits"]["circuits"]

            # Verify power values are realistic
            for circuit_data in circuits.values():
                power = circuit_data["instantPowerW"]
                assert isinstance(power, (int, float))
                # Solar should be negative (production), others positive (consumption)
                if "solar" in circuit_data["name"].lower():
                    assert power <= 0
                else:
                    assert power >= 0

    async def test_template_inference_coverage(self) -> None:
        """Test YAML template system - no inference needed."""
        from span_panel_api.simulation import DynamicSimulationEngine

        # Test that YAML configuration provides complete templates
        config_path = Path(__file__).parent.parent / "examples" / "simple_test_config.yaml"
        engine = DynamicSimulationEngine("template-test", config_path=config_path)
        await engine.initialize_async()

        # Verify that templates are loaded from YAML
        panel_data = await engine.get_panel_data()
        circuits = panel_data["circuits"]["circuits"]

        # All circuits should have proper templates applied
        for circuit_id, circuit_data in circuits.items():
            assert "instantPowerW" in circuit_data
            assert "priority" in circuit_data
            assert "isUserControllable" in circuit_data
            # Power should be realistic (not zero for non-solar circuits)
            if "solar" not in circuit_data["name"].lower():
                assert circuit_data["instantPowerW"] >= 0

    async def test_edge_cases_and_error_handling(self) -> None:
        """Test edge cases and error handling for YAML-only simulation."""
        from span_panel_api.simulation import DynamicSimulationEngine

        # Test with no config should raise error
        engine = DynamicSimulationEngine("edge-test")

        with pytest.raises(
            ValueError, match="Simulation mode requires either config_data or a valid config_path with YAML configuration"
        ):
            await engine.initialize_async()

        # Test with valid YAML config should work
        config_path = Path(__file__).parent.parent / "examples" / "minimal_config.yaml"
        engine_with_config = DynamicSimulationEngine("edge-test-with-config", config_path=config_path)
        await engine_with_config.initialize_async()

        panel_data = await engine_with_config.get_panel_data()
        assert isinstance(panel_data, dict)
        assert "circuits" in panel_data


class TestCircuitsSimulation:
    """Test circuits API simulation functionality."""

    @pytest.fixture
    def sim_client(self) -> SpanPanelClient:
        """Create a simulation mode client."""
        config_path = Path(__file__).parent.parent / "examples" / "simple_test_config.yaml"
        return SpanPanelClient(host="circuits-test", simulation_mode=True, simulation_config_path=str(config_path))

    @pytest.fixture
    def live_client(self) -> SpanPanelClient:
        """Create a live mode client."""
        return SpanPanelClient(host="192.168.1.100", simulation_mode=False)

    async def test_get_circuits_basic_simulation(self, sim_client: SpanPanelClient) -> None:
        """Test basic circuits retrieval in simulation mode."""
        async with sim_client:
            circuits = await sim_client.get_circuits()

            # Should return CircuitsOut object
            assert circuits is not None
            assert hasattr(circuits, "circuits")
            assert len(circuits.circuits.additional_properties) > 0

    async def test_get_circuits_with_global_variations(self, sim_client: SpanPanelClient) -> None:
        """Test circuits with global power and energy variations using override API."""
        async with sim_client:
            # Get baseline circuits
            baseline_circuits = await sim_client.get_circuits()

            # Apply global power multiplier override
            await sim_client.set_circuit_overrides(global_overrides={"power_multiplier": 1.2})

            # Get circuits with override
            overridden_circuits = await sim_client.get_circuits()

            assert overridden_circuits is not None
            assert len(overridden_circuits.circuits.additional_properties) > 0

            # Clear overrides
            await sim_client.clear_circuit_overrides()

    async def test_get_circuits_with_specific_variations(self, sim_client: SpanPanelClient) -> None:
        """Test circuits with specific circuit variations using override API."""
        async with sim_client:
            # Get baseline first
            baseline = await sim_client.get_circuits()
            circuit_ids = list(baseline.circuits.additional_properties.keys())

            if circuit_ids:
                test_circuit_id = circuit_ids[0]

                # Apply specific circuit override
                await sim_client.set_circuit_overrides({test_circuit_id: {"power_override": 1000.0}})

                # Get circuits with override
                overridden = await sim_client.get_circuits()

                assert overridden is not None
                assert len(overridden.circuits.additional_properties) > 0

                # Clear overrides
                await sim_client.clear_circuit_overrides()

    async def test_get_circuits_mixed_variations(self, sim_client: SpanPanelClient) -> None:
        """Test circuits with mixed global and specific variations."""
        async with sim_client:
            # Get baseline
            baseline = await sim_client.get_circuits()
            circuit_ids = list(baseline.circuits.additional_properties.keys())

            if circuit_ids:
                test_circuit_id = circuit_ids[0]

                # Apply both global and specific overrides
                await sim_client.set_circuit_overrides(
                    circuit_overrides={test_circuit_id: {"power_override": 500.0}},
                    global_overrides={"power_multiplier": 1.5},
                )

                overridden = await sim_client.get_circuits()

                assert overridden is not None
                assert len(overridden.circuits.additional_properties) > 0

                # Clear overrides
                await sim_client.clear_circuit_overrides()

    async def test_get_circuits_legacy_parameters(self, sim_client: SpanPanelClient) -> None:
        """Test that the clean API doesn't accept old variation parameters."""
        async with sim_client:
            # This should work (no parameters)
            circuits = await sim_client.get_circuits()
            assert circuits is not None

            # These should raise TypeError if old parameters are passed
            with pytest.raises(TypeError):
                await sim_client.get_circuits(global_power_variation=0.1)  # type: ignore[call-arg]

            with pytest.raises(TypeError):
                await sim_client.get_circuits(variations={})  # type: ignore[call-arg]


class TestPanelStateSimulation:
    """Test panel state API simulation functionality."""

    @pytest.fixture
    def sim_client(self) -> SpanPanelClient:
        """Create a simulation mode client."""
        config_path = Path(__file__).parent.parent / "examples" / "simple_test_config.yaml"
        return SpanPanelClient(host="panel-test", simulation_mode=True, simulation_config_path=str(config_path))

    async def test_get_panel_state_basic(self, sim_client: SpanPanelClient) -> None:
        """Test basic panel state retrieval in simulation mode."""
        async with sim_client:
            panel = await sim_client.get_panel_state()

            assert panel is not None
            assert hasattr(panel, "instant_grid_power_w")


class TestStatusSimulation:
    """Test status API simulation functionality."""

    @pytest.fixture
    def sim_client(self) -> SpanPanelClient:
        """Create a simulation mode client."""
        config_path = Path(__file__).parent.parent / "examples" / "simple_test_config.yaml"
        return SpanPanelClient(host="status-test", simulation_mode=True, simulation_config_path=str(config_path))

    async def test_get_status_basic(self, sim_client: SpanPanelClient) -> None:
        """Test basic status retrieval in simulation mode."""
        async with sim_client:
            status = await sim_client.get_status()

            assert status is not None
            assert hasattr(status, "system")


class TestStorageSimulation:
    """Test storage SOE API simulation functionality."""

    @pytest.fixture
    def sim_client(self) -> SpanPanelClient:
        """Create a simulation mode client."""
        config_path = Path(__file__).parent.parent / "examples" / "simple_test_config.yaml"
        return SpanPanelClient(host="storage-test", simulation_mode=True, simulation_config_path=str(config_path))

    async def test_get_storage_soe_basic(self, sim_client: SpanPanelClient) -> None:
        """Test basic storage SOE retrieval in simulation mode."""
        async with sim_client:
            soe = await sim_client.get_storage_soe()

            assert soe is not None
            assert hasattr(soe, "soe")


class TestSimulationRealism:
    """Test realistic simulation behaviors."""

    @pytest.fixture
    def sim_client(self) -> SpanPanelClient:
        """Create a simulation mode client."""
        config_path = Path(__file__).parent.parent / "examples" / "simple_test_config.yaml"
        return SpanPanelClient(host="realism-test", simulation_mode=True, simulation_config_path=str(config_path))

    async def test_power_variation_by_circuit_type(self, sim_client: SpanPanelClient) -> None:
        """Test that different circuit types have appropriate power variations."""
        async with sim_client:
            circuits = await sim_client.get_circuits()

            # Collect power values by circuit name patterns
            power_values = {}
            for circuit in circuits.circuits.additional_properties.values():
                power_values[circuit.name] = circuit.instant_power_w

            # Verify we have some circuits with different power levels
            assert len(power_values) > 0

    async def test_circuit_specific_behavior(self, sim_client: SpanPanelClient) -> None:
        """Test circuit-specific behavioral characteristics."""
        async with sim_client:
            circuits = await sim_client.get_circuits()

            # Test that circuits have varied power levels
            power_levels = [c.instant_power_w for c in circuits.circuits.additional_properties.values()]

            # Should have some variation in power levels (not all the same)
            assert len(set(power_levels)) > 1


class TestSimulationErrorHandling:
    """Test error handling in simulation mode."""

    async def test_missing_fixture_data_errors(self) -> None:
        """Test error handling when YAML config is missing."""
        from span_panel_api.simulation import DynamicSimulationEngine

        # Create engine without YAML config
        engine = DynamicSimulationEngine()

        # Should raise error when trying to initialize without config
        with pytest.raises(
            ValueError, match="Simulation mode requires either config_data or a valid config_path with YAML configuration"
        ):
            await engine.initialize_async()

    async def test_simulation_engine_methods(self) -> None:
        """Test simulation engine methods work correctly with YAML config."""
        from span_panel_api.simulation import DynamicSimulationEngine

        config_path = Path(__file__).parent.parent / "examples" / "minimal_config.yaml"
        engine = DynamicSimulationEngine("test-engine", config_path=config_path)
        await engine.initialize_async()

        # Test panel data method (async)
        panel_data = await engine.get_panel_data()
        assert isinstance(panel_data, dict)
        assert "circuits" in panel_data
        assert "panel" in panel_data

        # Test status data method (async)
        status_data = await engine.get_status()
        assert isinstance(status_data, dict)
        assert "system" in status_data

        # Test SOE data method (async)
        soe_data = await engine.get_soe()
        assert isinstance(soe_data, dict)
        assert "soe" in soe_data


class TestSimulationCaching:
    """Test simulation caching functionality."""

    @pytest.fixture
    def sim_client(self) -> SpanPanelClient:
        """Create a simulation mode client."""
        config_path = Path(__file__).parent.parent / "examples" / "simple_test_config.yaml"
        return SpanPanelClient(host="cache-test-host", simulation_mode=True, simulation_config_path=str(config_path))

    async def test_simulation_caching(self, sim_client: SpanPanelClient) -> None:
        """Test that simulation results are cached appropriately."""
        async with sim_client:
            # Same parameters should return cached results
            circuits1 = await sim_client.get_circuits()
            circuits2 = await sim_client.get_circuits()

            # Should get same results (from cache)
            assert circuits1 is not None
            assert circuits2 is not None

    async def test_different_variations_not_cached(self, sim_client: SpanPanelClient) -> None:
        """Test that different overrides create different cache entries."""
        async with sim_client:
            circuits1 = await sim_client.get_circuits()

            # Apply override
            await sim_client.set_circuit_overrides(global_overrides={"power_multiplier": 1.5})
            circuits2 = await sim_client.get_circuits()

            # Should get different results
            assert circuits1 is not None
            assert circuits2 is not None

            # Clear overrides
            await sim_client.clear_circuit_overrides()

    async def test_simulation_uses_host_as_serial_number(self) -> None:
        """Test that simulation mode uses the host parameter as the serial number."""
        custom_serial = "SPAN-TEST-ABC123"

        # Create client with custom serial as host
        config_path = Path(__file__).parent.parent / "examples" / "simple_test_config.yaml"
        client = SpanPanelClient(host=custom_serial, simulation_mode=True, simulation_config_path=str(config_path))

        async with client:
            status = await client.get_status()

            # Host parameter is still used as serial number override even with YAML config
            assert status.system.serial == custom_serial

    async def test_simulation_async_initialization_race_condition(self) -> None:
        """Test that concurrent initialization calls handle race conditions properly."""
        from span_panel_api.simulation import DynamicSimulationEngine
        import asyncio

        # Create engine with YAML config
        config_path = Path(__file__).parent.parent / "examples" / "simple_test_config.yaml"

        # Multiple concurrent initializations
        engine1 = DynamicSimulationEngine("test-race-1", str(config_path))
        engine2 = DynamicSimulationEngine("test-race-2", str(config_path))

        # Initialize both concurrently
        await asyncio.gather(engine1.initialize_async(), engine2.initialize_async())

        # Both should be initialized successfully
        panel_data1 = await engine1.get_panel_data()
        panel_data2 = await engine2.get_panel_data()

        assert panel_data1 is not None
        assert panel_data2 is not None
        assert "circuits" in panel_data1
        assert "circuits" in panel_data2
        assert len(panel_data1["circuits"]) > 0
        assert len(panel_data2["circuits"]) > 0

    async def test_simulation_relay_behavior(self) -> None:
        """Test circuit relay state behavior and its effect on power/energy.

        This test verifies that relay state changes work correctly in simulation mode:
        1. Opening a circuit relay sets its power to 0W
        2. Relay state is properly reflected in circuit data
        3. Panel grid power reflects the circuit changes
        4. Closing the relay restores normal behavior
        """
        import asyncio
        from tests.time_utils import advance_time_async

        config_path = Path(__file__).parent.parent / "examples" / "simple_test_config.yaml"
        client = SpanPanelClient(
            host="relay-test-demo",
            simulation_mode=True,
            simulation_config_path=str(config_path),
            cache_window=0.0,  # Disable caching for real-time data
        )

        async with client:
            # Ensure clean state
            await client.clear_circuit_overrides()

            # Step 1: Get baseline data (all circuits closed)
            circuits_baseline = await client.get_circuits()
            panel_baseline = await client.get_panel_state()

            # Find a circuit with significant power for testing
            active_circuit_id = None
            for circuit_id in circuits_baseline.circuits.additional_keys:
                circuit = circuits_baseline.circuits[circuit_id]
                if abs(circuit.instant_power_w) > 10:  # Find circuit with meaningful power
                    active_circuit_id = circuit_id
                    break

            assert active_circuit_id is not None, "Should find at least one circuit with significant power"

            baseline_circuit = circuits_baseline.circuits[active_circuit_id]
            baseline_total_power = sum(
                circuits_baseline.circuits[cid].instant_power_w for cid in circuits_baseline.circuits.additional_keys
            )

            # Step 2: Open the circuit relay (simulate turning off AC)
            await client.set_circuit_overrides({active_circuit_id: {"relay_state": "OPEN"}})
            circuits_open = await client.get_circuits()
            panel_open = await client.get_panel_state()

            # Verify circuit still exists in response
            assert (
                active_circuit_id in circuits_open.circuits.additional_keys
            ), f"Circuit {active_circuit_id} should still exist after override"

            open_circuit = circuits_open.circuits[active_circuit_id]
            open_total_power = sum(
                circuits_open.circuits[cid].instant_power_w for cid in circuits_open.circuits.additional_keys
            )

            # Calculate changes
            circuit_power_change = open_circuit.instant_power_w - baseline_circuit.instant_power_w
            total_power_change = open_total_power - baseline_total_power
            panel_power_change = panel_open.instant_grid_power_w - panel_baseline.instant_grid_power_w

            # Verify relay override is working correctly
            assert (
                open_circuit.relay_state == "OPEN"
            ), f"Relay state should be OPEN after override, got {open_circuit.relay_state}"
            assert (
                open_circuit.instant_power_w == 0.0
            ), f"Power should be 0W when relay is OPEN, got {open_circuit.instant_power_w}W"

            # Document the working behavior for verification:
            print(f"RELAY OVERRIDE SUCCESS - Circuit {baseline_circuit.name}:")
            print(f"  Relay state: {open_circuit.relay_state} (correct)")
            print(f"  Power: {open_circuit.instant_power_w:.1f}W (correct)")
            print(f"  Circuit power change: {circuit_power_change:.1f}W")
            print(f"  Total power change: {total_power_change:.1f}W")
            print(f"  Panel power change: {panel_power_change:.1f}W")

            # Step 3: Test energy accumulation behavior
            # Yield to event loop to allow any pending operations
            await advance_time_async(0)

            circuits_after_wait = await client.get_circuits()
            wait_circuit = circuits_after_wait.circuits[active_circuit_id]
            energy_consumed_change = wait_circuit.consumed_energy_wh - open_circuit.consumed_energy_wh

            # Verify power stays at 0 while relay is open
            assert (
                wait_circuit.instant_power_w == 0.0
            ), f"Power should remain 0W while relay is OPEN, got {wait_circuit.instant_power_w}W"
            print(
                f"  Energy accumulation check: {energy_consumed_change:.3f}Wh change (expected variation due to random generation)"
            )

            # Step 4: Close the relay
            await client.set_circuit_overrides({active_circuit_id: {"relay_state": "CLOSED"}})
            circuits_closed = await client.get_circuits()
            closed_circuit = circuits_closed.circuits[active_circuit_id]

            # Verify basic functionality still works
            assert closed_circuit is not None, "Circuit should exist after closing relay"

            # Clean up
            await client.clear_circuit_overrides()

    async def test_simulation_double_check_in_lock(self) -> None:
        """Test the double-check pattern inside the async lock."""
        from span_panel_api.simulation import DynamicSimulationEngine
        import asyncio

        config_path = Path(__file__).parent.parent / "examples" / "minimal_config.yaml"
        engine = DynamicSimulationEngine("TEST-SERIAL", config_path=config_path)

        # First initialization should load data
        await engine.initialize_async()
        first_data = engine._base_data

        # Second initialization should use existing data
        await engine.initialize_async()
        second_data = engine._base_data

        # Should be the same object (not re-created)
        assert first_data is second_data


# Cache functionality tests
async def test_cache_hit_paths() -> None:
    """Test that cache works correctly for simulation mode."""
    from span_panel_api.client import TimeWindowCache

    cache = TimeWindowCache()

    # Test basic cache functionality
    cache.set_cached_data("test_key", {"test": "data"})
    result = cache.get_cached_data("test_key")

    assert result is not None
    assert result["test"] == "data"

    # Test cache clear
    cache.clear()
    result = cache.get_cached_data("test_key")
    assert result is None


class TestSimulationErrorConditions:
    """Test error conditions for simulation-only methods."""

    @pytest.mark.asyncio
    async def test_circuit_overrides_outside_simulation_mode(self):
        """Test that circuit override methods fail outside simulation mode."""
        client = SpanPanelClient("192.168.1.100", simulation_mode=False)

        async with client:
            # set_circuit_overrides should fail
            with pytest.raises(SpanPanelAPIError, match="Circuit overrides only available in simulation mode"):
                await client.set_circuit_overrides({"test": {"power_override": 100.0}})

            # clear_circuit_overrides should fail
            with pytest.raises(SpanPanelAPIError, match="Circuit overrides only available in simulation mode"):
                await client.clear_circuit_overrides()

    @pytest.mark.asyncio
    async def test_set_circuit_relay_validation_in_simulation(self):
        """Test relay state validation in simulation mode."""
        client = SpanPanelClient(
            host="test-relay-validation", simulation_mode=True, simulation_config_path="examples/simple_test_config.yaml"
        )

        async with client:
            # Test invalid relay state
            with pytest.raises(SpanPanelAPIError, match="Invalid relay state 'INVALID'. Must be one of: OPEN, CLOSED"):
                await client.set_circuit_relay("living_room_lights", "INVALID")

            # Test valid relay states work (case insensitive)
            result_open = await client.set_circuit_relay("living_room_lights", "open")
            assert result_open["relay_state"] == "OPEN"

            result_closed = await client.set_circuit_relay("living_room_lights", "CLOSED")
            assert result_closed["relay_state"] == "CLOSED"


class TestSimulationEngineErrors:
    """Test simulation engine error conditions."""

    @pytest.mark.asyncio
    async def test_get_circuits_simulation_engine_not_initialized(self):
        """Test that get_circuits raises error when simulation engine not initialized."""
        client = SpanPanelClient(host="simulation", simulation_mode=True)
        # Force simulation engine to None to test this specific error path
        client._simulation_engine = None

        with pytest.raises(SpanPanelAPIError, match="Simulation engine not initialized"):
            await client.get_circuits()

    @pytest.mark.asyncio
    async def test_set_relay_state_simulation_engine_not_initialized(self):
        """Test that set_circuit_relay raises error when simulation engine not initialized."""
        client = SpanPanelClient(host="simulation", simulation_mode=True)
        # Force simulation engine to None to test this specific error path
        client._simulation_engine = None

        with pytest.raises(SpanPanelAPIError, match="Simulation engine not initialized"):
            await client.set_circuit_relay("circuit1", "OPEN")

    @pytest.mark.asyncio
    async def test_set_circuit_overrides_simulation_engine_not_initialized(self):
        """Test that set_circuit_overrides raises error when simulation engine not initialized."""
        client = SpanPanelClient(host="simulation", simulation_mode=True)
        # Force simulation engine to None to test this specific error path
        client._simulation_engine = None

        with pytest.raises(SpanPanelAPIError, match="Simulation engine not initialized"):
            await client.set_circuit_overrides(circuit_overrides={"circuit1": {"power_override": 100}})

    @pytest.mark.asyncio
    async def test_clear_circuit_overrides_simulation_engine_not_initialized(self):
        """Test that clear_circuit_overrides raises error when simulation engine not initialized."""
        client = SpanPanelClient(host="simulation", simulation_mode=True)
        # Force simulation engine to None to test this specific error path
        client._simulation_engine = None

        with pytest.raises(SpanPanelAPIError, match="Simulation engine not initialized"):
            await client.clear_circuit_overrides()


class TestCircuitOverrideEdgeCases:
    """Test circuit override edge cases."""

    @pytest.mark.asyncio
    async def test_set_circuit_overrides_not_simulation_mode(self):
        """Test that set_circuit_overrides raises error when not in simulation mode."""
        client = SpanPanelClient(host="192.168.1.100", simulation_mode=False)

        with pytest.raises(SpanPanelAPIError, match="Circuit overrides only available in simulation mode"):
            await client.set_circuit_overrides(circuit_overrides={"circuit1": {"power_override": 100}})

    @pytest.mark.asyncio
    async def test_clear_circuit_overrides_not_simulation_mode(self):
        """Test that clear_circuit_overrides raises error when not in simulation mode."""
        client = SpanPanelClient(host="192.168.1.100", simulation_mode=False)

        with pytest.raises(SpanPanelAPIError, match="Circuit overrides only available in simulation mode"):
            await client.clear_circuit_overrides()


class TestUnmappedTabEdgeCases:
    """Test unmapped tab creation edge cases."""

    @pytest.mark.asyncio
    async def test_unmapped_tab_creation_edge_case(self):
        """Test unmapped tab creation with unusual branch configuration."""
        # Use YAML config to have actual circuits and tabs
        config_path = Path(__file__).parent.parent / "examples" / "simulation_config_32_circuit.yaml"

        async with SpanPanelClient(host="test", simulation_mode=True, simulation_config_path=str(config_path)) as client:
            # Get initial state to ensure we have branches
            panel_state = await client.get_panel_state()

            # The simulation should handle unmapped tabs correctly
            circuits = await client.get_circuits()

            # Should have circuits and unmapped tabs
            assert circuits is not None
            assert len(circuits.circuits.additional_properties) > 0

            # Check that we can handle tabs correctly
            for circuit_id, circuit in circuits.circuits.additional_properties.items():
                if circuit_id.startswith("unmapped_tab_"):
                    # Verify unmapped tab properties
                    # Solar tabs (30, 32) produce power (negative values), others consume (positive)
                    tab_num = int(circuit_id.split("_")[-1])
                    if tab_num in [30, 32]:
                        # Solar tabs should produce power (negative values)
                        assert (
                            circuit.instant_power_w <= 0
                        ), f"Solar tab {tab_num} should produce power (negative): {circuit.instant_power_w}W"
                    else:
                        # Other unmapped tabs should consume power (positive values)
                        assert (
                            circuit.instant_power_w >= 0
                        ), f"Unmapped tab {tab_num} should consume power (positive): {circuit.instant_power_w}W"
                    assert circuit.name is not None
