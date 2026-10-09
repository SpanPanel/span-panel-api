"""The vendored reference captures are the bytes their upstream commit holds.

`fixtures/captures/SHA256SUMS` records the SHA-256 of every file copied byte for
byte from the upstream commit that directory's README names: each capture, and
the upstream `LICENSE` they are distributed under. A copy edited, reformatted or
regenerated here fails, and so does a capture added without a pin, a pin left
behind for a file that was removed, or any other file dropped into the
directory, which `CAPTURES` would otherwise skip without a word.
"""

from __future__ import annotations

import hashlib

import pytest

from reference_payloads.captures import CAPTURES, CAPTURES_DIR, capture_path

_SUMS = CAPTURES_DIR / "SHA256SUMS"
_RECORDS = frozenset({"README.md", "SHA256SUMS"})
"""This repository's own files in the directory, which nothing pins."""
_UPSTREAM_NOTICES = frozenset({"LICENSE"})
"""Upstream files copied beside the captures and pinned like them."""


def _pins() -> dict[str, str]:
    """File name -> pinned SHA-256, in the format `shasum -a 256` writes and `-c` reads."""
    pins: dict[str, str] = {}
    for line in _SUMS.read_text(encoding="utf-8").splitlines():
        digest, _, name = line.partition("  ")
        assert len(digest) == 64 and name, f"malformed SHA256SUMS line: {line!r}"
        assert name not in pins, f"{name} is pinned twice"
        pins[name] = digest
    return pins


def test_there_are_captures_to_replay() -> None:
    """An empty set would make every property over the captures pass vacuously."""
    assert CAPTURES


def test_every_copy_is_pinned_and_every_pin_names_a_copy() -> None:
    assert sorted(_pins()) == sorted(_UPSTREAM_NOTICES | {capture_path(stem).name for stem in CAPTURES})


def test_the_directory_holds_only_the_pinned_copies_and_their_records() -> None:
    present = sorted(path.name for path in CAPTURES_DIR.iterdir())
    assert present == sorted(_RECORDS | set(_pins()))


@pytest.mark.parametrize("name", sorted(_pins()))
def test_each_copy_matches_its_pinned_digest(name: str) -> None:
    assert hashlib.sha256((CAPTURES_DIR / name).read_bytes()).hexdigest() == _pins()[name]
