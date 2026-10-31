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
from reference_payloads.reidentify import reidentify, rename_devices
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


def _label(name: object, ids: Mapping[str, str]) -> object:
    """A description `name` as exported: one that is a device id is that device's placeholder, any other as published.

    The captures name many devices by their id, and the sweep finds the id.
    """
    return ids.get(name, name) if isinstance(name, str) else name


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
        elif "name" in expected:
            expected["name"] = _label(expected["name"], ids)
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
    assert label["description"]["name"] == _label(_description_of(topics[labelled])["name"], ids)
    circuits = _declaring(topics, _NAME, _TYPE_CIRCUIT)
    assert circuits
    for circuit in circuits:
        entry = export["devices"][ids[circuit]]
        assert _valued(entry)[_NAME] == topics[circuit][_NAME]
        assert entry["description"]["name"] == _label(_description_of(topics[circuit])["name"], ids)


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


# The sweep (§4.3). The captures are masked upstream, so every leak test plants
# its own identifiers first: `reidentify` gives each device id, serial and site
# value a new, seeded one, and the export must carry none of them.

_SEED = 337
_SITE_PROPERTIES = frozenset(_MASKS_BY_PROPERTY)


def _identifying(topics: RetainedTopicTree, root_id: str) -> set[str]:
    """Every valued serial and masked site value the tree publishes, and the panel's two names."""
    found = {
        value
        for device_topics in topics.values()
        for topic, value in _published(device_topics).items()
        if value and _property_id(topic) in {_SERIAL_ID, *_SITE_PROPERTIES}
    }
    panel_topics = topics[root_id]
    names = (panel_topics.get(_NAME, ""), str(_description_of(panel_topics).get("name", "")))
    return found | {name for name in names if name}


@every_tree
def test_reidentification_plants_every_identifier(case: Case) -> None:
    """A leak test is only as good as what it plants: every id, serial and site value is new, and planted."""
    reidentified = reidentify(case.topics, case.root_id, seed=_SEED)
    topics = reidentified.tree
    assert reidentified.root_id in topics
    assert set(topics).isdisjoint(case.topics)
    published = _identifying(topics, reidentified.root_id)
    assert published.isdisjoint(_identifying(case.topics, case.root_id))
    assert reidentified.planted == set(topics) | published
    assert len({value.casefold() for value in reidentified.planted}) == len(reidentified.planted)
    assert reidentify(case.topics, case.root_id, seed=_SEED) == reidentified
    original = replay(case.topics, case.root_id).build_snapshot()
    assert _identity_free(replay(topics, reidentified.root_id).build_snapshot()) == _identity_free(original)


@every_tree
def test_no_planted_identifier_survives(case: Case) -> None:
    reidentified = reidentify(case.topics, case.root_id, seed=_SEED)
    export, _ = _exported(reidentified.tree, reidentified.root_id)
    text = json.dumps(export).casefold()
    assert [value for value in sorted(reidentified.planted) if value.casefold() in text] == []


_TYPE_BESS = "energy.ebus.device.bess"
_TYPE_EVSE = "energy.ebus.device.evse"
_NOTE = "vendor/note"


def _first_of(topics: RetainedTopicTree, device_type: str) -> str:
    return next(device_id for device_id in sorted(topics, key=natural_key) if _type_of(topics[device_id]) == device_type)


def _reidentified_reference() -> tuple[dict[str, dict[str, str]], str]:
    """The reference tree re-identified, as a copy to plant in, and its new root id."""
    reidentified = reidentify(parent_child_tree(), _REFERENCE_ROOT, seed=_SEED)
    return _copy(reidentified.tree), reidentified.root_id


def _rename(topics: dict[str, dict[str, str]], device_id: str, name: str) -> None:
    """Set a device's `$description` `name`."""
    description = _description_of(topics[device_id])
    description["name"] = name
    topics[device_id]["$description"] = json.dumps(description)


