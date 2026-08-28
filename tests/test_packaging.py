"""Packaging invariants that only bite downstream.

Nothing in this suite can observe them by importing: a dev workspace resolves
every module from source, where a missing marker file costs nothing. The damage
shows up in someone else's project, against installed wheels, where a fully
annotated distribution silently resolves as Any.
"""

from __future__ import annotations

from pathlib import Path
import tomllib

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]

_REFERENCE_CAPTURES = {
    "span-panel-api-schema-0": "homie_schema.json",
    "span-panel-api-schema-1": "parent_child_tree.json",
}
"""The capture each adapter distribution ships, under `<package>/reference/`.

Keyed by distribution because the question is per-distribution: the bootstrap
ships none, and each adapter ships exactly the capture a consumer of *that*
parser tests against.
"""


def _wheel_source_packages() -> list[tuple[str, Path]]:
    """Every importable package each distribution in the workspace ships.

    Read from the manifests rather than listed here, so an adapter added under
    packages/ is covered the day it exists rather than the day someone
    remembers to extend this file.
    """
    manifests = [_REPO_ROOT / "pyproject.toml", *sorted(_REPO_ROOT.glob("packages/*/pyproject.toml"))]
    found: list[tuple[str, Path]] = []
    for manifest in manifests:
        config = tomllib.loads(manifest.read_text(encoding="utf-8"))
        distribution = config["project"]["name"]
        for package in config["tool"]["hatch"]["build"]["targets"]["wheel"]["packages"]:
            # src/ layout only. The root distribution also ships scripts/, which
            # is tooling rather than an importable API surface consumers type
            # against — see the standing note about it being top-level.
            if package.startswith("src/"):
                found.append((distribution, manifest.parent / package))
    return found


def test_every_workspace_member_is_discovered() -> None:
    """Guards the parametrisation below against passing vacuously: if manifest
    discovery breaks, every packaging test silently collects nothing.

    Derived from the directories on disk rather than a hardcoded list, so
    adding an adapter does not require editing this file — the failure mode
    worth catching is discovery finding *fewer* manifests than exist.
    """
    distributions = {name for name, _ in _wheel_source_packages()}
    expected = 1 + len(list(_REPO_ROOT.glob("packages/*/pyproject.toml")))

    assert "span-panel-api" in distributions
    assert len(distributions) == expected, f"discovered {sorted(distributions)}, expected {expected} distributions"


@pytest.mark.parametrize(
    ("distribution", "package_dir"),
    _wheel_source_packages(),
    ids=lambda value: value.name if isinstance(value, Path) else str(value),
)
def test_every_shipped_package_carries_a_py_typed_marker(distribution: str, package_dir: Path) -> None:
    """PEP 561: without this file a consumer's type checker refuses to read our
    annotations and every symbol we export becomes Any on their side.

    This repo type-checks under --strict and avoids Any deliberately; shipping a
    distribution that erases all of that at the wheel boundary undoes the work
    for exactly the audience it was done for.
    """
    marker = package_dir / "py.typed"
    assert marker.is_file(), f"{distribution} ships {package_dir.name} without a py.typed marker"


@pytest.mark.parametrize(
    ("distribution", "package_dir"),
    _wheel_source_packages(),
    ids=lambda value: value.name if isinstance(value, Path) else str(value),
)
def test_every_adapter_ships_its_reference_capture(distribution: str, package_dir: Path) -> None:
    """Each adapter carries the capture its consumers test against.

    Deliberately shipped, and the reasoning reversed from 3.1.0's. It is true
    that no runtime path reads these — that is why they were pulled out — but the
    cost of *not* shipping them is paid downstream: the integration vendored
    copies and then needed a provenance guard to keep the copies honest, which is
    more machinery than 59 KB in two wheels. Shipping them means a consumer
    pinned to a version of this adapter reads the same bytes that version was
    built and tested against, out of its own site-packages.

    Nothing declares what ships: hatchling takes the whole of `packages = [...]`,
    so a directory inside one is package data by position alone. That cuts both
    ways, which is why this is asserted rather than assumed — CI asserts the same
    against the built wheels, where it is finally true rather than inferred, but a
    failure here names the file before anyone builds one.

    The bootstrap ships neither: it registers no adapter and parses nothing, so
    there is no capture that belongs to it.
    """
    expected = _REFERENCE_CAPTURES.get(distribution)
    if expected is None:
        stray = sorted(str(path.relative_to(package_dir)) for path in package_dir.rglob("reference/*.json"))
        assert not stray, f"{distribution} parses nothing, so it should ship no reference capture; found {stray}"
        return

    capture = package_dir / "reference" / expected
    assert capture.is_file(), f"{distribution} ships no {expected}; run its capture script"
