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
- **Coverage** — this adapter against the reference tree, captured from the
  emitter our development is done against. Always runs, from a committed capture.
- **Provenance** — the vendored catalogs against their source, which is the
  `ebus-panel-sim` wheel's own `wire/catalogs/`. Always runs too, because the
  emitter is a pinned dev dependency and its files are installed rather than
  cloned. Nothing here skips.

Provenance proves we copied the right bytes; it cannot prove we understood them.
The first two are where the understanding gets checked.
"""

from __future__ import annotations

import ast
from collections.abc import Mapping
import importlib
import importlib.resources
import json
from pathlib import Path
import re

from ebus_panel_sim import __version__ as EMITTER_VERSION
import ebus_panel_sim

from reference_payloads.schema_one import RetainedTopicTree, devices_from_tree, parent_child_tree
from scripts import capture_parent_child_reference as capture_reference

from span_panel_api_schema_1 import const
from span_panel_api_schema_1.charge_limit import SPELLINGS
from span_panel_api_schema_1.field_metadata import _PROPERTY_FIELD_MAP

# Defined in panel.py rather than const.py, which is itself the point: the read
# set has to be derived from the modules that do the reading, not from one
# module that happens to hold most of the vocabulary.
from span_panel_api_schema_1.panel import PROP_ISLANDING_STATE

_REPO = Path(__file__).parent.parent
_SPEC = _REPO / "packages" / "schema-1" / "spec"
_CATALOGS = _SPEC / "catalogs"
_DEVICE_TYPES = _SPEC / "registries" / "device-types.md"
_SOURCE = Path(const.__file__).parent
_LOCK = _SOURCE / "spec_lock.json"

_EMITTER_CATALOGS = Path(str(importlib.resources.files(ebus_panel_sim) / "wire" / "catalogs"))
"""The emitter wheel's own copies of the capability catalogs.

The source our vendored copies are checked against, and it is installed rather
than cloned — `ebus-panel-sim` is a pinned dev dependency, so this directory is
there for every developer and every CI run alike. That is the whole reason the
provenance check below has no skip in it: the sibling checkout it used to need
was a thing an environment could fail to provide, and a check an environment can
switch off is one nobody can rely on.
"""

_UNSOURCED_CATALOGS = {
    "grid-forming": (
        "the emitter models the BESS as a single device with no inverter child, so it "
        "publishes no grid-forming capability and ships no catalog for one. This copy comes "
        "from the specification at `synced_commit` and is the one catalog with no installed "
        "source to compare against."
    )
}
"""Catalogs we vendor that the emitter does not ship, and why.

