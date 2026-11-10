"""The expected-failure registry stops the run on a row that names nothing.

A row whose file was renamed or deleted, or whose test no longer exists, would
otherwise mark nothing and stay in the registry unnoticed. The check reads the
files, not what a run collected, so a partial run judges the rows the same way
a full one does and raises no false alarm.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import conftest
from conftest import stale_rows
from expected_failures import EXPECTED_FAILURES

_ROOT = Path(__file__).resolve().parent.parent

_SOURCE = '''
def test_module_level():
    pass


async def test_async():
    pass


def helper():
    pass


class TestGroup:
    def test_method(self):
        pass


class Helpers:
    def test_not_collected(self):
        pass
'''


def _tree(tmp_path: Path) -> Path:
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_sample.py").write_text(_SOURCE, encoding="utf-8")
    return tmp_path


def test_the_registry_names_only_tests_that_exist() -> None:
    assert stale_rows(EXPECTED_FAILURES, _ROOT) == []


def test_tests_at_module_level_and_on_test_classes_are_found(tmp_path: Path) -> None:
    root = _tree(tmp_path)
    rows = [f"tests/test_sample.py::{name}" for name in ("test_module_level", "test_async", "test_method")]

    assert stale_rows(rows, root) == []


@pytest.mark.parametrize(
    "row",
    [
        "tests/test_renamed.py::test_module_level",
        "tests/test_sample.py::test_gone",
        "tests/test_sample.py::helper",
        "tests/test_sample.py::test_not_collected",
    ],
    ids=["missing-file", "missing-test", "not-a-test", "not-on-a-test-class"],
)
def test_a_row_naming_nothing_is_stale(tmp_path: Path, row: str) -> None:
    assert stale_rows([row], _tree(tmp_path)) == [row]


def test_a_stale_row_stops_even_a_run_that_collected_nothing_of_it(
    monkeypatch: pytest.MonkeyPatch, pytestconfig: pytest.Config
) -> None:
    """A partial run collects none of the registry's files, and still sees a stale row."""
    stale = "tests/test_does_not_exist.py::test_anything"
    monkeypatch.setattr(conftest, "EXPECTED_FAILURES", {**EXPECTED_FAILURES, stale: "planted"})

    with pytest.raises(pytest.UsageError, match="test_does_not_exist"):
        conftest.pytest_collection_modifyitems(pytestconfig, [])


def test_a_partial_run_raises_no_false_alarm(pytestconfig: pytest.Config) -> None:
    """With every row naming a test that exists, a run that collected none of them proceeds."""
    conftest.pytest_collection_modifyitems(pytestconfig, [])
