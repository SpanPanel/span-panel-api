"""Constants for SPAN Panel MQTT/Homie transport."""

# Homie device states
HOMIE_STATE_INIT = "init"
HOMIE_STATE_READY = "ready"
HOMIE_STATE_DISCONNECTED = "disconnected"
HOMIE_STATE_SLEEPING = "sleeping"
HOMIE_STATE_LOST = "lost"
HOMIE_STATE_ALERT = "alert"

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

# Every this many consecutive reconnect failures (any reason), rebuild the paho client from scratch
# and re-fetch the panel CA. Mirrors the recovery effect of a manual integration reload without
# going through HA's config_entry teardown. Resets after every rebuild attempt so the cadence holds
# throughout extended outages.
MQTT_FULL_REBUILD_AFTER_FAILURES = 3

# How long the relaxed diagnostic handshake in `_diagnose_leaf_name` may take,
# connection and TLS together. Short because it runs on a reconnect attempt that
# has already failed and is about to be retried anyway: the answer is worth
# having, and worth nothing if it arrives after the next attempt. A panel that
# cannot answer inside this is a panel mid-reboot, which the caller already
# treats as transient.
MQTT_LEAF_PROBE_TIMEOUT_S = 5.0
