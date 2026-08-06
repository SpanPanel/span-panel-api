"""Conformance checks — is every name this adapter reads one the eBus spec defines?

The consumer counterpart to `test_schema_provenance.py`, which does the same job
for the flat adapter against SPAN's own schema document. This one runs against
vendored copies of the eBus capability catalogs, because v1.0 vocabulary comes
from the specification rather than from a per-panel schema.

**The direction matters, and it is not the publisher's.** The simulator asks "is
everything I publish legal?", and for it an omission is legal and abundant. This
asks the opposite question: is everything we *read* actually defined? A consumer
addressing a name the spec no longer carries does not fail — the property simply
never arrives, a metadata lookup returns None, and an entity goes missing. That
has already happened upstream once: `ebus-sdk` 0.18.0 removed the `battery`
capability key outright in favour of `soc`, with no alias. A consumer hardcoding
`battery` would have gone quiet rather than broken.

Two checks with different reach, deliberately:

- **Conformance** (below) compares this adapter against the vendored catalogs and
  always runs, so CI needs no network and no specification checkout.
- **Provenance** (the last test) compares the vendored catalogs against the
  specification itself, and skips unless `EBUS_SPEC_DIR` points at a checkout.

Provenance proves we copied the right bytes; it cannot prove we understood them.
Conformance is where the understanding gets checked.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import re

import pytest

from span_panel_api_schema_1 import const
from span_panel_api_schema_1.field_metadata import _PROPERTY_FIELD_MAP

_SPEC = Path(__file__).parent.parent / "packages" / "schema-1" / "spec"
_CATALOGS = _SPEC / "catalogs"
_DEVICE_TYPES = _SPEC / "registries" / "device-types.md"
_LOCK = Path(const.__file__).parent / "spec_lock.json"


def _lock() -> dict[str, object]:
    with _LOCK.open() as handle:
        loaded: dict[str, object] = json.load(handle)
    return loaded


def _catalog(node: str) -> dict[str, object]:
    with (_CATALOGS / f"{node}.json").open() as handle:
        loaded: dict[str, object] = json.load(handle)
    return loaded


def _catalog_properties(node: str) -> set[str]:
    properties = _catalog(node).get("properties", {})
    assert isinstance(properties, dict)
    return set(properties)


# Properties this adapter reads that no catalog defines.
#
# These are legal: the specification lets a publisher emit properties it has
# never heard of, and SPAN does. They are listed rather than tolerated so that a
# name missing from the catalog has to be a deliberate claim about SPAN's own
# vocabulary, not an unnoticed typo — the two are indistinguishable at runtime,
# since both produce a property that never arrives.
_SPAN_EXTENSIONS: dict[tuple[str, str], str] = {
    (const.NODE_STATUS, "relay"): "panel main relay position; the catalog's status is alerts and comms only",
    (const.NODE_STATUS, "ethernet"): "panel ethernet link state",
    (const.NODE_STATUS, "wifi"): "panel wifi link state",
    (const.NODE_STATUS, "cloud-connection"): "panel vendor-cloud reachability",
    (const.NODE_STATUS, "status"): "EVSE session status",
    (const.NODE_METER, "voltage-a"): "split-phase per-leg voltage; the catalog carries a single voltage",
    (const.NODE_METER, "voltage-b"): "split-phase per-leg voltage; the catalog carries a single voltage",
    (const.NODE_METER, "current-a"): "split-phase per-leg current; the catalog carries a single current",
    (const.NODE_METER, "current-b"): "split-phase per-leg current; the catalog carries a single current",
    (const.NODE_METER, "advertised-current"): "EVSE pilot-advertised current",
    (const.NODE_INFO, "name"): "circuit label; Homie's $name is the device name, not the circuit's",
    (const.NODE_INFO, "spaces"): "breaker spaces occupied, a load-centre concept the catalog has no room for",
    (const.NODE_INFO, "nominal-power"): (
        "PV AC power rating in W. Deliberately not the catalog's nameplate-capacity, "
        "which is stored energy with an abstract unit — a different quantity with a confusable name."
    ),
    (const.NODE_SWITCH, "lock-state"): "EVSE connector lock",
}


# ---------------------------------------------------------------------------
# The lockfile describes what is actually vendored
# ---------------------------------------------------------------------------


def test_every_pinned_capability_is_vendored_at_the_pinned_version() -> None:
    """A pin that names a version the vendored file does not carry is worse than
    no pin: it reports provenance that was never true."""
    pinned = _lock()["implements"]
    assert isinstance(pinned, dict)
    capabilities = pinned["capabilities"]
    assert isinstance(capabilities, dict)

    mismatched = [
        f"{node}: lockfile says {version}, catalog says {_catalog(node).get('version')}"
        for node, version in capabilities.items()
        if _catalog(node).get("version") != version
    ]

    assert not mismatched, "lockfile disagrees with the vendored catalogs:\n  " + "\n  ".join(mismatched)


def test_every_capability_node_this_adapter_reads_has_a_vendored_catalog() -> None:
    """Adding a NODE_* to const.py without vendoring its catalog would leave that
    node's properties unchecked while looking checked."""
    read = {value for name, value in vars(const).items() if name.startswith("NODE_") and isinstance(value, str)}
    vendored = {path.stem for path in _CATALOGS.glob("*.json")}

    assert read <= vendored, f"capability nodes read but not vendored: {sorted(read - vendored)}"


