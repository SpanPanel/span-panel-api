"""The EVSE charge-current ceiling: read from the declaration, written to it.

The only settable property the v1.0 catch-up surfaces, and the only one whose
wire name is unsettled — the reference tree says `config/{max,user-max}-charge-current`,
the eBus catalog says `charge-limit/{installer-max,owner-limit}`, and no capture
can decide between them because the panels we can reach carry no SPAN Drive.

So the parser is written against the *rule* rather than against either name, and
these tests hold it to that: every read expectation is computed from the captured
tree or from a value republished on it, the catalogued spelling is driven through a
rewritten description and has to behave identically, and every write assertion
names the exact topic and payload the transport puts on the wire.

The capture sets no user limit. From firmware r202639 a charger publishes
`config/user-max-charge-current` only once a user sets one, and `ebus-panel-sim`
0.9.0 publishes that behaviour, so a test about a valued limit publishes one
(`_user_limit`) the way a charger would after a user set it.
"""

from __future__ import annotations

from collections.abc import Mapping
import json
from unittest.mock import MagicMock

import pytest

from conftest import FAST_CONTROL_DEADLINES, acking_bridge

from ebus_sdk.homie import DiscoveredDevice

from reference_payloads.captures import CAPTURES, capture_snapshot, capture_tree
from reference_payloads.schema_one import device_from_topics, parent_child_tree
from span_panel_api.exceptions import SpanPanelServerError
from span_panel_api.models import V2HomieSchema
from span_panel_api_schema_1 import SchemaOneAdapter
from span_panel_api_schema_1.charge_limit import resolve_charge_limit
from span_panel_api_schema_1.devices import build_evse
from span_panel_api_schema_1.field_metadata import build_field_metadata

_TREE = parent_child_tree()

PANEL = "example-40t-001"
EVSE = "evse"
EVSE_2 = "evse-2"
_EVSE_TYPE = "energy.ebus.device.evse"

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


def _user_limit(device_id: str) -> int:
    """A limit a user would set on this charger: inside its ceiling, and not equal to it.

    Below the ceiling so it cannot be mistaken for the ceiling standing in for an
    absent limit, which is what the effective limit reports when none is set.
    """
    return int(_published(device_id, CEILING_TOPIC)) - 8


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


def _renamed_to_catalog(device_id: str, base: dict[str, dict[str, str]] | None = None) -> dict[str, dict[str, str]]:
    """The capture (or `base`, a tree built from it) with one charger publishing the catalogued spelling instead.

    Both halves move — the `$description` node and the value topics — because a
    charger that renamed one and not the other would be publishing to a property
    it never declared, which is a different (and illegal) situation from the one
    under test. The limit's value topic moves only where the charger publishes
    one: unset, it is unpublished under either spelling. The ceiling is always
    published, so its absence is a fixture fault and fails here.
    """
    source = _TREE if base is None else base
    topics = dict(source[device_id])
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
    if LIMIT_TOPIC in topics:
        topics[CATALOG_LIMIT_TOPIC] = topics.pop(LIMIT_TOPIC)
    tree = {other: dict(values) for other, values in source.items()}
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
    """A charger with a limit set publishes both halves, and each is read off its own topic."""
    limit = _user_limit(EVSE)

    evse = _snapshot_evse(_tree(**{EVSE: {LIMIT_TOPIC: str(limit)}}), EVSE)

    assert evse.charge_current_limit_a == limit
    assert evse.charge_current_ceiling_a == int(_published(EVSE, CEILING_TOPIC))


def test_the_capture_sets_no_user_limit_on_either_charger() -> None:
    """The capture's own state, read the way the broker replays it.

    Each charger declares the limit, settable, and publishes no value for it, so
    each reads no limit, its own ceiling as the limit in force, and still a
    control. Asserted per charger, because the capture is the producer's account
    of r202639 and a parser that defaulted the absent limit to anything would
    report it here.
    """
    adapter = _adapter()
    snapshot = adapter.build_snapshot()

    for device_id in (EVSE, EVSE_2):
        declared = json.loads(_TREE[device_id]["$description"])["nodes"]["config"]["properties"]
        assert declared["user-max-charge-current"]["settable"] is True
        assert LIMIT_TOPIC not in _TREE[device_id]

        evse = snapshot.evse[_key(adapter, device_id)]

        assert evse.charge_current_limit_a is None
        assert evse.effective_charge_current_limit_a == int(_published(device_id, CEILING_TOPIC))
        assert evse.charge_current_limit_settable is True


