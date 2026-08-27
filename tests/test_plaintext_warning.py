"""Bootstrapping a panel over plaintext HTTP says so, once.

Without an `ssl_context` every bootstrap request is plain `http://`, and two of
them carry credentials: registration sends the panel passphrase and brings the
broker password back, and passphrase rotation sends a bearer token and brings
the new broker password back. Anything on the path reads all of it. That stays
the default, because requiring a pin would break every install on upgrade — but
the MQTT bridge has warned about its unpinned trust anchor since 3.1.0, and the
REST side said nothing at all.

The warning lives in the transport, so no call site can be added later that
quietly skips it, and it is scoped to the panel rather than to a request or a
client object. Those are the same two constraints the MQTT bridge already
resolved the same way: the CA is refetched on every reconnect, from a fresh
client each time, so anything narrower than "once per panel" is a warning per
reconnect — a line nobody reads by the second hour of an outage.
"""

from __future__ import annotations

import json
import logging
import ssl
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from span_panel_api.auth import download_ca_cert, regenerate_passphrase, register_v2
from span_panel_api.mqtt.models import MqttClientConfig

HOST = "panel.invalid"

V2_AUTH_JSON = {
    "accessToken": "jwt",
    "tokenType": "Bearer",
    "iatMs": 1700000000000,
    "ebusBrokerUsername": "user",
    "ebusBrokerPassword": "pass",
    "ebusBrokerHost": HOST,
    "ebusBrokerMqttsPort": 8883,
    "ebusBrokerWsPort": 9001,
    "ebusBrokerWssPort": 9002,
    "hostname": "spanpanel",
    "serialNumber": "SYN-0000-0001",
    "hopPassphrase": "hop",
}

PEM = "-----BEGIN CERTIFICATE-----\nsynthetic\n-----END CERTIFICATE-----"

#: A flat-schema fetch: no `dataModelVersion`, which is what routes the panel
#: to the schema_0 adapter the dev environment has installed.
SCHEMA_JSON = {
    "firmwareVersion": "spanos2/r202603/05",
    "types": {"energy.ebus.device.circuit": {"space": {"datatype": "integer", "format": "1:32:1"}}},
}


def _json_response(payload: object, method: str = "POST") -> httpx.Response:
    return httpx.Response(
        200,
        content=json.dumps(payload).encode(),
        headers={"content-type": "application/json"},
        request=httpx.Request(method, f"http://{HOST}/"),
    )


def _text_response(body: str) -> httpx.Response:
    return httpx.Response(
        200,
        content=body.encode(),
        headers={"content-type": "text/plain"},
        request=httpx.Request("GET", f"http://{HOST}/"),
    )


def _client(method: str, answer: httpx.Response) -> AsyncMock:
    injected = AsyncMock(spec=httpx.AsyncClient)
    setattr(injected, method, AsyncMock(return_value=answer))
    return injected


