"""Targeted tests for uncovered auth and homie code paths."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from span_panel_api_schema_0.accumulator import HomiePropertyAccumulator
from span_panel_api_schema_0.consumer import HomieDeviceConsumer, _parse_int
from span_panel_api.auth import _int, download_ca_cert, get_homie_schema
from span_panel_api.exceptions import (
    SpanPanelAPIError,
    SpanPanelConnectionError,
    SpanPanelServerError,
    SpanPanelTimeoutError,
)

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


def _mock_response(method: str, status_code: int) -> AsyncMock:
    """A client whose request completes and returns `status_code`."""
    response = MagicMock()
    response.status_code = status_code
    mock = AsyncMock()
    setattr(mock, method, AsyncMock(return_value=response))
    mock.__aenter__ = AsyncMock(return_value=mock)
    mock.__aexit__ = AsyncMock(return_value=False)
    return mock


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

    @pytest.mark.asyncio
    @pytest.mark.parametrize("status", [500, 502, 503, 504])
    async def test_a_server_status_is_not_ready_rather_than_wrong(self, status: int) -> None:
        """A rebooting panel answers from its front end while the app behind it starts.

        Raised as `SpanPanelServerError` so a caller can tell "not yet" from
        "no". The redispatch retry depends on this distinction: a live firmware
        upgrade produced 502 here, the retry loop did not catch the general
        `SpanPanelAPIError` it used to be, and the parser was never swapped.
        """
        with patch("span_panel_api._http.httpx.AsyncClient") as cls:
            cls.return_value = _mock_response("get", status)
            with pytest.raises(SpanPanelServerError) as caught:
                await get_homie_schema("192.168.1.1")
        assert caught.value.status_code == status

    @pytest.mark.asyncio
    @pytest.mark.parametrize("status", [401, 404])
    async def test_a_client_status_is_not_retryable(self, status: int) -> None:
        """These do not fix themselves, so they must not look like "not ready yet"."""
        with patch("span_panel_api._http.httpx.AsyncClient") as cls:
            cls.return_value = _mock_response("get", status)
            with pytest.raises(SpanPanelAPIError) as caught:
                await get_homie_schema("192.168.1.1")
        assert not isinstance(caught.value, SpanPanelServerError)


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
        acc = HomiePropertyAccumulator("test-serial")
        consumer = HomieDeviceConsumer(acc, panel_size=32)
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
        with (
            patch("span_panel_api._http.httpx.AsyncClient") as cls,
            patch("span_panel_api._http._create_ssl_context", new_callable=AsyncMock) as mock_ctx,
        ):
            mock_client = AsyncMock()
            mock_client.get.return_value = mock_response
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            cls.return_value = mock_client

            await download_ca_cert("192.168.1.1", timeout=88.5)

        cls.assert_called_once_with(timeout=88.5, verify=mock_ctx.return_value)

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


class TestGetHomieSchemaNotReadyShapes:
    """Every way a booting panel answers that is not a clean 5xx.

    Each of these used to escape `get_homie_schema` untranslated, skip the
    caller's retry clause entirely, and strand the parser — the same failure the
    502 produced on a live upgrade, wearing a different exception.
    """

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "failure",
        [
            httpx.ReadError("connection reset"),
            httpx.WriteError("broken pipe"),
            httpx.RemoteProtocolError("server closed connection without sending a response"),
        ],
        ids=["read-reset", "write-reset", "proxy-closed-without-answering"],
    )
    async def test_a_transport_failure_is_a_connection_error(self, failure: Exception) -> None:
        """A panel resetting its listener mid-request, and a proxy dying mid-request.

        `httpx.TimeoutException` is itself a `TransportError`, so the timeout
        branch has to stay ahead of this one — covered by the timeout test above.
        """
        with patch("span_panel_api._http.httpx.AsyncClient") as cls:
            cls.return_value = _mock_client("get", failure)
            with pytest.raises(SpanPanelConnectionError):
                await get_homie_schema("192.168.1.1")

    @pytest.mark.asyncio
    @pytest.mark.parametrize("body", ["", "{trunc", "null", "[]"], ids=["empty", "truncated", "null", "list"])
    async def test_a_200_that_cannot_be_a_schema_is_not_ready_rather_than_broken(self, body: str) -> None:
        """A panel part-way through starting can answer 200 with nothing usable.

        Retryable for the same reason a 502 is: it is "not ready yet" wearing a
        success status. The bounded attempt count makes retrying a genuinely
        broken body cheap.
        """
        response = MagicMock()
        response.status_code = 200
        response.json = MagicMock(side_effect=(lambda: json.loads(body)) if body else ValueError("no content"))
        mock = AsyncMock()
        mock.get = AsyncMock(return_value=response)
        mock.__aenter__ = AsyncMock(return_value=mock)
        mock.__aexit__ = AsyncMock(return_value=False)

        with patch("span_panel_api._http.httpx.AsyncClient") as cls:
            cls.return_value = mock
            with pytest.raises(SpanPanelServerError):
                await get_homie_schema("192.168.1.1")