def test_each_charger_reads_its_own_limit() -> None:
    """Two chargers, two different values, and neither may answer for the other.

    The capture sets no limit on either and publishes the same ceiling on both,
    so limits derived from it as-is would pass for a parser that read one
    charger and reported it twice. The values are set and made to differ first,
    which is the only shape of this test that proves anything.
    """
    first, second = _user_limit(EVSE), _user_limit(EVSE_2) - 8
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
    """A limit the user changes moves the reading on the same device, rather than holding the first one seen."""
    first = _user_limit(EVSE)
    moved = first - 4
    device = _evse_device(_tree(**{EVSE: {LIMIT_TOPIC: str(first)}}), EVSE)
    assert build_evse(device, {}, node_id=EVSE, feed_statuses={}).charge_current_limit_a == first

    device.update_property("config", "user-max-charge-current", str(moved))

    assert build_evse(device, {}, node_id=EVSE, feed_statuses={}).charge_current_limit_a == moved


def test_an_unpublished_value_is_none_rather_than_zero() -> None:
    """A charger that has not published yet has no limit, which is not 0 A."""
    evse = _snapshot_evse(_tree(**{EVSE: {LIMIT_TOPIC: None, CEILING_TOPIC: None}}), EVSE)

    assert evse.charge_current_limit_a is None
    assert evse.charge_current_ceiling_a is None
    # Still declared, so still writable: the value is missing, not the property.
    assert evse.charge_current_limit_settable is True


def test_no_user_limit_leaves_the_ceiling_in_force() -> None:
    """From r202639 the limit is published only once a user sets one.

    Absent, the ceiling is the limit the charger applies, and the property is
    still declared, so still a control.
    """
    evse = _snapshot_evse(_tree(**{EVSE: {LIMIT_TOPIC: None}}), EVSE)

    assert evse.charge_current_limit_a is None
    assert evse.effective_charge_current_limit_a == int(_published(EVSE, CEILING_TOPIC))
    assert evse.charge_current_limit_settable is True


def test_no_limit_and_no_ceiling_has_no_effective_limit() -> None:
    evse = _snapshot_evse(_tree(**{EVSE: {LIMIT_TOPIC: None, CEILING_TOPIC: None}}), EVSE)

    assert evse.effective_charge_current_limit_a is None


def test_a_limit_equal_to_the_ceiling_reads_the_same_as_none() -> None:
    """The value older firmware filled in on its own, possibly still retained
    after the upgrade cleared it. It and an absent limit must agree."""
    ceiling = _published(EVSE, CEILING_TOPIC)
    retained = _snapshot_evse(_tree(**{EVSE: {LIMIT_TOPIC: ceiling}}), EVSE)
    cleared = _snapshot_evse(_tree(**{EVSE: {LIMIT_TOPIC: None}}), EVSE)

    assert retained.effective_charge_current_limit_a == int(ceiling)
    assert retained.effective_charge_current_limit_a == cleared.effective_charge_current_limit_a


def test_a_user_limit_below_the_ceiling_is_the_effective_limit() -> None:
    lowered = int(_published(EVSE, CEILING_TOPIC)) - 8
    evse = _snapshot_evse(_tree(**{EVSE: {LIMIT_TOPIC: str(lowered)}}), EVSE)

    assert evse.effective_charge_current_limit_a == lowered


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
    current = _user_limit(EVSE)
    device = _evse_device(_tree(**{EVSE: {LIMIT_TOPIC: str(current)}}), EVSE)
    pending = current - 8
    device.update_property_target("config", "user-max-charge-current", str(pending))

    evse = build_evse(device, {}, node_id=EVSE, feed_statuses={})

    assert evse.charge_current_limit_target_a == pending
    assert evse.charge_current_limit_a == current


