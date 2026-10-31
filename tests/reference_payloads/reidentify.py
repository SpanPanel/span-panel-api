"""Give every device of a retained-topic tree a new id, keeping the tree intact.

Firmware may change device ids without changing the devices. A test that wants
to know whether something follows the device rather than its id renames a tree
with `rename_devices` and replays both.

Everything that names a device moves together: the tree's keys, a description's
`children`, `parent` and `root`, and the values of the properties that carry a
device id. An id the tree does not hold (a dangling reference) is left as
published.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
import json
from typing import Final

from .schema_one import RetainedTopicTree

_ID_PROPERTIES: Final = frozenset({"connection/feeds-device-id", "connection/fed-by-device-id", "grid/grid-forming-entity"})
_ID_LIST_SUFFIX: Final = "/shared-with-device-ids"


def rename_devices(tree: RetainedTopicTree, root_id: str, new_id: Callable[[str], str]) -> tuple[RetainedTopicTree, str]:
    """The tree with every device renamed by `new_id`, and the new root id."""
    names = {device_id: new_id(device_id) for device_id in tree}
    if len(set(names.values())) != len(names):
        raise ValueError("new_id gave two devices the same id")
    renamed = {
        names[device_id]: {topic: _value(topic, value, names) for topic, value in topics.items()}
        for device_id, topics in tree.items()
    }
    return renamed, names[root_id]


def _value(topic: str, value: str, names: Mapping[str, str]) -> str:
    if topic == "$description":
        return _description(value, names)
    if topic in _ID_PROPERTIES:
        return names.get(value, value)
    if topic.endswith(_ID_LIST_SUFFIX):
        return ",".join(names.get(part.strip(), part.strip()) for part in value.split(",") if part.strip())
    return value


def _description(text: str, names: Mapping[str, str]) -> str:
    description: object = json.loads(text)
    if not isinstance(description, dict):
        raise TypeError("a $description is not a JSON object")
    for key in ("parent", "root"):
        reference = description.get(key)
        if isinstance(reference, str):
            description[key] = names.get(reference, reference)
    children = description.get("children")
    if isinstance(children, list):
        description["children"] = [names.get(child, child) if isinstance(child, str) else child for child in children]
    return json.dumps(description)
