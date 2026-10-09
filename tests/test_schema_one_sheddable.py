"""`is_sheddable` means the panel will shed it: a shed priority and a controllable relay.

The public r202633 changelog says `UNKNOWN` means "not yet known", not sheddable.
"""

from __future__ import annotations

import json

from ebus_sdk.homie import DiscoveredDevice
import pytest

from reference_payloads.schema_one import device_from_topics, parent_child_tree
from span_panel_api_schema_1.circuits import build_circuit
from span_panel_api_schema_1.const import TYPE_CIRCUIT


def _circuit_device(*, priority: str, relay_controllable: bool) -> DiscoveredDevice:
    """The reference tree's first circuit, with its shed priority and relay-controllable replaced."""
    tree = parent_child_tree()
    circuit_id = next(
        device_id for device_id, topics in sorted(tree.items()) if json.loads(topics["$description"])["type"] == TYPE_CIRCUIT
    )
    topics = {
        **tree[circuit_id],
        "load-shed/priority": priority,
        "switch/relay-controllable": "true" if relay_controllable else "false",
    }
    return device_from_topics(circuit_id, topics)


@pytest.mark.parametrize(
    ("priority", "controllable", "sheddable"),
    [
        ("OFF_GRID", True, True),
        ("SOC_THRESHOLD", True, True),
        ("NEVER", True, False),
        ("UNKNOWN", True, False),
        ("OFF_GRID", False, False),
    ],
)
def test_sheddable_means_a_shed_priority_and_a_controllable_relay(
    priority: str, controllable: bool, sheddable: bool
) -> None:
    circuit = build_circuit(_circuit_device(priority=priority, relay_controllable=controllable))
    assert circuit.is_sheddable is sheddable
