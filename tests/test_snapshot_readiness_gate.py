"""Every snapshot passes one readiness gate, on connect and after a bridge rebuild alike.

A bridge rebuild discards the parser and replays the retained tree into a fresh
one. The tree is complete once every device has described itself, but its
labels arrive as separate retained messages, and a broker may replay them after
the last description. An inverter's `connection/feeds-device-id` is one of them:
until it lands, the inverter is keyed by its device id and its feeding circuit
reads as a load. `connect()` always waited those labels out; a rebuild used to
dispatch the moment the tree was complete, so the first snapshot after every
broker drop could move the inverter's key and flip the circuit's sign.

Driven through the real transport entry point, `_on_message`, with the real
parent/child parser, in real-time mode (`snapshot_interval=0`), which has no
debounce to hide behind.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from functools import partial
from unittest.mock import AsyncMock, MagicMock

import pytest

from reference_payloads.schema_one import RetainedTopicTree, parent_child_tree
from span_panel_api.models import SpanPanelSnapshot, V2HomieSchema
from span_panel_api.mqtt import client as client_module
from span_panel_api.mqtt.client import SpanMqttClient
from span_panel_api.mqtt.models import MqttClientConfig
from span_panel_api.protocol import SchemaAdapter
from span_panel_api_schema_1 import SchemaOneAdapter
from span_panel_api_schema_1.adapter import FEED_GRACE_S

from conftest import FAST_CONTROL_DEADLINES

_TREE = parent_child_tree()

PANEL = "example-40t-001"
SOLAR_CIRCUIT = "573066aaddd7b75114c4563ce3af18c4"
FEED = "connection/feeds-device-id"

# Long enough for every task the replay schedules to run, so a dispatch the gate
# should have held back has the chance to happen and be recorded.
_LET_TASKS_RUN_S = 0.05


def _schema() -> V2HomieSchema:
    return V2HomieSchema(
        firmware_version="spanos2/r202633/01",
        types_schema_hash="sha256:test",
        types={},
        data_model_version="1.0",
    )


@pytest.fixture(name="broker")
def _broker(mqtt_client_mock: MagicMock, monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    """The mocked broker, with a panel whose REST schema says what its tree says.

    Layered over `mqtt_client_mock`, which serves the flat schema: a reconnect
    edge would otherwise read that as a generation change and swap the parser.
    """
    monkeypatch.setattr(client_module, "get_homie_schema", AsyncMock(return_value=_schema()))
    return mqtt_client_mock


def _client(clock: Callable[[], float] | None = None) -> SpanMqttClient:
    factory: Callable[[str, V2HomieSchema], SchemaAdapter] = (
        SchemaOneAdapter if clock is None else partial(SchemaOneAdapter, clock=clock)
    )
    return SpanMqttClient(
        host="192.168.1.1",
        serial_number=PANEL,
        broker_config=MqttClientConfig(broker_host="broker.local", username="user", password="pass"),
        snapshot_interval=0,
        adapter_factory=factory,
        data_model_version="1.0",
        schema=_schema(),
        control_deadlines=FAST_CONTROL_DEADLINES,
    )


def _replay_labels(client: SpanMqttClient, tree: RetainedTopicTree, *, hold: tuple[str, ...] = ()) -> None:
    """Every device's description, state and retained values, panel first, except the held topics."""
    for device_id in [PANEL, *[d for d in tree if d != PANEL]]:
        topics = tree[device_id]
        prefix = f"ebus/5/{device_id}"
        client._on_message(f"{prefix}/$description", topics["$description"])
        client._on_message(f"{prefix}/$state", topics["$state"])
        for topic, value in topics.items():
            if not topic.startswith("$") and topic not in hold:
                client._on_message(f"{prefix}/{topic}", value)


def _replay_held(client: SpanMqttClient, tree: RetainedTopicTree, topic: str) -> None:
    for device_id, topics in tree.items():
        if topic in topics:
            client._on_message(f"ebus/5/{device_id}/{topic}", topics[topic])


def _shape(snapshot: SpanPanelSnapshot) -> tuple[frozenset[str], dict[str, str]]:
    """What a consumer keys entities and signs power on."""
    return frozenset(snapshot.pv_inverters), {cid: c.device_type for cid, c in snapshot.circuits.items()}


