"""The masked export: each device's placeholder, and the document built with them.

Each property runs over every vendored capture, the two main32 fixtures and the
reference tree, and selects what to check by what each tree declares. The
expected masks are restated here from the design rather than read from the
module, so a rule changed there fails here.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re

from ebus_sdk.homie import DiscoveredDevice
import pytest

from span_panel_api.models import MaskedTree, MaskedTreeDevice, SpanPanelSnapshot
from span_panel_api_schema_1 import SchemaOneAdapter
from span_panel_api_schema_1.tree_export import export_masked_tree, natural_key, placeholder_ids

from reference_payloads.captures import CAPTURES, capture_path, capture_tree, capture_tree_from_document
from reference_payloads.reidentify import rename_devices
from reference_payloads.schema_one import RetainedTopicTree, devices_from_tree, parent_child_tree, replay
from reference_payloads.synthetic_trees import SyntheticDevice, panel, tree

_FIXTURES = Path(__file__).parent / "fixtures"
_MAIN32_FIXTURES = ("main32-tree-v1.json", "main32_r202639-tree-v1.json")
_REFERENCE_ROOT = "example-40t-001"
_NEW_ROOT = "synthetic-root-0001"
_SPACES = "info/spaces"
_DIRECTION = "info/direction"
_TYPE_CIRCUIT = "energy.ebus.device.circuit"
_TYPE_LUGS = "energy.ebus.device.lugs"
_TYPE_MID = "energy.ebus.device.mid"
_GENERATOR_IDS = [f"g-{n}" for n in range(2, 12)]


@dataclass(frozen=True)
class Case:
    name: str
    topics: RetainedTopicTree
    root_id: str
    source: Path | None = None
    """The `tree-v1` file the case was read from; None for the retained-topic reference tree."""

    def __repr__(self) -> str:
        return self.name


def _main32_case(file_name: str) -> Case:
    document: object = json.loads((_FIXTURES / file_name).read_text(encoding="utf-8"))
    assert isinstance(document, dict)
    captured = capture_tree_from_document(document, file_name)
    return Case(file_name, captured.retained(), captured.root_id, _FIXTURES / file_name)


def _cases() -> list[Case]:
    captures = [
        Case(stem, capture_tree(stem).retained(), capture_tree(stem).root_id, capture_path(stem)) for stem in CAPTURES
    ]
    return [
        *captures,
        *(_main32_case(name) for name in _MAIN32_FIXTURES),
        Case("reference", parent_child_tree(), _REFERENCE_ROOT),
    ]


CASES = _cases()
every_tree = pytest.mark.parametrize("case", CASES, ids=repr)


def _ids_of(adapter: SchemaOneAdapter, root_id: str) -> dict[str, str]:
    root = adapter._controller.get_root(root_id)
    assert root is not None
    return placeholder_ids(root, adapter._children())


def _placeholders(topics: RetainedTopicTree, root_id: str) -> dict[str, str]:
    return _ids_of(replay(topics, root_id), root_id)


def _value(device: DiscoveredDevice, topic: str) -> str | None:
    node, _, prop = topic.partition("/")
    value = device.properties.get(node, {}).get(prop)
    return None if value is None else str(value)


def _of_type(case: Case, device_type: str) -> list[DiscoveredDevice]:
    return [
        device
        for device in devices_from_tree(case.topics)
        if device.description is not None and device.description.get("type") == device_type
    ]


@every_tree
def test_every_placeholder_is_prefixed(case: Case) -> None:
    ids = _placeholders(case.topics, case.root_id)
    assert set(ids) == set(case.topics)
    assert all(placeholder.startswith("masked-") for placeholder in ids.values())
    assert len(set(ids.values())) == len(ids)


@every_tree
def test_circuits_are_numbered_by_first_breaker_space(case: Case) -> None:
    ids = _placeholders(case.topics, case.root_id)
    hosted = [
        (min(int(space) for space in re.findall(r"[0-9]+", spaces)), device.device_id)
        for device in _of_type(case, _TYPE_CIRCUIT)
        if (spaces := _value(device, _SPACES))
    ]
    assert hosted, "no circuit publishes info/spaces"
    in_space_order = [device_id for _, device_id in sorted(hosted, key=lambda entry: (entry[0], natural_key(entry[1])))]
    assert [ids[device_id] for device_id in in_space_order] == [f"masked-circuit-{n}" for n in range(1, len(hosted) + 1)]


@every_tree
def test_lugs_take_their_direction(case: Case) -> None:
    ids = _placeholders(case.topics, case.root_id)
    lugs = {_value(device, _DIRECTION): device.device_id for device in _of_type(case, _TYPE_LUGS)}
    assert {"UPSTREAM", "DOWNSTREAM"} <= lugs.keys()
    assert ids[lugs["UPSTREAM"]] == "masked-lugs-upstream"
    assert ids[lugs["DOWNSTREAM"]] == "masked-lugs-downstream"


def _generators(ids: list[str]) -> RetainedTopicTree:
    return tree(
        panel(),
        *(
            SyntheticDevice(
                device_id=device_id, device_type="energy.ebus.device.generator", name="Generator", nodes={}, values={}
            )
            for device_id in ids
        ),
    )


@pytest.mark.parametrize("case", [*CASES, Case("many-generators", _generators(_GENERATOR_IDS), "panel-a")], ids=repr)
def test_placeholders_survive_an_id_change(case: Case) -> None:
    rank = {device_id: position for position, device_id in enumerate(sorted(case.topics, key=natural_key), start=1)}

    def new_id(old: str) -> str:
        return _NEW_ROOT if old == case.root_id else f"{_NEW_ROOT}-renamed-{rank[old]}"

    renamed, new_root = rename_devices(case.topics, case.root_id, new_id)
    before = _placeholders(case.topics, case.root_id)
    after = _placeholders(renamed, new_root)
    assert {new_id(old): placeholder for old, placeholder in before.items()} == after


def test_natural_order_compares_numbers() -> None:
    ids = ["g-1", "g-2", "g-3", "g-10", "g-11"]
    placeholders = _placeholders(_generators(list(reversed(ids))), "panel-a")
    assert [placeholders[device_id] for device_id in ids] == [f"masked-generator-{n}" for n in range(1, 6)]


def test_an_undescribed_device_is_named_as_such() -> None:
    topics = parent_child_tree()
    withheld = "pv"
    adapter = replay(topics, _REFERENCE_ROOT, withhold_description_of=withheld)
    ids = _ids_of(adapter, _REFERENCE_ROOT)
    assert ids[withheld] == "masked-undescribed-1"
    assert len(set(ids.values())) == len(ids)
    assert not any("pv" in placeholder for placeholder in ids.values())


def test_lugs_without_a_usable_direction_are_numbered() -> None:
    topics = {device_id: dict(values) for device_id, values in parent_child_tree().items()}
    topics["lugs-downstream"]["info/direction"] = "UPSTREAM"
    topics["lugs-upstream"].pop("info/direction")
    ids = _placeholders(topics, _REFERENCE_ROOT)
    assert ids["lugs-downstream"] == "masked-lugs-upstream"
    assert ids["lugs-upstream"] == "masked-lugs-1"


def test_a_second_lugs_claiming_a_taken_direction_is_numbered() -> None:
    topics = {device_id: dict(values) for device_id, values in parent_child_tree().items()}
    topics["lugs-downstream"]["info/direction"] = "UPSTREAM"
    ids = _placeholders(topics, _REFERENCE_ROOT)
    assert ids["lugs-downstream"] == "masked-lugs-upstream"
    assert ids["lugs-upstream"] == "masked-lugs-1"
    assert len(set(ids.values())) == len(ids)


def test_ids_tied_in_natural_order_are_named_the_same_in_any_input_order() -> None:
    tied = ["g-1", "g-01", "G-1", "g-2"]
    assert len({natural_key(device_id) for device_id in tied[:3]}) == 1
    devices = devices_from_tree(_generators(tied))
    root, children = devices[0], devices[1:]
    forward = placeholder_ids(root, children)
    assert placeholder_ids(root, list(reversed(children))) == forward
    assert placeholder_ids(root, children[1:] + children[:1]) == forward
    assert len(set(forward.values())) == len(forward)


# The document. Masks and kept values as the design states them (§4.3).

_PRODUCER = "span-panel-api-schema-1 0.0.0"
_SERIAL = "info/serial-number"
_NAME = "info/name"
_MASKED_PANEL = "Masked Panel"
_SITE_MASKS: Mapping[str, str] = {
    "info/utility-meter-serial-number": "MASKED-METER",
    "status/wifi-ssid": "masked-ssid",
    "status/postal-code": "00000",
    "info/address-lines": "Masked Address",
    "info/locality": "Masked City",
    "info/region": "Masked Region",
    "info/latitude": "0.0",
    "info/longitude": "0.0",
}
_FEEDS = "connection/feeds-device-id"
_FED_BY = "connection/fed-by-device-id"
_GRID_FORMING = "grid/grid-forming-entity"
_SHARED_WITH = "meter/shared-with-device-ids"
_LIST_SUFFIX = "/shared-with-device-ids"
_NUMERIC = ("integer", "float")
_MISSING = re.compile(r"masked-missing-[1-9][0-9]*")
_SEEDED_SITE: Mapping[str, tuple[str, str]] = {
    "info/utility-meter-serial-number": ("SEEDED-METER-77", "string"),
    "status/wifi-ssid": ("seeded-network", "string"),
    "status/postal-code": ("98000", "string"),
    "info/address-lines": ("4821 Seeded Lane", "string"),
    "info/locality": ("Seededville", "string"),
    "info/region": ("ZQ", "string"),
    "info/latitude": ("47.6", "float"),
    "info/longitude": ("-122.3", "float"),
    _NAME: ("Seeded Residence", "string"),
    "info/country-code": ("QZ", "string"),
    "status/time-zone": ("Etc/GMT+9", "string"),
}
_KEPT_SITE = ("info/country-code", "status/time-zone")
_SEEDED_LABEL = "Seeded Lugs Label"


def _object(value: object) -> dict[str, object]:
    assert isinstance(value, dict)
    return {str(key): item for key, item in value.items()}


def _description_of(topics: Mapping[str, str]) -> dict[str, object]:
    return _object(json.loads(topics["$description"]))


def _declared(description: Mapping[str, object]) -> dict[str, str]:
    """`node/property` -> declared datatype, for every property the description declares."""
    return {
        f"{node_id}/{property_id}": str(_object(definition).get("datatype"))
        for node_id, node in _object(description.get("nodes")).items()
        for property_id, definition in _object(_object(node).get("properties")).items()
    }


def _published(topics: Mapping[str, str]) -> dict[str, str]:
    """The retained values of one device, without its `$` attributes or `$target`s."""
    return {topic: value for topic, value in topics.items() if not topic.startswith("$") and "/$" not in topic}


def _type_of(topics: Mapping[str, str]) -> str:
    return str(_description_of(topics).get("type"))


def _declaring(topics: RetainedTopicTree, topic: str, device_type: str | None = None) -> list[str]:
    """Devices that declare `topic`, optionally of one type, in natural id order."""
    return [
        device_id
        for device_id in sorted(topics, key=natural_key)
        if topic in _declared(_description_of(topics[device_id]))
        and (device_type is None or _type_of(topics[device_id]) == device_type)
    ]


def _copy(topics: RetainedTopicTree) -> dict[str, dict[str, str]]:
    return {device_id: dict(values) for device_id, values in topics.items()}


def _export(adapter: SchemaOneAdapter, root_id: str, *, tree_complete: bool = True) -> MaskedTree:
    root = adapter._controller.get_root(root_id)
    assert root is not None
    return export_masked_tree(root, adapter._children(), producer=_PRODUCER, tree_complete=tree_complete)


def _exported(topics: RetainedTopicTree, root_id: str) -> tuple[MaskedTree, dict[str, str]]:
    adapter = replay(topics, root_id)
    return _export(adapter, root_id), _ids_of(adapter, root_id)


def _valued(entry: MaskedTreeDevice) -> dict[str, str]:
    """Every value the export carries for a described device; the three maps never share a key."""
    maps = (entry["properties"], entry["numeric_properties"], entry["undeclared_properties"])
    merged = {topic: value for values in maps for topic, value in values.items()}
    assert len(merged) == sum(len(values) for values in maps)
    return merged


def _is_reference(topic: str) -> bool:
    return topic in (_FEEDS, _FED_BY, _GRID_FORMING) or topic.endswith(_LIST_SUFFIX)


def _named(topic: str, value: str) -> list[str]:
    """The ids an id-valued property names as published: list elements trimmed, the grid itself none."""
    if topic.endswith(_LIST_SUFFIX):
        return [part.strip() for part in value.split(",") if part.strip()]
    if topic == _GRID_FORMING and value.strip().upper() == "GRID":
        return []
    return [value] if value else []


def _assert_resolves(topic: str, published: str, exported: str, ids: Mapping[str, str], missing: dict[str, str]) -> None:
    """Each id the value names is its device's placeholder, or one `masked-missing-<n>` per absent id."""
    named = _named(topic, published)
    if topic == _GRID_FORMING and not named:
        assert exported == published
        return
    placeholders = exported.split(",") if topic.endswith(_LIST_SUFFIX) and exported else [exported] if exported else []
    assert len(placeholders) == len(named), (topic, published, exported)
    for original, placeholder in zip(named, placeholders, strict=True):
        if original in ids:
            assert placeholder == ids[original], (topic, original)
        else:
            assert _MISSING.fullmatch(placeholder), (topic, original, placeholder)
            assert missing.setdefault(original, placeholder) == placeholder


