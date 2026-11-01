"""A retained value the panel clears reads as unpublished, not as an empty value.

MQTT deletes a retained message when a zero-length payload is published to its
topic, and Homie 5 keeps that meaning: a device removes a value by clearing its
topic. Homie 5 therefore spells an empty *string* differently, as a single
``0x00`` byte. The two arrive apart -- the transport hands the adapter ``""``
for a clear and ``"\\x00"`` for an empty string -- but ebus-sdk 0.22.0's
`Controller` decodes both to ``""`` and stores that, so a property the panel
cleared went on reading as a value the panel published.

That is not merely stale. The reference tree's upstream lugs publish
`connection/fed-by-device-id: bess`; cleared, the stored ``""`` read as "fed by
nothing", and the snapshot claimed the lugs were the service entrance. Declared
and unvalued, the honest answer is `None`.

Every test drives `handle_message`, the path the transport takes.
"""

from __future__ import annotations

from collections.abc import Iterable

from reference_payloads.captures import capture_tree_from_document
from reference_payloads.schema_one import RetainedTopicTree, parent_child_tree, replay
from span_panel_api.models import V2HomieSchema
from span_panel_api_schema_1 import SchemaOneAdapter
from span_panel_api_schema_1.tree_export import placeholder_ids

_TREE = parent_child_tree()

PANEL = "example-40t-001"
LUGS = "lugs-upstream"
FED_BY = "connection/fed-by-device-id"
FED_BY_TOPIC = f"ebus/5/{LUGS}/{FED_BY}"
KITCHEN_CIRCUIT = "0ab966b95f92a6a51ec548485aa85f54"
"""A controllable circuit, so a relay `$target` on it is one the panel could publish."""
RELAY_TARGET_TOPIC = f"ebus/5/{KITCHEN_CIRCUIT}/switch/relay/$target"
MODEL_TOPIC = f"ebus/5/{PANEL}/info/model"

CLEARED = ""
"""A zero-length MQTT payload, as the transport decodes it: the retained topic is deleted."""
EMPTY_STRING = "\x00"
"""Homie 5's empty-string value, as the transport decodes it: published, and empty."""


def _schema() -> V2HomieSchema:
    return V2HomieSchema(
        firmware_version="spanos2/r202633/01",
        types_schema_hash="sha256:test",
        types={},
        data_model_version="1.0",
    )


def _retained(tree: RetainedTopicTree = _TREE) -> list[tuple[str, str]]:
    """The tree as the broker replays it, the panel first."""
    messages: list[tuple[str, str]] = []
    for device_id in [PANEL, *[other for other in tree if other != PANEL]]:
        topics = tree[device_id]
        prefix = f"ebus/5/{device_id}"
        messages.append((f"{prefix}/$description", topics["$description"]))
        messages.append((f"{prefix}/$state", topics["$state"]))
        messages.extend((f"{prefix}/{topic}", value) for topic, value in topics.items() if not topic.startswith("$"))
    return messages


def _adapter(
    *messages: tuple[str, str], before: Iterable[tuple[str, str]] = (), tree: RetainedTopicTree = _TREE
) -> SchemaOneAdapter:
    """`tree` replayed, then `messages`; `before` arrives ahead of the replay, before any route exists."""
    adapter = SchemaOneAdapter(PANEL, _schema())
    for topic, payload in [*before, *_retained(tree), *messages]:
        adapter.handle_message(topic, payload)
    return adapter


def _exported_lugs(adapter: SchemaOneAdapter) -> dict[str, str]:
    """The upstream lugs' declared non-numeric values in the masked export."""
    tree = adapter.export_masked_tree()
    assert tree is not None
    root = adapter._controller.get_root(PANEL)
    assert root is not None
    placeholder = placeholder_ids(root, adapter._children())[LUGS]
    return tree["devices"][placeholder]["properties"]


# -- a cleared value -------------------------------------------------------


def test_the_reference_lugs_are_fed_by_the_battery() -> None:
    """The baseline the clears below move away from."""
    assert _adapter().build_snapshot().lugs_at_service_entrance is False


def test_a_cleared_property_reads_as_unpublished() -> None:
    """Declared and unvalued: the lugs have not said what feeds them."""
    adapter = _adapter((FED_BY_TOPIC, CLEARED))

    assert adapter.build_snapshot().lugs_at_service_entrance is None


def test_an_empty_string_still_reads_as_a_published_empty_value() -> None:
    """Homie 5's ``0x00`` is a value: published empty, which reads as fed by nothing."""
    adapter = _adapter((FED_BY_TOPIC, EMPTY_STRING))

    assert adapter.build_snapshot().lugs_at_service_entrance is True


def test_a_value_published_after_a_clear_is_read_again() -> None:
    adapter = _adapter((FED_BY_TOPIC, CLEARED), (FED_BY_TOPIC, "bess"))

    assert adapter.build_snapshot().lugs_at_service_entrance is False


def test_a_cleared_property_leaves_no_empty_node_behind() -> None:
    """A node whose every value is cleared is gone from the stored values, as from the broker."""
    lugs_values = {topic for topic in _TREE[LUGS] if topic.startswith("connection/")}
    adapter = _adapter(*((f"ebus/5/{LUGS}/{topic}", CLEARED) for topic in lugs_values))

    lugs = adapter._controller.get_device(LUGS)
    assert lugs is not None
    assert "connection" not in lugs.properties


