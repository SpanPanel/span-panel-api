"""The EVSE charge-current ceiling: read from the declaration, written to it.

The only settable property the v1.0 catch-up surfaces, and the only one whose
wire name is unsettled — the reference tree says `config/{max,user-max}-charge-current`,
the eBus catalog says `charge-limit/{installer-max,owner-limit}`, and no capture
can decide between them because the panels we can reach carry no SPAN Drive.

So the parser is written against the *rule* rather than against either name, and
these tests hold it to that: every read expectation is computed from the captured
tree, the catalogued spelling is driven through a rewritten description and has to
behave identically, and every write assertion names the exact topic and payload
the transport puts on the wire.
"""

from __future__ import annotations

from collections.abc import Mapping
import json
from unittest.mock import MagicMock

import pytest

from conftest import acking_bridge

from ebus_sdk.homie import DiscoveredDevice

from span_panel_api.exceptions import SpanPanelServerError
from span_panel_api.models import V2HomieSchema
from span_panel_api_schema_1 import SchemaOneAdapter
from span_panel_api_schema_1.charge_limit import resolve_charge_limit
from span_panel_api_schema_1.devices import build_evse
from span_panel_api_schema_1.field_metadata import build_field_metadata
from span_panel_api_schema_1.reference_payloads import device_from_topics, parent_child_tree

_TREE = parent_child_tree()

PANEL = "example-40t-001"
EVSE = "evse"
EVSE_2 = "evse-2"

CEILING_TOPIC = "config/max-charge-current"
LIMIT_TOPIC = "config/user-max-charge-current"

# The catalogued spelling, which no producer we have publishes. Written here as
# the topics a `charge-limit` charger would publish, so the rewrite below is a
# rename of the capture rather than a second hand-built tree.
CATALOG_CEILING_TOPIC = "charge-limit/installer-max"
CATALOG_LIMIT_TOPIC = "charge-limit/owner-limit"


def _schema() -> V2HomieSchema:
    return V2HomieSchema(
        firmware_version="spanos2/r202633/01",
        types_schema_hash="sha256:test",
        types={},
        data_model_version="1.0",
    )


def _published(device_id: str, topic: str) -> str:
    """What the capture publishes on this topic, or fail saying it does not.

    Every expectation below is computed from this rather than written as a
    literal, so a test cannot keep passing against a fixture that stopped
    carrying the value it is about.
    """
    value = _TREE[device_id].get(topic)
    assert value is not None, f"{device_id} publishes no {topic} in the capture"
    return value


def _tree(**overrides: Mapping[str, str | None]) -> dict[str, dict[str, str]]:
    """The capture with topics rewritten per device, or removed where `None`.

    Removal is a distinct probe from rewriting: a panel that stops publishing a
    property retains nothing, which is not the same event as publishing `""`.
    """
    tree = {device_id: dict(topics) for device_id, topics in _TREE.items()}
    for device_id, topics in overrides.items():
        for topic, value in topics.items():
            if value is None:
                tree[device_id].pop(topic, None)
            else:
                tree[device_id][topic] = value
    return tree


def _evse_device(tree: dict[str, dict[str, str]], device_id: str) -> DiscoveredDevice:
    return device_from_topics(device_id, tree[device_id])


def _snapshot_evse(tree: dict[str, dict[str, str]], device_id: str) -> object:
    """One EVSE snapshot built by the real mapper from `tree`."""
    return build_evse(_evse_device(tree, device_id), {}, node_id=device_id, feed_statuses={})


def _renamed_to_catalog(device_id: str) -> dict[str, dict[str, str]]:
    """The capture with one charger publishing the catalogued spelling instead.

    Both halves move — the `$description` node and the value topics — because a
    charger that renamed one and not the other would be publishing to a property
    it never declared, which is a different (and illegal) situation from the one
    under test.
    """
    topics = dict(_TREE[device_id])
    description = json.loads(topics["$description"])
    config = description["nodes"].pop("config")
    properties = config["properties"]
    description["nodes"]["charge-limit"] = {
        "name": "charge-limit",
        "type": "energy.ebus.capability.charge-limit",
        "properties": {
            "installer-max": properties["max-charge-current"],
            "owner-limit": properties["user-max-charge-current"],
        },
    }
    topics["$description"] = json.dumps(description)
    topics[CATALOG_CEILING_TOPIC] = topics.pop(CEILING_TOPIC)
    topics[CATALOG_LIMIT_TOPIC] = topics.pop(LIMIT_TOPIC)
    tree = {other: dict(values) for other, values in _TREE.items()}
    tree[device_id] = topics
    return tree


