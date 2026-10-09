"""Tests expected to fail on this line, each with the reason it does.

Keyed by pytest node id without parameters or class (`<file>::<function>`), so
every parametrized case of a listed function is expected to fail. `conftest.py` marks
each one `xfail(strict=True)`: a listed test that starts passing fails the run
until its row is deleted, and a row naming anything but a test function of its
module stops the run.

`PENDING` rows assert behaviour a later change brings; that change deletes
them. `DIVERGENT` rows assert behaviour this library deliberately does not
have; they stay until the test or the decision changes. A strict xfail stops at
its first failing line, so a companion asserts what each `DIVERGENT` test checks
after its divergent line: `test_schema_one_acceptance_companions.py` for the
device-tree tests, and `test_register_rate_limit_companions.py` for
registration.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Final

_ACCEPTANCE = "tests/test_schema_one_other_models.py"
_RATE_LIMIT = "tests/test_register_rate_limit.py"

PENDING: Final[Mapping[str, str]] = {}

_DEVICE_TYPE_AS_PUBLISHED = "the library keeps device_type as published; a SOLAR feeds-role is carried by feeds_role"

_RETRY_AFTER_IN_SECONDS = (
    "the delay is SpanPanelRateLimitError.retry_after_s, in seconds, and an HTTP-date Retry-After "
    "is read as the seconds until that time"
)

DIVERGENT: Final[Mapping[str, str]] = {
    f"{_ACCEPTANCE}::test_a_circuit_whose_feeds_role_is_solar_is_labeled_pv": _DEVICE_TYPE_AS_PUBLISHED,
    f"{_ACCEPTANCE}::test_the_shipped_tree_has_the_other_model_shape": _DEVICE_TYPE_AS_PUBLISHED,
    f"{_ACCEPTANCE}::test_the_shipped_tree_reports_its_other_model_only_properties_as_discovered": (
        "the upstream lugs' connection/overcurrent-protection is read into upstream_protection_rating_a, "
        "so it is an addressed property rather than a discovered one"
    ),
    f"{_RATE_LIMIT}::test_raises_the_rate_limit_class_with_its_status": _RETRY_AFTER_IN_SECONDS,
    f"{_RATE_LIMIT}::test_retry_after_is_none_without_a_usable_header": _RETRY_AFTER_IN_SECONDS,
}

assert not PENDING.keys() & DIVERGENT.keys(), "a test is either pending or divergent, never both"

EXPECTED_FAILURES: Final[Mapping[str, str]] = {**PENDING, **DIVERGENT}
