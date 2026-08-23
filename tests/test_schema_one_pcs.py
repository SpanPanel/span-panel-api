"""The enclosure's Power Control System, from the wire to the snapshot.

`pcs` 0.3 is the largest single capability the enclosure publishes: sixteen
properties on the panel and two on every circuit. The enclosure runs the
arbitration and publishes the *system* surface; a circuit publishes only its
*participation*.

**The capture is a PCS that is switched off, and that shapes every test here.**
Every limit is `0.0`, every enablement `UNCONFIGURED`, every boolean `false`.
Uniform data makes an assertion cheap to satisfy for the wrong reason: a field
wired to the neighbouring property reports the identical value, and a parser
that returned a zero of its own would agree with the wire by accident. So no
test in this module rests on the captured values alone. Presence is asserted
against the capture, and every *reading* is proved by republishing a value that
differs from the captured one and from every sibling's, one property at a time,
with the other fifteen fields pinned to their baseline. A field that read the
wrong property moves when it should not, and that is what fails.
"""

from __future__ import annotations

import dataclasses
import json
from typing import Any

import pytest

from ebus_sdk.homie import DiscoveredDevice

from span_panel_api.models import FieldMetadata, SpanPanelSnapshot, SpanPcsSnapshot
from span_panel_api_schema_1.const import PCS_LIMIT_SOURCES
from span_panel_api_schema_1.field_metadata import build_field_metadata
from span_panel_api_schema_1.reference_payloads import (
    RetainedTopicTree,
    device_from_topics,
    parent_child_tree,
)
from span_panel_api_schema_1.snapshot import build_snapshot

PANEL = "example-40t-001"
NODE = "pcs"

# A circuit the capture publishes participation for, and one that opts out. Two
# so a mapper that reported a constant cannot satisfy both.
MANAGED_CIRCUIT = "0ab966b95f92a6a51ec548485aa85f54"
UNMANAGED_CIRCUIT = "573066aaddd7b75114c4563ce3af18c4"


def _mutable_tree() -> dict[str, dict[str, str]]:
    return {device_id: dict(topics) for device_id, topics in parent_child_tree().items()}


def _snapshot(tree: RetainedTopicTree) -> SpanPanelSnapshot:
    panel = device_from_topics(PANEL, tree[PANEL])
    children = [device_from_topics(device_id, topics) for device_id, topics in tree.items() if device_id != PANEL]
    return build_snapshot(panel, children)


def _devices(tree: RetainedTopicTree) -> list[DiscoveredDevice]:
    return [device_from_topics(device_id, topics) for device_id, topics in tree.items()]


def _pcs(tree: RetainedTopicTree) -> SpanPcsSnapshot:
    """The snapshot's PCS, or fail saying the panel carries none.

    Narrowing here rather than at each call site: `pcs` is optional by design,
    and every test below that reaches into it has already asserted, or is
    asserting, that the capture publishes the node.
    """
    pcs = _snapshot(tree).pcs
    assert pcs is not None, "the capture's panel carries no pcs snapshot"
    return pcs


def _published(property_id: str) -> str:
    return parent_child_tree()[PANEL][f"{NODE}/{property_id}"]


def _declared_properties(tree: RetainedTopicTree, device_id: str = PANEL) -> dict[str, Any]:
    description: dict[str, Any] = json.loads(tree[device_id]["$description"])
    node: dict[str, Any] = description["nodes"][NODE]
    properties: dict[str, Any] = node["properties"]
    return properties


def _without_property(tree: dict[str, dict[str, str]], property_id: str) -> dict[str, dict[str, str]]:
    """Stop publishing one PCS property, and stop declaring it too."""
    del tree[PANEL][f"{NODE}/{property_id}"]
    description = json.loads(tree[PANEL]["$description"])
    del description["nodes"][NODE]["properties"][property_id]
    tree[PANEL]["$description"] = json.dumps(description)
    return tree


def _without_node(tree: dict[str, dict[str, str]], device_id: str = PANEL) -> dict[str, dict[str, str]]:
    """A device that publishes no `pcs` node at all."""
    for topic in [topic for topic in tree[device_id] if topic.startswith(f"{NODE}/")]:
        del tree[device_id][topic]
    description = json.loads(tree[device_id]["$description"])
    del description["nodes"][NODE]
    tree[device_id]["$description"] = json.dumps(description)
    return tree


