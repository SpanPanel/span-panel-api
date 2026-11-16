"""Bootstrapping a panel over plaintext HTTP says so, once.

Without an `ssl_context` every bootstrap request is plain `http://`, and the
ones that carry a credential warn: registration sends the panel passphrase,
when it is given one, and brings an access token and the broker password back;
passphrase rotation sends a bearer token and brings the new broker password
back; and the three FQDN calls each send a bearer token. Anything on the path
reads all of it. That stays the default, because requiring a pin would break
every install on upgrade — but the MQTT bridge has warned about its unpinned
trust anchor since 3.1.0, and the REST side said nothing at all.

The three calls that carry no credential — the CA download, the status probe
and, since 3.9.1, the schema read — do not warn while their requests carry no
header and no body, and do not spend the warning a credential-bearing call
behind them needs. What the schema exemption would otherwise leave unsaid, a
session says itself: a client whose TLS broker is pinned and whose REST calls
are not warns once that its schema reads are unverified, and a bridge without
TLS warns that its whole connection is plaintext.

The warning lives in the transport, so no call site can be added later that
quietly skips it, and it is scoped to the panel rather than to a request or a
client object. A call made without an injected client builds a fresh one each
time, so anything narrower than "once per panel" is a warning per call — a
line nobody reads by the second hour.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Iterator
import contextlib
from dataclasses import dataclass
import inspect
import json
import logging
import ssl
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from span_panel_api import auth, detection
from span_panel_api._http import CA_CERT_PATH, HOMIE_SCHEMA_PATH, V2_STATUS_PATH, _request
from span_panel_api.auth import (
    delete_fqdn,
    download_ca_cert,
    get_fqdn,
    get_homie_schema,
    get_v2_status,
    regenerate_passphrase,
    register_fqdn,
    register_v2,
    rotate_passphrase,
)
from span_panel_api.detection import detect_api_version
from span_panel_api.exceptions import SpanPanelConnectionError
from span_panel_api.mqtt.client import SpanMqttClient
from span_panel_api.mqtt.connection import AsyncMqttBridge
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
    """Not just registration: the two calls that move the most sensitive secrets, one by one.

    Every bootstrap call, the three FQDN calls included, is classified in
    `TestEachBootstrapCallWarnsExactlyWhenItCarriesACredential`.
    """

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
        config flow's log of the fingerprint it pinned, and the deferred pin's
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
        credential moves, by trust on first use rather than by anybody
        confirming the fingerprint. The warning here named credentials the
        call never carries and pointed at an action nobody could take — the
        line that made an operator remove a healthy emulator. The exemption's
        limits live in `_warn_plaintext_transport`'s
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
    async def test_the_schema_read_never_warns(self, caplog: pytest.LogCaptureFixture) -> None:
        """Exempted in 3.9.1, reversing what 3.4.0 and 3.4.1 kept on purpose.

        Those releases exempted the CA download and the status probe and left
        the schema read warning, because its only callers were then a session
        and the factory. Home Assistant, since 2.3.0, also reads it in its
        config flow, before the CA is pinned, to refuse firmware it cannot
        drive — so the line naming the passphrase and the broker password
        appeared the first time each panel was discovered or added after a
        start, for a read that carries neither, and spent the slot the
        registration behind it needed. Unlike the other two it does have an
        "on"; it is exempt
        because its integrity is controlled elsewhere, by the consumer's own
        read over the pinned transport and by the session's cross-check of the
        REST generation against the MQTT one. What the exemption drops for a
        session is said by the session instead (see
        `TestTheSessionSaysWhatTheExemptionDrops`).
        """
        answer = _json_response(SCHEMA_JSON, method="GET")
        with caplog.at_level(logging.WARNING):
            await get_homie_schema(HOST, httpx_client=_client("get", answer))
        assert len(_warnings(caplog)) == 0

    @pytest.mark.asyncio
    async def test_the_schema_read_does_not_swallow_a_later_warning(self, caplog: pytest.LogCaptureFixture) -> None:
        """A config flow reads the schema before it registers, so the read must not spend the slot.

        Spent, the registration behind it — the call that actually sends the
        passphrase and brings the broker password back — would say nothing.
        """
        schema = _json_response(SCHEMA_JSON, method="GET")
        with caplog.at_level(logging.WARNING):
            await get_homie_schema(HOST, httpx_client=_client("get", schema))
            # The midpoint is the assertion: the slot must still be unspent
            # here, so the warning below demonstrably belongs to registration.
            assert len(_warnings(caplog)) == 0
            await register_v2(HOST, "home-assistant", "secret", httpx_client=_client("post", _json_response(V2_AUTH_JSON)))
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
    async def test_a_prebuilt_config_no_longer_warns_for_its_schema_read(self, caplog: pytest.LogCaptureFixture) -> None:
        """Reversed in 3.9.1, deliberately: this path's one request carries no credential.

        It sends no passphrase, and its only bootstrap request is the schema
        read, which until 3.9.0 raised the plaintext-transport warning here,
        naming the passphrase and the broker password it never carries. The
        read is now exempt (`test_the_schema_read_never_warns` says why). The
        client is replaced here, so none of the lines a real client and its
        bridge say about their posture can appear; those are
        `TestTheSessionSaysWhatTheExemptionDrops`'s subject, including this
        same factory path with a pinned TLS broker.
        """
        from span_panel_api.factory import create_span_client

        config = MqttClientConfig(broker_host="broker.local", username="user", password="pass")
        injected = _client("get", _json_response(SCHEMA_JSON, method="GET"))
        with patch("span_panel_api.factory.SpanMqttClient") as mock_cls:
            mock_cls.return_value.connect = AsyncMock()
            with caplog.at_level(logging.WARNING):
                await create_span_client(HOST, mqtt_config=config, serial_number="SYN-0000-0001", httpx_client=injected)

        # The read was made, so the silence is the exemption's and not a skipped request.
        injected.get.assert_awaited_once()
        assert len(_warnings(caplog)) == 0

    @pytest.mark.asyncio
    async def test_the_registering_path_warns_exactly_once(self, caplog: pytest.LogCaptureFixture) -> None:
        """Registration and the schema read are two requests to one panel; the warning is registration's."""
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


