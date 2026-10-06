"""The enclosure firmware version, read down to the release build number."""

from __future__ import annotations

import pytest

from span_panel_api_schema_1.firmware import release_build


@pytest.mark.parametrize(
    ("firmware_version", "expected"),
    [
        ("spanos2/r202639/03", 202639),
        ("spanos2/r202639/01", 202639),
        ("spanos2/r202633/01", 202633),
        ("spanos2/r202603/05", 202603),
        # The respin suffix is never compared, however large it is.
        ("spanos2/r202633/39", 202633),
        ("spanos2/r202633/99", 202633),
        # No suffix, no prefix, surrounding whitespace.
        ("spanos2/r202639", 202639),
        ("r202639", 202639),
        ("  spanos2/r202639/03\n", 202639),
        # The first release segment wins.
        ("spanos2/r202633/r202639", 202633),
    ],
)
def test_release_build_reads_the_six_digit_release_segment(firmware_version: str, expected: int) -> None:
    assert release_build(firmware_version) == expected


@pytest.mark.parametrize(
    "firmware_version",
    [
        "",
        "example/v0.1.0",
        "spanos2/202639/03",
        "spanos2/r20263/03",
        "spanos2/r2026390/03",
        "spanos2/R202639/03",
        "spanos2/rc202639/03",
        "spanos2/r202639x/03",
        "spanos2-r202639-03",
        "spanos2/r 202639/03",
    ],
)
def test_release_build_rejects_strings_without_a_release_segment(firmware_version: str) -> None:
    assert release_build(firmware_version) is None


def test_release_build_of_an_unpublished_version_is_none() -> None:
    assert release_build(None) is None
