"""`scripts/peer_drift.py` — the check that asks a producer whether it moved.

Every test here hands the script fake fetchers, so nothing in this file touches
the network: the point of `HttpGet` / `LsRemote` being injectable is that the
comparisons can be exercised on a laptop with no connection, and that a suite
which passes today passes on the day panelbench cuts a release.

What is deliberately *not* faked is the lockfile. `main` reads the real
`spec_lock.json`, because "the pins live in exactly one place" is the property
these tests are here to hold, and a fixture standing in for it would test a copy.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import io
import json
from pathlib import Path
import re

import pytest

from span_panel_api_schema_1 import const

from scripts.peer_drift import (
    EBUS_SPEC_FILE,
    PANEL_SIM,
    PANELBENCH,
    SPECIFICATION,
    VENDORED,
    HttpGet,
    LsRemote,
    Unreachable,
    _headers,
    emitter,
    exit_code,
    fixtures,
    main,
    panelbench,
    specification,
    unasked,
    unvendored,
)

_LOCK = Path(const.__file__).parent / "spec_lock.json"
_SCRIPTS = Path(__file__).parent.parent / "scripts"

_HEAD = "a" * 40
"""A branch head that is not any pinned commit, and obviously synthetic."""


def _lock() -> dict[str, object]:
    """The real lockfile, read here rather than through the code under test."""
    with _LOCK.open(encoding="utf-8") as handle:
        document: dict[str, object] = json.load(handle)
    return document


def _peer(name: str) -> dict[str, object]:
    peers = _lock()["peers"]
    assert isinstance(peers, dict)
    block = peers[name]
    assert isinstance(block, dict)
    return block


def _pinned(name: str, key: str) -> str:
    value = _peer(name)[key]
    assert isinstance(value, str)
    return value


def _pypi(version: str) -> str:
    return json.dumps({"info": {"version": version}})


def _comparison(ahead: int, behind: int, files: Sequence[str]) -> str:
    return json.dumps(
        {
            "status": "diverged" if behind else "ahead",
            "ahead_by": ahead,
            "behind_by": behind,
            "files": [{"filename": name} for name in files],
        }
    )


def _http(answers: Mapping[str, str]) -> HttpGet:
    """Answer by URL fragment; anything unmatched is unreachable, loudly."""

    def get(url: str) -> str:
        for fragment, body in answers.items():
            if fragment in url:
                return body
        raise Unreachable(f"no fake answer for {url}")

    return get


def _refuses_http() -> HttpGet:
    def get(url: str) -> str:
        raise AssertionError(f"asked {url}, which this comparison should not need")

    return get


def _remote(heads: Mapping[str, str]) -> LsRemote:
    def resolve(repo: str, ref: str) -> str:
        for fragment, commit in heads.items():
            if fragment in repo:
                return commit
        raise Unreachable(f"no fake head for {repo} {ref}")

    return resolve


def _at_their_pins() -> LsRemote:
    """Every producer sitting exactly where the lockfile says it is."""
    return _remote(
        {
            "panelbench": _pinned(PANELBENCH, "commit"),
            "distribution-enclosure-simulator": _pinned(PANEL_SIM, "commit"),
            "specification": str(_lock()["synced_commit"]),
        }
    )


def _unreachable() -> LsRemote:
    def resolve(repo: str, ref: str) -> str:
        raise Unreachable("Name or service not known")

    return resolve


# ---------------------------------------------------------------------------
# The pins have one home
# ---------------------------------------------------------------------------


def test_the_script_states_no_pin_of_its_own() -> None:
    """No commit and no version literal in either module.

    The whole failure this check exists to catch is a pin that has two homes and
    stops agreeing with itself. A script that hardcoded either half would be that
    failure, in the tool meant to find it.
    """
    commit = re.compile(r"\b[0-9a-f]{40}\b")
    version = re.compile(r"\b\d+\.\d+\.\d+\b")
    for module in ("peer_drift.py", "_lock.py"):
        source = (_SCRIPTS / module).read_text(encoding="utf-8")
        assert not commit.findall(source), f"{module} names a commit; read it from spec_lock.json"
        assert not version.findall(source), f"{module} names a version; read it from spec_lock.json"


def test_every_producer_it_reports_is_pinned_where_it_says() -> None:
    lock = _lock()
    reported = {
        panelbench(lock, _refuses_http(), _at_their_pins()).pinned: _pinned(PANELBENCH, "commit"),
        emitter(lock, _http({"pypi.org": _pypi(_pinned(PANEL_SIM, "version"))}), _at_their_pins()).pinned: _pinned(
            PANEL_SIM, "version"
        ),
        specification(lock, _at_their_pins()).pinned: str(lock["synced_commit"]),
    }
    for observed, expected in reported.items():
        assert observed == expected


def test_every_pinned_producer_can_be_asked() -> None:
    """A peer added to the lockfile and not here would be silently exempt."""
    assert unasked(_lock()) == ()
    assert unvendored(_lock()) == ()


def test_a_token_reaches_github_and_nowhere_else(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GH_TOKEN", "not-a-real-token")
    assert "Authorization" in _headers("https://api.github.com/repos/o/n/compare/a...b")
    assert _headers("https://pypi.org/pypi/anything/json") == {}


# ---------------------------------------------------------------------------
# panelbench: a commit is only drift if it touched what we copy
# ---------------------------------------------------------------------------


def test_a_branch_at_the_pin_asks_github_nothing() -> None:
    """The ordinary day, and it costs one `ls-remote` and no API request."""
    found = panelbench(_lock(), _refuses_http(), _at_their_pins())
    assert found.verdict == "current"
    assert "the commit we pin" in found.detail


def test_commits_that_change_nothing_we_copy_stay_green() -> None:
    found = panelbench(
        _lock(),
        _http({"compare": _comparison(3, 0, ["README.md", "docs/index.md"])}),
        _remote({"panelbench": _HEAD}),
    )
    assert found.verdict == "current"
    assert found.observed == _HEAD
    assert "3 commits ahead, nothing we copy changed" in found.detail


def test_a_changed_capture_is_drift_and_says_where_to_copy_it() -> None:
    source = fixtures(_peer(PANELBENCH))["tree"]
    found = panelbench(
        _lock(),
        _http({"compare": _comparison(1, 0, [source, "README.md"])}),
        _remote({"panelbench": _HEAD}),
    )
    assert found.verdict == "drift"
    assert f"- `{source}`" in found.detail
    assert f"cp $PANELBENCH_DIR/{source}" in found.detail
    assert VENDORED["tree"] in found.detail
    assert VENDORED["wire"] not in found.detail


def test_a_changed_producer_lockfile_is_drift() -> None:
    found = panelbench(
        _lock(),
        _http({"compare": _comparison(1, 0, [EBUS_SPEC_FILE])}),
        _remote({"panelbench": _HEAD}),
    )
    assert found.verdict == "drift"
    assert "reading different vocabularies" in found.detail
    assert "cp $PANELBENCH_DIR" not in found.detail


def test_a_truncated_file_list_is_unknown_not_current() -> None:
    """300 files is the API's ceiling, not a statement that the 301st is safe."""
    found = panelbench(
        _lock(),
        _http({"compare": _comparison(400, 0, [f"src/file_{index}.py" for index in range(300)])}),
        _remote({"panelbench": _HEAD}),
    )
    assert found.verdict == "unknown"
    assert "all the API will list" in found.detail


