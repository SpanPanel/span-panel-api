"""Conformance checks — is every name this adapter reads one the eBus spec defines,
and does the producer we test against actually publish it?

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

Three checks with different reach, deliberately:

- **Conformance** — this adapter against the vendored catalogs. Always runs, so
  CI needs no network and no sibling checkout.
- **Coverage** — this adapter against a captured tree from the SPAN simulator,
  the producer our development is done against. Always runs, from a vendored copy.
- **Provenance** — the vendored copies against their sources. Skipped unless
  `EBUS_SPEC_DIR` / `SPAN_SIMULATOR_DIR` point at checkouts.

Provenance proves we copied the right bytes; it cannot prove we understood them.
The first two are where the understanding gets checked, which is why they are the
ones that must run everywhere.
"""

from __future__ import annotations

import ast
import importlib
import json
import os
from pathlib import Path
import re

import pytest

from span_panel_api_schema_1 import const
from span_panel_api_schema_1.field_metadata import _PROPERTY_FIELD_MAP

# Defined in panel.py rather than const.py, which is itself the point: the read
# set has to be derived from the modules that do the reading, not from one
# module that happens to hold most of the vocabulary.
from span_panel_api_schema_1.panel import PROP_ISLANDING_STATE

_SPEC = Path(__file__).parent.parent / "packages" / "schema-1" / "spec"
_CATALOGS = _SPEC / "catalogs"
_DEVICE_TYPES = _SPEC / "registries" / "device-types.md"
_SIMULATOR_TREE = _SPEC / "fixtures" / "simulator_tree.json"
_SIMULATOR_WIRE = _SPEC / "fixtures" / "simulator_wire.json"
_SOURCE = Path(const.__file__).parent
_LOCK = _SOURCE / "spec_lock.json"


def _lock() -> dict[str, object]:
    with _LOCK.open() as handle:
        loaded: dict[str, object] = json.load(handle)
    return loaded


def _peer() -> dict[str, object]:
    peer = _lock()["peer"]
    assert isinstance(peer, dict)
    return peer


def _peer_str(key: str) -> str:
    value = _peer()[key]
    assert isinstance(value, str), f"peer.{key} should be a string"
    return value


def _peer_fixtures() -> dict[str, str]:
    """The captures vendored from the peer, by kind.

    Two of them, answering different questions: `tree` is `$description`
    documents and is what the conformance profile is computed from; `wire` adds
    `$state` and every property value, and is the only one that can drive this
    parser end to end. A consumer checked against declarations alone has been
    checked for understanding the shape of a panel, not for building the right
    snapshot from one.
    """
    fixtures = _peer()["fixtures"]
    assert isinstance(fixtures, dict)
    return {str(kind): str(path) for kind, path in fixtures.items()}


def _catalog(node: str) -> dict[str, object]:
    with (_CATALOGS / f"{node}.json").open() as handle:
        loaded: dict[str, object] = json.load(handle)
    return loaded


def _catalog_properties(node: str) -> set[str]:
    properties = _catalog(node).get("properties", {})
    assert isinstance(properties, dict)
    return set(properties)


def _read_pairs() -> set[tuple[str, str]]:
    """Every ``(capability node, property)`` this adapter addresses.

    Derived, not listed, so it cannot drift from the code the way a hand-kept
    inventory does — the same reason `_derive_required_members` reads the
    protocol rather than restating it.

    Two sources, because the adapter addresses properties two ways.
    `_PROPERTY_FIELD_MAP` is the metadata contract, and is already declarative.
    The snapshot mapper instead calls readers like ``text(mid, NODE_GRID,
    PROP_ISLANDING_STATE)``, which no table records — and building this from the
    metadata map alone quietly omitted every one of them, including the MID
    reads, when this check was first written.

    Constants are resolved from the module that uses them rather than from
    `const`, because not all of them live there.
    """
    pairs = {(node, property_id) for _, node, property_id, _ in _PROPERTY_FIELD_MAP}

    for path in sorted(_SOURCE.glob("*.py")):
        if path.stem == "__init__":
            continue
        module = importlib.import_module(f"span_panel_api_schema_1.{path.stem}")
        for call in (n for n in ast.walk(ast.parse(path.read_text(encoding="utf-8"))) if isinstance(n, ast.Call)):
            names = [arg.id for arg in call.args if isinstance(arg, ast.Name)]
            for node_name in (name for name in names if name.startswith("NODE_")):
                for property_name in (name for name in names if name.startswith("PROP_")):
                    node = getattr(module, node_name, None)
                    property_id = getattr(module, property_name, None)
                    if isinstance(node, str) and isinstance(property_id, str):
                        pairs.add((node, property_id))
    return pairs


