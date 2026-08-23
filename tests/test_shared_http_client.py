"""The runtime path uses the caller's HTTP client, not one of its own.

Four config-flow-facing entry points already take an injected
`httpx.AsyncClient`; `SpanMqttClient` was the one runtime entry point without
it, so every schema fetch built a throwaway client -- including the retry loop
that runs during a firmware upgrade, which builds one per attempt at exactly the
moment the panel is mid-reboot.

Home Assistant is the caller that cares. It owns a shared client, closes it at
shutdown, and the integration's own `quality_scale.yaml` claims
`inject-websession: done` -- a claim that was true of the config flow and not of
anything that ran afterwards.

**Ownership is the whole contract.** A client handed in is never closed here, and
its policy is the caller's: timeouts, limits and headers are whatever the caller
configured, which is why the per-call `timeout` arguments are documented as
ignored when a client is injected. Home Assistant's shared client carries
httpx's default timeout rather than this library's, and that is the caller
exercising the policy it owns rather than a setting being lost.
"""

from __future__ import annotations

import asyncio
import contextlib
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from span_panel_api.mqtt import MqttClientConfig
from span_panel_api.mqtt.client import SpanMqttClient

SERIAL = "sp3-242424-001"


class _Schema:
    def __init__(self, version: str | None) -> None:
        self.data_model_version = version


def _client(injected: object | None) -> SpanMqttClient:
    return SpanMqttClient(
        host="192.168.1.1",
        serial_number=SERIAL,
        broker_config=MqttClientConfig(broker_host="broker.local", username="u", password="p"),
        httpx_client=injected,  # type: ignore[arg-type]
    )


@pytest.mark.asyncio
async def test_the_connect_fetch_uses_the_injected_client() -> None:
    """The first schema read of a session, and the one every install makes."""
    sentinel = MagicMock(name="shared-client")
    client = _client(sentinel)

    fetch = AsyncMock(return_value=_Schema("1.0"))
    with (
        patch("span_panel_api.mqtt.client.get_homie_schema", fetch),
        patch.object(client, "_preload_adapter", AsyncMock()),
        patch.object(client, "_build_adapter", MagicMock()),
        patch.object(client, "_connect_bridge", AsyncMock(), create=True),
    ):
        # Connect goes on to bring up the MQTT bridge, which has nothing to do
        # with this assertion and no broker to reach. The fetch is what is under
        # test, and the await-count assertion below is what keeps that from
        # passing vacuously if it never happened at all.
        with contextlib.suppress(Exception):
            await client.connect()

    assert fetch.await_count >= 1
    assert fetch.await_args.kwargs["httpx_client"] is sentinel


@pytest.mark.asyncio
async def test_the_upgrade_refetch_uses_the_injected_client() -> None:
    """The path that mattered most, because it builds one client per retry attempt.

    A panel accepts MQTT before it serves HTTP, so this loop can run several times
    in a row while the panel finishes booting -- each one previously a fresh
    client, a fresh connection pool, thrown away on the next attempt.
    """
    sentinel = MagicMock(name="shared-client")
    client = _client(sentinel)
    client._loop = asyncio.get_running_loop()

    fetch = AsyncMock(return_value=_Schema("1.0"))
    with patch("span_panel_api.mqtt.client.get_homie_schema", fetch):
        assert await client._fetch_schema_with_retry() is not None

    assert fetch.await_args.kwargs["httpx_client"] is sentinel


@pytest.mark.asyncio
async def test_an_injected_client_is_never_closed_here() -> None:
    """It belongs to the caller, and the caller may still be using it.

    Home Assistant hands out one shared client to every integration and closes it
    at shutdown; closing it from here would take the others down with it. HA
    guards its own copy, but a library that relies on the caller guarding it is
    relying on the caller.
    """
    sentinel = MagicMock(name="shared-client")
    sentinel.aclose = AsyncMock()
    client = _client(sentinel)

    await client.close()

    sentinel.aclose.assert_not_awaited()


@pytest.mark.asyncio
async def test_no_injected_client_still_works() -> None:
    """The default has to stay the default: this library is not Home Assistant's alone."""
    client = _client(None)
    client._loop = asyncio.get_running_loop()

    fetch = AsyncMock(return_value=_Schema("1.0"))
    with patch("span_panel_api.mqtt.client.get_homie_schema", fetch):
        assert await client._fetch_schema_with_retry() is not None

    assert fetch.await_args.kwargs["httpx_client"] is None
