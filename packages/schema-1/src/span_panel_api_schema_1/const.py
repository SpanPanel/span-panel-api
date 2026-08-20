"""Wire vocabulary for the parent/child schema (data-model-version 1.x).

Every name here is a v1.0 device class, capability node, or property id. Nothing
in this module is shared with the flat schema: v1.0 moved each property from a
node on one device to a capability node on its own device, so even names that
look unchanged are addressed differently.
"""

from __future__ import annotations

# -- Device classes ---------------------------------------------------------

TYPE_PANEL = "energy.ebus.device.distribution-enclosure"
TYPE_CIRCUIT = "energy.ebus.device.circuit"
TYPE_BESS = "energy.ebus.device.bess"
TYPE_PV = "energy.ebus.device.pv"
TYPE_EVSE = "energy.ebus.device.evse"
TYPE_MID = "energy.ebus.device.mid"
# BESS model 0.14 decomposes a BESS into child roles; grid-forming belongs to the
# inverter, so "can this panel island" becomes "can any inverter here form a grid".
TYPE_INVERTER = "energy.ebus.device.inverter"
TYPE_LUGS = "energy.ebus.device.lugs"

# -- Capability nodes -------------------------------------------------------

NODE_BREAKER = "breaker"
NODE_CONNECTION = "connection"
NODE_DOOR = "door"
NODE_GRID = "grid"
NODE_INFO = "info"
NODE_LOAD_SHED = "load-shed"
NODE_METER = "meter"
NODE_PCS = "pcs"
NODE_POWER_FLOWS = "power-flows"
NODE_SHED = "shed"
# `energy.ebus.capability.shed-forecast` 0.1 -- the enclosure's backup-planning
# estimates. A separate node from `shed`, which carries the policy and the
# asserted islanding state: `shed` says what the panel will do, `shed-forecast`
# says when. Present only where the enclosure publishes it, so every consumer of
# these fields gates on the node rather than defaulting.
NODE_SHED_FORECAST = "shed-forecast"
NODE_GRID_FORMING = "grid-forming"
NODE_SOC = "soc"
NODE_STATUS = "status"
NODE_SWITCH = "switch"

# -- Properties -------------------------------------------------------------

PROP_ACTIVE_POWER = "active-power"
PROP_CURRENT = "current"
PROP_EXPORTED_ENERGY = "exported-energy"
PROP_IMPORTED_ENERGY = "imported-energy"
PROP_NAME = "name"
PROP_POLES = "poles"
PROP_PRIORITY = "priority"
PROP_RATING = "rating"
PROP_RELAY = "relay"
PROP_RELAY_CONTROLLABLE = "relay-controllable"
PROP_RELAY_REQUESTER = "relay-requester"
PROP_SPACES = "spaces"

# shed node
PROP_ASSERTED_ISLANDING_STATE = "asserted-islanding-state"

# pcs node. `energy.ebus.capability.pcs` 0.3 publishes two disjoint property
# sets under one node type: the enclosure runs the arbitration and publishes the
# *system* surface, while a circuit publishes only its *participation*. Same
# capability, different publishers — the same split `meter` makes between the
# panel, a circuit and a lugs device.
PROP_ENABLED = "enabled"
PROP_ACTIVE = "active"
PROP_IMPORT_LIMIT = "import-limit"
PROP_BINDING_CONSTRAINT = "binding-constraint"
PROP_MANAGED = "managed"

# The amps-native constraint classes the enclosure reconciles, in the catalog's
# order. Each publishes the same `{<source>-import-limit, -enablement, -active}`
# triplet, and the catalog is explicit that "the number and naming of sources is
# not fixed by this spec": a vendor may publish further sources using the same
# shape. A tuple of prefixes rather than twelve constants is what lets the
# reader below be written once per triplet member instead of once per source.
PCS_LIMIT_SOURCES: tuple[str, ...] = ("feed", "operator", "off-grid", "requested")
PCS_LIMIT_SUFFIX = "-import-limit"
PCS_ENABLEMENT_SUFFIX = "-enablement"
PCS_ACTIVE_SUFFIX = "-active"