STATUS_JSON = {"serialNumber": "SYN-0000-0001", "firmwareVersion": "f"}


@dataclass(frozen=True)
class _BootstrapCall:
    """One public bootstrap call, driven through an injected client."""

    label: str
    function: Callable[..., Awaitable[object]]
    method: str
    answer: httpx.Response
    carries_a_credential: bool
    call: Callable[[AsyncMock], Awaitable[object]]


#: Every public bootstrap call, and whether it puts a credential on the wire in
#: either direction. `test_the_table_names_every_public_bootstrap_call` keeps it
#: complete, so a call added later cannot go unclassified.
_BOOTSTRAP_CALLS = (
    _BootstrapCall(
        "registration",
        register_v2,
        "post",
        _json_response(V2_AUTH_JSON),
        True,
        lambda injected: register_v2(HOST, "home-assistant", "secret", httpx_client=injected),
    ),
    # No passphrase goes up, but an access token and the broker password come back.
    _BootstrapCall(
        "door-bypass registration",
        register_v2,
        "post",
        _json_response(V2_AUTH_JSON),
        True,
        lambda injected: register_v2(HOST, "home-assistant", None, httpx_client=injected),
    ),
    _BootstrapCall(
        "passphrase rotation",
        rotate_passphrase,
        "put",
        _json_response({"ebusBrokerPassword": "new-pass", "hopPassphrase": "new-hop"}, method="PUT"),
        True,
        lambda injected: rotate_passphrase(HOST, "jwt", httpx_client=injected),
    ),
    _BootstrapCall(
        "passphrase regeneration",
        regenerate_passphrase,
        "put",
        _json_response({"ebusBrokerPassword": "new-pass"}, method="PUT"),
        True,
        lambda injected: regenerate_passphrase(HOST, "jwt", httpx_client=injected),
    ),
    _BootstrapCall(
        "FQDN registration",
        register_fqdn,
        "post",
        _json_response({}),
        True,
        lambda injected: register_fqdn(HOST, "jwt", "panel.example", httpx_client=injected),
    ),
    _BootstrapCall(
        "FQDN read",
        get_fqdn,
        "get",
        _json_response({"ebusTlsFqdn": "panel.example"}, method="GET"),
        True,
        lambda injected: get_fqdn(HOST, "jwt", httpx_client=injected),
    ),
    _BootstrapCall(
        "FQDN removal",
        delete_fqdn,
        "delete",
        _json_response({}, method="DELETE"),
        True,
        lambda injected: delete_fqdn(HOST, "jwt", httpx_client=injected),
    ),
    _BootstrapCall(
        "CA download",
        download_ca_cert,
        "get",
        _text_response(PEM),
        False,
        lambda injected: download_ca_cert(HOST, httpx_client=injected),
    ),
    _BootstrapCall(
        "schema read",
        get_homie_schema,
        "get",
        _json_response(SCHEMA_JSON, method="GET"),
        False,
        lambda injected: get_homie_schema(HOST, httpx_client=injected),
    ),
    _BootstrapCall(
        "status probe",
        get_v2_status,
        "get",
        _json_response(STATUS_JSON, method="GET"),
        False,
        lambda injected: get_v2_status(HOST, httpx_client=injected),
    ),
    _BootstrapCall(
        "detection probe",
        detect_api_version,
        "get",
        _json_response(STATUS_JSON, method="GET"),
        False,
        lambda injected: detect_api_version(HOST, httpx_client=injected),
    ),
)


