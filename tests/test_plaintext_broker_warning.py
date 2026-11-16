"""A broker connection without TLS says so, once per bridge.

`use_tls=False` puts the whole MQTT session on plaintext: the CONNECT packet
carries the broker username and password, and every reading and every command
after it crosses the network readable by anything on the path. The bridge's
only other warning is about its trust anchor, and a bridge without TLS resolves
none, so this posture used to log nothing at all.

Scoped as the unpinned-CA warning is, to the bridge: a bridge outlives every
reconnect, so the line is said once per bridge rather than once per connect.
"""

from __future__ import annotations

import logging
from unittest.mock import MagicMock

import pytest

from span_panel_api.mqtt.connection import AsyncMqttBridge

from conftest import SERIAL

PLAINTEXT_LINE = "is not encrypted: use_tls is False"
PASSWORD = "correct-horse-battery-staple"


def _bridge(*, use_tls: bool) -> AsyncMqttBridge:
    return AsyncMqttBridge(
        host="broker.local",
        port=9001,
        username="user",
        password=PASSWORD,
        panel_host="panel.invalid",
        serial_number=SERIAL,
        transport="websockets",
        use_tls=use_tls,
        # Pinned, so a TLS bridge says nothing about its anchor either and the
        # only line in question is this one. The fixture replaces the context
        # builder, so the PEM is never parsed.
        ca_pem="pinned-pem" if use_tls else None,
    )


def _plaintext_lines(caplog: pytest.LogCaptureFixture) -> list[logging.LogRecord]:
    return [record for record in caplog.records if PLAINTEXT_LINE in record.getMessage()]


class TestAPlaintextBrokerSaysSo:
    @pytest.mark.asyncio
    async def test_it_warns_once_across_reconnects(
        self, mqtt_client_mock: MagicMock, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Two rebuilds and a second connect on the same bridge: one line for all of it."""
        bridge = _bridge(use_tls=False)
        with caplog.at_level(logging.WARNING, logger="span_panel_api.mqtt.connection"):
            await bridge.connect()
            assert await bridge._rebuild_client() is True
            assert await bridge._rebuild_client() is True
            await bridge.disconnect()
            await bridge.connect()

        lines = _plaintext_lines(caplog)
        assert len(lines) == 1
        assert lines[0].levelno == logging.WARNING
        message = lines[0].getMessage()
        assert "broker.local:9001" in message
        assert "username and password" in message
        assert "plaintext" in message
        # A warning about a credential, not a place to put one.
        assert PASSWORD not in caplog.text

        await bridge.disconnect()

    @pytest.mark.asyncio
    async def test_a_new_bridge_says_it_again(self, mqtt_client_mock: MagicMock, caplog: pytest.LogCaptureFixture) -> None:
        """Per bridge, not per process: a reload builds a new bridge, as the unpinned-CA warning's does."""
        first = _bridge(use_tls=False)
        second = _bridge(use_tls=False)
        with caplog.at_level(logging.WARNING, logger="span_panel_api.mqtt.connection"):
            await first.connect()
            await second.connect()

        assert len(_plaintext_lines(caplog)) == 2

        await first.disconnect()
        await second.disconnect()

    @pytest.mark.asyncio
    async def test_a_tls_bridge_never_says_it(self, mqtt_client_mock: MagicMock, caplog: pytest.LogCaptureFixture) -> None:
        bridge = _bridge(use_tls=True)
        with caplog.at_level(logging.WARNING, logger="span_panel_api.mqtt.connection"):
            await bridge.connect()
            assert await bridge._rebuild_client() is True

        assert _plaintext_lines(caplog) == []

        await bridge.disconnect()
