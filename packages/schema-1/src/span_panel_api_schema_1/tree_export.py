"""The masked device-tree export: the panel's whole tree, safe to share.

``export_masked_tree`` writes the tree in the ``tree-v1`` form of the reference
captures, so an attached export replays through the same loader, adapter and
fidelity harness they do. Every retained value is carried as the wire string
the panel published; a declared property that was never valued, or whose
value the panel has since cleared, has no key, so the three states of a
property (valued, valued empty, unvalued) stay apart.

A masked export replaces every device id with a placeholder that starts
``masked-``, so it can never equal a real id. The placeholder follows the
device rather than the shape of its id: SPAN treats ids as opaque and has said
they can change, so circuits are numbered by the breaker space they occupy and
lugs take their direction. Exports taken before and after such a change then
compare device by device.

Values are masked by their property id on any node: a serial, and the site's
address, location, postal code and network; the panel's ``info/name``, the
site's name, is masked too. Every value that names a
device is passed through the same placeholders, so a reference still resolves;
one that names no device in the tree becomes ``masked-missing-<n>`` and stays
visibly dangling.

Last, every string the panel published that the rules keep is swept for the
tree's original device ids, for its serial numbers that read as identifiers,
the utility meter's among them, and for every dangling id that does, which
reads as its own ``masked-missing-<n>`` wherever it appears: a property the
library has never seen, a device named by an id, an identifier inside a
``json`` value, as a string literal or a number, after a backslash or an
escape written out in plain text, through JSON nested in strings a few levels
deep; a string nested deeper than real values go is masked whole where it may
hide one, and a description subtree nested far deeper than real descriptions
go is masked whole where anything in it would be. The sweep reads only what
came off the wire, never the placeholders and masks the rules wrote, so it
cannot rewrite them.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
import json
import re
from types import MappingProxyType
from typing import TYPE_CHECKING, Final

from span_panel_api.models import MaskedTree, MaskedTreeDevice, UndescribedDevice
from span_panel_api_schema_1.const import DEVICE_TYPE_PREFIX, TYPE_CIRCUIT, TYPE_LUGS
from span_panel_api_schema_1.description import declares_class, device_type, nodes, properties

if TYPE_CHECKING:
    from ebus_sdk.homie import DiscoveredDevice

_PREFIX = "masked-"
_PANEL = f"{_PREFIX}panel"
_UNDESCRIBED = "undescribed"
_UNTYPED = "untyped"
_LUGS = "lugs"
_CIRCUIT = "circuit"
_DIRECTIONS = {"UPSTREAM": "upstream", "DOWNSTREAM": "downstream"}
_MISSING = "missing"
_ESCAPED_TYPE = "type-"
_RESERVED_SHORT_TYPES: Final = frozenset({_MISSING, _UNDESCRIBED})
"""Placeholder names that are not a device type; a type spelt like one is numbered as ``type-<word>``.

``masked-missing-<n>`` is a reference to no device in the tree and
``masked-undescribed-<n>`` a device that has not described itself. A described
device whose short type is one of those words becomes ``masked-type-missing-1``
or ``masked-type-undescribed-1``, so it can be read as neither.

That keeps every placeholder distinct by construction. Each numbered one is
``masked-<name>-<n>`` with one counter per name, and ``<n>``, the digits after
the last ``-``, cannot contain a ``-``, so two different names or numbers never
spell the same placeholder. A device whose short type really is
``type-missing`` shares that name and its counter, and the two differ by
number. The unnumbered ``masked-panel`` and ``masked-lugs-upstream`` /
``-downstream`` do not end in digits.
"""

_SCHEMA: Final = "tree-v1"
_NUMERIC_DATATYPES: Final = frozenset({"integer", "float"})

_SERIAL_NUMBER: Final = "serial-number"
_METER_SERIAL_NUMBER: Final = "utility-meter-serial-number"
_NAME: Final = "info/name"
_MASKED_PANEL_NAME: Final = "Masked Panel"
_SITE_MASKS: Final[Mapping[str, str]] = MappingProxyType(
    {
        _METER_SERIAL_NUMBER: "MASKED-METER",
        "wifi-ssid": "masked-ssid",
        "postal-code": "00000",
        "address-lines": "Masked Address",
        "locality": "Masked City",
        "region": "Masked Region",
        "latitude": "0.0",
        "longitude": "0.0",
        "time-zone": "UTC",
        "country-code": "ZZ",
    }
)
"""Site values replaced whole, by property id on whichever node publishes them.

The panel declares them under ``info`` and ``status``, but nothing in the
specification ties a site property to a node, which is why ``SITE_PROPERTIES``
(``const.py``) is read the same way. ``time-zone`` and ``country-code`` are masked
to ``UTC`` and ``ZZ``, valid values a consumer that parses the export still reads.
The serial number is matched by property id too; the site's name is not, since
``name`` is every device's label and only the panel's ``info/name`` is masked.
"""

_ID_PROPERTIES: Final = frozenset({"connection/feeds-device-id", "connection/fed-by-device-id"})
_GRID_FORMING_ENTITY: Final = "grid/grid-forming-entity"
_GRID: Final = "GRID"
_ID_LIST_PROPERTY: Final = "shared-with-device-ids"
_REFERENCE_KEYS: Final = ("parent", "root")
_CHILDREN: Final = "children"
_DESCRIPTION_NAME: Final = "name"

_DIGIT_RUNS = re.compile(r"([0-9]+)")
_MIN_EMBEDDED: Final = 6
"""An identifier shorter than this is swept only where it is a whole value, never inside one."""
_BOUNDARY_BEFORE: Final = r"(?:(?<![A-Za-z0-9])|(?<=\\[bfnrt])|(?<=\\u[0-9A-Fa-f]{4}))"
"""What may come before an identifier in plain text: no letter or digit, or a JSON-style escape written out.