def _without_settable(device_id: str) -> dict[str, dict[str, str]]:
    """The capture with the limit's `$settable` attribute gone from its declaration."""
    topics = dict(_TREE[device_id])
    description = json.loads(topics["$description"])
    description["nodes"]["config"]["properties"]["user-max-charge-current"].pop("settable")
    topics["$description"] = json.dumps(description)
    tree = {other: dict(values) for other, values in _TREE.items()}
    tree[device_id] = topics
    return tree


def _without_node(device_id: str) -> dict[str, dict[str, str]]:
    """The capture with the whole charge-limit node gone — a fixed-rate charger."""
    topics = {topic: value for topic, value in _TREE[device_id].items() if topic not in {CEILING_TOPIC, LIMIT_TOPIC}}
    description = json.loads(topics["$description"])
    description["nodes"].pop("config")
    topics["$description"] = json.dumps(description)
    tree = {other: dict(values) for other, values in _TREE.items()}
    tree[device_id] = topics
    return tree


def _adapter(tree: dict[str, dict[str, str]] | None = None) -> SchemaOneAdapter:
    """An adapter fed the tree the way the broker replays it."""
    replayed = _TREE if tree is None else tree
    adapter = SchemaOneAdapter(PANEL, _schema())
    for device_id in [PANEL, *[d for d in replayed if d != PANEL]]:
        topics = replayed[device_id]
        prefix = f"ebus/5/{device_id}"
        adapter.handle_message(f"{prefix}/$description", topics["$description"])
        adapter.handle_message(f"{prefix}/$state", topics["$state"])
        for topic, value in topics.items():
            if not topic.startswith("$"):
                adapter.handle_message(f"{prefix}/{topic}", value)
    return adapter


def _key(adapter: SchemaOneAdapter, device_id: str) -> str:
    """The snapshot key for one charger — its serial, not its device id.

    Looked up rather than written down, because the difference between the two
    is what the command tests are checking.
    """
    snapshot = adapter.build_snapshot()
    for key, evse in snapshot.evse.items():
        if evse.serial_number == _published(device_id, "info/serial-number"):
            return key
    raise AssertionError(f"no EVSE in the snapshot carries {device_id}'s serial")


# ---------------------------------------------------------------------------
# Reading — from the capture, and per charger
# ---------------------------------------------------------------------------


def test_both_halves_come_off_the_wire() -> None:
    evse = _snapshot_evse(_TREE, EVSE)

    assert evse.charge_current_limit_a == int(_published(EVSE, LIMIT_TOPIC))
    assert evse.charge_current_ceiling_a == int(_published(EVSE, CEILING_TOPIC))


def test_each_charger_reads_its_own_limit() -> None:
    """Two chargers, two different values, and neither may answer for the other.

    The capture publishes 32 on both, so an assertion against it as-published
    would pass for a parser that read one charger and reported it twice. The
    values are made to differ first, which is the only shape of this test that
    proves anything.
    """
    first, second = int(_published(EVSE, LIMIT_TOPIC)) - 8, int(_published(EVSE_2, LIMIT_TOPIC)) - 16
    assert first != second

    tree = _tree(**{EVSE: {LIMIT_TOPIC: str(first)}, EVSE_2: {LIMIT_TOPIC: str(second)}})

    assert _snapshot_evse(tree, EVSE).charge_current_limit_a == first
    assert _snapshot_evse(tree, EVSE_2).charge_current_limit_a == second


def test_each_charger_reads_its_own_ceiling() -> None:
    """The same proof for the installer ceiling, which bounds the control."""
    first, second = int(_published(EVSE, CEILING_TOPIC)) - 8, int(_published(EVSE_2, CEILING_TOPIC)) - 16
    assert first != second

    tree = _tree(**{EVSE: {CEILING_TOPIC: str(first)}, EVSE_2: {CEILING_TOPIC: str(second)}})

    assert _snapshot_evse(tree, EVSE).charge_current_ceiling_a == first
    assert _snapshot_evse(tree, EVSE_2).charge_current_ceiling_a == second


def test_republishing_moves_the_reading() -> None:
    raised = int(_published(EVSE, LIMIT_TOPIC)) - 12

    assert _snapshot_evse(_tree(**{EVSE: {LIMIT_TOPIC: str(raised)}}), EVSE).charge_current_limit_a == raised