def test_a_first_limit_still_pending_shows_as_a_target_and_no_reading() -> None:
    """The capture's own state with a user's first write in flight.

    The target shows at once; the limit stays unread until the charger
    publishes one, and the ceiling stays the limit in force meanwhile.
    """
    device = _evse_device(_TREE, EVSE)
    pending = _user_limit(EVSE)
    device.update_property_target("config", "user-max-charge-current", str(pending))

    evse = build_evse(device, {}, node_id=EVSE, feed_statuses={})

    assert evse.charge_current_limit_target_a == pending
    assert evse.charge_current_limit_a is None
    assert evse.effective_charge_current_limit_a == int(_published(EVSE, CEILING_TOPIC))


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
    unrenamed tree rather than against literals, so the two paths are held to
    each other and not merely to the same numbers: once with a limit set, and
    once as captured, with none.
    """
    limit = _user_limit(EVSE)
    valued = _tree(**{EVSE: {LIMIT_TOPIC: str(limit)}})
    catalogued = _snapshot_evse(_renamed_to_catalog(EVSE, valued), EVSE)

    assert catalogued == _snapshot_evse(valued, EVSE)
    assert catalogued.charge_current_limit_a == limit
    assert _snapshot_evse(_renamed_to_catalog(EVSE), EVSE) == _snapshot_evse(_TREE, EVSE)


def test_the_catalogued_spelling_is_written_to_its_own_topic() -> None:
    adapter = _adapter(_renamed_to_catalog(EVSE))
    key = _key(adapter, EVSE)

    assert adapter.set_evse_charge_limit_target(key).topic == f"ebus/5/{EVSE}/charge-limit/owner-limit/set"


def test_the_catalogued_spelling_wins_where_both_are_declared() -> None:
    """A charger mid-migration declares both; the specified one is authoritative.

    Not a hypothetical: a rename lands in firmware by adding the new node before
    retiring the old, and a reader that took whichever it saw first would flip
    between them on the strength of dict ordering.
    """
    limit = str(_user_limit(EVSE))
    tree = _renamed_to_catalog(EVSE, _tree(**{EVSE: {LIMIT_TOPIC: limit}}))
    stale = json.loads(_TREE[EVSE]["$description"])["nodes"]["config"]
    description = json.loads(tree[EVSE]["$description"])
    description["nodes"]["config"] = stale
    tree[EVSE]["$description"] = json.dumps(description)
    tree[EVSE][CEILING_TOPIC] = _published(EVSE, CEILING_TOPIC)
    tree[EVSE][LIMIT_TOPIC] = limit

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
    assert adapter.set_evse_charge_limit_target(key).topic == f"ebus/5/{EVSE}/config/user-max-charge-current/set"


def test_each_charger_gets_its_own_set_topic() -> None:
    adapter = _adapter()

    assert adapter.set_evse_charge_limit_target(_key(adapter, EVSE)).topic == (
        f"ebus/5/{EVSE}/config/user-max-charge-current/set"
    )
    assert adapter.set_evse_charge_limit_target(_key(adapter, EVSE_2)).topic == (
        f"ebus/5/{EVSE_2}/config/user-max-charge-current/set"
    )


def test_no_topic_for_a_charger_that_does_not_declare_the_limit_settable() -> None:
    """The refusal. A property with no `$settable` is not a control, and naming a
    topic for it would put a write on the wire the panel never offered."""
    adapter = _adapter(_without_settable(EVSE))
    key = _key(adapter, EVSE)

    assert adapter.set_evse_charge_limit_target(key) is None
    assert adapter.evse_charge_limit_payload(key, 16) is None


def test_no_topic_for_a_charger_with_no_charge_limit_node() -> None:
    adapter = _adapter(_without_node(EVSE))
    key = _key(adapter, EVSE)

    assert adapter.set_evse_charge_limit_target(key) is None
    assert adapter.evse_charge_limit_payload(key, 16) is None


def test_no_topic_for_a_charger_the_panel_does_not_have() -> None:
    adapter = _adapter()

    assert adapter.set_evse_charge_limit_target("not-a-charger") is None
    assert adapter.evse_charge_limit_payload("not-a-charger", 16) is None


def test_a_value_at_or_below_the_ceiling_is_published_as_it_is() -> None:
    adapter = _adapter()
    key = _key(adapter, EVSE)
    ceiling = int(_published(EVSE, CEILING_TOPIC))

    assert adapter.evse_charge_limit_payload(key, ceiling) == str(ceiling)
    assert adapter.evse_charge_limit_payload(key, ceiling - 16) == str(ceiling - 16)
    assert adapter.evse_charge_limit_payload(key, _declared_minimum(EVSE)) == str(_declared_minimum(EVSE))


def _declared_minimum(device_id: str) -> int:
    """The lower bound the capture's limit declares in `$format` (`<min>:<max>`)."""
    declared = json.loads(_TREE[device_id]["$description"])["nodes"]["config"]["properties"]
    return int(declared["user-max-charge-current"]["format"].split(":")[0])


