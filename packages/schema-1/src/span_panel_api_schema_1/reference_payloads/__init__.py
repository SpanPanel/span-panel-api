"""Reference wire payloads for the parent/child schema, shipped as package data.

The counterpart to `span_panel_api.reference_payloads`, and here rather than
there for the reason that decides every placement in this workspace: a retained
topic tree is only interpretable by the parser that speaks its vocabulary, and
the eBus SDK that turns it back into devices is this distribution's dependency
alone. The bootstrap ships the document it fetches; this ships the tree it
cannot read.

`devices_from_tree` is exported alongside the capture because a tree is not
directly usable — every consumer of it has to replay the retained topics
through `DiscoveredDevice` first, and that replay is the parser's own knowledge
of how the transport feeds it. Shipping the capture without the replay just
moves a copy of this module's logic into every consumer, which is the burden
the package data exists to remove.
"""

from __future__ import annotations

from collections.abc import Mapping
from importlib import resources
import json
from typing import TypeAlias

from ebus_sdk.homie import DiscoveredDevice

RetainedTopicTree: TypeAlias = Mapping[str, Mapping[str, str]]
"""A retained-topic capture: device id -> topic -> payload, all strings.

`$description` is a JSON *string*, not a nested object — it is stored on the
wire exactly as the panel publishes it, and `update_description` parses it.
"""

_PACKAGE = "span_panel_api_schema_1.reference_payloads"
_PARENT_CHILD_TREE = "parent_child_tree.json"

_DEFAULT_STATE = "ready"
_DOMAIN = "ebus"


def parent_child_tree() -> RetainedTopicTree:
    """The captured retained topics of a full 40-space panel.

    Thirteen devices: the panel, both lugs, a BESS with its MID, a PV, an EVSE
    and the circuits — enough that a consumer can check what each device class
    does and does not declare, including the absences.
    """
    text = resources.files(_PACKAGE).joinpath(_PARENT_CHILD_TREE).read_text(encoding="utf-8")
    tree: object = json.loads(text)
    if not isinstance(tree, dict):
        raise TypeError(f"{_PARENT_CHILD_TREE} is not a JSON object")
    return tree


def device_from_topics(device_id: str, topics: Mapping[str, str]) -> DiscoveredDevice:
    """Rebuild one discovered device from its retained topics.

    The same sequence the transport performs on a broker replay: describe,
    state, then every non-`$` topic as a `node/property` value. A device with no
    `$state` retained is treated as ready, which is what the transport assumes
    for a device that described itself.
    """
    device = DiscoveredDevice(device_id, _DOMAIN)
    device.update_description(topics["$description"])
    device.update_state(topics.get("$state", _DEFAULT_STATE))
    for topic, value in topics.items():
        if topic.startswith("$"):
            continue
        node, _, prop = topic.partition("/")
        if prop:
            device.update_property(node, prop, value)
    return device


def devices_from_tree(tree: RetainedTopicTree) -> list[DiscoveredDevice]:
    """Rebuild every device in a capture.

    Takes the tree rather than reading it, so a consumer can filter the capture
    first — dropping the BESS to model a panel that has none, say — and still
    build devices the same way.
    """
    return [device_from_topics(device_id, topics) for device_id, topics in tree.items()]
