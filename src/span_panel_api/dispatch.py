"""Tier 1 dispatch: a panel's data-model-version selects the adapter major.

Separate from ``adapters.py`` because they answer different questions.
``adapters.py`` knows *what is installed*; this module knows *what this panel
needs*. Keeping them apart is also what lets both the factory and the transport
dispatch without importing each other.
"""

from __future__ import annotations

import logging
import re

from .adapters import DEFAULT_ADAPTER_KEY
from .exceptions import SpanPanelSchemaVersionError

_LOGGER = logging.getLogger(__name__)

# The canonical form the published spec defines: MAJOR.MINOR[.PATCH].
_DMV_CANONICAL = re.compile(r"^(\d+)\.\d+(?:\.\d+)?$")
# Tolerant form: a leading integer major, optionally followed by a separator and
# anything at all. Accepts '1', '1.0.3-rc2', '1_0'; rejects 'v1.0', '', 'x'.
_DMV_MAJOR = re.compile(r"^(\d+)(?:[._-].*)?$")


def select_adapter_key(data_model_version: str | None) -> tuple[str, str]:
    """Return the adapter key this panel needs, and why.

    Absence is the flat-schema signal — the property was introduced by the same
    firmware that introduced the parent/child model, so a panel that does not
    publish it is speaking the flat schema. SPAN confirmed this holds over REST
    as well as MQTT, which is what makes dispatch possible before the broker is
    opened.

    Presence is never read as flat. Falling back to schema_0 for a value we do
    not recognise would hand a parent/child panel to the flat parser, which does
    not fail — it produces plausible but wrong power and energy figures. A wrong
    number in Home Assistant is worse than an error, so anything present and
    unreadable raises instead.

    Between those two poles sits a value whose major is unambiguous even though
    its full form is not canonical ('1', '1.0-beta'). That is not a guess: the
    major is what selects the adapter, and it was read, not assumed. Those
    dispatch normally and log the deviation, so a firmware that starts emitting
    a new format is visible before it is an outage.

    Note this is the opposite of the rule for enum *properties*, where the spec
    requires consumers not to raise on an unrecognised value. The difference is
    blast radius: an unknown enum value affects one property, while an unknown
    schema version means every value in the tree may be misread.

    Raises:
        SpanPanelSchemaVersionError: A version is present but no major can be
            extracted from it.
    """
    if data_model_version is None:
        return DEFAULT_ADAPTER_KEY, "data-model-version absent (flat schema)"

    if (match := _DMV_CANONICAL.match(data_model_version)) is not None:
        return f"schema_{int(match.group(1))}", f"data-model-version={data_model_version!r}"

    if (match := _DMV_MAJOR.match(data_model_version)) is not None:
        _LOGGER.warning(
            "data-model-version=%r is not the canonical MAJOR.MINOR[.PATCH] form; "
            "dispatching on major %s. Please report this value.",
            data_model_version,
            match.group(1),
        )
        return (
            f"schema_{int(match.group(1))}",
            f"data-model-version={data_model_version!r} (non-canonical; major only)",
        )

    raise SpanPanelSchemaVersionError(data_model_version)
