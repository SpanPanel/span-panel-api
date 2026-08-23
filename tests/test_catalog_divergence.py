"""The registry used as a validator — what a panel *declares* against what the
catalogs *define*.

`test_schema_one_conformance.py` asks whether every name this adapter reads is
one the specification carries. That is a question about vocabulary, and it is
answered by presence: a catalog exists, the property is in it, done. It never
opens the definition.

This asks the next question, which is the one that corrupts readings when the
answer is wrong: **does the producer's declared `unit` and `datatype` for a
property agree with the catalog's?** Agreement is silence. Disagreement is a
finding, and is never resolved silently in either direction — the wire is not
"fixed" to match the catalog, and the catalog is not assumed to be right. The
one case in this repository's history was found by a person noticing that a
sibling device declared the same quantity differently; `meter/active-power`
labelled `kW` while the values were watts, a 1000x error that shipped. That is
what this makes mechanical.

**Both producers are the subject.** The v1.0 side declares its capability nodes
on the wire, so the catalog for a property is whatever the node's `$type` names.
The flat schema document has no capability nodes at all — it predates them — so
its properties are joined to the catalogued vocabulary through the one thing the
two adapters already agree on: the snapshot field path each fills. The join is
required to agree on the property *name* as well, so a pre-catalog **rename**
(`dipole` for `breaker/poles`, `l1-voltage` for `meter/voltage-a`) is left out
rather than reported as a datatype divergence. A rename is a major-version event
under the eBus contract and is handled by having two adapters; it is not a
mislabel.

**The register is not a suppression list.** An entry is a human saying "SPAN
ships this, we have looked at it, and we compensate" — with what the wire says,
what the catalog says, where it is observed, why, and when. It fails in both
directions like every other baseline here: a new divergence fails until somebody
records it, and a recorded divergence that has *disappeared* fails until its line
is removed. The second direction is what makes the register self-cleaning when a
firmware or a catalog is fixed.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping, Sequence
import copy
from dataclasses import dataclass
import json
from pathlib import Path

from span_panel_api import reference_payloads as flat_payloads
from span_panel_api_schema_0.field_metadata import _PROPERTY_FIELD_MAP as _FLAT_FIELD_MAP
from span_panel_api_schema_1 import reference_payloads as tree_payloads
from span_panel_api_schema_1.catalog import (
    CATALOGUED_CONCRETE_UNITS,
    UNIT_FAMILIES,
    Declaration,
    Divergence,
    Divergent,
    capability_of,
    compare,
    declaration,
    unclassified_units,
    unit_agrees,
)
from span_panel_api_schema_1.const import NODE_METER
from span_panel_api_schema_1.description import nodes as declared_nodes, optional_str, properties as declared_properties
from span_panel_api_schema_1.field_metadata import (
    _DOWNSTREAM_LUGS_FIELDS,
    _PROPERTY_FIELD_MAP as _ONE_FIELD_MAP,
    _UPSTREAM_LUGS_FIELDS,
)

_SPEC = Path(__file__).parent.parent / "packages" / "schema-1" / "spec"
_CATALOGS = _SPEC / "catalogs"
_SIMULATOR_TREE = _SPEC / "fixtures" / "simulator_tree.json"
_SIMULATOR_WIRE = _SPEC / "fixtures" / "simulator_wire.json"

SIMULATOR_TREE = "simulator-tree"
SIMULATOR_WIRE = "simulator-wire"
REFERENCE_TREE = "reference-tree"
FLAT_SCHEMA = "flat-schema"


# ---------------------------------------------------------------------------
# The acknowledged-divergence register
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Acknowledged:
    """One divergence a human has read and decided to live with.

    `observed_in` is part of what is checked, not annotation. A divergence that
    moves between producers — flat's mislabel being fixed while a v1.0 capture
    starts showing it — is a different situation than the one that was recorded,
    and an entry that went on covering it would be exactly the suppression this
    register is not.

    `recorded` is the date the entry was written, so a line nobody has revisited
    since the firmware it describes shipped is visible as such.
    """

    observed_in: tuple[str, ...]
    reason: str
    recorded: str


_REGISTER: dict[Divergence, Acknowledged] = {
    Divergence("meter", "active-power", Divergent.UNIT, "kW", "W"): Acknowledged(
        observed_in=(FLAT_SCHEMA,),
        reason=(
            "The flat schema document labels circuit active power `kW`; real panels publish watts, "
            "and following the label reintroduces the 1000x error 1eef0dc removed after checking "
            "against hardware. The consumer reads it as W deliberately -- "
            "`test_circuit_active_power_unit_still_disagrees_with_the_schema` in "
            "test_schema_provenance.py holds that side of it, against the schema. This line holds "
            "the other side, against the catalog, which is what makes the disagreement a measured "
            "fact about two producers rather than a comment in one test. v1.0 declares `W` and does "
            "not carry the defect, which is why only the flat producer is observed here."
        ),
        recorded="2026-08-20",
    ),
    Divergence("info", "model", Divergent.DATATYPE, "enum", "string"): Acknowledged(
        observed_in=(REFERENCE_TREE, SIMULATOR_TREE, SIMULATOR_WIRE),
        reason=(
            "The catalog types `model` as `string` while its own description invites a publisher to "
            "advertise the valid set 'via Homie `$format` on the property' -- which Homie 5 permits "
            "only on an `enum`. The two halves of the catalog entry disagree, and SPAN followed the "
            "description: the enclosure declares its model as an enum over the five load-centre "
            "configurations (MAIN_16..MLO_48), and every other device class declares the plain "
            "string. Nothing is compensated in code, because an enum payload is text either way and "
            "`battery.model` / `pv.model` are read as text. Recorded rather than silenced because "
            "the catalog is the side that should move: raise it upstream so `model` is typed the way "
            "its description already describes."
        ),
        recorded="2026-08-20",
    ),
}


# ---------------------------------------------------------------------------
# The catalogued reference
# ---------------------------------------------------------------------------


def _json_object(path: Path) -> dict[str, object]:
    with path.open(encoding="utf-8") as handle:
        loaded: object = json.load(handle)
    assert isinstance(loaded, dict), f"{path} is not a JSON object"
    return {str(key): value for key, value in loaded.items()}


def _objects(raw: object) -> dict[str, dict[str, object]]:
    """The object-valued members of a JSON object, keyed by name."""
    if not isinstance(raw, dict):
        return {}
    return {str(key): value for key, value in raw.items() if isinstance(value, dict)}


def _catalogued() -> dict[str, dict[str, Declaration]]:
    """Every vendored catalog, keyed by the capability it declares itself to be.

    By the `capability` field rather than the file name, because that is the
    name a node's `$type` carries. The two agree today and the convention is
    that they always will; keying on the one that is matched against removes the
    convention from the load-bearing path.
    """
    catalogued: dict[str, dict[str, Declaration]] = {}
    for path in sorted(_CATALOGS.glob("*.json")):
        document = _json_object(path)
        capability = capability_of(optional_str(document.get("capability")))
        assert capability is not None, f"{path.name} declares no capability in the eBus namespace"
        catalogued[capability] = {
            property_id: declaration(definition) for property_id, definition in _objects(document.get("properties")).items()
        }
    return catalogued


# ---------------------------------------------------------------------------
# What the producers declare
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Declared:
    """One property declaration, normalised across producers."""

    capability: str
    property_id: str
    declaration: Declaration


def _from_description(description: dict[str, object]) -> Iterator[Declared]:
    """Every property one `$description` declares on a capability node.

    Nodes whose `$type` is outside the eBus capability namespace are skipped —
    there is nothing to look a catalog up by. `test_every_captured_node_names_a_capability`
    pins that this never happens in the captures we hold, so the skip cannot
    quietly shrink the surface being checked.
    """
    for node in declared_nodes(description).values():
        capability = capability_of(optional_str(node.get("type")))
        if capability is None:
            continue
        for property_id, definition in declared_properties(node).items():
            yield Declared(capability, property_id, declaration(definition))


def _untyped_nodes(descriptions: Sequence[dict[str, object]]) -> list[str]:
    """Node ids whose `$type` names no eBus capability."""
    return [
        node_id
        for description in descriptions
        for node_id, node in declared_nodes(description).items()
        if capability_of(optional_str(node.get("type"))) is None
    ]


def _tree_descriptions() -> list[dict[str, object]]:
    """The simulator capture that is already a tree of parsed descriptions."""
    return list(_objects(_json_object(_SIMULATOR_TREE)).values())


def _wire_descriptions(tree: Mapping[str, Mapping[str, str]]) -> list[dict[str, object]]:
    """The descriptions inside a retained-topic capture.

    `$description` is a JSON *string* on the wire, which is the shape the two
    wire captures are vendored in — and the shape a live broker replay has, so
    this reader is the one a diagnostic would reuse.
    """
    descriptions: list[dict[str, object]] = []
    for topics in tree.values():
        raw = topics.get("$description")
        if raw is None:
            continue
        parsed: object = json.loads(raw)
        assert isinstance(parsed, dict), "a captured $description is not a JSON object"
        descriptions.append({str(key): value for key, value in parsed.items()})
    return descriptions


def _simulator_wire() -> Mapping[str, Mapping[str, str]]:
    return {
        device_id: {str(topic): str(payload) for topic, payload in topics.items()}
        for device_id, topics in _objects(_json_object(_SIMULATOR_WIRE)).items()
    }


# ---------------------------------------------------------------------------
# The flat producer, joined to the catalogued vocabulary
# ---------------------------------------------------------------------------


def _catalogued_spellings() -> dict[str, set[tuple[str, str]]]:
    """Snapshot field path -> the `(capability, property)` v1.0 fills it from.

    Derived from the v1.0 metadata table plus the two lugs tables, which is
    every route the parser has to a field path. Restating it would let the join
    go on describing a mapping the parser had moved.
    """
    spellings: dict[str, set[tuple[str, str]]] = {}
    rows = [(node, property_id, path) for _, node, property_id, path in _ONE_FIELD_MAP]
    rows += [(NODE_METER, property_id, path) for property_id, path in _UPSTREAM_LUGS_FIELDS + _DOWNSTREAM_LUGS_FIELDS]
    for node, property_id, path in rows:
        spellings.setdefault(path, set()).add((node, property_id))
    return spellings


def _flat_declared() -> list[Declared]:
    """The flat schema document's properties, under their catalogued capability.

    The flat document is a real producer — captured from a panel on
    `spanos2/r202603/05` — and it is where the one mislabel this whole check
    exists for actually lives. It cannot be read the way a v1.0 tree is: it
    declares properties per *device type*, with no capability node to look a
    catalog up by.

    So the capability comes from the snapshot field the two adapters agree the
    property fills, and the join is admitted only when both sides spell the
    property the same. That second condition is what keeps this honest. Fifteen
    flat properties reach a catalogued property under a *different* name --
    `dipole` for `breaker/poles`, `software-version` for `info/firmware-version`,
    `shed-priority` for `load-shed/priority` -- and every one of those is a
    rename rather than a mislabel. Comparing across a rename would report
    `dipole`'s `boolean` against `poles`'s `integer` as a divergence, when what
    it really shows is that flat asks a yes/no question where v1.0 publishes a
    count.
    """
    spellings = _catalogued_spellings()
    declared: list[Declared] = []
    for device_type, properties in flat_payloads.homie_schema_types().items():
        for property_id, definition in _objects(properties).items():
            for path in (p for kind, name, p in _FLAT_FIELD_MAP if kind == device_type and name == property_id):
                for capability, catalogued_name in sorted(spellings.get(path, set())):
                    if catalogued_name == property_id:
                        declared.append(Declared(capability, property_id, declaration(definition)))
    return declared


# ---------------------------------------------------------------------------
# The survey
# ---------------------------------------------------------------------------


def _surface() -> dict[str, list[Declared]]:
    """Every declaration this check judges, by producer."""
    return {
        SIMULATOR_TREE: [d for description in _tree_descriptions() for d in _from_description(description)],
        SIMULATOR_WIRE: [d for description in _wire_descriptions(_simulator_wire()) for d in _from_description(description)],
        REFERENCE_TREE: [
            d
            for description in _wire_descriptions(tree_payloads.parent_child_tree())
            for d in _from_description(description)
        ],
        FLAT_SCHEMA: _flat_declared(),
    }


def _findings(surface: Mapping[str, Sequence[Declared]]) -> dict[Divergence, frozenset[str]]:
    """Every divergence in a surface, with the producers that show it.

    Producer-independent identity: one mislabel published by a panel and
    captured three ways is one finding. Which captures show it is the value, so
    a register entry can be checked against it without being written three
    times.
    """
    catalogued = _catalogued()
    found: dict[Divergence, set[str]] = {}
    for producer, declarations in surface.items():
        for entry in declarations:
            definition = catalogued.get(entry.capability, {}).get(entry.property_id)
            for divergence in compare(entry.capability, entry.property_id, entry.declaration, definition):
                found.setdefault(divergence, set()).add(producer)
    return {divergence: frozenset(producers) for divergence, producers in found.items()}


def _divergences(surface: Mapping[str, Sequence[Declared]]) -> dict[Divergence, frozenset[str]]:
    """Findings that are a disagreement about a definition, not an absence."""
    return {
        divergence: producers
        for divergence, producers in _findings(surface).items()
        if divergence.kind is not Divergent.UNCATALOGUED
    }


def _report(divergence: Divergence, producers: frozenset[str]) -> str:
    return f"{divergence} [{', '.join(sorted(producers))}]"


# ---------------------------------------------------------------------------
# The register fails in both directions
# ---------------------------------------------------------------------------


def test_every_divergence_is_acknowledged() -> None:
    """A producer declaring something the catalog contradicts stops the build.

    The direction that catches the next `kW`. What it wants is not a fix — the
    right answer is often that the producer is right and the catalog is stale —
    but a human decision, written down, with a date on it.
    """
    surveyed = _divergences(_surface())
    unrecorded = sorted(
        (_report(divergence, producers) for divergence, producers in surveyed.items() if divergence not in _REGISTER)
    )

    assert not unrecorded, (
        "declared definitions that disagree with the vendored catalogs:\n  "
        + "\n  ".join(unrecorded)
        + "\n\nDecide which side is wrong — the catalog is not automatically right — and record the "
        "outcome in _REGISTER with a reason and a date. Do not change the wire reader to agree with "
        "the catalog, or the catalog copy to agree with the wire."
    )


def test_every_acknowledgement_still_describes_a_real_divergence() -> None:
    """The self-cleaning direction.

    When a firmware or a catalog is fixed, the entry describing the old
    disagreement becomes a false statement about the producer — and a silent
    one, because everything still passes. This turns it into a prompt to delete
    the line, which is the only thing that keeps the register from becoming the
    suppression list it must not be.
    """
    surveyed = _divergences(_surface())
    stale = sorted(
        f"{divergence} — recorded {entry.recorded}" for divergence, entry in _REGISTER.items() if divergence not in surveyed
    )

    assert not stale, (
        "recorded as acknowledged divergences but no producer declares them any more:\n  "
        + "\n  ".join(stale)
        + "\n\nThe disagreement is over. Delete the entry; its failing is good news."
    )


def test_every_acknowledgement_names_the_producers_that_still_show_it() -> None:
    """Where a divergence lives is checked, not annotated.

    A mislabel fixed in one producer and appearing in another is a new
    situation, not the one somebody signed off. Without this the entry would go
    on covering it under a reason that had stopped being true.
    """
    surveyed = _divergences(_surface())
    moved = sorted(
        f"{divergence}: recorded in {sorted(entry.observed_in)}, observed in {sorted(surveyed[divergence])}"
        for divergence, entry in _REGISTER.items()
        if divergence in surveyed and frozenset(entry.observed_in) != surveyed[divergence]
    )

    assert not moved, (
        "acknowledged divergences no longer observed where they were recorded:\n  "
        + "\n  ".join(moved)
        + "\n\nRe-read the entry's reason before updating `observed_in` — a divergence changing "
        "producers usually means the reason is out of date too."
    )


def test_every_acknowledgement_justifies_itself() -> None:
    """A register line is a human's claim, and a claim needs its working.

    Cheap to assert and worth asserting, because the failure mode of a register
    is a line added under deadline with `reason="known issue"`, which is a
    suppression with extra syntax.
    """
    thin = sorted(str(divergence) for divergence, entry in _REGISTER.items() if len(entry.reason) < 120)
    assert not thin, f"acknowledgements with no real reason recorded: {thin}"

    undated = sorted(str(divergence) for divergence, entry in _REGISTER.items() if not entry.recorded.count("-") == 2)
    assert not undated, f"acknowledgements with no ISO date: {undated}"

    misfiled = sorted(
        str(divergence)
        for divergence, entry in _REGISTER.items()
        if set(entry.observed_in) - {SIMULATOR_TREE, SIMULATOR_WIRE, REFERENCE_TREE, FLAT_SCHEMA}
    )
    assert not misfiled, f"acknowledgements naming a producer this check does not survey: {misfiled}"


# ---------------------------------------------------------------------------
# An absence is an absence, and is reported once
# ---------------------------------------------------------------------------


def test_a_property_no_catalog_defines_is_never_reported_as_a_mismatch() -> None:
    """The EVSE `config` node is the case, and it is not a defect.

    `config` is not an eBus capability at all — the specification has no catalog
    of that name, which `test_an_unvendored_node_is_one_the_specification_really_does_not_define`
    checks against a real checkout, and both its properties are declared
    extensions in `_SPAN_EXTENSIONS`. Comparing its `unit` against a catalog that
    does not exist would report SPAN's own vocabulary as a mislabel, twice per
    property.

    So an absence is terminal: reported once, as an absence, and never again as
    a disagreement about a definition.
    """
    findings = _findings(_surface())
    absent = {(d.capability, d.property_id) for d in findings if d.kind is Divergent.UNCATALOGUED}
    mismatched = {(d.capability, d.property_id) for d in findings if d.kind is not Divergent.UNCATALOGUED}

    assert ("config", "max-charge-current") in absent, "the EVSE config node is no longer reported as uncatalogued"
    assert ("config", "user-max-charge-current") in absent, "the EVSE config node is no longer reported as uncatalogued"

    both = sorted(absent & mismatched)
    assert not both, f"reported as both absent from the catalog and disagreeing with it: {both}"

    for property_id in ("max-charge-current", "user-max-charge-current"):
        reported = [d for d in findings if (d.capability, d.property_id) == ("config", property_id)]
        assert len(reported) == 1, f"config/{property_id} reported {len(reported)} times: {[str(d) for d in reported]}"


# ---------------------------------------------------------------------------
# The rule that keeps an abstract unit from producing a false finding
# ---------------------------------------------------------------------------


def test_an_abstract_family_unit_is_satisfied_by_a_member_of_the_family() -> None:
    """`unit: "energy"` is an instruction to substitute, not a unit to match.

    The catalog says `soc/soe` and `info/nameplate-capacity` are `energy`; the
    BESS in every capture publishes `kWh`, which is the substitution the
    specification asks for. A string compare would report conformance as the
    defect — and it would do so on four of the sixty-odd properties this check
    compares, which is enough noise to get the whole check turned off.

    Membership is what is satisfied, and only membership: echoing the token back
    is not a substitution, and an energy unit nobody enumerated is a question for
    a human rather than a pass.
    """
    assert unit_agrees("kWh", "energy"), "the substitution the specification asks for must be silent"
    assert unit_agrees("Wh", "energy"), "a water heater's thermal Wh is the same substitution"
    assert not unit_agrees("energy", "energy"), "echoing the placeholder is not substituting a unit"
    assert not unit_agrees("W", "energy"), "a power unit does not satisfy an energy dimension"
    assert not unit_agrees(None, "energy"), "declaring no unit at all does not satisfy it either"

    assert unit_agrees("W", "W"), "a concrete unit is an exact match"
    assert not unit_agrees("kW", "W"), "the mislabel this whole check exists for must not be excused"
    assert unit_agrees(None, None), "a property neither side gives a unit is silent"
    assert not unit_agrees("%", None), "a unit where the catalog carries none is a disagreement"


def test_a_node_outside_the_capability_namespace_resolves_to_no_capability() -> None:
    """What a node's `$type` has to be before a catalog can be looked up for it.

    The namespace is the whole check on a name that arrives from a publisher: a
    device type, a vendor extension or an empty string names no capability, and
    `capability_of` says so rather than producing a bare word that would then
    miss every catalog and be reported as an absence. The two are different
    situations, and only one of them is a fact about the specification.
    """
    assert capability_of("energy.ebus.capability.meter") == "meter"
    assert capability_of("energy.ebus.capability.config") == "config", "an uncatalogued capability is still a capability"
    assert capability_of("energy.ebus.device.circuit") is None, "a device type is not a capability"
    assert capability_of("meter") is None, "a bare node id makes no claim about the namespace"
    assert capability_of("energy.ebus.capability.") is None, "an empty suffix names nothing"
    assert capability_of(None) is None


def test_a_finding_reads_as_the_sentence_a_human_has_to_act_on() -> None:
    """The report line is the whole interface of this check.

    Everything above produces one of these two sentences, and a person reading a
    failed build has nothing else to go on — so the two kinds have to be
    distinguishable at a glance, and an absence must not be dressed up as a
    disagreement with values it does not have.
    """
    mismatch = Divergence("meter", "active-power", Divergent.UNIT, "kW", "W")
    assert str(mismatch) == "meter/active-power: declared unit 'kW', catalog says 'W'"

    absent = Divergence("config", "max-charge-current", Divergent.UNCATALOGUED, None, None)
    assert str(absent) == "config/max-charge-current: no catalog defines it"


def test_every_catalogued_unit_token_is_classified() -> None:
    """The guard on the family rule.

    A unit token arriving in a vendored catalog that is neither a concrete unit
    nor an enumerated dimension would be string-compared against whatever a
    publisher substitutes, and report an entire new family as broken. That is
    the false finding this module was written to avoid, so a new token has to be
    classified by a human before it is compared against anything.
    """
    catalogued = frozenset(
        definition.unit for properties in _catalogued().values() for definition in properties.values() if definition.unit
    )
    unclassified = sorted(unclassified_units(catalogued))

    assert not unclassified, (
        f"unit tokens in the vendored catalogs that are neither concrete nor an enumerated family: {unclassified}. "
        "Decide which, and add it to CATALOGUED_CONCRETE_UNITS or UNIT_FAMILIES in catalog.py."
    )

    assert "energy" in UNIT_FAMILIES, "the one abstract family this repository has met"
    retired = sorted(CATALOGUED_CONCRETE_UNITS - catalogued)
    assert not retired, (
        f"units pinned as catalogued but no catalog uses them any more: {retired}. "
        "Drop them, so this set keeps describing the vendored vocabulary rather than a past one."
    )


# ---------------------------------------------------------------------------
# The check is actually looking at something
# ---------------------------------------------------------------------------


def test_every_producer_contributes_a_compared_surface() -> None:
    """A survey that silently reads nothing passes every assertion above.

    The way this check dies is not a wrong answer, it is a reader that stops
    finding declarations — a capture reshaped, a metadata table moved — after
    which the register is a list of comments and the build is green. So the
    surface is measured, and the four anchors that make the comparison worth
    running are named.
    """
    catalogued = _catalogued()
    surface = _surface()

    for producer, declarations in surface.items():
        assert declarations, f"{producer} contributed no declarations at all"

    compared = {
        (entry.capability, entry.property_id)
        for declarations in surface.values()
        for entry in declarations
        if entry.property_id in catalogued.get(entry.capability, {})
    }

    for anchor in (("meter", "active-power"), ("soc", "soe"), ("info", "model"), ("breaker", "rating")):
        assert anchor in compared, f"{anchor[0]}/{anchor[1]} is no longer being compared against its catalog"

    assert len(compared) >= 55, f"only {len(compared)} catalogued properties are being compared; the readers have narrowed"


def test_the_flat_join_still_reaches_the_known_mislabel() -> None:
    """The flat producer is joined through two metadata tables, and both move.

    If either table drops the row that carries `circuit.instant_power_w`, the
    join goes quiet and the `kW` mislabel stops being compared — with the
    register entry still sitting there, describing a divergence nothing looks
    for any more. `test_every_acknowledgement_still_describes_a_real_divergence`
    would catch that as a stale entry, but it would read as good news rather
    than as a broken join, so the join is asserted on its own.
    """
    joined = {(entry.capability, entry.property_id) for entry in _flat_declared()}
    assert ("meter", "active-power") in joined, "the flat schema's circuit active-power no longer reaches the meter catalog"

    respellings = {("breaker", "poles"), ("meter", "voltage-a"), ("info", "firmware-version"), ("load-shed", "priority")}
    assert not (joined & respellings), (
        "the flat join now compares across a rename. A pre-catalog spelling of a catalogued property "
        "is an adapter concern, not a mislabel, and comparing across it invents divergences."
    )


def test_every_captured_node_names_a_capability() -> None:
    """`_from_description` skips a node whose `$type` names no capability.

    Nothing in a capture we hold does that, and pinning it here is what keeps
    the skip from becoming a way for the surface to shrink unnoticed — a node
    that lost its `$type` would drop off the comparison silently.
    """
    descriptions = (
        _tree_descriptions() + _wire_descriptions(_simulator_wire()) + _wire_descriptions(tree_payloads.parent_child_tree())
    )
    untyped = sorted(set(_untyped_nodes(descriptions)))

    assert not untyped, f"captured nodes declaring no eBus capability type: {untyped}"


# ---------------------------------------------------------------------------
# Proof that it bites
# ---------------------------------------------------------------------------


def test_a_relabelled_unit_in_a_capture_is_reported() -> None:
    """Mutation proof, on the exact shape of the defect this exists to catch.

    A capture is copied, one circuit's `meter/active-power` is relabelled `kW`
    the way the flat schema has it, and the survey is re-run over the copy. The
    real captures are untouched — the point is that the reader, not a fixture,
    is what notices.
    """
    mutated = copy.deepcopy(_json_object(_SIMULATOR_TREE))
    relabelled = 0
    for device in _objects(mutated).values():
        meter = declared_nodes(device).get(NODE_METER, {})
        for property_id, definition in declared_properties(meter).items():
            if property_id == "active-power" and definition.get("unit") == "W":
                definition["unit"] = "kW"
                relabelled += 1
    assert relabelled, "no captured device declares meter/active-power in W; the mutation proves nothing"

    surface = {SIMULATOR_TREE: [d for device in _objects(mutated).values() for d in _from_description(device)]}
    reported = _divergences(surface)

    mislabel = Divergence("meter", "active-power", Divergent.UNIT, "kW", "W")
    assert mislabel in reported, f"a relabelled unit was not reported; found {sorted(str(d) for d in reported)}"
    assert reported[mislabel] == frozenset({SIMULATOR_TREE}), "the finding names the wrong producer"

    assert mislabel in _REGISTER, "the register happens to carry this one, from the flat schema"
    assert _REGISTER[mislabel].observed_in == (FLAT_SCHEMA,), (
        "which is why the same divergence arriving from a v1.0 capture fails "
        "test_every_acknowledgement_names_the_producers_that_still_show_it rather than passing quietly"
    )


def test_a_relabelled_datatype_in_a_capture_is_reported() -> None:
    """The other field, mutated the same way.

    `unit` and `datatype` are compared by different rules — one family-aware,
    one exact — so proving one bites does not prove the other does.
    """
    mutated = copy.deepcopy(_json_object(_SIMULATOR_TREE))
    relabelled = 0
    for device in _objects(mutated).values():
        breaker = declared_nodes(device).get("breaker", {})
        for property_id, definition in declared_properties(breaker).items():
            if property_id == "rating":
                definition["datatype"] = "string"
                relabelled += 1
    assert relabelled, "no captured device declares breaker/rating; the mutation proves nothing"

    surface = {SIMULATOR_TREE: [d for device in _objects(mutated).values() for d in _from_description(device)]}
    reported = _divergences(surface)

    assert (
        Divergence("breaker", "rating", Divergent.DATATYPE, "string", "integer") in reported
    ), f"a relabelled datatype was not reported; found {sorted(str(d) for d in reported)}"
