"""What the reference tree declares that this adapter reads nothing from.

`build_discovery` answers that question at runtime, for the panel in front of
the user, by subtracting four enumerations of what schema_1 addresses from what
the tree declares. Three of those enumerations are hand-written, so the answer
is only as good as they are — and a stale entry fails *silently*, by keeping a
property out of discovery rather than by raising.

So every entry is checked by the same experiment the consumer-side gate uses:
republish one declared property with a legal different value, rebuild the
snapshot through the real mapper, and see whether any snapshot field moved. That
is a fact about the code rather than about a table, and it is what makes the
discovery output mean "nothing here reads this" instead of "nobody wrote it
down".

The two directions are asserted separately because they fail differently. An
entry in `_CONSUMED_WITHOUT_A_ROW` that moves nothing is a property that has
silently dropped out of discovery. A discovered row that *does* move something
is a false positive, and false positives are what teach a maintainer to stop
reading a report.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
import dataclasses
from functools import lru_cache
import json

from ebus_sdk.homie import DiscoveredDevice
import pytest

from span_panel_api.models import DiscoveredMetadata, SpanPanelSnapshot, is_discovery_path
from span_panel_api_schema_1 import field_metadata as field_metadata_module
from span_panel_api_schema_1.const import (
    DEVICE_TYPE_PREFIX,
    NODE_METER,
    NODE_STATUS,
    TYPE_CIRCUIT,
    TYPE_LUGS,
    TYPE_PANEL,
)
from span_panel_api_schema_1.field_metadata import (
    _ADDRESSED,
    _CONSUMED_OFF_SNAPSHOT,
    _CONSUMED_WITHOUT_A_ROW,
    _PROPERTY_FIELD_MAP,
    build_discovery,
    build_field_metadata,
)
from span_panel_api_schema_1.reference_payloads import (
    device_from_topics,
    devices_from_tree,
    parent_child_tree,
)
from span_panel_api_schema_1.snapshot import build_snapshot

PANEL_DEVICE_ID = "example-40t-001"
"""The enclosure in the reference capture. Every other device is its child."""

Tree = dict[str, dict[str, str]]
Declaration = tuple[str, str, str]
"""``(device type, node, property)`` — the granularity every table here uses."""


# --- the tree, and the experiment over it ----------------------------------


def _tree() -> Tree:
    """A mutable, one-level-deep copy of the capture. One topic is one string."""
    return {device_id: dict(topics) for device_id, topics in parent_child_tree().items()}


def _devices(tree: Tree) -> list[DiscoveredDevice]:
    return [device_from_topics(device_id, topics) for device_id, topics in tree.items()]


def _snapshot(tree: Tree) -> SpanPanelSnapshot:
    panel = device_from_topics(PANEL_DEVICE_ID, tree[PANEL_DEVICE_ID])
    children = [device_from_topics(device_id, topics) for device_id, topics in tree.items() if device_id != PANEL_DEVICE_ID]
    return build_snapshot(panel, children)


def _mapping(value: object) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        return {}
    return {str(key): item for key, item in value.items()}


def _text(value: object) -> str:
    return value if isinstance(value, str) else ""


def _path(declaration: Declaration) -> str:
    """The discovery path a declaration would be reported under."""
    device_type, node_id, property_id = declaration
    return f"discovered.{device_type.removeprefix(DEVICE_TYPE_PREFIX)}/{node_id}/{property_id}"


def _record(fields: dict[str, str], prefix: str, obj: object) -> None:
    if not dataclasses.is_dataclass(obj) or isinstance(obj, type):
        return
    for field in dataclasses.fields(obj):
        fields[f"{prefix}.{field.name}"] = repr(getattr(obj, field.name))


def _snapshot_fields(snapshot: SpanPanelSnapshot) -> dict[str, str]:
    """Flatten a snapshot to ``{path: repr(value)}``, keyed per instance.

    The circuit and EVSE maps are keyed by their own ids so two instances cannot
    mask each other's change, and values are held as `repr` so the comparison is
    a plain string diff whatever a field holds.
    """
    fields: dict[str, str] = {}
    for field in dataclasses.fields(snapshot):
        value = getattr(snapshot, field.name)
        if field.name == "extension_properties":
            # Excluded, and the exclusion is the point rather than a convenience.
            # This field carries every *unaddressed* declaration by construction,
            # so republishing any property discovery reports would move it — and
            # "does republishing this move a snapshot field" would answer yes for
            # every discovered row, which is the question this oracle exists to
            # ask. What the test still catches is the real defect: a discovered
            # property that moves a *curated* field, i.e. one the mapper reads
            # while the addressed set says it does not.
            continue
        if field.name in {"circuits", "evse"}:
            for key, item in value.items():
                _record(fields, f"{field.name}@{key}", item)
        elif dataclasses.is_dataclass(value) and not isinstance(value, type):
            _record(fields, field.name, value)
        else:
            fields[f"panel.{field.name}"] = repr(value)
    return fields


def _instances(tree: Tree) -> dict[Declaration, list[tuple[str, str, Mapping[str, object]]]]:
    """Every declaration in the tree, with the ``(device id, topic, body)`` of each instance."""
    found: dict[Declaration, list[tuple[str, str, Mapping[str, object]]]] = {}
    for device_id, topics in tree.items():
        description = _mapping(json.loads(topics["$description"]))
        device_type = _text(description.get("type"))
        for node_id, node in _mapping(description.get("nodes")).items():
            for property_id, definition in _mapping(_mapping(node).get("properties")).items():
                found.setdefault((device_type, node_id, property_id), []).append(
                    (device_id, f"{node_id}/{property_id}", _mapping(definition))
                )
    return found


def _perturbed(body: Mapping[str, object], current: str | None) -> str:
    """A legal value for this property that differs from `current`.

    Legality matters: a value the parser rejects leaves the field unchanged and
    the property reads as unconsumed. So the replacement is built from the same
    declared `datatype` and `format` the mapper parses against.
    """
    datatype = _text(body.get("datatype"))
    if datatype in {"float", "integer"}:
        try:
            number = float(current or "")
        except ValueError:
            return "7" if datatype == "integer" else "7.5"
        return str(int(number) + 7) if datatype == "integer" else str(number + 7.5)
    if datatype == "boolean":
        return "false" if (current or "").lower() == "true" else "true"
    if datatype == "enum":
        for option in _text(body.get("format")).split(","):
            if option and option != current:
                return option
    return "probe-value" if current != "probe-value" else "probe-value-2"


@lru_cache(maxsize=1)
def _moved() -> Mapping[Declaration, frozenset[str]]:
    """Republish each declared property once; return the snapshot fields it moved.

    One rebuild per declaring *device*, unioned: the two lugs devices and the
    five circuits declare the same properties and are read differently, so a
    single probe against whichever came first would answer for both.
    """
    tree = _tree()
    baseline = _snapshot_fields(_snapshot(tree))
    moved: dict[Declaration, frozenset[str]] = {}
    for declaration, instances in _instances(tree).items():
        changed: set[str] = set()
        for device_id, topic, body in instances:
            current = tree[device_id].get(topic)
            replacement = _perturbed(body, current)
            assert replacement != current, (
                f"{declaration} on {device_id}: the probe equals the published value "
                f"({current!r}), so this property is not being tested"
            )
            mutated = {other: dict(topics) for other, topics in tree.items()}
            mutated[device_id][topic] = replacement
            after = _snapshot_fields(_snapshot(mutated))
            changed.update(path for path, value in after.items() if baseline.get(path) != value)
        moved[declaration] = frozenset(changed)
    return moved


def _declared() -> frozenset[Declaration]:
    return frozenset(_instances(_tree()))


def _discovered() -> dict[str, DiscoveredMetadata]:
    return build_discovery(devices_from_tree(parent_child_tree()))


def _rendered(declarations: Iterable[Declaration]) -> str:
    return "\n".join(f"  {'/'.join(item)}" for item in sorted(declarations)) or "  (none)"


# --- the experiment must be able to observe anything at all -----------------


def test_the_probe_moves_something_for_a_known_reading() -> None:
    """The floor under every assertion below.

    All of them are satisfied by a probe that changes nothing, ever: the tables
    would simply have to grow to match. This fails first if that happens.
    """
    moved = _moved()[(TYPE_CIRCUIT, NODE_METER, "active-power")]
    assert any(path.startswith("circuits@") and path.endswith(".instant_power_w") for path in moved), (
        f"republishing a circuit's active power moved {sorted(moved)}, which does not "
        "include the reading it produces — the experiment is not observing the mapper"
    )


# --- the enumerations, checked against the mapper rather than against prose --


def test_every_property_consumed_without_a_row_really_moves_the_snapshot() -> None:
    """An entry that stops being true drops a property out of discovery silently.

    This is the direction with no natural signal: an over-broad "we read this"
    table produces a *smaller* report, and a smaller report looks exactly like a
    panel with nothing new on it.
    """
    moved = _moved()
    declared = _declared()
    inert = [entry for entry in _CONSUMED_WITHOUT_A_ROW if entry in declared and not moved[entry]]
    assert not inert, (
        "_CONSUMED_WITHOUT_A_ROW claims these are read into the snapshot and "
        f"republishing them moves nothing:\n{_rendered(inert)}\n"
        "Either the mapper stopped reading them — in which case they belong in "
        "discovery — or the route is off-snapshot and belongs in "
        "_CONSUMED_OFF_SNAPSHOT with the code that reads it named."
    )


def test_an_off_snapshot_route_that_became_observable_must_be_retired() -> None:
    """The mirror of the integration's `test_no_internal_route_is_observable_after_all`.

    `_CONSUMED_OFF_SNAPSHOT` is the one table the experiment cannot verify
    positively, so it is the one that could quietly become an allowlist. It is
    held to the opposite claim instead: the moment a route's property does move
    a snapshot field, the route is no longer the only thing consuming it and the
    entry is hiding a real reader from whoever adds a property beside it.
    """
    moved = _moved()
    observable = [entry for entry in _CONSUMED_OFF_SNAPSHOT if moved.get(entry)]
    assert not observable, (
        "off-snapshot route entries whose property now moves a snapshot field:\n"
        f"{_rendered(observable)}\nDelete the entry; the mapper reads it now."
    )


def test_no_addressed_entry_has_gone_stale() -> None:
    """A table entry outlives its declaration silently; the file only ever grows."""
    declared = _declared()
    stale = [entry for entry in (*_CONSUMED_WITHOUT_A_ROW, *_CONSUMED_OFF_SNAPSHOT) if entry not in declared]
    assert not stale, f"addressed-property entries the reference tree no longer declares:\n{_rendered(stale)}"


def test_no_addressed_entry_duplicates_a_metadata_row() -> None:
    """The four tables partition the addressed set; they do not overlap.

    A property with a `_PROPERTY_FIELD_MAP` row already states its unit and
    datatype for a snapshot field. Listing it again as read-without-a-row would
    make the second entry unfalsifiable — deleting it changes nothing, so the
    experiment above could never report it stale.
    """
    with_rows = {(device_type, node_id, property_id) for device_type, node_id, property_id, _field in _PROPERTY_FIELD_MAP}
    duplicated = [entry for entry in (*_CONSUMED_WITHOUT_A_ROW, *_CONSUMED_OFF_SNAPSHOT) if entry in with_rows]
    assert not duplicated, f"addressed twice, once with a metadata row:\n{_rendered(duplicated)}"


def test_every_off_snapshot_route_names_the_code_that_reads_it() -> None:
    """A reason-less entry is an allowlist line wearing an exemption's clothes."""
    thin = [entry for entry, reason in _CONSUMED_OFF_SNAPSHOT.items() if len(reason.split()) < 6]
    assert not thin, f"off-snapshot entries with no usable reason:\n{_rendered(thin)}"


