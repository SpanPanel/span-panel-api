"""Targeted tests for uncovered code paths."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from span_panel_api.auth import _int, download_ca_cert, get_homie_schema
from span_panel_api.exceptions import SpanPanelConnectionError, SpanPanelTimeoutError
from span_panel_api.mqtt.homie import HomieDeviceConsumer, _parse_int


# ---------------------------------------------------------------------------
# auth._int edge cases (lines 29-31)
# ---------------------------------------------------------------------------


class TestIntHelper:
    def test_int_passthrough(self) -> None:
        assert _int(42) == 42

    def test_float_truncated(self) -> None:
        assert _int(3.9) == 3

    def test_string_parsed(self) -> None:
        assert _int("7") == 7


# ---------------------------------------------------------------------------
# auth — connection / timeout errors for download_ca_cert (lines 111-114)
# ---------------------------------------------------------------------------


def _mock_client(method: str, side_effect: Exception) -> AsyncMock:
    mock = AsyncMock()
    setattr(mock, method, AsyncMock(side_effect=side_effect))
    mock.__aenter__ = AsyncMock(return_value=mock)
    mock.__aexit__ = AsyncMock(return_value=False)
    return mock


class TestDownloadCaCertErrors:
    @pytest.mark.asyncio
    async def test_connection_error(self) -> None:
        with patch("span_panel_api.auth.httpx.AsyncClient") as cls:
            cls.return_value = _mock_client("get", httpx.ConnectError("refused"))
            with pytest.raises(SpanPanelConnectionError):
                await download_ca_cert("192.168.1.1")

    @pytest.mark.asyncio
    async def test_timeout_error(self) -> None:
        with patch("span_panel_api.auth.httpx.AsyncClient") as cls:
            cls.return_value = _mock_client("get", httpx.TimeoutException("slow"))
            with pytest.raises(SpanPanelTimeoutError):
                await download_ca_cert("192.168.1.1")


# ---------------------------------------------------------------------------
# auth — connection / timeout errors for get_homie_schema (lines 148-151, 154)
# ---------------------------------------------------------------------------


class TestGetHomieSchemaErrors:
    @pytest.mark.asyncio
    async def test_connection_error(self) -> None:
        with patch("span_panel_api.auth.httpx.AsyncClient") as cls:
            cls.return_value = _mock_client("get", httpx.ConnectError("refused"))
            with pytest.raises(SpanPanelConnectionError):
                await get_homie_schema("192.168.1.1")

    @pytest.mark.asyncio
    async def test_timeout_error(self) -> None:
        with patch("span_panel_api.auth.httpx.AsyncClient") as cls:
            cls.return_value = _mock_client("get", httpx.TimeoutException("slow"))
            with pytest.raises(SpanPanelTimeoutError):
                await get_homie_schema("192.168.1.1")


# ---------------------------------------------------------------------------
# homie._parse_int failure path (lines 51-52)
# ---------------------------------------------------------------------------


class TestParseInt:
    def test_valid(self) -> None:
        assert _parse_int("42") == 42

    def test_invalid_returns_default(self) -> None:
        assert _parse_int("not_a_number") == 0

    def test_invalid_with_custom_default(self) -> None:
        assert _parse_int("bad", default=-1) == -1


# ---------------------------------------------------------------------------
# homie — callback unregister (lines 104-105)
# ---------------------------------------------------------------------------


class TestHomieCallbackUnregister:
    def test_unregister_removes_callback(self) -> None:
        consumer = HomieDeviceConsumer("test-serial", panel_size=32)
        cb = AsyncMock()
        unregister = consumer.register_property_callback(cb)
        unregister()
        # Second unregister should not raise (debug log path)
        unregister()


# ---------------------------------------------------------------------------
# Simulation helpers
# ---------------------------------------------------------------------------


def _sim_config(**overrides: Any) -> dict[str, Any]:
    """Minimal valid simulation config."""
    cfg: dict[str, Any] = {
        "panel_config": {
            "serial_number": "test-serial",
            "total_tabs": 8,
            "main_size": 100,
        },
        "simulation_params": {"noise_factor": 0.0},
        "circuit_templates": {
            "light": {
                "energy_profile": {"mode": "consumer", "typical_power": 100, "power_variation": 0, "power_range": [50, 150]},
                "relay_behavior": "controllable",
                "priority": "MUST_HAVE",
            },
        },
        "circuits": [
            {"id": "c1", "name": "Light", "template": "light", "tabs": [1]},
        ],
    }
    cfg.update(overrides)
    return cfg


# ---------------------------------------------------------------------------
# simulation — dynamic overrides (lines 1163-1195)
# ---------------------------------------------------------------------------


class TestSimulationDynamicOverrides:
    @pytest.mark.asyncio
    async def test_set_and_clear_overrides(self) -> None:
        from span_panel_api.simulation import DynamicSimulationEngine

        engine = DynamicSimulationEngine(config_data=_sim_config())
        await engine.initialize_async()

        engine.set_dynamic_overrides(
            circuit_overrides={"c1": {"power_override": 999.0}},
            global_overrides={"power_multiplier": 2.0},
        )

        snapshot = await engine.get_snapshot()
        assert "c1" in snapshot.circuits

        engine.clear_dynamic_overrides()

        snapshot2 = await engine.get_snapshot()
        assert "c1" in snapshot2.circuits

    @pytest.mark.asyncio
    async def test_relay_override_opens_circuit(self) -> None:
        from span_panel_api.simulation import DynamicSimulationEngine

        engine = DynamicSimulationEngine(config_data=_sim_config())
        await engine.initialize_async()

        engine.set_dynamic_overrides(
            circuit_overrides={"c1": {"relay_state": "OPEN"}},
        )

        snapshot = await engine.get_snapshot()
        assert snapshot.circuits["c1"].relay_state == "OPEN"
        assert snapshot.circuits["c1"].instant_power_w == 0.0

    @pytest.mark.asyncio
    async def test_priority_override(self) -> None:
        from span_panel_api.simulation import DynamicSimulationEngine

        engine = DynamicSimulationEngine(config_data=_sim_config())
        await engine.initialize_async()

        engine.set_dynamic_overrides(
            circuit_overrides={"c1": {"priority": "NON_ESSENTIAL"}},
        )

        snapshot = await engine.get_snapshot()
        assert snapshot.circuits["c1"].priority == "NON_ESSENTIAL"

    @pytest.mark.asyncio
    async def test_power_multiplier_override(self) -> None:
        from span_panel_api.simulation import DynamicSimulationEngine

        engine = DynamicSimulationEngine(config_data=_sim_config())
        await engine.initialize_async()

        engine.set_dynamic_overrides(
            circuit_overrides={"c1": {"power_multiplier": 0.0}},
        )

        snapshot = await engine.get_snapshot()
        assert snapshot.circuits["c1"].instant_power_w == 0.0


# ---------------------------------------------------------------------------
# simulation — override_simulation_start_time (lines 568-598)
# ---------------------------------------------------------------------------


class TestSimulationStartTimeOverride:
    @pytest.mark.asyncio
    async def test_override_before_init(self) -> None:
        from span_panel_api.simulation import DynamicSimulationEngine

        engine = DynamicSimulationEngine(config_data=_sim_config())
        engine.override_simulation_start_time("2024-06-15T12:00:00")
        await engine.initialize_async()

        snapshot = await engine.get_snapshot()
        assert snapshot.serial_number == "test-serial"

    @pytest.mark.asyncio
    async def test_override_after_init(self) -> None:
        from span_panel_api.simulation import DynamicSimulationEngine

        engine = DynamicSimulationEngine(config_data=_sim_config())
        await engine.initialize_async()
        engine.override_simulation_start_time("2024-06-15T12:00:00")

        snapshot = await engine.get_snapshot()
        assert snapshot.serial_number == "test-serial"

    @pytest.mark.asyncio
    async def test_override_invalid_datetime(self) -> None:
        from span_panel_api.simulation import DynamicSimulationEngine

        engine = DynamicSimulationEngine(config_data=_sim_config())
        await engine.initialize_async()
        engine.override_simulation_start_time("not-a-datetime")

        snapshot = await engine.get_snapshot()
        assert snapshot.serial_number == "test-serial"

    @pytest.mark.asyncio
    async def test_override_with_z_suffix(self) -> None:
        from span_panel_api.simulation import DynamicSimulationEngine

        engine = DynamicSimulationEngine(config_data=_sim_config())
        await engine.initialize_async()
        engine.override_simulation_start_time("2024-06-15T12:00:00Z")

        snapshot = await engine.get_snapshot()
        assert snapshot.serial_number == "test-serial"
