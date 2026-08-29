"""Pinning the panel CA, and refusing to draw the wrong conclusion from a TLS failure.

Two separate claims are under test here and they pull in opposite directions.

The first is that a pinned bridge never re-anchors: it makes no CA request on
connect or on rebuild, so a panel presenting a chain from some other CA cannot
become trusted by being persistent.

The second is that the bridge is *slow* to call something a CA change. A valid
pinned CA still produces `SSLCertVerificationError` when the panel's clock has
reset or its address has moved, and a broker restarting mid-handshake produces
`SSLEOFError` -- which is the ordinary shape of a firmware upgrade. Every one of
those has to stay retryable, because escalating one of them converts a
self-healing outage into a permanent one.
"""

from __future__ import annotations

import asyncio
import base64
import logging
import ssl
from unittest.mock import MagicMock, patch

from paho.mqtt.client import DisconnectFlags
from paho.mqtt.reasoncodes import ReasonCode
import pytest

from span_panel_api._ssl import ca_fingerprint
from span_panel_api.exceptions import (
    SpanPanelCAChangedError,
    SpanPanelConnectionError,
    SpanPanelError,
    SpanPanelStaleDataError,
    SpanPanelTimeoutError,
    SpanPanelValidationError,
)
from span_panel_api.mqtt import connection as conn_mod
from span_panel_api.mqtt.client import SpanMqttClient
from span_panel_api.mqtt.connection import AsyncMqttBridge
from span_panel_api.mqtt.models import MqttClientConfig

from conftest import SERIAL


def _pem(marker: bytes) -> str:
    """A PEM block whose body is `marker`.

    `ca_fingerprint` hashes the decoded DER bytes and asks nothing else of them,
    and every test that also needs a *usable* context patches the context
    builder. Minting real X.509 for these would add a `cryptography` dependency
    to assertions that are about identity, not about validity --
    `test_ssl_context.py` already covers the certificate side.
    """
    return "-----BEGIN CERTIFICATE-----\n" + base64.b64encode(marker).decode() + "\n-----END CERTIFICATE-----\n"


PINNED_PEM = _pem(b"the-panel-ca")
ROTATED_PEM = _pem(b"a-different-ca")
PINNED_FP = ca_fingerprint(PINNED_PEM)
ROTATED_FP = ca_fingerprint(ROTATED_PEM)


def _bridge(*, ca_pem: str | None) -> AsyncMqttBridge:
    return AsyncMqttBridge(
        host="broker.local",
        port=8883,
        username="user",
        password="pass",
        panel_host="panel.invalid",
        serial_number=SERIAL,
        use_tls=True,
        ca_pem=ca_pem,
    )


def _drive_reconnect_loop(bridge: AsyncMqttBridge, client_mock: MagicMock) -> None:
    """Push the bridge into its reconnect loop through a disconnect edge."""
    bridge._on_disconnect(
        client_mock,
        None,
        DisconnectFlags(is_disconnect_packet_from_server=True),
        ReasonCode(packetType=2, aName="Success"),
        None,
    )
    assert bridge._reconnect_task is not None


# ---------------------------------------------------------------------------
# ca_fingerprint
# ---------------------------------------------------------------------------


