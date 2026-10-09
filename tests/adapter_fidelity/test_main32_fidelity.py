"""Every property the public MAIN 32 captures declare reaches the snapshot, faithfully.

Each capture is replayed through the real adapter; each declared property is
perturbed to see which snapshot leaves it moves. A property that moves nothing and
is not dropped on purpose by code that says so is silently lost. A value with no
wire behind it, or behind only unvalued properties, is fabricated.

Two cells: the MAIN 32 capture on spanos3/r202639/03, and the MAIN 32 capture on
spanos3/r202633/02, both published with the eBus panel simulator.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from functools import cache
import hashlib
from pathlib import Path

import pytest

from span_panel_api_schema_1.const import DEVICE_TYPE_PREFIX, NODE_METER, TYPE_PANEL
from span_panel_api_schema_1.field_metadata import _DOWNSTREAM_LUGS_FIELDS, _PROPERTY_FIELD_MAP, _UPSTREAM_LUGS_FIELDS

from .harness import DropReason, FidelityReport, RowKey, run_cell
from .rules import MAIN32_RULES

FIXTURES = Path(__file__).parent.parent / "fixtures"


@dataclass(frozen=True, slots=True)
class _Cell:
    capture: Path
    sha256: str
    """Of the file as published at the eBus panel simulator's v0.9.0 tag."""
    deferred: tuple[RowKey, ...]
    """The rows this capture values and the library drops today, each deferred in the rules with its reason."""
    dropped_with_a_value: Mapping[RowKey, str]
    """The valued rows a drop rule covers here, each reviewed, with why dropping it is right."""


_PEER_TYPE = (
    "the declared type of the device the record names, which the library reads from that device's own "
    "$description when it sorts the tree"
)
_DISPATCH = "chooses which adapter parses the tree, so it is consumed before any snapshot exists"

CELLS = {
    "main32_r202639": _Cell(
        capture=FIXTURES / "main32_r202639-tree-v1.json",
        sha256="bf16d8ce21c2b685496700310286198664c6be1a62ea0fe12b1f15144ebfc086",
        deferred=(),
        dropped_with_a_value={
            RowKey("circuit", "connection/feeds-device-type"): _PEER_TYPE,
            RowKey("lugs-upstream", "connection/fed-by-device-type"): _PEER_TYPE,
            RowKey("panel", "info/data-model-version"): _DISPATCH,
        },
    ),
    "main32_r202633": _Cell(
        capture=FIXTURES / "main32-tree-v1.json",
        sha256="0e92bbdca40f7d8d471e8edbfe2b49f986f2995512025bc67ffb4fcf20f7dd30",
        # The upstream lugs here are fed by another panel, and report that link OK.
        deferred=(RowKey("lugs-upstream", "connection/fed-by-device-status"),),
        dropped_with_a_value={
            RowKey("lugs-upstream", "connection/fed-by-device-type"): _PEER_TYPE,
            RowKey("panel", "info/data-model-version"): _DISPATCH,
        },
    ),
}


_UPSTREAM_NOTICES = {
    "distribution-enclosure-simulator.LICENSE": "6174860cd3289ce0d1a39a186e9bbf907504dbac4ef0e00ab0d490d4a0fc2501",
}
"""Upstream files in `FIXTURES` copied beside the captures, byte for byte from the same tag, and the SHA-256 of each."""


@cache
def _report(cell: str) -> FidelityReport:
    return run_cell(CELLS[cell].capture, MAIN32_RULES)


def _why(report: FidelityReport, leaves: tuple[str, ...]) -> str:
    return "\n".join(f"{leaf}: {report.notes[leaf]}" for leaf in leaves)


@pytest.mark.parametrize("cell", sorted(CELLS))
def test_no_property_is_silently_dropped(cell: str) -> None:
    assert _report(cell).silently_dropped == ()