def test_the_sweep_catches_what_no_rule_names() -> None:
    topics, root = _reidentified_reference()
    assert topics[root][_SERIAL] == root, "the panel's serial is its id"
    bess, evse = _first_of(topics, _TYPE_BESS), _first_of(topics, _TYPE_EVSE)
    circuit, named = _declaring(topics, _FEEDS, _TYPE_CIRCUIT)[:2]
    ghost, phantom = f"{root}-ghost", f"{root}-phantom"
    assert not {ghost, phantom} & set(topics)
    topics[root][_NOTE] = topics[bess][_SERIAL]
    topics[root]["vendor/peer"] = evse
    topics[root]["vendor/ghost"] = phantom
    topics[circuit][_FEEDS] = ghost
    _rename(topics, named, circuit)
    export, ids = _exported(topics, root)
    panel_values = export["devices"]["masked-panel"]["undeclared_properties"]
    assert panel_values[_NOTE] == ids[bess].upper()
    assert panel_values["vendor/peer"] == ids[evse]
    assert export["devices"][ids[named]]["description"]["name"] == ids[circuit]
    assert panel_values["vendor/ghost"] == "masked-panel-phantom"
    dangling = _valued(export["devices"][ids[circuit]])[_FEEDS]
    assert _MISSING.fullmatch(dangling)
    assert root.casefold() not in json.dumps(export).casefold()


def test_the_sweep_reaches_inside_json() -> None:
    """A `json` value is a wire string: the sweep reads it as text, so an identifier inside it is masked."""
    topics, root = _reidentified_reference()
    bess = _first_of(topics, _TYPE_BESS)
    serial = topics[bess][_SERIAL]
    description = _description_of(topics[root])
    _declare(description, "vendor/state", "json")
    topics[root]["$description"] = json.dumps(description)
    topics[root]["vendor/state"] = json.dumps({"owner": serial, "peers": [serial.upper(), "kept"]})
    export, ids = _exported(topics, root)
    exported = export["devices"]["masked-panel"]["properties"]["vendor/state"]
    assert json.loads(exported) == {"owner": ids[bess].upper(), "peers": [ids[bess].upper(), "kept"]}


def test_a_shared_serial_masks_consistently() -> None:
    """One serial on two devices: each device's own serial is its own mask; anywhere else, the first owner's."""
    topics, root = _reidentified_reference()
    bess, mid = _first_of(topics, _TYPE_BESS), _first_of(topics, _TYPE_MID)
    assert sorted((bess, mid)) == [mid, bess], "the seed must order the ids against their placeholders"
    shared = topics[bess][_SERIAL]
    topics[mid][_SERIAL] = shared
    for device_id in (root, mid, bess):
        topics[device_id][_NOTE] = shared
    labelled = _first_of(topics, "energy.ebus.device.pv")
    _rename(topics, labelled, f"gateway {shared}")
    export, ids = _exported(topics, root)
    first = min((bess, mid), key=lambda device_id: natural_key(ids[device_id]))
    assert first == bess
    assert _valued(export["devices"][ids[bess]])[_SERIAL] == ids[bess].upper()
    assert _valued(export["devices"][ids[mid]])[_SERIAL] == ids[mid].upper()
    for device_id in (root, mid, bess):
        assert _valued(export["devices"][ids[device_id]])[_NOTE] == ids[first].upper(), device_id
    assert export["devices"][ids[labelled]]["description"]["name"] == f"gateway {ids[first].upper()}"


def test_longest_identifier_wins() -> None:
    """An id that embeds the panel serial maps to its own placeholder, not the panel's with a suffix.

    Longest first, not priority first: the PV's placeholder sorts after the
    panel's, so the panel's id is the one tried first without the length order.
    """
    topics, root = _reidentified_reference()
    upstream, pv = f"{root}-lugs-up", _first_of(topics, "energy.ebus.device.pv")
    assert upstream in topics
    assert pv.startswith(f"{root}-")
    topics[root][_NOTE] = f"fed from {upstream}; {pv.upper()} reports"
    export, ids = _exported(topics, root)
    assert (ids[upstream], ids[pv]) == ("masked-lugs-upstream", "masked-pv-1")
    assert natural_key(ids[pv]) > natural_key("masked-panel")
    assert (
        export["devices"]["masked-panel"]["undeclared_properties"][_NOTE]
        == "fed from masked-lugs-upstream; masked-pv-1 reports"
    )