class TestEachBootstrapCallWarnsExactlyWhenItCarriesACredential:
    """The whole rule in one table: the warning names credentials, so only a call carrying one says it."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("bootstrap", _BOOTSTRAP_CALLS, ids=lambda bootstrap: bootstrap.label)
    async def test_it_warns_exactly_when_it_carries_a_credential(
        self, bootstrap: _BootstrapCall, caplog: pytest.LogCaptureFixture
    ) -> None:
        injected = _client(bootstrap.method, bootstrap.answer)
        with caplog.at_level(logging.WARNING):
            await bootstrap.call(injected)
        sent = getattr(injected, bootstrap.method)
        # The request was made, so a silent row is the exemption's and not a skipped call.
        sent.assert_awaited_once()
        if not bootstrap.carries_a_credential:
            # A silent row is honest only while nothing but the URL goes up: a
            # header or a body is where a credential would ride.
            assert sent.await_args.kwargs.get("headers") is None
            assert sent.await_args.kwargs.get("json") is None
        assert len(_warnings(caplog)) == (1 if bootstrap.carries_a_credential else 0)

    def test_the_table_names_every_public_bootstrap_call(self) -> None:
        """Every public coroutine the bootstrap modules define has a row, exported or not.

        Read from the modules rather than from `__all__`: a call that puts a
        credential on the wire is no less on the wire for being left out of the
        package's exports, and one added without a row would go unclassified.
        """
        defined = {
            name
            for module in (auth, detection)
            for name, function in inspect.getmembers(module, inspect.iscoroutinefunction)
            if not name.startswith("_") and function.__module__ == module.__name__
        }
        assert defined == {bootstrap.function.__name__ for bootstrap in _BOOTSTRAP_CALLS}


class TestTheExemptionReadsTheRequest:
    """An exempt path stays silent only while its request carries nothing a credential could ride in.

    No public call sends a header or a body to an exempt path today, which is
    why these drive the transport directly: the guarantee is about the call
    somebody changes later, such as a bearer token added to the schema read.
    """

    @pytest.mark.asyncio
    @pytest.mark.parametrize("path", [CA_CERT_PATH, V2_STATUS_PATH, HOMIE_SCHEMA_PATH])
    async def test_a_header_on_an_exempt_path_warns(self, path: str, caplog: pytest.LogCaptureFixture) -> None:
        injected = _client("get", _json_response({}, method="GET"))
        with caplog.at_level(logging.WARNING):
            await _request(
                "GET", HOST, None, path, timeout=1.0, httpx_client=injected, headers={"Authorization": "Bearer jwt"}
            )
        assert len(_warnings(caplog)) == 1

    @pytest.mark.asyncio
    @pytest.mark.parametrize("path", ["/api/v2/auth/register", "/api/v2/auth/passphrase", auth._FQDN_PATH])
    async def test_no_other_path_is_exempt_even_when_bare(self, path: str, caplog: pytest.LogCaptureFixture) -> None:
        """The three are the whole exempt set: a credential-bearing endpoint warns for its path alone.

        A request to one of these always carries something today, so only a
        bare one shows that the path, and not just what rides with it, is what
        keeps it warning.
        """
        injected = _client("get", _json_response({}, method="GET"))
        with caplog.at_level(logging.WARNING):
            await _request("GET", HOST, None, path, timeout=1.0, httpx_client=injected)
        assert len(_warnings(caplog)) == 1

    @pytest.mark.asyncio
    @pytest.mark.parametrize("path", [CA_CERT_PATH, V2_STATUS_PATH, HOMIE_SCHEMA_PATH])
    async def test_a_body_on_an_exempt_path_warns(self, path: str, caplog: pytest.LogCaptureFixture) -> None:
        injected = _client("post", _json_response({}))
        with caplog.at_level(logging.WARNING):
            await _request("POST", HOST, None, path, timeout=1.0, httpx_client=injected, json={"hopPassphrase": "secret"})
        assert len(_warnings(caplog)) == 1


SESSION_LINE = "Homie schema reads for"
BRIDGE_LINE = "obtained unauthenticated"
PLAINTEXT_BROKER_LINE = "is not encrypted: use_tls is False"
TRANSPORT_LINE = "Bootstrap traffic for"


def _lines(caplog: pytest.LogCaptureFixture, marker: str) -> list[str]:
    return [line for line in _warnings(caplog) if marker in line]


def _session(*, ca_pem: str | None, ssl_context: ssl.SSLContext | None, use_tls: bool = True) -> SpanMqttClient:
    return SpanMqttClient(
        HOST,
        "SYN-0000-0001",
        MqttClientConfig(broker_host="broker.local", username="user", password="pass", use_tls=use_tls, ca_pem=ca_pem),
        ssl_context=ssl_context,
    )


@contextlib.contextmanager
def _a_panel_with_no_broker() -> Iterator[AsyncMock]:
    """A panel that serves its schema over either scheme and has no CA or broker to reach.

    Every client here builds its own HTTP client, plaintext or pinned, so the
    class is replaced and every schema read lands on one mock, which is
    yielded so a test can count the reads. A connect then ends where the
    bridge would open a socket, after each posture line has had its chance:
    a pinned bridge cannot build a context from the synthetic PEM, an
    unpinned one cannot fetch its CA, and one without TLS cannot build a
    broker client. All three raise `SpanPanelConnectionError`.
    """
    with (
        patch("span_panel_api._http.httpx.AsyncClient") as mock_cls,
        patch(
            "span_panel_api.mqtt.connection.download_ca_cert",
            AsyncMock(side_effect=SpanPanelConnectionError("no CA in this test")),
        ),
        patch.object(
            AsyncMqttBridge,
            "_make_paho_client",
            side_effect=SpanPanelConnectionError("no broker in this test"),
        ),
    ):
        dedicated = AsyncMock()
        dedicated.__aenter__ = AsyncMock(return_value=dedicated)
        dedicated.__aexit__ = AsyncMock(return_value=False)
        dedicated.get = AsyncMock(return_value=_json_response(SCHEMA_JSON, method="GET"))
        mock_cls.return_value = dedicated
        yield dedicated


class TestTheSessionSaysWhatTheExemptionDrops:
    """A pinned TLS broker beside unpinned REST calls is the one posture nothing else reports.

    Before 3.9.1 the transport warning was the only line a consumer in that
    posture got about its schema reads, and the schema exemption removes it:
    the bridge is pinned, so it says nothing either. The client now says it,
    once, and stays silent wherever another line already covers the posture
    or nothing is unverified.
    """

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("use_tls", "ca_pem", "pinned_rest", "session_lines", "unpinned_lines", "plaintext_lines"),
        [
            pytest.param(True, PEM, False, 1, 0, 0, id="TLS broker pinned, REST not: the client says it"),
            pytest.param(True, PEM, True, 0, 0, 0, id="TLS broker and REST pinned: nothing to say"),
            pytest.param(True, None, False, 0, 1, 0, id="TLS, neither pinned: the bridge's unpinned line"),
            pytest.param(True, None, True, 0, 1, 0, id="TLS, REST pinned, broker not: the bridge's unpinned line"),
            # Without TLS `ca_pem` pins nothing, so the client must not claim it
            # does; the bridge's plaintext line covers every one of these.
            pytest.param(False, PEM, False, 0, 0, 1, id="no TLS, ca_pem set, REST not pinned: the bridge's plaintext line"),
            pytest.param(False, PEM, True, 0, 0, 1, id="no TLS, ca_pem set, REST pinned: the bridge's plaintext line"),
            pytest.param(False, None, False, 0, 0, 1, id="no TLS, nothing pinned: the bridge's plaintext line"),
            pytest.param(False, None, True, 0, 0, 1, id="no TLS, REST pinned: the bridge's plaintext line"),
        ],
    )
    async def test_each_posture_gets_exactly_the_line_it_needs(
        self,
        use_tls: bool,
        ca_pem: str | None,
        pinned_rest: bool,
        session_lines: int,
        unpinned_lines: int,
        plaintext_lines: int,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        client = _session(
            ca_pem=ca_pem,
            ssl_context=ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT) if pinned_rest else None,
            use_tls=use_tls,
        )
        with _a_panel_with_no_broker() as panel, caplog.at_level(logging.WARNING):
            with pytest.raises(SpanPanelConnectionError):
                await client.connect()
        panel.get.assert_awaited_once()
        assert len(_lines(caplog, SESSION_LINE)) == session_lines
        assert len(_lines(caplog, BRIDGE_LINE)) == unpinned_lines
        assert len(_lines(caplog, PLAINTEXT_BROKER_LINE)) == plaintext_lines
        # No posture is told about its schema read by the transport any more.
        assert _lines(caplog, TRANSPORT_LINE) == []

    @pytest.mark.asyncio
    async def test_the_mixed_posture_says_it_once_per_client(self, caplog: pytest.LogCaptureFixture) -> None:
        """Once across the reads a session makes, as the bridge says its own line once across reconnects.

        A connect reads the schema, a redispatch reads it again, and a second
        connect on the same client reuses it: one line for all of it, because a
        line per read through a day of reconnects is a log nobody reads.
        """
        client = _session(ca_pem=PEM, ssl_context=None)
        with _a_panel_with_no_broker() as panel, caplog.at_level(logging.WARNING):
            with pytest.raises(SpanPanelConnectionError):
                await client.connect()
            await client._redispatch_if_generation_changed()
            with pytest.raises(SpanPanelConnectionError):
                await client.connect()
        assert panel.get.await_count == 2
        lines = _lines(caplog, SESSION_LINE)
        assert len(lines) == 1
        assert HOST in lines[0]
        assert "ssl_context" in lines[0]
        assert "No credential" in lines[0]

    @pytest.mark.asyncio
    async def test_a_new_client_says_it_again(self, caplog: pytest.LogCaptureFixture) -> None:
        """Per client, not per host: a reload builds a new client, and the bridge's line repeats on one too."""
        with _a_panel_with_no_broker(), caplog.at_level(logging.WARNING):
            for _ in range(2):
                with pytest.raises(SpanPanelConnectionError):
                    await _session(ca_pem=PEM, ssl_context=None).connect()
        assert len(_lines(caplog, SESSION_LINE)) == 2

    @pytest.mark.asyncio
    async def test_the_factory_read_is_covered(self, caplog: pytest.LogCaptureFixture) -> None:
        """The factory reads the schema before the client exists and hands it in.

        So the client makes no read of its own at connect, and a line said only
        at the client's fetch would say nothing about the one read this path
        makes. It is said at connect instead, whichever way the schema arrived.
        """
        from span_panel_api.factory import create_span_client

        config = MqttClientConfig(broker_host="broker.local", username="user", password="pass", ca_pem=PEM)
        with _a_panel_with_no_broker() as panel, caplog.at_level(logging.WARNING):
            with pytest.raises(SpanPanelConnectionError):
                await create_span_client(HOST, mqtt_config=config, serial_number="SYN-0000-0001")
        # The factory's read, and no other.
        panel.get.assert_awaited_once()
        assert len(_lines(caplog, SESSION_LINE)) == 1
        assert _lines(caplog, TRANSPORT_LINE) == []