def _warnings(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [record.getMessage() for record in caplog.records if record.levelno >= logging.WARNING]


class TestEveryCredentialBearingCallWarns:
    """Both of the calls that put a secret on the wire, not just registration."""

    @pytest.mark.asyncio
    async def test_registration_warns(self, caplog: pytest.LogCaptureFixture) -> None:
        with caplog.at_level(logging.WARNING):
            await register_v2(HOST, "home-assistant", "secret", httpx_client=_client("post", _json_response(V2_AUTH_JSON)))
        warnings = _warnings(caplog)
        assert len(warnings) == 1
        assert HOST in warnings[0]
        assert "plaintext" in warnings[0]
        assert "ssl_context" in warnings[0]

    @pytest.mark.asyncio
    async def test_passphrase_rotation_warns(self, caplog: pytest.LogCaptureFixture) -> None:
        """The reauth path calls this one directly, and it was silent.

        It sends the bearer token up and brings the new broker password back
        over the same plaintext transport registration uses.
        """
        answer = _json_response({"ebusBrokerPassword": "new-pass"}, method="PUT")
        with caplog.at_level(logging.WARNING):
            await regenerate_passphrase(HOST, "jwt", httpx_client=_client("put", answer))
        warnings = _warnings(caplog)
        assert len(warnings) == 1
        assert HOST in warnings[0]

    @pytest.mark.asyncio
    async def test_the_warning_never_repeats_the_credential(self, caplog: pytest.LogCaptureFixture) -> None:
        """It is a warning about a credential, not a place to put one."""
        with caplog.at_level(logging.WARNING):
            await register_v2(
                HOST,
                "home-assistant",
                "correct-horse-battery-staple",
                httpx_client=_client("post", _json_response(V2_AUTH_JSON)),
            )
        assert "correct-horse-battery-staple" not in caplog.text


class TestItIsSaidOnce:
    @pytest.mark.asyncio
    async def test_two_calls_on_one_client_warn_once(self, caplog: pytest.LogCaptureFixture) -> None:
        answer = _json_response({"ebusBrokerPassword": "new-pass"}, method="PUT")
        injected = _client("put", answer)
        with caplog.at_level(logging.WARNING):
            await regenerate_passphrase(HOST, "jwt", httpx_client=injected)
            await regenerate_passphrase(HOST, "jwt", httpx_client=injected)
        assert len(_warnings(caplog)) == 1

    @pytest.mark.asyncio
    async def test_repeated_ca_fetches_on_fresh_clients_warn_once(self, caplog: pytest.LogCaptureFixture) -> None:
        """The reconnect path, and the reason this is scoped to the panel.

        `download_ca_cert` is called on every MQTT reconnect with no injected
        client, so each call builds its own. Anything keyed on the client object
        would warn once per reconnect — exactly the log the bridge's own
        once-per-bridge warning exists to avoid.
        """
        with patch("span_panel_api._http.httpx.AsyncClient") as mock_cls:
            dedicated = AsyncMock()
            dedicated.__aenter__ = AsyncMock(return_value=dedicated)
            dedicated.__aexit__ = AsyncMock(return_value=False)
            dedicated.get = AsyncMock(return_value=_text_response(PEM))
            mock_cls.return_value = dedicated

            with caplog.at_level(logging.WARNING):
                for _ in range(5):
                    await download_ca_cert(HOST)

        assert len(_warnings(caplog)) == 1

    @pytest.mark.asyncio
    async def test_a_second_panel_is_warned_about_separately(self, caplog: pytest.LogCaptureFixture) -> None:
        """Scoped to the panel, so a second unpinned panel is not swallowed."""
        answer = _json_response({"ebusBrokerPassword": "new-pass"}, method="PUT")
        with caplog.at_level(logging.WARNING):
            await regenerate_passphrase(HOST, "jwt", httpx_client=_client("put", answer))
            await regenerate_passphrase("other-panel.invalid", "jwt", httpx_client=_client("put", answer))
        assert len(_warnings(caplog)) == 2


class TestTlsIsSilent:
    @pytest.mark.asyncio
    async def test_a_pinned_call_never_warns(self, caplog: pytest.LogCaptureFixture) -> None:
        context = ssl.create_default_context()
        with patch("span_panel_api._http.httpx.AsyncClient") as mock_cls:
            dedicated = AsyncMock()
            dedicated.__aenter__ = AsyncMock(return_value=dedicated)
            dedicated.__aexit__ = AsyncMock(return_value=False)
            dedicated.post = AsyncMock(return_value=_json_response(V2_AUTH_JSON))
            mock_cls.return_value = dedicated

            with caplog.at_level(logging.WARNING):
                await register_v2(HOST, "home-assistant", "secret", ssl_context=context)

        assert _warnings(caplog) == []

    @pytest.mark.asyncio
    async def test_pinning_one_panel_does_not_silence_an_unpinned_one(self, caplog: pytest.LogCaptureFixture) -> None:
        """A silent TLS call must not register the host as already warned."""
        context = ssl.create_default_context()
        # Built before the patch: specced against the real class, not the mock.
        plaintext = _client("post", _json_response(V2_AUTH_JSON))
        with patch("span_panel_api._http.httpx.AsyncClient") as mock_cls:
            dedicated = AsyncMock()
            dedicated.__aenter__ = AsyncMock(return_value=dedicated)
            dedicated.__aexit__ = AsyncMock(return_value=False)
            dedicated.post = AsyncMock(return_value=_json_response(V2_AUTH_JSON))
            mock_cls.return_value = dedicated

            with caplog.at_level(logging.WARNING):
                await register_v2(HOST, "home-assistant", "secret", ssl_context=context)
                await register_v2(HOST, "home-assistant", "secret", httpx_client=plaintext)

        assert len(_warnings(caplog)) == 1


class TestCreateSpanClient:
    """Both factory paths bootstrap over the same transport.

    Neither patches the bootstrap calls out, because the warning now comes from
    the request itself -- patching them would leave the test asserting nothing.
    """

    @pytest.mark.asyncio
    async def test_a_prebuilt_config_still_warns(self, caplog: pytest.LogCaptureFixture) -> None:
        """This path sends no passphrase, but the schema fetch is still plaintext."""
        from span_panel_api.factory import create_span_client

        config = MqttClientConfig(broker_host="broker.local", username="user", password="pass")
        injected = _client("get", _json_response(SCHEMA_JSON, method="GET"))
        with patch("span_panel_api.factory.SpanMqttClient") as mock_cls:
            mock_cls.return_value.connect = AsyncMock()
            with caplog.at_level(logging.WARNING):
                await create_span_client(HOST, mqtt_config=config, serial_number="SYN-0000-0001", httpx_client=injected)

        assert len(_warnings(caplog)) == 1

    @pytest.mark.asyncio
    async def test_the_registering_path_warns_exactly_once(self, caplog: pytest.LogCaptureFixture) -> None:
        """Registration and the schema fetch are two requests to one panel."""
        from span_panel_api.factory import create_span_client

        injected = AsyncMock(spec=httpx.AsyncClient)
        injected.post = AsyncMock(return_value=_json_response(V2_AUTH_JSON))
        injected.get = AsyncMock(return_value=_json_response(SCHEMA_JSON, method="GET"))
        with patch("span_panel_api.factory.SpanMqttClient") as mock_cls:
            mock_cls.return_value.connect = AsyncMock()
            with caplog.at_level(logging.WARNING):
                await create_span_client(HOST, passphrase="secret", httpx_client=injected)

        injected.post.assert_awaited_once()
        injected.get.assert_awaited_once()
        assert len(_warnings(caplog)) == 1