def _with_limit_format(
    fmt: str | None, device_id: str = EVSE, base: dict[str, dict[str, str]] | None = None
) -> dict[str, dict[str, str]]:
    """The capture (or `base`, a tree built from it) with the limit's declared `$format` replaced, or removed where `None`."""
    source = _TREE if base is None else base
    topics = dict(source[device_id])
    description = json.loads(topics["$description"])
    declaration = description["nodes"]["config"]["properties"]["user-max-charge-current"]
    if fmt is None:
        declaration.pop("format")
    else:
        declaration["format"] = fmt
    topics["$description"] = json.dumps(description)
    tree = {other: dict(values) for other, values in source.items()}
    tree[device_id] = topics
    return tree


def test_below_the_declared_minimum_is_refused() -> None:
    """The panel declares the range it accepts (`6:<maximum>`); a value under it
    is not a smaller limit but one the charger does not take."""
    adapter = _adapter()
    key = _key(adapter, EVSE)
    minimum = _declared_minimum(EVSE)

    assert adapter.evse_charge_limit_payload(key, minimum - 1) is None
    assert adapter.evse_charge_limit_payload(key, 0) is None


def test_the_declared_minimum_itself_is_accepted() -> None:
    adapter = _adapter()
    key = _key(adapter, EVSE)
    minimum = _declared_minimum(EVSE)

    assert adapter.evse_charge_limit_payload(key, minimum) == str(minimum)


def test_a_declared_maximum_tighter_than_the_ceiling_is_enforced() -> None:
    ceiling = int(_published(EVSE, CEILING_TOPIC))
    adapter = _adapter(_with_limit_format(f"6:{ceiling - 8}"))
    key = _key(adapter, EVSE)

    assert adapter.evse_charge_limit_payload(key, ceiling - 8) == str(ceiling - 8)
    assert adapter.evse_charge_limit_payload(key, ceiling - 7) is None


def test_a_range_with_an_open_side_enforces_only_the_other() -> None:
    low_open = _adapter(_with_limit_format(":20"))
    assert low_open.evse_charge_limit_payload(_key(low_open, EVSE), 0) == "0"
    assert low_open.evse_charge_limit_payload(_key(low_open, EVSE), 21) is None

    high_open = _adapter(_with_limit_format("6:"))
    ceiling = int(_published(EVSE, CEILING_TOPIC))
    assert high_open.evse_charge_limit_payload(_key(high_open, EVSE), 5) is None
    assert high_open.evse_charge_limit_payload(_key(high_open, EVSE), ceiling) == str(ceiling)
    assert high_open.evse_charge_limit_payload(_key(high_open, EVSE), ceiling + 1) is None


def test_no_declared_format_keeps_the_ceiling_as_the_only_bound() -> None:
    adapter = _adapter(_with_limit_format(None))
    key = _key(adapter, EVSE)
    ceiling = int(_published(EVSE, CEILING_TOPIC))

    assert adapter.evse_charge_limit_payload(key, 0) == "0"
    assert adapter.evse_charge_limit_payload(key, ceiling) == str(ceiling)
    assert adapter.evse_charge_limit_payload(key, ceiling + 1) is None