def test_the_sweep_leaves_unrelated_text() -> None:
    """Site values are not swept, and an id matches only where no letter or digit adjoins it."""
    case = next(case for case in CASES if {"circuit-1", "circuit-12"} <= set(case.topics))
    topics = _copy(case.topics)
    statuses = [
        (device_id, topic)
        for device_id, device_topics in topics.items()
        for topic, value in _published(device_topics).items()
        if value == "OK"
    ]
    assert any(topic == "connection/feeds-device-status" for _, topic in statuses)
    unrelated = "spare circuit-120, subcircuit-12, circuit-1a"
    assert not {"circuit-120", "circuit-1a"} & set(topics)
    topics[case.root_id]["info/region"] = "OK"
    topics[case.root_id][_NOTE] = "circuit-12"
    topics[case.root_id]["vendor/spare"] = unrelated
    export, ids = _exported(topics, case.root_id)
    for device_id, topic in statuses:
        assert _valued(export["devices"][ids[device_id]])[topic] == "OK", (device_id, topic)
    panel_values = _valued(export["devices"]["masked-panel"])
    assert panel_values["info/region"] == "Masked Region"
    assert panel_values[_NOTE] == ids["circuit-12"]
    assert ids["circuit-12"] != ids["circuit-1"]
    assert panel_values["vendor/spare"] == unrelated


def test_a_short_identifier_is_matched_only_whole() -> None:
    """`bess` and `pv` are shorter than six characters: a whole value is swept, a word inside one or a key is not."""
    topics = _copy(parent_child_tree())
    assert {"bess", "pv"} <= set(topics)
    topics[_REFERENCE_ROOT]["vendor/peer"] = "bess"
    topics[_REFERENCE_ROOT]["vendor/peer-shouted"] = "BESS"
    _rename(topics, "bess", "Example BESS")
    export, ids = _exported(topics, _REFERENCE_ROOT)
    panel = export["devices"]["masked-panel"]
    assert panel["undeclared_properties"]["vendor/peer"] == ids["bess"]
    assert panel["undeclared_properties"]["vendor/peer-shouted"] == ids["bess"]
    assert export["devices"][ids["bess"]]["description"]["name"] == "Example BESS"
    declaring_pv = [ids[device_id] for device_id in _declaring(topics, "power-flows/pv")]
    assert declaring_pv
    for placeholder in declaring_pv:
        flows = _object(_object(export["devices"][placeholder]["description"]["nodes"])["power-flows"])
        assert "pv" in _object(flows["properties"])
        assert "power-flows/pv" in _valued(export["devices"][placeholder])


@every_tree
def test_the_sweep_leaves_exporter_output_alone(case: Case) -> None:
    """Placeholders and masks are the exporter's own text, never swept.

    The captures' ids (`circuit-3`, `lugs-upstream`) lie inside their own
    placeholders at a `-` boundary, so a sweep over the finished document would
    rewrite `masked-circuit-3` into `masked-masked-circuit-<n>`.
    """
    topics, _ = _seeded(case)
    circuit = _declaring(topics, _SPACES, _TYPE_CIRCUIT)[0]
    topics[circuit][_FEEDS] = "absent-device-1"
    export, ids = _exported(topics, case.root_id)
    assert set(export["devices"]) == set(ids.values())
    for device_id, device_topics in topics.items():
        entry = export["devices"][ids[device_id]]
        published = _description_of(device_topics)
        for key in ("parent", "root"):
            if key in published:
                assert entry["description"][key] == ids[str(published[key])], (device_id, key)
        children = published.get("children")
        if isinstance(children, list):
            assert entry["description"]["children"] == [ids[str(child)] for child in children], device_id
        assert _valued(entry)[_SERIAL] == ids[device_id].upper(), device_id
    panel = export["devices"]["masked-panel"]
    assert {topic: _valued(panel)[topic] for topic in _SITE_MASKS} == _SITE_MASKS
    assert (_valued(panel)[_NAME], panel["description"]["name"]) == (_MASKED_PANEL, _MASKED_PANEL)
    assert _valued(export["devices"][ids[circuit]])[_FEEDS] == "masked-missing-1"


