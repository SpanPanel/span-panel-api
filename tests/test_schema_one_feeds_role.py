"""Each circuit's declared feeds role, as `SpanCircuitSnapshot.feeds_role`.

`connection/feeds-role` says what a circuit feeds when that is not itself a
device on the tree. The role is reported as declared and changes nothing else:
a circuit whose role is solar is still a circuit, and no inverter is made up
for it. A value outside the role set, or none at all, reads as `None`.
"""

from __future__ import annotations

from typing import get_args

import pytest

from conftest import flat_schema
from reference_payloads.schema_one import replay
from reference_payloads.synthetic_trees import PANEL_ID, hosted_circuit, panel, tree, without_values
from span_panel_api import FEEDS_ROLES, FeedsRole, SpanPanelSnapshot
from span_panel_api_schema_0 import SchemaZeroAdapter


def _snapshot(role: str | None) -> SpanPanelSnapshot:
    return replay(tree(panel(), hosted_circuit("c-1", (1,), feeds_role=role)), PANEL_ID).build_snapshot()


def test_the_role_set_is_the_literal() -> None:
    assert set(FEEDS_ROLES) == set(get_args(FeedsRole.__value__))
    assert len(FEEDS_ROLES) == len(set(FEEDS_ROLES))


@pytest.mark.parametrize("role", FEEDS_ROLES)
def test_a_declared_role_is_reported_as_published(role: FeedsRole) -> None:
    assert _snapshot(role).circuits["c-1"].feeds_role == role


@pytest.mark.parametrize("role", ["NOT_A_ROLE", "solar", ""])
def test_a_value_outside_the_role_set_reads_none(role: str) -> None:
    assert _snapshot(role).circuits["c-1"].feeds_role is None


def test_an_undeclared_role_reads_none() -> None:
    assert _snapshot(None).circuits["c-1"].feeds_role is None


def test_a_declared_role_not_yet_published_reads_none() -> None:
    circuit = without_values(hosted_circuit("c-1", (1,), feeds_role="LOADS"), "connection/feeds-role")
    snapshot = replay(tree(panel(), circuit), PANEL_ID).build_snapshot()

    assert snapshot.circuits["c-1"].feeds_role is None


@pytest.mark.spec_only
def test_a_solar_role_keeps_the_circuit_a_circuit() -> None:
    """The role travels in `feeds_role`; `device_type` stays as published and no inverter appears."""
    snapshot = _snapshot("SOLAR")

    assert snapshot.circuits["c-1"].feeds_role == "SOLAR"
    assert snapshot.circuits["c-1"].device_type == "circuit"
    assert snapshot.pv_inverters == {}


def test_a_flat_panel_reports_no_role() -> None:
    snapshot = SchemaZeroAdapter(serial_number="sim-40t-001", schema=flat_schema(40)).build_snapshot()

    assert all(circuit.feeds_role is None for circuit in snapshot.circuits.values())