@pytest.mark.parametrize(
    "fmt", ["garbage", "6", "a:b", "6:32:1", "6.5:32", "UNLOCKED,LOCKED", "1_0:32", "+6:32", "6:+32", "\u0666:32", "32:6"]
)
def test_an_uninterpretable_format_keeps_the_ceiling_as_the_only_bound(fmt: str) -> None:
    adapter = _adapter(_with_limit_format(fmt))
    key = _key(adapter, EVSE)
    ceiling = int(_published(EVSE, CEILING_TOPIC))

    assert adapter.evse_charge_limit_payload(key, 0) == "0"
    assert adapter.evse_charge_limit_payload(key, ceiling + 1) is None


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
    # A declared range would bound 1000 A too; this test is about a charger that
    # declares neither a ceiling nor a range, which nothing bounds.
    description["nodes"]["config"]["properties"]["user-max-charge-current"].pop("format")
    topics["$description"] = json.dumps(description)
    topics.pop(CEILING_TOPIC)
    tree = {other: dict(values) for other, values in _TREE.items()}
    tree[EVSE] = topics

    adapter = _adapter(tree)
    key = _key(adapter, EVSE)

    assert adapter.evse_charge_limit_payload(key, 1000) == "1000"
    assert adapter.set_evse_charge_limit_target(key).topic == f"ebus/5/{EVSE}/config/user-max-charge-current/set"


def test_a_declared_maximum_bounds_a_charger_that_publishes_no_ceiling() -> None:
    """With no ceiling the declared `$format` maximum is still a published bound."""
    topics = dict(_TREE[EVSE])
    description = json.loads(topics["$description"])
    description["nodes"]["config"]["properties"].pop("max-charge-current")
    topics["$description"] = json.dumps(description)
    topics.pop(CEILING_TOPIC)
    tree = {other: dict(values) for other, values in _TREE.items()}
    tree[EVSE] = topics

    adapter = _adapter(tree)
    key = _key(adapter, EVSE)
    maximum = int(
        json.loads(topics["$description"])["nodes"]["config"]["properties"]["user-max-charge-current"]["format"].split(":")[
            1
        ]
    )

    assert adapter.evse_charge_limit_payload(key, maximum) == str(maximum)
    assert adapter.evse_charge_limit_payload(key, maximum + 1) is None


# ---------------------------------------------------------------------------
# The declared range — what a control may offer, reported before any write
# ---------------------------------------------------------------------------


def _range(evse: object) -> tuple[int | None, int | None]:
    return evse.charge_current_limit_minimum_a, evse.charge_current_limit_maximum_a


def _declared_range(device_id: str) -> tuple[int, int]:
    """The `<min>:<max>` the capture's limit declares, read from its `$description`."""
    declared = json.loads(_TREE[device_id]["$description"])["nodes"]["config"]["properties"]
    low, high = declared["user-max-charge-current"]["format"].split(":")
    return int(low), int(high)


def test_each_charger_reports_the_range_its_limit_declares() -> None:
    """The capture's own declarations, read the way the broker replays them.

    Each charger declares `6:32`; a control bounded by anything else offers a
    value the write path then refuses.
    """
    adapter = _adapter()
    snapshot = adapter.build_snapshot()

    for device_id in (EVSE, EVSE_2):
        assert _declared_range(device_id) == (6, 32)
        assert _range(snapshot.evse[_key(adapter, device_id)]) == _declared_range(device_id)


def test_the_range_is_not_the_ceiling() -> None:
    """The declared maximum and the commissioned ceiling are separate readings.

    The ceiling is lowered below the declared maximum so a mapper that reported
    the ceiling as the range, or the range as the ceiling, would show here.
    """
    lowered = _declared_range(EVSE)[1] - 8
    evse = _snapshot_evse(_tree(**{EVSE: {CEILING_TOPIC: str(lowered)}}), EVSE)

    assert evse.charge_current_ceiling_a == lowered
    assert _range(evse) == _declared_range(EVSE)


def test_each_charger_reports_its_own_range() -> None:
    """Both chargers declare the same range in the capture, so they are made to differ first."""
    tree = _with_limit_format("8:16", EVSE_2, base=_with_limit_format("6:24", EVSE))
    adapter = _adapter(tree)
    snapshot = adapter.build_snapshot()

    assert _range(snapshot.evse[_key(adapter, EVSE)]) == (6, 24)
    assert _range(snapshot.evse[_key(adapter, EVSE_2)]) == (8, 16)


def test_no_declared_format_reports_no_range() -> None:
    assert _range(_snapshot_evse(_with_limit_format(None), EVSE)) == (None, None)