class TestCaFingerprint:
    def test_stable_across_pem_whitespace(self) -> None:
        """A firmware that reflows its PEM has not rotated its CA.

        Reporting one because the line width changed is the worse of the two
        available errors: it teaches a user to dismiss the alert that matters.
        """
        body = base64.b64encode(b"the-panel-ca").decode()
        variants = [
            f"-----BEGIN CERTIFICATE-----\n{body}\n-----END CERTIFICATE-----\n",
            f"-----BEGIN CERTIFICATE-----\r\n{body}\r\n-----END CERTIFICATE-----\r\n",
            f"  -----BEGIN CERTIFICATE-----\n {body[:4]}\n  {body[4:]}  \n-----END CERTIFICATE-----   ",
            f"issuer: test\n-----BEGIN CERTIFICATE-----\n{body}\n-----END CERTIFICATE-----\ntrailing\n",
        ]
        assert {ca_fingerprint(v) for v in variants} == {PINNED_FP}

    def test_lowercase_hex_without_separators(self) -> None:
        assert len(PINNED_FP) == 64
        assert PINNED_FP == PINNED_FP.lower()
        assert ":" not in PINNED_FP

    def test_different_certificates_differ(self) -> None:
        assert PINNED_FP != ROTATED_FP

    def test_only_the_first_certificate_is_read(self) -> None:
        """An appended chain must not change the anchor's fingerprint."""
        assert ca_fingerprint(PINNED_PEM + ROTATED_PEM) == PINNED_FP

    @pytest.mark.parametrize(
        "bad",
        [
            "no certificate here",
            "-----BEGIN CERTIFICATE-----\nZm9v\n",
            "-----BEGIN CERTIFICATE-----\n!!!not base64!!!\n-----END CERTIFICATE-----\n",
            "-----BEGIN CERTIFICATE-----\n\n-----END CERTIFICATE-----\n",
        ],
        ids=["no-block", "unterminated", "not-base64", "empty"],
    )
    def test_malformed_input_is_rejected(self, bad: str) -> None:
        with pytest.raises(SpanPanelValidationError):
            ca_fingerprint(bad)


# ---------------------------------------------------------------------------
# The pin itself: no CA request on any path
# ---------------------------------------------------------------------------


class TestTrustAnchor:
    @pytest.mark.asyncio
    async def test_pinned_connect_makes_no_ca_request(self, mqtt_client_mock: MagicMock) -> None:
        bridge = _bridge(ca_pem=PINNED_PEM)
        with patch("span_panel_api.mqtt.connection.build_panel_ssl_context") as build:
            await bridge.connect()

        conn_mod.download_ca_cert.assert_not_called()
        build.assert_called_once_with(PINNED_PEM)
        await bridge.disconnect()

    @pytest.mark.asyncio
    async def test_pinned_rebuild_makes_no_ca_request(self, mqtt_client_mock: MagicMock) -> None:
        """The rebuild was the re-anchoring path, and is the one that mattered."""
        bridge = _bridge(ca_pem=PINNED_PEM)
        await bridge.connect()
        conn_mod.download_ca_cert.reset_mock()

        assert await bridge._rebuild_client() is True
        conn_mod.download_ca_cert.assert_not_called()

        await bridge.disconnect()

    @pytest.mark.asyncio
    async def test_unpinned_fetches_and_warns_once_per_bridge(
        self, mqtt_client_mock: MagicMock, caplog: pytest.LogCaptureFixture
    ) -> None:
        """3.0.1's behaviour, kept, plus exactly one warning.

        Once per bridge rather than once per connect: the fetch happens on every
        reconnect, and a line per reconnect through a day-long outage is a log
        nobody reads.
        """
        bridge = _bridge(ca_pem=None)
        with caplog.at_level(logging.WARNING, logger="span_panel_api.mqtt.connection"):
            await bridge.connect()
            await bridge._rebuild_client()
            await bridge._rebuild_client()

        assert conn_mod.download_ca_cert.call_count == 3
        unpinned = [r for r in caplog.records if "obtained unauthenticated" in r.message]
        assert len(unpinned) == 1
        assert unpinned[0].levelno == logging.WARNING

        await bridge.disconnect()


# ---------------------------------------------------------------------------
# The disambiguation procedure
# ---------------------------------------------------------------------------


