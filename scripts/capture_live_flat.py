"""Capture the retained tree from a live SPAN panel running flat firmware.

Produces `tests/fixtures/live_flat_wire.json`, which is **gitignored**. That file
carries the panel's serial (which is also its MQTT username), the household's
circuit names and real consumption, so it stays on the machine that took it. What
gets committed is the verdict of `tests/test_live_flat_differential.py`, never the
capture.

Reads credentials from `.env` (see `.env.example`):

    LIVE_PANEL_HOST  LIVE_PANEL_PORT  LIVE_PANEL_USERNAME  LIVE_PANEL_PASSWORD

Run:

    uv run python scripts/capture_live_flat.py

Why it exists: the flat side of the migration classification is the frozen
simulator, a proxy for firmware. This measures the proxy. Where the panel and the
simulator agree, the simulator is attested; where they differ, the panel is
ground truth and the simulator is wrong.

TLS with verification off, matching how the panel is reached in practice — it
presents a self-signed certificate.
"""

import json
import os
import pathlib
import ssl
import sys
import threading
import time

import paho.mqtt.client as mqtt

_REPO = pathlib.Path(__file__).resolve().parent.parent
OUT = pathlib.Path(sys.argv[1]) if len(sys.argv) > 1 else _REPO / "tests" / "fixtures" / "live_flat_wire.json"

# Stop when nothing new has arrived for this long. A retained store replays in a
# burst on subscribe, so silence is the signal that the burst is over.
QUIET_SECONDS = 5.0
MAX_SECONDS = 60.0


def _load_dotenv() -> None:
    path = _REPO / ".env"
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def main() -> int:
    _load_dotenv()

    host = os.environ.get("LIVE_PANEL_HOST", "")
    port = int(os.environ.get("LIVE_PANEL_PORT") or 8883)
    username = os.environ.get("LIVE_PANEL_USERNAME", "")
    password = os.environ.get("LIVE_PANEL_PASSWORD", "")

    missing = [
        name
        for name, value in (
            ("LIVE_PANEL_HOST", host),
            ("LIVE_PANEL_USERNAME", username),
            ("LIVE_PANEL_PASSWORD", password),
        )
        if not value
    ]
    if missing:
        print(f"missing in .env: {', '.join(missing)} — see .env.example")
        return 2

    retained: dict[str, str] = {}
    last_message = [time.monotonic()]
    connected = threading.Event()
    failed: list[str] = []

    def on_connect(client: mqtt.Client, _u: object, _f: object, reason: object, _p: object = None) -> None:
        code = getattr(reason, "value", reason)
        if code != 0:
            failed.append(f"connect refused: {reason}")
            connected.set()
            return
        # The panel publishes its whole tree under its own serial.
        client.subscribe(f"ebus/5/{username}/#", qos=1)
        connected.set()

    def on_message(_c: object, _u: object, message: mqtt.MQTTMessage) -> None:
        if message.retain:
            retained[message.topic] = message.payload.decode(errors="replace")
            last_message[0] = time.monotonic()

    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="span-flat-capture")
    client.username_pw_set(username, password)
    client.tls_set(cert_reqs=ssl.CERT_NONE)
    client.tls_insecure_set(True)
    client.on_connect = on_connect
    client.on_message = on_message

    print(f"connecting to {host}:{port} …")
    client.connect(host, port, keepalive=30)
    client.loop_start()

    if not connected.wait(timeout=20):
        client.loop_stop()
        print("timed out waiting for CONNACK")
        return 1
    if failed:
        client.loop_stop()
        print(failed[0])
        return 1

    started = time.monotonic()
    while time.monotonic() - started < MAX_SECONDS:
        if retained and time.monotonic() - last_message[0] > QUIET_SECONDS:
            break
        time.sleep(0.25)

    client.loop_stop()
    client.disconnect()

    if not retained:
        print("connected but received no retained messages; is the topic prefix right?")
        return 1

    devices: dict[str, dict[str, str]] = {}
    for topic, payload in sorted(retained.items()):
        parts = topic.split("/")
        if len(parts) < 4:
            continue
        devices.setdefault(parts[2], {})["/".join(parts[3:])] = payload

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(devices, indent=2, sort_keys=True) + "\n")

    topics = sum(len(v) for v in devices.values())
    print(f"devices: {len(devices)}   topics: {topics}")
    print(f"written to {OUT} (gitignored)")
    return 0


raise SystemExit(main())
