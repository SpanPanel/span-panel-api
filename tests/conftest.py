"""Shared pytest configuration and fixtures for SPAN Panel API tests."""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from collections.abc import AsyncGenerator
from unittest.mock import MagicMock, patch

import paho.mqtt.client as paho
import pytest
from paho.mqtt.client import ConnectFlags
from paho.mqtt.reasoncodes import ReasonCode

import span_panel_api._http as _http_mod
from span_panel_api.models import V2HomieSchema
from span_panel_api_schema_0.const import TOPIC_PREFIX, TYPE_CORE

_DOTENV = Path(__file__).parent.parent / ".env"


def _load_dotenv() -> None:
    """Populate the environment from `.env`, without overriding what is set.

    Read directly rather than through python-dotenv: this supplies developer
    defaults for the optional provenance checks (`EBUS_SPEC_DIR`,
    `PANELBENCH_DIR`), and taking a dependency to parse two lines would put a
    package in the test path to save nothing.

    `setdefault`, never assignment. An exported value is a deliberate choice for
    this run — pointing at a different checkout to reproduce something — and a
    file silently winning over it is the kind of surprise that costs an
    afternoon. See `.env.example`; absence is fine, the checks skip.
    """
    if not _DOTENV.exists():
        return
    for raw in _DOTENV.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


_load_dotenv()


@pytest.fixture(autouse=True)
def _reset_ssl_cache() -> None:
    """Ensure the module-level SSL context cache doesn't leak between tests."""
    _http_mod._ssl_cache.context = None
    _http_mod._ssl_cache.lock = None


# ---------------------------------------------------------------------------
# Constants shared across MQTT tests
# ---------------------------------------------------------------------------

SERIAL = "nj-2316-XXXX"
TOPIC_PREFIX_SERIAL = f"{TOPIC_PREFIX}/{SERIAL}"

# Minimal Homie description that makes the device "ready"
MINIMAL_DESCRIPTION = json.dumps({"nodes": {"core": {"type": TYPE_CORE}}})


def flat_schema(panel_size: int = 32) -> V2HomieSchema:
    """A flat-schema REST response declaring ``panel_size`` breaker spaces.

    No ``data_model_version``: absence is exactly what marks a payload as flat,
    so this is what dispatch reads to select schema_0.
    """
    return V2HomieSchema(
        firmware_version="test",
        types_schema_hash="sha256:test",
        types={
            "energy.ebus.device.circuit": {
                "space": {"datatype": "integer", "format": f"1:{panel_size}:1"},
            },
        },
    )


def parent_child_schema(data_model_version: str = "1.0") -> V2HomieSchema:
    """A parent/child REST response, as r202633+ firmware serves it.

    ``types`` is empty because that firmware keeps its definitions under
    ``deviceClasses`` — which is exactly why the version has to be read before
    anything tries to parse the payload.
    """
    return V2HomieSchema(
        firmware_version="spanos2/r202633/01",
        types_schema_hash="sha256:test",
        types={},
        data_model_version=data_model_version,
    )


# Mock schema for SpanMqttClient.connect() — panel_size=32, flat.
MOCK_SCHEMA = flat_schema(32)


# ---------------------------------------------------------------------------
# Mock MQTT client fixture
# ---------------------------------------------------------------------------


def _make_fake_sock() -> MagicMock:
    """Create a fake socket with fileno=-1 (skips add_reader/add_writer)."""
    sock = MagicMock()
    sock.fileno.return_value = -1
    return sock


@pytest.fixture
async def mqtt_client_mock() -> AsyncGenerator[MagicMock, None]:
    """Patch AsyncMQTTClient to return a MagicMock that simulates paho.

    The mock wires up ``connect()`` to trigger the bridge's ``on_connect``
    and ``on_socket_open`` callbacks, exactly matching HA core's mock pattern.

    Yields the mock client instance (``cls.return_value``).
    """
    loop = asyncio.get_running_loop()
    fake_sock = _make_fake_sock()

    def _connect(
        host: str = "",
        port: int = 0,
        keepalive: int = 60,
        **_kwargs: object,
    ) -> int:
        """Simulate paho connect — fire socket + CONNACK callbacks."""
        # Socket open goes through the sync bridge → call_soon_threadsafe
        mock_client.on_socket_open(mock_client, None, fake_sock)
        mock_client.on_socket_register_write(mock_client, None, fake_sock)
        # Schedule on_connect on event loop (we're in executor thread)
        loop.call_soon_threadsafe(
            mock_client.on_connect,
            mock_client,
            None,
            ConnectFlags(session_present=0),
            ReasonCode(packetType=2, aName="Success"),
            None,
        )
        return 0

    def _reconnect() -> int:
        """Simulate paho reconnect."""
        mock_client.on_socket_open(mock_client, None, fake_sock)
        mock_client.on_socket_register_write(mock_client, None, fake_sock)
        loop.call_soon_threadsafe(
            mock_client.on_connect,
            mock_client,
            None,
            ConnectFlags(session_present=0),
            ReasonCode(packetType=2, aName="Success"),
            None,
        )
        return 0

    with (
        patch("span_panel_api.mqtt.connection.AsyncMQTTClient") as cls,
        patch("span_panel_api.mqtt.connection.download_ca_cert", return_value="FAKE-PEM"),
        patch("span_panel_api.mqtt.connection.build_panel_ssl_context", return_value=MagicMock()),
        patch("span_panel_api.mqtt.client.get_homie_schema", return_value=MOCK_SCHEMA),
        patch("span_panel_api.factory.get_homie_schema", return_value=MOCK_SCHEMA),
    ):
        mock_client = cls.return_value
        mock_client.connect.side_effect = _connect
        mock_client.reconnect.side_effect = _reconnect
        mock_client.subscribe.return_value = (0, 1)
        mock_client.publish.return_value = MagicMock(rc=0, mid=1)
        mock_client.disconnect.return_value = 0
        mock_client.loop_read.return_value = 0
        mock_client.loop_write.return_value = 0
        mock_client.loop_misc.return_value = paho.MQTT_ERR_SUCCESS

        yield mock_client
