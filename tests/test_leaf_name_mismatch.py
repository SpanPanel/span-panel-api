"""Telling a panel that moved from a panel whose clock reset.

Both produce `SSLCertVerificationError` against a perfectly valid pinned CA, and
until now the library said so and stopped there: a warning per retry saying it
could be either, and a consumer left waiting through one of them forever. One is
transient and the panel fixes it; the other is an address that will never come
back on its own, and only a person can act on it.

What distinguishes them is a second handshake with hostname checking relaxed --
which still verifies the chain, the signature and the expiry against the pin, and
therefore reaches the point of holding a certificate whose names can be read. The
tests here are about the conclusion the transport draws from that and what it does
with it, so every one of them runs a real handshake against a real server; the
certificate-level outcomes are in `test_ssl_context.py`.

Two properties matter more than the classification itself and are asserted
separately below: the reconnect loop is **not** stopped by a mismatch, because a
returning DHCP lease still fixes this without anyone's help; and nothing the
diagnostic sees can become the pin.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import ssl
from unittest.mock import AsyncMock, MagicMock, patch

from paho.mqtt.client import ConnectFlags, DisconnectFlags
from paho.mqtt.reasoncodes import ReasonCode
import pytest

from span_panel_api._ssl import LeafNameMismatch
from span_panel_api.exceptions import SpanPanelCAChangedError
from span_panel_api.mqtt.client import SpanMqttClient
from span_panel_api.mqtt.connection import AsyncMqttBridge
from span_panel_api.mqtt.models import MqttClientConfig

from conftest import MOCK_SCHEMA, SERIAL
from tls_fixtures import Chain, closed_port, mint_chain, tls_server

_LOG = "span_panel_api.mqtt.connection"


def _bridge(chain: Chain | None, host: str, port: int) -> AsyncMqttBridge:
    """A pinned TLS bridge dialling ``host:port``, or an unpinned one for ``None``.

    ``panel_host`` is deliberately not ``host``: the CA re-read goes to the panel's
    HTTP endpoint and the diagnostic handshake goes to the broker, and conflating
    the two would hide a diagnostic that dialled the wrong one.
    """
    return AsyncMqttBridge(
        host=host,
        port=port,
        username="user",
        password="pass",
        panel_host="panel.invalid",
        serial_number=SERIAL,
        use_tls=True,
        ca_pem=chain.ca_pem if chain is not None else None,
    )


def _succeed_connect(bridge: AsyncMqttBridge) -> None:
    """Drive the CONNACK the bridge would see on a successful (re)connect."""
    bridge._on_connect(
        MagicMock(),
        None,
        ConnectFlags(session_present=0),
        ReasonCode(packetType=2, aName="Success"),
        None,
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


class TestTheDiagnosis:
    """`_diagnose_verification_failure`, driven directly, against a live broker."""

    @pytest.mark.asyncio
    async def test_a_moved_panel_is_reported_and_is_not_terminal(self) -> None:
        """The whole point: a typed signal, and a transport that keeps trying."""
        chain = mint_chain(names=("panel.local", "10.0.0.5"))
        seen: list[LeafNameMismatch] = []

        with tls_server(chain) as (host, port):
            bridge = _bridge(chain, host, port)
            bridge.set_leaf_mismatch_callback(seen.append)
            with patch("span_panel_api.mqtt.connection.download_ca_cert", return_value=chain.ca_pem):
                assert await bridge._diagnose_verification_failure() is None

        assert seen == [LeafNameMismatch(host="127.0.0.1", leaf_names=("panel.local", "10.0.0.5"))]
        assert bridge.fatal_error is None

    @pytest.mark.asyncio
    async def test_the_warning_names_the_certificate_and_the_configuration(self, caplog: pytest.LogCaptureFixture) -> None:
        """Replaces "it could be either" with the answer, for the log a user reads.

        The per-retry warning stays -- it is the only place a mismatch that
        nothing subscribed to shows up -- so what changes is that it is now
        specific, and specific in the direction that names the remedy.
        """
        chain = mint_chain(names=("panel.local",))

        with tls_server(chain) as (host, port):
            bridge = _bridge(chain, host, port)
            with (
                patch("span_panel_api.mqtt.connection.download_ca_cert", return_value=chain.ca_pem),
                caplog.at_level(logging.WARNING, logger=_LOG),
            ):
                await bridge._diagnose_verification_failure()

        assert "names panel.local" in caplog.text
        assert "configured as 127.0.0.1" in caplog.text
        assert "would both look like this" not in caplog.text

    @pytest.mark.asyncio
    async def test_reported_once_until_a_successful_connect(self) -> None:
        """A mismatch that lasts a week is one condition, not one per backoff tick.

        And a mismatch that comes back after a recovery is a second condition,
        which is why the latch is released by the connect rather than never.
        """
        chain = mint_chain(names=("panel.local",))
        seen: list[LeafNameMismatch] = []

        with tls_server(chain) as (host, port):
            bridge = _bridge(chain, host, port)
            bridge.set_leaf_mismatch_callback(seen.append)
            with patch("span_panel_api.mqtt.connection.download_ca_cert", return_value=chain.ca_pem):
                await bridge._diagnose_verification_failure()
                await bridge._diagnose_verification_failure()
                assert len(seen) == 1

                _succeed_connect(bridge)
                await bridge._diagnose_verification_failure()

        assert len(seen) == 2
        assert seen[0] == seen[1]

    @pytest.mark.asyncio
    async def test_an_expired_leaf_reports_nothing(self, caplog: pytest.LogCaptureFixture) -> None:
        """A panel whose clock reset fixes itself, and a notice saying "wait" is noise."""
        chain = mint_chain(names=("127.0.0.1",), expired=True)
        seen: list[LeafNameMismatch] = []

        with tls_server(chain) as (host, port):
            bridge = _bridge(chain, host, port)
            bridge.set_leaf_mismatch_callback(seen.append)
            with (
                patch("span_panel_api.mqtt.connection.download_ca_cert", return_value=chain.ca_pem),
                caplog.at_level(logging.WARNING, logger=_LOG),
            ):
                assert await bridge._diagnose_verification_failure() is None

        assert seen == []
        assert "still advertises the pinned CA" in caplog.text
        assert "rejected too" in caplog.text

    @pytest.mark.asyncio
    async def test_an_unreachable_broker_reports_nothing(self, caplog: pytest.LogCaptureFixture) -> None:
        """A panel mid-reboot answers neither question, and that is not an answer."""
        chain = mint_chain(names=("panel.local",))
        seen: list[LeafNameMismatch] = []

        with closed_port() as (host, port):
            bridge = _bridge(chain, host, port)
            bridge.set_leaf_mismatch_callback(seen.append)
            with (
                patch("span_panel_api.mqtt.connection.download_ca_cert", return_value=chain.ca_pem),
                caplog.at_level(logging.WARNING, logger=_LOG),
            ):
                assert await bridge._diagnose_verification_failure() is None

        assert seen == []
        assert "could not reach it" in caplog.text

    @pytest.mark.asyncio
    async def test_a_leaf_that_names_the_host_reports_nothing(self, caplog: pytest.LogCaptureFixture) -> None:
        """Cannot follow a failed strict handshake, so it is logged and not acted on."""
        chain = mint_chain(names=("127.0.0.1",))
        seen: list[LeafNameMismatch] = []

        with tls_server(chain) as (host, port):
            bridge = _bridge(chain, host, port)
            bridge.set_leaf_mismatch_callback(seen.append)
            with (
                patch("span_panel_api.mqtt.connection.download_ca_cert", return_value=chain.ca_pem),
                caplog.at_level(logging.WARNING, logger=_LOG),
            ):
                assert await bridge._diagnose_verification_failure() is None

        assert seen == []
        assert "does name 127.0.0.1" in caplog.text

    @pytest.mark.asyncio
    async def test_a_rotated_ca_short_circuits_the_name_question(self) -> None:
        """The order is load-bearing: a different anchor settles it, names or no names.

        The broker here serves a chain that would read as a name mismatch if
        anybody asked. Nobody asks, because the panel is advertising an anchor
        that is not the pin -- which is terminal, and which a "you have probably
        moved" notice alongside it would only soften.
        """
        chain = mint_chain(names=("panel.local",))
        rotated = mint_chain(names=("panel.local",))
        seen: list[LeafNameMismatch] = []

        with tls_server(chain) as (host, port):
            bridge = _bridge(chain, host, port)
            bridge.set_leaf_mismatch_callback(seen.append)
            with patch("span_panel_api.mqtt.connection.download_ca_cert", return_value=rotated.ca_pem):
                verdict = await bridge._diagnose_verification_failure()

        assert isinstance(verdict, SpanPanelCAChangedError)
        assert seen == []

    @pytest.mark.asyncio
    async def test_an_unpinned_bridge_never_probes(self) -> None:
        """Nothing to verify against, so there is no question to ask."""
        chain = mint_chain(names=("panel.local",))
        seen: list[LeafNameMismatch] = []

        with tls_server(chain) as (host, port):
            bridge = _bridge(None, host, port)
            bridge.set_leaf_mismatch_callback(seen.append)
            with patch("span_panel_api.mqtt.connection.download_ca_cert") as fetch:
                assert await bridge._diagnose_verification_failure() is None

        fetch.assert_not_called()
        assert seen == []

    @pytest.mark.asyncio
    async def test_a_raising_subscriber_is_swallowed_and_still_latches(self, caplog: pytest.LogCaptureFixture) -> None:
        """The diagnostic runs on the reconnect path; a broken subscriber cannot kill it."""
        chain = mint_chain(names=("panel.local",))

        def _explode(_mismatch: LeafNameMismatch) -> None:
            raise RuntimeError("subscriber is broken")

        with tls_server(chain) as (host, port):
            bridge = _bridge(chain, host, port)
            bridge.set_leaf_mismatch_callback(_explode)
            with (
                patch("span_panel_api.mqtt.connection.download_ca_cert", return_value=chain.ca_pem),
                caplog.at_level(logging.WARNING, logger=_LOG),
            ):
                await bridge._diagnose_verification_failure()

        assert "Leaf-mismatch callback raised" in caplog.text
        assert bridge._leaf_mismatch_reported is True


class TestThePinIsUntouched:
    """The discipline the CA re-read is built on, extended to the second fetch.

    A diagnostic that could re-anchor would be worse than no diagnostic: the
    failing handshake is exactly the moment an attacker is answering, and this
    path now opens a second connection at that moment.
    """

    @pytest.mark.asyncio
    async def test_the_anchor_survives_a_mismatch(self) -> None:
        chain = mint_chain(names=("panel.local",))

        with tls_server(chain) as (host, port):
            bridge = _bridge(chain, host, port)
            with patch("span_panel_api.mqtt.connection.download_ca_cert", return_value=chain.ca_pem):
                await bridge._diagnose_verification_failure()

        assert bridge._ca_pem == chain.ca_pem

    @pytest.mark.asyncio
    async def test_no_context_is_rebuilt_from_what_the_diagnostic_saw(self) -> None:
        """The transport's own context builder is never reached from this path.

        `probe_leaf_name` builds one of its own, from the pin, and throws it away.
        Watching the transport's import of the builder is what separates the two:
        a call here would mean a context had been made for the connection out of
        something the diagnostic observed.
        """
        chain = mint_chain(names=("panel.local",))

        with tls_server(chain) as (host, port):
            bridge = _bridge(chain, host, port)
            with (
                patch("span_panel_api.mqtt.connection.download_ca_cert", return_value=chain.ca_pem),
                patch("span_panel_api.mqtt.connection.build_panel_ssl_context") as build,
            ):
                await bridge._diagnose_verification_failure()

        build.assert_not_called()

    @pytest.mark.asyncio
    async def test_a_rebuild_after_a_mismatch_still_anchors_on_the_pin(self, mqtt_client_mock: MagicMock) -> None:
        """The recovery path a mismatch leaves running must not have moved.

        `mqtt_client_mock` patches the transport's context builder, so what is
        asserted is the argument: whatever the broker served during the
        diagnostic, the rebuild is handed the pinned PEM and nothing else.
        """
        chain = mint_chain(names=("panel.local",))

        with tls_server(chain) as (host, port):
            bridge = _bridge(chain, host, port)
            await bridge.connect()
            with patch("span_panel_api.mqtt.connection.download_ca_cert", return_value=chain.ca_pem):
                await bridge._diagnose_verification_failure()
                assert await bridge._rebuild_client() is True

            from span_panel_api.mqtt import connection as conn_mod

            conn_mod.build_panel_ssl_context.assert_called_with(chain.ca_pem)
            await bridge.disconnect()


class TestTheReconnectLoop:
    @pytest.mark.asyncio
    async def test_a_moved_panel_keeps_the_loop_running_and_reports_once(
        self, mqtt_client_mock: MagicMock, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The behaviour the design turns on: report it, and go on retrying.

        A terminal state here would convert a DHCP lease that comes back in an
        hour into an outage that lasts until somebody reloads the integration.
        The loop runs several times over the sleep below and the notification
        still arrives exactly once.
        """
        monkeypatch.setattr("span_panel_api.mqtt.connection.MQTT_RECONNECT_MIN_DELAY_S", 0.01)
        chain = mint_chain(names=("panel.local",))
        seen: list[LeafNameMismatch] = []

        with tls_server(chain) as (host, port):
            bridge = _bridge(chain, host, port)
            await bridge.connect()
            bridge.set_leaf_mismatch_callback(seen.append)
            mqtt_client_mock.reconnect.side_effect = ssl.SSLCertVerificationError("hostname mismatch")

            with patch("span_panel_api.mqtt.connection.download_ca_cert", return_value=chain.ca_pem):
                _drive_reconnect_loop(bridge, mqtt_client_mock)
                await asyncio.sleep(0.3)

                assert mqtt_client_mock.reconnect.call_count > 1
                assert bridge.fatal_error is None
                assert bridge._should_reconnect is True
                assert seen == [LeafNameMismatch(host="127.0.0.1", leaf_names=("panel.local",))]

            await bridge.disconnect()

    @pytest.mark.asyncio
    async def test_a_recovered_connection_re_arms_the_report(
        self, mqtt_client_mock: MagicMock, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A second outage after a recovery is a second thing to tell somebody about."""
        monkeypatch.setattr("span_panel_api.mqtt.connection.MQTT_RECONNECT_MIN_DELAY_S", 0.01)
        chain = mint_chain(names=("panel.local",))
        seen: list[LeafNameMismatch] = []

        with tls_server(chain) as (host, port):
            bridge = _bridge(chain, host, port)
            await bridge.connect()
            bridge.set_leaf_mismatch_callback(seen.append)
            mqtt_client_mock.reconnect.side_effect = ssl.SSLCertVerificationError("hostname mismatch")

            with patch("span_panel_api.mqtt.connection.download_ca_cert", return_value=chain.ca_pem):
                _drive_reconnect_loop(bridge, mqtt_client_mock)
                await asyncio.sleep(0.2)
                assert len(seen) == 1

                # The broker comes back on the configured address, then moves again.
                _succeed_connect(bridge)
                _drive_reconnect_loop(bridge, mqtt_client_mock)
                await asyncio.sleep(0.2)

            assert len(seen) == 2
            await bridge.disconnect()


class TestTheClientSurface:
    """`register_leaf_mismatch_callback`, and the wiring that makes it fire."""

    @staticmethod
    def _client() -> SpanMqttClient:
        return SpanMqttClient(
            host="panel.invalid",
            serial_number=SERIAL,
            broker_config=MqttClientConfig(broker_host="broker.local", username="u", password="p"),
        )

    @pytest.mark.asyncio
    async def test_the_bridge_is_wired_to_the_client_fan_out(self) -> None:
        """Proves the subscription exists, rather than a test making it exist.

        `connect()` is run against a stub bridge and allowed to fail afterwards:
        what is under test is the wiring done at construction time, and driving a
        full Homie handshake to reach it would be testing the handshake.
        """
        client = self._client()
        bridge = MagicMock()
        bridge.connect = AsyncMock()

        with (
            patch("span_panel_api.mqtt.client.AsyncMqttBridge", return_value=bridge),
            patch("span_panel_api.mqtt.client.get_homie_schema", AsyncMock(return_value=MOCK_SCHEMA)),
            patch.object(client, "_preload_adapter", AsyncMock()),
            patch.object(client, "_build_adapter", MagicMock()),
            contextlib.suppress(Exception),
        ):
            await client.connect()

        bridge.set_leaf_mismatch_callback.assert_called_once_with(client._on_leaf_mismatch)

    @pytest.mark.asyncio
    async def test_registered_callback_fires_and_unregisters(self) -> None:
        client = self._client()
        seen: list[LeafNameMismatch] = []
        unregister = client.register_leaf_mismatch_callback(seen.append)
        mismatch = LeafNameMismatch(host="10.0.0.9", leaf_names=("panel.local",))

        client._on_leaf_mismatch(mismatch)
        assert seen == [mismatch]

        unregister()
        unregister()  # idempotent
        client._on_leaf_mismatch(mismatch)
        assert seen == [mismatch]

    @pytest.mark.asyncio
    async def test_a_raising_subscriber_does_not_swallow_the_rest(self) -> None:
        client = self._client()
        seen: list[LeafNameMismatch] = []

        def _explode(_mismatch: LeafNameMismatch) -> None:
            raise RuntimeError("subscriber is broken")

        client.register_leaf_mismatch_callback(_explode)
        client.register_leaf_mismatch_callback(seen.append)
        client._on_leaf_mismatch(LeafNameMismatch(host="10.0.0.9", leaf_names=()))

        assert len(seen) == 1

    @pytest.mark.asyncio
    async def test_a_mismatch_is_not_a_fatal_error(self) -> None:
        """The two channels stay separate, because the responses to them differ."""
        client = self._client()
        fatal: list[object] = []
        mismatches: list[LeafNameMismatch] = []
        client.register_fatal_error_callback(fatal.append)
        client.register_leaf_mismatch_callback(mismatches.append)

        client._on_leaf_mismatch(LeafNameMismatch(host="10.0.0.9", leaf_names=("panel.local",)))

        assert fatal == []
        assert len(mismatches) == 1
