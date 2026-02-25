"""MQTT transport configuration models."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from .const import MQTT_DEFAULT_MQTTS_PORT, MQTT_DEFAULT_WS_PORT, MQTT_DEFAULT_WSS_PORT

MqttTransport = Literal["tcp", "websockets"]


@dataclass(frozen=True, slots=True)
class MqttClientConfig:
    """MQTT broker connection parameters from v2 auth response.

    CA certificate is not stored here — AsyncMqttBridge fetches it
    fresh from GET /api/v2/certificate/ca on every connect/reconnect.
    """

    broker_host: str
    username: str
    password: str
    mqtts_port: int = MQTT_DEFAULT_MQTTS_PORT
    ws_port: int = MQTT_DEFAULT_WS_PORT
    wss_port: int = MQTT_DEFAULT_WSS_PORT
    transport: MqttTransport = "tcp"
    use_tls: bool = True

    @property
    def effective_port(self) -> int:
        """Return the port for the configured transport/TLS combination."""
        if self.transport == "tcp":
            return self.mqtts_port
        return self.wss_port if self.use_tls else self.ws_port