def _property_id(topic: str) -> str:
    return topic.partition("/")[2]


_SERIAL_ID = _property_id(_SERIAL)
_MASKS_BY_PROPERTY: Mapping[str, str] = {_property_id(topic): mask for topic, mask in _SITE_MASKS.items()}
"""The site masks by property id: they apply on whichever node publishes the property."""
_SEEDED_BY_PROPERTY: Mapping[str, tuple[str, str]] = {
    **{_property_id(topic): seeded for topic, seeded in _SEEDED_SITE.items() if _property_id(topic) in _MASKS_BY_PROPERTY},
    _SERIAL_ID: ("SEEDSN00001", "string"),
}
"""A seeded value and datatype for every property id masked on any node."""


def _assert_masked(
    topic: str,
    published: str,
    exported: str,
    *,
    owner: str,
    root_id: str,
    ids: Mapping[str, str],
    missing: dict[str, str],
) -> None:
    """The exported value is the published one with the design's masks applied, and nothing else changed."""
    if published == "":
        assert exported == ""
    elif owner == root_id and topic == _NAME:
        assert exported == _MASKED_PANEL
    elif _property_id(topic) == _SERIAL_ID:
        assert exported == ids[owner].upper()
    elif _property_id(topic) in _MASKS_BY_PROPERTY:
        assert exported == _MASKS_BY_PROPERTY[_property_id(topic)]
    elif _is_reference(topic):
        _assert_resolves(topic, published, exported, ids, missing)
    else:
        assert exported == published, topic


