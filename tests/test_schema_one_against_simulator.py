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


def test_no_der_declares_a_model_it_never_publishes(adapter: SchemaOneAdapter) -> None:
    """The `info/model` half of the producer gap, closed on 2026-08-08.

    This asserted `["bess", "pv", "evse", "evse-2"]` until the producer adopted
    the upstream emitter and its DER metadata keys. All four declared
    `info/model` and never sent a value.

    Scope is exactly `model`, because that is what `circuit_nodes_missing_names()`
    measures for a DER — `PROP_MODEL` declared with no value, alongside circuits
    missing `PROP_NAME`. The wider declared-but-unpublished question is
    `test_the_ders_still_declare_two_identity_fields_they_never_publish` below,
    which is not empty.

    The consumer symptom is specific: an entity is created from the declaration,
    waits for a value that never arrives, and never updates.

    Worth keeping the note that panelbench's own conformance checker **cannot**
    see this. It compares declarations against catalogs, so a property declared
    and never published is conformant by construction. Only a capture carrying
    values catches it, which remains the argument for this fixture.

    Asserted empty rather than deleted: zero is the state worth defending.
    """
    assert adapter.circuit_nodes_missing_names() == [], (
        "these devices declare info/model and never publish it, which creates entities "
        "that never update. This was empty as of the 2026-08-08 recapture, so it is a "
        "producer regression rather than a known gap."
    )


_DER_TYPES = frozenset(
    {
        "energy.ebus.device.bess",
        "energy.ebus.device.pv",
        "energy.ebus.device.evse",
    }
)
"""The proxied DER classes, which are what the over-declaration check covers."""


def test_the_ders_still_declare_two_identity_fields_they_never_publish() -> None:
    """The rest of §5.2, which adopting the upstream emitter did *not* close.

    `circuit_nodes_missing_names()` looks only at `info/model`, so it reports
    clean while three declared properties still arrive with no value. Reading the
    capture directly is the only way to see the whole class, and leaving it
    unmeasured would let "the model gap closed" read as "the gap closed".

    `battery.software_version` in the delta analysis's Class B depends on the BESS
    firmware-version below, so that mapping stays untestable until this moves —
    `battery.serial_number`, its Class B twin, is now unblocked because the BESS
    does publish `info/serial-number`.

    Pinned as an exact set so it fails in either direction: a new over-declaration
    appears, or one of these is finally published and the expectation should
    shrink.

    **Keyed by device type, not device id.** The ids are `<proxier>-<identifier>`
    and move with the panel serial and the DER's own serial, so keying on them
    would make this fail whenever a config changed — for a reason that has nothing
    to do with what it measures. Type is the stable discriminator, and it is what
    the mapper itself resolves on.
    """
    with _WIRE.open() as handle:
        wire = json.load(handle)
    with (_WIRE.parent / "simulator_tree.json").open() as handle:
        tree = json.load(handle)

    gaps: dict[str, list[str]] = {}
    for device_id, description in tree.items():
        device_type = str(description.get("type") or "")
        if device_type not in _DER_TYPES:
            continue
        declared = {
            f"{node}/{prop}"
            for node, body in (description.get("nodes") or {}).items()
            for prop in (body.get("properties") or {})
        }
        published = {key for key in wire[device_id] if not key.startswith("$")}
        if absent := sorted(declared - published):
            already = gaps.setdefault(device_type, absent)
            assert already == absent, (
                f"two {device_type} devices disagree on which declarations go unpublished "
                f"({already} vs {absent}); collapsing by type would hide one of them"
            )

    assert gaps == {
        "energy.ebus.device.bess": ["info/firmware-version"],
        "energy.ebus.device.pv": ["info/firmware-version", "info/serial-number"],
    }, f"the declared-but-unpublished set moved: {gaps}"


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


def test_grid_state_is_read_from_the_mid(adapter: SchemaOneAdapter) -> None:
    """The gap this used to pin, now closed and asserted from the other side.

    Until 2026-08-08 this test asserted `grid_state is None`, because the
    simulator supported a MID fully — profile, resolvers, snapshot field — and no
    config instantiated one, so the mapping had no evidence behind it. The
    producer now publishes a MID and this reads a real value, so the expectation
    inverts rather than disappears: the mapping is exercised, and going back to
    `None` would be a regression, not a return to normal.

    `ON_GRID` and not `UP` is the substance. The MID publishes both
    `grid/islanding-state` (`ON_GRID`) and `grid/grid-state` (`UP`), and reading
    the wrong one is precisely the defect corrected on 2026-08-06 — flat-schema
    vocabulary sitting in a v1.0 property. Asserting the value proves which
    property the reader reached, where asserting "not None" would pass either way.
    """
    assert adapter.build_snapshot().grid_state == "ON_GRID", (
        "grid_state must come from the MID's grid/islanding-state. 'UP' or 'DOWN' means "
        "the reader has drifted onto grid/grid-state; None means the producer stopped "
        "publishing a MID and the mapping is unexercised again."
    )