# Every panel property the capability publishes, paired with the field that
# reads it and with a republished value chosen to be distinct from the captured
# one *and* from every sibling's. Distinctness is the whole apparatus: against a
# capture where all sixteen values are zeros, `false` and `UNCONFIGURED`, an
# assertion that a field equals what was published is satisfied by fifteen wrong
# wirings as easily as by the right one.
#
# The enablement enum has exactly four members and there are exactly four
# constraint classes, so each family gets a different one; the limits get
# unrelated decimals; and each boolean is flipped away from the captured value.
_PANEL_READS: tuple[tuple[str, str, str, object], ...] = (
    ("enabled", "enabled", "true", True),
    ("active", "active", "true", True),
    ("import-limit", "import_limit_a", "55.5", 55.5),
    ("binding-constraint", "binding_constraint", "FSR", "FSR"),
    ("feed-import-limit", "feed_import_limit_a", "11.5", 11.5),
    ("feed-import-limit-enablement", "feed_import_limit_enablement", "ENABLED", "ENABLED"),
    ("feed-import-limit-active", "feed_import_limit_active", "true", True),
    ("operator-import-limit", "operator_import_limit_a", "22.25", 22.25),
    ("operator-import-limit-enablement", "operator_import_limit_enablement", "DISABLED", "DISABLED"),
    ("operator-import-limit-active", "operator_import_limit_active", "true", True),
    ("off-grid-import-limit", "off_grid_import_limit_a", "33.75", 33.75),
    ("off-grid-import-limit-enablement", "off_grid_import_limit_enablement", "UNSPECIFIED", "UNSPECIFIED"),
    ("off-grid-import-limit-active", "off_grid_import_limit_active", "true", True),
    ("requested-import-limit", "requested_import_limit_a", "44.125", 44.125),
    (
        "requested-import-limit-enablement",
        "requested_import_limit_enablement",
        "UNSPECIFIED",
        "UNSPECIFIED",
    ),
    ("requested-import-limit-active", "requested_import_limit_active", "true", True),
)

_PANEL_PROPERTIES = tuple(property_id for property_id, _, _, _ in _PANEL_READS)


def _fields(pcs: SpanPcsSnapshot) -> dict[str, object]:
    return {field.name: getattr(pcs, field.name) for field in dataclasses.fields(pcs)}


# ---------------------------------------------------------------------------
# The premise: what the capture actually carries
# ---------------------------------------------------------------------------


def test_the_capture_publishes_the_whole_system_surface() -> None:
    """Guard the premise. Sixteen properties, every one declared and published;
    a capture that dropped one would make its absence test vacuous."""
    tree = parent_child_tree()
    declared = _declared_properties(tree)

    assert set(declared) == set(_PANEL_PROPERTIES)
    for property_id in _PANEL_PROPERTIES:
        assert f"{NODE}/{property_id}" in tree[PANEL]


def test_the_capture_is_a_pcs_that_is_switched_off() -> None:
    """The fact every test in this module is written around, asserted rather
    than assumed.

    Uniform data is the hazard here: a wrong wiring reports the same value as a
    right one, so no reading below is proved by comparing against the capture.
    Were the capture ever retaken with a configured PCS, this fails first and
    says so, rather than the mutation tests continuing to pass while the weaker
    assertions they replace quietly became meaningful.
    """
    pcs = _pcs(parent_child_tree())

    assert pcs.enabled is False
    assert pcs.active is False
    assert pcs.binding_constraint == "NONE"
    assert {value for name, value in _fields(pcs).items() if name.endswith("_limit_a")} == {0.0}
    assert {value for name, value in _fields(pcs).items() if name.endswith("_enablement")} == {"UNCONFIGURED"}


def test_the_catalog_constraint_classes_are_the_ones_the_panel_declares() -> None:
    """The four amps-native sources, checked against the wire rather than listed
    twice.

    The capability is explicit that "the number and naming of sources is not
    fixed by this spec" — a vendor may publish further triplets. So this is the
    drift signal: a fifth source arriving is firmware growing a constraint class
    nothing reads, and it should fail here rather than go unnoticed.
    """
    declared = _declared_properties(parent_child_tree())
    triplets = {
        property_id.removesuffix("-active").removesuffix("-enablement").removesuffix("-import-limit")
        for property_id in declared
        if property_id.endswith("-import-limit") or "-import-limit-" in property_id
    }

    assert triplets == set(PCS_LIMIT_SOURCES)