def _declare(description: dict[str, object], topic: str, datatype: str) -> None:
    """Add `topic` to a description's declarations unless it is already declared."""
    node_id, _, property_id = topic.partition("/")
    nodes = description.setdefault("nodes", {})
    assert isinstance(nodes, dict)
    node = nodes.setdefault(node_id, {"name": node_id, "type": f"energy.ebus.capability.{node_id}", "properties": {}})
    assert isinstance(node, dict)
    declared = node.setdefault("properties", {})
    assert isinstance(declared, dict)
    declared.setdefault(property_id, {"name": property_id, "datatype": datatype})


def _seeded(case: Case) -> tuple[dict[str, dict[str, str]], str]:
    """The tree with every masked and kept property declared and valued with a seeded value.

    Every device declares and publishes a distinct serial; the panel declares
    and publishes every site property and a seeded `$description` name; one
    device that is neither the panel nor a circuit declares and publishes an
    `info/name` of its own, returned beside the tree.
    """
    topics = _copy(case.topics)
    labelled = next(
        device_id
        for device_id in sorted(topics, key=natural_key)
        if device_id != case.root_id and _type_of(topics[device_id]) != _TYPE_CIRCUIT
    )
    for number, device_id in enumerate(sorted(topics, key=natural_key), start=1):
        description = _description_of(topics[device_id])
        _declare(description, _SERIAL, "string")
        topics[device_id][_SERIAL] = f"SEEDSN{number:05d}"
        if device_id == case.root_id:
            for topic, (value, datatype) in _SEEDED_SITE.items():
                _declare(description, topic, datatype)
                topics[device_id][topic] = value
            description["name"] = _SEEDED_SITE[_NAME][0]
        if device_id == labelled:
            _declare(description, _NAME, "string")
            topics[device_id][_NAME] = _SEEDED_LABEL
        topics[device_id]["$description"] = json.dumps(description)
    return topics, labelled


