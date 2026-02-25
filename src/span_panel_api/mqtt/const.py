"""Constants for SPAN Panel MQTT/Homie transport."""

# Homie v5 topic structure
HOMIE_VERSION = 5
HOMIE_DOMAIN = "ebus"
TOPIC_PREFIX = f"{HOMIE_DOMAIN}/{HOMIE_VERSION}"

# Topic patterns (serial_number substituted at runtime)
DEVICE_TOPIC_FMT = f"{TOPIC_PREFIX}/{{serial}}"
STATE_TOPIC_FMT = f"{TOPIC_PREFIX}/{{serial}}/$state"
DESCRIPTION_TOPIC_FMT = f"{TOPIC_PREFIX}/{{serial}}/$description"
PROPERTY_TOPIC_FMT = f"{TOPIC_PREFIX}/{{serial}}/{{node}}/{{prop}}"
PROPERTY_SET_TOPIC_FMT = f"{TOPIC_PREFIX}/{{serial}}/{{node}}/{{prop}}/set"
WILDCARD_TOPIC_FMT = f"{TOPIC_PREFIX}/{{serial}}/#"

# Homie device states
HOMIE_STATE_INIT = "init"
HOMIE_STATE_READY = "ready"
HOMIE_STATE_DISCONNECTED = "disconnected"
HOMIE_STATE_SLEEPING = "sleeping"
HOMIE_STATE_LOST = "lost"
HOMIE_STATE_ALERT = "alert"

# Homie type strings from schema
TYPE_CORE = "energy.ebus.device.distribution-enclosure.core"
TYPE_LUGS = "energy.ebus.device.lugs"
TYPE_CIRCUIT = "energy.ebus.device.circuit"
TYPE_BESS = "energy.ebus.device.bess"
TYPE_PV = "energy.ebus.device.pv"
TYPE_EVSE = "energy.ebus.device.evse"
TYPE_PCS = "energy.ebus.device.pcs"
TYPE_POWER_FLOWS = "energy.ebus.device.power-flows"

# MQTT connection defaults
MQTT_DEFAULT_MQTTS_PORT = 8883
MQTT_DEFAULT_WS_PORT = 9001
MQTT_DEFAULT_WSS_PORT = 9002
MQTT_KEEPALIVE_S = 60

# Connection/ready timeouts
MQTT_CONNECT_TIMEOUT_S = 15.0
MQTT_READY_TIMEOUT_S = 30.0

# Reconnect backoff (reuses strategy from REST retry constants)
MQTT_RECONNECT_MIN_DELAY_S = 1.0
MQTT_RECONNECT_MAX_DELAY_S = 60.0
MQTT_RECONNECT_BACKOFF_MULTIPLIER = 2

# Lugs direction values
LUGS_UPSTREAM = "UPSTREAM"
LUGS_DOWNSTREAM = "DOWNSTREAM"


def normalize_circuit_id(node_id: str) -> str:
    """Strip dashes from Homie UUID for entity stability."""
    return node_id.replace("-", "")


def denormalize_circuit_id(circuit_id: str) -> str:
    """Restore dashes to a 32-char dashless UUID (8-4-4-4-12 format)."""
    if len(circuit_id) == 32 and "-" not in circuit_id:
        return f"{circuit_id[:8]}-{circuit_id[8:12]}-{circuit_id[12:16]}-{circuit_id[16:20]}-{circuit_id[20:]}"
    return circuit_id