# --- what discovery reports, and that it is exactly right -------------------


def test_discovery_reports_only_declarations_that_move_nothing() -> None:
    """The claim a discovered row makes, asserted against the mapper.

    A row that moves a snapshot field is a false positive: something does read
    it, and reporting it as unread sends a maintainer looking for a gap that is
    not there.
    """
    moved = _moved()
    reported = set(_discovered())
    false_positives = [entry for entry in sorted(_declared()) if _path(entry) in reported and moved[entry]]
    assert not false_positives, (
        "discovery reports these as unread and republishing them moves a snapshot "
        f"field:\n{_rendered(false_positives)}\nAdd them to _CONSUMED_WITHOUT_A_ROW."
    )


def test_discovery_finds_every_declaration_nothing_reads() -> None:
    """The converse, so an over-broad addressed table cannot shrink the report.

    Together with the test above this pins the output exactly: discovery is the
    set of declarations that move no snapshot field, less the three routes that
    are consumed where no snapshot field can show it.
    """
    moved = _moved()
    reported = set(_discovered())
    missing = [
        entry
        for entry in sorted(_declared())
        if not moved[entry] and entry not in _CONSUMED_OFF_SNAPSHOT and _path(entry) not in reported
    ]
    assert not missing, (
        "these declarations move no snapshot field and discovery does not report "
        f"them:\n{_rendered(missing)}\nAn addressed-property table claims a reader "
        "that does not exist."
    )