_TREE_V1_CASES = [case for case in CASES if case.source is not None]


@pytest.mark.parametrize("case", _TREE_V1_CASES, ids=repr)
def test_split_matches_the_captures(case: Case) -> None:
    assert case.source is not None
    export, ids = _exported(case.topics, case.root_id)
    devices = _object(_object(json.loads(case.source.read_text(encoding="utf-8")))["devices"])
    assert set(export["devices"]) == {ids[device_id] for device_id in devices}
    for device_id, body in devices.items():
        entry = export["devices"][ids[device_id]]
        assert set(entry["properties"]) == set(_object(_object(body)["properties"])), device_id
        assert set(entry["numeric_properties"]) == set(_object(_object(body)["numeric_properties"])), device_id


@every_tree
def test_nothing_is_lost(case: Case) -> None:
    export, ids = _exported(case.topics, case.root_id)
    assert export["metadata"] == {"schema": "tree-v1", "masked": True, "producer": _PRODUCER, "tree_complete": True}
    assert export["undescribed_devices"] == {}
    assert set(export["devices"]) == {ids[device_id] for device_id in case.topics}
    missing: dict[str, str] = {}
    for device_id, topics in case.topics.items():
        entry = export["devices"][ids[device_id]]
        declared = _declared(_description_of(topics))
        assert _declared(entry["description"]) == declared
        published = _published(topics)
        exported = _valued(entry)
        assert exported.keys() == published.keys(), device_id
        for topic, value in published.items():
            _assert_masked(topic, value, exported[topic], owner=device_id, root_id=case.root_id, ids=ids, missing=missing)
        assert set(entry["numeric_properties"]) == {topic for topic in published if declared.get(topic) in _NUMERIC}
        assert set(entry["undeclared_properties"]) == published.keys() - declared.keys()
        assert entry["targets"] == {}
        assert entry.get("state") == topics.get("$state", "ready")
    assert len(set(missing.values())) == len(missing)


@every_tree
def test_descriptions_are_published_with_their_references_mapped(case: Case) -> None:
    export, ids = _exported(case.topics, case.root_id)
    for device_id, topics in case.topics.items():
        expected = _description_of(topics)
        for key in ("parent", "root"):
            if key in expected:
                expected[key] = ids[str(expected[key])]
        children = expected.get("children")
        if isinstance(children, list):
            expected["children"] = [ids[str(child)] for child in children]
        if device_id == case.root_id and "name" in expected:
            expected["name"] = _MASKED_PANEL
        assert export["devices"][ids[device_id]]["description"] == expected, device_id


@every_tree
def test_the_export_leaves_the_tree_as_it_was(case: Case) -> None:
    adapter = replay(case.topics, case.root_id)
    root = adapter._controller.get_root(case.root_id)
    assert root is not None
    devices = [root, *adapter._children()]

    def state() -> str:
        return json.dumps(
            [(device.description, device.properties, device.property_targets, device.state) for device in devices]
        )

    before = state()
    export_masked_tree(root, devices[1:], producer=_PRODUCER, tree_complete=True)
    assert state() == before


