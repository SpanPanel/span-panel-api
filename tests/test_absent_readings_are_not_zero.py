"""A reading the panel has not sent is absent, and absent is not zero.

A retained-topic replay delivers a device's `$description` before its property
values, so there is a window in which the parser knows a circuit exists and
knows nothing it reports. Filling that window with `0.0` publishes a reading
the panel never made, and a consumer cannot tell it apart from a meter that
genuinely sits at zero.

For a cumulative counter the difference is not cosmetic. A consumer that
compensates for firmware counter resets sees the fabricated zero as a reset,
books the whole lifetime counter as an offset, and adds it again on the next
replay. `SpanPanel/span#259` is that failure on real hardware: megawatt-hours
of fictional energy in long-term statistics, one restart at a time.

So the rule these tests pin is a discrimination, not a default: an absent
reading is `None`, a reported zero is `0.0`, and the two never collapse into
each other.
"""

from __future__ import annotations

import json

import pytest

from ebus_sdk.homie import DiscoveredDevice

from conftest import flat_schema
from reference_payloads.schema_one import device_from_topics, parent_child_tree
from span_panel_api.models import SpanCircuitSnapshot, SpanPanelSnapshot
from span_panel_api_schema_0 import SchemaZeroAdapter
from span_panel_api_schema_0.const import TYPE_CIRCUIT, TYPE_CORE, TYPE_POWER_FLOWS
from span_panel_api_schema_1.circuits import build_circuit
from span_panel_api_schema_1.panel import PanelFields, find_lugs

_TREE = parent_child_tree()

# From the fixture: a 1-pole load, and the two lugs devices.
KITCHEN_LIGHTS = "0ab966b95f92a6a51ec548485aa85f54"
LUGS_UPSTREAM = "lugs-upstream"
LUGS_DOWNSTREAM = "lugs-downstream"
PANEL = "example-40t-001"


def _described_only(device_id: str) -> DiscoveredDevice:
    """The device mid-replay: described and ready, no property value yet.

    Exactly what `$description` + `$state` alone produce, which is the state a
    broker leaves a fresh subscriber in until the retained property burst lands.
    """
    topics = _TREE[device_id]
    return device_from_topics(device_id, {"$description": topics["$description"], "$state": "ready"})


def _with_meter(device_id: str, **readings: str) -> DiscoveredDevice:
    """The same device, plus only the named `meter/...` values."""
    topics = _TREE[device_id]
    replay = {"$description": topics["$description"], "$state": "ready"}
    replay.update({f"meter/{prop}": value for prop, value in readings.items()})
    return device_from_topics(device_id, replay)


class TestCircuitReadings:
    """`build_circuit` on a described-but-silent circuit."""

    def test_energy_is_none_before_the_meter_reports(self) -> None:
        circuit = build_circuit(_described_only(KITCHEN_LIGHTS))

        assert circuit.consumed_energy_wh is None
        assert circuit.produced_energy_wh is None

    def test_power_is_none_before_the_meter_reports(self) -> None:
        assert build_circuit(_described_only(KITCHEN_LIGHTS)).instant_power_w is None

    def test_a_reported_zero_stays_zero(self) -> None:
        """The discrimination the whole change exists for.

        A meter that has published `0` is not the same as one that has published
        nothing, and a new circuit legitimately reads zero.
        """
        circuit = build_circuit(
            _with_meter(KITCHEN_LIGHTS, **{"exported-energy": "0", "imported-energy": "0", "active-power": "0"})
        )

        assert circuit.consumed_energy_wh == 0.0
        assert circuit.produced_energy_wh == 0.0
        assert circuit.instant_power_w == 0.0

    def test_one_absent_reading_does_not_take_down_the_others(self) -> None:
        """Absence is per-property; a circuit reporting some of its meter is
        not wholly unknown."""
        circuit = build_circuit(_with_meter(KITCHEN_LIGHTS, **{"exported-energy": "163562.4"}))

        assert circuit.consumed_energy_wh == pytest.approx(163562.4)
        assert circuit.produced_energy_wh is None

    def test_an_unparseable_reading_is_absent_rather_than_zero(self) -> None:
        """Unparseable was already treated as absent; it must not become a
        fabricated zero on the way through."""
        assert build_circuit(_with_meter(KITCHEN_LIGHTS, **{"exported-energy": "n/a"})).consumed_energy_wh is None