# ---------------------------------------------------------------------------
# Every reading is proved by mutation, one property at a time
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("property_id", "attribute", "republished", "expected"),
    _PANEL_READS,
    ids=[property_id for property_id, _, _, _ in _PANEL_READS],
)
def test_republishing_one_property_moves_only_the_field_that_reads_it(
    property_id: str, attribute: str, republished: str, expected: object
) -> None:
    """The load-bearing test of the module, and the answer to the uniform capture.

    Two assertions, and the second is the one that bites. The first says the
    field followed the wire. The second says *no other field did* — which is
    what a field reading the neighbouring property fails, and what an assertion
    against the captured zeros could never detect, since every sibling already
    holds the value a wrong wiring would report.
    """
    baseline = _fields(_pcs(parent_child_tree()))

    tree = _mutable_tree()
    tree[PANEL][f"{NODE}/{property_id}"] = republished
    after = _fields(_pcs(tree))

    assert after[attribute] == expected
    moved = {name for name, value in after.items() if value != baseline[name]}
    assert moved == {attribute}, f"republishing {property_id} also moved {sorted(moved - {attribute})}"


def test_a_fully_configured_pcs_lands_every_value_on_its_own_field() -> None:
    """All sixteen republished at once, every value distinct from its siblings'.

    The per-property test above proves each field reads its own property. This
    proves the sixteen do not interfere: a mapper that assembled the dataclass
    positionally, or that reused one triplet's reader for another family, passes
    every single-property test and fails here.
    """
    tree = _mutable_tree()
    for property_id, _, republished, _ in _PANEL_READS:
        tree[PANEL][f"{NODE}/{property_id}"] = republished

    after = _fields(_pcs(tree))

    assert after == {attribute: expected for _, attribute, _, expected in _PANEL_READS}


def test_the_four_limits_are_four_different_readings() -> None:
    """The families are distinguishable, on data where the capture makes them
    identical. Four unrelated decimals, and each has to land on its own field."""
    tree = _mutable_tree()
    for index, source in enumerate(PCS_LIMIT_SOURCES):
        tree[PANEL][f"{NODE}/{source}-import-limit"] = str(index + 1)

    pcs = _pcs(tree)

    assert pcs.feed_import_limit_a == 1.0
    assert pcs.operator_import_limit_a == 2.0
    assert pcs.off_grid_import_limit_a == 3.0
    assert pcs.requested_import_limit_a == 4.0


def test_the_effective_limit_is_not_any_of_its_inputs() -> None:
    """`import-limit` is the arbitration *result*, and the catalog says so. A
    mapper that took it from the FSR would be plausible and wrong, so the
    republished result differs from every input."""
    tree = _mutable_tree()
    tree[PANEL][f"{NODE}/import-limit"] = "12.5"
    for source in PCS_LIMIT_SOURCES:
        tree[PANEL][f"{NODE}/{source}-import-limit"] = "99.0"

    pcs = _pcs(tree)

    assert pcs.import_limit_a == 12.5


def test_enabled_and_active_are_two_different_facts() -> None:
    """A configured PCS spends most of its life enabled and inactive, so the two
    booleans must be readable in opposition. Both are `false` in the capture,
    which is exactly the state in which crossing them is invisible."""
    tree = _mutable_tree()
    tree[PANEL][f"{NODE}/enabled"] = "true"
    tree[PANEL][f"{NODE}/active"] = "false"

    pcs = _pcs(tree)

    assert pcs.enabled is True
    assert pcs.active is False


def test_binding_constraint_is_kept_as_the_wire_string() -> None:
    """Publishers may extend the enum through `$format`, and this property's
    whole job is naming a source — so a value outside the catalog's eight must
    survive rather than be normalised onto `UNKNOWN`."""
    tree = _mutable_tree()
    tree[PANEL][f"{NODE}/binding-constraint"] = "VENDOR_THERMAL"

    assert _pcs(tree).binding_constraint == "VENDOR_THERMAL"


# ---------------------------------------------------------------------------
# Absence: an unpublished property, a dropped node, no PCS at all
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("property_id", "attribute"),
    [(property_id, attribute) for property_id, attribute, _, _ in _PANEL_READS],
    ids=[property_id for property_id, _, _, _ in _PANEL_READS],
)
def test_a_property_the_panel_does_not_publish_is_none(property_id: str, attribute: str) -> None:
    """`None`, never `0.0` and never `False`. Three of the four constraint
    classes are `MAY`, so an omitted family is conformant firmware — and a limit
    defaulted to zero would read as "no import permitted", the most alarming
    reading the property has."""
    pcs = _pcs(_without_property(_mutable_tree(), property_id))

    assert getattr(pcs, attribute) is None