@every_tree
def test_the_document_does_not_depend_on_discovery_order(case: Case) -> None:
    adapter = replay(case.topics, case.root_id)
    root = adapter._controller.get_root(case.root_id)
    assert root is not None
    children = adapter._children()
    forward = export_masked_tree(root, children, producer=_PRODUCER, tree_complete=True)
    backward = export_masked_tree(root, list(reversed(children)), producer=_PRODUCER, tree_complete=True)
    assert json.dumps(backward) == json.dumps(forward)


@every_tree
def test_an_empty_value_stays_empty(case: Case) -> None:
    topics = _copy(case.topics)
    declared = _declared(_description_of(topics[case.root_id]))
    plain = [
        topic
        for topic in sorted(declared)
        if _property_id(topic) not in {*_MASKS_BY_PROPERTY, _SERIAL_ID} and topic != _NAME and not _is_reference(topic)
    ]
    text = next(topic for topic in plain if declared[topic] not in _NUMERIC)
    number = next(topic for topic in plain if declared[topic] in _NUMERIC)
    topics[case.root_id][text] = ""
    topics[case.root_id][number] = ""
    export, _ = _exported(topics, case.root_id)
    entry = export["devices"]["masked-panel"]
    assert entry["properties"][text] == ""
    assert entry["numeric_properties"][number] == ""


def test_an_empty_masked_or_referencing_value_stays_empty() -> None:
    topics = _copy(parent_child_tree())
    circuit = _declaring(topics, _FEEDS, _TYPE_CIRCUIT)[0]
    for topic in (_SERIAL, "status/postal-code", "status/wifi-ssid"):
        topics[_REFERENCE_ROOT][topic] = ""
    topics[circuit][_FEEDS] = ""
    export, ids = _exported(topics, _REFERENCE_ROOT)
    panel_values = _valued(export["devices"]["masked-panel"])
    assert {topic: panel_values[topic] for topic in (_SERIAL, "status/postal-code", "status/wifi-ssid")} == {
        _SERIAL: "",
        "status/postal-code": "",
        "status/wifi-ssid": "",
    }
    assert _valued(export["devices"][ids[circuit]])[_FEEDS] == ""


@every_tree
def test_an_undeclared_value_is_kept_apart(case: Case) -> None:
    topics = _copy(case.topics)
    topics[case.root_id]["vendor/extra"] = "seeded-extra"
    export, _ = _exported(topics, case.root_id)
    entry = export["devices"]["masked-panel"]
    assert entry["undeclared_properties"] == {"vendor/extra": "seeded-extra"}
    assert "vendor/extra" not in entry["properties"]
    assert "vendor/extra" not in entry["numeric_properties"]


def test_an_undeclared_value_is_masked_by_its_name() -> None:
    topics = _copy(parent_child_tree())
    circuit = _declaring(topics, _FEEDS, _TYPE_CIRCUIT)[0]
    root_declares = _declared(_description_of(topics[_REFERENCE_ROOT]))
    circuit_declares = _declared(_description_of(topics[circuit]))
    assert "info/address-lines" not in root_declares
    assert _SERIAL not in circuit_declares
    assert _FED_BY not in circuit_declares
    topics[_REFERENCE_ROOT]["info/address-lines"] = "4821 Seeded Lane"
    topics[circuit][_SERIAL] = "SEEDSN00001"
    topics[circuit][_FED_BY] = "bess"
    export, ids = _exported(topics, _REFERENCE_ROOT)
    assert export["devices"]["masked-panel"]["undeclared_properties"] == {"info/address-lines": "Masked Address"}
    assert export["devices"][ids[circuit]]["undeclared_properties"] == {
        _SERIAL: ids[circuit].upper(),
        _FED_BY: ids["bess"],
    }


@every_tree
def test_references_resolve(case: Case) -> None:
    export, ids = _exported(case.topics, case.root_id)
    missing: dict[str, str] = {}
    for device_id, topics in case.topics.items():
        exported = _valued(export["devices"][ids[device_id]])
        for topic, value in _published(topics).items():
            if _is_reference(topic) and value:
                _assert_resolves(topic, value, exported[topic], ids, missing)
    assert len(set(missing.values())) == len(missing)
    assert not set(missing.values()) & set(ids.values())


def test_every_kind_of_reference_is_exercised() -> None:
    """`test_references_resolve` checks what each tree publishes; across them, every kind is published."""
    named = [
        (topic, original, case.topics)
        for case in CASES
        for topics in case.topics.values()
        for topic, value in _published(topics).items()
        if _is_reference(topic)
        for original in _named(topic, value)
    ]
    kinds = {_LIST_SUFFIX if topic.endswith(_LIST_SUFFIX) else topic for topic, _, _ in named}
    assert {_FEEDS, _FED_BY, _LIST_SUFFIX} <= kinds
    assert any(original not in topics for _, original, topics in named), "no tree publishes a dangling reference"