class TestDiagnoseVerificationFailure:
    """The CA question, which is asked first and settles the matter when it differs.

    The name question that follows a matching fingerprint has its own suite in
    `test_leaf_name_mismatch.py`, against a real broker. Here the pinned PEM is a
    marker rather than a certificate, so the second handshake cannot be attempted
    at all -- which is itself worth asserting, because that failure arrives inside
    the reconnect loop's exception handler and must not escape it.
    """

    @pytest.mark.asyncio
    async def test_unpinned_never_escalates(self) -> None:
        bridge = _bridge(ca_pem=None)
        with patch("span_panel_api.mqtt.connection.download_ca_cert") as fetch:
            assert await bridge._diagnose_verification_failure() is None
        fetch.assert_not_called()

    @pytest.mark.asyncio
    async def test_same_fingerprint_is_not_a_ca_change(self, caplog: pytest.LogCaptureFixture) -> None:
        """An expired leaf or a moved host: verification fails, the anchor did not move."""
        bridge = _bridge(ca_pem=PINNED_PEM)
        with (
            patch("span_panel_api.mqtt.connection.download_ca_cert", return_value=PINNED_PEM),
            caplog.at_level(logging.WARNING, logger="span_panel_api.mqtt.connection"),
        ):
            assert await bridge._diagnose_verification_failure() is None
        assert "still advertises the pinned CA" in caplog.text

    @pytest.mark.asyncio
    async def test_a_pin_the_second_handshake_cannot_use_is_still_transient(self, caplog: pytest.LogCaptureFixture) -> None:
        """The diagnostic must not be able to kill the loop that awaits it.

        `PINNED_PEM` fingerprints perfectly well and is not a certificate, so it
        gets as far as the name question and then cannot build a context. That
        exception is raised inside the reconnect loop's own exception handler,
        where anything escaping leaves a task dead with no traceback and a bridge
        that looks merely disconnected forever.
        """
        bridge = _bridge(ca_pem=PINNED_PEM)
        with (
            patch("span_panel_api.mqtt.connection.download_ca_cert", return_value=PINNED_PEM),
            caplog.at_level(logging.WARNING, logger="span_panel_api.mqtt.connection"),
        ):
            assert await bridge._diagnose_verification_failure() is None

        assert "could not be used for a second look" in caplog.text
        assert bridge.fatal_error is None

    @pytest.mark.asyncio
    async def test_different_fingerprint_carries_both(self) -> None:
        bridge = _bridge(ca_pem=PINNED_PEM)
        with patch("span_panel_api.mqtt.connection.download_ca_cert", return_value=ROTATED_PEM):
            error = await bridge._diagnose_verification_failure()
        assert isinstance(error, SpanPanelCAChangedError)
        assert error.expected_fingerprint == PINNED_FP
        assert error.observed_fingerprint == ROTATED_FP
        assert PINNED_FP in str(error)
        assert ROTATED_FP in str(error)

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "failure",
        [
            SpanPanelConnectionError("panel unreachable"),
            SpanPanelTimeoutError("timed out"),
            OSError("network down"),
        ],
        ids=["unreachable", "timeout", "oserror"],
    )
    async def test_fetch_failure_never_escalates(self, failure: Exception) -> None:
        """Missing evidence is not evidence. A panel mid-reboot looks exactly like this."""
        bridge = _bridge(ca_pem=PINNED_PEM)
        with patch("span_panel_api.mqtt.connection.download_ca_cert", side_effect=failure):
            assert await bridge._diagnose_verification_failure() is None

    @pytest.mark.asyncio
    async def test_unfingerprintable_answer_never_escalates(self) -> None:
        """A proxy's error page in place of a PEM says nothing about the CA."""
        bridge = _bridge(ca_pem=PINNED_PEM)
        with patch("span_panel_api.mqtt.connection.download_ca_cert", return_value="<html>404</html>"):
            assert await bridge._diagnose_verification_failure() is None


# ---------------------------------------------------------------------------
# Initial connect
# ---------------------------------------------------------------------------


class TestInitialConnect:
    @pytest.mark.asyncio
    async def test_ca_changed_while_down_raises_rather_than_looping(self, mqtt_client_mock: MagicMock) -> None:
        """The rotation-while-shut-down case, which used to be an endless setup retry."""
        bridge = _bridge(ca_pem=PINNED_PEM)
        mqtt_client_mock.connect.side_effect = ssl.SSLCertVerificationError("unable to get local issuer certificate")

        with patch("span_panel_api.mqtt.connection.download_ca_cert", return_value=ROTATED_PEM):
            with pytest.raises(SpanPanelCAChangedError) as excinfo:
                await bridge.connect()

        assert excinfo.value.expected_fingerprint == PINNED_FP
        assert excinfo.value.observed_fingerprint == ROTATED_FP

    @pytest.mark.asyncio
    async def test_verification_failure_with_unchanged_ca_stays_retryable(self, mqtt_client_mock: MagicMock) -> None:
        bridge = _bridge(ca_pem=PINNED_PEM)
        mqtt_client_mock.connect.side_effect = ssl.SSLCertVerificationError("certificate has expired")

        with patch("span_panel_api.mqtt.connection.download_ca_cert", return_value=PINNED_PEM):
            with pytest.raises(SpanPanelConnectionError):
                await bridge.connect()


