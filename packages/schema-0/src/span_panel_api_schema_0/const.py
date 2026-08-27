"""Constants for the flat-schema (Homie v5) parsing implementation."""

# ---------------------------------------------------------------------------
# Provenance anchor — the schema revision every fact in this module was read
# from. `tests/test_schema_provenance.py` fails when a captured schema reports a
# different one, which is the only pre-release signal that this adapter has
# drifted from the wire it claims to parse.
#
# The field name is per-adapter, not per-bootstrap: flat firmware publishes
# `typesSchemaHash` over a `types` block, while parent/child renames it to
# `deviceClassesSchemaHash` over `deviceClasses` — the hash is renamed with the
# block it covers, so schema_1 declares its own.
#
# Content-derived, not build-derived: SPAN defines it as the SHA-256 of the
# canonicalized schema object and states the schema "may remain unchanged across
# multiple firmware releases". So it moves when the schema moves, not on every
# release — which is what makes it usable as an anchor rather than noise.
# ---------------------------------------------------------------------------
SCHEMA_ANCHOR_FIELD = "typesSchemaHash"
SCHEMA_ANCHOR = "sha256:d347556a07d98f40"
SCHEMA_ANCHOR_FIRMWARE = "spanos2/r202603/05"

# Homie v5 topic structure
HOMIE_VERSION = 5
HOMIE_DOMAIN = "ebus"
TOPIC_PREFIX = f"{HOMIE_DOMAIN}/{HOMIE_VERSION}"

# Topic patterns (serial_number substituted at runtime).
# The adapter subscribes with the wildcard and publishes with the set pattern;
# per-topic read formats are not needed because every message arrives through
# the one wildcard subscription.
PROPERTY_SET_TOPIC_FMT = f"{TOPIC_PREFIX}/{{serial}}/{{node}}/{{prop}}/set"
WILDCARD_TOPIC_FMT = f"{TOPIC_PREFIX}/{{serial}}/#"

# ---------------------------------------------------------------------------
# Homie type strings.
#
# Two namespaces that are easy to conflate and are NOT the same set:
#
#   * the `types` block of GET /api/v2/homie/schema, which declares the
#     properties, units and datatypes available to a type; and
#   * the `type` string a node actually carries in its $description on the wire.
#
# Every constant below is a node type observed on the wire. The ones in the
# first group are also declared in the schema, so metadata lookup finds them
# directly. See tests/test_schema_provenance.py, which asserts that.
# ---------------------------------------------------------------------------
TYPE_CORE = "energy.ebus.device.distribution-enclosure.core"
TYPE_LUGS = "energy.ebus.device.lugs"
TYPE_CIRCUIT = "energy.ebus.device.circuit"
TYPE_BESS = "energy.ebus.device.bess"
TYPE_PV = "energy.ebus.device.pv"
TYPE_EVSE = "energy.ebus.device.evse"
TYPE_POWER_FLOWS = "energy.ebus.device.power-flows"

# Wire-only subtypes: real node types published by real firmware (confirmed
# against a live panel in 1eef0dc), but NOT declared in the schema's `types`
# block, which carries only the base `energy.ebus.device.lugs`. Firmware uses
# one convention or the other — typed nodes, or generic nodes plus a
# `direction` property — and _find_lugs_node handles both.
#
# Because the schema does not declare them, every one of these needs an entry
# in field_metadata._LUGS_FALLBACK mapping it to a declared type, or property
# metadata silently comes back empty for those nodes. The provenance test
# asserts that pairing rather than trusting it.
TYPE_LUGS_UPSTREAM = "energy.ebus.device.lugs.upstream"
TYPE_LUGS_DOWNSTREAM = "energy.ebus.device.lugs.downstream"

# Lugs direction values
LUGS_UPSTREAM = "UPSTREAM"
LUGS_DOWNSTREAM = "DOWNSTREAM"


def normalize_circuit_id(node_id: str) -> str:
    """Strip dashes from Homie UUID for entity stability."""
    return node_id.replace("-", "")