def test_an_unpublished_value_is_none_rather_than_zero() -> None:
    """A charger that has not published yet has no limit, which is not 0 A."""
    evse = _snapshot_evse(_tree(**{EVSE: {LIMIT_TOPIC: None, CEILING_TOPIC: None}}), EVSE)

    assert evse.charge_current_limit_a is None
    assert evse.charge_current_ceiling_a is None
    # Still declared, so still writable: the value is missing, not the property.
    assert evse.charge_current_limit_settable is True


def test_the_declaration_decides_settability() -> None:
    assert _snapshot_evse(_TREE, EVSE).charge_current_limit_settable is True
    assert _snapshot_evse(_without_settable(EVSE), EVSE).charge_current_limit_settable is False


def test_the_ceiling_is_never_reported_settable() -> None:
    """The regression this defaulting rule exists to prevent.

    Ceiling and limit differ by one Homie attribute. `load-shed/priority` reads
    an absent `$settable` as settable, correctly — locking is the exception a
    panel announces there. Carrying that default here would make the installer's
    commissioned maximum look writable, so the two halves are asserted apart.
    """
    surface = resolve_charge_limit(_evse_device(_TREE, EVSE))

    assert surface is not None
    assert surface.ceiling is not None and surface.ceiling.settable is False
    assert surface.limit is not None and surface.limit.settable is True


def test_a_pending_write_shows_as_a_target() -> None:
    """The Homie `$target` echo, the same pending-command signal the priority
    select already reads through `circuit.priority_target`."""
    device = _evse_device(_TREE, EVSE)
    pending = int(_published(EVSE, LIMIT_TOPIC)) - 8
    device.update_property_target("config", "user-max-charge-current", str(pending))

    evse = build_evse(device, {}, node_id=EVSE, feed_statuses={})

    assert evse.charge_current_limit_target_a == pending
    assert evse.charge_current_limit_a == int(_published(EVSE, LIMIT_TOPIC))


def test_no_pending_write_is_no_target() -> None:
    assert _snapshot_evse(_TREE, EVSE).charge_current_limit_target_a is None


def test_a_charger_with_no_charge_limit_node_reports_none() -> None:
    """`charge-limit.md`: absence means the EVSE charges at a fixed rate."""
    evse = _snapshot_evse(_without_node(EVSE), EVSE)

    assert evse.charge_current_limit_a is None
    assert evse.charge_current_ceiling_a is None
    assert evse.charge_current_limit_settable is False
    # The rest of the charger still reads, so this is the node going away and
    # not the device.
    assert evse.status == _published(EVSE, "status/status")


# ---------------------------------------------------------------------------
# The other spelling
# ---------------------------------------------------------------------------


def test_the_catalogued_spelling_reads_identically() -> None:
    """`charge-limit/{installer-max,owner-limit}` — the eBus 0.1 naming.

    The claim this whole design rests on: nothing outside `charge_limit.py`
    names a node, so a charger publishing the specified spelling produces the
    same snapshot as one publishing SPAN's. Asserted field by field against the
    unrenamed capture rather than against literals, so the two paths are held to
    each other and not merely to the same numbers.
    """
    published = _snapshot_evse(_TREE, EVSE)
    catalogued = _snapshot_evse(_renamed_to_catalog(EVSE), EVSE)

    assert catalogued == published


def test_the_catalogued_spelling_is_written_to_its_own_topic() -> None:
    adapter = _adapter(_renamed_to_catalog(EVSE))
    key = _key(adapter, EVSE)

    assert adapter.set_evse_charge_limit_topic(key) == f"ebus/5/{EVSE}/charge-limit/owner-limit/set"


def test_the_catalogued_spelling_wins_where_both_are_declared() -> None:
    """A charger mid-migration declares both; the specified one is authoritative.

    Not a hypothetical: a rename lands in firmware by adding the new node before
    retiring the old, and a reader that took whichever it saw first would flip
    between them on the strength of dict ordering.
    """
    tree = _renamed_to_catalog(EVSE)
    stale = json.loads(_TREE[EVSE]["$description"])["nodes"]["config"]
    description = json.loads(tree[EVSE]["$description"])
    description["nodes"]["config"] = stale
    tree[EVSE]["$description"] = json.dumps(description)
    tree[EVSE][CEILING_TOPIC] = _published(EVSE, CEILING_TOPIC)
    tree[EVSE][LIMIT_TOPIC] = _published(EVSE, LIMIT_TOPIC)

    surface = resolve_charge_limit(_evse_device(tree, EVSE))

    assert surface is not None
    assert surface.node == "charge-limit"


