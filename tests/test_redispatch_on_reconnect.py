"""A panel that comes back as a different generation gets a different parser.

The adapter is chosen once, at connect, from the REST `dataModelVersion`. Everything
afterwards reused it: `connect()` short-circuits on the cached schema, the reconnect
path re-subscribes with the existing adapter's topics, and the pre-rebuild hook
rebuilds from the cached schema on the stated assumption that "the Homie schema
cannot change within a session".

A firmware upgrade is that assumption failing. The panel disconnects and returns as a
different generation while the session is still open, so nothing reconsiders. Seen
live: a flat panel upgraded to v1.0 underneath a running Home Assistant, the client
reconnected, kept the flat parser, and read the v1.0 tree with it. It logged a single
`Invalid $description JSON` and then reported every circuit as missing -- a wrong
answer rather than an error, which is the failure mode worth testing for.

**The trigger is the MQTT property, not the reconnect edge.** Triggering on reconnect
was the first attempt and it does not work: the edge fires the moment the broker
accepts a connection, which on a real upgrade precedes the panel binding its HTTP
port. It failed with `Cannot reach panel` 25ms after reconnect, and since MQTT had
reconnected successfully there was no later edge to retry on -- the wrong parser
stayed for the rest of the session. The retained `info/data-model-version` arrives
only once the new panel is publishing, so it is the first moment the answer exists.
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import patch

import pytest

from span_panel_api.exceptions import SpanPanelConnectionError, SpanPanelServerError
from span_panel_api.mqtt.client import (
    _REDISPATCH_RETRY_ATTEMPTS,
    _REDISPATCH_RETRY_INITIAL_S,
    _REDISPATCH_RETRY_MAX_S,
    SpanMqttClient,
)
from span_panel_api.mqtt.models import MqttClientConfig

from conftest import SERIAL


class _Schema:
    """The slice of `V2HomieSchema` the dispatch path reads."""

    def __init__(self, version: str | None) -> None:
        self.data_model_version = version
        self.types: dict[str, Any] = {}
        self.types_schema_hash = f"sha256:{version}"


class _Adapter:
    """Records which schema it was built from, so a swap is observable."""

    def __init__(self, serial: str, schema: _Schema) -> None:
        self.serial = serial
        self.schema = schema
        self.schema_major = f"schema_for_{schema.data_model_version}"

    def topics_to_subscribe(self) -> list[str]:
        return [f"topics/for/{self.schema.data_model_version}"]

    def build_field_metadata(self) -> dict[str, Any]:
        return {}

    def is_ready(self) -> bool:
        return False

    def handle_message(self, topic: str, payload: str) -> None:
        return None


class _Bridge:
    def __init__(self) -> None:
        self.subscribed: list[str] = []

    def subscribe(self, topic: str, qos: int = 0) -> None:
        self.subscribed.append(topic)


def _client(initial: str | None) -> tuple[SpanMqttClient, _Bridge]:
    client = SpanMqttClient(
        host="192.168.1.1",
        serial_number=SERIAL,
        broker_config=MqttClientConfig(broker_host="broker.local", username="u", password="p"),
        adapter_factory=_Adapter,  # type: ignore[arg-type]
        data_model_version=initial,
    )
    bridge = _Bridge()
    client._bridge = bridge  # type: ignore[assignment]
    client._adapter = _Adapter(SERIAL, _Schema(initial))  # type: ignore[assignment]
    client._loop = asyncio.get_running_loop()
    return client, bridge


async def _panel_publishes_version(client: SpanMqttClient, version: str | None) -> None:
    """Deliver the retained `info/data-model-version` the way the broker would."""
    client._on_message(f"ebus/5/{SERIAL}/info/data-model-version", version or "")
    # The refetch is scheduled rather than awaited, so the message callback can stay
    # synchronous. Let the loop drain it.
    # One turn per retry attempt, plus slack for the task itself.
    for _ in range(_REDISPATCH_RETRY_ATTEMPTS + 4):
        await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_a_generation_change_rebuilds_the_parser() -> None:
    """The upgrade case: a flat panel starts publishing v1.0.

    Asserting the adapter *instance* changed and carries the new version, rather than
    a log line -- the parser is what reads the tree, so it is the thing that has to
    move.
    """
    client, _ = _client(None)
    before = client.adapter

    with patch("span_panel_api.mqtt.client.get_homie_schema", return_value=_Schema("1.0")):
        await _panel_publishes_version(client, "1.0")

    assert client.adapter is not before, "the parser must be rebuilt, not reused"
    assert client.data_model_version == "1.0"


@pytest.mark.asyncio
async def test_the_new_parsers_topics_are_subscribed() -> None:
    """A new parser reading old topics would be a quieter version of the same bug.

    The two generations do not share a topic shape, so a rebuilt adapter that never
    subscribes to its own topics receives nothing and reports an empty panel -- which
    looks like a panel that has gone away rather than one that was mis-read.
    """
    client, bridge = _client(None)

    with patch("span_panel_api.mqtt.client.get_homie_schema", return_value=_Schema("1.0")):
        await _panel_publishes_version(client, "1.0")

    assert "topics/for/1.0" in bridge.subscribed


@pytest.mark.asyncio
async def test_an_unchanged_generation_never_fetches() -> None:
    """Steady state must cost nothing.

    The panel republishes this property on every reconnect and on every retained
    replay. Rebuilding — or even fetching — each time would discard accumulated tree
    state and put avoidable load on the panel, so agreement is answered by comparison
    alone, before anything is scheduled.
    """
    client, _ = _client("1.0")
    before = client.adapter
    calls = 0

    def _count(*_a: object, **_k: object) -> _Schema:
        nonlocal calls
        calls += 1
        return _Schema("1.0")

    with patch("span_panel_api.mqtt.client.get_homie_schema", side_effect=_count):
        await _panel_publishes_version(client, "1.0")
        await _panel_publishes_version(client, "1.0")
        await _panel_publishes_version(client, "1.0")

    assert client.adapter is before
    assert calls == 0, f"a matching generation must not be fetched, got {calls} fetches"


@pytest.mark.asyncio
async def test_a_patch_release_is_not_a_generation_change() -> None:
    """`1.0` and `1.0.3` are read by the same parser.

    Comparing reported strings instead of the adapters they select would rebuild on
    any patch release -- a pointless swap that drops tree state on a routine bump.
    """
    client, _ = _client("1.0")
    before = client.adapter

    with patch("span_panel_api.mqtt.client.get_homie_schema", return_value=_Schema("1.0.3")):
        await _panel_publishes_version(client, "1.0.3")

    assert client.adapter is before


@pytest.mark.asyncio
async def test_http_lagging_the_broker_is_retried_not_abandoned() -> None:
    """The failure that made the first attempt useless.

    A panel accepts MQTT before it serves HTTP: the broker is listening while the
    application is still binding its port. The first fetch therefore fails, and
    because MQTT reconnected *successfully* there is no later edge to retry on. One
    attempt means the wrong parser stays for the rest of the session, which is exactly
    what was observed live.
    """
    client, _ = _client(None)
    before = client.adapter
    attempts = 0

    def _lags_then_answers(*_a: object, **_k: object) -> _Schema:
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise SpanPanelConnectionError("Cannot reach panel")
        return _Schema("1.0")

    with (
        patch("span_panel_api.mqtt.client.get_homie_schema", side_effect=_lags_then_answers),
        patch("span_panel_api.mqtt.client._REDISPATCH_RETRY_INITIAL_S", 0),
        patch("span_panel_api.mqtt.client._REDISPATCH_RETRY_MAX_S", 0),
    ):
        await _panel_publishes_version(client, "1.0")

    assert attempts >= 3, "the fetch must be retried while HTTP is still coming up"
    assert client.adapter is not before, "the parser must swap once the fetch succeeds"


@pytest.mark.asyncio
async def test_a_panel_that_never_serves_http_leaves_the_parser_alone() -> None:
    """Bounded, and non-fatal when the bound is reached.

    MQTT is up or this path would not be running, so tearing the connection down over
    an unreachable HTTP endpoint would turn a degraded panel into a dead integration.
    A stale parser reports missing data rather than wrong data, because the two
    schemas share no topic shape.
    """
    client, _ = _client(None)
    before = client.adapter

    with (
        patch(
            "span_panel_api.mqtt.client.get_homie_schema",
            side_effect=SpanPanelConnectionError("never answers"),
        ),
        patch("span_panel_api.mqtt.client._REDISPATCH_RETRY_INITIAL_S", 0),
        patch("span_panel_api.mqtt.client._REDISPATCH_RETRY_MAX_S", 0),
    ):
        await _panel_publishes_version(client, "1.0")

    assert client.adapter is before
    assert not client._redispatch_in_flight, "the in-flight guard must clear on failure"


@pytest.mark.asyncio
async def test_a_consumer_is_told_the_generation_changed() -> None:
    """Swapping the parser restores reading, not topology.

    A consumer builds its devices and entities from the tree as it looked at setup.
    v1.0 introduces a MID the flat tree has no equivalent for and re-keys the EVSEs,
    so a parser swap alone leaves the panel reading correctly while still showing the
    old device set — observed live, where data flowed and a manual reload was still
    needed. Only the consumer can rebuild that, so it is told rather than guessed at.

    Fired after the swap, so a consumer inspecting the client from inside the callback
    sees the generation it is being told about rather than the one being replaced.
    """
    client, _ = _client(None)
    seen: list[tuple[str | None, str | None, str | None]] = []

    client.register_schema_change_callback(
        lambda previous, current: seen.append((previous, current, client.data_model_version))
    )

    with patch("span_panel_api.mqtt.client.get_homie_schema", return_value=_Schema("1.0")):
        await _panel_publishes_version(client, "1.0")

    assert seen == [(None, "1.0", "1.0")]


@pytest.mark.asyncio
async def test_an_unchanged_generation_tells_nobody() -> None:
    """The callback reloads a config entry, so a spurious one is disruptive.

    This property republishes on every reconnect and retained replay. Firing on each
    would reload the integration repeatedly, tearing down and rebuilding every entity
    for a panel that never changed.
    """
    client, _ = _client("1.0")
    seen: list[tuple[str | None, str | None]] = []

    client.register_schema_change_callback(lambda p, c: seen.append((p, c)))

    with patch("span_panel_api.mqtt.client.get_homie_schema", return_value=_Schema("1.0")):
        await _panel_publishes_version(client, "1.0")
        await _panel_publishes_version(client, "1.0")

    assert seen == []


@pytest.mark.asyncio
async def test_a_raising_consumer_does_not_break_the_swap() -> None:
    """The parser is already rebuilt when subscribers are told.

    Reloading a config entry tears down the object that registered the callback, so a
    subscriber raising mid-teardown is a realistic outcome rather than a hypothetical
    one. It must not leave the client half-swapped.
    """
    client, _ = _client(None)
    client.register_schema_change_callback(lambda _p, _c: (_ for _ in ()).throw(RuntimeError("boom")))
    reached: list[str] = []
    client.register_schema_change_callback(lambda _p, _c: reached.append("second"))

    with patch("span_panel_api.mqtt.client.get_homie_schema", return_value=_Schema("1.0")):
        await _panel_publishes_version(client, "1.0")

    assert client.data_model_version == "1.0", "the swap must stand"
    assert reached == ["second"], "one raising subscriber must not starve the others"


@pytest.mark.asyncio
async def test_a_rebooting_panel_answering_502_is_waited_for_not_abandoned() -> None:
    """The failure that cost a live firmware upgrade its automatic reload.

    A panel accepts MQTT before it serves HTTP, and the retry loop above exists
    for that. But there are three ways HTTP lags the broker, and this loop
    originally handled two: it caught "cannot reach" and "timed out" and not
    "answered, with 502". A booting device brings its network stack and reverse
    proxy up before the application behind them, so 502 is the *ordinary* shape,
    not an exotic one.

    Because `SpanPanelServerError` was not caught, the very first attempt raised
    straight out of the loop, out of the fire-and-forget task that called it, and
    the parser was never swapped. Observed on two Home Assistant instances
    watching one panel through the same upgrade: both logged `Task exception was
    never retrieved`, both stayed on the flat parser, and neither recovered
    without a manual reload.
    """
    client, _ = _client(None)
    before = client.adapter
    attempts = 0

    def _five_oh_two_then_ready(*_a: object, **_k: object) -> _Schema:
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise SpanPanelServerError("Panel not ready: HTTP 502 fetching the Homie schema", 502)
        return _Schema("1.0")

    with (
        patch("span_panel_api.mqtt.client.get_homie_schema", side_effect=_five_oh_two_then_ready),
        patch("span_panel_api.mqtt.client._REDISPATCH_RETRY_INITIAL_S", 0),
        patch("span_panel_api.mqtt.client._REDISPATCH_RETRY_MAX_S", 0),
    ):
        await _panel_publishes_version(client, "1.0")

    assert attempts >= 3, "a 502 must be retried rather than ending the attempt"
    assert client.adapter is not before, "the parser must swap once the panel answers"


@pytest.mark.asyncio
async def test_an_unexpected_failure_leaves_a_usable_message_rather_than_a_bare_traceback(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Nothing may escape the fire-and-forget task.

    An escaping exception surfaces as "Task exception was never retrieved" and
    the parser silently stays on the old generation -- the exact failure this
    method exists to prevent, reached by a different route. The user's remedy is
    a reload, and nothing else is going to tell them so.
    """
    client, _ = _client(None)
    before = client.adapter

    with patch(
        "span_panel_api.mqtt.client.get_homie_schema",
        side_effect=RuntimeError("something nobody predicted"),
    ):
        await _panel_publishes_version(client, "1.0")

    assert client.adapter is before
    assert "Reload the integration" in caplog.text
    assert "something nobody predicted" in caplog.text


def test_the_retry_window_outlasts_a_real_panel_reboot() -> None:
    """Catching the 502 buys nothing if the loop gives up before the panel is ready.

    Measured rather than assumed. On a live firmware upgrade the panel dropped
    MQTT at 11:22:07 and the broker was back at 11:26:15 — four minutes — and its
    HTTP front end was still answering 502 at that moment, which is when this
    loop starts.

    Pinned as a total because the three constants only mean something together,
    and because the widening was written once, lost to a failed edit, and shipped
    without it. Nothing failed: the 502 was caught and the loop still gave up
    after twenty-three seconds. A test on the constants is the only thing that
    would have noticed.
    """
    delay = _REDISPATCH_RETRY_INITIAL_S
    total = 0.0
    for _ in range(_REDISPATCH_RETRY_ATTEMPTS):
        total += delay
        delay = min(delay * 2, _REDISPATCH_RETRY_MAX_S)

    observed_reboot_s = 4 * 60
    assert total >= observed_reboot_s, (
        f"the retry window is {total:.0f}s, shorter than the {observed_reboot_s}s reboot " "this loop exists to wait out"
    )
