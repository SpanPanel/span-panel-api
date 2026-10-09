"""The harness fails where the adapter is unfaithful, not only passes where it is faithful.

A gate that cannot fail proves nothing. So each kind of failure is planted in the
adapter's snapshot, over a small cut of the public r202639 capture, and the
harness must name it: a value with no wire behind it, one behind only unvalued
properties, a wrong sign (on a captured zero too), a derivation that breaks its
rule, and a published property that reaches nothing.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import replace
import json
from pathlib import Path

from ebus_sdk.homie import DiscoveredDevice
import pytest

from span_panel_api.models import SpanPanelSnapshot
from span_panel_api_schema_1 import adapter as adapter_module
from span_panel_api_schema_1.const import TYPE_CIRCUIT, TYPE_PV
from span_panel_api_schema_1.snapshot import build_snapshot

from .harness import FidelityReport, RowKey, run_cell
from .rules import MAIN32_RULES

CAPTURE = Path(__file__).parent.parent / "fixtures" / "main32_r202639-tree-v1.json"

Plant = Callable[[SpanPanelSnapshot], SpanPanelSnapshot]


def _mapping(value: object) -> dict[str, object]:
    if not isinstance(value, Mapping):
        raise TypeError(f"expected a JSON object, got {value!r}")
    return {str(key): item for key, item in value.items()}


def _power(record: Mapping[str, object]) -> float | None:
    raw = _mapping(record.get("numeric_properties", {})).get("meter/active-power")
    return float(raw) if isinstance(raw, str) else None


@pytest.fixture
def small_capture(tmp_path: Path) -> Path:
    """The capture cut to the panel, its lugs, its battery and MID, and one circuit drawing power.

    A circuit at a non-zero reading, so that a wrong sign shows in the captured
    value itself; the battery reads 0 W, so a wrong sign there shows only in the
    values the perturbation publishes.
    """
    tree = _mapping(json.loads(CAPTURE.read_text(encoding="utf-8")))
    devices = _mapping(tree["devices"])
    circuits = sorted(
        device_id
        for device_id, entry in devices.items()
        if _mapping(_mapping(entry)["description"]).get("type") == TYPE_CIRCUIT
    )
    drawing = next(device_id for device_id in circuits if _power(_mapping(devices[device_id])) not in (None, 0.0))
    dropped = {device_id for device_id in circuits if device_id != drawing} | {
        device_id for device_id, entry in devices.items() if _mapping(_mapping(entry)["description"]).get("type") == TYPE_PV
    }
    kept: dict[str, object] = {}
    for device_id, entry in devices.items():
        if device_id in dropped:
            continue
        record = _mapping(entry)
        description = _mapping(record["description"])
        children = description.get("children")
        if isinstance(children, list):
            description["children"] = [child for child in children if child not in dropped]
        kept[device_id] = {**record, "description": description}
    path = tmp_path / "small-tree-v1.json"
    path.write_text(json.dumps({**tree, "devices": kept}), encoding="utf-8")
    return path


@pytest.fixture
def plant(monkeypatch: pytest.MonkeyPatch) -> Callable[[Plant], None]:
    """Change every snapshot the adapter builds."""

    def install(change: Plant) -> None:
        def planted(panel: DiscoveredDevice, children: list[DiscoveredDevice]) -> SpanPanelSnapshot:
            return change(build_snapshot(panel, children))

        monkeypatch.setattr(adapter_module, "build_snapshot", planted)

    return install


def _clean(report: FidelityReport) -> bool:
    return not (report.silently_dropped or report.fabricated or report.mismatched)


def test_the_unchanged_adapter_passes(small_capture: Path) -> None:
    """The baseline the planted failures are measured against."""
    assert _clean(run_cell(small_capture, MAIN32_RULES))


def test_a_constant_with_no_wire_source_is_fabricated(small_capture: Path, plant: Callable[[Plant], None]) -> None:
    plant(lambda snapshot: replace(snapshot, uptime_s=0))
    report = run_cell(small_capture, MAIN32_RULES)
    assert report.fabricated == ("panel.uptime_s",)
    assert report.unowned_leaves() == {"panel.uptime_s"}


def test_a_value_behind_only_unvalued_properties_is_fabricated(small_capture: Path, plant: Callable[[Plant], None]) -> None:
    """The capture declares the MID's model and never values it."""

    def model_or_placeholder(snapshot: SpanPanelSnapshot) -> SpanPanelSnapshot:
        assert snapshot.mid is not None
        return replace(snapshot, mid=replace(snapshot.mid, model=snapshot.mid.model or "placeholder"))

    plant(model_or_placeholder)
    report = run_cell(small_capture, MAIN32_RULES)
    assert report.fabricated == ("mid.model",)
    assert report.failing_rows() == {RowKey("mid", "info/model")}
    assert report.unowned_leaves() == frozenset()


