"""Whether this enclosure's upstream lugs are the utility connection point.

`instant_grid_power_w` is the upstream lugs' `meter/active-power`, and the name
is only true at the service entrance. Put a BESS ahead of the main lugs, or feed
this panel from another panel, and the lugs measure flow on the panel side of
that device while the utility side differs by whatever it contributes or
absorbs. `power_flow_grid` stays site-level and correct; the two then
legitimately disagree.

That disagreement is the whole problem. Without a signal a consumer seeing them
differ cannot tell a topology from a fault, and `fed-by-device-id` -- the
mechanism `power-flows` 0.3 names when it qualifies its own negation table --
was read by this parser and then discarded. So there was nothing downstream
could compute for itself.

**The reference capture is itself one of these topologies**, which is the part
worth knowing before reading anything below. Its upstream lugs publish
`fed-by-device-id: bess` -- the producer wires the battery ahead of the main
lugs, and computes `power-flows/grid` from the lugs reading together with the
BESS rather than by negating the lugs. So on the reference panel
`instant_grid_power_w` has never been the utility figure, and the flag reads
`False` for it. The capture is falsifiable in both directions without being
contrived, which is why the cases below both republish into it and take it away.
"""

from __future__ import annotations

import pytest

from reference_payloads.schema_one import (
    RetainedTopicTree,
    device_from_topics,
    parent_child_tree,
)
from span_panel_api.models import SpanPanelSnapshot
from span_panel_api_schema_1.const import (
    NODE_CONNECTION,
    PROP_FED_BY_DEVICE_ID,
    PROP_FED_BY_DEVICE_STATUS,
)
from span_panel_api_schema_1.snapshot import build_snapshot

PANEL = "example-40t-001"
UPSTREAM_LUGS = "lugs-upstream"

FED_BY_ID_TOPIC = f"{NODE_CONNECTION}/{PROP_FED_BY_DEVICE_ID}"
FED_BY_STATUS_TOPIC = f"{NODE_CONNECTION}/{PROP_FED_BY_DEVICE_STATUS}"


def _mutable_tree() -> dict[str, dict[str, str]]:
    return {device_id: dict(topics) for device_id, topics in parent_child_tree().items()}


def _snapshot(tree: RetainedTopicTree) -> SpanPanelSnapshot:
    panel = device_from_topics(PANEL, tree[PANEL])
    children = [device_from_topics(device_id, topics) for device_id, topics in tree.items() if device_id != PANEL]
    return build_snapshot(panel, children)


def test_the_capture_has_a_battery_ahead_of_its_main_lugs() -> None:
    """The reference panel is behind an upstream DER, and reports itself as one.

    Recorded as its own test because it is a claim about the producer rather than
    about this parser, and because it is easy to assume the opposite: a reference
    capture is usually the simple case, and this one is not. If the producer ever
    moves the battery downstream this fails saying so, rather than silently
    turning the cases below into assertions about a panel that no longer exists.
    """
    tree = _mutable_tree()
    assert tree[UPSTREAM_LUGS][FED_BY_ID_TOPIC] == "bess"
    assert _snapshot(tree).lugs_at_service_entrance is False


def test_a_panel_with_nothing_ahead_of_its_lugs_is_at_the_service_entrance() -> None:
    """The ordinary case, reached by taking the capture's upstream BESS away.

    `True` has to be earned from the tree rather than defaulted into: a mapper
    that always answered `True` would pass this and fail everything above it,
    and one that always answered `False` would do the reverse.
    """
    tree = _mutable_tree()
    del tree[UPSTREAM_LUGS][FED_BY_ID_TOPIC]
    del tree[UPSTREAM_LUGS][FED_BY_STATUS_TOPIC]

    assert _snapshot(tree).lugs_at_service_entrance is True


@pytest.mark.parametrize(
    ("intervening", "topology"),
    [
        ("bess", "a BESS wired between the utility and the main lugs"),
        ("example-40t-002", "an enclosure fed by another enclosure"),
    ],
)
def test_a_device_between_the_utility_and_the_lugs_is_reported(intervening: str, topology: str) -> None:
    """Both topologies the specification names, and one signal covers both.

    They differ in what is upstream and not in what it does to the reading, which
    is why this is one boolean rather than a description of the device. The
    enclosure-chain case could not carry a description anyway: the feeding device
    is another panel with its own tree, not a child of this one.
    """
    tree = _mutable_tree()
    tree[UPSTREAM_LUGS][FED_BY_ID_TOPIC] = intervening
    tree[UPSTREAM_LUGS][FED_BY_STATUS_TOPIC] = "OK"

    assert _snapshot(tree).lugs_at_service_entrance is False, topology


