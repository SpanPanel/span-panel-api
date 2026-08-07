"""Drive the parser end to end from what the simulator actually publishes.

Every other schema_1 test runs on `fixtures/parent_child_tree.json`, which was
captured off the upstream *generic* eBus panel simulator. That fixture is fine
for exercising the mapper, but it is not SPAN: it has never carried the
extensions and divergences that are SPAN's own vocabulary, which is precisely
the part a generic panel cannot produce.

This runs on a capture from SPAN's own publisher — the same panel the
conformance and coverage checks are written against — fed in exactly as the
transport feeds it: one retained message at a time, in whatever order the store
replays them.

**Values are deliberately not asserted.** The simulator's config carries
`noise_factor` and its clock advances, so power and current differ every capture.
Pinning a wattage here would produce a test that fails whenever the fixture is
refreshed, for a reason nobody can act on. What is asserted is what must hold for
any capture of a 40-space panel: that the parser reaches ready, sizes the panel,
finds every circuit, and populates the fields the integration consumes.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from span_panel_api.models import V2HomieSchema
from span_panel_api_schema_1 import SchemaOneAdapter

_WIRE = Path(__file__).parent.parent / "packages" / "schema-1" / "spec" / "fixtures" / "simulator_wire.json"
_PANEL = "sim-40t-001"
_TOPIC_PREFIX = "ebus/5"


def _schema() -> V2HomieSchema:
    return V2HomieSchema(
        firmware_version="spanos2/r202633/01",
        types_schema_hash="sha256:simulator-capture",
        types={},
        data_model_version="1.0",
    )


@pytest.fixture(name="adapter")
def _adapter() -> SchemaOneAdapter:
    """Feed the capture the way the broker replays it.

    Sorted by topic rather than tree order, on purpose: the retained store has no
    notion of parents before children, and the ordering bug fixed before 0.1.0b1
    was exactly a case of that assumption being made silently.
    """
    with _WIRE.open() as handle:
        capture: dict[str, dict[str, str]] = json.load(handle)

    adapter = SchemaOneAdapter(_PANEL, _schema())
    messages = [
        (f"{_TOPIC_PREFIX}/{device_id}/{key}", payload)
        for device_id, body in capture.items()
        for key, payload in body.items()
    ]
    for topic, payload in sorted(messages):
        adapter.handle_message(topic, payload)
    return adapter


def test_the_parser_reaches_ready_on_the_simulators_own_capture(adapter: SchemaOneAdapter) -> None:
    """The claim that matters: this parser can complete a connection to SPAN's
    publisher, not merely to a generic eBus panel."""
    assert adapter.is_ready(), "the parser never reached ready on a full capture of the simulator's tree"


def test_the_panel_is_sized_from_the_model_the_simulator_declares(adapter: SchemaOneAdapter) -> None:
    """Panel size drives the unmapped-position entries the integration builds
    from total-minus-occupied, so a wrong size is missing entities, not an error."""
    snapshot = adapter.build_snapshot()

    assert snapshot.panel_size == 40, "the simulator declares MAIN_40; PANEL_SIZE_BY_MODEL must know it"


def test_every_circuit_the_simulator_publishes_is_parsed(adapter: SchemaOneAdapter) -> None:
    """30 circuits in the tracked config; the remainder of the 40 spaces are the
    unmapped positions the integration expects to exist."""
    snapshot = adapter.build_snapshot()
    real = [circuit_id for circuit_id in snapshot.circuits if not circuit_id.startswith("unmapped_tab_")]

    assert len(real) == 30, f"expected the config's 30 circuits, parsed {len(real)}"
    assert all(snapshot.circuits[circuit_id].name for circuit_id in real), "a circuit arrived with no name"


def test_the_ders_declare_a_model_they_never_publish(adapter: SchemaOneAdapter) -> None:
    """A producer-side gap, pinned so it cannot fade into the background.

    Every DER the simulator publishes declares `info/model` in its
    `$description` and never sends a value for it. PV is the widest: it declares
    firmware-version, model, nominal-power, serial-number and vendor-name, and
    publishes vendor-name alone.

    That breaks the one standing obligation eBus places on a publisher — be
    self-describing, declare accurately what you publish — and it is the failure
    mode this parser's `circuit_nodes_missing_names()` exists to surface: a
    consumer waits on a value that is promised and never arrives, so the entity
    is created and never updates.

    Worth knowing that panelbench's own conformance checker **cannot** catch
    this. It compares declarations against catalogs, so a property declared and
    never published is conformant by construction. Only a capture that carries
    values can see it, which is the argument for this fixture existing.

    Pinned rather than asserted away: when the simulator publishes these, this
    test fails and the expectation gets deleted.
    """
    assert adapter.circuit_nodes_missing_names() == ["bess", "pv", "evse", "evse-2"], (
        "the set of devices declaring a model they never publish has changed. If the simulator "
        "now publishes them, delete this test and assert circuit_nodes_missing_names() is empty."
    )


def test_the_fields_the_integration_consumes_are_populated(adapter: SchemaOneAdapter) -> None:
    """Presence, not values. A field left None reaches a user as an entity that
    exists and never updates, which is the failure this whole exercise is about.
    """
    snapshot = adapter.build_snapshot()

    assert snapshot.instant_grid_power_w is not None
    assert snapshot.main_meter_energy_consumed_wh is not None
    assert snapshot.main_meter_energy_produced_wh is not None
    assert snapshot.battery.soe_percentage is not None
    assert snapshot.l1_voltage is not None
    assert snapshot.l2_voltage is not None


def test_field_metadata_covers_what_the_snapshot_carries(adapter: SchemaOneAdapter) -> None:
    """Metadata is read from each device's `$description`, so a capture is the
    only way to check it against a real publisher rather than against a schema
    document that describes every panel ever built."""
    metadata = adapter.build_field_metadata()

    assert metadata, "no field metadata was built from a full capture"
    assert all(
        entry.unit != "energy" for entry in metadata.values()
    ), "an abstract unit token reached field metadata; units must come from the device description"


def test_grid_state_is_absent_because_the_simulator_publishes_no_mid(adapter: SchemaOneAdapter) -> None:
    """The one gap, asserted rather than left to be noticed.

    `grid_state` reads the MID's `grid/islanding-state`. The simulator supports a
    MID fully — profile, resolvers, snapshot field — but nothing instantiates one,
    so no config produces it and this capture cannot exercise the mapping.

    Pinned as an expectation so that the day the simulator does publish a MID,
    this fails and says so, rather than the gap quietly persisting behind a
    passing suite. Its counterpart is `_NOT_EXERCISED_BY_SIMULATOR` in
    `test_schema_one_conformance.py`; both must be cleared together.
    """
    assert adapter.build_snapshot().grid_state is None, (
        "the simulator now publishes a MID. Drop this test, and drop grid/islanding-state "
        "from _NOT_EXERCISED_BY_SIMULATOR so the coverage check holds it instead."
    )
