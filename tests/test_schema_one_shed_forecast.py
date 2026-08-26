"""The enclosure's backup-planning forecast, from the wire to the snapshot.

`shed-forecast` 0.1 publishes four `integer` minute estimates and a confidence
enum. Nothing derives them and nothing defaults them: every assertion here is
against a value the captured tree actually publishes, and every one of them has
a paired test that republishes something different, so a reading that the parser
hardcoded rather than read cannot pass.
"""

from __future__ import annotations

from collections.abc import Mapping
import json
from typing import Any

import pytest

from ebus_sdk.homie import DiscoveredDevice

from reference_payloads.schema_one import (
    RetainedTopicTree,
    device_from_topics,
    parent_child_tree,
)
from span_panel_api.models import FieldMetadata, SpanPanelSnapshot
from span_panel_api_schema_1.field_metadata import build_field_metadata
from span_panel_api_schema_1.snapshot import build_snapshot

PANEL = "example-40t-001"
NODE = "shed-forecast"

TIME_TO_PRIORITY_SHED = "time-to-priority-shed"
TOTAL_TIME_REMAINING = "total-time-remaining"
FULL_CHARGE_TIME_TO_PRIORITY_SHED = "full-charge-time-to-priority-shed"
FULL_CHARGE_TOTAL_TIME_REMAINING = "full-charge-total-time-remaining"
CONFIDENCE = "confidence"

_LIVE_PATHS = {
    TIME_TO_PRIORITY_SHED: "panel.shed_time_to_priority_shed_min",
    TOTAL_TIME_REMAINING: "panel.shed_total_time_remaining_min",
}


def _mutable_tree() -> dict[str, dict[str, str]]:
    """A deep-enough copy of the capture that a test can rewrite one topic.

    Rewriting the published value is the whole point of this module: an
    assertion against a constant proves nothing unless the same code reports a
    different constant when the panel sends one.
    """
    return {device_id: dict(topics) for device_id, topics in parent_child_tree().items()}


def _snapshot(tree: RetainedTopicTree) -> SpanPanelSnapshot:
    panel = device_from_topics(PANEL, tree[PANEL])
    children = [device_from_topics(device_id, topics) for device_id, topics in tree.items() if device_id != PANEL]
    return build_snapshot(panel, children)


def _devices(tree: RetainedTopicTree) -> list[DiscoveredDevice]:
    return [device_from_topics(device_id, topics) for device_id, topics in tree.items()]


def _published(property_id: str) -> str:
    return parent_child_tree()[PANEL][f"{NODE}/{property_id}"]


def _without_property(tree: dict[str, dict[str, str]], property_id: str) -> dict[str, dict[str, str]]:
    """Stop publishing one forecast property, and stop declaring it too.

    Both halves, because they are different situations to the metadata builder —
    an undeclared property is a gap and an unpublished one is a missing value —
    and this helper models a firmware that simply does not have the property.
    """
    del tree[PANEL][f"{NODE}/{property_id}"]
    description = json.loads(tree[PANEL]["$description"])
    del description["nodes"][NODE]["properties"][property_id]
    tree[PANEL]["$description"] = json.dumps(description)
    return tree


def _without_node(tree: dict[str, dict[str, str]]) -> dict[str, dict[str, str]]:
    """A panel that publishes no `shed-forecast` node at all."""
    for topic in [topic for topic in tree[PANEL] if topic.startswith(f"{NODE}/")]:
        del tree[PANEL][topic]
    description = json.loads(tree[PANEL]["$description"])
    del description["nodes"][NODE]
    tree[PANEL]["$description"] = json.dumps(description)
    return tree


def _declared(tree: RetainedTopicTree) -> Mapping[str, Any]:
    description: dict[str, Any] = json.loads(tree[PANEL]["$description"])
    nodes: dict[str, Any] = description["nodes"]
    return nodes


# ---------------------------------------------------------------------------
# The capture publishes it; the snapshot reports what was published
# ---------------------------------------------------------------------------