def test_discovery_is_not_empty_on_the_reference_tree() -> None:
    """A report that is always empty passes every assertion above.

    The reference capture is known to declare properties nothing reads — the
    `connection/count` pair no producer publishes, the two deliberate skips in
    `status`, the redundant `*-device-type` echoes. If this ever legitimately
    reaches zero, the tests above are the ones that keep meaning something and
    this is the one to delete, deliberately.
    """
    assert len(_discovered()) >= 5


def test_discovery_names_the_datatype_and_unit_the_tree_declares() -> None:
    description = _mapping(json.loads(_tree()[PANEL_DEVICE_ID]["$description"]))
    status = _mapping(_mapping(description.get("nodes")).get(NODE_STATUS))
    declared = _mapping(_mapping(status.get("properties")).get("postal-code"))
    row = _discovered()["discovered.distribution-enclosure/status/postal-code"]
    assert row.datatype == _text(declared.get("datatype"))
    assert row.unit == (_text(declared.get("unit")) or None)


def test_retained_says_whether_a_value_has_arrived_and_never_what_it_is() -> None:
    """`retained` is the declared-but-never-valued signal, and the only value question asked."""
    rows = _discovered()
    assert rows["discovered.distribution-enclosure/status/time-zone"].retained is True
    assert rows["discovered.circuit/connection/count"].retained is False

    tree = _tree()
    del tree[PANEL_DEVICE_ID]["status/time-zone"]
    unvalued = build_discovery(_devices(tree))
    assert unvalued["discovered.distribution-enclosure/status/time-zone"].retained is False