Listed rather than tolerated, for the same reason `_SPAN_EXTENSIONS` is: a file
with no source and a file whose source moved are indistinguishable from a diff,
and only one of them is deliberate.
"""


def _lock() -> dict[str, object]:
    with _LOCK.open() as handle:
        loaded: dict[str, object] = json.load(handle)
    return loaded


def _without_description_versions(tree: RetainedTopicTree) -> dict[str, dict[str, object]]:
    """A capture with each `$description`'s wall-clock `version` dropped.

    Parsed rather than string-edited, so the comparison is over documents and a
    reformatting of the same declaration is not reported as a wire change.
    `$description` is the only topic reached into; every other payload is
    compared exactly as retained.
    """
    normalised: dict[str, dict[str, object]] = {}
    for device_id, topics in tree.items():
        body: dict[str, object] = dict(topics)
        raw = topics.get("$description")
        if raw is not None:
            described: object = json.loads(raw)
            assert isinstance(described, Mapping), f"{device_id}'s $description is not a JSON object"
            body["$description"] = {key: value for key, value in described.items() if key != "version"}
        normalised[device_id] = body
    return normalised


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


def _emitter_declared() -> set[tuple[str, str]]:
    """Every ``(node, property)`` the reference tree declares anywhere.

    Flattened across devices rather than kept per device type, matching the
    granularity of the catalogs: a capability's property set is the same
    wherever that capability appears.

    Read through the same replay every other test uses rather than off the raw
    JSON, because the capture holds `$description` as a retained *string* — the
    shape a broker serves — and reaching into it by hand here would be a second
    parser for a document `device_from_topics` already knows how to read.
    """
    return {
        (node_id, property_id)
        for device in devices_from_tree(parent_child_tree())
        for node_id in device.get_nodes()
        for property_id in device.get_node_properties(node_id)
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


# Properties this adapter reads that the reference tree never declares.
#
# Not defects on either side, but the precise list of what our development
# producer does not exercise — which is exactly the part of the parser that gets
# no evidence from testing against it.
#
# The mechanism is load-bearing: the coverage check holds every other mapping
# with nothing excused, so a producer regression fails here rather than landing
# quietly. An entry earns its place by naming a reason the emitter cannot
# publish the property, not by recording that it does not.
_NOT_EXERCISED_BY_THE_EMITTER: dict[tuple[str, str], str] = {
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


def test_an_unvendored_node_is_one_the_emitter_really_does_not_publish() -> None:
    """The claim behind an excused node, checked against the emitter.

    `_fully_excused_nodes` says "no catalog exists to vendor". Nothing in the
    files we chose to copy can check that, so a capability adopted upstream under
    an excused name would stay invisible exactly as long as nobody re-read the
    spec — which used to mean until somebody cloned it.

    Asked of the emitter's catalog set instead, and the narrowing is worth
    stating: this now answers "has the producer started carrying a catalog for
    this node?" rather than "does the specification define it?". The producer is
    the spec in runnable form and vendors these files from it, so an adoption it
    publishes against reaches here — and this runs on every machine rather than
    on the ones with a checkout, which the previous version did not.
    """
    adopted = sorted(node for node in _fully_excused_nodes() if (_EMITTER_CATALOGS / f"{node}.json").exists())

    assert not adopted, (
        f"ebus-panel-sim {EMITTER_VERSION} now carries catalogs for {adopted}. Vendor each one, "
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


def test_every_property_read_is_exercised_by_the_emitter() -> None:
    """What the producer never publishes, testing against it never proves.

    An entry in `_NOT_EXERCISED_BY_THE_EMITTER` is not a defect on either side;
    it is a precise statement of where this parser has no evidence, which is
    worth knowing before trusting a passing suite.
    """
    declared = _emitter_declared()
    unexercised = sorted(
        f"{node}/{property_id}"
        for node, property_id in _read_pairs()
        if (node, property_id) not in declared and (node, property_id) not in _NOT_EXERCISED_BY_THE_EMITTER
    )

    assert not unexercised, (
        "properties this adapter reads that the reference tree never declares:\n  "
        + "\n  ".join(unexercised)
        + "\n\nEither the emitter should publish them, or record why it does not in "
        "_NOT_EXERCISED_BY_THE_EMITTER."
    )


def test_nothing_is_recorded_as_unexercised_once_the_emitter_publishes_it() -> None:
    """When the producer starts covering a gap, the entry stops being true. Left
    in place it would go on excusing a property that is now testable."""
    declared = _emitter_declared()
    now_covered = sorted(
        f"{node}/{property_id}" for node, property_id in _NOT_EXERCISED_BY_THE_EMITTER if (node, property_id) in declared
    )

    assert not now_covered, (
        "the emitter now declares these; drop them from _NOT_EXERCISED_BY_THE_EMITTER "
        "and let the coverage check hold them:\n  " + "\n  ".join(now_covered)
    )


# ---------------------------------------------------------------------------
# Provenance — against the installed emitter, so nothing here can be skipped
# ---------------------------------------------------------------------------


def test_vendored_catalogs_are_byte_identical_to_the_emitters() -> None:
    """Are the bytes we vendored the bytes we claim they are?

    Compared against `ebus-panel-sim`'s own copies, which are the specification's
    `capabilities/` carried in a wheel — the emitter is written by the
    organisation that writes the spec, and it publishes against these files
    rather than beside them. So this asks the integrity question of the same
    artifact the reference capture came out of: our vocabulary and the producer's
    are one set of bytes or the comparison says where they differ.

    **Integrity, deliberately not currency.** Whether upstream has moved past the
    release we pin is a different question, and it is pip's: Dependabot raises the
    bump, the bump PR re-captures, and the suite says whether the wire moved.
    Conflating the two is what made the previous version of this check
    unreliable — it failed on a sibling clone that had merely moved ahead.

    Nothing here skips. The old version needed `EBUS_SPEC_DIR` to name a
    checkout, so it ran only where somebody had cloned the specification, and a
    skip reads in a summary line exactly like a pass — which is how a stale
    vendored capture went unnoticed for nine days. A pinned dependency is
    installed for everyone or the environment is broken outright.
    """
    differing = [
        path.name
        for path in sorted(_CATALOGS.glob("*.json"))
        if path.stem not in _UNSOURCED_CATALOGS and path.read_bytes() != (_EMITTER_CATALOGS / path.name).read_bytes()
    ]

    assert not differing, (
        f"vendored catalogs differ from ebus-panel-sim {EMITTER_VERSION}: {differing}. These are byte "
        "copies, so either re-vendor them from the installed wheel or the emitter changed what it "
        "publishes against — and the reference capture was taken through the second one."
    )


def test_every_vendored_catalog_has_a_source_or_a_recorded_reason() -> None:
    """The comparison above is worth what its inputs are, and a file the emitter
    does not ship is silently exempt from it.

    Both directions, because both fail quietly. A new unsourced catalog would be
    vendored bytes nothing checks; an entry in `_UNSOURCED_CATALOGS` that the
    emitter has since started shipping would go on excusing a file that can now
    be compared.
    """
    vendored = {path.stem for path in _CATALOGS.glob("*.json")}
    shipped = {path.stem for path in _EMITTER_CATALOGS.glob("*.json")}

    unchecked = sorted(vendored - shipped - set(_UNSOURCED_CATALOGS))
    assert not unchecked, (
        f"these vendored catalogs have no source in ebus-panel-sim {EMITTER_VERSION} and nothing "
        f"says why: {unchecked}. Nothing compares them, so add each to _UNSOURCED_CATALOGS with the "
        "reason the emitter does not publish that capability."
    )

    now_shipped = sorted(name for name in _UNSOURCED_CATALOGS if name in shipped)
    assert not now_shipped, (
        f"ebus-panel-sim {EMITTER_VERSION} now ships {now_shipped}; drop the entry from "
        "_UNSOURCED_CATALOGS and let the byte comparison hold them."
    )


def test_the_shipped_reference_tree_is_what_the_pinned_emitter_produces() -> None:
    """Regenerate the capture and compare it to the bytes this repository ships.

    This is the whole provenance mechanism, and it replaces every document that
    used to say which release made the tree. Nothing records that any more: the
    pin in `pyproject.toml` is the only statement, and this is what holds it true.
    A record can go stale silently — that is exactly how the tree went three
    emitter releases out of date while thirty test files asserted a producer
    defect as fact. A regeneration cannot: it fails on the commit that moved the
    pin.

    In-process, so it needs no network, no checkout and no broker — the producer
    is installed. On a Dependabot bump of `ebus-panel-sim` this goes red exactly
    when the wire moved, and stays green when it did not.

    **One field is normalised: each `$description`'s `version`.** Homie's own
    change counter, minted from the wall clock when a device is built, so all
    fourteen differ on every run and none of them is a fact about the wire.
    Nothing in this library reads it. Everything else — every topic, every
    payload, every declaration — is held to the byte.
    """
    # Through `serialise` rather than compared as returned. That is the function
    # the script writes with, so this compares what *would be committed* against
    # what is — and it settles the payload types on the way, because the SDK hands
    # the recorder a `DeviceState` for `$state` where its own signature says
    # `str`. Comparing the live objects would have leaned on that enum's string
    # equality to pass, which is not a thing to depend on.
    regenerated = _without_description_versions(json.loads(capture_reference.serialise(capture_reference.capture())))
    shipped = _without_description_versions(parent_child_tree())

    assert regenerated == shipped, (
        f"ebus-panel-sim {EMITTER_VERSION} no longer produces the reference tree this repository "
        "ships. Adopt the new capture and read the diff — it is a wire change:\n"
        "    uv run python scripts/capture_parent_child_reference.py"
    )


def test_the_comparison_would_notice_a_moved_wire() -> None:
    """The guard on the guard: a normalisation that swallowed too much would make
    the check above pass on any capture at all.

    So the one field it drops is dropped by name, and a payload changed anywhere
    else has to survive it. `$description` is where a normalisation is most
    likely to go wrong, because that is the field being reached into.
    """
    tree = parent_child_tree()
    device = next(iter(tree))
    described = json.loads(tree[device]["$description"])
    described["name"] = "a panel by another name"
    mutated = {**tree, device: {**tree[device], "$description": json.dumps(described)}}

    assert _without_description_versions(mutated) != _without_description_versions(tree), (
        "the normalisation drops more than the wall-clock version stamp, so the comparison "
        "above would accept a capture whose declarations had changed"
    )