def test_an_id_spelt_like_exporter_output_does_not_rewrite_it() -> None:
    """Device ids that spell another device's placeholder, a mask and a dangling placeholder.

    The sweep replaces each where the panel published it, and leaves alone the
    same text where the exporter wrote it.
    """
    spelt = ["masked-generator-1", "masked-missing-1", "masked-ssid"]
    topics = _copy(_generators(["g-1", *spelt]))
    topics["panel-a"]["status/wifi-ssid"] = "seeded-network"
    topics["panel-a"][_NOTE] = "masked-generator-1"
    topics["g-1"][_FEEDS] = "absent-device-1"
    export, ids = _exported(topics, "panel-a")
    assert [ids[device_id] for device_id in ["g-1", *spelt]] == [f"masked-generator-{n}" for n in range(1, 5)]
    assert set(export["devices"]) == {"masked-panel", *(f"masked-generator-{n}" for n in range(1, 5))}
    panel_values = _valued(export["devices"]["masked-panel"])
    assert panel_values["status/wifi-ssid"] == "masked-ssid"
    assert panel_values[_NOTE] == ids["masked-generator-1"]
    assert _valued(export["devices"][ids["g-1"]])[_FEEDS] == "masked-missing-1"
    assert export["devices"]["masked-panel"]["description"]["children"] == [ids[device_id] for device_id in ["g-1", *spelt]]


def test_a_serial_published_as_a_target_is_swept_too() -> None:
    """A serial a device publishes only as a `$target`, beside a different value, is still one of its identifiers."""
    topics = _copy(parent_child_tree())
    target_serial = "TGTARGET00001"
    assert topics["bess"][_SERIAL] != target_serial
    topics["bess"][f"{_SERIAL}/$target"] = target_serial
    topics[_REFERENCE_ROOT][_NOTE] = target_serial
    export, ids = _exported(topics, _REFERENCE_ROOT)
    assert export["devices"][ids["bess"]]["targets"][_SERIAL] == ids["bess"].upper()
    assert export["devices"]["masked-panel"]["undeclared_properties"][_NOTE] == ids["bess"].upper()


def test_a_key_naming_a_device_is_swept_alike_where_declared_and_valued() -> None:
    """A property id that embeds a serial is swept in the description and the value's key, so the value stays declared."""
    topics, root = _reidentified_reference()
    bess = _first_of(topics, _TYPE_BESS)
    topic = f"vendor/battery-{topics[bess][_SERIAL]}"
    description = _description_of(topics[root])
    _declare(description, topic, "string")
    topics[root]["$description"] = json.dumps(description)
    topics[root][topic] = "seeded-reading"
    export, ids = _exported(topics, root)
    swept = f"vendor/battery-{ids[bess].upper()}"
    panel = export["devices"]["masked-panel"]
    assert panel["properties"][swept] == "seeded-reading"
    assert swept not in panel["undeclared_properties"]
    assert swept in _declared(panel["description"])


def test_a_state_is_swept() -> None:
    topics, root = _reidentified_reference()
    pv, evse = _first_of(topics, "energy.ebus.device.pv"), _first_of(topics, _TYPE_EVSE)
    topics[pv]["$state"] = evse
    export, ids = _exported(topics, root)
    assert export["devices"][ids[pv]].get("state") == ids[evse]


def test_a_tree_of_short_identifiers_exports() -> None:
    """With no identifier long enough to match inside text, the sweep matches whole values only, and nothing else."""
    short = {"panel-a": "p", "g-1": "g1", "g-2": "g2"}
    topics, root = rename_devices(_generators(["g-1", "g-2"]), "panel-a", short.__getitem__)
    planted = _copy(topics)
    planted[root][_SERIAL] = root
    planted[root][_NOTE] = "G1"
    planted[root]["vendor/text"] = "g1 -- g2 stay"
    export, ids = _exported(planted, root)
    panel_values = export["devices"]["masked-panel"]["undeclared_properties"]
    assert panel_values == {_NOTE: ids["g1"], "vendor/text": "g1 -- g2 stay"}


_METER_SERIAL = "info/utility-meter-serial-number"
_SEEDED_METER_SERIAL = _SEEDED_SITE[_METER_SERIAL][0]