class TestPanelReadings:
    """`PanelFields` on described-but-silent lugs."""

    def _panel(self) -> DiscoveredDevice:
        return device_from_topics(PANEL, _TREE[PANEL])

    def test_lugs_energy_is_none_before_the_meter_reports(self) -> None:
        fields = PanelFields(
            self._panel(),
            upstream_lugs=_described_only(LUGS_UPSTREAM),
            downstream_lugs=_described_only(LUGS_DOWNSTREAM),
            mid=None,
        )

        assert fields.main_meter_energy_consumed_wh is None
        assert fields.main_meter_energy_produced_wh is None
        assert fields.feedthrough_energy_consumed_wh is None
        assert fields.feedthrough_energy_produced_wh is None

    def test_lugs_power_is_none_before_the_meter_reports(self) -> None:
        fields = PanelFields(
            self._panel(),
            upstream_lugs=_described_only(LUGS_UPSTREAM),
            downstream_lugs=_described_only(LUGS_DOWNSTREAM),
            mid=None,
        )

        assert fields.instant_grid_power_w is None
        assert fields.feedthrough_power_w is None

    def test_unresolved_lugs_report_nothing_rather_than_zero(self) -> None:
        """The hole a readiness gate cannot see.

        `find_lugs` resolves a lugs device by its `direction` property. Until
        that property arrives the panel has no lugs to read, and the panel-level
        energy fields were being filled with `0.0` — the same fabrication as a
        circuit's, on the sensors that carry the whole site's import and export.
        """
        fields = PanelFields(self._panel(), upstream_lugs=None, downstream_lugs=None, mid=None)

        assert fields.main_meter_energy_consumed_wh is None
        assert fields.main_meter_energy_produced_wh is None
        assert fields.feedthrough_energy_consumed_wh is None
        assert fields.feedthrough_energy_produced_wh is None
        assert fields.instant_grid_power_w is None
        assert fields.feedthrough_power_w is None

    def test_lugs_are_unresolvable_while_direction_is_absent(self) -> None:
        """Pins the premise of the test above rather than assuming it."""
        described = [_described_only(LUGS_UPSTREAM), _described_only(LUGS_DOWNSTREAM)]

        assert find_lugs(described, upstream=True) is None
        assert find_lugs(described, upstream=False) is None


class TestFlatSchemaReadings:
    """The same discrimination in the flat adapter.

    Flat has no per-device tree — one `$description` names every node — so a
    replay reaches the same state by a shorter route: node types known, values
    not yet delivered. The `SpanPanel/span#259` report is from a flat-schema
    panel, so this is the adapter the defect was actually observed on.
    """

    SERIAL = "sim-40t-001"
    CIRCUIT = "ac3dccda46a94b98878a227df6fed588"

    def _adapter(self, **values: str) -> SchemaZeroAdapter:
        adapter = SchemaZeroAdapter(serial_number=self.SERIAL, schema=flat_schema(40))
        adapter.handle_message(
            f"ebus/5/{self.SERIAL}/$description",
            json.dumps({"nodes": {self.CIRCUIT: {"type": TYPE_CIRCUIT}}}),
        )
        adapter.handle_message(f"ebus/5/{self.SERIAL}/$state", "ready")
        for prop, value in values.items():
            adapter.handle_message(f"ebus/5/{self.SERIAL}/{self.CIRCUIT}/{prop}", value)
        return adapter

    def _circuit(self, **values: str) -> SpanCircuitSnapshot:
        return self._adapter(**values).build_snapshot().circuits[self.CIRCUIT]

    def test_readings_are_none_before_the_circuit_reports(self) -> None:
        circuit = self._circuit()

        assert circuit.consumed_energy_wh is None
        assert circuit.produced_energy_wh is None
        assert circuit.instant_power_w is None

    def test_a_reported_zero_stays_zero(self) -> None:
        circuit = self._circuit(**{"exported-energy": "0", "imported-energy": "0", "active-power": "0"})

        assert circuit.consumed_energy_wh == 0.0
        assert circuit.produced_energy_wh == 0.0
        assert circuit.instant_power_w == 0.0

    def test_panel_energy_is_none_while_the_lugs_are_unresolved(self) -> None:
        """Flat resolves lugs by node type or a `direction` property, exactly as
        the tree schema does, and fabricated the same panel-level zeros."""
        snapshot = self._adapter().build_snapshot()

        assert snapshot.main_meter_energy_consumed_wh is None
        assert snapshot.main_meter_energy_produced_wh is None
        assert snapshot.feedthrough_energy_consumed_wh is None
        assert snapshot.feedthrough_energy_produced_wh is None
        assert snapshot.instant_grid_power_w is None
        assert snapshot.feedthrough_power_w is None

    def test_an_unoccupied_breaker_position_still_reads_zero(self) -> None:
        """Not every zero is a fabrication.

        An unmapped tab is synthesised, not parsed: it is a breaker position
        with nothing behind it, so zero power and zero energy are what it
        genuinely reports. Only readings the panel was asked for and did not
        give become `None`.
        """
        unmapped = self._adapter().build_snapshot().circuits.get("unmapped_tab_2")

        assert unmapped is not None
        assert unmapped.instant_power_w == 0.0
        assert unmapped.consumed_energy_wh == 0.0
        assert unmapped.produced_energy_wh == 0.0