@pytest.mark.parametrize("cell", sorted(CELLS))
def test_only_the_recorded_rows_are_deferred(cell: str) -> None:
    """A deferral that no longer holds fails as surely as a new silent drop."""
    assert _report(cell).deferred == CELLS[cell].deferred


@pytest.mark.parametrize("cell", sorted(CELLS))
def test_only_the_reviewed_rows_are_dropped_with_a_value(cell: str) -> None:
    """A drop rule covers a class of property; a valued one it newly covers waits for review here."""
    assert _report(cell).dropped_with_a_value == tuple(sorted(CELLS[cell].dropped_with_a_value))


@pytest.mark.parametrize("cell", sorted(CELLS))
def test_no_value_is_fabricated(cell: str) -> None:
    report = _report(cell)
    assert report.fabricated == (), _why(report, report.fabricated)


@pytest.mark.parametrize("cell", sorted(CELLS))
def test_every_mapped_value_matches_its_documented_transform(cell: str) -> None:
    report = _report(cell)
    assert report.mismatched == (), _why(report, report.mismatched)


@pytest.mark.parametrize("cell", sorted(CELLS))
def test_the_matrix_covers_every_declared_property(cell: str) -> None:
    report = _report(cell)
    assert sum(1 for fates in report.rows.values() if fates) == len(report.rows)
    assert report.declared_property_instances > 0
    assert sum(report.instance_fates.values()) == report.declared_property_instances


@pytest.mark.parametrize("cell", sorted(CELLS))
def test_no_leaf_is_fabricated_outside_any_row(cell: str) -> None:
    assert _report(cell).unowned_leaves() == frozenset()


@pytest.mark.parametrize("cell", sorted(CELLS))
def test_no_device_is_named_by_its_own_id(cell: str) -> None:
    """While a device forms the grid, the name shown for it is never its device id.

    Both captures publish each non-root device's `$description.name` as its id.
    """
    names = _report(cell).grid_forming_names
    assert names
    assert sorted(device_id for device_id, name in names.items() if name == device_id) == []


@pytest.mark.parametrize("cell", sorted(CELLS))
def test_the_capture_is_the_published_one(cell: str) -> None:
    assert hashlib.sha256(CELLS[cell].capture.read_bytes()).hexdigest() == CELLS[cell].sha256


@pytest.mark.parametrize("notice", sorted(_UPSTREAM_NOTICES))
def test_the_upstream_license_is_the_published_one(notice: str) -> None:
    """The captures are distributed under the license they were published with, unedited."""
    assert hashlib.sha256((FIXTURES / notice).read_bytes()).hexdigest() == _UPSTREAM_NOTICES[notice]


def test_every_intentional_drop_and_derivation_cites_code() -> None:
    assert set(MAIN32_RULES.drops) == set(DropReason)
    assert all(reason.strip() for reason in MAIN32_RULES.justifications.values())


def _role(device_type: str) -> str:
    return "panel" if device_type == TYPE_PANEL else device_type.removeprefix(DEVICE_TYPE_PREFIX)


def test_every_mapped_field_has_a_documented_transform() -> None:
    """A field the library starts mapping cannot reach a snapshot unchecked."""
    mapped = {(RowKey(_role(device_type), f"{node}/{prop}"), path) for device_type, node, prop, path in _PROPERTY_FIELD_MAP}
    for role, lugs_fields in (("lugs-upstream", _UPSTREAM_LUGS_FIELDS), ("lugs-downstream", _DOWNSTREAM_LUGS_FIELDS)):
        mapped |= {(RowKey(role, f"{NODE_METER}/{prop}"), path) for prop, path in lugs_fields}
    missing = sorted((key, path) for key, path in mapped if path not in MAIN32_RULES.transforms.get(key, {}))
    assert missing == []


def test_every_derivation_is_needed() -> None:
    """A derivation no leaf needs is a standing excuse for the next fabrication."""
    used = frozenset().union(*(_report(cell).derivations_used for cell in CELLS))
    assert {derivation.family for derivation in MAIN32_RULES.derivations} == used
