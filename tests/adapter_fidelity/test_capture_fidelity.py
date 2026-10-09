"""Every property the r202639 reference captures declare reaches the snapshot, faithfully.

The same measurement as the MAIN 32 cells, over each vendored reference capture
(`tests/fixtures/captures/`, pinned by `test_captures_unchanged.py`): each
declared property is perturbed to see what it moves, and every value is held to
the transform the rules document for it. A property that moves nothing and is
not dropped on purpose by code that says so is silently lost; a value with no
wire behind it is fabricated.

What the cells pin is keyed by row, never by capture, so a capture added to the
directory is measured by every test here without an edit.
"""

from __future__ import annotations

from collections.abc import Mapping
from functools import cache
from typing import Final

import pytest

from reference_payloads.captures import CAPTURES, capture_path

from .harness import DropReason, Fate, FidelityReport, RowKey, run_cell
from .rules_captured import CAPTURED_RULES

_PEER_TYPE: Final = (
    "the declared type of the device the record names, which the library reads from that device's own "
    "$description when it sorts the tree"
)
_DISPATCH: Final = "chooses which adapter parses the tree, so it is consumed before any snapshot exists"
_NODE_RULE: Final = (
    "an identity or topology property, which extension.build_extension_properties skips by node: "
    "a label or a wiring fact, never a reading"
)
_SITE: Final = (
    "where the panel is installed: on the identity node, which is never a reading, and on "
    "const.SITE_PROPERTIES wherever else a device declares it"
)

DROPPED_WITH_A_VALUE: Final[Mapping[RowKey, str]] = {
    RowKey("circuit", "connection/count"): _NODE_RULE,
    RowKey("circuit", "connection/feeds-device-type"): _PEER_TYPE,
    RowKey("circuit", "info/dedicated"): _NODE_RULE,
    RowKey("circuit", "info/locations"): _NODE_RULE,
    RowKey("circuit", "info/tags"): _NODE_RULE,
    RowKey("lugs-downstream", "connection/feeds-role"): _NODE_RULE,
    RowKey("lugs-upstream", "connection/service-rating"): _NODE_RULE,
    RowKey("panel", "info/address-lines"): _SITE,
    RowKey("panel", "info/data-model-version"): _DISPATCH,
    RowKey("panel", "info/latitude"): _SITE,
    RowKey("panel", "info/locality"): _SITE,
    RowKey("panel", "info/longitude"): _SITE,
    RowKey("panel", "info/name"): _SITE,
    RowKey("panel", "info/region"): _SITE,
    RowKey("panel", "info/utility-meter-serial-number"): _SITE,
}
"""The valued rows a drop rule covers in some reference capture, each reviewed, with why dropping it is right."""

DROPPED_BY_THE_NODE_RULE: Final = (
    RowKey("circuit", "info/tags"),
    RowKey("circuit", "info/locations"),
    RowKey("circuit", "info/dedicated"),
    RowKey("circuit", "connection/backed-up"),
    RowKey("lugs-upstream", "connection/service-rating"),
    RowKey("lugs-downstream", "connection/feeds-role"),
)
"""Properties the library reads into nothing on purpose, by the identity and topology node rule."""


@cache
def _report(stem: str) -> FidelityReport:
    return run_cell(capture_path(stem), CAPTURED_RULES)


def _why(report: FidelityReport, leaves: tuple[str, ...]) -> str:
    return "\n".join(f"{leaf}: {report.notes[leaf]}" for leaf in leaves)


@pytest.mark.parametrize("stem", CAPTURES)
def test_no_property_is_silently_dropped(stem: str) -> None:
    assert _report(stem).silently_dropped == ()


@pytest.mark.parametrize("stem", CAPTURES)
def test_nothing_is_deferred(stem: str) -> None:
    assert _report(stem).deferred == ()


@pytest.mark.parametrize("stem", CAPTURES)
def test_only_the_reviewed_rows_are_dropped_with_a_value(stem: str) -> None:
    assert set(_report(stem).dropped_with_a_value) <= set(DROPPED_WITH_A_VALUE)


def test_every_reviewed_drop_is_seen() -> None:
    """A reviewed row no capture drops any more is a stale review."""
    seen = set().union(*(_report(stem).dropped_with_a_value for stem in CAPTURES))
    assert seen == set(DROPPED_WITH_A_VALUE)


@pytest.mark.parametrize("stem", CAPTURES)
def test_the_node_rule_drops_what_it_should(stem: str) -> None:
    rows = _report(stem).rows
    for key in DROPPED_BY_THE_NODE_RULE:
        if key in rows:
            assert rows[key] == (Fate.INTENTIONAL,), key


def test_the_captures_declare_what_the_node_rule_drops() -> None:
    declared = set().union(*(_report(stem).rows for stem in CAPTURES))
    assert set(DROPPED_BY_THE_NODE_RULE) <= declared


@pytest.mark.parametrize("stem", CAPTURES)
def test_no_value_is_fabricated(stem: str) -> None:
    report = _report(stem)
    assert report.fabricated == (), _why(report, report.fabricated)


@pytest.mark.parametrize("stem", CAPTURES)
def test_every_mapped_value_matches_its_documented_transform(stem: str) -> None:
    report = _report(stem)
    assert report.mismatched == (), _why(report, report.mismatched)


@pytest.mark.parametrize("stem", CAPTURES)
def test_no_leaf_is_fabricated_outside_any_row(stem: str) -> None:
    assert _report(stem).unowned_leaves() == frozenset()


@pytest.mark.parametrize("stem", CAPTURES)
def test_the_matrix_covers_every_declared_property(stem: str) -> None:
    report = _report(stem)
    assert sum(1 for fates in report.rows.values() if fates) == len(report.rows)
    assert report.declared_property_instances > 0
    assert sum(report.instance_fates.values()) == report.declared_property_instances


@pytest.mark.parametrize("stem", CAPTURES)
def test_no_device_is_named_by_its_own_id(stem: str) -> None:
    names = _report(stem).grid_forming_names
    assert sorted(device_id for device_id, name in names.items() if name == device_id) == []


def test_every_intentional_drop_and_derivation_cites_code() -> None:
    assert set(CAPTURED_RULES.drops) == set(DropReason)
    assert all(reason.strip() for reason in CAPTURED_RULES.justifications.values())


def test_every_derivation_is_needed() -> None:
    """A derivation no leaf needs is a standing excuse for the next fabrication."""
    used = frozenset().union(*(_report(stem).derivations_used for stem in CAPTURES))
    assert {derivation.family for derivation in CAPTURED_RULES.derivations} == used