@pytest.mark.parametrize(
    "fmt", ["garbage", "6", "a:b", "6:32:1", "6.5:32", "UNLOCKED,LOCKED", "1_0:32", "+6:32", "6:+32", "٦:32", "32:6"]
)
def test_an_uninterpretable_or_inverted_format_reports_no_range(fmt: str) -> None:
    """The same formats the write path refuses to read bound nothing here either."""
    assert _range(_snapshot_evse(_with_limit_format(fmt), EVSE)) == (None, None)


def test_an_open_side_is_none_on_that_side_only() -> None:
    assert _range(_snapshot_evse(_with_limit_format(":20"), EVSE)) == (None, 20)
    assert _range(_snapshot_evse(_with_limit_format("6:"), EVSE)) == (6, None)


def test_a_float_limit_reports_no_range() -> None:
    """Only an `integer` declaration's range is read, as on the write path."""
    topics = dict(_TREE[EVSE])
    description = json.loads(topics["$description"])
    description["nodes"]["config"]["properties"]["user-max-charge-current"]["datatype"] = "float"
    topics["$description"] = json.dumps(description)
    tree = {other: dict(values) for other, values in _TREE.items()}
    tree[EVSE] = topics

    assert _range(_snapshot_evse(tree, EVSE)) == (None, None)


def test_a_charger_with_no_charge_limit_node_reports_no_range() -> None:
    assert _range(_snapshot_evse(_without_node(EVSE), EVSE)) == (None, None)


def test_the_range_is_reported_whether_or_not_the_limit_is_settable() -> None:
    """A declaration, reported as declared; `charge_current_limit_settable` is the separate answer to whether to offer it.

    The same rule `lock_state_options` follows for a lock the charger does not
    accept writes to.
    """
    evse = _snapshot_evse(_without_settable(EVSE), EVSE)

    assert evse.charge_current_limit_settable is False
    assert _range(evse) == _declared_range(EVSE)


def test_the_catalogued_spelling_reports_the_same_range() -> None:
    catalogued = _snapshot_evse(_renamed_to_catalog(EVSE), EVSE)

    assert _range(catalogued) == _declared_range(EVSE)


def test_the_range_does_not_wait_for_a_value() -> None:
    """The range is in the declaration, so it is reported before the charger publishes either half."""
    evse = _snapshot_evse(_tree(**{EVSE: {LIMIT_TOPIC: None, CEILING_TOPIC: None}}), EVSE)

    assert evse.charge_current_ceiling_a is None
    assert _range(evse) == _declared_range(EVSE)


def _captured_ranges(stem: str) -> list[tuple[int, int]]:
    """The `<min>:<max>` each charger in a reference capture declares for its limit."""
    tree = capture_tree(stem)
    declared = []
    for device_id in tree.devices:
        if tree.device_type(device_id) != _EVSE_TYPE:
            continue
        properties = json.loads(tree.devices[device_id].description)["nodes"]["config"]["properties"]
        low, high = properties["user-max-charge-current"]["format"].split(":")
        declared.append((int(low), int(high)))
    return sorted(declared)


@pytest.mark.parametrize("stem", CAPTURES)
def test_each_captured_charger_reports_the_range_it_declares(stem: str) -> None:
    """Every reference capture, against its own declarations rather than the reference tree's."""
    snapshot = capture_snapshot(stem)

    assert sorted(_range(charger) for charger in snapshot.evse.values()) == _captured_ranges(stem)


def test_the_captures_hold_a_declared_range() -> None:
    """Without one, the test above compares two empty lists and proves nothing."""
    assert any(_captured_ranges(stem) for stem in CAPTURES)


# ---------------------------------------------------------------------------
# The transport — what actually reaches the wire
# ---------------------------------------------------------------------------


def _client(adapter: SchemaOneAdapter) -> tuple[object, MagicMock]:
    from span_panel_api.mqtt.client import MqttClientConfig, SpanMqttClient

    config = MqttClientConfig(broker_host="h", username="u", password="p")
    client = SpanMqttClient(
        host="192.168.1.1",
        serial_number=PANEL,
        broker_config=config,
        control_deadlines=FAST_CONTROL_DEADLINES,
    )
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
    # Only the renamed charger is described. With the second charger present it
    # would answer for the first, and the metadata would resolve with the
    # catalogued spelling unrecognised.
    tree = _renamed_to_catalog(EVSE)
    tree.pop(EVSE_2)

    metadata = _metadata(tree)

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
