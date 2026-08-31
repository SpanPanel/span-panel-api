"""The schema fetch and the CA download need different ports, and had one.

`panel_http_port` serves two transports with opposite security properties: the
bridge's CA download, which is plaintext by design because it fetches the very
anchor everything else is checked against, and the client's schema fetch, which
should ride the pinned HTTPS transport whenever the caller holds one. One
parameter fed both, so a consumer that pinned a CA could not move its schema
fetch to HTTPS without simultaneously pointing the CA download at a TLS port it
speaks plaintext to.

The split: `panel_https_port` carries the schema fetch when an `ssl_context` is
supplied, and `panel_http_port` keeps the bridge's deliberately-plaintext CA
fetches exactly where they were.

Alongside it, a TLS verification failure on a bootstrap REST call gets its own
exception class. A consumer that fails closed on an untrusted certificate needs
to tell "something answered with a certificate the pin does not sign" apart from
"nothing answered" — the first is terminal and needs a person, the second clears
itself when the panel finishes rebooting.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import ssl
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from span_panel_api._http import _reset_plaintext_warnings
from span_panel_api.auth import get_homie_schema
from span_panel_api.exceptions import (
    SpanPanelConnectionError,
    SpanPanelTLSVerificationError,
    SpanPanelValidationError,
)
from span_panel_api.mqtt import MqttClientConfig
from span_panel_api.mqtt.client import SpanMqttClient

from conftest import flat_schema

HOST = "192.168.1.1"
SERIAL = "sp3-242424-001"


@pytest.fixture(autouse=True)
def _fresh_warning_state() -> None:
    """Keep the once-per-host warning set from leaking between tests."""
    _reset_plaintext_warnings()


@pytest.fixture
def context() -> ssl.SSLContext:
    """Any context object will do — nothing here completes a handshake."""
    return ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)


class _Schema:
    def __init__(self, version: str | None) -> None:
        self.data_model_version = version


def _client(**kwargs: object) -> SpanMqttClient:
    return SpanMqttClient(
        host=HOST,
        serial_number=SERIAL,
        broker_config=MqttClientConfig(broker_host="broker.local", username="u", password="p"),
        **kwargs,  # type: ignore[arg-type]
    )


async def _run_connect_fetch(client: SpanMqttClient, fetch: AsyncMock) -> None:
    """Drive connect() far enough to make its schema fetch, and no further."""
    with (
        patch("span_panel_api.mqtt.client.get_homie_schema", fetch),
        patch.object(client, "_preload_adapter", AsyncMock()),
        patch.object(client, "_build_adapter", MagicMock()),
    ):
        with contextlib.suppress(Exception):
            await client.connect()
    assert fetch.await_count >= 1


class TestSchemaFetchPortSelection:
    @pytest.mark.asyncio
    async def test_a_pinned_client_fetches_the_schema_on_the_https_port(self, context: ssl.SSLContext) -> None:
        client = _client(panel_http_port=80, panel_https_port=8443, ssl_context=context)
        fetch = AsyncMock(return_value=_Schema("1.0"))
        await _run_connect_fetch(client, fetch)
        assert fetch.await_args.kwargs["port"] == 8443
        assert fetch.await_args.kwargs["ssl_context"] is context

    @pytest.mark.asyncio
    async def test_a_pinned_client_without_a_named_port_takes_the_scheme_default(self, context: ssl.SSLContext) -> None:
        """`None` reaches `_build_url`, whose default for a TLS call is 443."""
        client = _client(panel_http_port=80, ssl_context=context)
        fetch = AsyncMock(return_value=_Schema("1.0"))
        await _run_connect_fetch(client, fetch)
        assert fetch.await_args.kwargs["port"] is None
        assert fetch.await_args.kwargs["ssl_context"] is context

    @pytest.mark.asyncio
    async def test_an_unpinned_client_keeps_the_plaintext_port(self) -> None:
        client = _client(panel_http_port=8080)
        fetch = AsyncMock(return_value=_Schema("1.0"))
        await _run_connect_fetch(client, fetch)
        assert fetch.await_args.kwargs["port"] == 8080
        assert fetch.await_args.kwargs["ssl_context"] is None

    @pytest.mark.asyncio
    async def test_the_upgrade_refetch_uses_the_same_transport(self, context: ssl.SSLContext) -> None:
        """One call site for both fetches is the point; prove the retry loop kept it."""
        client = _client(panel_http_port=80, panel_https_port=8443, ssl_context=context)
        client._loop = asyncio.get_running_loop()
        fetch = AsyncMock(return_value=_Schema("1.0"))
        with patch("span_panel_api.mqtt.client.get_homie_schema", fetch):
            assert await client._fetch_schema_with_retry() is not None
        assert fetch.await_args.kwargs["port"] == 8443
        assert fetch.await_args.kwargs["ssl_context"] is context

    def test_an_https_port_without_an_anchor_is_refused(self) -> None:
        """A TLS port with nothing to verify against is a decision nobody made.

        Accepting it silently would put the schema fetch on plaintext HTTP at a
        port the caller believes is TLS — the same misreading `_build_url`
        refuses for port 80 with a context, from the other direction.
        """
        with pytest.raises(SpanPanelValidationError):
            _client(panel_https_port=8443)

    @pytest.mark.asyncio
    async def test_the_bridge_keeps_the_plaintext_port_when_the_schema_fetch_moves(self, context: ssl.SSLContext) -> None:
        """The CA download is plaintext by design and must not follow the pin."""
        client = _client(panel_http_port=8080, panel_https_port=8443, ssl_context=context)
        # A real schema, because this test needs connect() to get all the way
        # to bridge construction rather than stopping at the fetch.
        fetch = AsyncMock(return_value=flat_schema(32))
        bridge_cls = MagicMock()
        with (
            patch("span_panel_api.mqtt.client.get_homie_schema", fetch),
            patch("span_panel_api.mqtt.client.AsyncMqttBridge", bridge_cls),
            patch.object(client, "_preload_adapter", AsyncMock()),
            patch.object(client, "_build_adapter", MagicMock()),
        ):
            with contextlib.suppress(Exception):
                await client.connect()
        assert bridge_cls.call_args is not None
        assert bridge_cls.call_args.kwargs["panel_http_port"] == 8080


class TestFactoryPortRouting:
    """`create_span_client`'s one `port` lands in the slot its transport needs.

    The factory's REST calls already read `port` as "the HTTPS port" when an
    `ssl_context` rides along — `_build_url` refuses anything else — but it then
    handed the same number to `panel_http_port`, pointing the bridge's
    deliberately-plaintext CA download at a TLS port.
    """

    @pytest.mark.asyncio
    async def test_a_pinned_factory_call_routes_its_port_to_the_https_slot(self, context: ssl.SSLContext) -> None:
        from span_panel_api.adapters import _reset_adapter_cache
        from span_panel_api.factory import create_span_client

        _reset_adapter_cache()
        config = MqttClientConfig(broker_host="broker.local", username="u", password="p")
        with (
            patch("span_panel_api.factory.SpanMqttClient") as mock_cls,
            patch("span_panel_api.factory.get_homie_schema", return_value=_Schema(None)),
        ):
            mock_cls.return_value.connect = AsyncMock()
            await create_span_client(
                HOST,
                mqtt_config=config,
                serial_number=SERIAL,
                port=8443,
                ssl_context=context,
            )
        kwargs = mock_cls.call_args.kwargs
        assert kwargs["panel_https_port"] == 8443
        assert kwargs["ssl_context"] is context
        # The CA download takes the plaintext default rather than the TLS port.
        assert kwargs["panel_http_port"] is None

    @pytest.mark.asyncio
    async def test_an_unpinned_factory_call_keeps_its_port_on_the_plaintext_slot(self) -> None:
        from span_panel_api.adapters import _reset_adapter_cache
        from span_panel_api.factory import create_span_client

        _reset_adapter_cache()
        config = MqttClientConfig(broker_host="broker.local", username="u", password="p")
        with (
            patch("span_panel_api.factory.SpanMqttClient") as mock_cls,
            patch("span_panel_api.factory.get_homie_schema", return_value=_Schema(None)),
        ):
            mock_cls.return_value.connect = AsyncMock()
            await create_span_client(HOST, mqtt_config=config, serial_number=SERIAL, port=8080)
        kwargs = mock_cls.call_args.kwargs
        assert kwargs["panel_http_port"] == 8080
        assert kwargs.get("panel_https_port") is None


class TestRedispatchRetryDoesNotRetryVerificationFailures:
    """The retry loop's contract is 'the panel still coming up', and this is not that.

    A verification failure cannot succeed on a later attempt under the same
    context — the anchor is fixed for the session — so retrying it is a
    background task GETting the panel every thirty seconds forever while the
    log blames a slow boot. Left to raise, the redispatch wrapper logs the
    failure once per trigger, and the MQTT side owns the escalation: a rotated
    CA surfaces through the bridge's own diagnosis and fatal-error channel.
    """

    @pytest.mark.asyncio
    async def test_a_verification_failure_raises_out_of_the_retry_loop(self, context: ssl.SSLContext) -> None:
        client = _client(panel_https_port=8443, ssl_context=context)
        client._loop = asyncio.get_running_loop()
        fetch = AsyncMock(side_effect=SpanPanelTLSVerificationError("cert rejected by the pin"))
        with patch("span_panel_api.mqtt.client.get_homie_schema", fetch):
            # wait_for, because the defect this guards against is an infinite
            # retry loop with real backoff sleeps: without it, a regression
            # hangs the suite instead of failing it.
            with pytest.raises(SpanPanelTLSVerificationError):
                await asyncio.wait_for(client._fetch_schema_with_retry(), timeout=5)
        # One attempt, no backoff loop: the failure is not retried at all.
        assert fetch.await_count == 1

    @pytest.mark.asyncio
    async def test_the_escaping_failure_is_logged_as_what_it_is(
        self, context: ssl.SSLContext, caplog: pytest.LogCaptureFixture
    ) -> None:
        """The wrapper's catch-all blamed a slow boot; verification is not that.

        'Reload once the panel is fully back up' is accidentally workable advice
        — a reload does surface the repair — but the sentence points at the
        wrong cause, and the wrong cause is the one a user investigating an
        interception must not be steered away from.
        """
        client = _client(panel_https_port=8443, ssl_context=context)
        client._loop = asyncio.get_running_loop()
        failure = SpanPanelTLSVerificationError("cert rejected by the pin")
        with patch.object(client, "_redispatch_once", AsyncMock(side_effect=failure)):
            with caplog.at_level(logging.ERROR):
                await client._redispatch_if_generation_changed()
        assert "certificate verification" in caplog.text
        assert "fully back up" not in caplog.text


class TestTLSVerificationFailureIsNamed:
    @pytest.mark.asyncio
    async def test_a_certificate_verification_failure_is_its_own_error(self) -> None:
        """The one transport failure that must not be retried into submission."""
        verify_failure = ssl.SSLCertVerificationError("certificate verify failed: unable to get local issuer certificate")
        wrapped = httpx.ConnectError("[SSL: CERTIFICATE_VERIFY_FAILED]")
        wrapped.__cause__ = verify_failure
        injected = MagicMock()
        injected.get = AsyncMock(side_effect=wrapped)

        with pytest.raises(SpanPanelTLSVerificationError) as excinfo:
            await get_homie_schema(HOST, httpx_client=injected)
        # Still a connection error, so a consumer holding the 3.x contract —
        # catch SpanPanelConnectionError, retry — keeps working unchanged.
        assert isinstance(excinfo.value, SpanPanelConnectionError)

    @pytest.mark.asyncio
    async def test_an_ordinary_connect_failure_stays_a_connection_error(self) -> None:
        """A refused socket is 'not up yet', and must not look terminal."""
        injected = MagicMock()
        injected.get = AsyncMock(side_effect=httpx.ConnectError("connection refused"))

        with pytest.raises(SpanPanelConnectionError) as excinfo:
            await get_homie_schema(HOST, httpx_client=injected)
        assert not isinstance(excinfo.value, SpanPanelTLSVerificationError)
