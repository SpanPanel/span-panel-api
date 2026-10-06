"""Read the release build number out of the enclosure's firmware version string.

SPAN firmware publishes ``info/firmware-version`` as slash-separated segments,
for example ``spanos2/r202639/03``. The ``rNNNNNN`` segment names the release and
is the only part a behavior gate may compare: the trailing ``/03`` is a respin of
that release, and two respins of one release publish the same wire conventions.

Kept to one function so every gate in this package parses the string the same
way, and so a string this package does not recognize yields `None` rather than a
guess. A caller decides what `None` means for its own question.
"""

from __future__ import annotations

import re

_RELEASE_SEGMENT = re.compile(r"r(\d{6})")


def release_build(firmware_version: str | None) -> int | None:
    """The six-digit release build number, or `None` when there is none to read.

    ``"spanos2/r202639/03"`` gives ``202639``. Only a whole path segment of the
    form ``r`` plus exactly six digits counts, so ``"r2026390"``, ``"rc1"`` and a
    simulator's ``"example/v0.1.0"`` all give `None`. The first such segment wins;
    the respin suffix after it is never read.
    """
    if firmware_version is None:
        return None
    for segment in firmware_version.strip().split("/"):
        match = _RELEASE_SEGMENT.fullmatch(segment)
        if match is not None:
            return int(match.group(1))
    return None
