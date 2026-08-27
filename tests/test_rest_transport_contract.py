"""Every bootstrap REST call answers in this library's vocabulary.

Seven of them caught `httpx.ConnectError` and `httpx.TimeoutException` and
nothing else, so a connection that failed any other way — a panel resetting its
listener mid-response, a proxy closing without answering, which is what a proxy
restarting under load produces — came back as a raw httpx exception. A caller
holding this library's contract catches `SpanPanelError` and does not catch
that, so it escaped every retry clause built on it.

The bodies were unguarded in the same way. A 200 is not a promise of a body: a
panel part-way through starting answers one with nothing in it, and a proxy in
front of one answers with an HTML error page under a 200. Those reached the
caller as a bare `JSONDecodeError`, and a 200 missing a field as a bare
`KeyError`, neither of them something this library says it can raise.

`get_homie_schema` already did all of this correctly and is here to hold that
line while the other seven move onto the same helper.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from unittest.mock import AsyncMock

import httpx
import pytest

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
from span_panel_api.exceptions import SpanPanelAPIError, SpanPanelConnectionError, SpanPanelError

HOST = "panel.invalid"

#: Every REST call this library makes, with the httpx method it reaches for.
#: `download_ca_cert` reads a PEM rather than JSON, so it appears only where the
#: transport is under test.
CALLS: list[tuple[str, Callable[[httpx.AsyncClient], Awaitable[object]], str]] = [
    ("register_v2", lambda c: register_v2(HOST, "home-assistant", "pass", httpx_client=c), "post"),
    ("download_ca_cert", lambda c: download_ca_cert(HOST, httpx_client=c), "get"),
    ("regenerate_passphrase", lambda c: regenerate_passphrase(HOST, "jwt", httpx_client=c), "put"),
    ("register_fqdn", lambda c: register_fqdn(HOST, "jwt", "panel.example.com", httpx_client=c), "post"),
    ("get_fqdn", lambda c: get_fqdn(HOST, "jwt", httpx_client=c), "get"),
    ("delete_fqdn", lambda c: delete_fqdn(HOST, "jwt", httpx_client=c), "delete"),
    ("get_v2_status", lambda c: get_v2_status(HOST, httpx_client=c), "get"),
    ("get_homie_schema", lambda c: get_homie_schema(HOST, httpx_client=c), "get"),
]

#: The subset that decodes a JSON object out of a 200. The other four either
#: read a PEM or read nothing but the status line.
DECODERS: list[tuple[str, Callable[[httpx.AsyncClient], Awaitable[object]], str]] = [
    ("register_v2", lambda c: register_v2(HOST, "home-assistant", "pass", httpx_client=c), "post"),
    ("regenerate_passphrase", lambda c: regenerate_passphrase(HOST, "jwt", httpx_client=c), "put"),
    ("get_fqdn", lambda c: get_fqdn(HOST, "jwt", httpx_client=c), "get"),
    ("get_v2_status", lambda c: get_v2_status(HOST, httpx_client=c), "get"),
]


def _client(method: str, answer: httpx.Response | Exception) -> AsyncMock:
    """An injected client whose one method returns, or raises, `answer`."""
    injected = AsyncMock(spec=httpx.AsyncClient)
    if isinstance(answer, Exception):
        setattr(injected, method, AsyncMock(side_effect=answer))
    else:
        setattr(injected, method, AsyncMock(return_value=answer))
    return injected


def _response(status_code: int, *, body: bytes, content_type: str) -> httpx.Response:
    return httpx.Response(
        status_code=status_code,
        content=body,
        headers={"content-type": content_type},
        request=httpx.Request("GET", f"http://{HOST}/"),
    )


def _json_response(payload: object, status_code: int = 200) -> httpx.Response:
    return _response(status_code, body=json.dumps(payload).encode(), content_type="application/json")


class TestTransportFailuresAreTranslated:
    """The whole `httpx.TransportError` family, not just a refused connect."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize(("name", "call", "method"), CALLS, ids=[row[0] for row in CALLS])
    @pytest.mark.parametrize(
        "failure",
        [
            httpx.ReadError("connection reset"),
            httpx.WriteError("broken pipe"),
            httpx.RemoteProtocolError("server closed connection without sending a response"),
        ],
        ids=["read-reset", "write-reset", "proxy-closed-without-answering"],
    )
    async def test_a_transport_failure_is_a_connection_error(
        self,
        name: str,
        call: Callable[[httpx.AsyncClient], Awaitable[object]],
        method: str,
        failure: Exception,
    ) -> None:
        with pytest.raises(SpanPanelConnectionError):
            await call(_client(method, failure))

    @pytest.mark.asyncio
    async def test_detection_reports_a_read_reset_as_a_failed_probe(self) -> None:
        """The detector answers rather than raising, and this was not among the
        failures it recognised — so a panel resetting mid-probe raised
        `httpx.ReadError` out of a function documented to return a result."""
        result = await detect_api_version(HOST, httpx_client=_client("get", httpx.ReadError("connection reset")))
        assert result.api_version == "v1"
        assert result.probe_failed is True


class TestMalformedBodies:
    """A 200 whose body cannot be read is this library's error, not httpx's."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize(("name", "call", "method"), DECODERS, ids=[row[0] for row in DECODERS])
    async def test_a_200_that_is_not_json_is_an_api_error(
        self, name: str, call: Callable[[httpx.AsyncClient], Awaitable[object]], method: str
    ) -> None:
        page = _response(200, body=b"<html><body>Bad Gateway</body></html>", content_type="text/html")
        with pytest.raises(SpanPanelAPIError):
            await call(_client(method, page))

    @pytest.mark.asyncio
    @pytest.mark.parametrize(("name", "call", "method"), DECODERS, ids=[row[0] for row in DECODERS])
    async def test_a_200_that_is_not_an_object_is_an_api_error(
        self, name: str, call: Callable[[httpx.AsyncClient], Awaitable[object]], method: str
    ) -> None:
        with pytest.raises(SpanPanelAPIError):
            await call(_client(method, _json_response([1, 2, 3])))

    @pytest.mark.asyncio
    async def test_register_names_the_field_the_panel_left_out(self) -> None:
        """A 200 without `accessToken` used to raise `KeyError('accessToken')`."""
        with pytest.raises(SpanPanelAPIError) as caught:
            await register_v2(
                HOST, "home-assistant", "pass", httpx_client=_client("post", _json_response({"tokenType": "Bearer"}))
            )
        assert "accessToken" in str(caught.value)

    @pytest.mark.asyncio
    async def test_regenerate_names_the_field_the_panel_left_out(self) -> None:
        with pytest.raises(SpanPanelAPIError) as caught:
            await regenerate_passphrase(HOST, "jwt", httpx_client=_client("put", _json_response({})))
        assert "ebusBrokerPassword" in str(caught.value)

    @pytest.mark.asyncio
    async def test_every_malformed_body_stays_inside_the_error_hierarchy(self) -> None:
        """The point of the translation: one `except SpanPanelError` catches it."""
        with pytest.raises(SpanPanelError):
            await get_v2_status(HOST, httpx_client=_client("get", _response(200, body=b"", content_type="text/plain")))