# shed-forecast node. All four times are `integer` minutes; `confidence` is the
# enum LOW/MEDIUM/HIGH qualifying them. The `full-charge-*` pair answers the
# hypothetical "if the BESS were full", so it is a capability figure rather than
# a live countdown and moves only when the installation does.
PROP_TIME_TO_PRIORITY_SHED = "time-to-priority-shed"
PROP_TOTAL_TIME_REMAINING = "total-time-remaining"
PROP_FULL_CHARGE_TIME_TO_PRIORITY_SHED = "full-charge-time-to-priority-shed"
PROP_FULL_CHARGE_TOTAL_TIME_REMAINING = "full-charge-total-time-remaining"
PROP_CONFIDENCE = "confidence"
# `energy.ebus.capability.grid-forming` 0.1: "Static hardware capability: does this
# inverter support grid-forming operation at all?" -- the same *kind* of statement
# flat's `grid-islandable` made, and a MUST on the capability.
PROP_CAPABLE = "capable"
PROP_GRID_FORMING_ENTITY = "grid-forming-entity"

# Panel-level
PROP_DATA_MODEL_VERSION = "data-model-version"
PROP_FIRMWARE_VERSION = "firmware-version"
PROP_SERIAL_NUMBER = "serial-number"
PROP_STATE = "state"
PROP_VOLTAGE_A = "voltage-a"
PROP_VOLTAGE_B = "voltage-b"

# status node
PROP_CLOUD_CONNECTION = "cloud-connection"
PROP_ETHERNET = "ethernet"
PROP_WIFI = "wifi"
# `energy.ebus.capability.status` 0.1: the publisher's view of its own link to
# the device it represents (proxy) or to its backhaul (native). Enum
# OK/DEGRADED/LOST/UNKNOWN. Orthogonal to whether the eBus publisher is reporting
# to *its* consumers, and orthogonal to the enclosure's `connection/*` view of
# the same device -- see `devices.py`'s module docstring.
PROP_COMMUNICATION_STATE = "communication-state"

# -- Values -----------------------------------------------------------------

PRIORITY_NEVER = "NEVER"
UNKNOWN = "UNKNOWN"
CLOUD_CONNECTED = "CONNECTED"

PROP_MODEL = "model"

# Topic root. Children are peers of the panel in the topic tree rather than
# nodes beneath it, so a subscription covering the tree spans the domain.
HOMIE_DOMAIN = "ebus"
HOMIE_VERSION = "5"

STATE_READY = "ready"

# Breaker spaces per panel model.
#
# This is the only source of the panel's total size in v1.0. The flat schema
# carried it in the Homie schema's `space` format (`"1:32:1"`, max = 32); its
# successor `info/spaces` is a plain string with no format, and the panel device
# publishes no size property. What v1.0 does publish is `info/model`, a **closed
# enum** — the topic reference, the migration guide, and the panel's own Homie
# `$format` all list exactly these five values — so this is a lookup over a
# defined value set, not an inference from a vendor string.
#
# The *sizes* are ours: neither the SDK nor the published schema states how many
# spaces a model has, only which model names are valid. `panel_model_drift`
# exists because of that split — the panel can tell us a model we have no size
# for, and we would rather say so than guess.
#
# Total size matters beyond a display field: unoccupied positions are only
# knowable as `total - occupied`, and synthesising them is what gives the
# integration its unmapped-circuit sensors.
PANEL_SIZE_BY_MODEL: dict[str, int] = {
    "MAIN_16": 16,
    "MLO_24": 24,
    "MAIN_32": 32,
    "MAIN_40": 40,
    "MLO_48": 48,
}

# Prefix for synthesised unoccupied-position entries. Must match the flat
# adapter's, because the integration keys entities off it and a rename would
# strand every existing unmapped-tab entity.
UNMAPPED_TAB_PREFIX = "unmapped_tab_"

# The Homie attribute that carries what the flat schema published as the
# `never-backup` boolean. v1.0 retires the property and expresses it as
# mutability: a circuit commissioned never-backup has its priority locked, so
# the panel publishes `$settable = false` on `load-shed/priority`.
ATTR_SETTABLE = "settable"