def _declaration_strings(tree: Tree) -> set[str]:
    """Every string that appears anywhere in the capture's ``$description`` documents.

    Keys and values alike, plus the pieces of each — comma-separated `format`
    options and dot-separated type stems — because a row names a device type by
    its tail and a `format` option by itself. Published values are deliberately
    not walked: this is the vocabulary a row is permitted to be built from.
    """
    found: set[str] = set()

    def walk(node: object) -> None:
        if isinstance(node, str):
            found.add(node)
            found.update(node.split(","))
            found.update(node.split("."))
        elif isinstance(node, Mapping):
            for key, value in node.items():
                found.add(str(key))
                walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    for topics in tree.values():
        walk(json.loads(topics["$description"]))
    return found


def test_no_published_value_reaches_a_discovery_row() -> None:
    """The privacy constraint, asserted rather than reviewed.

    These rows are built to be forwarded in consumer diagnostics, which leave
    the machine they were generated on, and the consumer's own redaction is
    key-based and knows nothing about wire names — so nothing downstream can
    protect a value put in here.

    Checked by provenance rather than by scanning for known strings, because a
    scan is only as good as the capture's values happen to be distinctive.
    Every string a row carries has to decompose into the vocabulary of the
    `$description` documents, which hold no published values at all; the only
    thing a row says about a value is the boolean `retained`. The scan runs too,
    over the values that are *not* declaration vocabulary, as the empirical
    half.
    """
    tree = _tree()
    allowed = _declaration_strings(tree)
    rows = _discovered()
    assert rows, "no rows, so this proves nothing"

    for path, row in rows.items():
        namespace, _, body = path.partition(".")
        assert namespace == "discovered"
        components = body.split("/")
        assert len(components) == 3, f"{path} is not device-type/node/property"
        for component in components:
            assert component in allowed, f"{component!r} in {path} came from outside a declaration"
        assert row.datatype in allowed
        assert row.unit is None or row.unit in allowed
        assert isinstance(row.retained, bool)

    published = {
        value
        for topics in tree.values()
        for topic, value in topics.items()
        if not topic.startswith("$") and value and value not in allowed
    }
    assert published, "every published value is also declaration vocabulary; the scan is vacuous"
    emitted = "\n".join(f"{path} {row.datatype} {row.unit} {row.retained}" for path, row in rows.items())
    leaked = sorted(value for value in published if value in emitted)
    assert not leaked, f"published values reached the discovery rows: {leaked}"


# --- the partition, and that it holds --------------------------------------