# ---------------------------------------------------------------------------
# The core assertion — every name resolves, or is a declared extension
# ---------------------------------------------------------------------------


def test_every_mapped_property_is_catalogued_or_a_declared_extension() -> None:
    """`_PROPERTY_FIELD_MAP` is the adapter's statement of what it reads. Every
    row must be a property the specification defines, or one this file declares
    SPAN publishes on its own account."""
    undeclared = [
        f"{device_type}  {node}/{property_id}  -> {field_path}"
        for device_type, node, property_id, field_path in _PROPERTY_FIELD_MAP
        if property_id not in _catalog_properties(node) and (node, property_id) not in _SPAN_EXTENSIONS
    ]

    assert not undeclared, (
        "properties read by this adapter that no catalog defines and no extension declares:\n  "
        + "\n  ".join(undeclared)
        + "\n\nEither the specification moved and the adapter must follow, or this is a SPAN "
        "extension and belongs in _SPAN_EXTENSIONS with a reason."
    )


def test_no_declared_extension_has_been_adopted_by_the_specification() -> None:
    """The reverse direction. When upstream adopts a name we carried as an
    extension, the entry becomes wrong — and silently so, because everything
    still works. This converts that into a visible prompt to re-read the catalog,
    since an adopted property may be specified differently than SPAN publishes it.
    """
    adopted = [
        f"{node}/{property_id} — {reason}"
        for (node, property_id), reason in _SPAN_EXTENSIONS.items()
        if property_id in _catalog_properties(node)
    ]

    assert not adopted, (
        "declared as SPAN extensions but now in the catalog:\n  "
        + "\n  ".join(adopted)
        + "\n\nCompare the catalog's definition against what SPAN publishes, then drop the entry."
    )


def test_no_extension_is_declared_for_a_property_nothing_reads() -> None:
    """An allowlist that outlives its use quietly grants permission for names the
    adapter no longer has, which is how allowlists rot."""
    read = {(node, property_id) for _, node, property_id, _ in _PROPERTY_FIELD_MAP}
    unused = sorted(pair for pair in _SPAN_EXTENSIONS if pair not in read)

    assert not unused, f"extensions declared for properties nothing reads: {unused}"


def test_every_device_class_is_in_the_device_types_registry() -> None:
    """The seven classes the mapper sorts the tree by. A class the registry drops
    means SPAN is publishing something eBus no longer names."""
    registry = _DEVICE_TYPES.read_text(encoding="utf-8")
    registered = set(re.findall(r"`(energy\.ebus\.device\.[a-z-]+)`", registry))
    read = {value for name, value in vars(const).items() if name.startswith("TYPE_") and isinstance(value, str)}

    assert read <= registered, f"device classes not in the registry: {sorted(read - registered)}"


# ---------------------------------------------------------------------------
# The rule that does not travel with a vendored file
# ---------------------------------------------------------------------------


def test_an_abstract_unit_is_never_taken_from_the_catalog() -> None:
    """`unit: "energy"` names a dimension, not a unit — a BESS reports kWh, a
    water heater Wh — and the specification requires a publisher to substitute a
    real one. A consumer that trusted the catalog would hand the integration the
    placeholder as though it were a unit.

    This adapter is right by construction, because it reads units from each
    device's `$description` rather than from any catalog. That is worth asserting
    rather than assuming: the catalog is vendored right here, and reaching for it
    is the obvious shortcut the day someone wants a unit the description omits.
    """
    abstract = {
        (node, property_id)
        for node in ("soc", "info")
        for property_id, definition in _catalog(node).get("properties", {}).items()  # type: ignore[union-attr]
        if isinstance(definition, dict) and definition.get("unit") == "energy"
    }

    assert abstract, "no catalog property carries an abstract unit; this test no longer guards anything"
    assert (const.NODE_SOC, "soe") in abstract, "soc/soe is the one this adapter reads; the catalog no longer marks it"

    metadata_source = (Path(const.__file__).parent / "field_metadata.py").read_text(encoding="utf-8")
    assert "spec_lock" not in metadata_source and "catalogs" not in metadata_source, (
        "field_metadata.py now references the vendored spec. Units must come from each device's "
        "$description; the catalog is the superset across all hardware and carries abstract units."
    )


# ---------------------------------------------------------------------------
# Provenance — opportunistic, because it needs a checkout
# ---------------------------------------------------------------------------


def test_vendored_catalogs_are_byte_identical_to_the_specification() -> None:
    """Byte comparison against the specification at `synced_commit`.

    Skipped rather than failed without a checkout: the conformance checks above
    are the ones that must run everywhere, and making all of them depend on a
    second repository would mean they stop running.
    """
    spec_dir = os.environ.get("EBUS_SPEC_DIR")
    if not spec_dir:
        pytest.skip("set EBUS_SPEC_DIR to a specification checkout to verify vendored bytes")

    spec = Path(spec_dir)
    lock = _lock()
    differing = [
        path.name
        for path in sorted(_CATALOGS.glob("*.json"))
        if (spec / "capabilities" / path.name).read_bytes() != path.read_bytes()
    ]

    assert not differing, (
        f"vendored catalogs differ from {spec_dir} (lockfile pins {lock['synced_commit']}): {differing}. "
        "Check the checkout is at synced_commit before assuming the copies are wrong."
    )
