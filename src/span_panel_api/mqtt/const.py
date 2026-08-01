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
