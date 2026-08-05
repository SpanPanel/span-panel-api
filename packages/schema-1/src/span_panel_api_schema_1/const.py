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

# -- Values -----------------------------------------------------------------

PRIORITY_NEVER = "NEVER"
UNKNOWN = "UNKNOWN"
CLOUD_CONNECTED = "CONNECTED"

PROP_MODEL = "model"

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