def test_a_truncated_list_that_names_a_capture_is_still_drift() -> None:
    """The ceiling makes an absence unreliable, not a presence."""
    source = fixtures(_peer(PANELBENCH))["wire"]
    padding = [f"src/file_{index}.py" for index in range(299)]
    found = panelbench(
        _lock(),
        _http({"compare": _comparison(400, 0, [source, *padding])}),
        _remote({"panelbench": _HEAD}),
    )
    assert found.verdict == "drift"
    assert VENDORED["wire"] in found.detail


def test_a_pin_the_branch_does_not_contain_is_unknown() -> None:
    found = panelbench(
        _lock(),
        _http({"compare": _comparison(4, 2, [])}),
        _remote({"panelbench": _HEAD}),
    )
    assert found.verdict == "unknown"
    assert "not an ancestor" in found.detail
    assert f"peers.{PANELBENCH}.ref" in found.detail


def test_a_producer_that_cannot_be_reached_is_unknown() -> None:
    found = panelbench(_lock(), _refuses_http(), _unreachable())
    assert found.verdict == "unknown"
    assert found.observed == ""
    assert "not having asked is not the same fact" in found.detail.lower()


# ---------------------------------------------------------------------------
# the emitter: the release is the verdict, the branch is context
# ---------------------------------------------------------------------------


