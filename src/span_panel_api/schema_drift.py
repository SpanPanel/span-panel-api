"""Diagnostic logging for Homie schema drift between panel sessions.

Schema-agnostic: operates purely on ``HomieSchemaTypes`` dicts (a mapping of
node type to property definitions) and has no dependency on flat-schema
(schema_0) internals. Lives at the bootstrap level so ``span_panel_api.mqtt``
can call it without importing anything from an adapter distribution.
"""

from __future__ import annotations

import logging

from span_panel_api.models import HomieSchemaTypes

_LOGGER = logging.getLogger(__name__)


def log_schema_drift(
    previous: HomieSchemaTypes,
    current: HomieSchemaTypes,
) -> None:
    """Log property-level differences between two schema versions.

    Called by the client when the schema hash changes between connections.
    All Homie-specific detail stays in this module — the integration never
    sees this output, only the transport-agnostic field metadata.
    """
    prev_types = set(previous.keys())
    curr_types = set(current.keys())

    for node_type in sorted(curr_types - prev_types):
        _LOGGER.debug("Schema drift: new node type '%s'", node_type)

    for node_type in sorted(prev_types - curr_types):
        _LOGGER.debug("Schema drift: removed node type '%s'", node_type)

    for node_type in sorted(prev_types & curr_types):
        prev_props = previous[node_type]
        curr_props = current[node_type]
        if not isinstance(prev_props, dict) or not isinstance(curr_props, dict):
            continue

        for prop_id in sorted(set(curr_props) - set(prev_props)):
            _LOGGER.debug("Schema drift: new property '%s/%s'", node_type, prop_id)

        for prop_id in sorted(set(prev_props) - set(curr_props)):
            _LOGGER.debug("Schema drift: removed property '%s/%s'", node_type, prop_id)

        for prop_id in sorted(set(prev_props) & set(curr_props)):
            prev_def = prev_props[prop_id]
            curr_def = curr_props[prop_id]
            if not isinstance(prev_def, dict) or not isinstance(curr_def, dict):
                continue
            for attr in ("datatype", "unit", "format"):
                old_val = prev_def.get(attr)
                new_val = curr_def.get(attr)
                if old_val != new_val:
                    _LOGGER.debug(
                        "Schema drift: '%s/%s' %s changed: '%s' → '%s'",
                        node_type,
                        prop_id,
                        attr,
                        old_val,
                        new_val,
                    )