The sweep decodes every string literal it finds, so a raw escape is met only
in text outside any literal. There ``\\n`` before an identifier would hide it
behind the escape's own letter; read as a boundary it hides nothing. The rule
only adds matches, and a match still begins on the identifier's own first
character, as it does after a lone backslash.
"""
_BOUNDARY_AFTER: Final = "(?![A-Za-z0-9])"
_DIGITS_BOUNDARY_BEFORE: Final = f"{_BOUNDARY_BEFORE}(?<![0-9]\\.)"
_DIGITS_BOUNDARY_AFTER: Final = f"{_BOUNDARY_AFTER}(?!\\.[0-9])"
"""The boundaries of an identifier made only of digits, which a decimal point joining it to another digit also fails.

A meter serial of ``123456`` lies inside the reading ``0.123456`` and the
fraction ``123456.5`` and is not found there; after ``meter 123456.`` it is.
"""
_JSON_STRING: Final = re.compile(r'("(?:[^"\\]|\\.)*")(\s*:)?|"(?:[^"\\]|\\.)*(\\?)\Z', re.DOTALL)
"""A JSON string literal in any text, and the colon after it when it is an object key.

Over text that parses as JSON it finds every string literal, since valid JSON
holds no quote outside them. Over any other text it finds what reads as one,
and the text between the finds is swept as plain text. The second alternative
matches a quote that never closes, with the rest of the text, so group 1 is
None there: a later quote cannot open a literal that closes under the same
pairing. Its content is the text after the quote less group 3, a lone
backslash at the end, which the body cannot hold. The scan is linear in the
text: from a quote the body consumes to the closing quote or the end, each at
most twice.
"""
_JSON_ESCAPE: Final = re.compile(r'\\(?:["\\/bfnrt]|u[0-9A-Fa-f]{4})|(\\)')
"""An escape JSON defines, or (group 1) a backslash that starts none and is read as a literal backslash."""
_NESTING_BOUND: Final = 4
"""How deep the sweep decodes JSON nested in strings.

Real values nest JSON in a string at most once or twice. Every level written
again doubles the escapes of the levels inside it, so the bound is tight.
"""
_DESCRIPTION_NESTING_BOUND: Final = 32
"""How deep the sweep reads a description's containers before deciding on a subtree whole.

A description's top-level values are depth 0 and real descriptions reach depth
3, a property's definition under ``nodes/<node>/properties``, whose fields are
scalars; a vendor's definition may nest a level or two deeper. Nothing in a
description is written again, so the bound guards the interpreter's stack
alone: ``parsed`` costs about three frames per level, and thirty-two levels
stay far inside the default limit.
"""
_NESTED_VALUE_MASK: Final = f"{_PREFIX}nested-value"
_DESCRIPTION_COPY_LIMIT: Final = 64
"""How deep a kept description subtree may reach: a consumer writes the export indented, which a deep one makes raise."""
_JSON_NUMBER: Final = re.compile(r"(-?[0-9]+(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?)")
"""A JSON number token, captured so ``split`` returns the tokens at the odd indices.

