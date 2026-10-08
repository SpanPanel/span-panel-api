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

No test here waits on wall-clock time. One fake clock drives both the adapter's
feed grace and the client's name deadline, the poll interval is zero so the label
wait yields instead of sleeping, and every wait is for an observable condition:
the label wait having looked at the tree, the gate having opened, or the
dispatches it scheduled having finished.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
import gc
import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from reference_payloads.schema_one import RetainedTopicTree, parent_child_tree
from span_panel_api.models import SpanPanelSnapshot, V2HomieSchema
from span_panel_api.mqtt import client as client_module
from span_panel_api.mqtt.client import SpanMqttClient
from span_panel_api.mqtt.models import MqttClientConfig
from span_panel_api_schema_1 import SchemaOneAdapter
from span_panel_api_schema_1.adapter import FEED_GRACE_S

from conftest import FAST_CONTROL_DEADLINES

_TREE = parent_child_tree()

PANEL = "example-40t-001"
SOLAR_CIRCUIT = "573066aaddd7b75114c4563ce3af18c4"
FEED = "connection/feeds-device-id"
NAME = "info/name"

# A bound on how long a condition may take to become true, never a delay any test
# relies on: every wait below returns the moment its condition holds.
_CONDITION_BOUND_S = 5.0


class _Clock:
    """The one clock both the adapter's feed grace and the client's name deadline read."""

    def __init__(self) -> None:
        self.now = 100.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


@dataclass
class _Probe:
    """How often the label wait has asked the adapter, and how many of those asks fail.

    `failures` counts down; a negative value fails every ask.
    """

    polls: int = 0
    failures: int = 0


def _schema() -> V2HomieSchema:
    return V2HomieSchema(
        firmware_version="spanos2/r202633/01",
        types_schema_hash="sha256:test",
        types={},
        data_model_version="1.0",
    )


@pytest.fixture(name="clock")
def _clock_fixture(monkeypatch: pytest.MonkeyPatch) -> _Clock:
    clock = _Clock()
    monkeypatch.setattr(client_module, "time", SimpleNamespace(monotonic=clock))
    monkeypatch.setattr(client_module, "_CIRCUIT_NAMES_POLL_INTERVAL_S", 0)
    return clock


