"""The masked export's device placeholders.

Each property runs over every vendored capture, the two main32 fixtures and the
reference tree, and selects what to check by what each tree declares.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re

from ebus_sdk.homie import DiscoveredDevice
import pytest

from span_panel_api_schema_1 import SchemaOneAdapter
from span_panel_api_schema_1.tree_export import natural_key, placeholder_ids

from reference_payloads.captures import CAPTURES, capture_tree, capture_tree_from_document
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
_GENERATOR_IDS = [f"g-{n}" for n in range(2, 12)]


@dataclass(frozen=True)
class Case:
    name: str
    topics: RetainedTopicTree
    root_id: str

    def __repr__(self) -> str:
        return self.name


def _main32_case(file_name: str) -> Case:
    document: object = json.loads((_FIXTURES / file_name).read_text(encoding="utf-8"))
    assert isinstance(document, dict)
    captured = capture_tree_from_document(document, file_name)
    return Case(file_name, captured.retained(), captured.root_id)


def _cases() -> list[Case]:
    captures = [Case(stem, capture_tree(stem).retained(), capture_tree(stem).root_id) for stem in CAPTURES]
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
