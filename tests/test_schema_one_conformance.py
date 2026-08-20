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

- **Conformance** — this adapter against the vendored catalogs. Always runs, from
  a vendored copy, so it needs neither network nor a sibling checkout.
- **Coverage** — this adapter against a captured tree from the SPAN simulator,
  the producer our development is done against. Always runs, from a vendored copy.
- **Provenance** — the vendored copies against their sources, which need
  `EBUS_SPEC_DIR` / `PANELBENCH_DIR` to name checkouts. Skipped without them on a
  developer machine and **failed** without them under `CI`, where the workflow
  clones both: see `_unconfigured`, and DEVELOPMENT.md's "A skip here is not a pass".

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
from typing import NoReturn

import pytest

from span_panel_api_schema_1 import const
from span_panel_api_schema_1.charge_limit import SPELLINGS
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


def _unconfigured(reason: str) -> NoReturn:
    """Not configured: skip on a developer machine, fail in CI.

    Locally, skipping is right — not every developer keeps sibling checkouts, and a
    provenance check is not what they are running the suite for.

    In CI it is the opposite. The workflow clones both peers and exports both
    variables, so an unset or wrong path there does not mean "unavailable", it means
    the wiring that makes these checks run has come undone. Skipping on that reads in
    the summary line exactly like passing, which is how these checks stayed silent for
    the nine days it took the vendored capture to go stale. A check that can be
    switched off by a missing environment variable is a check nobody can rely on.

    `CI` rather than a variable of our own, because it is what GitHub Actions and every
    other runner already set — an environment that stops supplying a path has to opt
    *out* of being an environment, which is not something a workflow edit does by
    accident.
    """
    if os.environ.get("CI"):
        pytest.fail(
            f"{reason}. CI configures both peer checkouts, so this is the provenance "
            "wiring being broken rather than a check that is unavailable — and a skip "
            "here is indistinguishable from a pass."
        )
    pytest.skip(reason)


def _checkout(variable: str, what: str, expect: str | None = None) -> Path:
    """A sibling checkout named by an environment variable, or unconfigured.

    A variable that is unset and one pointing at a directory that is gone are the
    same situation — the checkout is not available — and both take the same exit.
    Letting a stale path through instead produces a FileNotFoundError from somewhere
    deep in a comparison, which reads as a broken test rather than an unconfigured one.
    Set them in `.env`; see `.env.example`.

    "Gone" includes *emptied*, which is the form this actually takes. A checkout under
    a temp directory keeps its `.git` and its directory tree while the reaper removes
    the files, so `is_dir()` was true at every level and the comparison still raised.
    Presence of a directory proves nothing here; the caller names one that must hold
    at least one `.json`, which is what distinguishes a populated checkout from the
    skeleton of a reaped one.

    Each of the three states keeps its own message, because they call for different
    actions — set the variable, fix the path, or re-clone — and collapsing them would
    make the most confusing one, the reaped skeleton, look like the simplest one.
    """
    configured = os.environ.get(variable)
    if not configured:
        _unconfigured(f"set {variable} to {what}")
    path = Path(configured)
    if not path.is_dir():
        _unconfigured(f"{variable}={configured} does not exist; point it at {what}")
    if expect is not None and not any((path / expect).glob("*.json")):
        _unconfigured(f"{variable}={configured} has no files under {expect}/ — the checkout is empty or is not {what}")
    return path


def _catalog(node: str) -> dict[str, object]:
    with (_CATALOGS / f"{node}.json").open() as handle:
        loaded: dict[str, object] = json.load(handle)
    return loaded


def _catalog_properties(node: str) -> set[str]:
    """The property set a catalog defines, or an empty one where no catalog exists.

    Empty rather than an error, because "no catalog defines this node" is a real
    and legal state — `config` is one — and the checks below already have the
    vocabulary to say what it means. Raising here instead would take the two
    tests that ask the interesting question (is every name either catalogued or
    a declared extension?) and turn them into a FileNotFoundError from a helper.
    """
    if not (_CATALOGS / f"{node}.json").exists():
        return set()
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

    # The EVSE charge-current surface, which neither source above can express.
    # It is addressed through neither a metadata row nor a `NODE_*`/`PROP_*`
    # call: the node and property are chosen at runtime from whichever spelling
    # the charger's own `$description` declares, so the adapter's read set for
    # it *is* the spelling table. Derived from that table rather than restated,
    # for the same reason as everything else here.
    pairs.update(
        (spelling.node, property_id) for spelling in SPELLINGS for property_id in (spelling.ceiling, spelling.limit)
    )

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
    (const.NODE_STATUS, "wifi-ssid"): (
        "the network the panel is joined to. Declared on the enclosure and documented by "
        "r202633 as the MQTT successor to the flat Wi-Fi endpoint, but absent from the "
        "status catalog, which is alerts and comms only -- the same reason its `wifi` "
        "sibling above is an extension."
    ),
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
    ("config", "max-charge-current"): (
        "the EVSE's commissioned charge-current ceiling, in SPAN's pre-catalog spelling. "
        "No `config` capability exists upstream at all; the catalogued surface is "
        "`charge-limit` 0.1, whose `installer-max` this adapter also reads. Both are read "
        "because the charger's `$description` is the authority on which it publishes, and "
        "the panels we can reach carry no EVSE to settle it."
    ),
    ("config", "user-max-charge-current"): (
        "the settable half of the same extension node, `charge-limit/owner-limit` in the "
        "catalogued spelling. The only settable property this adapter writes outside the "
        "panel and its circuits, which is why it is read from the declaration -- including "
        "its `$settable` flag -- rather than from a constant."
    ),
}


