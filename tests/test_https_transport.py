"""HTTPS for the bootstrap REST calls, and the two ways it could silently not happen.

Both failure modes this covers are ones where the control *appears* to be on:
an `ssl_context` handed to a call that also has an injected httpx client (httpx
fixes `verify=` at construction, so the context would have been dropped), and a
port left at the stored plaintext default while the caller believes it is
talking TLS.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
import json
import ssl
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from span_panel_api._http import _build_url, _get_client
from span_panel_api.auth import (
    delete_fqdn,
    download_ca_cert,
    get_fqdn,
    get_homie_schema,
    get_v2_status,
    regenerate_passphrase,
    register_fqdn,
    register_v2,
)
from span_panel_api.detection import detect_api_version
from span_panel_api.exceptions import SpanPanelValidationError

HOST = "panel.invalid"

V2_AUTH_JSON = {
    "accessToken": "jwt",
    "tokenType": "Bearer",
    "iatMs": 1700000000000,
    "ebusBrokerUsername": "broker-user",
    "ebusBrokerPassword": "broker-pass",
    "ebusBrokerHost": HOST,
    "ebusBrokerMqttsPort": 8883,
    "ebusBrokerWsPort": 9001,
    "ebusBrokerWssPort": 9002,
    "hostname": "panel",
    "serialNumber": "SYN-0000-0001",
    "hopPassphrase": "hop",
}

PEM = "-----BEGIN CERTIFICATE-----\nZm9v\n-----END CERTIFICATE-----\n"


@pytest.fixture
def context() -> ssl.SSLContext:
    """Any context object will do — nothing here completes a handshake."""
    return ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)


def _json_response(payload: object, status_code: int = 200) -> httpx.Response:
    return httpx.Response(
        status_code=status_code,
        content=json.dumps(payload).encode(),
        headers={"content-type": "application/json"},
        request=httpx.Request("GET", f"http://{HOST}/"),
    )


def _text_response(text: str, status_code: int = 200) -> httpx.Response:
    return httpx.Response(
        status_code=status_code,
        content=text.encode(),
        headers={"content-type": "text/plain"},
        request=httpx.Request("GET", f"http://{HOST}/"),
    )


class TestBuildUrl:
    def test_plaintext_unchanged_from_3_0_1(self) -> None:
        assert _build_url(HOST, 80, "/api/v2/status") == f"http://{HOST}/api/v2/status"
        assert _build_url(HOST, 8080, "/api/v2/status") == f"http://{HOST}:8080/api/v2/status"

    def test_omitted_port_defaults_by_scheme(self, context: ssl.SSLContext) -> None:
        assert _build_url(HOST, None, "/p") == f"http://{HOST}/p"
        assert _build_url(HOST, None, "/p", context) == f"https://{HOST}/p"

    def test_https_names_a_non_default_port(self, context: ssl.SSLContext) -> None:
        assert _build_url(HOST, 8443, "/p", context) == f"https://{HOST}:8443/p"

    def test_explicit_443_is_omitted(self, context: ssl.SSLContext) -> None:
        assert _build_url(HOST, 443, "/p", context) == f"https://{HOST}/p"

    def test_explicit_port_80_with_a_context_is_refused(self, context: ssl.SSLContext) -> None:
        """The unmigrated-consumer case: a port stored before the CA was pinned."""
        with pytest.raises(SpanPanelValidationError) as excinfo:
            _build_url(HOST, 80, "/p", context)
        message = str(excinfo.value)
        assert "80" in message
        assert "443" in message
        assert HOST in message


class TestGetClient:
    @pytest.mark.asyncio
    async def test_context_beats_an_injected_client(self, context: ssl.SSLContext) -> None:
        """The whole point of L2: the pin must not be silently dropped."""
        injected = AsyncMock(spec=httpx.AsyncClient)
        injected.aclose = AsyncMock()

        with patch("span_panel_api._http.httpx.AsyncClient") as mock_cls:
            dedicated = AsyncMock()
            dedicated.__aenter__ = AsyncMock(return_value=dedicated)
            dedicated.__aexit__ = AsyncMock(return_value=False)
            mock_cls.return_value = dedicated

            async with _get_client(injected, timeout=9.0, ssl_context=context) as client:
                assert client is dedicated

        mock_cls.assert_called_once_with(timeout=9.0, verify=context)
        # The dedicated client is ours, so we close it; the injected one is not.
        dedicated.__aexit__.assert_awaited_once()
        injected.aclose.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_context_still_yields_the_injected_client(self) -> None:
        injected = AsyncMock(spec=httpx.AsyncClient)
        async with _get_client(injected, timeout=1.0) as client:
            assert client is injected


class TestAuthCallsUseHttps:
    """Every bootstrap endpoint moves to https:// when a context is supplied."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("call", "method", "response", "path"),
        [
            (
                lambda ctx, c: register_v2(HOST, "ha", "secret", httpx_client=c, ssl_context=ctx),
                "post",
                _json_response(V2_AUTH_JSON),
                "/api/v2/auth/register",
            ),
            (
                lambda ctx, c: get_homie_schema(HOST, httpx_client=c, ssl_context=ctx),
                "get",
                _json_response({"firmwareVersion": "x", "types": {}}),
                "/api/v2/homie/schema",
            ),
            (
                lambda ctx, c: regenerate_passphrase(HOST, "tok", httpx_client=c, ssl_context=ctx),
                "put",
                _json_response({"ebusBrokerPassword": "new"}),
                "/api/v2/auth/passphrase",
            ),
            (
                lambda ctx, c: register_fqdn(HOST, "tok", "panel.example", httpx_client=c, ssl_context=ctx),
                "post",
                _json_response({}, 204),
                "/api/v2/dns/fqdn",
            ),
            (
                lambda ctx, c: get_fqdn(HOST, "tok", httpx_client=c, ssl_context=ctx),
                "get",
                _json_response({"ebusTlsFqdn": "panel.example"}),
                "/api/v2/dns/fqdn",
            ),
            (
                lambda ctx, c: delete_fqdn(HOST, "tok", httpx_client=c, ssl_context=ctx),
                "delete",
                _json_response({}, 204),
                "/api/v2/dns/fqdn",
            ),
            (
                lambda ctx, c: get_v2_status(HOST, httpx_client=c, ssl_context=ctx),
                "get",
                _json_response({"serialNumber": "SYN-0000-0001", "firmwareVersion": "x"}),
                "/api/v2/status",
            ),
            (
                lambda ctx, c: detect_api_version(HOST, httpx_client=c, ssl_context=ctx),
                "get",
                _json_response({"serialNumber": "SYN-0000-0001", "firmwareVersion": "x"}),
                "/api/v2/status",
            ),
        ],
        ids=[
            "register_v2",
            "get_homie_schema",
            "regenerate_passphrase",
            "register_fqdn",
            "get_fqdn",
            "delete_fqdn",
            "get_v2_status",
            "detect_api_version",
        ],
    )
    async def test_https_url_and_dedicated_client(
        self,
        context: ssl.SSLContext,
        call: Callable[[ssl.SSLContext, httpx.AsyncClient], Awaitable[object]],
        method: str,
        response: httpx.Response,
        path: str,
    ) -> None:
        injected = AsyncMock(spec=httpx.AsyncClient)
        with patch("span_panel_api._http.httpx.AsyncClient") as mock_cls:
            dedicated = AsyncMock()
            dedicated.__aenter__ = AsyncMock(return_value=dedicated)
            dedicated.__aexit__ = AsyncMock(return_value=False)
            getattr(dedicated, method).return_value = response
            mock_cls.return_value = dedicated

            await call(context, injected)

        mock_cls.assert_called_once()
        assert mock_cls.call_args.kwargs["verify"] is context
        url = getattr(dedicated, method).call_args.args[0]
        assert url == f"https://{HOST}{path}"
        # The injected client must not have been used for a call that pins a CA.
        getattr(injected, method).assert_not_called()

    @pytest.mark.asyncio
    async def test_without_a_context_the_url_is_unchanged(self) -> None:
        injected = AsyncMock(spec=httpx.AsyncClient)
        injected.get = AsyncMock(return_value=_json_response({"firmwareVersion": "x", "types": {}}))
        await get_homie_schema(HOST, httpx_client=injected)
        assert injected.get.call_args.args[0] == f"http://{HOST}/api/v2/homie/schema"


class TestDownloadCaCert:
    """The bootstrap fetch stays plaintext by default; the diagnostic refetch does not."""

    @pytest.mark.asyncio
    async def test_default_is_plaintext(self) -> None:
        injected = AsyncMock(spec=httpx.AsyncClient)
        injected.get = AsyncMock(return_value=_text_response(PEM))
        assert await download_ca_cert(HOST, httpx_client=injected) == PEM
        assert injected.get.call_args.args[0] == f"http://{HOST}/api/v2/certificate/ca"

    @pytest.mark.asyncio
    async def test_refetch_with_a_context_is_https(self, context: ssl.SSLContext) -> None:
        with patch("span_panel_api._http.httpx.AsyncClient") as mock_cls:
            dedicated = AsyncMock()
            dedicated.__aenter__ = AsyncMock(return_value=dedicated)
            dedicated.__aexit__ = AsyncMock(return_value=False)
            dedicated.get.return_value = _text_response(PEM)
            mock_cls.return_value = dedicated

            assert await download_ca_cert(HOST, ssl_context=context) == PEM

        assert dedicated.get.call_args.args[0] == f"https://{HOST}/api/v2/certificate/ca"
