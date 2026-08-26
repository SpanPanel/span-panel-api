"""MQTT transport configuration models."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from .const import MQTT_DEFAULT_MQTTS_PORT, MQTT_DEFAULT_WS_PORT, MQTT_DEFAULT_WSS_PORT

MqttTransport = Literal["tcp", "websockets"]


@dataclass(frozen=True, slots=True)
class MqttClientConfig:
    """MQTT broker connection parameters from v2 auth response.

    ``ca_pem`` is the trust anchor for the broker connection, and supplying it is
    what makes that connection pinned. With it set, ``AsyncMqttBridge`` builds
    its SSL context from this value and makes no CA request on any path --
    neither at connect nor at rebuild.

    Leaving it ``None`` keeps 3.0.1's behaviour: the bridge fetches the CA from
    ``GET /api/v2/certificate/ca`` on every connect and every rebuild,
    unauthenticated and over plaintext HTTP, and trusts whatever comes back. It
    is permitted because requiring a pin would break every existing install on
    upgrade, and it logs a warning once per bridge because it is a real downgrade
    from what this field offers, not a neutral default.

    Separate from ``create_span_client``'s ``ssl_context``, which anchors the
    *REST* calls, because the two secure different transports. They are the same
    panel CA; a caller holding the PEM supplies both from it.
    """

    broker_host: str
    username: str
    password: str
    mqtts_port: int = MQTT_DEFAULT_MQTTS_PORT
    ws_port: int = MQTT_DEFAULT_WS_PORT
    wss_port: int = MQTT_DEFAULT_WSS_PORT
    transport: MqttTransport = "tcp"
    use_tls: bool = True
    # Last, so the positional signature every existing caller uses is unchanged,
    # and beside `use_tls` because it is the other half of the same decision.
    ca_pem: str | None = None

    @property
    def effective_port(self) -> int:
        """Return the port for the configured transport/TLS combination."""
        if self.transport == "tcp":
            return self.mqtts_port
        return self.wss_port if self.use_tls else self.ws_port