def test_the_meter_serial_is_swept_as_an_identifier() -> None:
    """A utility meter serial is a serial number, not free text: found anywhere else, it is `MASKED-METER` (§4.3)."""
    assert any(_SEEDED_METER_SERIAL in reidentify(case.topics, case.root_id, seed=_SEED).planted for case in CASES)
    topics, root = _reidentified_reference()
    description = _description_of(topics[root])
    _declare(description, _METER_SERIAL, "string")
    _declare(description, "vendor/meter", "json")
    topics[root]["$description"] = json.dumps(description)
    topics[root][_METER_SERIAL] = _SEEDED_METER_SERIAL
    topics[root][_NOTE] = f"meter {_SEEDED_METER_SERIAL}"
    topics[root]["vendor/meter"] = json.dumps({"meter": _SEEDED_METER_SERIAL.lower()})
    export, _ = _exported(topics, root)
    panel = export["devices"]["masked-panel"]
    assert panel["properties"][_METER_SERIAL] == "MASKED-METER"
    assert panel["undeclared_properties"][_NOTE] == "meter MASKED-METER"
    assert json.loads(panel["properties"]["vendor/meter"]) == {"meter": "MASKED-METER"}
    assert _SEEDED_METER_SERIAL.casefold() not in json.dumps(export).casefold()


def test_an_identifier_after_a_json_escape_is_swept() -> None:
    """An identifier after an escape in a `json` value is masked: the value is swept literal by literal, decoded.

    Only a literal that changed is written again (`ensure_ascii=False`, so its
    `\u00b0` becomes `°`); the keys around it stay byte for byte.
    """
    topics, root = _reidentified_reference()
    bess, evse = _first_of(topics, _TYPE_BESS), _first_of(topics, _TYPE_EVSE)
    serial = topics[bess][_SERIAL].upper()
    lines = {
        escape: f"{before}{evse}|{before}{serial}"
        for escape, before in (("newline", "one\n"), ("tab", "a\t"), ("quote", 'say "'), ("unicode", "°"))
    }
    published = json.dumps(lines)
    assert all(escape in published for escape in ("\\n", "\\t", '\\"', "\\u00b0"))
    description = _description_of(topics[root])
    _declare(description, "vendor/log", "json")
    topics[root]["$description"] = json.dumps(description)
    topics[root]["vendor/log"] = published
    export, ids = _exported(topics, root)
    exported = export["devices"]["masked-panel"]["properties"]["vendor/log"]
    swept = {escape: line.replace(evse, ids[evse]).replace(serial, ids[bess].upper()) for escape, line in lines.items()}
    literals = (f"{json.dumps(escape)}: {json.dumps(line, ensure_ascii=False)}" for escape, line in swept.items())
    assert exported == "{" + ", ".join(literals) + "}"
    assert json.loads(exported) == swept
    assert evse not in exported
    assert serial not in exported


def test_a_dangling_id_reads_the_same_everywhere() -> None:
    """An id the panel publishes for a device it does not hold is one `masked-missing-<n>`, at a reference and in free text.

    Built from the devices directly: replayed, a child the panel declares
    becomes an undescribed device rather than a dangling one.
    """
    topics, root = _reidentified_reference()
    referring, named = _declaring(topics, _FEEDS, _TYPE_CIRCUIT)[:2]
    ghost, absent_child = f"{root}-ghost", f"{root}-absent-child"
    assert not {ghost, absent_child} & set(topics)
    assert all(len(dangling) >= 6 and re.search(r"[0-9]", dangling) for dangling in (ghost, absent_child))
    topics[referring][_FEEDS] = ghost
    panel_description = _description_of(topics[root])
    children = panel_description["children"]
    assert isinstance(children, list)
    panel_description["children"] = [*children, absent_child]
    topics[root]["$description"] = json.dumps(panel_description)
    topics[root][_NOTE] = f"waiting for {ghost} and {absent_child}"
    _rename(topics, named, ghost)
    devices = devices_from_tree(topics)
    panel_device = next(device for device in devices if device.device_id == root)
    others = [device for device in devices if device is not panel_device]
    ids = placeholder_ids(panel_device, others)
    export = export_masked_tree(panel_device, others, producer=_PRODUCER, tree_complete=True)
    missing = _valued(export["devices"][ids[referring]])[_FEEDS]
    panel_children = export["devices"]["masked-panel"]["description"]["children"]
    assert isinstance(panel_children, list)
    missing_child = panel_children[-1]
    assert isinstance(missing_child, str)
    assert _MISSING.fullmatch(missing) and _MISSING.fullmatch(missing_child) and missing != missing_child
    assert export["devices"]["masked-panel"]["undeclared_properties"][_NOTE] == f"waiting for {missing} and {missing_child}"
    assert export["devices"][ids[named]]["description"]["name"] == missing
    assert ghost not in json.dumps(export)
    assert absent_child not in json.dumps(export)