def test_dropping_one_property_leaves_the_others_reading() -> None:
    """Absence is per-property: a panel publishing a partial `pcs` node still
    reports the part it has."""
    tree = _mutable_tree()
    tree[PANEL][f"{NODE}/import-limit"] = "17.5"
    pcs = _pcs(_without_property(tree, "feed-import-limit"))

    assert pcs.feed_import_limit_a is None
    assert pcs.import_limit_a == 17.5


def test_a_panel_with_no_pcs_node_carries_no_pcs_at_all() -> None:
    """The presence signal a consumer gates entity creation on. `None` rather
    than an empty instance, so nothing has to be inferred from a sentinel."""
    assert _snapshot(_without_node(_mutable_tree())).pcs is None


def test_a_switched_off_pcs_is_still_a_pcs() -> None:
    """The distinction the node-presence gate exists to keep, and the reason it
    cannot be a value gate: this capture publishes zeros throughout, and a
    consumer that read those as absence would delete the entities of every panel
    whose PCS is merely unconfigured."""
    assert _snapshot(parent_child_tree()).pcs is not None


def test_a_declared_node_with_no_published_values_is_still_present() -> None:
    """Mid-discovery is the normal case for a device that has announced itself
    and not yet retained its topics. The node is declared, so the PCS exists and
    every reading is unknown — which is not the same as no PCS."""
    tree = _mutable_tree()
    for property_id in _PANEL_PROPERTIES:
        del tree[PANEL][f"{NODE}/{property_id}"]

    pcs = _snapshot(tree).pcs

    assert pcs is not None
    assert set(_fields(pcs).values()) == {None}


def test_a_limit_that_is_not_a_number_reads_as_absent() -> None:
    """Same answer as not publishing, because neither is a reading."""
    tree = _mutable_tree()
    tree[PANEL][f"{NODE}/import-limit"] = "n/a"

    assert _pcs(tree).import_limit_a is None


def test_zero_amps_is_a_reading_and_not_an_absence() -> None:
    """The distinction the `None` default exists to keep: the PCS is permitting
    no import at all, which is a state, not a gap."""
    tree = _mutable_tree()
    tree[PANEL][f"{NODE}/import-limit"] = "0.0"

    assert _pcs(tree).import_limit_a == 0.0


# ---------------------------------------------------------------------------
# The circuit half: participation, not arbitration
# ---------------------------------------------------------------------------


def test_the_capture_publishes_participation_on_its_circuits() -> None:
    """Guard the premise for the circuit tests, and pin that the two circuits
    they use actually disagree."""
    tree = parent_child_tree()

    assert tree[MANAGED_CIRCUIT][f"{NODE}/managed"] == "true"
    assert tree[UNMANAGED_CIRCUIT][f"{NODE}/managed"] == "false"
    assert tree[MANAGED_CIRCUIT][f"{NODE}/priority"] != tree[UNMANAGED_CIRCUIT][f"{NODE}/priority"]


def test_a_circuit_reports_its_own_participation() -> None:
    """Read against the tree rather than against literals, and against two
    circuits that differ, so a mapper reporting a constant fails."""
    tree = parent_child_tree()
    circuits = _snapshot(tree).circuits

    assert circuits[MANAGED_CIRCUIT].pcs_managed is True
    assert circuits[UNMANAGED_CIRCUIT].pcs_managed is False
    assert circuits[MANAGED_CIRCUIT].pcs_priority == int(tree[MANAGED_CIRCUIT][f"{NODE}/priority"])
    assert circuits[UNMANAGED_CIRCUIT].pcs_priority == int(tree[UNMANAGED_CIRCUIT][f"{NODE}/priority"])


def test_republishing_participation_moves_the_circuit_fields() -> None:
    """The mutation half. The republished priority is outside the range the
    capture uses on any circuit, so a field wired to another circuit's value —
    or to the load-shed priority beside it — cannot report it."""
    tree = _mutable_tree()
    tree[MANAGED_CIRCUIT][f"{NODE}/managed"] = "false"
    tree[MANAGED_CIRCUIT][f"{NODE}/priority"] = "42"

    circuit = _snapshot(tree).circuits[MANAGED_CIRCUIT]

    assert circuit.pcs_managed is False
    assert circuit.pcs_priority == 42