def test_an_empty_fed_by_id_is_not_a_device() -> None:
    """Homie publishes an empty payload for a property with no value.

    An empty string is the absence, not a device named "". Reading it as one
    would tell every panel that publishes the property-but-not-the-value that it
    is behind something.
    """
    tree = _mutable_tree()
    tree[UPSTREAM_LUGS][FED_BY_ID_TOPIC] = ""
    del tree[UPSTREAM_LUGS][FED_BY_STATUS_TOPIC]

    assert _snapshot(tree).lugs_at_service_entrance is True


def test_the_grid_reading_itself_is_unchanged_either_way() -> None:
    """The label is conditional; the measurement is not.

    A panel behind a DER still meters its own lugs correctly, so this must not
    become a reason to withhold or alter the value -- only to say what it is.
    """
    behind = _mutable_tree()
    plain = _mutable_tree()
    del plain[UPSTREAM_LUGS][FED_BY_ID_TOPIC]
    del plain[UPSTREAM_LUGS][FED_BY_STATUS_TOPIC]

    assert _snapshot(behind).lugs_at_service_entrance != _snapshot(plain).lugs_at_service_entrance
    assert _snapshot(behind).instant_grid_power_w == _snapshot(plain).instant_grid_power_w
    assert _snapshot(behind).power_flow_grid == _snapshot(plain).power_flow_grid


def test_a_panel_with_no_upstream_lugs_is_not_reported_as_behind_something() -> None:
    """A tree missing the device says nothing about topology, and `False` is a claim."""
    tree = _mutable_tree()
    del tree[UPSTREAM_LUGS]

    assert _snapshot(tree).lugs_at_service_entrance is True


def test_a_flat_panel_reports_itself_at_the_service_entrance() -> None:
    """The default is a fact about flat firmware, not an optimism about it.

    Flat predates enclosure chaining and publishes no way to express it, so a flat
    panel's lugs *are* its service entrance and `True` is the right answer rather
    than a safe-looking one. schema_0 therefore leaves the field alone, and this
    is what holds the default where it is -- without it the field could be
    defaulted either way and every schema-1 test above would still pass.
    """
    from conftest import flat_schema
    from span_panel_api_schema_0 import SchemaZeroAdapter

    adapter = SchemaZeroAdapter(serial_number="sim-40t-001", schema=flat_schema(40))

    assert SpanPanelSnapshot.__dataclass_fields__["lugs_at_service_entrance"].default is True
    assert adapter.build_snapshot().lugs_at_service_entrance is True


def test_a_float_property_published_without_a_decimal_point_still_parses() -> None:
    """Live firmware publishes integer literals for `float` properties, inconsistently.

    Observed on a service-entrance panel: `power-flows/pv` arrived as `-2434`,
    `battery` as `0` and `grid` as `-310`, while `site` on the same node arrived
    as `2744.0`. All four declare `datatype: float`. An integer literal is a legal
    float payload under Homie 5, so this is firmware being terse rather than
    wrong -- but the inconsistency is between sibling properties of one node, so
    nothing can be inferred from a sample of one property.

    Worth its own test because no producer we develop against does it: the
    reference emitter publishes a decimal point every time, so the whole suite
    would pass while a stricter parse silently dropped three of the four site
    flows to `None` and reported the panel as publishing no power-flows node.
    """
    tree = _mutable_tree()
    for name, terse in (("pv", "-2434"), ("battery", "0"), ("grid", "-310")):
        tree[PANEL][f"power-flows/{name}"] = terse
    tree[PANEL]["power-flows/site"] = "2744.0"

    snapshot = _snapshot(tree)

    assert snapshot.power_flow_pv == -2434.0
    assert snapshot.power_flow_battery == 0.0
    assert snapshot.power_flow_grid == -310.0
    assert snapshot.power_flow_site == 2744.0
    # The four terms sum to zero, which is the identity the specification states
    # and which a dropped term would break silently rather than loudly.
    assert (
        snapshot.power_flow_pv + snapshot.power_flow_battery + snapshot.power_flow_grid + snapshot.power_flow_site
    ) == 0.0