# ---------------------------------------------------------------------------
# The reconnect loop
# ---------------------------------------------------------------------------


class TestReconnectLoop:
    @pytest.mark.asyncio
    async def test_confirmed_ca_change_stops_the_loop_and_fires_the_callback(
        self, mqtt_client_mock: MagicMock, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("span_panel_api.mqtt.connection.MQTT_RECONNECT_MIN_DELAY_S", 0.01)
        bridge = _bridge(ca_pem=PINNED_PEM)
        await bridge.connect()

        seen: list[SpanPanelError] = []
        bridge.set_fatal_error_callback(seen.append)
        mqtt_client_mock.reconnect.side_effect = ssl.SSLCertVerificationError("unable to get local issuer certificate")

        with patch("span_panel_api.mqtt.connection.download_ca_cert", return_value=ROTATED_PEM):
            _drive_reconnect_loop(bridge, mqtt_client_mock)
            await asyncio.sleep(0.2)

        assert isinstance(bridge.fatal_error, SpanPanelCAChangedError)
        assert [type(e) for e in seen] == [SpanPanelCAChangedError]
        # The loop is out, and _on_disconnect cannot start a replacement.
        assert bridge._should_reconnect is False
        assert bridge._reconnect_task is not None
        assert bridge._reconnect_task.done()

    @pytest.mark.asyncio
    async def test_expired_leaf_keeps_retrying(self, mqtt_client_mock: MagicMock, monkeypatch: pytest.MonkeyPatch) -> None:
        """A panel whose clock reset after a power cut must not be declared compromised."""
        monkeypatch.setattr("span_panel_api.mqtt.connection.MQTT_RECONNECT_MIN_DELAY_S", 0.01)
        bridge = _bridge(ca_pem=PINNED_PEM)
        await bridge.connect()
        mqtt_client_mock.reconnect.side_effect = ssl.SSLCertVerificationError("certificate has expired")

        with patch("span_panel_api.mqtt.connection.download_ca_cert", return_value=PINNED_PEM):
            _drive_reconnect_loop(bridge, mqtt_client_mock)
            await asyncio.sleep(0.1)

            assert bridge.fatal_error is None
            assert bridge._should_reconnect is True
            assert mqtt_client_mock.reconnect.call_count > 1

        await bridge.disconnect()

    @pytest.mark.asyncio
    async def test_ssl_eof_keeps_retrying(self, mqtt_client_mock: MagicMock, monkeypatch: pytest.MonkeyPatch) -> None:
        """A broker restarting mid-handshake -- the ordinary shape of a firmware upgrade."""
        monkeypatch.setattr("span_panel_api.mqtt.connection.MQTT_RECONNECT_MIN_DELAY_S", 0.01)
        bridge = _bridge(ca_pem=PINNED_PEM)
        await bridge.connect()
        mqtt_client_mock.reconnect.side_effect = ssl.SSLEOFError("EOF occurred in violation of protocol")

        with patch("span_panel_api.mqtt.connection.download_ca_cert", return_value=ROTATED_PEM) as fetch:
            _drive_reconnect_loop(bridge, mqtt_client_mock)
            await asyncio.sleep(0.1)

            assert bridge.fatal_error is None
            assert bridge._should_reconnect is True
            # Never even asked: SSLEOFError is not a verification failure, so the
            # diagnostic that could escalate it is never reached.
            fetch.assert_not_called()

        await bridge.disconnect()

    @pytest.mark.asyncio
    async def test_diagnostic_fetch_failure_keeps_retrying(
        self, mqtt_client_mock: MagicMock, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("span_panel_api.mqtt.connection.MQTT_RECONNECT_MIN_DELAY_S", 0.01)
        bridge = _bridge(ca_pem=PINNED_PEM)
        await bridge.connect()
        mqtt_client_mock.reconnect.side_effect = ssl.SSLCertVerificationError("unable to get local issuer certificate")

        with patch(
            "span_panel_api.mqtt.connection.download_ca_cert",
            side_effect=SpanPanelConnectionError("panel HTTP not up yet"),
        ):
            _drive_reconnect_loop(bridge, mqtt_client_mock)
            await asyncio.sleep(0.1)

            assert bridge.fatal_error is None
            assert bridge._should_reconnect is True

        await bridge.disconnect()

    @pytest.mark.asyncio
    async def test_unpinned_verification_failure_still_rebuilds(
        self, mqtt_client_mock: MagicMock, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Unpinned behaviour is 3.0.1's, unchanged: the refetch *is* the recovery."""
        monkeypatch.setattr("span_panel_api.mqtt.connection.MQTT_RECONNECT_MIN_DELAY_S", 0.01)
        bridge = _bridge(ca_pem=None)
        await bridge.connect()
        before = conn_mod.download_ca_cert.call_count
        mqtt_client_mock.reconnect.side_effect = [ssl.SSLCertVerificationError("verify failed"), 0]

        _drive_reconnect_loop(bridge, mqtt_client_mock)
        await asyncio.sleep(0.2)

        assert conn_mod.download_ca_cert.call_count == before + 1
        assert bridge.fatal_error is None

        await bridge.disconnect()


# ---------------------------------------------------------------------------
# Surfacing it through the client
# ---------------------------------------------------------------------------


class TestClientSurface:
    """The client is wired to the bridge's terminal state, and to nothing else.

    Built by hand rather than through `connect()`: these assertions are about
    what `ping()` and `get_snapshot()` consult and in what order, and driving a
    full Homie handshake to reach them would test the handshake.
    """

    @staticmethod
    def _client(*, connected: bool = True) -> tuple[SpanMqttClient, AsyncMqttBridge]:
        client = SpanMqttClient(
            host="panel.invalid",
            serial_number=SERIAL,
            broker_config=MqttClientConfig(broker_host="broker.local", username="u", password="p", ca_pem=PINNED_PEM),
        )
        bridge = _bridge(ca_pem=PINNED_PEM)
        bridge._connected = connected
        bridge.set_fatal_error_callback(client._on_fatal_error)
        client._bridge = bridge
        adapter = MagicMock()
        adapter.is_ready.return_value = True
        client._adapter = adapter
        return client, bridge

    @pytest.mark.asyncio
    async def test_ping_and_get_snapshot_reraise(self) -> None:
        """A consumer that registered no callback still cannot read dead as healthy."""
        client, bridge = self._client()
        bridge._enter_terminal_state(SpanPanelCAChangedError(PINNED_FP, ROTATED_FP))

        with pytest.raises(SpanPanelCAChangedError):
            await client.ping()
        with pytest.raises(SpanPanelCAChangedError):
            await client.get_snapshot()

    @pytest.mark.asyncio
    async def test_stale_data_is_still_stale_data(self) -> None:
        """Without a terminal failure, a disconnect keeps its retryable shape."""
        client, _bridge_obj = self._client(connected=False)
        with pytest.raises(SpanPanelStaleDataError):
            await client.get_snapshot()
        assert await client.ping() is False

    @pytest.mark.asyncio
    async def test_registered_callback_fires_and_unregisters(self) -> None:
        client, bridge = self._client()
        seen: list[SpanPanelError] = []
        unregister = client.register_fatal_error_callback(seen.append)

        bridge._enter_terminal_state(SpanPanelCAChangedError(PINNED_FP, ROTATED_FP))
        assert len(seen) == 1

        unregister()
        unregister()  # idempotent
        client._on_fatal_error(SpanPanelCAChangedError(PINNED_FP, ROTATED_FP))
        assert len(seen) == 1

    @pytest.mark.asyncio
    async def test_a_raising_subscriber_does_not_swallow_the_rest(self) -> None:
        client, _bridge_obj = self._client()
        seen: list[SpanPanelError] = []

        def _explode(_error: SpanPanelError) -> None:
            raise RuntimeError("subscriber is broken")

        client.register_fatal_error_callback(_explode)
        client.register_fatal_error_callback(seen.append)
        client._on_fatal_error(SpanPanelCAChangedError(PINNED_FP, ROTATED_FP))
        assert len(seen) == 1