@pytest.fixture(name="broker")
def _broker(mqtt_client_mock: MagicMock, monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    """The mocked broker, with a panel whose REST schema says what its tree says.

    Layered over `mqtt_client_mock`, which serves the flat schema: a reconnect
    edge would otherwise read that as a generation change and swap the parser.
    """
    monkeypatch.setattr(client_module, "get_homie_schema", AsyncMock(return_value=_schema()))
    return mqtt_client_mock


def _client(clock: _Clock, probe: _Probe | None = None) -> SpanMqttClient:
    probe = probe if probe is not None else _Probe()

    class _Probed(SchemaOneAdapter):
        def circuit_nodes_missing_names(self) -> list[str]:
            probe.polls += 1
            if probe.failures:
                probe.failures -= 1 if probe.failures > 0 else 0
                raise RuntimeError("cannot read the tree")
            return super().circuit_nodes_missing_names()

    def factory(serial_number: str, schema: V2HomieSchema) -> SchemaOneAdapter:
        return _Probed(serial_number, schema, clock=clock)

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


def _names(snapshot: SpanPanelSnapshot) -> dict[str, str]:
    """What a consumer derives entity ids from on a first install."""
    return {cid: c.name for cid, c in snapshot.circuits.items()}


async def _until(condition: Callable[[], bool]) -> None:
    """Yield to the loop until `condition` holds."""
    async with asyncio.timeout(_CONDITION_BOUND_S):
        while not condition():
            await asyncio.sleep(0)


def _gate_open(client: SpanMqttClient) -> bool:
    adapter = client._adapter
    return adapter is not None and client._snapshot_ready(adapter)


async def _quiesce(client: SpanMqttClient) -> None:
    """Every dispatch scheduled so far has run. Label waits, current or replaced, may still be pending."""
    await _until(lambda: not any(task.get_name() == "span_mqtt_dispatch_snapshot" for task in client._background_tasks))


async def _after_one_look(client: SpanMqttClient, probe: _Probe) -> None:
    """The label wait has judged the tree as it now stands, and what that scheduled has run.

    Either it asked the adapter again, or the gate is already open. A dispatch
    the gate should have held back has then had every chance to happen.
    """
    start = probe.polls
    await _until(lambda: probe.polls > start or _gate_open(client))
    await _quiesce(client)


async def _gate_opened(client: SpanMqttClient) -> None:
    await _until(lambda: _gate_open(client))
    await _quiesce(client)


async def _connected_and_streaming(client: SpanMqttClient) -> list[SpanPanelSnapshot]:
    """Connect on the friendly replay order and start streaming; return the dispatch record."""
    connect = asyncio.create_task(client.connect())
    await _until(lambda: client._bridge is not None and client._bridge.is_connected())
    _replay_labels(client, _TREE)
    await asyncio.wait_for(connect, timeout=_CONDITION_BOUND_S)

    dispatched: list[SpanPanelSnapshot] = []

    async def record(snapshot: SpanPanelSnapshot) -> None:
        dispatched.append(snapshot)

    client.register_snapshot_callback(record)
    await client.start_streaming()
    return dispatched


def _logged(caplog: pytest.LogCaptureFixture, level: int) -> list[logging.LogRecord]:
    return [r for r in caplog.records if r.name == "span_panel_api.mqtt.client" and r.levelno == level]


def _timeouts(caplog: pytest.LogCaptureFixture) -> list[int]:
    return [r.levelno for r in caplog.records if r.getMessage().startswith("Timed out waiting for circuit names")]


# ---------------------------------------------------------------------------
# A rebuild dispatches only once its labels have settled
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_rebuild_never_dispatches_before_the_inverters_feed_arrives(broker: MagicMock, clock: _Clock) -> None:
    """The replay delivers every description and name, and only then the feed links.

    No snapshot reaches a consumer keyed or typed from the half-replayed tree, and
    the first one that does matches the panel as it was before the drop.
    """
    probe = _Probe()
    client = _client(clock, probe)
    dispatched = await _connected_and_streaming(client)
    before = _shape(await client.get_snapshot())
    assert before[0] == {SOLAR_CIRCUIT}, "precondition: the inverter is keyed by its feeding circuit"
    assert before[1][SOLAR_CIRCUIT] == "pv", "precondition: its feeding circuit is typed pv"

    client._on_pre_rebuild()
    _replay_labels(client, _TREE, hold=(FEED,))
    await _after_one_look(client, probe)

    assert dispatched == [], "a snapshot was dispatched before the feed links arrived"

    _replay_held(client, _TREE, FEED)
    await _gate_opened(client)

    assert dispatched, "the rebuilt tree never dispatched once its labels had arrived"
    assert [_shape(snapshot) for snapshot in dispatched] == [before] * len(dispatched)

    await client.close()


@pytest.mark.asyncio
async def test_a_rebuild_whose_panel_never_sends_the_feed_dispatches_after_the_grace(
    broker: MagicMock, clock: _Clock
) -> None:
    """A DER may be published with no connection record at all.

    Then no feed is coming, and the gate must give up after the adapter's grace
    rather than hold every snapshot until the name timeout, or for ever.
    """
    probe = _Probe()
    client = _client(clock, probe)
    dispatched = await _connected_and_streaming(client)

    client._on_pre_rebuild()
    _replay_labels(client, _TREE, hold=(FEED,))
    await _after_one_look(client, probe)

    assert dispatched == [], "the feed was not waited on at all"

    clock.advance(FEED_GRACE_S)
    await _gate_opened(client)

    assert dispatched, "the grace expired and still nothing was dispatched"
    assert "pv" in dispatched[0].pv_inverters, "an unplaced inverter is keyed by its device id"

    await client.close()


@pytest.mark.asyncio
async def test_a_rebuild_with_nothing_missing_dispatches_its_first_snapshot_unprompted(
    broker: MagicMock, clock: _Clock
) -> None:
    """Once the replay is over the panel may go quiet, so the gate opening is itself a dispatch.

    Otherwise a consumer would see nothing from the rebuilt tree until the next
    value happened to change.
    """
    client = _client(clock)
    dispatched = await _connected_and_streaming(client)
    before = _shape(await client.get_snapshot())

    client._on_pre_rebuild()
    _replay_labels(client, _TREE)
    await _gate_opened(client)

    assert dispatched, "nothing was dispatched after the replay"
    assert _shape(dispatched[-1]) == before

    await client.close()


@pytest.mark.asyncio
async def test_a_debounce_timer_armed_before_the_rebuild_does_not_dispatch_the_half_replayed_tree(
    broker: MagicMock, clock: _Clock
) -> None:
    """The dispatch itself checks the gate, not only the message that armed it.

    With a debounce, the timer a message armed just before the drop fires after
    the rebuild, into whatever the replay has delivered by then.
    """
    probe = _Probe()
    client = _client(clock, probe)
    dispatched = await _connected_and_streaming(client)
    client.set_snapshot_interval(60.0)
    client._on_message(f"ebus/5/{PANEL}/power-flows/pv", "1000")
    assert client._snapshot_timer is not None, "precondition: a debounced dispatch is pending"

    client._on_pre_rebuild()
    _replay_labels(client, _TREE, hold=(FEED,))
    client._fire_snapshot()
    await _after_one_look(client, probe)

    assert dispatched == [], "the pending timer dispatched the half-replayed tree"

    await client.close()


# ---------------------------------------------------------------------------
# The gate always opens, exactly once, for the tree that is current
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_slow_settle_of_a_replaced_tree_never_closes_the_live_trees_gate(broker: MagicMock, clock: _Clock) -> None:
    """Two rebuilds in a row: the first tree is still waiting when the second settles.

    The first tree's wait ends later, on its feed grace. It must leave the gate
    as the second tree set it rather than claim it for a parser nothing feeds
    any more, which would hold every snapshot until the next rebuild.
    """
    probe = _Probe()
    client = _client(clock, probe)
    dispatched = await _connected_and_streaming(client)
    before = _shape(await client.get_snapshot())

    client._on_pre_rebuild()
    _replay_labels(client, _TREE, hold=(FEED,))
    await _after_one_look(client, probe)
    replaced = client._settle_task
    assert replaced is not None and not replaced.done(), "precondition: the first tree is still settling"

    client._on_pre_rebuild()
    _replay_labels(client, _TREE)
    await _gate_opened(client)
    assert dispatched, "precondition: the second tree settled and dispatched"

    clock.advance(FEED_GRACE_S)
    await _until(replaced.done)
    dispatched.clear()

    assert _shape(await client.get_snapshot()) == before
    client._on_message(f"ebus/5/{PANEL}/power-flows/pv", "1000")
    await _quiesce(client)
    assert dispatched, "the live tree's gate was closed by the replaced tree's late settle"

    await client.close()


@pytest.mark.asyncio
async def test_the_name_timeout_opens_the_gate_after_a_rebuild_and_says_so_only_at_debug(
    broker: MagicMock, clock: _Clock, caplog: pytest.LogCaptureFixture
) -> None:
    """A label that never arrives costs one bounded wait, then the tree is served as it is.

    Only connect's wait warns. A panel with a circuit that is never named would
    otherwise log the same warning on every broker reconnect.
    """
    probe = _Probe()
    client = _client(clock, probe)
    dispatched = await _connected_and_streaming(client)

    client._on_pre_rebuild()
    with caplog.at_level(logging.DEBUG, logger="span_panel_api.mqtt.client"):
        _replay_labels(client, _TREE, hold=(NAME,))
        await _after_one_look(client, probe)
        assert dispatched == [], "the missing names were not waited on"

        clock.advance(client_module._CIRCUIT_NAMES_TIMEOUT_S)
        await _gate_opened(client)

    assert dispatched, "the name timeout expired and the gate stayed closed"
    assert _timeouts(caplog) == [logging.DEBUG]

    await client.close()


@pytest.mark.asyncio
async def test_the_name_timeout_on_connect_still_warns(
    broker: MagicMock, clock: _Clock, caplog: pytest.LogCaptureFixture
) -> None:
    """The first wait of the client's life is the one a user can act on: name the circuit."""
    probe = _Probe()
    client = _client(clock, probe)

    with caplog.at_level(logging.DEBUG, logger="span_panel_api.mqtt.client"):
        connect = asyncio.create_task(client.connect())
        await _until(lambda: client._bridge is not None and client._bridge.is_connected())
        _replay_labels(client, _TREE, hold=(NAME,))
        await _until(lambda: probe.polls > 0)
        clock.advance(client_module._CIRCUIT_NAMES_TIMEOUT_S)
        await asyncio.wait_for(connect, timeout=_CONDITION_BOUND_S)

    assert _timeouts(caplog) == [logging.WARNING]

    await client.close()


@pytest.mark.asyncio
async def test_close_during_a_settle_cancels_it_and_nothing_dispatches_afterwards(broker: MagicMock, clock: _Clock) -> None:
    """Unload must not leave a wait running that later dispatches into a torn-down consumer."""
    probe = _Probe()
    client = _client(clock, probe)
    dispatched = await _connected_and_streaming(client)
    unobserved: list[dict[str, object]] = []
    asyncio.get_running_loop().set_exception_handler(lambda _loop, context: unobserved.append(context))

    client._on_pre_rebuild()
    _replay_labels(client, _TREE, hold=(FEED,))
    await _after_one_look(client, probe)
    settle = client._settle_task
    assert settle is not None and not settle.done(), "precondition: the rebuilt tree is still settling"

    await client.close()
    clock.advance(FEED_GRACE_S)
    await _until(settle.done)
    del settle
    gc.collect()

    assert client._settle_task is not None and client._settle_task.cancelled()
    assert dispatched == []
    assert unobserved == []


# ---------------------------------------------------------------------------
# A parser that cannot answer: waited out, reported once, never fatal
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_poll_that_raises_once_is_waited_past_until_the_names_are_complete(
    broker: MagicMock, clock: _Clock, caplog: pytest.LogCaptureFixture
) -> None:
    """A transient fault must not open the gate early with whatever names have arrived.

    On a first install a consumer derives permanent entity ids from those names,
    so an early opening would fix a placeholder in place for good.
    """
    probe = _Probe()
    client = _client(clock, probe)
    dispatched = await _connected_and_streaming(client)
    before = _names(await client.get_snapshot())

    probe.failures = 1
    client._on_pre_rebuild()
    with caplog.at_level(logging.DEBUG, logger="span_panel_api.mqtt.client"):
        _replay_labels(client, _TREE, hold=(NAME,))
        await _after_one_look(client, probe)
        await _after_one_look(client, probe)
        assert probe.failures == 0, "precondition: the fault was hit"
        assert dispatched == [], "the gate opened on the fault, before the names arrived"

        _replay_held(client, _TREE, NAME)
        await _gate_opened(client)

    assert dispatched, "the names arrived and the gate stayed closed"
    assert [_names(snapshot) for snapshot in dispatched] == [before] * len(dispatched)
    assert len(_logged(caplog, logging.ERROR)) == 1

    await client.close()


@pytest.mark.asyncio
async def test_a_poll_that_always_raises_opens_the_gate_at_the_deadline_with_one_error(
    broker: MagicMock, clock: _Clock, caplog: pytest.LogCaptureFixture
) -> None:
    """A deterministic fault costs the same bounded wait as a label that never arrives.

    It is reported at ERROR, once, with the exception, then the tree is served as
    it is: holding every snapshot for the tree's whole life would report the
    panel offline until the next rebuild.
    """
    probe = _Probe()
    client = _client(clock, probe)
    dispatched = await _connected_and_streaming(client)
    before = _shape(await client.get_snapshot())

    probe.failures = -1
    client._on_pre_rebuild()
    with caplog.at_level(logging.DEBUG, logger="span_panel_api.mqtt.client"):
        _replay_labels(client, _TREE)
        await _after_one_look(client, probe)
        await _after_one_look(client, probe)
        assert dispatched == [], "the gate opened on the fault rather than at the deadline"

        clock.advance(client_module._CIRCUIT_NAMES_TIMEOUT_S)
        await _gate_opened(client)

    assert dispatched, "the deadline passed and the gate stayed closed"
    assert _shape(dispatched[-1]) == before
    errors = _logged(caplog, logging.ERROR)
    assert len(errors) == 1
    assert errors[0].exc_info is not None and isinstance(errors[0].exc_info[1], RuntimeError)

    await client.close()


@pytest.mark.asyncio
async def test_a_label_wait_that_fails_outright_still_opens_the_gate_and_reports_it_at_error(
    broker: MagicMock, clock: _Clock, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The settle's own guard, behind the per-poll one: nothing awaits a rebuild's settle.

    An exception escaping the wait itself would otherwise close the gate for the
    tree's whole life without a word.
    """
    client = _client(clock)
    dispatched = await _connected_and_streaming(client)
    monkeypatch.setattr(client, "_wait_for_circuit_names", AsyncMock(side_effect=RuntimeError("wait failed")))

    client._on_pre_rebuild()
    with caplog.at_level(logging.DEBUG, logger="span_panel_api.mqtt.client"):
        _replay_labels(client, _TREE)
        await _gate_opened(client)

    assert dispatched, "the failed wait left the gate closed"
    errors = _logged(caplog, logging.ERROR)
    assert len(errors) == 1
    assert errors[0].exc_info is not None and isinstance(errors[0].exc_info[1], RuntimeError)

    await client.close()
