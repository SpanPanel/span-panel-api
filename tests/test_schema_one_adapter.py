"""The parent/child adapter behind the SchemaAdapter protocol.

Driven by replaying the captured tree through `handle_message`, which is exactly
how the transport feeds it — so these exercise the SDK's real discovery path
(root ready, then each child) rather than a stubbed tree.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from span_panel_api.adapters import _derive_required_members
from span_panel_api.models import V2HomieSchema
from span_panel_api.protocol import SchemaAdapter
from span_panel_api_schema_1 import SchemaOneAdapter

_TREE = json.loads((Path(__file__).parent / "fixtures" / "parent_child_tree.json").read_text(encoding="utf-8"))

PANEL = "example-40t-001"
SOLAR_CIRCUIT = "573066aaddd7b75114c4563ce3af18c4"


def _schema() -> V2HomieSchema:
    return V2HomieSchema(
        firmware_version="spanos2/r202633/01",
        types_schema_hash="sha256:test",
        types={},
        data_model_version="1.0",
    )


def _feed(adapter: SchemaOneAdapter, device_ids: list[str] | None = None, omit: tuple[str, ...] = ()) -> None:
    """Replay retained topics the way the broker would deliver them.

    The panel first by default, which is the friendly order — the SDK gates a
    child's subscription on its parent reaching `ready`, so anything earlier
    has no route yet. The transport holds those messages until the route
    appears; `test_children_before_the_panel` covers the unfriendly order,
    which a broker is equally entitled to replay.
    """
    for device_id in device_ids or [PANEL, *[d for d in _TREE if d != PANEL]]:
        topics = _TREE[device_id]
        prefix = f"ebus/5/{device_id}"
        adapter.handle_message(f"{prefix}/$description", topics["$description"])
        adapter.handle_message(f"{prefix}/$state", topics["$state"])
        for topic, value in topics.items():
            if not topic.startswith("$") and topic not in omit:
                adapter.handle_message(f"{prefix}/{topic}", value)


@pytest.fixture(name="adapter")
def _adapter() -> SchemaOneAdapter:
    adapter = SchemaOneAdapter(PANEL, _schema())
    _feed(adapter)
    return adapter


def test_it_satisfies_the_schema_adapter_protocol() -> None:
    missing = [m for m in _derive_required_members(SchemaAdapter) if not hasattr(SchemaOneAdapter, m)]

    assert missing == []
    assert SchemaOneAdapter.schema_major == "schema_1"
    assert SchemaOneAdapter.SUPPORTS_DATA_MODEL_VERSIONS == (">=1.0", "<2.0")


def test_construction_touches_no_connection() -> None:
    """The transport builds a parser before a connection exists, so this must
    work with nothing to talk to."""
    adapter = SchemaOneAdapter(PANEL, _schema())

    assert adapter.is_ready() is False


def test_one_broad_subscription_covers_the_whole_tree() -> None:
    """Children are peers of the panel in the topic tree, so the wildcard spans
    devices. The adapter is asked this once and cannot add more later."""
    assert SchemaOneAdapter(PANEL, _schema()).topics_to_subscribe() == ["ebus/5/#"]


def test_replaying_the_tree_makes_it_ready(adapter: SchemaOneAdapter) -> None:
    assert adapter.is_ready() is True


def test_children_before_the_panel_still_yields_the_whole_tree(adapter: SchemaOneAdapter) -> None:
    """A broker replays its retained store in whatever order it likes.

    Found by the live reconnect check, not by review: seeded in this order the
    panel parsed as ready with zero circuits — a complete, silent loss that
    reported itself as a healthy connection.
    """
    reversed_order = [*[d for d in _TREE if d != PANEL], PANEL]
    late = SchemaOneAdapter(PANEL, _schema())
    _feed(late, reversed_order)

    assert late.is_ready() is True
    assert _fingerprint(late) == _fingerprint(adapter)


def _fingerprint(adapter: SchemaOneAdapter) -> tuple[str, int, int, list[str]]:
    snapshot = adapter.build_snapshot()
    return (
        snapshot.serial_number,
        snapshot.panel_size,
        len(snapshot.circuits),
        sorted(snapshot.evse),
    )


def test_a_panel_that_never_becomes_ready_is_not_ready() -> None:
    """The SDK gates child subscription on the parent's ready edge, so a
    non-ready panel yields nothing — silently, which is why it is asserted."""
    adapter = SchemaOneAdapter(PANEL, _schema())
    prefix = f"ebus/5/{PANEL}"
    adapter.handle_message(f"{prefix}/$description", _TREE[PANEL]["$description"])
    adapter.handle_message(f"{prefix}/$state", "disconnected")

    assert adapter.is_ready() is False


def test_a_root_whose_children_are_still_arriving_is_not_ready() -> None:
    """The root reaches ready as soon as *its own* description lands.

    Trusting that hands the transport a panel with a few circuits and no model
    — which it reports as a healthy connection. Found by the live reconnect
    check: the first connect parsed 4 of 37 circuits and nothing said so.
    """
    adapter = SchemaOneAdapter(PANEL, _schema())
    _feed(adapter, [PANEL])

    assert adapter.is_ready() is False


def test_readiness_goes_back_to_false_when_the_panel_declares_a_new_child(adapter: SchemaOneAdapter) -> None:
    """Readiness is a reconciling predicate, not a barrier that latches.

    A Homie tree grows out of band: commission a circuit and the panel
    republishes a `$description` naming a child nobody has heard from. The
    common consumer defect is to treat the first ready as settled and stop
    reconciling, so the new device is never seen — a failure that moves from
    startup to steady state, which makes it harder to find rather than less
    real. `ebus-sdk`'s `doc/consuming-a-homie-tree.md` names it the one-shot
    barrier.

    The transport consults `is_ready()` on every snapshot, so this must fall
    back to False and recover once the newcomer describes itself.
    """
    assert adapter.is_ready() is True

    description = json.loads(_TREE[PANEL]["$description"])
    description["children"] = [*description.get("children", []), "circuit-38"]
    adapter.handle_message(f"ebus/5/{PANEL}/$description", json.dumps(description))

    assert adapter.is_ready() is False, "a declared but unheard-of child left readiness latched True"

    adapter.handle_message(
        "ebus/5/circuit-38/$description",
        json.dumps({"homie": "5.0", "name": "New circuit", "type": "energy.ebus.device.circuit", "nodes": {}}),
    )
    adapter.handle_message("ebus/5/circuit-38/$state", "ready")

    assert adapter.is_ready() is True, "readiness did not recover once the new child described itself"


def test_an_offline_child_does_not_block_readiness(adapter: SchemaOneAdapter) -> None:
    """A commissioned DER that is unplugged publishes `lost` but keeps its
    retained description. A panel must not fail to connect over it."""
    adapter.handle_message("ebus/5/bess/$state", "lost")

    assert adapter.is_ready() is True


def test_readiness_waits_for_the_model_the_panel_declared() -> None:
    """Panel size comes from nowhere else, and a snapshot built a moment early
    reports zero spaces — which erases every unmapped position rather than
    mis-stating a number."""
    adapter = SchemaOneAdapter(PANEL, _schema())
    _feed(adapter, omit=("info/model",))

    assert adapter.is_ready() is False

    adapter.handle_message(f"ebus/5/{PANEL}/info/model", _TREE[PANEL]["info/model"])

    assert adapter.is_ready() is True
    assert adapter.build_snapshot().panel_size == 40


def test_a_panel_that_declares_no_model_still_connects() -> None:
    """Waiting for a property the firmware never promised would make one
    missing field fatal. The drift warning already covers the consequence."""
    description = json.loads(_TREE[PANEL]["$description"])
    del description["nodes"]["info"]["properties"]["model"]
    adapter = SchemaOneAdapter(PANEL, _schema())
    adapter.handle_message(f"ebus/5/{PANEL}/$description", json.dumps(description))
    adapter.handle_message(f"ebus/5/{PANEL}/$state", _TREE[PANEL]["$state"])
    _feed(adapter, [d for d in _TREE if d != PANEL])

    assert adapter.is_ready() is True
    assert adapter.build_snapshot().panel_size == 0


def test_snapshot_is_built_from_the_discovered_tree(adapter: SchemaOneAdapter) -> None:
    snapshot = adapter.build_snapshot()

    assert snapshot.serial_number == PANEL
    assert snapshot.panel_size == 40
    assert snapshot.circuits[SOLAR_CIRCUIT].name == "Solar Inverter"
    assert snapshot.battery.soe_percentage == pytest.approx(50.4104, rel=1e-4)
    # 5 circuits occupying 8 positions (two are multi-pole), so 32 remain.
    assert len(snapshot.circuits) == 37


def test_building_a_snapshot_before_discovery_fails_loudly() -> None:
    adapter = SchemaOneAdapter(PANEL, _schema())

    with pytest.raises(RuntimeError, match="not ready"):
        adapter.build_snapshot()


# ---------------------------------------------------------------------------
# Field metadata
# ---------------------------------------------------------------------------


def test_field_metadata_takes_units_from_the_tree(adapter: SchemaOneAdapter) -> None:
    metadata = adapter.build_field_metadata()

    assert metadata["circuit.instant_power_w"].unit == "W"
    assert metadata["circuit.instant_power_w"].datatype == "float"
    assert metadata["circuit.current_a"].unit == "A"
    assert metadata["panel.l1_voltage"].unit == "V"
    assert metadata["battery.soe_percentage"].unit == "%"


def test_no_property_declares_an_abstract_unit() -> None:
    """Units must reach Home Assistant renderable, not as a catalog token.

    eBus catalogs may carry an abstract `unit: "energy"` rather than a concrete
    one, which a device resolves in its own `$description`. Reading the runtime
    description is what keeps us clear of it — but only as long as the panel
    resolves it too, and the symptom if it stops is an entity whose unit reads
    the literal string. Asserted against the captured tree for the same reason
    the flat adapter asserts its schema facts: a silent absence needs a signal
    that does not depend on anyone noticing it.
    """
    abstract = {"energy", "power", "current", "voltage"}
    declared = {
        properties.get("unit")
        for device in _TREE.values()
        for node in json.loads(device["$description"]).get("nodes", {}).values()
        for properties in node.get("properties", {}).values()
    }

    assert not declared & abstract, f"abstract unit tokens in the captured tree: {sorted(declared & abstract)}"


def test_field_metadata_omits_fields_the_mapper_declines(adapter: SchemaOneAdapter) -> None:
    """Advertising a unit for a reading that never arrives would have the
    integration validate against a field nothing populates."""
    metadata = adapter.build_field_metadata()

    assert "panel.dominant_power_source" not in metadata
    assert "panel.grid_islandable" not in metadata
    assert "pv.relative_position" not in metadata


def test_field_metadata_is_empty_before_discovery() -> None:
    assert SchemaOneAdapter(PANEL, _schema()).build_field_metadata() == {}


# ---------------------------------------------------------------------------
# Commands — the adapter names the topic, the transport publishes it
# ---------------------------------------------------------------------------


def test_command_topics_address_the_child_device(adapter: SchemaOneAdapter) -> None:
    """Under parent/child a circuit is its own device, so its command topic is
    rooted at the circuit rather than nested under the panel."""
    assert adapter.set_circuit_relay_topic(SOLAR_CIRCUIT) == f"ebus/5/{SOLAR_CIRCUIT}/switch/relay/set"
    assert adapter.set_circuit_priority_topic(SOLAR_CIRCUIT) == f"ebus/5/{SOLAR_CIRCUIT}/load-shed/priority/set"


def test_dominant_power_source_writes_the_panel_assertion(adapter: SchemaOneAdapter) -> None:
    """It split in two; this is the settable half, on the panel's shed node.

    Returned None until 2026-08-08, which left a real capability unreachable:
    comms to the BESS drop, the grid returns, and the user has no way to assert
    that it is up so the BESS stops discharging. The panel offered the control
    the whole time — `shed/asserted-islanding-state`, `settable=True` — and the
    adapter simply never named it.
    """
    assert adapter.set_dominant_power_source_topic() == f"ebus/5/{PANEL}/shed/asserted-islanding-state/set"


def test_the_flat_vocabulary_is_translated_not_forwarded(adapter: SchemaOneAdapter) -> None:
    """The published protocol speaks flat's enum; the panel accepts a different one.

    Forwarding the caller's string would publish a value outside
    `NONE,ON_GRID,OFF_GRID` and the panel would reject it. The narrowing loses
    nothing real: six *source classes* were pressed into service as a manual
    override, and the override only ever needed on-grid, off-grid, or nothing.
    """
    assert adapter.dominant_power_source_payload("GRID") == "ON_GRID"

    for off_grid in ("BATTERY", "PV", "GENERATOR"):
        assert adapter.dominant_power_source_payload(off_grid) == "OFF_GRID", off_grid

    for no_assertion in ("NONE", "UNKNOWN"):
        assert adapter.dominant_power_source_payload(no_assertion) == "NONE", no_assertion


def test_an_unrecognised_value_is_refused_rather_than_guessed(adapter: SchemaOneAdapter) -> None:
    """None means "no legal representation", and the transport raises on it.

    Asserting an islanding state the user did not ask for is worse than refusing
    the command, because this control tells a BESS whether to keep discharging.
    """
    assert adapter.dominant_power_source_payload("SOLAR") is None
    assert adapter.dominant_power_source_payload("") is None


# ---------------------------------------------------------------------------
# Discovery helpers the transport uses
# ---------------------------------------------------------------------------


def test_circuits_missing_names_is_empty_once_retained_names_arrive(adapter: SchemaOneAdapter) -> None:
    assert adapter.circuit_nodes_missing_names() == []


def test_a_der_missing_its_declared_model_is_reported_alongside_circuits() -> None:
    """Readiness proves the tree's shape, not its labels.

    A DER's identity arrives as its own retained message, which can land after
    the last description — and the integration registers an HA device from the
    first snapshot, so a placeholder there is permanent until reload.
    """
    adapter = SchemaOneAdapter(PANEL, _schema())
    _feed(adapter, omit=("info/model",))
    adapter.handle_message(f"ebus/5/{PANEL}/info/model", _TREE[PANEL]["info/model"])

    assert "pv" in adapter.circuit_nodes_missing_names()

    adapter.handle_message("ebus/5/pv/info/model", _TREE["pv"]["info/model"])

    assert "pv" not in adapter.circuit_nodes_missing_names()


def test_find_node_by_type_answers_with_a_device_id(adapter: SchemaOneAdapter) -> None:
    assert adapter.find_node_by_type("energy.ebus.device.bess") == "bess"
    assert adapter.find_node_by_type("energy.ebus.device.nonexistent") is None


def test_property_callbacks_receive_updates() -> None:
    seen: list[tuple[str, str, str, str | None]] = []
    adapter = SchemaOneAdapter(PANEL, _schema())
    unregister = adapter.register_property_callback(lambda d, n, p, v: seen.append((d, n, p, v)))

    _feed(adapter, [PANEL])

    assert any(node == "status" and prop == "relay" for _, node, prop, _ in seen)

    unregister()
    before = len(seen)
    _feed(adapter, [PANEL])
    assert len(seen) == before
