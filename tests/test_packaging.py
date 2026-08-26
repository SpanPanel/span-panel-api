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

_CAPTURES = Path(__file__).parent / "reference_payloads"
"""Where the reference captures live, and the source of the names below.

Read off the fixture directory rather than listed here so a capture added there
is covered the day it exists — the same reason `_wheel_source_packages` reads
the manifests.
"""


def _is_reference_capture(path: Path) -> bool:
    """Would this file be one of the reference captures, wherever it sits?

    Two questions, because there are two ways to reintroduce the problem: move
    the directory back inside a package, or drop one capture in beside a module.
    """
    return "reference_payloads" in path.parts or path.name in {capture.name for capture in _CAPTURES.glob("*.json")}


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
def test_no_shipped_package_carries_a_reference_capture(distribution: str, package_dir: Path) -> None:
    """Test data must not sit inside a package directory.

    Nothing declares what ships: hatchling takes the whole of `packages = [...]`,
    so a directory dropped inside one is package data by position alone. That is
    how both wheels came to carry a reference capture between 3.0.0 and 3.1.0 —
    56 KB no runtime path reads, in every install, plus an import surface the
    distributions never meant to promise and could not remove without a breaking
    change.

    The captures are fixtures now, under `tests/reference_payloads`. This is the
    cheap half of holding that: CI asserts the same thing against the built
    wheels, where it is finally true rather than inferred, but a failure here
    names the file before anyone builds one.
    """
    payloads = [path for path in package_dir.rglob("*.json") if _is_reference_capture(path)]
    assert not payloads, (
        f"{distribution} would ship {[str(p.relative_to(package_dir)) for p in payloads]} — "
        "reference captures belong in tests/reference_payloads, not inside a package directory"
    )
