"""Targeted tests for uncovered auth and homie code paths."""

from __future__ import annotations

import json
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
        with patch("span_panel_api._http.httpx.AsyncClient") as cls:
            cls.return_value = _mock_client("get", httpx.ConnectError("refused"))
            with pytest.raises(SpanPanelConnectionError):
                await download_ca_cert("192.168.1.1")

    @pytest.mark.asyncio
    async def test_timeout_error(self) -> None:
        with patch("span_panel_api._http.httpx.AsyncClient") as cls:
            cls.return_value = _mock_client("get", httpx.TimeoutException("slow"))
            with pytest.raises(SpanPanelTimeoutError):
                await download_ca_cert("192.168.1.1")


# ---------------------------------------------------------------------------
# auth — connection / timeout errors for get_homie_schema (lines 148-151, 154)
# ---------------------------------------------------------------------------


class TestGetHomieSchemaErrors:
    @pytest.mark.asyncio
    async def test_connection_error(self) -> None:
        with patch("span_panel_api._http.httpx.AsyncClient") as cls:
            cls.return_value = _mock_client("get", httpx.ConnectError("refused"))
            with pytest.raises(SpanPanelConnectionError):
                await get_homie_schema("192.168.1.1")

    @pytest.mark.asyncio
    async def test_timeout_error(self) -> None:
        with patch("span_panel_api._http.httpx.AsyncClient") as cls:
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
# httpx_client injection (auth helpers)
# ---------------------------------------------------------------------------


class TestHttpxClientInjectionAuthHelpers:
    @pytest.mark.asyncio
    async def test_download_ca_cert_injected_client_not_closed(self) -> None:
        pem = "-----BEGIN CERTIFICATE-----\nX\n-----END CERTIFICATE-----"
        mock_response = httpx.Response(
            200,
            content=pem.encode(),
            headers={"content-type": "text/plain"},
            request=httpx.Request("GET", "http://test"),
        )
        injected = AsyncMock(spec=httpx.AsyncClient)
        injected.get = AsyncMock(return_value=mock_response)
        injected.aclose = AsyncMock()

        with patch("span_panel_api._http.httpx.AsyncClient") as mock_cls:
            result = await download_ca_cert("192.168.1.1", httpx_client=injected)

        assert result.startswith("-----BEGIN")
        mock_cls.assert_not_called()
        injected.aclose.assert_not_called()

    @pytest.mark.asyncio
    async def test_download_ca_cert_fallback_uses_timeout_for_client(self) -> None:
        pem = "-----BEGIN CERTIFICATE-----\nX\n-----END CERTIFICATE-----"
        mock_response = httpx.Response(
            200,
            content=pem.encode(),
            headers={"content-type": "text/plain"},
            request=httpx.Request("GET", "http://test"),
        )
        with patch("span_panel_api._http.httpx.AsyncClient") as cls:
            mock_client = AsyncMock()
            mock_client.get.return_value = mock_response
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            cls.return_value = mock_client

            await download_ca_cert("192.168.1.1", timeout=88.5)

        cls.assert_called_once_with(timeout=88.5, verify=False)

    @pytest.mark.asyncio
    async def test_get_homie_schema_injected_skips_constructor(self) -> None:
        schema_json: dict[str, object] = {"firmwareVersion": "fw", "types": {}}
        content = json.dumps(schema_json).encode()
        mock_response = httpx.Response(
            200,
            content=content,
            headers={"content-type": "application/json"},
            request=httpx.Request("GET", "http://test"),
        )
        injected = AsyncMock(spec=httpx.AsyncClient)
        injected.get = AsyncMock(return_value=mock_response)
        injected.aclose = AsyncMock()

        with patch("span_panel_api._http.httpx.AsyncClient") as mock_cls:
            await get_homie_schema("192.168.1.1", timeout=123.0, httpx_client=injected)

        mock_cls.assert_not_called()
        injected.aclose.assert_not_called()
