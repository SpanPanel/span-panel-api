"""The panel's first and last breaker positions, and a model reported as `UNKNOWN`.

The range comes from the reported model where the table knows it, and from the
occupied spaces where it does not. It only says which positions exist: nothing
is filled in over it and no circuit is made up for an empty position. A model
reported as `UNKNOWN` names no model: it reads as absent, sizes the panel 0
and is neither warned about on every snapshot nor counted as drift.
"""

from __future__ import annotations

import logging

import pytest

from conftest import flat_schema
from reference_payloads.schema_one import device_from_topics, replay
from reference_payloads.synthetic_trees import PANEL_ID, hosted_circuit, panel, panel_with_positions, tree
from span_panel_api_schema_0 import SchemaZeroAdapter
from span_panel_api_schema_1.const import PANEL_POSITIONS_BY_MODEL, PANEL_SIZE_BY_MODEL
from span_panel_api_schema_1.panel import panel_model_drift

_ADVERTISED = (*PANEL_SIZE_BY_MODEL, "UNKNOWN")


@pytest.mark.parametrize("model", sorted(PANEL_POSITIONS_BY_MODEL))
def test_positions_follow_the_reported_model(model: str) -> None:
    first, last = PANEL_POSITIONS_BY_MODEL[model]
    snapshot = replay(panel_with_positions(model, (first, last)), PANEL_ID).build_snapshot()

    assert (snapshot.first_position, snapshot.last_position) == (first, last)
    assert snapshot.panel_size == PANEL_SIZE_BY_MODEL[model]


def test_every_sized_model_has_a_range_as_long_as_its_size() -> None:
    assert set(PANEL_POSITIONS_BY_MODEL) == set(PANEL_SIZE_BY_MODEL)
    for model, (first, last) in PANEL_POSITIONS_BY_MODEL.items():
        assert last - first + 1 == PANEL_SIZE_BY_MODEL[model], model


def test_an_occupied_space_outside_the_model_range_keeps_the_range_and_warns_once(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The table's range stands, and the disagreement is reported once per panel."""
    model, (first, last) = next((model, span) for model, span in PANEL_POSITIONS_BY_MODEL.items() if span[0] > 1)
    adapter = replay(panel_with_positions(model, (first - 1, last)), PANEL_ID)

    with caplog.at_level(logging.WARNING):
        snapshots = [adapter.build_snapshot(), adapter.build_snapshot()]

    assert {(snapshot.first_position, snapshot.last_position) for snapshot in snapshots} == {(first, last)}
    warnings = [record.getMessage() for record in caplog.records if "breaker positions" in record.getMessage()]
    assert len(warnings) == 1
    assert all(part in warnings[0] for part in (model, f"{first}-{last}", f"{first - 1}-{last}"))


def test_a_panel_inside_its_model_range_is_not_reported(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING):
        replay(panel_with_positions("MAIN_32", (1, 32)), PANEL_ID).build_snapshot()

    assert not [record for record in caplog.records if "breaker positions" in record.getMessage()]


def test_an_unknown_model_takes_its_range_from_occupied_spaces_and_size_zero(caplog: pytest.LogCaptureFixture) -> None:
    occupied = tree(
        panel(model="UNKNOWN", advertised_models=_ADVERTISED),
        hosted_circuit("c-12", (12, 14)),
        hosted_circuit("c-3", (3,)),
    )
    with caplog.at_level(logging.WARNING):
        snapshot = replay(occupied, PANEL_ID).build_snapshot()

    assert (snapshot.first_position, snapshot.last_position) == (3, 14)
    assert snapshot.panel_size == 0
    assert snapshot.model is None
    assert not caplog.records


def test_an_unknown_model_with_nothing_occupied_has_no_range() -> None:
    snapshot = replay(tree(panel(model="UNKNOWN", advertised_models=_ADVERTISED)), PANEL_ID).build_snapshot()

    assert (snapshot.first_position, snapshot.last_position) == (None, None)


def test_a_model_the_table_cannot_size_still_warns_and_takes_the_occupied_range(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.WARNING):
        snapshot = replay(panel_with_positions("MAIN_64", (5, 7)), PANEL_ID).build_snapshot()

    assert (snapshot.first_position, snapshot.last_position, snapshot.panel_size) == (5, 7, 0)
    assert snapshot.model == "MAIN_64"
    assert any("MAIN_64" in record.getMessage() for record in caplog.records)


def test_no_position_is_synthesised() -> None:
    snapshot = replay(panel_with_positions("MAIN_32", (2, 9)), PANEL_ID).build_snapshot()

    assert set(snapshot.circuits) == {"c-2", "c-9"}


def test_drift_ignores_unknown(caplog: pytest.LogCaptureFixture) -> None:
    device = device_from_topics(PANEL_ID, tree(panel(model="MAIN_32", advertised_models=_ADVERTISED))[PANEL_ID])

    with caplog.at_level(logging.WARNING):
        assert panel_model_drift(device) == ()
    assert not caplog.records


@pytest.mark.parametrize("role", ["LOADS", "SOLAR"])
def test_a_declared_feeds_role_marks_a_panel_that_publishes_solar_roles(role: str) -> None:
    snapshot = replay(tree(panel(), hosted_circuit("c-1", (1,), feeds_role=role)), PANEL_ID).build_snapshot()

    assert snapshot.publishes_solar_roles is True


def test_a_panel_whose_circuits_declare_no_role_does_not() -> None:
    assert replay(panel_with_positions("MAIN_32", (1,)), PANEL_ID).build_snapshot().publishes_solar_roles is False


def test_a_flat_panel_reports_no_range_and_no_roles() -> None:
    snapshot = SchemaZeroAdapter(serial_number="sim-40t-001", schema=flat_schema(40)).build_snapshot()

    assert (snapshot.first_position, snapshot.last_position) == (None, None)
    assert snapshot.publishes_solar_roles is False
