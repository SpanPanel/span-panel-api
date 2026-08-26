"""Provenance checks — do this adapter's hardcoded facts still match the wire?

Design doc testing item 8, clauses 8a and 8b. This is the **only** signal that
catches adapter-axis drift before release. Every other symptom of "SPAN changed
the schema and we did not notice" shows up in production as a silent absence: a
property that stops arriving, a metadata lookup that quietly returns None, an
entity that goes unavailable without an error anywhere.

The failure this guards against has already happened once upstream
(electrification-bus/python-sdk#27 was exactly a hardcoded fact that had stopped
resolving against its source), which is why it is worth having with a single
adapter rather than waiting for schema_1 to make comparison interesting.

Clause 8c (does SUPPORTS_DATA_MODEL_VERSIONS still cover what the panel reports)
is deliberately absent: flat firmware publishes no version to compare against,
and the check only becomes meaningful with a second adapter.
"""

from __future__ import annotations

from typing import Any

import pytest

from reference_payloads.bootstrap import homie_schema
from span_panel_api_schema_0 import const
from span_panel_api_schema_0.field_metadata import _LUGS_FALLBACK, _PROPERTY_FIELD_MAP, _lookup_property


@pytest.fixture(name="schema")
def _schema() -> dict[str, Any]:
    """The captured `GET /api/v2/homie/schema` response — our stand-in for the panel.

    Read through the shipped accessor, so the anchor below is checked against
    the bytes a consumer installing this release gets rather than against a
    file that only exists in this checkout.
    """
    return dict(homie_schema())


# ---------------------------------------------------------------------------
# 8b — anchor check
# ---------------------------------------------------------------------------


def test_captured_schema_still_matches_the_anchor(schema: dict[str, Any]) -> None:
    """The schema revision this adapter was written against.

    A mismatch does not mean the adapter is broken — it means the schema moved
    and every fact below is now unverified until someone looks. That is the
    whole job of an anchor: convert a silent change into a visible one.
    """
    assert schema[const.SCHEMA_ANCHOR_FIELD] == const.SCHEMA_ANCHOR, (
        f"Schema hash moved from {const.SCHEMA_ANCHOR} to {schema[const.SCHEMA_ANCHOR_FIELD]}. "
        "Re-verify the facts in const.py and _PROPERTY_FIELD_MAP against the new schema, "
        "then update SCHEMA_ANCHOR."
    )


def test_anchor_field_is_the_flat_era_name(schema: dict[str, Any]) -> None:
    """Flat serves `typesSchemaHash` over `types`; parent/child renames both to
    `deviceClassesSchemaHash` over `deviceClasses`.

    Pinning the name here is what stops schema_1 from inheriting a field that
    does not exist on its firmware and silently getting no anchor at all.
    """
    assert const.SCHEMA_ANCHOR_FIELD in schema
    assert "deviceClassesSchemaHash" not in schema, "this fixture is parent/child, not flat"
    assert schema["firmwareVersion"] == const.SCHEMA_ANCHOR_FIRMWARE


# ---------------------------------------------------------------------------
# 8a — hardcoded facts resolve against source
# ---------------------------------------------------------------------------


def test_homie_domain_and_version_match_the_schema(schema: dict[str, Any]) -> None:
    """TOPIC_PREFIX is built from these two, so every topic this adapter
    subscribes to or publishes depends on them being right."""
    assert const.HOMIE_DOMAIN == schema["homieDomain"]
    assert const.HOMIE_VERSION == schema["homieVersion"]
    assert const.TOPIC_PREFIX == f"{schema['homieDomain']}/{schema['homieVersion']}"


# Node types this adapter restates from the schema's `types` block.
_SCHEMA_DECLARED_TYPES = (
    const.TYPE_CORE,
    const.TYPE_LUGS,
    const.TYPE_CIRCUIT,
    const.TYPE_BESS,
    const.TYPE_PV,
    const.TYPE_EVSE,
    const.TYPE_POWER_FLOWS,
)

