"""Breaker positions and the solar-role signal, over every reference capture.

Each test reads the model, the occupied spaces and the declared roles from the
capture it replays, so a capture added later is covered without an edit.
"""

from __future__ import annotations

import logging

import pytest

from reference_payloads.captures import (
    CAPTURES,
    CaptureTree,
    capture_adapter,
    capture_snapshot,
    capture_tree,
    hosted_circuits,
)
from reference_payloads.schema_one import parent_child_tree, replay
from span_panel_api_schema_1.const import PANEL_POSITIONS_BY_MODEL, PANEL_SIZE_BY_MODEL

_REFERENCE_PANEL = "example-40t-001"


def _model(tree: CaptureTree) -> str | None:
    return tree.value(tree.root_id, "info/model")


def _occupied(tree: CaptureTree) -> set[int]:
    return {
        int(space)
        for circuit_id in hosted_circuits(tree)
        for space in (tree.value(circuit_id, "info/spaces") or "").split(",")
        if space.strip()
    }


@pytest.mark.parametrize("stem", CAPTURES)
def test_positions_follow_the_declared_model(stem: str, caplog: pytest.LogCaptureFixture) -> None:
    tree = capture_tree(stem)
    model = _model(tree)
    if model not in PANEL_POSITIONS_BY_MODEL:
        return
    with caplog.at_level(logging.WARNING):
        snapshot = capture_adapter(stem).build_snapshot()
    first, last = PANEL_POSITIONS_BY_MODEL[model]

    assert (snapshot.first_position, snapshot.last_position, snapshot.panel_size) == (
        first,
        last,
        PANEL_SIZE_BY_MODEL[model],
    )
    assert all(first <= space <= last for space in _occupied(tree))
    assert not [record for record in caplog.records if "breaker positions" in record.getMessage()]


@pytest.mark.parametrize("stem", CAPTURES)
def test_an_unknown_model_takes_its_range_from_occupied_spaces_and_size_zero(
    stem: str, caplog: pytest.LogCaptureFixture
) -> None:
    tree = capture_tree(stem)
    if _model(tree) != "UNKNOWN":
        return
    with caplog.at_level(logging.WARNING):
        snapshot = capture_snapshot(stem)
    occupied = _occupied(tree)

    assert snapshot.model is None
    assert snapshot.panel_size == 0
    assert (snapshot.first_position, snapshot.last_position) == (min(occupied), max(occupied))
    assert not [record for record in caplog.records if "Unknown panel model" in record.getMessage()]


def test_the_captures_hold_both_a_sized_model_and_an_unknown_one() -> None:
    models = {_model(capture_tree(stem)) for stem in CAPTURES}

    assert models & set(PANEL_POSITIONS_BY_MODEL)
    assert "UNKNOWN" in models


@pytest.mark.parametrize("stem", CAPTURES)
def test_no_position_is_synthesised(stem: str) -> None:
    assert set(capture_snapshot(stem).circuits) == set(capture_tree(stem).circuits())


@pytest.mark.parametrize("stem", CAPTURES)
def test_solar_roles_are_published_only_where_declared(stem: str) -> None:
    tree = capture_tree(stem)
    declared = any(tree.declares(circuit_id, "connection/feeds-role") for circuit_id in tree.circuits())

    assert declared
    assert capture_snapshot(stem).publishes_solar_roles is declared


def test_the_reference_tree_lies_inside_its_model_range_and_publishes_no_solar_roles(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.WARNING):
        snapshot = replay(parent_child_tree(), _REFERENCE_PANEL).build_snapshot()
    first, last = PANEL_POSITIONS_BY_MODEL[str(snapshot.model)]

    assert snapshot.publishes_solar_roles is False
    assert (snapshot.first_position, snapshot.last_position) == (first, last)
    assert all(first <= tab <= last for circuit in snapshot.circuits.values() for tab in circuit.tabs)
    assert not [record for record in caplog.records if "breaker positions" in record.getMessage()]