def test_a_wrong_sign_is_mismatched(small_capture: Path, plant: Callable[[Plant], None]) -> None:
    """Circuit power left in the enclosure frame, where a load reads negative.

    Flipped as `0.0 - x`, which never yields -0.0, so the sign itself is what fails.
    """

    def unnegated(snapshot: SpanPanelSnapshot) -> SpanPanelSnapshot:
        circuits = {
            circuit_id: replace(
                circuit, instant_power_w=None if circuit.instant_power_w is None else 0.0 - circuit.instant_power_w
            )
            for circuit_id, circuit in snapshot.circuits.items()
        }
        return replace(snapshot, circuits=circuits)

    plant(unnegated)
    report = run_cell(small_capture, MAIN32_RULES)
    assert [leaf.rsplit(".", 1)[1] for leaf in report.mismatched] == ["instant_power_w"]
    assert report.failing_rows() == {RowKey("circuit", "meter/active-power")}


def test_a_wrong_sign_on_a_reading_captured_at_zero_is_mismatched(
    small_capture: Path, plant: Callable[[Plant], None]
) -> None:
    """The battery's frame on r202639, where the capture's battery reads 0 W.

    A captured zero reads the same under either sign, so only the values the
    perturbation publishes can show the frame is wrong.
    """
    plant(
        lambda snapshot: replace(
            snapshot,
            battery=replace(
                snapshot.battery,
                power_w=None if snapshot.battery.power_w is None else 0.0 - snapshot.battery.power_w,
            ),
        )
    )
    report = run_cell(small_capture, MAIN32_RULES)
    assert report.mismatched == ("battery.power_w",)
    assert report.failing_rows() == {RowKey("bess", "meter/active-power")}


def test_a_derivation_that_breaks_its_rule_is_mismatched(small_capture: Path, plant: Callable[[Plant], None]) -> None:
    """A breaker read as 240 V exactly when it has fewer than two poles."""
    plant(
        lambda snapshot: replace(
            snapshot,
            circuits={
                circuit_id: replace(circuit, is_240v=not circuit.is_240v)
                for circuit_id, circuit in snapshot.circuits.items()
            },
        )
    )
    report = run_cell(small_capture, MAIN32_RULES)
    assert [leaf.rsplit(".", 1)[1] for leaf in report.mismatched] == ["is_240v"]
    assert report.failing_rows() == {RowKey("circuit", "breaker/poles")}


def test_a_published_property_that_reaches_nothing_is_silently_dropped(
    small_capture: Path, plant: Callable[[Plant], None]
) -> None:
    plant(lambda snapshot: replace(snapshot, battery=replace(snapshot.battery, vendor_name=None)))
    report = run_cell(small_capture, MAIN32_RULES)
    assert report.silently_dropped == (RowKey("bess", "info/vendor-name"),)
    assert report.deferred == ()


def test_a_deferred_row_is_reported_apart_from_the_silent_drops(small_capture: Path, plant: Callable[[Plant], None]) -> None:
    plant(lambda snapshot: replace(snapshot, battery=replace(snapshot.battery, vendor_name=None)))
    key = RowKey("bess", "info/vendor-name")
    report = run_cell(small_capture, replace(MAIN32_RULES, deferred={key: "planted for this test"}))
    assert report.silently_dropped == ()
    assert report.deferred == (key,)
