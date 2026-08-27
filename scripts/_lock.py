"""`spec_lock.json`, and the shape checks that reading it honestly needs.

Two scripts ask this file for a pin, and they ask different questions of it.
`capture_parent_child_reference.py` asks what release the reference tree is a
capture of, so it can refuse to write one taken from any other. `peer_drift.py`
asks what every producer was pinned at, so it can ask that producer whether it
has moved. Neither restates a pin — and the path to the lockfile is not a third
place to get it wrong either, so it is named here once and imported.

`json.load` returns `object`, and a reader under strict typing is not allowed to
pretend otherwise. The helpers below are how that stays true without a `cast`:
each one narrows exactly one shape and says what it found when the shape is
wrong. A malformed lockfile is fatal in both callers — it is a file this
repository owns, and a broken one makes every pin in it a guess — so they exit
with a sentence naming the key rather than raising for a caller that has nothing
useful to do about it.
"""

from __future__ import annotations

from collections.abc import Mapping
import json
import pathlib

REPO = pathlib.Path(__file__).resolve().parent.parent
LOCK = REPO / "packages" / "schema-1" / "src" / "span_panel_api_schema_1" / "spec_lock.json"


def mapping(value: object, where: str) -> dict[str, object]:
    if not isinstance(value, Mapping):
        raise SystemExit(f"{where} must be a mapping, got {type(value).__name__}")
    return {str(key): item for key, item in value.items()}


def required(source: Mapping[str, object], key: str, where: str) -> object:
    """One key that has to be there, reported the way everything else here is.

    Indexing straight into the mapping says the same thing as a bare `KeyError`
    traceback, which is the one failure mode that makes a reader work out what
    the caller wanted. Every other malformed input exits with a sentence naming
    it.
    """
    if key not in source:
        raise SystemExit(f"{where} has no {key!r} entry")
    return source[key]


def string(source: Mapping[str, object], key: str, where: str) -> str:
    """A required key whose value the lockfile promises is a string."""
    value = required(source, key, where)
    if not isinstance(value, str):
        raise SystemExit(f"{where}.{key} must be a string, got {type(value).__name__}")
    return value


def load() -> dict[str, object]:
    with LOCK.open(encoding="utf-8") as handle:
        document: object = json.load(handle)
    return mapping(document, LOCK.name)


def peer(name: str, lock: Mapping[str, object] | None = None) -> dict[str, object]:
    """One producer's block, by name.

    Keyed rather than positional, for the reason `spec_lock.json` says at
    `peers`: every reader wants a specific producer, never "the first one".
    Callers that have already read the lockfile pass it in rather than reading
    it again; the ones that want a single pin and nothing else do not have to.
    """
    document = load() if lock is None else lock
    peers = mapping(required(document, "peers", LOCK.name), "peers")
    return mapping(required(peers, name, "peers"), f"peers.{name}")