def test_an_undescribed_device_is_swept_in_free_text() -> None:
    """A device that has not described itself still has an id and a serial, and both are found wherever they appear."""
    partial = _copy(parent_child_tree())
    del partial["evse"]["$description"]
    reidentified = reidentify(partial, _REFERENCE_ROOT, seed=_SEED)
    topics, root = _copy(reidentified.tree), reidentified.root_id
    undescribed = next(device_id for device_id, device_topics in topics.items() if "$description" not in device_topics)
    serial = topics[undescribed][_SERIAL]
    assert serial in reidentified.planted
    topics[root][_NOTE] = f"{undescribed} reports {serial}"
    adapter = replay(topics, root, withhold_description_of=undescribed)
    ids = _ids_of(adapter, root)
    export = _export(adapter, root, tree_complete=False)
    assert ids[undescribed] == "masked-undescribed-1"
    assert ids[undescribed] in export["undescribed_devices"]
    expected = f"{ids[undescribed]} reports {ids[undescribed].upper()}"
    assert export["devices"]["masked-panel"]["undeclared_properties"][_NOTE] == expected
    assert [value for value in (undescribed, serial) if value.casefold() in json.dumps(export).casefold()] == []


@pytest.mark.parametrize("word", ["UNKNOWN", "NONE", "BACKUP"])
def test_a_word_at_a_reference_site_is_not_swept_elsewhere(word: str) -> None:
    """A dangling reference that is a word rather than an id reads `masked-missing-<n>` there, and nowhere else.

    Only a dangling value of six characters or more with a digit in it is swept
    beyond its reference: `UNKNOWN` is also a reading and an enum member, and
    `NONE` every relay requester's reading.
    """
    topics = _copy(parent_child_tree())
    mid = _declaring(topics, _GRID_FORMING, _TYPE_MID)[0]
    topics[_REFERENCE_ROOT]["vendor/status"] = word
    as_grid = _copy(topics)
    as_grid[mid][_GRID_FORMING] = "GRID"
    topics[mid][_GRID_FORMING] = word
    export, ids = _exported(topics, _REFERENCE_ROOT)
    baseline, _ = _exported(as_grid, _REFERENCE_ROOT)
    assert _MISSING.fullmatch(_valued(export["devices"][ids[mid]])[_GRID_FORMING])
    export["devices"][ids[mid]]["properties"].pop(_GRID_FORMING)
    baseline["devices"][ids[mid]]["properties"].pop(_GRID_FORMING)
    assert word in json.dumps(export)
    assert export == baseline


def test_an_identifier_never_starts_inside_a_json_escape() -> None:
    """A match may start after a complete escape, never on an escape's own letter, which would split the escape."""
    topics, root = _reidentified_reference()
    assert root.startswith("n")
    cases = {
        "split": "\n" + root[1:],
        "escaped-backslash-then-split": "\\\n" + root[1:],
        "after-escaped-backslash": "\\" + root,
        "after-newline": "\n" + root,
    }
    description = _description_of(topics[root])
    for name, text in cases.items():
        _declare(description, f"vendor/{name}", "json")
        topics[root][f"vendor/{name}"] = json.dumps({"n": text})
    topics[root]["$description"] = json.dumps(description)
    export, _ = _exported(topics, root)
    exported = export["devices"]["masked-panel"]["properties"]
    for name in ("split", "escaped-backslash-then-split"):
        assert exported[f"vendor/{name}"] == topics[root][f"vendor/{name}"], name
    for name, text in cases.items():
        assert json.loads(exported[f"vendor/{name}"]) == {"n": text.replace(root, "masked-panel")}, name