def test_a_newer_release_is_drift_and_names_the_capture_script() -> None:
    found = emitter(_lock(), _http({"pypi.org": _pypi("99.0.0")}), _at_their_pins())
    assert found.verdict == "drift"
    assert found.observed == "99.0.0"
    assert _pinned(PANEL_SIM, "capture_script") in found.detail
    produces = _peer(PANEL_SIM)["produces"]
    assert isinstance(produces, dict)
    assert str(produces["tree"]) in found.detail
    assert "at v99.0.0" in found.detail, "the tag prefix comes from the pinned tag, not from a guess"


def test_the_emitters_branch_moving_is_reported_and_not_a_verdict() -> None:
    found = emitter(
        _lock(),
        _http({"pypi.org": _pypi(_pinned(PANEL_SIM, "version"))}),
        _remote({"distribution-enclosure-simulator": _HEAD}),
    )
    assert found.verdict == "current"
    assert "the branch head has" in found.detail
    assert "not itself a reason to recapture" in found.detail


def test_pypi_being_unreachable_still_reports_the_branch() -> None:
    found = emitter(_lock(), _http({}), _at_their_pins())
    assert found.verdict == "unknown"
    assert "UNKNOWN" in found.detail
    assert "commits" in found.detail, "the context is still worth printing when the verdict is not"


# ---------------------------------------------------------------------------
# the specification: reported, never decisive
# ---------------------------------------------------------------------------


def test_the_specification_moving_decides_nothing() -> None:
    found = specification(_lock(), _remote({"specification": _HEAD}))
    assert found.advisory
    assert found.verdict == "drift"
    assert exit_code([found], strict=True) == 0


# ---------------------------------------------------------------------------
# exit codes
# ---------------------------------------------------------------------------


def _run(*, strict: bool, get: HttpGet, ls: LsRemote, peer: str | None = None) -> tuple[int, str]:
    out = io.StringIO()
    argv = ["--strict"] if strict else []
    if peer is not None:
        argv += ["--peer", peer]
    return main(argv, get=get, ls=ls, out=out), out.getvalue()


def _all_current() -> HttpGet:
    return _http({"pypi.org": _pypi(_pinned(PANEL_SIM, "version"))})


def test_a_run_where_nothing_moved_passes() -> None:
    code, report = _run(strict=True, get=_all_current(), ls=_at_their_pins())
    assert code == 0
    assert [line for line in report.splitlines() if line.startswith("## ")] == [
        f"## {PANELBENCH}",
        f"## {PANEL_SIM}",
        f"## {SPECIFICATION}",
    ]


def test_drift_fails_a_commit_and_a_ci_run_alike() -> None:
    drifted = _http({"pypi.org": _pypi("99.0.0")})
    assert _run(strict=False, get=drifted, ls=_at_their_pins(), peer=PANEL_SIM)[0] == 1
    assert _run(strict=True, get=drifted, ls=_at_their_pins(), peer=PANEL_SIM)[0] == 1


def test_unknown_passes_a_commit_and_fails_ci() -> None:
    """A laptop on a plane still commits; a CI run with no answer does not pass."""
    code, report = _run(strict=False, get=_http({}), ls=_unreachable())
    assert code == 0
    assert "UNKNOWN" in report

    assert _run(strict=True, get=_http({}), ls=_unreachable())[0] == 1


def test_peer_asks_one_producer() -> None:
    code, report = _run(strict=True, get=_refuses_http(), ls=_at_their_pins(), peer=PANELBENCH)
    assert code == 0
    assert report.count("## ") == 1
    assert report.startswith(f"## {PANELBENCH}")