def _simulator_declared() -> set[tuple[str, str]]:
    """Every ``(node, property)`` the captured simulator tree declares anywhere.

    Flattened across devices rather than kept per device type, matching the
    granularity of the catalogs: a capability's property set is the same
    wherever that capability appears.
    """
    with _SIMULATOR_TREE.open() as handle:
        tree: dict[str, dict[str, object]] = json.load(handle)
    return {
        (node_id, property_id)
        for device in tree.values()
        for node_id, node in (device.get("nodes") or {}).items()  # type: ignore[union-attr]
        for property_id in (node.get("properties") or {})
    }


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
    (const.NODE_INFO, "direction"): "which of the two identically-typed lugs devices is upstream",
    (const.NODE_INFO, "nominal-power"): (
        "PV AC power rating in W. Deliberately not the catalog's nameplate-capacity, "
        "which is stored energy with an abstract unit — a different quantity with a confusable name."
    ),
    (const.NODE_SWITCH, "lock-state"): "EVSE connector lock",
}


# Properties this adapter reads that the captured simulator tree never declares.
#
# Not defects on either side, but the precise list of what our development
# producer does not exercise — which is exactly the part of the parser that gets
# no evidence from testing against it.
_NOT_EXERCISED_BY_SIMULATOR: dict[tuple[str, str], str] = {
    (const.NODE_GRID, PROP_ISLANDING_STATE): (
        "the simulator models a MID (wire/profiles/mid.json) but its tracked config publishes none, "
        "so grid_state — corrected 2026-08-06 to read islanding-state rather than grid-state — is the "
        "one mapping the producer gives no evidence for"
    ),
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
    read = {node for node, _ in _read_pairs()}
    vendored = {path.stem for path in _CATALOGS.glob("*.json")}

    assert read <= vendored, f"capability nodes read but not vendored: {sorted(read - vendored)}"


# ---------------------------------------------------------------------------
# Conformance — every name resolves, or is a declared extension
# ---------------------------------------------------------------------------


def test_every_property_read_is_catalogued_or_a_declared_extension() -> None:
    """The core assertion, over everything the adapter addresses rather than only
    what carries metadata."""
    undeclared = sorted(
        f"{node}/{property_id}"
        for node, property_id in _read_pairs()
        if property_id not in _catalog_properties(node) and (node, property_id) not in _SPAN_EXTENSIONS
    )

    assert not undeclared, (
        "properties read by this adapter that no catalog defines and no extension declares:\n  "
        + "\n  ".join(undeclared)
        + "\n\nEither the specification moved and the adapter must follow, or this is a SPAN "
        "extension and belongs in _SPAN_EXTENSIONS with a reason."
    )


def test_the_read_set_reaches_past_the_metadata_map() -> None:
    """`_read_pairs` exists because the metadata map is not the whole read set.

    Pinned because the omission is invisible: a check built on the map alone
    passes cleanly while never looking at the MID, which is where `grid_state`
    comes from.
    """
    mapped = {(node, property_id) for _, node, property_id, _ in _PROPERTY_FIELD_MAP}

    assert (const.NODE_GRID, PROP_ISLANDING_STATE) not in mapped, "the MID now carries metadata; simplify this"
    assert (const.NODE_GRID, PROP_ISLANDING_STATE) in _read_pairs(), "the MID read is no longer being discovered"


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
    unused = sorted(pair for pair in _SPAN_EXTENSIONS if pair not in _read_pairs())

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

    metadata_source = (_SOURCE / "field_metadata.py").read_text(encoding="utf-8")
    assert "spec_lock" not in metadata_source and "catalogs" not in metadata_source, (
        "field_metadata.py now references the vendored spec. Units must come from each device's "
        "$description; the catalog is the superset across all hardware and carries abstract units."
    )


# ---------------------------------------------------------------------------
# Coverage — does the producer we develop against exercise what we read?
# ---------------------------------------------------------------------------


def test_the_peer_is_pinned_to_the_same_specification_commit() -> None:
    """Publisher and consumer must be reading the same vocabulary.

    Checked against the recorded peer rather than a live checkout so it runs
    everywhere. Its real job is to make bumping our own pin without looking at
    the other side impossible to do quietly.
    """
    assert _peer_str("synced_commit") == _lock()["synced_commit"], (
        "this adapter and the simulator it is developed against are pinned to different "
        "specification commits; re-vendor both, or record why they may differ."
    )


def test_the_peer_targets_the_same_firmware() -> None:
    """The firmware range is the anchor the two sides actually share — the spec
    says what a device class *may* publish, while a panel publishes one tree."""
    firmware = _lock()["firmware"]
    assert isinstance(firmware, dict)

    assert _peer_str("firmware_range") == firmware["range"]


def test_every_property_read_is_exercised_by_the_simulator() -> None:
    """What the producer never publishes, testing against it never proves.

    An entry in `_NOT_EXERCISED_BY_SIMULATOR` is not a defect on either side; it
    is a precise statement of where this parser has no evidence, which is worth
    knowing before trusting a passing suite.
    """
    declared = _simulator_declared()
    unexercised = sorted(
        f"{node}/{property_id}"
        for node, property_id in _read_pairs()
        if (node, property_id) not in declared and (node, property_id) not in _NOT_EXERCISED_BY_SIMULATOR
    )

    assert not unexercised, (
        "properties this adapter reads that the captured simulator tree never declares:\n  "
        + "\n  ".join(unexercised)
        + "\n\nEither the simulator should publish them, or record why it does not in "
        "_NOT_EXERCISED_BY_SIMULATOR."
    )


def test_nothing_is_recorded_as_unexercised_once_the_simulator_publishes_it() -> None:
    """When the producer starts covering a gap, the entry stops being true. Left
    in place it would go on excusing a property that is now testable."""
    declared = _simulator_declared()
    now_covered = sorted(
        f"{node}/{property_id}" for node, property_id in _NOT_EXERCISED_BY_SIMULATOR if (node, property_id) in declared
    )

    assert not now_covered, (
        "the simulator now declares these; drop them from _NOT_EXERCISED_BY_SIMULATOR "
        "and let the coverage check hold them:\n  " + "\n  ".join(now_covered)
    )


# ---------------------------------------------------------------------------
# Provenance — opportunistic, because it needs checkouts
# ---------------------------------------------------------------------------


def test_vendored_catalogs_are_byte_identical_to_the_specification() -> None:
    """Byte comparison against the specification at `synced_commit`.

    Skipped rather than failed without a checkout: the checks above are the ones
    that must run everywhere, and making them depend on a second repository would
    mean they stop running.
    """
    spec_dir = os.environ.get("EBUS_SPEC_DIR")
    if not spec_dir:
        pytest.skip("set EBUS_SPEC_DIR to a specification checkout to verify vendored bytes")

    spec = Path(spec_dir)
    differing = [
        path.name
        for path in sorted(_CATALOGS.glob("*.json"))
        if (spec / "capabilities" / path.name).read_bytes() != path.read_bytes()
    ]

    assert not differing, (
        f"vendored catalogs differ from {spec_dir} (lockfile pins {_lock()['synced_commit']}): {differing}. "
        "Check the checkout is at synced_commit before assuming the copies are wrong."
    )


def test_the_vendored_captures_match_the_simulator() -> None:
    """Both captures against the simulator that produced them.

    Byte comparison for the tree, whose content is deterministic. The wire
    capture carries values perturbed by `noise_factor` and an advancing clock, so
    it is compared on shape: same devices, same topics. Holding it to bytes would
    fail on every recapture for a reason nobody can act on.
    """
    sim_dir = os.environ.get("SPAN_SIMULATOR_DIR")
    if not sim_dir:
        pytest.skip("set SPAN_SIMULATOR_DIR to a simulator checkout to verify the captured fixtures")

    fixtures = _peer_fixtures()
    ref, commit = _peer_str("ref"), _peer_str("commit")

    tree_source = Path(sim_dir) / fixtures["tree"]
    assert tree_source.exists(), f"{tree_source} is missing; is {sim_dir} on {ref}?"
    assert tree_source.read_bytes() == _SIMULATOR_TREE.read_bytes(), (
        f"the captured tree differs from {tree_source}. Re-capture it and update peer.commit " f"(recorded: {commit})."
    )

    wire_source = Path(sim_dir) / fixtures["wire"]
    assert wire_source.exists(), f"{wire_source} is missing; is {sim_dir} on {ref}?"
    with wire_source.open() as handle:
        theirs = json.load(handle)
    with _SIMULATOR_WIRE.open() as handle:
        ours = json.load(handle)

    assert set(theirs) == set(ours), "the simulator now publishes a different device set than the vendored capture"
    differing = sorted(device for device in ours if set(ours[device]) != set(theirs[device]))
    assert not differing, (
        f"these devices publish different topics than the vendored capture: {differing}. "
        f"Re-vendor from {wire_source} and update peer.commit (recorded: {commit})."
    )


def test_the_peer_record_matches_the_simulator_lockfile() -> None:
    """What we believe the producer pins, against what it actually pins."""
    sim_dir = os.environ.get("SPAN_SIMULATOR_DIR")
    if not sim_dir:
        pytest.skip("set SPAN_SIMULATOR_DIR to a simulator checkout to verify the peer record")

    with (Path(sim_dir) / ".ebus-spec.json").open() as handle:
        theirs = json.load(handle)
    assert theirs["role"] == _peer_str("role"), "the peer is not publishing; this pairing is not what it claims"
    assert theirs["synced_commit"] == _peer_str("synced_commit"), (
        f"the simulator now pins {theirs['synced_commit']}, we recorded {_peer_str('synced_commit')}. "
        "Re-vendor and update both, or the two sides are reading different vocabularies."
    )