def test_an_identifier_after_a_literal_backslash_is_swept() -> None:
    """A backslash in plain text, or in a decoded description string, is a boundary like any other character."""
    topics, root = _reidentified_reference()
    bess = _first_of(topics, _TYPE_BESS)
    circuit = _declaring(topics, _FEEDS, _TYPE_CIRCUIT)[0]
    serial = topics[bess][_SERIAL].upper()
    topics[root]["vendor/path"] = f"C:\\{serial}"
    topics[root]["vendor/log"] = f"logs\\{serial}\\x"
    topics[root]["vendor/circuit"] = f"\\{circuit}"
    description = _description_of(topics[root])
    description["vendor-path"] = f"C:\\{serial}"
    topics[root]["$description"] = json.dumps(description)
    export, ids = _exported(topics, root)
    panel = export["devices"]["masked-panel"]
    mask = ids[bess].upper()
    assert {topic: panel["undeclared_properties"][topic] for topic in ("vendor/path", "vendor/log", "vendor/circuit")} == {
        "vendor/path": f"C:\\{mask}",
        "vendor/log": f"logs\\{mask}\\x",
        "vendor/circuit": f"\\{ids[circuit]}",
    }
    assert panel["description"]["vendor-path"] == f"C:\\{mask}"


def test_an_identifier_spelt_with_an_escape_is_swept() -> None:
    """An identifier written with a `\\u` escape in a `json` value is found once the literal is decoded."""
    topics, root = _reidentified_reference()
    assert root.startswith("n")
    published = '{"n": "\\u006e' + root[1:] + '", "kept": "\\u00b0"}'
    assert json.loads(published) == {"n": root, "kept": "°"}
    topics[root]["vendor/raw"] = published
    export, _ = _exported(topics, root)
    exported = export["devices"]["masked-panel"]["undeclared_properties"]["vendor/raw"]
    assert exported == '{"n": "masked-panel", "kept": "\\u00b0"}'


def test_a_value_that_is_not_a_json_object_or_array_is_plain_text() -> None:
    """Only a value that parses as a JSON object or array is swept literal by literal; anything else is plain text.

    On the reference tree `bess` is a short id: a whole literal in a JSON array
    or object value is swept, while in text that only looks like JSON, in a JSON
    string scalar, or as an object key it is not a whole value and is kept.
    """
    topics = _copy(parent_child_tree())
    serial = topics["bess"][_SERIAL]
    planted = {
        "vendor/object": '{"peer": "bess", "bess": 1}',
        "vendor/array": '["BESS", "kept"]',
        "vendor/broken": '{"peer": "bess", "serial": "' + serial + '",}',
        "vendor/scalar": '"bess"',
    }
    topics[_REFERENCE_ROOT].update(planted)
    export, ids = _exported(topics, _REFERENCE_ROOT)
    values = export["devices"]["masked-panel"]["undeclared_properties"]
    assert values == {
        "vendor/object": '{"peer": "' + ids["bess"] + '", "bess": 1}',
        "vendor/array": '["' + ids["bess"] + '", "kept"]',
        "vendor/broken": '{"peer": "bess", "serial": "' + ids["bess"].upper() + '",}',
        "vendor/scalar": '"bess"',
    }


def _json_containers(values: Mapping[str, str]) -> dict[str, str]:
    """The values that parse as a JSON object or array."""
    found: dict[str, str] = {}
    for topic, value in values.items():
        try:
            parsed: object = json.loads(value)
        except ValueError:
            continue
        if isinstance(parsed, (dict, list)):
            found[topic] = value
    return found


@every_tree
def test_every_json_value_still_parses(case: Case) -> None:
    """Every published JSON object or array, captured or planted with escapes and identifiers, still parses exported."""
    reidentified = reidentify(case.topics, case.root_id, seed=_SEED)
    topics, root = _copy(reidentified.tree), reidentified.root_id
    other = next(device_id for device_id in sorted(topics) if device_id != root)
    topics[root]["vendor/escaped"] = json.dumps(
        {"split": "\n" + root[1:], "after": f"\n{root}", "backslash": f"C:\\{other}", other: ["\u00b0" + root, "\\"]}
    )
    export, ids = _exported(topics, root)
    published = {ids[device_id]: _json_containers(_published(device_topics)) for device_id, device_topics in topics.items()}
    assert published["masked-panel"]
    for placeholder, containers in published.items():
        values = _valued(export["devices"][placeholder])
        for topic in containers:
            exported: object = json.loads(values[topic])
            assert isinstance(exported, (dict, list)), (placeholder, topic)
    assert json.loads(_valued(export["devices"]["masked-panel"])["vendor/escaped"]) == {
        "split": "\n" + root[1:],
        "after": "\nmasked-panel",
        "backslash": f"C:\\{ids[other]}",
        ids[other]: ["\u00b0masked-panel", "\\"],
    }
