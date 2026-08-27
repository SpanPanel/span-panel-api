"""A control the panel declares non-commandable produces no target to publish to.

**The rule is the specification's.** `switch` 0.3, vendored at
`packages/schema-1/spec/catalogs/switch.json`, declares `relay` "Settable when
`relay-controllable = true`" and defines `relay-controllable` false as locked. So
this is not a behaviour inferred from what one producer happens to emit; it is
the catalogued contract, and the first test below reads the catalog and says so,
which is what stops the rule outliving the sentence it came from.

The refusal was in the tree long before it was in the code: the adapter derived
`is_user_controllable` and `is_never_backup` correctly for the snapshot, while
`set_circuit_relay_target` formatted a topic from a device id and consulted
nothing. A consumer gating entity creation on the snapshot hid that, but only
for entities -- these are public methods, and settability changes at runtime when
a circuit is re-commissioned in place.

Driven from the shipped capture wherever the capture has the case, and from a
mutated copy of it where it does not. The distinction matters: the locked relay
below is what `ebus-panel-sim` 0.7.0 -- the specification's own executable
publisher -- emits for a circuit commissioned `non-controllable`, so those tests
are evidence that the catalogued rule and a conforming producer agree. The
never-backup circuit is a mutation, because no circuit in the capture is
commissioned that way, and it says so.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from reference_payloads.schema_one import RetainedTopicTree, parent_child_tree
from span_panel_api.models import V2HomieSchema
from span_panel_api_schema_1 import SchemaOneAdapter

_CATALOGS = Path(__file__).parent.parent / "packages" / "schema-1" / "spec" / "catalogs"

PANEL = "example-40t-001"
LOCKED_CIRCUIT = "573066aaddd7b75114c4563ce3af18c4"
"""Solar Inverter, commissioned `non-controllable`: `relay-controllable` is
`false` and `switch/relay` carries no `$settable`."""

CONTROLLABLE_CIRCUIT = "0ab966b95f92a6a51ec548485aa85f54"
"""Kitchen Lights, its ordinary sibling."""


def _schema() -> V2HomieSchema:
    return V2HomieSchema(
        firmware_version="spanos2/r202633/01",
        types_schema_hash="sha256:test",
        types={},
        data_model_version="1.0",
    )


def _adapter(tree: RetainedTopicTree) -> SchemaOneAdapter:
    """Replay a whole capture into an adapter, panel first.

    Panel first because the SDK gates a child's subscription on its parent
    reaching `ready`.
    """
    adapter = SchemaOneAdapter(PANEL, _schema())
    for device_id in [PANEL, *[d for d in tree if d != PANEL]]:
        topics = tree[device_id]
        adapter.handle_message(f"ebus/5/{device_id}/$description", topics["$description"])
        adapter.handle_message(f"ebus/5/{device_id}/$state", topics.get("$state", "ready"))
        for topic, payload in topics.items():
            if not topic.startswith("$"):
                adapter.handle_message(f"ebus/5/{device_id}/{topic}", payload)
    return adapter


def _copy() -> dict[str, dict[str, str]]:
    """A writable copy of the capture.

    Copied rather than mutated in place because `parent_child_tree()` reads the
    capture once per process and every other module in the suite is reading the
    same object.
    """
    return {device_id: dict(topics) for device_id, topics in parent_child_tree().items()}


def _redeclared(device_id: str, node: str, prop: str, *, settable: bool | None) -> dict[str, dict[str, str]]:
    """The capture with one property's `$settable` set, or removed when None.

    Expressed as an edit to the declaration rather than as a hand-written
    description, so the mutation is one attribute away from what the producer
    published and everything else about the device stays the producer's.
    """
    tree = _copy()
    description = json.loads(tree[device_id]["$description"])
    definition: dict[str, object] = description["nodes"][node]["properties"][prop]
    if settable is None:
        definition.pop("settable", None)
    else:
        definition["settable"] = settable
    tree[device_id]["$description"] = json.dumps(description)
    return tree


@pytest.fixture(name="adapter")
def _shipped_adapter() -> SchemaOneAdapter:
    return _adapter(parent_child_tree())


# ---------------------------------------------------------------------------
# Where the rule comes from
# ---------------------------------------------------------------------------


def _catalogued(capability: str, property_id: str) -> dict[str, object]:
    with (_CATALOGS / f"{capability}.json").open(encoding="utf-8") as handle:
        catalog = json.load(handle)
    definition: dict[str, object] = catalog["properties"][property_id]
    return definition


def test_the_catalog_still_states_the_condition_this_refusal_encodes() -> None:
    """The premise of the whole module, read from the specification we vendor.

    `relay_is_settable` implements a rule it cannot derive: `switch` 0.3 puts
    `settable: true` on `relay` unconditionally in the JSON and states the
    narrowing condition in the prose beside it, because the machine-readable
    field describes the property across the whole capability while the condition
    applies per device. There is nothing to compute, so the rule is written into
    the code -- and a rule written into code outlives the sentence it came from
    unless something checks.

    This is that check, and it is deliberately about the *catalog* rather than
    about any producer. If the specification rewords this clause, update the
    substring here and move on. If it **removes** the condition, or redefines
    what `relay-controllable` false means, that is a decision to re-take: the
    refusal in `relay_is_settable` would no longer have a source, and no test
    that reads a capture could tell you so.
    """
    relay = _catalogued("switch", "relay")
    controllable = _catalogued("switch", "relay-controllable")

    assert relay["settable"] is True, "the catalog no longer declares `relay` settable at all"
    assert "Settable when `relay-controllable = true`" in str(relay["description"]), (
        "`switch` no longer conditions `relay`'s settability on `relay-controllable`. "
        "That condition is the entire basis for refusing a relay command in "
        "`circuits.relay_is_settable`; re-read the catalog before adjusting either."
    )
    assert "locked" in str(controllable["description"]), (
        "`relay-controllable` no longer describes false as locked, which is the half of "
        "the rule that says a refusal is correct rather than merely cautious."
    )


def test_the_catalog_puts_no_such_condition_on_the_shed_priority() -> None:
    """Why the relay reads a second signal and the priority reads one.

    Both properties answer an absent `$settable` the same way — Homie 5's
    default, false — so the asymmetry is not in the default. It is in what else
    there is to read: `switch` narrows `relay` by a *value* property, so
    `relay_is_settable` reads `relay-controllable` alongside the declaration,
    while `load-shed` states no condition on `priority` and there is no second
    signal to consult. A condition appearing here would mean `priority_is_settable`
    is now reading half of its rule.
    """
    priority = _catalogued("load-shed", "priority")

    assert priority["settable"] is True
    assert "settable" not in str(priority["description"]).lower(), (
        "`load-shed` has grown a condition on `priority`'s settability. "
        "`circuits.priority_is_settable` reads the declaration alone on the strength of "
        "there being no condition to read; that is now a decision to re-take."
    )


# ---------------------------------------------------------------------------
# The relay, which is what the capture already carries
# ---------------------------------------------------------------------------


def test_the_capture_carries_a_locked_relay_and_a_controllable_one(adapter: SchemaOneAdapter) -> None:
    """The premise of everything below, asserted rather than assumed.

    Both halves: a fixture regenerated with every circuit controllable would
    make the refusal tests pass by having nothing to refuse, and a fixture with
    every circuit locked would make the permission tests vacuous. Either failure
    reads as a producer change here rather than as a mystery three tests down.
    """
    tree = parent_child_tree()

    locked = json.loads(tree[LOCKED_CIRCUIT]["$description"])["nodes"]["switch"]["properties"]["relay"]
    controllable = json.loads(tree[CONTROLLABLE_CIRCUIT]["$description"])["nodes"]["switch"]["properties"]["relay"]

    assert "settable" not in locked
    assert tree[LOCKED_CIRCUIT]["switch/relay-controllable"] == "false"
    assert controllable["settable"] is True
    assert tree[CONTROLLABLE_CIRCUIT]["switch/relay-controllable"] == "true"


def test_a_locked_relay_yields_no_target(adapter: SchemaOneAdapter) -> None:
    assert adapter.set_circuit_relay_target(LOCKED_CIRCUIT) is None


def test_a_controllable_sibling_still_yields_one(adapter: SchemaOneAdapter) -> None:
    """The refusal has to be about the circuit, not about the control."""
    target = adapter.set_circuit_relay_target(CONTROLLABLE_CIRCUIT)

    assert target is not None
    assert target.topic == f"ebus/5/{CONTROLLABLE_CIRCUIT}/switch/relay/set"
    assert (target.device_id, target.node_id, target.property_id) == (CONTROLLABLE_CIRCUIT, "switch", "relay")


def test_a_relay_target_exists_exactly_where_relay_controllable_is_true(adapter: SchemaOneAdapter) -> None:
    """The invariant the hardware holds, asserted over every circuit at once.

    Across the two production enclosures we hold captures from -- 27 circuits --
    `$settable` on `switch/relay` is present exactly when `relay-controllable` is
    `true`, without exception. That makes the published value a sufficient
    predictor of whether a target should exist, and asserting it over the whole
    capture catches a refusal that is right on one circuit for the wrong reason.
    """
    tree = parent_child_tree()
    circuits = {
        device_id: topics
        for device_id, topics in tree.items()
        if json.loads(topics["$description"])["type"].endswith(".circuit")
    }
    assert len(circuits) == 5

    for device_id, topics in circuits.items():
        controllable = topics["switch/relay-controllable"] == "true"
        assert (adapter.set_circuit_relay_target(device_id) is not None) is controllable, device_id


def test_either_signal_alone_is_enough_to_refuse() -> None:
    """Refuses when either says no, which is the point of reading both.

    SPAN reports a firmware defect in which the `$settable` re-toggle on the
    runtime re-commissioning path is skipped until the service restarts, so the
    declaration and the value can disagree on a real panel. Each disagreement is
    built here from the controllable circuit, one signal at a time, so neither
    test can pass on the other signal's account.
    """

    declaration_stale = _adapter(_redeclared(CONTROLLABLE_CIRCUIT, "switch", "relay", settable=None))
    assert declaration_stale.set_circuit_relay_target(CONTROLLABLE_CIRCUIT) is None

    tree = _copy()
    tree[CONTROLLABLE_CIRCUIT]["switch/relay-controllable"] = "false"
    value_stale = _adapter(tree)
    assert value_stale.set_circuit_relay_target(CONTROLLABLE_CIRCUIT) is None


def test_a_circuit_the_tree_does_not_carry_yields_no_target(adapter: SchemaOneAdapter) -> None:
    """`_target` is string formatting, so an unknown id used to produce a topic."""
    assert adapter.set_circuit_relay_target("0" * 32) is None
    assert adapter.set_circuit_priority_target("0" * 32) is None


# ---------------------------------------------------------------------------
# The priority, which the capture has no case for
# ---------------------------------------------------------------------------


def test_priority_stays_settable_on_a_locked_relay(adapter: SchemaOneAdapter) -> None:
    """The one combination that would be easy to conflate, and real panels have it.

    `switch` 0.3 and `load-shed` 0.3 scope the two separately, and a circuit
    commissioned always-on is not thereby commissioned never-backup. Refusing
    the priority alongside the relay would take a control away from every locked
    circuit on every panel.
    """
    assert adapter.set_circuit_relay_target(LOCKED_CIRCUIT) is None

    target = adapter.set_circuit_priority_target(LOCKED_CIRCUIT)
    assert target is not None
    assert target.topic == f"ebus/5/{LOCKED_CIRCUIT}/load-shed/priority/set"


def test_a_never_backup_circuit_yields_no_priority_target() -> None:
    """Mutated, because no circuit in the capture is commissioned never-backup."""

    adapter = _adapter(_redeclared(CONTROLLABLE_CIRCUIT, "load-shed", "priority", settable=False))

    assert adapter.set_circuit_priority_target(CONTROLLABLE_CIRCUIT) is None
    # And only the priority: the relay is a separate commissioning flag.
    assert adapter.set_circuit_relay_target(CONTROLLABLE_CIRCUIT) is not None


def test_an_unannounced_priority_settable_yields_no_target() -> None:
    """Omission is the announcement, the same way it is on the relay.

    Homie 5 defaults `$settable` to false and the eBus SDK's description builder
    emits the attribute only when the property is settable, so a conforming
    publisher describes a never-backup circuit by omitting it. Reading silence as
    permission resolved a write topic for precisely the circuits commissioned not
    to accept one, and the panel would have refused the publish.

    Only the priority: the relay is a separate commissioning flag and this
    circuit's is untouched.
    """

    adapter = _adapter(_redeclared(CONTROLLABLE_CIRCUIT, "load-shed", "priority", settable=None))

    assert adapter.set_circuit_priority_target(CONTROLLABLE_CIRCUIT) is None
    assert adapter.set_circuit_relay_target(CONTROLLABLE_CIRCUIT) is not None


def test_every_circuit_in_the_capture_announces_its_priority_settable(adapter: SchemaOneAdapter) -> None:
    """The premise of the two mutations above, asserted rather than assumed.

    Both build a locked priority by editing a declaration that the producer
    published as settable. A capture that stopped announcing the attribute would
    make the mutations indistinguishable from the shipped state and quietly turn
    `test_a_priority_target_exists_exactly_on_the_circuits` into an assertion
    that no circuit has a priority target at all.

    It is also the standing answer to the argument the permissive default was
    built on -- firmware that publishes no attribute at all. Every circuit here
    announces one, so no producer we hold a capture from ever needed it.
    """
    tree = parent_child_tree()
    circuits = {
        device_id: topics
        for device_id, topics in tree.items()
        if json.loads(topics["$description"])["type"].endswith(".circuit")
    }
    assert len(circuits) == 5, "the capture's circuit set has moved; this premise is no longer about what it was"

    for device_id, topics in circuits.items():
        definition = json.loads(topics["$description"])["nodes"]["load-shed"]["properties"]["priority"]
        assert definition.get("settable") is True, device_id


# ---------------------------------------------------------------------------
# A device that declares no such control at all
# ---------------------------------------------------------------------------

_NON_CIRCUITS = ("bess", "bess-mid", "pv", "lugs-upstream", "lugs-downstream")
"""Devices in the capture that carry no `load-shed` node at all."""


def test_the_capture_carries_devices_with_no_shed_priority(adapter: SchemaOneAdapter) -> None:
    """The premise, asserted rather than assumed: these devices declare nothing
    about a shed priority, so there is a property-undeclared case to refuse."""
    tree = parent_child_tree()

    for device_id in _NON_CIRCUITS:
        nodes = json.loads(tree[device_id]["$description"])["nodes"]
        assert "load-shed" not in nodes, device_id


@pytest.mark.parametrize("device_id", _NON_CIRCUITS)
def test_a_device_that_declares_no_priority_yields_no_priority_target(adapter: SchemaOneAdapter, device_id: str) -> None:
    """A BESS, a MID and the lugs declare no `load-shed` node at all.

    They have published no shed priority for anything to be settable *on*, and
    `_target` is pure string formatting from a device id — so this is the case
    that produced a write topic for a control the device never offered. It is
    asserted separately from the settability tests because it does not depend on
    them: an id in the tree is not a control, whatever any `$settable` says.
    """
    assert adapter.set_circuit_priority_target(device_id) is None
    assert adapter.set_circuit_relay_target(device_id) is None


def test_a_priority_target_exists_exactly_on_the_circuits(adapter: SchemaOneAdapter) -> None:
    """Over the whole capture at once, the way the relay invariant is asserted.

    Every device in the tree is addressable by id, and only the circuits declare
    a shed priority — so a target on anything else is a target for a control
    that device never offered, whatever its `$settable` would have defaulted to.
    """
    tree = parent_child_tree()

    for device_id, topics in tree.items():
        is_circuit = json.loads(topics["$description"])["type"].endswith(".circuit")
        assert (adapter.set_circuit_priority_target(device_id) is not None) is is_circuit, device_id


def test_a_declared_priority_missing_from_its_node_is_undeclared_too(adapter: SchemaOneAdapter) -> None:
    """The narrower half of the same case: the node is there, the property is not.

    Removing `priority` leaves `load-shed` declared, so nothing but the property
    lookup distinguishes this from the capture's ordinary circuit. It has to
    refuse for the same reason the BESS does -- there is no property to be
    settable.
    """
    tree = _copy()
    description = json.loads(tree[CONTROLLABLE_CIRCUIT]["$description"])
    del description["nodes"]["load-shed"]["properties"]["priority"]
    tree[CONTROLLABLE_CIRCUIT]["$description"] = json.dumps(description)

    assert _adapter(tree).set_circuit_priority_target(CONTROLLABLE_CIRCUIT) is None


# ---------------------------------------------------------------------------
# Telling "no such circuit" apart from "the circuit says no"
# ---------------------------------------------------------------------------


def test_has_circuit_answers_for_the_circuits_and_nothing_else(adapter: SchemaOneAdapter) -> None:
    """Membership of the circuit set, not of the topology.

    The transport asks this to name the refusal it is raising, and a BESS
    reported as a circuit would have its refusal read as "declares its relay
    non-commandable" -- a claim about a relay that device does not have.
    """
    assert adapter.has_circuit(CONTROLLABLE_CIRCUIT) is True
    assert adapter.has_circuit(LOCKED_CIRCUIT) is True, "locked is not absent"

    for device_id in _NON_CIRCUITS:
        assert adapter.has_circuit(device_id) is False, device_id
    assert adapter.has_circuit(PANEL) is False
    assert adapter.has_circuit("0" * 32) is False
