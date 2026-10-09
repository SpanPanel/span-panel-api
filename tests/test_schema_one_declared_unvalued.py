"""Declared but unvalued: a model never published.

A `$description` says what a device has; its values arrive separately, and some
never do. A declared model with no value holds connecting only for `FEED_GRACE_S`, because the model
fills device metadata and nothing else: a value that lands later changes the
battery's or the charger's model and moves no key.
"""

from __future__ import annotations

from dataclasses import replace
import logging

import pytest

from reference_payloads.schema_one import replay
from reference_payloads.synthetic_trees import PANEL_ID, battery, evse, hosted_circuit, panel, tree, without_values
from span_panel_api_schema_1.adapter import FEED_GRACE_S

_MODEL = "info/model"
_START = 100.0


def test_a_declared_model_is_waited_on_until_the_grace_runs_out(caplog: pytest.LogCaptureFixture) -> None:
    now = [_START]
    adapter = replay(
        tree(panel(), without_values(battery(), _MODEL), without_values(evse(), _MODEL)),
        PANEL_ID,
        clock=lambda: now[0],
    )

    assert sorted(adapter.circuit_nodes_missing_names()) == ["battery-a", "evse-a"]

    now[0] = _START + FEED_GRACE_S - 0.1
    assert sorted(adapter.circuit_nodes_missing_names()) == ["battery-a", "evse-a"]

    now[0] = _START + FEED_GRACE_S
    with caplog.at_level(logging.INFO, logger="span_panel_api_schema_1.adapter"):
        assert adapter.circuit_nodes_missing_names() == []
        now[0] += FEED_GRACE_S
        assert adapter.circuit_nodes_missing_names() == []

    messages = [record.getMessage() for record in caplog.records if record.levelno == logging.INFO]
    assert len(messages) == 2
    assert any("battery-a" in message and _MODEL in message for message in messages)
    assert any("evse-a" in message and _MODEL in message for message in messages)


def test_the_model_grace_starts_only_once_every_name_has_arrived() -> None:
    now = [_START]
    circuit = hosted_circuit("c-1", (1,), name="Kitchen")
    adapter = replay(
        tree(panel(), without_values(battery(), _MODEL), without_values(circuit, "info/name")),
        PANEL_ID,
        clock=lambda: now[0],
    )
    assert sorted(adapter.circuit_nodes_missing_names()) == ["battery-a", "c-1"]

    now[0] = _START + FEED_GRACE_S
    adapter.handle_message("ebus/5/c-1/info/name", "Kitchen")

    assert adapter.circuit_nodes_missing_names() == ["battery-a"]


def test_a_model_that_arrives_is_no_longer_waited_on() -> None:
    now = [_START]
    adapter = replay(tree(panel(), without_values(battery(), _MODEL)), PANEL_ID, clock=lambda: now[0])
    assert adapter.circuit_nodes_missing_names() == ["battery-a"]

    adapter.handle_message("ebus/5/battery-a/info/model", "Battery")

    assert adapter.circuit_nodes_missing_names() == []


def test_a_late_model_value_only_fills_metadata() -> None:
    """After the grace a model value changes the device's model and nothing else."""
    now = [_START]
    adapter = replay(
        tree(panel(), without_values(battery(), _MODEL), without_values(evse(), _MODEL)),
        PANEL_ID,
        clock=lambda: now[0],
    )
    adapter.circuit_nodes_missing_names()
    now[0] = _START + FEED_GRACE_S
    assert adapter.circuit_nodes_missing_names() == []
    before = adapter.build_snapshot()

    adapter.handle_message("ebus/5/battery-a/info/model", "Battery")
    adapter.handle_message("ebus/5/evse-a/info/model", "Charger")
    after = adapter.build_snapshot()

    assert after == replace(
        before,
        battery=replace(before.battery, model="Battery"),
        evse={key: replace(charger, model="Charger") for key, charger in before.evse.items()},
    )
    assert before.evse, "precondition: the charger is in the snapshot"