# Properties this adapter reads that the captured simulator tree never declares.
#
# Not defects on either side, but the precise list of what our development
# producer does not exercise — which is exactly the part of the parser that gets
# no evidence from testing against it.
#
# Empty as of 2026-08-08, and that is a measurement rather than a default. Its
# one entry was grid/islanding-state, excused because the simulator modelled a
# MID but no tracked config published one. The producer now publishes a MID, so
# the entry stopped being true and the check below said so. Every property this
# parser reads is now exercised by the capture it is developed against.
#
# The mechanism stays for the next gap. An empty dict is the honest state, and it
# is load-bearing: the coverage check holds every other mapping with nothing
# excused, so a future producer regression fails rather than lands here.
#
# Non-empty again as of 2026-08-10, with one entry and a different cause than the
# last: not a config that failed to enable a device, but a device class the
# producer does not model at all.
_NOT_EXERCISED_BY_SIMULATOR: dict[tuple[str, str], str] = {
    ("grid-forming", "capable"): (
        "BESS model 0.14 decomposes a BESS into `battery` / `inverter` / `mid` child "
        "roles and puts grid-forming on the inverter. The emitter models the BESS as a "
        "single device with no children other than the MID, so no inverter exists to "
        "carry the capability and nothing publishes it. Read anyway, because it is the "
        "decided successor to flat's `grid_islandable` and the mapping is unit-tested "
        "against a synthetic inverter -- but with no producer evidence, which is what "
        "this entry records. `resolve_grid_islandable` returns None rather than False "
        "on absence, so the gap surfaces as an uncreated entity rather than a claim."
    ),
    ("charge-limit", "installer-max"): (
        "the catalogued spelling of the EVSE charge-current ceiling. The producer publishes "
        "the `config/max-charge-current` spelling instead, and both are read because the "
        "charger's `$description` decides. No producer we have declares this one, and no "
        "capture can: the panel we expect access to has no SPAN Drive."
    ),
    ("charge-limit", "owner-limit"): (
        "the catalogued spelling of the settable charge-current limit, unexercised for the "
        "same reason as `installer-max` above. `test_the_entity_reads_the_catalogued_spelling` "
        "in test_schema_one_charge_limit.py drives it from a synthetic description, which is "
        "evidence of a parser and not of a producer -- which is what this entry records."
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


def _fully_excused_nodes() -> set[str]:
    """Nodes every one of whose read properties is a declared extension.

    The node-level counterpart of `_SPAN_EXTENSIONS`, derived from it rather
    than listed beside it. `config` is the case: the specification has no
    capability of that name, so there is no catalog to vendor and no version to
    pin, and the only honest description of the node is the two per-property
    claims already written above.

    Derived, so the tolerance cannot outlive the claim. A node stops being
    excused the moment it is read for a property nobody declared an extension
    for, which is exactly the "unvendored node looks checked" failure the test
    below exists to prevent.
    """
    read: dict[str, set[str]] = {}
    for node, property_id in _read_pairs():
        read.setdefault(node, set()).add(property_id)
    return {node for node, properties in read.items() if all((node, p) in _SPAN_EXTENSIONS for p in properties)}


def test_every_capability_node_this_adapter_reads_has_a_vendored_catalog() -> None:
    """Adding a NODE_* to const.py without vendoring its catalog would leave that
    node's properties unchecked while looking checked."""
    read = {node for node, _ in _read_pairs()}
    vendored = {path.stem for path in _CATALOGS.glob("*.json")}
    missing = read - vendored - _fully_excused_nodes()

    assert not missing, f"capability nodes read but not vendored: {sorted(missing)}"


def test_an_unvendored_node_is_one_the_specification_really_does_not_define() -> None:
    """The claim behind an excused node, checked against the specification.

    `_fully_excused_nodes` says "no catalog exists to vendor". Nothing else can
    check that, because the check runs against the files we chose to copy — so
    a capability adopted upstream under an excused name would stay invisible
    exactly as long as nobody re-read the spec. Opportunistic, like every other
    provenance check here.
    """
    spec = _checkout("EBUS_SPEC_DIR", "a specification checkout to verify vendored bytes", expect="capabilities")
    adopted = sorted(node for node in _fully_excused_nodes() if (spec / "capabilities" / f"{node}.json").exists())

    assert not adopted, (
        f"the specification now defines these capabilities: {adopted}. Vendor the catalog, "
        "pin it in spec_lock.json, and compare what it specifies against what SPAN publishes."
    )


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
    spec = _checkout(
        "EBUS_SPEC_DIR",
        "a specification checkout to verify vendored bytes",
        expect="capabilities",
    )
    differing = [
        path.name
        for path in sorted(_CATALOGS.glob("*.json"))
        if (spec / "capabilities" / path.name).read_bytes() != path.read_bytes()
    ]

    assert not differing, (
        f"vendored catalogs differ from {spec} (lockfile pins {_lock()['synced_commit']}): {differing}. "
        "Check the checkout is at synced_commit before assuming the copies are wrong."
    )


def test_the_vendored_captures_match_the_simulator() -> None:
    """Both captures against the simulator that produced them.

    Byte comparison for the tree, whose content is deterministic. The wire
    capture carries values perturbed by `noise_factor` and an advancing clock, so
    it is compared on shape: same devices, same topics. Holding it to bytes would
    fail on every recapture for a reason nobody can act on.
    """
    sim_dir = _checkout("PANELBENCH_DIR", "a panelbench checkout to verify the captured fixtures")
    fixtures = _peer_fixtures()
    ref, commit = _peer_str("ref"), _peer_str("commit")

    tree_source = sim_dir / fixtures["tree"]
    assert tree_source.exists(), f"{tree_source} is missing; is {sim_dir} on {ref}?"
    assert (
        tree_source.read_bytes() == _SIMULATOR_TREE.read_bytes()
    ), f"the captured tree differs from {tree_source}. Re-capture it and update peer.commit (recorded: {commit})."

    wire_source = sim_dir / fixtures["wire"]
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
    sim_dir = _checkout("PANELBENCH_DIR", "a panelbench checkout to verify the peer record")

    with (sim_dir / ".ebus-spec.json").open() as handle:
        theirs = json.load(handle)
    assert theirs["role"] == _peer_str("role"), "the peer is not publishing; this pairing is not what it claims"
    assert theirs["synced_commit"] == _peer_str("synced_commit"), (
        f"the simulator now pins {theirs['synced_commit']}, we recorded {_peer_str('synced_commit')}. "
        "Re-vendor and update both, or the two sides are reading different vocabularies."
    )


def test_an_unconfigured_peer_checkout_fails_in_ci_and_skips_locally(monkeypatch: pytest.MonkeyPatch) -> None:
    """The guard on the guard.

    Everything above this line is worth exactly as much as the thing that decides
    whether it runs, and that thing is one `if`. It has already gone wrong once in the
    other direction: `PANELBENCH_DIR` named a directory that did not exist, every peer
    check skipped, and nine days of drift accumulated behind a summary line that read
    like a pass.

    So the skip and the failure are both asserted, in both environments, for all three
    of the states `_checkout` distinguishes. Asserting only the CI half would leave the
    local half free to become a failure, which is the change that makes a developer
    delete the check rather than configure it.

    `_checkout` is exercised through its public behaviour — the exception it raises —
    rather than by inspecting `_unconfigured`, so this keeps holding if the branch
    moves into the callers.
    """
    outcomes = (pytest.fail.Exception, pytest.skip.Exception)
    missing = "/nonexistent/peer/checkout"

    monkeypatch.delenv("CI", raising=False)
    monkeypatch.setenv("PANELBENCH_DIR", missing)
    with pytest.raises(outcomes, match="does not exist") as local:
        _checkout("PANELBENCH_DIR", "a panelbench checkout")
    assert local.type is pytest.skip.Exception, (
        f"off CI an unavailable checkout must skip, got {local.typename}. Failing instead is "
        "what makes a developer without sibling checkouts delete the check rather than configure it"
    )

    monkeypatch.setenv("CI", "true")
    for variable, value, expect, why in (
        ("PANELBENCH_DIR", "", None, "unset"),
        ("PANELBENCH_DIR", missing, None, "a path that is gone"),
        ("EBUS_SPEC_DIR", str(_SPEC), "no-such-directory", "a checkout reaped to an empty skeleton"),
    ):
        monkeypatch.setenv(variable, value)
        with pytest.raises(outcomes) as raised:
            _checkout(variable, "a peer checkout", expect=expect)
        assert raised.type is pytest.fail.Exception, (
            f"under CI, {why} must fail rather than {raised.typename.lower()}: a peer check that "
            "skips is one an environment can switch off, and the summary line cannot tell the "
            "difference between that and a pass"
        )