def test_every_discovered_row_is_namespaced_and_no_curated_row_is() -> None:
    """The partition a consumer applies, checked on the real metadata dict.

    `build_field_metadata` returns both kinds in one map, so the namespace is
    the only thing standing between a discovered property and a consumer's
    inventory of produced fields.
    """
    metadata = build_field_metadata(devices_from_tree(parent_child_tree()))
    discovered = set(_discovered())
    assert discovered, "no discovered rows, so the partition is untested"
    assert discovered <= set(metadata)

    for path, row in metadata.items():
        if path in discovered:
            assert is_discovery_path(path)
            assert isinstance(row, DiscoveredMetadata)
        else:
            assert not is_discovery_path(path), f"{path} is curated and sits in the namespace"
            assert not isinstance(row, DiscoveredMetadata)


def test_a_curated_field_path_is_never_a_discovery_path() -> None:
    """No `_PROPERTY_FIELD_MAP` row can collide with the namespace."""
    for _device_type, _node, _prop, field_path in _PROPERTY_FIELD_MAP:
        assert not is_discovery_path(field_path)


# --- it bites in both directions -------------------------------------------


def test_a_property_nothing_reads_appears_with_its_declared_datatype_and_unit() -> None:
    """Add a declaration to a copy of the tree; discovery must name it.

    The whole point of the runtime half: a panel in the field that starts
    publishing a property is invisible to a vendored capture until somebody
    recaptures, and this is the mechanism that makes it visible without one.
    """
    tree = _tree()
    description = _mapping(json.loads(tree[PANEL_DEVICE_ID]["$description"]))
    nodes = dict(_mapping(description.get("nodes")))
    status = dict(_mapping(nodes.get(NODE_STATUS)))
    status["properties"] = {
        **_mapping(status.get("properties")),
        "enclosure-temperature": {
            "name": "Enclosure temperature",
            "datatype": "float",
            "unit": "°C",
        },
    }
    nodes[NODE_STATUS] = status
    tree[PANEL_DEVICE_ID]["$description"] = json.dumps({**description, "nodes": nodes})
    tree[PANEL_DEVICE_ID]["status/enclosure-temperature"] = "41.5"

    row = build_discovery(_devices(tree))["discovered.distribution-enclosure/status/enclosure-temperature"]
    assert row.datatype == "float"
    assert row.unit == "°C"
    assert row.retained is True
    assert "41.5" not in repr(row)


def test_a_property_that_becomes_read_leaves_the_report(monkeypatch: pytest.MonkeyPatch) -> None:
    """The other direction: once something addresses a property, it stops being reported.

    Mapping is what a maintainer does in response to a discovered row, so the
    row disappearing is the acceptance criterion for that work. Patched rather
    than edited so the test states the rule instead of tracking whichever
    property happens to be unread this month.
    """
    path = "discovered.distribution-enclosure/status/postal-code"
    assert path in _discovered()

    monkeypatch.setattr(
        field_metadata_module,
        "_ADDRESSED",
        _ADDRESSED | {(TYPE_PANEL, NODE_STATUS, "postal-code")},
    )
    assert path not in build_discovery(devices_from_tree(parent_child_tree()))


def test_a_subtyped_device_does_not_report_its_parents_mapped_properties() -> None:
    """Subtyping is the false positive that would make the report unreadable.

    Firmware may declare `…device.lugs.upstream` rather than `…device.lugs` with
    a direction property. Every mapped lugs property would then look
    unaddressed — ten panel readings reported as newly discovered on a panel
    where nothing changed.
    """
    tree = _tree()
    description = _mapping(json.loads(tree["lugs-upstream"]["$description"]))
    tree["lugs-upstream"]["$description"] = json.dumps({**description, "type": f"{TYPE_LUGS}.upstream"})

    rows = build_discovery(_devices(tree))
    assert not [path for path in rows if path.startswith("discovered.lugs.upstream/meter/")]
    assert "discovered.lugs.upstream/connection/count" in rows


def test_the_charge_current_pair_is_addressed_by_resolution_not_by_a_table() -> None:
    """A charger names its own charge-limit node, so discovery must resolve it too.

    `_charge_limit_metadata` produces rows for whichever spelling the charger
    declares. A hardcoded pair here would report the other spelling as unread on
    every charger that used it.
    """
    rows = _discovered()
    assert not [path for path in rows if path.endswith("max-charge-current")]


def test_a_device_mid_discovery_contributes_nothing() -> None:
    """A device the tree names before it has described itself is normal, not a finding."""
    undescribed = DiscoveredDevice("not-yet-described", "ebus")
    assert not build_discovery([undescribed])