def test_a_clear_is_reported_to_property_observers_as_no_value() -> None:
    """The protocol's `None`: the property now has no value, which observers must not keep."""
    adapter = _adapter()
    seen: list[tuple[str, str, str, str | None]] = []
    adapter.register_property_callback(lambda *change: seen.append(change))

    adapter.handle_message(FED_BY_TOPIC, CLEARED)

    assert seen == [(LUGS, "connection", "fed-by-device-id", None)]


def test_clearing_the_model_readiness_waits_on_makes_the_tree_not_ready() -> None:
    """The remembered readiness is forgotten by a clear exactly as by a value change."""
    adapter = _adapter()
    assert adapter.is_ready() is True

    adapter.handle_message(MODEL_TOPIC, CLEARED)

    assert adapter.is_ready() is False


# -- a cleared $target -----------------------------------------------------


def test_a_cleared_target_is_dropped() -> None:
    published = _adapter((RELAY_TARGET_TOPIC, "OPEN"))
    assert published.build_snapshot().circuits[KITCHEN_CIRCUIT].relay_state_target == "OPEN"

    cleared = _adapter((RELAY_TARGET_TOPIC, "OPEN"), (RELAY_TARGET_TOPIC, CLEARED))

    assert cleared.build_snapshot().circuits[KITCHEN_CIRCUIT].relay_state_target is None


def test_an_empty_string_target_is_still_a_published_empty_target() -> None:
    adapter = _adapter((RELAY_TARGET_TOPIC, "OPEN"), (RELAY_TARGET_TOPIC, EMPTY_STRING))

    assert adapter.build_snapshot().circuits[KITCHEN_CIRCUIT].relay_state_target == ""


# -- the masked export -----------------------------------------------------


def test_the_export_shows_a_cleared_property_as_declared_and_unvalued() -> None:
    """No key, the export's spelling of a declared property with no value."""
    assert FED_BY in _exported_lugs(_adapter())

    exported = _exported_lugs(_adapter((FED_BY_TOPIC, CLEARED)))

    assert FED_BY not in exported


def test_the_export_keeps_an_empty_string_as_empty() -> None:
    assert _exported_lugs(_adapter((FED_BY_TOPIC, EMPTY_STRING)))[FED_BY] == ""


def test_an_exported_empty_value_replays_as_an_empty_value() -> None:
    """The export writes an empty value decoded, as ``""``, and replaying it must not turn it into a clear."""
    export = _adapter((FED_BY_TOPIC, EMPTY_STRING)).export_masked_tree()
    assert export is not None
    captured = capture_tree_from_document(export, "export")

    again = replay(captured.retained(), captured.root_id)

    assert again.build_snapshot().lugs_at_service_entrance is True


# -- clears that change nothing --------------------------------------------


def test_a_clear_for_a_value_never_published_is_a_no_op() -> None:
    """Nothing to remove, nothing to report, and no empty node created for it."""
    adapter = _adapter()
    before = adapter.build_snapshot()
    seen: list[tuple[str, str, str, str | None]] = []
    adapter.register_property_callback(lambda *change: seen.append(change))

    adapter.handle_message(f"ebus/5/{LUGS}/vendor/never-published", CLEARED)
    adapter.handle_message(RELAY_TARGET_TOPIC, CLEARED)

    lugs = adapter._controller.get_device(LUGS)
    circuit = adapter._controller.get_device(KITCHEN_CIRCUIT)
    assert lugs is not None and circuit is not None
    assert "vendor" not in lugs.properties
    assert circuit.property_targets == {}
    assert seen == []
    assert adapter.build_snapshot() == before


def test_a_clear_for_a_device_outside_the_tree_is_a_no_op() -> None:
    """Not held either: a broker replays nothing for a cleared topic."""
    adapter = _adapter()
    before = adapter.build_snapshot()
    held = adapter._routes.held
    seen: list[tuple[str, str, str, str | None]] = []
    adapter.register_property_callback(lambda *change: seen.append(change))

    adapter.handle_message("ebus/5/no-such-device/meter/active-power", CLEARED)
    adapter.handle_message("ebus/5/no-such-device/switch/relay/$target", CLEARED)

    assert adapter._routes.held == held
    assert seen == []
    assert adapter.build_snapshot() == before


def test_a_value_cleared_before_its_device_is_routed_never_arrives() -> None:
    """A message ahead of its route is held for it, and a clear takes the held value back.

    Under a per-device subscription the broker would replay nothing for the
    cleared topic, so releasing the held value, or an empty one, would invent a
    reading.
    """
    without = {device: dict(topics) for device, topics in _TREE.items()}
    del without[LUGS][FED_BY]

    adapter = _adapter(before=[(FED_BY_TOPIC, "bess"), (FED_BY_TOPIC, CLEARED)], tree=without)

    assert adapter.build_snapshot().lugs_at_service_entrance is None
    assert _exported_lugs(adapter).get(FED_BY) is None