# ---------------------------------------------------------------------------
# Writing — the exact topic, the exact payload
# ---------------------------------------------------------------------------


def test_the_set_topic_addresses_the_device_and_the_declared_property() -> None:
    """Device id in the topic, serial in the snapshot key — they are not the same string.

    A charger that publishes `info/serial-number` is keyed by that serial in the
    snapshot, while the wire addresses it by device id. Building the topic from
    the key the caller holds would publish to `ebus/5/SIM-EVSE-…/…`, which no
    device subscribes to, and nothing would report a failure.
    """
    adapter = _adapter()
    key = _key(adapter, EVSE)

    assert key != EVSE
    assert adapter.set_evse_charge_limit_topic(key) == f"ebus/5/{EVSE}/config/user-max-charge-current/set"


def test_each_charger_gets_its_own_set_topic() -> None:
    adapter = _adapter()

    assert adapter.set_evse_charge_limit_topic(_key(adapter, EVSE)) == (f"ebus/5/{EVSE}/config/user-max-charge-current/set")
    assert adapter.set_evse_charge_limit_topic(_key(adapter, EVSE_2)) == (
        f"ebus/5/{EVSE_2}/config/user-max-charge-current/set"
    )


def test_no_topic_for_a_charger_that_does_not_declare_the_limit_settable() -> None:
    """The refusal. A property with no `$settable` is not a control, and naming a
    topic for it would put a write on the wire the panel never offered."""
    adapter = _adapter(_without_settable(EVSE))
    key = _key(adapter, EVSE)

    assert adapter.set_evse_charge_limit_topic(key) is None
    assert adapter.evse_charge_limit_payload(key, 16) is None


def test_no_topic_for_a_charger_with_no_charge_limit_node() -> None:
    adapter = _adapter(_without_node(EVSE))
    key = _key(adapter, EVSE)

    assert adapter.set_evse_charge_limit_topic(key) is None
    assert adapter.evse_charge_limit_payload(key, 16) is None


def test_no_topic_for_a_charger_the_panel_does_not_have() -> None:
    adapter = _adapter()

    assert adapter.set_evse_charge_limit_topic("not-a-charger") is None
    assert adapter.evse_charge_limit_payload("not-a-charger", 16) is None


def test_a_value_at_or_below_the_ceiling_is_published_as_it_is() -> None:
    adapter = _adapter()
    key = _key(adapter, EVSE)
    ceiling = int(_published(EVSE, CEILING_TOPIC))

    assert adapter.evse_charge_limit_payload(key, ceiling) == str(ceiling)
    assert adapter.evse_charge_limit_payload(key, ceiling - 16) == str(ceiling - 16)
    assert adapter.evse_charge_limit_payload(key, 0) == "0"


def test_above_the_ceiling_is_refused_rather_than_clamped() -> None:
    """`charge-limit` 0.1 makes `owner-limit <= installer-max` a MUST, and the
    ceiling is derated hardware protection. Clamping would report a limit the
    charger is not enforcing; refusing tells the caller."""
    adapter = _adapter()
    key = _key(adapter, EVSE)

    assert adapter.evse_charge_limit_payload(key, int(_published(EVSE, CEILING_TOPIC)) + 1) is None


def test_the_ceiling_that_bounds_the_write_is_that_charger_s_own() -> None:
    """Two chargers with different ceilings; a value legal on one is not on the other."""
    lowered = int(_published(EVSE_2, CEILING_TOPIC)) - 16
    adapter = _adapter(_tree(**{EVSE_2: {CEILING_TOPIC: str(lowered)}}))
    asked = lowered + 8
    assert asked <= int(_published(EVSE, CEILING_TOPIC))

    assert adapter.evse_charge_limit_payload(_key(adapter, EVSE), asked) == str(asked)
    assert adapter.evse_charge_limit_payload(_key(adapter, EVSE_2), asked) is None


def test_a_negative_amperage_is_refused() -> None:
    adapter = _adapter()

    assert adapter.evse_charge_limit_payload(_key(adapter, EVSE), -1) is None