def test_the_capture_publishes_the_whole_capability() -> None:
    """Guard the premise. Every assertion below reads the tree for its expected
    value, so a capture that stopped publishing the node would make them all
    vacuously true rather than failing."""
    tree = parent_child_tree()

    assert NODE in _declared(tree)
    for property_id in (*_LIVE_PATHS, FULL_CHARGE_TIME_TO_PRIORITY_SHED, FULL_CHARGE_TOTAL_TIME_REMAINING, CONFIDENCE):
        assert f"{NODE}/{property_id}" in tree[PANEL]


def test_every_forecast_property_reaches_the_snapshot() -> None:
    """Read against the tree rather than against literals: the expected value is
    whatever the panel published, so changing the capture changes the
    expectation instead of silently disagreeing with it."""
    snapshot = _snapshot(parent_child_tree())

    assert snapshot.shed_time_to_priority_shed_min == int(_published(TIME_TO_PRIORITY_SHED))
    assert snapshot.shed_total_time_remaining_min == int(_published(TOTAL_TIME_REMAINING))
    assert snapshot.shed_full_charge_time_to_priority_shed_min == int(_published(FULL_CHARGE_TIME_TO_PRIORITY_SHED))
    assert snapshot.shed_full_charge_total_time_remaining_min == int(_published(FULL_CHARGE_TOTAL_TIME_REMAINING))
    assert snapshot.shed_forecast_confidence == _published(CONFIDENCE)


def test_the_two_live_estimates_are_not_the_same_reading() -> None:
    """`time-to-priority-shed` and `total-time-remaining` are distinct in the
    capture, so a parser that crossed the two would fail here rather than
    reporting a plausible pair."""
    snapshot = _snapshot(parent_child_tree())

    assert snapshot.shed_time_to_priority_shed_min != snapshot.shed_total_time_remaining_min


@pytest.mark.parametrize(
    ("property_id", "attribute", "republished", "expected"),
    [
        (TIME_TO_PRIORITY_SHED, "shed_time_to_priority_shed_min", "17", 17),
        (TOTAL_TIME_REMAINING, "shed_total_time_remaining_min", "1440", 1440),
        (
            FULL_CHARGE_TIME_TO_PRIORITY_SHED,
            "shed_full_charge_time_to_priority_shed_min",
            "615",
            615,
        ),
        (
            FULL_CHARGE_TOTAL_TIME_REMAINING,
            "shed_full_charge_total_time_remaining_min",
            "720",
            720,
        ),
        (CONFIDENCE, "shed_forecast_confidence", "LOW", "LOW"),
    ],
)
def test_republishing_a_property_moves_the_field_that_reads_it(
    property_id: str, attribute: str, republished: str, expected: int | str
) -> None:
    """The mutation half. Each value differs from the captured one *and* from
    every other captured one, so a field wired to the wrong property reports a
    number the assertion rejects."""
    tree = _mutable_tree()
    tree[PANEL][f"{NODE}/{property_id}"] = republished

    assert getattr(_snapshot(tree), attribute) == expected


@pytest.mark.parametrize(
    ("property_id", "attribute"),
    [
        (TIME_TO_PRIORITY_SHED, "shed_time_to_priority_shed_min"),
        (TOTAL_TIME_REMAINING, "shed_total_time_remaining_min"),
        (FULL_CHARGE_TIME_TO_PRIORITY_SHED, "shed_full_charge_time_to_priority_shed_min"),
        (FULL_CHARGE_TOTAL_TIME_REMAINING, "shed_full_charge_total_time_remaining_min"),
        (CONFIDENCE, "shed_forecast_confidence"),
    ],
)
def test_a_property_the_panel_does_not_publish_is_none(property_id: str, attribute: str) -> None:
    """`None`, never zero. Zero minutes is a legitimate forecast — shedding
    starts now — so a default would be indistinguishable from the worst reading
    the capability can report."""
    snapshot = _snapshot(_without_property(_mutable_tree(), property_id))

    assert getattr(snapshot, attribute) is None


def test_dropping_one_property_leaves_the_others_reading() -> None:
    """Absence is per-property, so a panel with a partial forecast still reports
    the part it has."""
    snapshot = _snapshot(_without_property(_mutable_tree(), TIME_TO_PRIORITY_SHED))

    assert snapshot.shed_time_to_priority_shed_min is None
    assert snapshot.shed_total_time_remaining_min == int(_published(TOTAL_TIME_REMAINING))


