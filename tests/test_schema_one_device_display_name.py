"""A device's display name is never its own id.

Real r202639 panels publish each non-root device's id as its `$description.name`
(the public MAIN 32 capture shows it for the battery, MID, PV, lugs and circuits).
Surfacing that id as a name is the outcome the attribute's docstring calls worse
than no name.
"""

from __future__ import annotations

import json

from ebus_sdk.homie import DiscoveredDevice

from reference_payloads.schema_one import device_from_topics, parent_child_tree
from span_panel_api.models import SpanPanelSnapshot
from span_panel_api_schema_1.const import TYPE_BESS, TYPE_MID
from span_panel_api_schema_1.panel import device_display_name
from span_panel_api_schema_1.snapshot import build_snapshot

_TREE = parent_child_tree()


def _device_id(device_type: str) -> str:
    return next(
        device_id for device_id, topics in _TREE.items() if json.loads(topics["$description"])["type"] == device_type
    )


BESS_ID = _device_id(TYPE_BESS)
MID_ID = _device_id(TYPE_MID)
PANEL_ID = next(device_id for device_id, topics in _TREE.items() if not json.loads(topics["$description"]).get("parent"))


def _bess_topics(*, name: str, vendor: str | None, model: str | None) -> dict[str, str]:
    """The reference BESS's retained topics, named and identified as given; `None` leaves a property unvalued."""
    description = json.loads(_TREE[BESS_ID]["$description"])
    description["name"] = name
    topics = {topic: value for topic, value in _TREE[BESS_ID].items() if topic not in ("info/vendor-name", "info/model")}
    topics["$description"] = json.dumps(description)
    if vendor is not None:
        topics["info/vendor-name"] = vendor
    if model is not None:
        topics["info/model"] = model
    return topics


def _device(*, name: str, vendor: str | None, model: str | None) -> DiscoveredDevice:
    return device_from_topics(BESS_ID, _bess_topics(name=name, vendor=vendor, model=model))


def _reference_snapshot(*, bess_name_is_its_id: bool, grid_forming: str) -> SpanPanelSnapshot:
    """The reference tree with the MID naming `grid_forming`, and the BESS identified as the public MAIN 32 capture shows it."""
    tree = {device_id: dict(topics) for device_id, topics in _TREE.items()}
    tree[BESS_ID] = _bess_topics(name=BESS_ID if bess_name_is_its_id else "Battery", vendor="Tesla", model="Powerwall 2 AC")
    tree[MID_ID]["grid/grid-forming-entity"] = grid_forming
    panel = device_from_topics(PANEL_ID, tree[PANEL_ID])
    children = [device_from_topics(device_id, topics) for device_id, topics in tree.items() if device_id != PANEL_ID]
    return build_snapshot(panel, children)


def test_a_name_equal_to_the_id_falls_back_to_vendor_and_model() -> None:
    device = _device(name=BESS_ID, vendor="Tesla", model="Powerwall 2 AC")
    assert device_display_name(device) == "Tesla Powerwall 2 AC"


def test_model_alone_names_the_device() -> None:
    assert device_display_name(_device(name=BESS_ID, vendor=None, model="Powerwall 2 AC")) == "Powerwall 2 AC"


def test_a_vendor_alone_does_not_name_a_device() -> None:
    assert device_display_name(_device(name=BESS_ID, vendor="Tesla", model=None)) is None


def test_an_empty_value_is_no_value() -> None:
    """An empty name, vendor or model names nothing, exactly as an unpublished one does."""
    assert device_display_name(_device(name="", vendor="", model="Powerwall 2 AC")) == "Powerwall 2 AC"
    assert device_display_name(_device(name=BESS_ID, vendor="Tesla", model="")) is None


def test_a_real_name_is_kept() -> None:
    assert device_display_name(_device(name="Battery", vendor="Tesla", model="Powerwall 2 AC")) == "Battery"


def test_the_grid_forming_attribute_names_the_battery_not_its_id() -> None:
    snapshot = _reference_snapshot(bess_name_is_its_id=True, grid_forming=BESS_ID)
    assert snapshot.mid is not None
    assert snapshot.mid.grid_forming_device_name == "Tesla Powerwall 2 AC"
    grid = _reference_snapshot(bess_name_is_its_id=True, grid_forming="GRID")
    assert grid.mid is not None
    assert grid.mid.grid_forming_device_name is None