def test_a_charger_with_no_ceiling_is_not_second_guessed() -> None:
    """`installer-max` is a SHOULD. With none declared there is no published
    bound, and inventing one here would be this library making up hardware limits."""
    topics = dict(_TREE[EVSE])
    description = json.loads(topics["$description"])
    description["nodes"]["config"]["properties"].pop("max-charge-current")
    topics["$description"] = json.dumps(description)
    topics.pop(CEILING_TOPIC)
    tree = {other: dict(values) for other, values in _TREE.items()}
    tree[EVSE] = topics

    adapter = _adapter(tree)
    key = _key(adapter, EVSE)

    assert adapter.evse_charge_limit_payload(key, 1000) == "1000"
    assert adapter.set_evse_charge_limit_topic(key) == f"ebus/5/{EVSE}/config/user-max-charge-current/set"


# ---------------------------------------------------------------------------
# The transport — what actually reaches the wire
# ---------------------------------------------------------------------------


def _client(adapter: SchemaOneAdapter) -> tuple[object, MagicMock]:
    from span_panel_api.mqtt.client import MqttClientConfig, SpanMqttClient

    config = MqttClientConfig(broker_host="h", username="u", password="p")
    client = SpanMqttClient(host="192.168.1.1", serial_number=PANEL, broker_config=config)
    client._adapter = adapter
    bridge = acking_bridge()
    client._bridge = bridge
    return client, bridge


@pytest.mark.asyncio
async def test_the_transport_publishes_the_topic_and_payload_the_adapter_named() -> None:
    adapter = _adapter()
    client, bridge = _client(adapter)
    key = _key(adapter, EVSE)
    asked = int(_published(EVSE, CEILING_TOPIC)) - 8

    await client.set_evse_charge_limit(key, asked)

    bridge.publish.assert_called_once_with(f"ebus/5/{EVSE}/config/user-max-charge-current/set", str(asked))


@pytest.mark.asyncio
async def test_the_transport_refuses_a_charger_with_no_control() -> None:
    adapter = _adapter(_without_settable(EVSE))
    client, bridge = _client(adapter)

    with pytest.raises(SpanPanelServerError, match="No settable charge-current limit"):
        await client.set_evse_charge_limit(_key(adapter, EVSE), 16)

    bridge.publish.assert_not_called()


@pytest.mark.asyncio
async def test_the_transport_refuses_a_value_above_the_ceiling() -> None:
    adapter = _adapter()
    client, bridge = _client(adapter)
    over = int(_published(EVSE, CEILING_TOPIC)) + 1

    with pytest.raises(SpanPanelServerError, match=f"{over} A is outside"):
        await client.set_evse_charge_limit(_key(adapter, EVSE), over)

    bridge.publish.assert_not_called()


# ---------------------------------------------------------------------------
# Metadata — the unit and datatype come from the same resolution
# ---------------------------------------------------------------------------


def _metadata(tree: dict[str, dict[str, str]]) -> dict[str, object]:
    devices = [device_from_topics(device_id, topics) for device_id, topics in tree.items()]
    return dict(build_field_metadata(devices))


def test_metadata_carries_the_declared_unit_and_datatype() -> None:
    declared = json.loads(_TREE[EVSE]["$description"])["nodes"]["config"]["properties"]
    metadata = _metadata(_TREE)

    for path, property_id in (
        ("evse.charge_current_limit_a", "user-max-charge-current"),
        ("evse.charge_current_ceiling_a", "max-charge-current"),
    ):
        entry = metadata[path]
        assert entry.unit == declared[property_id]["unit"]
        assert entry.datatype == declared[property_id]["datatype"]
        assert entry.resolved is True


def test_metadata_follows_the_catalogued_spelling_too() -> None:
    metadata = _metadata(_renamed_to_catalog(EVSE))

    assert metadata["evse.charge_current_limit_a"].unit == "A"
    assert metadata["evse.charge_current_ceiling_a"].datatype == "integer"


def test_a_declared_node_missing_a_half_is_a_gap_not_absent_hardware() -> None:
    topics = dict(_TREE[EVSE])
    description = json.loads(topics["$description"])
    description["nodes"]["config"]["properties"].pop("max-charge-current")
    topics["$description"] = json.dumps(description)
    tree = {other: dict(values) for other, values in _TREE.items()}
    tree[EVSE] = topics
    # The second charger still declares both, and must not answer for the first.
    tree.pop(EVSE_2)

    metadata = _metadata(tree)

    assert metadata["evse.charge_current_ceiling_a"].resolved is False
    assert metadata["evse.charge_current_limit_a"].resolved is True


def test_no_metadata_where_no_charger_declares_the_node() -> None:
    tree = _without_node(EVSE)
    tree.pop(EVSE_2)

    metadata = _metadata(tree)

    assert "evse.charge_current_limit_a" not in metadata
    assert "evse.charge_current_ceiling_a" not in metadata