def test_a_dangling_reference_is_masked_missing() -> None:
    topics = _copy(parent_child_tree())
    circuits = _declaring(topics, _FEEDS, _TYPE_CIRCUIT)[:2]
    for circuit in circuits:
        topics[circuit][_FEEDS] = "absent-device-1"
    export, ids = _exported(topics, _REFERENCE_ROOT)
    assert [_valued(export["devices"][ids[circuit]])[_FEEDS] for circuit in circuits] == ["masked-missing-1"] * 2


def test_dangling_references_are_numbered_in_placeholder_order() -> None:
    topics = _copy(parent_child_tree())
    ids = _placeholders(topics, _REFERENCE_ROOT)
    first, second, third = sorted(_declaring(topics, _FEEDS, _TYPE_CIRCUIT), key=lambda device: natural_key(ids[device]))[:3]
    topics[first][_FEEDS] = "absent-device-2"
    topics[second][_FEEDS] = "absent-device-1"
    topics[third][_FEEDS] = "absent-device-2"
    export, ids = _exported(topics, _REFERENCE_ROOT)
    assert [_valued(export["devices"][ids[circuit]])[_FEEDS] for circuit in (first, second, third)] == [
        "masked-missing-1",
        "masked-missing-2",
        "masked-missing-1",
    ]


def _a_reference(case: Case) -> tuple[str, str, str] | None:
    """The first published connection id naming a device of the tree other than the panel."""
    for device_id in sorted(case.topics, key=natural_key):
        for topic, value in sorted(_published(case.topics[device_id]).items()):
            if topic in (_FEEDS, _FED_BY) and value in case.topics and value != case.root_id:
                return device_id, topic, value
    return None


_REFERRING_CASES = [case for case in CASES if _a_reference(case) is not None]


@pytest.mark.parametrize("case", _REFERRING_CASES, ids=repr)
def test_a_reference_to_an_undescribed_device_resolves(case: Case) -> None:
    reference = _a_reference(case)
    assert reference is not None
    referrer, topic, target = reference
    adapter = replay(case.topics, case.root_id, withhold_description_of=target)
    ids = _ids_of(adapter, case.root_id)
    export = _export(adapter, case.root_id, tree_complete=False)
    assert ids[target].startswith("masked-undescribed-")
    assert ids[target] in export["undescribed_devices"]
    assert _valued(export["devices"][ids[referrer]])[topic] == ids[target]


_SHARING_CASES = [case for case in CASES if len(_declaring(case.topics, _SHARED_WITH)) >= 3]


@pytest.mark.parametrize("case", _SHARING_CASES, ids=repr)
def test_shared_with_lists_are_trimmed(case: Case) -> None:
    topics = _copy(case.topics)
    listed, emptied, gapped = _declaring(topics, _SHARED_WITH)[:3]
    a, b = [device_id for device_id in sorted(topics, key=natural_key) if device_id not in (listed, emptied, gapped)][:2]
    topics[listed][_SHARED_WITH] = f"{a}, {b}"
    topics[emptied][_SHARED_WITH] = ""
    topics[gapped][_SHARED_WITH] = f" {b},, {a} ,"
    export, ids = _exported(topics, case.root_id)
    assert _valued(export["devices"][ids[listed]])[_SHARED_WITH] == f"{ids[a]},{ids[b]}"
    assert _valued(export["devices"][ids[emptied]])[_SHARED_WITH] == ""
    assert _valued(export["devices"][ids[gapped]])[_SHARED_WITH] == f"{ids[b]},{ids[a]}"


_MID_CASES = [case for case in CASES if _declaring(case.topics, _GRID_FORMING, _TYPE_MID)]


@pytest.mark.parametrize("case", _MID_CASES, ids=repr)
def test_the_grid_forming_entity_is_the_grid_or_a_device(case: Case) -> None:
    mid = _declaring(case.topics, _GRID_FORMING, _TYPE_MID)[0]
    parent = str(_description_of(case.topics[mid])["parent"])
    for published, expected in (("GRID", "GRID"), (" Grid", " Grid"), (parent, None)):
        topics = _copy(case.topics)
        topics[mid][_GRID_FORMING] = published
        export, ids = _exported(topics, case.root_id)
        assert _valued(export["devices"][ids[mid]])[_GRID_FORMING] == (ids[parent] if expected is None else expected)


def test_targets_are_kept_and_masked_like_values() -> None:
    topics = _copy(parent_child_tree())
    circuit = _declaring(topics, "switch/relay", _TYPE_CIRCUIT)[0]
    mid = _declaring(topics, _GRID_FORMING, _TYPE_MID)[0]
    parent = str(_description_of(topics[mid])["parent"])
    topics[circuit]["switch/relay/$target"] = "OPEN"
    topics[mid][f"{_GRID_FORMING}/$target"] = parent
    export, ids = _exported(topics, _REFERENCE_ROOT)
    assert export["devices"][ids[circuit]]["targets"] == {"switch/relay": "OPEN"}
    assert export["devices"][ids[mid]]["targets"] == {_GRID_FORMING: ids[parent]}