def test_a_panel_with_no_forecast_node_carries_no_forecast() -> None:
    """The presence gate a consumer builds entities from."""
    snapshot = _snapshot(_without_node(_mutable_tree()))

    assert snapshot.shed_time_to_priority_shed_min is None
    assert snapshot.shed_total_time_remaining_min is None
    assert snapshot.shed_full_charge_time_to_priority_shed_min is None
    assert snapshot.shed_full_charge_total_time_remaining_min is None
    assert snapshot.shed_forecast_confidence is None


def test_zero_minutes_is_a_reading_and_not_an_absence() -> None:
    """The distinction the `None` default exists to keep: shedding has started."""
    tree = _mutable_tree()
    tree[PANEL][f"{NODE}/{TIME_TO_PRIORITY_SHED}"] = "0"

    assert _snapshot(tree).shed_time_to_priority_shed_min == 0


def test_a_whole_number_sent_with_a_decimal_point_still_reads() -> None:
    """The datatype declares the quantity, not the formatting. A publisher that
    serialises 3037 as `3037.0` has not stopped publishing minutes."""
    tree = _mutable_tree()
    tree[PANEL][f"{NODE}/{TOTAL_TIME_REMAINING}"] = "4321.0"

    assert _snapshot(tree).shed_total_time_remaining_min == 4321


def test_a_value_that_is_not_a_number_reads_as_absent() -> None:
    """Same answer as not publishing, because neither is a reading."""
    tree = _mutable_tree()
    tree[PANEL][f"{NODE}/{TOTAL_TIME_REMAINING}"] = "unknown"

    assert _snapshot(tree).shed_total_time_remaining_min is None


# ---------------------------------------------------------------------------
# Metadata: the two live estimates carry the declared unit
# ---------------------------------------------------------------------------


def test_the_live_estimates_take_their_unit_from_the_tree() -> None:
    metadata = build_field_metadata(_devices(parent_child_tree()))
    declared = _declared(parent_child_tree())[NODE]["properties"]

    for property_id, field_path in _LIVE_PATHS.items():
        entry = metadata[field_path]
        assert entry.resolved is True
        assert entry.unit == declared[property_id]["unit"]
        assert entry.datatype == declared[property_id]["datatype"]


def test_changing_the_declared_unit_changes_the_metadata() -> None:
    """The mutation proof for the metadata half: the unit is read from the
    device's `$description`, not from the vendored catalog and not from a
    literal in the adapter."""
    tree = _mutable_tree()
    description = json.loads(tree[PANEL]["$description"])
    description["nodes"][NODE]["properties"][TOTAL_TIME_REMAINING]["unit"] = "h"
    tree[PANEL]["$description"] = json.dumps(description)

    metadata = build_field_metadata(_devices(tree))

    assert metadata["panel.shed_total_time_remaining_min"].unit == "h"


def test_a_declared_node_missing_a_property_is_a_gap_not_absent_hardware() -> None:
    """The three-way contract. The node is here, so an omitted property is
    degradation and gets an unresolved row rather than no row."""
    metadata = build_field_metadata(_devices(_without_property(_mutable_tree(), TIME_TO_PRIORITY_SHED)))

    entry = metadata["panel.shed_time_to_priority_shed_min"]
    assert entry == FieldMetadata(unit=None, datatype="unknown", resolved=False)


def test_no_forecast_node_produces_no_rows_at_all() -> None:
    """Hardware that is not there is not a defect: no entry, so a consumer reads
    "nothing will populate this" rather than "this is broken"."""
    metadata = build_field_metadata(_devices(_without_node(_mutable_tree())))

    for field_path in _LIVE_PATHS.values():
        assert field_path not in metadata


def test_the_hypothetical_pair_and_confidence_carry_no_metadata_row() -> None:
    """Deliberate, and asserted so it stays deliberate. They are read into the
    snapshot but rendered beside the two live estimates rather than as readings
    of their own, so there is no unit surface for a row to describe.
    """
    metadata = build_field_metadata(_devices(parent_child_tree()))

    assert "panel.shed_full_charge_time_to_priority_shed_min" not in metadata
    assert "panel.shed_full_charge_total_time_remaining_min" not in metadata
    assert "panel.shed_forecast_confidence" not in metadata
