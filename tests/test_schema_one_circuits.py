"""Mapping a v1.0 circuit device onto SpanCircuitSnapshot.

Driven from the reference tree in `tests/reference_payloads`, captured off the
eBus emitter rather than hand-written, so the shapes are a conforming
publisher's rather than my idea of them. See
`scripts/capture_parent_child_reference.py` and the manifest beside it.

Two kinds of question live here and they take their inputs from different
places. What a real panel *looks like* comes from the capture. What this parser
is *obliged* to handle comes from the vendored catalogs, which is why the
priority-value tests below read `load-shed.json` and mutate a device rather than
expecting the capture to carry every enum member.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ebus_sdk.homie import DiscoveredDevice

from reference_payloads.schema_one import device_from_topics, parent_child_tree
from span_panel_api_schema_1.circuits import build_circuit

_TREE = parent_child_tree()
_CATALOGS = Path(__file__).parent.parent / "packages" / "schema-1" / "spec" / "catalogs"

# From the fixture: a 1-pole load, and a 2-pole backfeeding PV breaker.
KITCHEN_LIGHTS = "0ab966b95f92a6a51ec548485aa85f54"
SOLAR_INVERTER = "573066aaddd7b75114c4563ce3af18c4"


def _device(device_id: str) -> DiscoveredDevice:
    return device_from_topics(device_id, _TREE[device_id])


@pytest.fixture(name="kitchen")
def _kitchen() -> DiscoveredDevice:
    return _device(KITCHEN_LIGHTS)


@pytest.fixture(name="solar")
def _solar() -> DiscoveredDevice:
    return _device(SOLAR_INVERTER)


def test_identity_and_name(kitchen: DiscoveredDevice) -> None:
    circuit = build_circuit(kitchen)

    assert circuit.circuit_id == KITCHEN_LIGHTS
    assert circuit.name == "Kitchen Lights"
    assert circuit.relay_state == "CLOSED"


def test_a_load_reports_positive_consumption(kitchen: DiscoveredDevice) -> None:
    """The enclosure frame is the reverse of what the names suggest.

    A load reads negative `active-power` because power flows *out* of the
    panel into it. The snapshot reports consumption as positive, so the sign
    flips here. Getting this backwards is the classic silent defect: every
    number still looks plausible.
    """
    assert kitchen.get_property("meter", "active-power") == "-121.0"

    assert build_circuit(kitchen).instant_power_w == 121.0


def test_a_backfeeding_circuit_reports_negative_consumption(solar: DiscoveredDevice) -> None:
    assert solar.get_property("meter", "active-power") == "8500.0"

    assert build_circuit(solar).instant_power_w == -8500.0


def test_energy_accumulators_are_swapped_to_the_circuit_perspective(solar: DiscoveredDevice) -> None:
    """`imported-energy` is named from the panel's side: energy the panel took
    *from* the circuit, which the circuit produced."""
    circuit = build_circuit(solar)

    assert solar.get_property("meter", "imported-energy") == "182.16666666666666"
    assert circuit.produced_energy_wh == pytest.approx(182.166666, rel=1e-6)
    assert circuit.consumed_energy_wh == 0.0


def test_tabs_come_from_the_published_list_not_a_derivation(solar: DiscoveredDevice) -> None:
    """v1.0 publishes occupied spaces literally. The flat schema published one
    space plus a `dipole` flag and left the consumer to infer `space + 2`."""
    assert solar.get_property("info", "spaces") == "36,38"

    assert build_circuit(solar).tabs == [36, 38]


def test_single_pole_circuit(kitchen: DiscoveredDevice) -> None:
    circuit = build_circuit(kitchen)

    assert circuit.tabs == [1]
    assert circuit.is_240v is False


def test_two_pole_circuit_is_240v(solar: DiscoveredDevice) -> None:
    assert build_circuit(solar).is_240v is True


def test_breaker_rating_and_current(kitchen: DiscoveredDevice) -> None:
    circuit = build_circuit(kitchen)

    assert circuit.breaker_rating_a == 15.0
    assert circuit.current_a == pytest.approx(1.00833, rel=1e-4)


# ---------------------------------------------------------------------------
# The three flat booleans v1.0 retired
# ---------------------------------------------------------------------------


def test_always_on_is_the_inverse_of_relay_controllable(kitchen: DiscoveredDevice, solar: DiscoveredDevice) -> None:
    """`always-on` is retired; the migration guide defines
    `relay-controllable = !always-on`."""
    assert kitchen.get_property("switch", "relay-controllable") == "true"
    assert solar.get_property("switch", "relay-controllable") == "false"

    assert build_circuit(kitchen).always_on is False
    assert build_circuit(kitchen).is_user_controllable is True
    assert build_circuit(solar).always_on is True
    assert build_circuit(solar).is_user_controllable is False


def test_relay_controllable_defaults_to_controllable_when_absent(kitchen: DiscoveredDevice) -> None:
    """The property marks the exception. Defaulting it False would silently
    make every circuit uncontrollable on a panel that omits it."""
    kitchen.update_property("switch", "relay-controllable", "")

    assert build_circuit(kitchen).is_user_controllable is True


def test_sheddable_is_computed_not_read(kitchen: DiscoveredDevice, solar: DiscoveredDevice) -> None:
    """Retired with no replacement property: the guide defines it as
    `priority != NEVER and relay-controllable`."""
    # Kitchen: priority SOC_THRESHOLD (not NEVER) and controllable -> sheddable
    assert build_circuit(kitchen).is_sheddable is True
    # Solar: priority NEVER and not controllable -> not sheddable
    assert solar.get_property("load-shed", "priority") == "NEVER"
    assert build_circuit(solar).is_sheddable is False


def test_never_backup_reads_the_settable_attribute(kitchen: DiscoveredDevice) -> None:
    """v1.0 expresses never-backup as mutability, so the signal is the Homie
    `$settable` attribute on the priority definition, not a value topic."""
    definition = kitchen.get_node_properties("load-shed")["priority"]
    assert definition["settable"] is True

    assert build_circuit(kitchen).is_never_backup is False


def test_a_locked_priority_means_never_backup(kitchen: DiscoveredDevice) -> None:
    description = json.loads(_TREE[KITCHEN_LIGHTS]["$description"])
    description["nodes"]["load-shed"]["properties"]["priority"]["settable"] = False
    kitchen.update_description(json.dumps(description))

    assert build_circuit(kitchen).is_never_backup is True


def test_an_unannounced_settable_means_never_backup(kitchen: DiscoveredDevice) -> None:
    """Omitting the attribute *is* how a panel announces the lock.

    Homie 5 defaults `$settable` to false, and the eBus SDK's description
    builder writes the attribute only when the property is settable — so a
    conforming publisher describes a never-backup circuit by leaving it out, and
    never by publishing `false`. Reading silence as permission offered a
    priority control on exactly the circuits commissioned not to have one.
    """
    description = json.loads(_TREE[KITCHEN_LIGHTS]["$description"])
    del description["nodes"]["load-shed"]["properties"]["priority"]["settable"]
    kitchen.update_description(json.dumps(description))

    assert build_circuit(kitchen).is_never_backup is True


def _catalogued_priorities() -> list[str]:
    """Every value `load-shed` 0.3 declares for `priority`, in catalog order.

    Read from the vendored catalog rather than listed here, so the obligation
    this drives is the specification's current one. A value added upstream
    arrives in this test the moment the catalog is re-vendored, which is the
    only way a contract test stays a contract test.
    """
    with (_CATALOGS / "load-shed.json").open(encoding="utf-8") as handle:
        catalog = json.load(handle)
    declared = str(catalog["properties"]["priority"]["format"])
    return [value.strip() for value in declared.split(",") if value.strip()]


def test_the_catalogued_priority_values_are_the_ones_worth_testing() -> None:
    """The premise, so a catalog that loses `UNKNOWN` does not quietly end the test below."""
    values = _catalogued_priorities()

    assert "UNKNOWN" in values, "`load-shed` no longer declares UNKNOWN; the contract below has changed"
    assert set(values) >= {"UNKNOWN", "NEVER", "OFF_GRID"}, "the baseline every host must publish has moved"


@pytest.mark.parametrize("declared", _catalogued_priorities())
def test_every_catalogued_priority_is_carried_through_rather_than_repaired(kitchen: DiscoveredDevice, declared: str) -> None:
    """Each declared enum member reaches the snapshot as itself.

    **Synthetic, and from the catalog rather than from the capture, on purpose.**
    These are two different obligations and only one of them is about panels.
    `load-shed` 0.3 declares the format and calls `UNKNOWN`, `NEVER` and
    `OFF_GRID` the baseline for every host, so a parser that mishandles any of
    them is broken against the specification no matter what hardware emits. The
    reference capture answers the other question -- what a real panel looks like
    -- and no production capture has ever published `UNKNOWN` (27 circuits,
    two enclosures), so putting one in the fixture would misrepresent a panel in
    order to test a contract. Contract obligations come from the catalog;
    representativeness comes from the capture.

    Sheddability is asserted alongside because it is the derivation that would
    hide a repaired value: `priority != NEVER and relay-controllable` makes every
    member except `NEVER` sheddable, which is the answer the rule gives and not
    one this reader should soften.
    """
    kitchen.update_property("load-shed", "priority", declared)

    circuit = build_circuit(kitchen)

    assert circuit.priority == declared
    assert circuit.is_sheddable is (declared != "NEVER")


# ---------------------------------------------------------------------------
# Robustness
# ---------------------------------------------------------------------------


def test_an_unreadable_number_is_treated_as_absent_not_fatal(kitchen: DiscoveredDevice) -> None:
    """One malformed value must not take down a whole snapshot."""
    kitchen.update_property("meter", "current", "not-a-number")

    assert build_circuit(kitchen).current_a is None


def test_zero_power_never_becomes_negative_zero(kitchen: DiscoveredDevice) -> None:
    """-0.0 compares equal to 0.0 but formats as '-0.0' in the UI."""
    kitchen.update_property("meter", "active-power", "0.0")

    from math import copysign

    assert copysign(1.0, build_circuit(kitchen).instant_power_w) == 1.0
