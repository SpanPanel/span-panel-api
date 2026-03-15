"""Targeted tests for uncovered auth and homie code paths."""

from __future__ import annotations

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