Between the string literals of text that parses as a JSON object or array there
are only numbers, ``true``, ``false``, ``null``, punctuation and whitespace, so
an identifier found there lies in a number.
"""
_DIGIT: Final = re.compile(r"[0-9]")

type NaturalKey = tuple[tuple[int, str | int], ...]
type Order = tuple[NaturalKey, str]


def natural_key(text: str) -> NaturalKey:
    """Sort key that compares runs of ASCII digits as integers and the rest case-folded."""
    return tuple(
        (1, int(part)) if part.isascii() and part.isdigit() else (0, part.casefold())
        for part in _DIGIT_RUNS.split(text)
        if part
    )


def _order(device_id: str) -> Order:
    """Total order on ids: natural order, with the raw id breaking a tie.

    Distinct ids can share a natural key (`g-01` and `g-1`, or ids differing only
    in case); without the tie-breaker their order is the input order, and the
    same tree would not always give the same document.
    """
    return (natural_key(device_id), device_id)


def placeholder_ids(root: DiscoveredDevice, descendants: Sequence[DiscoveredDevice]) -> dict[str, str]:
    """Every device id in the tree, mapped to its distinct ``masked-`` placeholder.

    The root is ``masked-panel``. Circuits are numbered by their lowest breaker
    space, then by natural id order for those that share a space or declare
    none (the remote CT). Circuits and lugs are recognised by class, so a
    subtyped one (`declares_class`) is placed exactly as its class is. Lugs
    take ``masked-lugs-upstream`` / ``-downstream``
    from ``info/direction``; a lugs device whose direction is absent, not one of
    the two, or already taken is numbered ``masked-lugs-<n>``. Any other
    described device is numbered per short type in natural id order, and a device
    that has not described itself, or whose description is not a JSON object,
    is ``masked-undescribed-<n>``.
    """
    ids: dict[str, str] = {root.device_id: _PANEL}
    counters: dict[str, int] = {}

    def numbered(name: str) -> str:
        counters[name] = counters.get(name, 0) + 1
        return f"{_PREFIX}{name}-{counters[name]}"

    ordered = sorted(descendants, key=lambda device: _order(device.device_id))
    circuits = [device for device in ordered if _is_described(device) and declares_class(device_type(device), TYPE_CIRCUIT)]
    for device in sorted(circuits, key=_circuit_order):
        ids[device.device_id] = numbered(_CIRCUIT)

    taken: set[str] = set()
    for device in ordered:
        if not _is_described(device) or not declares_class(device_type(device), TYPE_LUGS):
            continue
        direction = _DIRECTIONS.get(_text(device, "info", "direction"))
        if direction is None or direction in taken:
            ids[device.device_id] = numbered(_LUGS)
        else:
            taken.add(direction)
            ids[device.device_id] = f"{_PREFIX}{_LUGS}-{direction}"

    for device in ordered:
        if device.device_id in ids:
            continue
        ids[device.device_id] = numbered(_short_type(device) if _is_described(device) else _UNDESCRIBED)
    return ids


def _is_described(device: DiscoveredDevice) -> bool:
    """Whether the device has described itself with a ``$description`` that is a JSON object.

    The SDK stores whatever JSON the topic carried. A description that parsed
    to a list or a string declares nothing, so the device is treated as one
    that has not described itself, by the placeholders and the document alike.
    """
    description: object = device.description
    return isinstance(description, dict)


def _short_type(device: DiscoveredDevice) -> str:
    declared = device_type(device).removeprefix(DEVICE_TYPE_PREFIX) or _UNTYPED
    return f"{_ESCAPED_TYPE}{declared}" if declared in _RESERVED_SHORT_TYPES else declared


def _text(device: DiscoveredDevice, node: str, prop: str) -> str:
    value = device.properties.get(node, {}).get(prop)
    return "" if value is None else str(value).strip()


def _circuit_order(device: DiscoveredDevice) -> tuple[int, int, Order]:
    """Hosted circuits by lowest breaker space first, the rest after, ties by natural id."""
    spaces = [int(part) for part in re.findall(r"[0-9]+", _text(device, "info", "spaces"))]
    if not spaces:
        return (1, 0, _order(device.device_id))
    return (0, min(spaces), _order(device.device_id))


def export_masked_tree(
    root: DiscoveredDevice, descendants: Sequence[DiscoveredDevice], *, producer: str, tree_complete: bool
) -> MaskedTree:
    """The tree as a ``tree-v1`` document, with device ids, serials and site values masked.

    A described device goes under ``devices``: its ``$description`` with
    ``parent``, ``root`` and ``children`` mapped to placeholders, every declared
    value split by its declared ``datatype`` (``integer`` and ``float`` into
    ``numeric_properties``, the rest into ``properties``), every value its
    description does not declare in ``undeclared_properties``, its ``$target``
    values, and its ``$state`` when one arrived. A device its parent declared
    but that has not described itself, or whose description is not a JSON
    object, goes under ``undescribed_devices`` with its values unsplit, since
    nothing says what they are.

    The masks apply to a value by its property id on any node, wherever it is
    published, declared or not, and to targets as to values. A valued-empty
    ``""`` stays empty under every rule: it carries no identity, and replacing
    it would turn an empty reading into a value. Everything else the panel
    published is swept (``_Sweep``): the values and targets no rule replaces,
    their ``node/property`` keys, ``$state``, and every key, string and number
    of a description but the references and the panel's name the rules map.

    Devices are written in placeholder order and each device's values in key
    order, so the same tree always gives the same document. A dangling id is
    numbered ``masked-missing-<n>`` in the order it is first met: devices in
    placeholder order, and within one its values, then its targets, then its
    description's references; the sweep replaces the same id with the same
    placeholder wherever else the panel published it. The SDK's objects are
    only read; a description is copied before anything in it is replaced.
    """
    ids = placeholder_ids(root, descendants)
    in_order = sorted([root, *descendants], key=lambda device: _order(ids[device.device_id]))
    references = _References(ids)
    for device in in_order:
        references.register(device)
    masker = _Masker(references, root.device_id, _Sweep(_identifiers(ids, in_order, references.dangling())))
    devices: dict[str, MaskedTreeDevice] = {}
    undescribed: dict[str, UndescribedDevice] = {}
    for device in in_order:
        placeholder = ids[device.device_id]
        values = masker.values(device.device_id, _flattened(device.properties))
        targets = masker.values(device.device_id, _flattened(device.property_targets))
        state = None if device.state is None else masker.published(str(device.state))
        description: object = device.description
        if isinstance(description, dict):  # `_is_described`, inline so the description narrows
            devices[placeholder] = _described(masker.description(device.device_id, description), values, targets, state)
        else:
            undescribed[placeholder] = _undescribed(values, targets, state)
    return {
        "metadata": {"schema": _SCHEMA, "masked": True, "producer": producer, "tree_complete": tree_complete},
        "devices": devices,
        "undescribed_devices": undescribed,
    }


def _identifiers(
    ids: Mapping[str, str], in_order: Sequence[DiscoveredDevice], dangling: Sequence[tuple[str, str]]
) -> list[tuple[str, str]]:
    """Every device id, and every id-like dangling id and serial, with what the sweep replaces it by, highest priority first.

    An id is replaced by its device's placeholder and a serial by its device's
    masked serial, the placeholder in upper case. Ids come first, so on a panel
    whose serial is its id the text is read as the id; then the dangling ids
    that read as ids (``_is_id_like``), each with the ``masked-missing-<n>`` its
    reference was given, so such an id reads the same at a reference and
    anywhere else, even one spelt like a serial;
    then serials, so a serial that is also an id is read as that id. Within
    each, devices come in placeholder order, so a serial two devices publish (a
    MID and its BESS) is the first owner's wherever it is found. The utility
    meter's serial comes last and is replaced by its own mask,
    ``MASKED-METER``: it is a serial number, not free text, though it names the
    premises rather than a device. A serial that does not read as an id
    (``0``, ``N/A``, ``unknown``) is a placeholder the panel published for a
    serial it does not have: its own property is masked by the rule all the
    same, but it is not swept, since every equal value would go with it.
    """
    identifiers = [(device.device_id, ids[device.device_id]) for device in in_order]
    identifiers.extend((device_id, missing) for device_id, missing in dangling if _is_id_like(device_id))
    for device in in_order:
        identifiers.extend(
            (serial, ids[device.device_id].upper()) for serial in _serials(device, _SERIAL_NUMBER) if _is_id_like(serial)
        )
    meter_mask = _SITE_MASKS[_METER_SERIAL_NUMBER]
    for device in in_order:
        identifiers.extend((serial, meter_mask) for serial in _serials(device, _METER_SERIAL_NUMBER) if _is_id_like(serial))
    return identifiers


def _is_id_like(value: str) -> bool:
    """Whether a dangling reference or a serial reads as an identifier, not a word: six characters or more, with a digit.

    A reference names no device for one of two reasons: the device is absent,
    or the panel published a word there (``UNKNOWN``, ``NONE``, ``BACKUP``).
    Such a word is mapped where it is published like any dangling id, but it is
    also a reading and an enum member, so it is not swept anywhere else. A
    serial of ``0`` or ``N/A`` stands in for one the panel does not have, and
    is likewise masked where it is published and swept nowhere else.
    """
    return len(value) >= _MIN_EMBEDDED and _DIGIT.search(value) is not None


def _serials(device: DiscoveredDevice, property_id: str) -> list[str]:
    """The values a device publishes for ``property_id``, on any node, as values or targets, without surrounding space."""
    serials: list[str] = []
    for published in (_flattened(device.properties), _flattened(device.property_targets)):
        serials.extend(published[topic].strip() for topic in sorted(published) if topic.partition("/")[2] == property_id)
    return [serial for serial in serials if serial]


class _Sweep:
    """Replaces every device id, and every id-like dangling id and serial number, found in text the panel published.

    The matching rules keep it from rewriting unrelated text. Only identifiers
    are swept: device ids, dangling references that read as ids, and serial
    numbers, the utility meter's included. Any other site value is free text or
    a short code, so a region of ``OK`` swept as a whole would rewrite every
    status of ``OK``, and the property rules mask those alone.

    An identifier matches case-insensitively and only where no ASCII letter or
    digit adjoins it, so ``circuit-1`` is not found inside ``circuit-12``; any
    other character, a ``-`` or a backslash among them, is a boundary, and so
    is a JSON-style escape written out in plain text (``\\n``, ``\\u00b0``),
    whose letter or last hex digit would otherwise adjoin the identifier. A
    ``-`` must be one, because device ids are serials and suffixes joined by
    ``-``, and a serial embedded in an id the tree does not hold must still be
    found. For an identifier made only of digits a decimal point that joins it
    to another digit is no boundary either, so a meter serial is not found
    inside a decimal reading. Identifiers are tried longest first, so
    ``<serial>-lugs-up`` maps to the lugs' placeholder rather than the panel's
    with a suffix. One shorter than six characters is matched only as a whole
    string, never inside a longer string or in a key, where ``pv`` is also a
    property id. A serial is swept only where it reads as an identifier
    (``_is_id_like``); its own property is masked by rule whatever it says.

    Every string is swept the same way (``value``), whatever it was declared
    as. Each JSON string literal in it is decoded, since in the raw text an
    escape's own letter would adjoin an identifier or spell part of one
    (``\\u006e``), and its content is swept as a string of its own by this same
    rule, so JSON carried inside a string is reached; a literal followed by
    ``:`` is an object key and is swept as a key. A quote that never closes
    opens a literal all the same, whose content is the rest of the text less a
    lone backslash at the end; it is written again without a closing quote.
    The literal is decoded leniently, a raw control character allowed and a
    backslash that starts no JSON escape read as a backslash, so that a valid
    escape beside such a flaw cannot hide an identifier. Only a literal that
    changed is written again, with standard escapes, and escaped where UTF-8
    cannot carry a character of it. The text between the literals, and a
    literal that still does not decode, is swept as plain text. A number equal
    to an identifier is replaced there like any other text, and where the whole
    text parses as a JSON object or array the number token is written as a
    JSON string, so the value still parses. Every other byte stays as
    published.

    The decoding stops at ``_NESTING_BOUND``: every level written again doubles
    the escapes of the levels inside it and costs a stack frame, so unbounded
    nesting is exponential output and a ``RecursionError``. A literal decoded
    at the bound is not swept inside; it is replaced whole by
    ``masked-nested-value`` if its content holds an identifier under the plain
    rule, or a quote, which may open a literal the sweep will not read, and is
    kept as published otherwise. A description's containers (``parsed``) are
    already decoded and are written again nowhere, so they are read by
    recursion to the looser ``_DESCRIPTION_NESTING_BOUND`` and a container at
    it is walked with an explicit stack instead: it is replaced whole by
    ``masked-nested-value`` if any key, string or number in it would be swept
    by the rules above the bound, and copied as published otherwise.

    Where identifiers that differ only in case match, the one spelt as found
    wins, then the one of higher priority (``_identifiers``).
    """

    def __init__(self, identifiers: Sequence[tuple[str, str]]) -> None:
        self._replacements: dict[str, str] = {}
        for identifier, replacement in identifiers:
            if identifier:
                self._replacements.setdefault(identifier, replacement)
        embedded = sorted((ident for ident in self._replacements if len(ident) >= _MIN_EMBEDDED), key=len, reverse=True)
        whole = [ident for ident in self._replacements if len(ident) < _MIN_EMBEDDED]
        self._embedded = _alternation(
            ([ident for ident in embedded if not _is_digits(ident)], _BOUNDARY_BEFORE, _BOUNDARY_AFTER),
            ([ident for ident in embedded if _is_digits(ident)], _DIGITS_BOUNDARY_BEFORE, _DIGITS_BOUNDARY_AFTER),
        )
        self._whole = _alternation((whole, "", ""))

    def value(self, text: str) -> str:
        """A published string with every identifier in it replaced, a short one only where it is the whole string."""
        return self._swept(text, whole=True)

    def key(self, text: str) -> str:
        """A key with every identifier of six characters or more in it replaced."""
        return self._swept(text, whole=False)

    def topic(self, topic: str) -> str:
        """A ``node/property`` key, swept as its node and property ids are in the description that declares them."""
        node_id, separator, property_id = topic.partition("/")
        return f"{self.key(node_id)}{separator}{self.key(property_id)}"

    def parsed(self, value: object, depth: int = 0) -> object:
        """A copy of parsed JSON (a description): each key swept as a key, each string as a string, each number as its text.

        ``depth`` is how many containers enclose the value below the
        description's own keys. A container at ``_DESCRIPTION_NESTING_BOUND``
        is not walked by recursion: ``_bounded`` decides for it whole, on the
        same leaf rules, with a stack of its own.
        """
        if isinstance(value, str):
            return self.value(value)
        if isinstance(value, int | float) and not isinstance(value, bool):
            return self._number(value)
        if isinstance(value, dict | list) and depth >= _DESCRIPTION_NESTING_BOUND:
            return self._bounded(value, depth)
        if isinstance(value, dict):
            return _first_per_key((self.key(str(key)), self.parsed(item, depth + 1)) for key, item in value.items())
        if isinstance(value, list):
            return [self.parsed(item, depth + 1) for item in value]
        return value

    def _number(self, value: int | float) -> object:
        """A number whose text holds an identifier, as that text swept; any other number is itself.

        A serial published as a JSON number in a description (``45123987``) is
        the text of one published in a ``json`` value and reads the same mask,
        as a string; a sign stays, inside the string.
        """
        text = str(value)
        swept = self._swept_flat(text, whole=True)
        return value if swept == text else swept

    def _bounded(self, value: dict[object, object] | list[object], depth: int) -> object:
        """A container at the description bound: a copy, or ``masked-nested-value`` if it would change or reaches too deep.

        The subtree is walked with a stack of its own, not the interpreter's,
        so a description nested to any depth costs no stack frames. Each key
        is held to the key rule and each string and number leaf to the rule
        ``parsed`` applies above the bound; a boolean or null is never an
        identifier. If any of them would change, the container is masked
        whole, because nothing below the bound is swept in place, and so is
        one whose containers reach past ``_DESCRIPTION_COPY_LIMIT`` from the
        description's root, which the export must not carry; otherwise the
        copy keeps the document from sharing an object with the SDK's tree.
        """
        copied: dict[object, object] | list[object] = {} if isinstance(value, dict) else []
        pending = [(value, copied, depth)]
        while pending:
            source, target, at = pending.pop()
            pairs: Iterable[tuple[object, object]] = source.items() if isinstance(source, dict) else enumerate(source)
            for key, item in pairs:
                if isinstance(key, str) and self.key(key) != key:
                    return _NESTED_VALUE_MASK
                leaf: object = item
                if isinstance(item, dict | list):
                    if at + 1 > _DESCRIPTION_COPY_LIMIT:
                        return _NESTED_VALUE_MASK
                    child: dict[object, object] | list[object] = {} if isinstance(item, dict) else []
                    pending.append((item, child, at + 1))
                    leaf = child
                elif self._leaf_changes(item):
                    return _NESTED_VALUE_MASK
                if isinstance(target, dict):
                    target[key] = leaf
                else:
                    target.append(leaf)
        return copied

    def _leaf_changes(self, leaf: object) -> bool:
        """Whether ``parsed`` would replace a scalar: a string the sweep changes, or a number whose text holds one."""
        if isinstance(leaf, str):
            return self.value(leaf) != leaf
        if isinstance(leaf, int | float) and not isinstance(leaf, bool):
            return isinstance(self._number(leaf), str)
        return False

    def _swept(self, text: str, *, whole: bool, depth: int = 0) -> str:
        """The text with its string literals swept each as a string and the text between them as plain text.

        ``whole`` allows a short identifier to match the entire text. ``depth``
        is how many literals the text was decoded from: the wire string is 0.
        The plain text is swept again with its numbers written as strings when
        an identifier was found in it and the text parses as a JSON object or
        array: a number is then in a value position, and a bare word there
        would stop the value parsing. A quote that never closes ends the plain
        text; it and the rest are one unclosed literal.
        """
        if whole and self._whole is not None and self._whole.fullmatch(text):
            return self._replacement(text)
        literals: list[re.Match[str]] = []
        spans: list[str] = []
        position = 0
        unclosed: re.Match[str] | None = None
        for match in _JSON_STRING.finditer(text):
            if match.group(1) is None:
                unclosed = match
                break
            literals.append(match)
            spans.append(text[position : match.start()])
            position = match.end()
        spans.append(text[position : len(text) if unclosed is None else unclosed.start()])
        swept = [self._plain(span) for span in spans]
        if swept != spans and _is_json_container(text):
            swept = [self._numbers_quoted(span) for span in spans]
        pieces = [
            piece for span, match in zip(swept[:-1], literals, strict=True) for piece in (span, self._literal(match, depth))
        ]
        tail = "" if unclosed is None else self._unclosed(unclosed, depth)
        return "".join([*pieces, swept[-1], tail])

    def _plain(self, text: str) -> str:
        """Plain text with every identifier of six characters or more in it replaced."""
        if self._embedded is None:
            return text
        return self._embedded.sub(lambda match: self._replacement(match.group()), text)

    def _numbers_quoted(self, span: str) -> str:
        """Plain text from a JSON object or array, swept, with a number that held an identifier written as a JSON string."""
        parts = _JSON_NUMBER.split(span)
        for index, part in enumerate(parts):
            swept = self._plain(part)
            parts[index] = json.dumps(swept) if index % 2 and swept != part else swept
        return "".join(parts)

    def _literal(self, match: re.Match[str], depth: int) -> str:
        """One string literal, its content swept as a string (as a key before a colon), written again only if it changed.

        A literal that does not decode is swept as the plain text it is.
        """
        literal, colon = match.group(1), match.group(2)
        swept = self._literal_swept(literal, whole=colon is None, depth=depth)
        return self._plain(match.group()) if swept is None else swept + (colon or "")

    def _unclosed(self, match: re.Match[str], depth: int) -> str:
        """A quote that never closes, with the rest of the text: swept as a literal, written again without a closing quote.

        The content is the text after the quote less a lone backslash at the
        end (``_JSON_STRING`` group 3), which stays as it was; swept as plain
        text, an escape's letter in it would adjoin an identifier. Content
        that does not decode is swept as plain text, and content that did not
        change keeps its bytes.
        """
        text, trailing = match.group(), match.group(3)
        swept = self._literal_swept(f'"{text[1 : len(text) - len(trailing)]}"', whole=True, depth=depth)
        return self._plain(text) if swept is None else f'"{swept[1:-1]}{trailing}'

    def _literal_swept(self, literal: str, *, whole: bool, depth: int) -> str | None:
        """A string literal with its content swept as a string: itself where nothing changed, None where it does not decode.

        The content is decoded leniently (``_JSON_ESCAPE``, ``strict=False``).
        Content decoded at ``_NESTING_BOUND`` is not swept inside: the literal
        becomes ``masked-nested-value`` where the content may hide an
        identifier, and is kept otherwise.
        """
        try:
            decoded: object = json.loads(_JSON_ESCAPE.sub(_defined_escape, literal), strict=False)
        except ValueError:
            return None
        if not isinstance(decoded, str):
            return literal
        if depth + 1 >= _NESTING_BOUND:
            return _dumped(_NESTED_VALUE_MASK) if self._hides_an_identifier(decoded, whole=whole) else literal
        swept = self._swept(decoded, whole=whole, depth=depth + 1)
        return literal if swept == decoded else _dumped(swept)

    def _hides_an_identifier(self, text: str, *, whole: bool) -> bool:
        """Whether text the sweep will not read inside may hold an identifier.

        It holds one under the plain rule (``_swept_flat``), or a quote, which
        may open a literal the sweep will not read.
        """
        return '"' in text or self._swept_flat(text, whole=whole) != text

    def _swept_flat(self, text: str, *, whole: bool) -> str:
        """The text swept by the plain rule alone, a short identifier only as the whole of it; no literal is read."""
        if whole and self._whole is not None and self._whole.fullmatch(text):
            return self._replacement(text)
        return self._plain(text)

    def _replacement(self, found: str) -> str:
        exact = self._replacements.get(found)
        if exact is not None:
            return exact
        return next(
            replacement
            for identifier, replacement in self._replacements.items()
            if re.fullmatch(re.escape(identifier), found, re.IGNORECASE)
        )


def _defined_escape(match: re.Match[str]) -> str:
    """A ``_JSON_ESCAPE`` match as JSON defines it: an escape as written, a stray backslash doubled."""
    return "\\\\" if match.group(1) else match.group()


def _is_json_container(text: str) -> bool:
    """Whether a published value parses as a JSON object or array, whatever its declared datatype.

    It decides only whether a swept number is written as a JSON string
    (``_Sweep``). JSON text that opens with ``{`` or ``[`` is an object or array
    if it parses at all, so a scalar (``"bess"``, ``12``) never gets past the
    first check: a bare number equal to an identifier is a whole value and is
    replaced as text. Nesting too deep to parse is read as not parsing, so the
    export stays total.
    """
    if not text.lstrip().startswith(("{", "[")):
        return False
    try:
        json.loads(text)
    except ValueError, RecursionError:
        return False
    return True


def _dumped(text: str) -> str:
    """A string as a JSON literal, its characters written as they are, or escaped where UTF-8 cannot carry one.

    A lone surrogate, which the escape ``\\ud800`` decodes to, has no UTF-8
    encoding and would stop the export being written; it goes back as an escape.
    """
    literal = json.dumps(text, ensure_ascii=False)
    try:
        literal.encode("utf-8")
    except UnicodeEncodeError:
        return json.dumps(text)
    return literal


def _is_digits(identifier: str) -> bool:
    """Whether an identifier is made only of ASCII digits, so a decimal point may join it to a number."""
    return identifier.isascii() and identifier.isdigit()


def _alternation(*groups: tuple[Sequence[str], str, str]) -> re.Pattern[str] | None:
    """One case-insensitive pattern trying each group's identifiers in turn between its two boundaries; None for none.

    No identifiers gives None, never an empty pattern, which would match at
    every position. Each group's boundaries are shared, outside its
    alternation: an identifier that fails the boundary after it still
    backtracks into the next, so the matches are those of a boundary around
    each, and the boundary before is tested once per position rather than once
    per identifier. The groups are tried in order at each position, which
    costs no longer match: identifiers from two groups can both match at one
    position only where one is a prefix of the other, so a group whose
    identifiers may be prefixes of another's comes after it.
    """
    sources = [
        f"{before}(?:{'|'.join(re.escape(ident) for ident in identifiers)}){after}"
        for identifiers, before, after in groups
        if identifiers
    ]
    if not sources:
        return None
    return re.compile("|".join(sources), re.IGNORECASE)


def _first_per_key[T](pairs: Iterable[tuple[str, T]]) -> dict[str, T]:
    """Pairs as a map; where sweeping made two keys one, the first in order is kept."""
    merged: dict[str, T] = {}
    for key, item in pairs:
        merged.setdefault(key, item)
    return merged


def _named_ids(topic: str, value: str) -> list[str] | None:
    """The ids a value names where its property carries device ids; None where it names none.

    ``connection/feeds-device-id`` and ``fed-by-device-id`` name one,
    ``*/shared-with-device-ids`` a comma-separated list with each element
    trimmed, and the MID's ``grid/grid-forming-entity`` one unless it is the grid
    itself, read as the adapter reads it.
    """
    if topic in _ID_PROPERTIES:
        return [value]
    if topic == _GRID_FORMING_ENTITY:
        return None if value.strip().upper() == _GRID else [value]
    if topic.partition("/")[2] == _ID_LIST_PROPERTY:
        return [part.strip() for part in value.split(",") if part.strip()]
    return None


def _description_ids(description: Mapping[object, object]) -> list[str]:
    """The ids a description names: its ``parent``, its ``root``, then each of its ``children``."""
    named = [reference for key in _REFERENCE_KEYS if isinstance(reference := description.get(key), str)]
    children = description.get(_CHILDREN)
    if isinstance(children, list):
        named.extend(child for child in children if isinstance(child, str))
    return named


class _References:
    """Device ids mapped to placeholders, and each dangling id to its own ``masked-missing-<n>``."""

    def __init__(self, ids: Mapping[str, str]) -> None:
        self._ids = ids
        self._missing: dict[str, str] = {}

    def placeholder(self, device_id: str) -> str:
        """The placeholder of a device in the tree."""
        return self._ids[device_id]

    def map(self, device_id: str) -> str:
        """The placeholder of the device an id names, or the dangling id's own ``masked-missing-<n>``."""
        placeholder = self._ids.get(device_id)
        if placeholder is not None:
            return placeholder
        if device_id not in self._missing:
            self._missing[device_id] = f"{_PREFIX}{_MISSING}-{len(self._missing) + 1}"
        return self._missing[device_id]

    def register(self, device: DiscoveredDevice) -> None:
        """Number every dangling id a device names, in the order the masking meets them.

        Its values, then its targets, each in key order, then its description's
        references; a valued-empty ``""`` names nothing. Registering every
        device in placeholder order before any is masked numbers each dangling
        id as the masking would, so the sweep can replace it everywhere else.
        """
        for published in (_flattened(device.properties), _flattened(device.property_targets)):
            for topic in sorted(published):
                named = _named_ids(topic, published[topic]) if published[topic] else None
                for device_id in named or ():
                    self.map(device_id)
        description: object = device.description
        if isinstance(description, dict):
            for device_id in _description_ids(description):
                self.map(device_id)

    def dangling(self) -> list[tuple[str, str]]:
        """Each dangling id met so far, with its ``masked-missing-<n>``, in the order met."""
        return list(self._missing.items())


class _Masker:
    """The masking rules and the sweep, sharing one map of references."""

    def __init__(self, references: _References, panel_id: str, sweep: _Sweep) -> None:
        self._references = references
        self._panel_id = panel_id
        self._sweep = sweep

    def published(self, text: str) -> str:
        """A string the panel published and no rule replaces, swept."""
        return self._sweep.value(text)

    def values(self, owner: str, values: Mapping[str, str]) -> dict[str, str]:
        """One device's ``node/property`` values with every rule applied, keys swept, in key order.

        The rules read each value under its published key, in that key's order,
        so dangling ids are numbered the same whatever the sweep makes of a key.
        """
        masked = _first_per_key(
            (self._sweep.topic(topic), self.value(owner, topic, values[topic])) for topic in sorted(values)
        )
        return {topic: masked[topic] for topic in sorted(masked)}

    def value(self, owner: str, topic: str, value: str) -> str:
        if value == "":
            return value
        masked = self._fixed_mask(owner, topic)
        return self._referenced(topic, value) if masked is None else masked

    def _fixed_mask(self, owner: str, topic: str) -> str | None:
        """The replacement for an identifying or site value, or None where the name carries neither."""
        if topic == _NAME and owner == self._panel_id:
            return _MASKED_PANEL_NAME
        property_id = topic.partition("/")[2]
        if property_id == _SERIAL_NUMBER:
            return self._references.placeholder(owner).upper()
        return _SITE_MASKS.get(property_id)

    def _referenced(self, topic: str, value: str) -> str:
        """The value with every device id it names mapped; a value that names none, swept."""
        named = _named_ids(topic, value)
        if named is None:
            return self.published(value)
        return ",".join(self._references.map(device_id) for device_id in named)

    def description(self, owner: str, description: Mapping[object, object]) -> dict[str, object]:
        """A copy of a ``$description`` with its device references mapped, the panel's name masked, the rest swept.

        The references and the name are decided from the description as
        published, before the sweep, and are not swept themselves.
        """
        replaced: dict[str, object] = {}
        for key in _REFERENCE_KEYS:
            reference = description.get(key)
            if isinstance(reference, str):
                replaced[key] = self._references.map(reference)
        children = description.get(_CHILDREN)
        if isinstance(children, list):
            replaced[_CHILDREN] = [
                self._references.map(child) if isinstance(child, str) else self._sweep.parsed(child) for child in children
            ]
        if owner == self._panel_id and _DESCRIPTION_NAME in description:
            replaced[_DESCRIPTION_NAME] = _MASKED_PANEL_NAME
        return _first_per_key(
            (name, replaced[name]) if name in replaced else (self._sweep.key(name), self._sweep.parsed(value))
            for name, value in ((str(key), value) for key, value in description.items())
        )


def _described(
    description: dict[str, object], values: dict[str, str], targets: dict[str, str], state: str | None
) -> MaskedTreeDevice:
    numeric = {
        f"{node_id}/{property_id}": _is_numeric(definition)
        for node_id, node in nodes(description).items()
        for property_id, definition in properties(node).items()
    }
    entry: MaskedTreeDevice = {
        "description": description,
        "properties": {},
        "numeric_properties": {},
        "targets": targets,
        "undeclared_properties": {},
    }
    for topic, value in values.items():
        if topic not in numeric:
            entry["undeclared_properties"][topic] = value
        elif numeric[topic]:
            entry["numeric_properties"][topic] = value
        else:
            entry["properties"][topic] = value
    if state is not None:
        entry["state"] = state
    return entry


def _is_numeric(definition: Mapping[str, object]) -> bool:
    """Whether a property's declared ``datatype`` puts its value in ``numeric_properties``."""
    datatype = definition.get("datatype")
    return isinstance(datatype, str) and datatype in _NUMERIC_DATATYPES


def _undescribed(values: dict[str, str], targets: dict[str, str], state: str | None) -> UndescribedDevice:
    entry: UndescribedDevice = {"values": values, "targets": targets}
    if state is not None:
        entry["state"] = state
    return entry


def _flattened(by_node: object) -> dict[str, str]:
    """The SDK's ``{node: {property: value}}`` map, keyed ``node/property``."""
    flat: dict[str, str] = {}
    if not isinstance(by_node, dict):
        return flat
    for node_id, by_property in by_node.items():
        if isinstance(by_property, dict):
            for property_id, value in by_property.items():
                flat[f"{node_id}/{property_id}"] = str(value)
    return flat
