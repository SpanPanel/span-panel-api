"""Tests expected to fail on this line, each with the reason it does.

Keyed by pytest node id without parameters (`<file>::<function>`), so every
parametrized case of a listed function is expected to fail. `conftest.py` marks
each one `xfail(strict=True)`: a listed test that starts passing fails the run
until its row is deleted, and a row naming anything but a test function of its
module stops the run.

`PENDING` rows assert behaviour a later change brings; that change deletes
them. `DIVERGENT` rows assert behaviour this library deliberately does not
have; they stay until the test or the decision changes. A strict xfail stops at
its first failing line, so `test_schema_one_acceptance_companions.py` asserts
what each `DIVERGENT` test checks after its divergent line.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Final

_ACCEPTANCE = "tests/test_schema_one_other_models.py"
_COMPANIONS = "tests/test_schema_one_acceptance_companions.py"

_READINGS = "the panel and circuit readings"
_SHARED = "the shared meters and relays"

_UNPUBLISHED_IS_NONE = f"None for an undeclared reading or shared-with lands with {_READINGS} and {_SHARED}"

PENDING: Final[Mapping[str, str]] = {
    f"{_ACCEPTANCE}::test_the_panel_meter_reads_busbar_current_and_frequency": (
        f"busbar current and frequency land with {_READINGS}"
    ),
    f"{_ACCEPTANCE}::test_a_circuit_reads_its_nominal_voltage_and_protection_functions": (
        f"nominal voltage and protection functions land with {_READINGS}"
    ),
    f"{_ACCEPTANCE}::test_the_model_properties_carry_metadata_from_their_declarations": (
        f"field metadata for the new readings lands with {_READINGS}, "
        f"and shared-with stops being a discovered property with {_SHARED}"
    ),
    f"{_ACCEPTANCE}::test_the_shipped_tree_resolves_every_mapped_row": (
        f"field metadata for the new readings lands with {_READINGS}"
    ),
    f"{_ACCEPTANCE}::test_the_model_properties_are_not_extension_properties": (
        f"the new readings and shared-with leave the extension rows with {_READINGS} and {_SHARED}"
    ),
    f"{_ACCEPTANCE}::test_every_model_property_is_none_where_not_published": _UNPUBLISHED_IS_NONE,
    f"{_ACCEPTANCE}::test_the_reference_main_32_publishes_none_of_them": _UNPUBLISHED_IS_NONE,
    f"{_ACCEPTANCE}::test_shared_with_resolves_to_other_circuits_in_device_id_order": (
        f"shared-with resolved to circuit ids lands with {_SHARED}"
    ),
    f"{_ACCEPTANCE}::test_shared_with_orders_instance_numbers_numerically": (
        f"shared-with in numeric instance order lands with {_SHARED}"
    ),
    f"{_ACCEPTANCE}::test_a_shared_group_naming_no_known_circuit_is_still_shared": (
        f"an empty shared-with for a declared group lands with {_SHARED}"
    ),
    f"{_COMPANIONS}::test_the_shipped_tree_reads_busbar_current_and_frequency": (
        f"busbar current and frequency land with {_READINGS}"
    ),
    f"{_COMPANIONS}::test_the_shipped_tree_resolves_its_shared_relay": (
        f"shared-with resolved to circuit ids lands with {_SHARED}"
    ),
}

_DEVICE_TYPE_AS_PUBLISHED = "the library keeps device_type as published; a SOLAR feeds-role is carried by feeds_role"

DIVERGENT: Final[Mapping[str, str]] = {
    f"{_ACCEPTANCE}::test_a_circuit_whose_feeds_role_is_solar_is_labeled_pv": _DEVICE_TYPE_AS_PUBLISHED,
    f"{_ACCEPTANCE}::test_the_shipped_tree_has_the_other_model_shape": _DEVICE_TYPE_AS_PUBLISHED,
}

assert not PENDING.keys() & DIVERGENT.keys(), "a test is either pending or divergent, never both"

EXPECTED_FAILURES: Final[Mapping[str, str]] = {**PENDING, **DIVERGENT}