def test_pcs_priority_is_not_the_load_shed_priority() -> None:
    """Two policies on one relay, kept apart by the catalog and here. One is an
    integer shed ordering under an import limit; the other is the backup tier a
    user sets, and they do not even share a value space."""
    circuit = _snapshot(parent_child_tree()).circuits[MANAGED_CIRCUIT]

    assert isinstance(circuit.pcs_priority, int)
    assert isinstance(circuit.priority, str)
    assert circuit.priority != str(circuit.pcs_priority)


@pytest.mark.parametrize("property_id", ["managed", "priority"])
def test_a_circuit_that_does_not_publish_participation_reports_none(property_id: str) -> None:
    """Both are `MAY`. A circuit that has not said it is managed has not said it
    is unmanaged, and priority `0` is a legal ranking — so neither may default."""
    tree = _mutable_tree()
    del tree[MANAGED_CIRCUIT][f"{NODE}/{property_id}"]

    circuit = _snapshot(tree).circuits[MANAGED_CIRCUIT]

    assert getattr(circuit, f"pcs_{property_id}") is None


def test_a_circuit_with_no_pcs_node_participates_in_nothing() -> None:
    circuit = _snapshot(_without_node(_mutable_tree(), MANAGED_CIRCUIT)).circuits[MANAGED_CIRCUIT]

    assert circuit.pcs_managed is None
    assert circuit.pcs_priority is None


def test_a_synthesised_unmapped_position_carries_no_participation() -> None:
    """Unmapped tabs are invented by the adapter, not published, so claiming a
    PCS relationship for one would be a fabrication."""
    circuits = _snapshot(parent_child_tree()).circuits
    unmapped = next(circuit for circuit_id, circuit in circuits.items() if circuit_id.startswith("unmapped_tab_"))

    assert unmapped.pcs_managed is None
    assert unmapped.pcs_priority is None


# ---------------------------------------------------------------------------
# Metadata: only the result carries a row
# ---------------------------------------------------------------------------


def test_the_effective_limit_takes_its_unit_from_the_tree() -> None:
    metadata = build_field_metadata(_devices(parent_child_tree()))
    declared = _declared_properties(parent_child_tree())

    entry = metadata["pcs.import_limit_a"]
    assert entry.resolved is True
    assert entry.unit == declared["import-limit"]["unit"]
    assert entry.datatype == declared["import-limit"]["datatype"]


def test_changing_the_declared_unit_changes_the_metadata() -> None:
    """The mutation proof for the metadata half: the unit comes from the panel's
    own `$description`, not from the vendored catalog and not from a literal."""
    tree = _mutable_tree()
    description = json.loads(tree[PANEL]["$description"])
    description["nodes"][NODE]["properties"]["import-limit"]["unit"] = "kA"
    tree[PANEL]["$description"] = json.dumps(description)

    assert build_field_metadata(_devices(tree))["pcs.import_limit_a"].unit == "kA"


def test_the_result_properties_carry_rows_and_the_inputs_do_not() -> None:
    """Deliberate, and asserted so it stays deliberate.

    The capability calls `import-limit` and `binding-constraint` "the result",
    and those plus `active` are what a consumer renders as readings. The four
    constraint families and `enabled` qualify that result rather than standing
    alone, so a unit row for them would advertise a surface that is not there —
    the same treatment the `shed-forecast` full-charge pair gets.
    """
    metadata = build_field_metadata(_devices(parent_child_tree()))
    pcs_rows = {path for path in metadata if path.startswith("pcs.")}

    assert pcs_rows == {"pcs.import_limit_a", "pcs.binding_constraint", "pcs.active"}


def test_a_declared_node_missing_a_property_is_a_gap_not_absent_hardware() -> None:
    """The three-way contract: the node is here, so an omitted property is
    degradation and gets an unresolved row rather than no row."""
    metadata = build_field_metadata(_devices(_without_property(_mutable_tree(), "import-limit")))

    assert metadata["pcs.import_limit_a"] == FieldMetadata(unit=None, datatype="unknown", resolved=False)


def test_no_pcs_node_produces_no_rows_at_all() -> None:
    """Hardware that is not there is not a defect: no entry, so a consumer reads
    "nothing will populate this" rather than "this is broken"."""
    metadata = build_field_metadata(_devices(_without_node(_mutable_tree())))

    assert not [path for path in metadata if path.startswith("pcs.")]


def test_circuit_participation_carries_no_metadata_row() -> None:
    """Read into the snapshot and rendered as attributes on the circuit's own
    sensor, not as readings of their own."""
    metadata = build_field_metadata(_devices(parent_child_tree()))

    assert "circuit.pcs_managed" not in metadata
    assert "circuit.pcs_priority" not in metadata