class TestIslandingIsNotInferredFromSilence:
    """`dsm_state` reads a grid-power measurement, so it needed the same rule.

    The heuristic asks whether power is crossing the service entrance, and
    concluded "islanded" from a reading at zero. While that zero was fabricated,
    a panel that had reported nothing at all was declared off-grid — the
    fabrication propagating into a state a consumer acts on.
    """

    SERIAL = "sim-40t-001"
    CORE = "core"
    FLOWS = "power-flows"

    def _snapshot(self, grid_flow: str | None = None) -> SpanPanelSnapshot:
        """A panel on battery, with no lugs — so `instant_grid_power_w` is
        unreported — optionally publishing a `power-flows/grid` reading.

        Battery as the dominant source is what sends the heuristic past its two
        authoritative answers and down to the measurement.
        """
        adapter = SchemaZeroAdapter(serial_number=self.SERIAL, schema=flat_schema(40))
        adapter.handle_message(
            f"ebus/5/{self.SERIAL}/$description",
            json.dumps(
                {
                    "nodes": {
                        self.CORE: {"type": TYPE_CORE},
                        self.FLOWS: {"type": TYPE_POWER_FLOWS},
                    }
                }
            ),
        )
        adapter.handle_message(f"ebus/5/{self.SERIAL}/$state", "ready")
        adapter.handle_message(f"ebus/5/{self.SERIAL}/{self.CORE}/dominant-power-source", "BATTERY")
        if grid_flow is not None:
            adapter.handle_message(f"ebus/5/{self.SERIAL}/{self.FLOWS}/grid", grid_flow)
        return adapter.build_snapshot()

    def test_unknown_while_no_grid_power_has_been_reported(self) -> None:
        assert self._snapshot().dsm_state == "UNKNOWN"

    def test_a_reported_zero_still_means_islanded(self) -> None:
        """The measurement, when it exists, is read exactly as before."""
        assert self._snapshot(grid_flow="0").dsm_state == "DSM_OFF_GRID"

    def test_a_reported_flow_still_means_on_grid(self) -> None:
        assert self._snapshot(grid_flow="1500").dsm_state == "DSM_ON_GRID"


class TestTheCaptureStillReads:
    """The fixture is a real panel's retained state; it must be unaffected."""

    def test_a_fully_reported_circuit_keeps_its_readings(self) -> None:
        circuit = build_circuit(device_from_topics(KITCHEN_LIGHTS, _TREE[KITCHEN_LIGHTS]))

        assert circuit.consumed_energy_wh is not None
        assert circuit.produced_energy_wh is not None
        assert circuit.instant_power_w is not None

    def test_the_capture_declares_the_meter_it_reports(self) -> None:
        """Guards the fixtures above: `_described_only` is only a meaningful
        stand-in for mid-replay if the capture really does carry these values
        as separate retained topics."""
        topics = _TREE[KITCHEN_LIGHTS]
        declared = json.loads(topics["$description"])["nodes"]["meter"]["properties"]

        assert "exported-energy" in declared
        assert "meter/exported-energy" in topics
