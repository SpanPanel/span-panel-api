"""The parent/child schema's reference payload, and the replay that reads it.

`parent_child_tree.json` is a retained-topic capture: only the parser that
speaks its vocabulary can interpret it, and the eBus SDK that turns it back into
devices is `span-panel-api-schema-1`'s dependency alone. Importing this module
therefore reaches the SDK; importing `bootstrap` does not.

`devices_from_tree` stays beside the capture for the reason it was written: a
tree is not directly usable, every consumer of it has to replay the retained
topics through `DiscoveredDevice` first, and separating the two would put the
same twelve lines in each of the test modules that read it.

Read by path rather than through `importlib.resources`: this is a file in a test
tree now, not package data, and saying so in the loader is part of the point.
"""

from __future__ import annotations

from collections.abc import Mapping
import json
from pathlib import Path

from ebus_sdk.homie import DiscoveredDevice

type RetainedTopicTree = Mapping[str, Mapping[str, str]]
"""A retained-topic capture: device id -> topic -> payload, all strings.

`$description` is a JSON *string*, not a nested object — it is stored on the
wire exactly as the panel publishes it, and `update_description` parses it.
"""

_PARENT_CHILD_TREE = Path(__file__).parent / "parent_child_tree.json"

_DEFAULT_STATE = "ready"
_DOMAIN = "ebus"


def parent_child_tree() -> RetainedTopicTree:
    """The captured retained topics of a full 40-space panel.

    Fourteen devices: the panel, both lugs, a BESS with its MID, a PV, an EVSE
    and the circuits — enough that a test can check what each device class does
    and does not declare, including the absences.
    """
    tree: object = json.loads(_PARENT_CHILD_TREE.read_text(encoding="utf-8"))
    if not isinstance(tree, dict):
        raise TypeError(f"{_PARENT_CHILD_TREE.name} is not a JSON object")
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

    Takes the tree rather than reading it, so a caller can filter the capture
    first — dropping the BESS to model a panel that has none, say — and still
    build devices the same way.
    """
    return [device_from_topics(device_id, topics) for device_id, topics in tree.items()]