async def _connected_and_streaming(client: SpanMqttClient) -> list[SpanPanelSnapshot]:
    """Connect on the friendly replay order and start streaming; return the dispatch record."""
    connect = asyncio.create_task(client.connect())
    await asyncio.sleep(_LET_TASKS_RUN_S)
    _replay_labels(client, _TREE)
    await asyncio.wait_for(connect, timeout=5.0)

    dispatched: list[SpanPanelSnapshot] = []

    async def record(snapshot: SpanPanelSnapshot) -> None:
        dispatched.append(snapshot)

    client.register_snapshot_callback(record)
    await client.start_streaming()
    return dispatched


@pytest.mark.asyncio
async def test_a_rebuild_never_dispatches_before_the_inverters_feed_arrives(broker: MagicMock) -> None:
    """The replay delivers every description and name, and only then the feed links.

    No snapshot reaches a consumer keyed or typed from the half-replayed tree, and
    the first one that does matches the panel as it was before the drop.
    """
    client = _client()
    dispatched = await _connected_and_streaming(client)
    before = _shape(await client.get_snapshot())
    assert before[0] == {SOLAR_CIRCUIT}, "precondition: the inverter is keyed by its feeding circuit"
    assert before[1][SOLAR_CIRCUIT] == "pv", "precondition: its feeding circuit is typed pv"

    client._on_pre_rebuild()
    _replay_labels(client, _TREE, hold=(FEED,))
    await asyncio.sleep(_LET_TASKS_RUN_S)

    assert dispatched == [], "a snapshot was dispatched before the feed links arrived"

    _replay_held(client, _TREE, FEED)
    await asyncio.sleep(client_module._CIRCUIT_NAMES_POLL_INTERVAL_S + _LET_TASKS_RUN_S)

    assert dispatched, "the rebuilt tree never dispatched once its labels had arrived"
    assert [_shape(snapshot) for snapshot in dispatched] == [before] * len(dispatched)

    await client.close()


@pytest.mark.asyncio
async def test_a_rebuild_whose_panel_never_sends_the_feed_dispatches_after_the_grace(
    broker: MagicMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A DER may be published with no connection record at all.

    Then no feed is coming, and the gate must give up after the adapter's grace
    rather than hold every snapshot until the name timeout, or for ever.
    """
    monkeypatch.setattr(client_module, "_CIRCUIT_NAMES_POLL_INTERVAL_S", 0.01)
    now = [100.0]
    client = _client(clock=lambda: now[0])
    dispatched = await _connected_and_streaming(client)

    client._on_pre_rebuild()
    _replay_labels(client, _TREE, hold=(FEED,))
    await asyncio.sleep(_LET_TASKS_RUN_S)

    assert dispatched == [], "the feed was not waited on at all"

    now[0] += FEED_GRACE_S
    await asyncio.sleep(_LET_TASKS_RUN_S)

    assert dispatched, "the grace expired and still nothing was dispatched"
    assert "pv" in dispatched[0].pv_inverters, "an unplaced inverter is keyed by its device id"

    await client.close()


@pytest.mark.asyncio
async def test_a_rebuild_with_nothing_missing_dispatches_its_first_snapshot_unprompted(
    broker: MagicMock,
) -> None:
    """Once the replay is over the panel may go quiet, so the gate opening is itself a dispatch.

    Otherwise a consumer would see nothing from the rebuilt tree until the next
    value happened to change.
    """
    client = _client()
    dispatched = await _connected_and_streaming(client)
    before = _shape(await client.get_snapshot())

    client._on_pre_rebuild()
    _replay_labels(client, _TREE)
    await asyncio.sleep(_LET_TASKS_RUN_S)

    assert dispatched, "nothing was dispatched after the replay"
    assert _shape(dispatched[-1]) == before

    await client.close()


@pytest.mark.asyncio
async def test_a_debounce_timer_armed_before_the_rebuild_does_not_dispatch_the_half_replayed_tree(
    broker: MagicMock,
) -> None:
    """The dispatch itself checks the gate, not only the message that armed it.

    With a debounce, the timer a message armed just before the drop fires after
    the rebuild, into whatever the replay has delivered by then.
    """
    client = _client()
    dispatched = await _connected_and_streaming(client)
    client.set_snapshot_interval(60.0)
    client._on_message(f"ebus/5/{PANEL}/power-flows/pv", "1000")
    assert client._snapshot_timer is not None, "precondition: a debounced dispatch is pending"

    client._on_pre_rebuild()
    _replay_labels(client, _TREE, hold=(FEED,))
    client._fire_snapshot()
    await asyncio.sleep(_LET_TASKS_RUN_S)

    assert dispatched == [], "the pending timer dispatched the half-replayed tree"

    await client.close()
