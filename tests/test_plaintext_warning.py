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

from span_panel_api.auth import download_ca_cert, get_v2_status, regenerate_passphrase, register_v2
from span_panel_api.detection import detect_api_version
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
    async def test_the_ca_download_never_warns(self, caplog: pytest.LogCaptureFixture) -> None:
        """Reversed in 3.4.0, deliberately: this endpoint is unverifiable by construction.

        The warning exists so an operator can tell a security property is *off*
        when it could be on, and for the first fetch of the anchor itself there
        is no "on": any verification would require the anchor being fetched, an
        unverified-TLS wrapping would be readable and forgeable by the same
        active on-path attacker, and the payload is a public certificate with no
        credential in either direction — the authenticity control is the leaf
        check its callers run *after* the fetch. Every caller also already says
        so in its own voice: the bridge's once-per-bridge unpinned warning, the
        config flow's fingerprint confirmation, and the deferred pin's
        trust-on-first-use log line. Until 3.3.x this endpoint warned like the
        rest, naming credentials it never carries; that line is what issue
        span#264 reported. A caller that already holds the anchor and wants a
        verified second copy passes ``ssl_context``, and no warning was ever in
        question there.
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

        assert len(_warnings(caplog)) == 0

    @pytest.mark.asyncio
    async def test_the_status_probe_never_warns(self, caplog: pytest.LogCaptureFixture) -> None:
        """Exempted in 3.4.1, for the CA download's reason from the other side.

        The status endpoint is the detection probe — the request zeroconf
        discovery makes against a device nobody has configured, once per boot,
        where no pin can exist because trust-on-first-use has not happened and
        there is nothing the operator can do but configure the panel. It
        carries no credential in either direction, and the flows that probe it
        on a configured-but-unpinned entry acquire the pin before any
        credential moves, with a human confirming the fingerprint. The warning
        here named credentials the call never carries and pointed at an action
        nobody could take — the line that made an operator remove a healthy
        emulator. The exemption's limits live in `_warn_plaintext_transport`'s
        docstring: a consumer probing a *configured* host owes that probe the
        entry's own transport, because no warning will say so anymore.
        """
        answer = _json_response({"serialNumber": "SYN-0000-0001", "firmwareVersion": "f"}, method="GET")
        with caplog.at_level(logging.WARNING):
            await get_v2_status(HOST, httpx_client=_client("get", answer))
        assert len(_warnings(caplog)) == 0

    @pytest.mark.asyncio
    async def test_the_detection_probe_never_warns(self, caplog: pytest.LogCaptureFixture) -> None:
        """Detection reads the same endpoint, and must be equally silent."""
        answer = _json_response({"serialNumber": "SYN-0000-0001", "firmwareVersion": "f"}, method="GET")
        with caplog.at_level(logging.WARNING):
            await detect_api_version(HOST, httpx_client=_client("get", answer))
        assert len(_warnings(caplog)) == 0

    @pytest.mark.asyncio
    async def test_the_status_probe_does_not_swallow_a_later_warning(self, caplog: pytest.LogCaptureFixture) -> None:
        """The probe fires first in every flow, so it must not spend the slot.

        Today it does exactly that: a reauth's status probe claims the
        once-per-host warning, and the credential-bearing call behind it says
        nothing. The warning belongs to whichever call actually deserves it.
        """
        status = _json_response({"serialNumber": "SYN-0000-0001", "firmwareVersion": "f"}, method="GET")
        rotate = _json_response({"ebusBrokerPassword": "new-pass"}, method="PUT")
        with caplog.at_level(logging.WARNING):
            await get_v2_status(HOST, httpx_client=_client("get", status))
            # The midpoint is the assertion: the slot must still be unspent
            # here, so the warning below demonstrably belongs to the rotation.
            assert len(_warnings(caplog)) == 0
            await regenerate_passphrase(HOST, "jwt", httpx_client=_client("put", rotate))
        assert len(_warnings(caplog)) == 1

    @pytest.mark.asyncio
    async def test_the_ca_download_does_not_swallow_a_later_warning(self, caplog: pytest.LogCaptureFixture) -> None:
        """Skipping the warning must not mark the host as already warned.

        The diagnostic CA re-read runs on a *pinned* entry whose other calls are
        HTTPS; if it claimed the once-per-host slot, a genuinely plaintext call
        made later — a reauth on an entry that lost its pin — would say nothing.
        """
        answer = _json_response({"ebusBrokerPassword": "new-pass"}, method="PUT")
        # Built before the patch below replaces `httpx.AsyncClient`; a spec
        # against the patched class is a spec against a Mock, which mock refuses.
        injected = _client("put", answer)
        with patch("span_panel_api._http.httpx.AsyncClient") as mock_cls:
            dedicated = AsyncMock()
            dedicated.__aenter__ = AsyncMock(return_value=dedicated)
            dedicated.__aexit__ = AsyncMock(return_value=False)
            dedicated.get = AsyncMock(return_value=_text_response(PEM))
            mock_cls.return_value = dedicated
            with caplog.at_level(logging.WARNING):
                await download_ca_cert(HOST)
                await regenerate_passphrase(HOST, "jwt", httpx_client=injected)

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