@every_tree
def test_site_values_are_masked(case: Case) -> None:
    topics, _ = _seeded(case)
    export, ids = _exported(topics, case.root_id)
    entry = export["devices"]["masked-panel"]
    values = _valued(entry)
    assert {topic: values[topic] for topic in _SITE_MASKS} == _SITE_MASKS
    assert values[_NAME] == _MASKED_PANEL
    assert entry["description"]["name"] == _MASKED_PANEL
    assert entry["numeric_properties"]["info/latitude"] == "0.0"
    assert entry["numeric_properties"]["info/longitude"] == "0.0"
    for device_id in topics:
        assert _valued(export["devices"][ids[device_id]])[_SERIAL] == ids[device_id].upper(), device_id


@every_tree
def test_kept_values_are_published(case: Case) -> None:
    topics, labelled = _seeded(case)
    export, ids = _exported(topics, case.root_id)
    panel_values = _valued(export["devices"]["masked-panel"])
    assert {topic: panel_values[topic] for topic in _KEPT_SITE} == {
        topic: topics[case.root_id][topic] for topic in _KEPT_SITE
    }
    label = export["devices"][ids[labelled]]
    assert _valued(label)[_NAME] == _SEEDED_LABEL
    assert label["description"]["name"] == _description_of(topics[labelled])["name"]
    circuits = _declaring(topics, _NAME, _TYPE_CIRCUIT)
    assert circuits
    for circuit in circuits:
        entry = export["devices"][ids[circuit]]
        assert _valued(entry)[_NAME] == topics[circuit][_NAME]
        assert entry["description"]["name"] == _description_of(topics[circuit])["name"]


def _identity_free(snapshot: SpanPanelSnapshot) -> dict[str, object]:
    """The snapshot fields that carry no identity, with id-keyed collections as multisets of values."""
    return {
        "circuit count": len(snapshot.circuits),
        "circuits": Counter(
            (circuit.name, tuple(circuit.tabs), circuit.instant_power_w, circuit.relay_state, circuit.priority)
            for circuit in snapshot.circuits.values()
        ),
        "battery.present": snapshot.battery.present,
        "battery.soe_percentage": snapshot.battery.soe_percentage,
        "battery.power_w": snapshot.battery.power_w,
        "pv_inverters": len(snapshot.pv_inverters),
        "evse": len(snapshot.evse),
        "mid": snapshot.mid is not None,
        "lugs_at_service_entrance": snapshot.lugs_at_service_entrance,
        "instant_grid_power_w": snapshot.instant_grid_power_w,
        "power_flow_pv": snapshot.power_flow_pv,
        "power_flow_battery": snapshot.power_flow_battery,
        "power_flow_grid": snapshot.power_flow_grid,
        "power_flow_site": snapshot.power_flow_site,
        "firmware_version": snapshot.firmware_version,
        "panel_size": snapshot.panel_size,
    }


@every_tree
def test_it_replays(case: Case) -> None:
    export, _ = _exported(case.topics, case.root_id)
    captured = capture_tree_from_document(_object(json.loads(json.dumps(export))), "export")
    assert captured.root_id == "masked-panel"
    original = replay(case.topics, case.root_id)
    again = replay(captured.retained(), captured.root_id)
    assert again.is_ready()
    assert _identity_free(again.build_snapshot()) == _identity_free(original.build_snapshot())


@every_tree
def test_partial_tree_exports(case: Case) -> None:
    withheld = _declaring(case.topics, _SPACES, _TYPE_CIRCUIT)[0]
    adapter = replay(case.topics, case.root_id, withhold_description_of=withheld)
    complete = adapter._controller.is_tree_complete(case.root_id)
    assert complete is False
    export = _export(adapter, case.root_id, tree_complete=complete)
    ids = _ids_of(adapter, case.root_id)
    placeholder = ids[withheld]
    assert export["metadata"]["tree_complete"] is False
    assert placeholder not in export["devices"]
    assert set(export["undescribed_devices"]) == {placeholder}
    entry = export["undescribed_devices"][placeholder]
    published = _published(case.topics[withheld])
    assert entry["values"].keys() == published.keys()
    missing: dict[str, str] = {}
    for topic, value in published.items():
        _assert_masked(topic, value, entry["values"][topic], owner=withheld, root_id=case.root_id, ids=ids, missing=missing)
    assert entry["targets"] == {}
    assert "state" not in entry


def test_an_undescribed_device_is_masked_by_the_same_rules() -> None:
    topics = _copy(parent_child_tree())
    topics["pv"][_SERIAL] = "SEEDSN00001"
    topics["pv"]["status/postal-code"] = "98000"
    topics["pv"][_FEEDS] = "evse"
    topics["pv"][f"{_FED_BY}/$target"] = "absent-device-1"
    adapter = replay(topics, _REFERENCE_ROOT, withhold_description_of="pv")
    ids = _ids_of(adapter, _REFERENCE_ROOT)
    entry = _export(adapter, _REFERENCE_ROOT, tree_complete=False)["undescribed_devices"][ids["pv"]]
    assert {topic: entry["values"][topic] for topic in (_SERIAL, "status/postal-code", _FEEDS)} == {
        _SERIAL: ids["pv"].upper(),
        "status/postal-code": "00000",
        _FEEDS: ids["evse"],
    }
    assert entry["targets"] == {_FED_BY: "masked-missing-1"}