# Node types real firmware publishes in $description but the schema does not
# declare. See const.py: the schema carries only the base lugs type.
_WIRE_ONLY_TYPES = (
    const.TYPE_LUGS_UPSTREAM,
    const.TYPE_LUGS_DOWNSTREAM,
)


@pytest.mark.parametrize("node_type", _SCHEMA_DECLARED_TYPES)
def test_declared_node_types_exist_in_the_schema(node_type: str, schema: dict[str, Any]) -> None:
    assert node_type in schema["types"], f"{node_type} is no longer a declared type"


@pytest.mark.parametrize("node_type", _WIRE_ONLY_TYPES)
def test_wire_only_types_are_absent_but_aliased(node_type: str, schema: dict[str, Any]) -> None:
    """The two namespaces are not the same set, and this pins both halves.

    These types are real — confirmed against a live panel — but undeclared, so
    a metadata lookup for them only works through the alias. If SPAN ever
    *declares* them, the alias becomes wrong and this test says so. If someone
    adds another wire-only subtype without an alias, property metadata silently
    comes back empty for those nodes and this test catches that too.
    """
    assert (
        node_type not in schema["types"]
    ), f"{node_type} is now declared in the schema; the _LUGS_FALLBACK alias may no longer be correct"
    assert node_type in _LUGS_FALLBACK, f"{node_type} is undeclared and unaliased — metadata lookups will return None"
    assert _LUGS_FALLBACK[node_type] in schema["types"]


def test_every_mapped_property_resolves_against_the_schema(schema: dict[str, Any]) -> None:
    """The core 8a assertion.

    `_PROPERTY_FIELD_MAP` is ~70 hardcoded (node_type, property_id) pairs, each
    asserting a property exists on the wire. Every one must resolve through the
    same lookup path `build_field_metadata` uses — otherwise that field silently
    gets no unit and no datatype, and the integration renders an entity with no
    device class rather than failing.
    """
    unresolved = [
        f"{node_type}/{property_id} -> {field_path}"
        for node_type, property_id, field_path in _PROPERTY_FIELD_MAP
        if _lookup_property(schema["types"], node_type, property_id) is None
    ]

    assert not unresolved, "hardcoded properties no longer in the schema:\n  " + "\n  ".join(unresolved)


def test_no_mapped_property_is_missing_a_field_path() -> None:
    """Every mapping row must name a snapshot field, and no two rows may claim
    the same one — a duplicate means one silently overwrites the other."""
    field_paths = [field_path for _, _, field_path in _PROPERTY_FIELD_MAP]

    assert all(field_paths), "a mapping row has an empty field path"
    duplicates = {path for path in field_paths if field_paths.count(path) > 1}
    assert not duplicates, f"field paths claimed by more than one property: {sorted(duplicates)}"


# ---------------------------------------------------------------------------
# Known, deliberate disagreements with the schema
# ---------------------------------------------------------------------------


def test_circuit_active_power_unit_still_disagrees_with_the_schema(schema: dict[str, Any]) -> None:
    """The schema says kW. Real panels publish W. We follow the panel.

    Recorded as an asserted expectation rather than a comment because it is a
    standing contradiction between our implementation and the published schema,
    and the natural instinct on finding it is to "fix" the code back to kW —
    which would reintroduce the 1000x error that 1eef0dc removed after checking
    against real hardware.

    When this test fails because the schema now says W, the disagreement is over:
    delete this test. It failing is good news.
    """
    declared = schema["types"][const.TYPE_CIRCUIT]["active-power"]["unit"]

    assert declared == "kW", (
        "The schema now declares circuit active-power as "
        f"{declared!r} rather than 'kW'. If that is 'W', the long-standing "
        "schema-versus-hardware disagreement is resolved and this test should be deleted."
    )
