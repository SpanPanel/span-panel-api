"""Give every device of a retained-topic tree a new id, keeping the tree intact.

Firmware may change device ids without changing the devices. A test that wants
to know whether something follows the device rather than its id renames a tree
with `rename_devices` and replays both.

Everything that names a device moves together: the tree's keys, a description's
`children`, `parent` and `root`, and the values of the properties that carry a
device id. An id the tree does not hold (a dangling reference) is left as
published.

The reference captures are masked upstream, with ids such as `circuit-1` and
site values such as `Masked Address`, so a test that looked for their originals
in an export would test nothing. `reidentify` plants identifying values for it
to look for instead: realistic, seeded, and every one of them invented.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
import json
import random
import string
from typing import Final

from .schema_one import RetainedTopicTree

_ID_PROPERTIES: Final = frozenset({"connection/feeds-device-id", "connection/fed-by-device-id", "grid/grid-forming-entity"})
_ID_LIST_SUFFIX: Final = "/shared-with-device-ids"

_TYPE_CIRCUIT: Final = "energy.ebus.device.circuit"
_TYPE_LUGS: Final = "energy.ebus.device.lugs"
_LUGS_SUFFIXES: Final[Mapping[str, str]] = {"UPSTREAM": "lugs-up", "DOWNSTREAM": "lugs-dn"}
_DIRECTION: Final = "info/direction"
_SERIAL: Final = "info/serial-number"
_DESCRIPTION: Final = "$description"
_NAME: Final = "info/name"
_ALNUM: Final = string.ascii_lowercase + string.digits
_SEEDED_SITE: Final[Mapping[str, str]] = {
    "address-lines": "4821 Seeded Lane",
    "locality": "Seededville",
    "region": "ZQ",
    "postal-code": "98000",
    "wifi-ssid": "seeded-network",
    "latitude": "47.6",
    "longitude": "-122.3",
    "utility-meter-serial-number": "SEEDED-METER-77",
}
"""The planted value of each masked site property, by property id on whichever node publishes it."""
_SEEDED_SITE_NAME: Final = "Seeded Residence"


@dataclass(frozen=True)
class Reidentified:
    """A re-identified tree, its root, and every identifying value planted in it."""

    tree: RetainedTopicTree
    root_id: str
    planted: frozenset[str]


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
    if topic == _DESCRIPTION:
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


def reidentify(tree: RetainedTopicTree, root_id: str, seed: int) -> Reidentified:
    """The tree with every device id, serial number and masked site value replaced by a seeded one.

    Ids take the shapes a panel publishes: a circuit a 32-hex id, the root
    `nt-<4 digits>-<5 alnum>`, lugs `<root>-lugs-up` / `-lugs-dn` by direction,
    every other device `<root>-<10 alnum>`. Each distinct `info/serial-number`
    becomes a distinct vendor-style serial, except a serial that is a device's
    id, as the panel's is: it becomes that device's new id, so the new root id
    is also the panel serial embedded in its children's ids. Every masked site
    property takes its value in `_SEEDED_SITE`, and the panel's `info/name` and
    `$description` `name` the site's name. A description `name` that is a
    device's id (as the captures' are) follows the id.

    Every reference moves with its device (`rename_devices`); a valued-empty
    `""` stays empty. A device with no `$description` (one that has not
    described itself) has no type to read, so it takes the `<root>-<10 alnum>`
    shape, and stays without one. `planted` holds every new id, serial and site value, and
    the same seed always gives the same tree.
    """
    rng = random.Random(seed)
    taken: set[str] = set()

    def alnum(length: int) -> str:
        return "".join(rng.choice(_ALNUM) for _ in range(length))

    def fresh(make: Callable[[], str]) -> str:
        value = make()
        while value in taken:
            value = make()
        taken.add(value)
        return value

    new_root = fresh(lambda: f"nt-{rng.randrange(10_000):04d}-{alnum(5)}")
    names = {root_id: new_root}
    for device_id in sorted(tree):
        if device_id == root_id:
            continue
        topics = tree[device_id]
        device_type = _description_object(topics[_DESCRIPTION]).get("type") if _DESCRIPTION in topics else None
        suffix = _LUGS_SUFFIXES.get(topics.get(_DIRECTION, "")) if device_type == _TYPE_LUGS else None
        if device_type == _TYPE_CIRCUIT:
            names[device_id] = fresh(lambda: f"{rng.getrandbits(128):032x}")
        elif suffix is not None and f"{new_root}-{suffix}" not in taken:
            names[device_id] = fresh(lambda: f"{new_root}-{suffix}")
        else:
            names[device_id] = fresh(lambda: f"{new_root}-{alnum(10)}")

    serials: dict[str, str] = {}
    for device_id in sorted(tree):
        serial = tree[device_id].get(_SERIAL, "")
        if serial and serial not in serials:
            serials[serial] = names.get(serial) or fresh(lambda: f"tg{rng.randrange(10**9):09d}{alnum(3)}")

    renamed, new_root = rename_devices(tree, root_id, names.__getitem__)
    planted = set(renamed)
    seeded: dict[str, dict[str, str]] = {}
    for device_id, topics in renamed.items():
        values = dict(topics)
        for topic, value in topics.items():
            property_id = topic.partition("/")[2]
            if not value or topic.startswith("$") or "/$" in topic:
                continue
            if topic == _SERIAL:
                values[topic] = serials[value]
            elif property_id in _SEEDED_SITE:
                values[topic] = _SEEDED_SITE[property_id]
            elif topic == _NAME and device_id == new_root:
                values[topic] = _SEEDED_SITE_NAME
            else:
                continue
            planted.add(values[topic])
        if _DESCRIPTION in topics:
            description = _description_object(topics[_DESCRIPTION])
            name = description.get("name")
            if device_id == new_root and isinstance(name, str) and name:
                description["name"] = _SEEDED_SITE_NAME
                planted.add(_SEEDED_SITE_NAME)
            elif isinstance(name, str) and name in names:
                description["name"] = names[name]
            values[_DESCRIPTION] = json.dumps(description)
        seeded[device_id] = values
    return Reidentified(seeded, new_root, frozenset(planted))


def _description_object(text: str) -> dict[str, object]:
    description: object = json.loads(text)
    if not isinstance(description, dict):
        raise TypeError("a $description is not a JSON object")
    return {str(key): value for key, value in description.items()}