@pytest.mark.parametrize("payload", ["[1, 2]", '"a string"'], ids=["list", "string"])
def test_a_description_that_is_not_an_object_is_undescribed(payload: str) -> None:
    """Valid JSON that is not an object describes nothing: the device is undescribed, its values masked.

    Built from the devices directly rather than replayed: the eBus SDK (0.22.0)
    raises on such a description itself, in `children_ids`, so no adapter can be
    replayed into this state; the exporter is still total over it.
    """
    topics = _copy(parent_child_tree())
    topics["pv"]["$description"] = payload
    topics["pv"][_SERIAL] = "SEEDSN00001"
    devices = devices_from_tree(topics)
    root = next(device for device in devices if device.device_id == _REFERENCE_ROOT)
    children = [device for device in devices if device is not root]
    ids = placeholder_ids(root, children)
    export = export_masked_tree(root, children, producer=_PRODUCER, tree_complete=False)
    assert ids["pv"] == "masked-undescribed-1"
    assert len(set(ids.values())) == len(ids)
    assert ids["pv"] not in export["devices"]
    entry = export["undescribed_devices"][ids["pv"]]
    assert entry["values"][_SERIAL] == "MASKED-UNDESCRIBED-1"
    assert entry["values"].keys() == _published(topics["pv"]).keys()
    assert entry["state"] == "ready"
    children_named = export["devices"]["masked-panel"]["description"]["children"]
    assert isinstance(children_named, list)
    assert ids["pv"] in children_named


@pytest.mark.parametrize("node", ["site", "vendor"])
def test_site_and_serial_values_are_masked_on_any_node(node: str) -> None:
    """A site or serial property is masked by its id, whichever node declares or publishes it."""
    topics = _copy(parent_child_tree())
    circuit = _declaring(topics, _FEEDS, _TYPE_CIRCUIT)[0]
    description = _description_of(topics[_REFERENCE_ROOT])
    planted = {f"{node}/{property_id}": value for property_id, (value, _) in _SEEDED_BY_PROPERTY.items()}
    for topic, value in planted.items():
        _declare(description, topic, _SEEDED_BY_PROPERTY[_property_id(topic)][1])
        topics[_REFERENCE_ROOT][topic] = value
    topics[_REFERENCE_ROOT]["$description"] = json.dumps(description)
    kept = {f"{node}/name": "Seeded Site Label", f"{node}/country-code": "QZ", f"{node}/time-zone": "Etc/GMT+9"}
    topics[_REFERENCE_ROOT].update(kept)
    topics[circuit][f"{node}/serial-number"] = "SEEDSN00002"
    export, ids = _exported(topics, _REFERENCE_ROOT)
    panel_values = _valued(export["devices"]["masked-panel"])
    assert {topic: panel_values[topic] for topic in planted} == {
        f"{node}/{property_id}": _MASKS_BY_PROPERTY.get(property_id, "MASKED-PANEL") for property_id in _SEEDED_BY_PROPERTY
    }
    assert {topic: panel_values[topic] for topic in kept} == kept
    assert _valued(export["devices"][ids[circuit]])[f"{node}/serial-number"] == ids[circuit].upper()


def _typed(device_id: str, short_type: str) -> SyntheticDevice:
    return SyntheticDevice(
        device_id=device_id, device_type=f"energy.ebus.device.{short_type}", name=device_id, nodes={}, values={}
    )


def test_a_device_typed_missing_is_not_a_dangling_reference() -> None:
    topics = _copy(tree(panel(), _typed("m-1", "missing"), _typed("t-1", "type-missing"), _typed("g-1", "generator")))
    topics["g-1"][_FEEDS] = "absent-device-1"
    topics["g-1"][_FED_BY] = "m-1"
    export, ids = _exported(topics, "panel-a")
    dangling = _valued(export["devices"][ids["g-1"]])[_FEEDS]
    assert dangling == "masked-missing-1"
    assert dangling not in ids.values()
    assert _valued(export["devices"][ids["g-1"]])[_FED_BY] == ids["m-1"]
    assert not ids["m-1"].startswith("masked-missing-")
    assert ids["m-1"] == "masked-type-missing-1"
    assert len(set(ids.values())) == len(ids)


def test_a_device_typed_undescribed_is_not_an_undescribed_device() -> None:
    topics = tree(panel(), _typed("u-1", "undescribed"), _typed("g-1", "generator"))
    adapter = replay(topics, "panel-a", withhold_description_of="g-1")
    ids = _ids_of(adapter, "panel-a")
    export = _export(adapter, "panel-a", tree_complete=False)
    assert ids["g-1"] == "masked-undescribed-1"
    assert not ids["u-1"].startswith("masked-undescribed-")
    assert len(set(ids.values())) == len(ids)
    assert set(export["devices"]) == {"masked-panel", ids["u-1"]}
    assert set(export["undescribed_devices"]) == {ids["g-1"]}
