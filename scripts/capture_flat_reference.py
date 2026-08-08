"""Capture the flat simulator's retained surface, without a broker.

Produces `tests/fixtures/flat_wire.json`, the schema_0 side of the Phase 3
classification.

Run it from the **simulator's** environment, not this one — it imports the flat
emitter, whose `aiomqtt` dependency this repo does not carry:

    cd ../simulator
    uv run python ../span-panel-api/scripts/capture_flat_reference.py \\
        ../span-panel-api/tests/fixtures/flat_wire.json

`SIMULATOR_DIR` overrides where the checkout is looked for; it defaults to a
`simulator` directory beside this repo.

Substitutes the transport rather than reassembling the emitter: `_AiomqttPublisher`
is swapped for a recorder and `start_clone` then runs its ordinary path — real
manifest builder, real BESS and load-shedding config, real Emitter, real
`start()`. Only the socket is different, which is the point of the capture.
Reassembling instead would prove less than it appears to, because a capture taken
through different wiring than a real panel uses is a capture of the wiring.

The `mqttPublishFail` warnings this prints are the SDK's own redundant publish
path finding no paho client. Harmless: the lifecycle publishes `$state` and
`$description` through the injected transport, and both land in the capture. The
run asserts that rather than trusting it.

The flat simulator is frozen at `v1.0.15 — the locked flat schema release`, so the
output is stable and vendored rather than re-taken. Re-run only if that changes.

**Shape-stable, not byte-stable.** `noise_factor` and an advancing clock move 53
of the 559 topics on every run; the device set and the topic set do not move at
all. That is enough, because the classification this feeds compares which fields
are *populated*, never their values — and it is the same property the
parent/child capture has, for the same reason.
"""

import asyncio
import json
import os
import pathlib
import sys

_REPO = pathlib.Path(__file__).resolve().parent.parent
SIM = pathlib.Path(os.environ.get("SIMULATOR_DIR", _REPO.parent / "simulator"))
if not (SIM / "src").is_dir():
    raise SystemExit(f"no simulator checkout at {SIM}; set SIMULATOR_DIR")
sys.path.insert(0, str(SIM / "src"))

from span_panel_simulator.emitter_adapter import runtime as flat_runtime  # noqa: E402
from span_panel_simulator.engine import DynamicSimulationEngine  # noqa: E402

CONFIG = SIM / "configs" / "default_MAIN_40.yaml"
OUT = pathlib.Path(sys.argv[1]) if len(sys.argv) > 1 else pathlib.Path("flat_capture.json")


class RecordingPublisher:
    """Satisfies the emitter's duck-typed MQTT interface, keeping last-wins state.

    Last-wins because that is what a broker's retained store holds, and therefore
    what a consumer replays on connect.
    """

    def __init__(self, **kwargs: object) -> None:
        self.retained: dict[str, bytes] = {}
        self._kwargs = kwargs
        LAST.append(self)

    async def connect(self) -> None:
        return None

    async def disconnect(self) -> None:
        return None

    def is_connected(self) -> bool:
        return True

    async def publish(
        self, topic: str, payload: bytes, qos: int = 0, retain: bool = False
    ) -> None:
        del qos
        if retain:
            self.retained[topic] = payload if isinstance(payload, bytes) else str(payload).encode()

    async def subscribe(self, topic: str) -> None:
        del topic
        return None


LAST: list[RecordingPublisher] = []


def as_capture(retained: dict[str, bytes]) -> dict[str, dict[str, str]]:
    """Regroup flat topics into the device-keyed shape a consumer sees."""
    devices: dict[str, dict[str, str]] = {}
    for topic, payload in sorted(retained.items()):
        parts = topic.split("/")
        if len(parts) < 4:
            continue
        devices.setdefault(parts[2], {})["/".join(parts[3:])] = payload.decode()
    return devices


async def main() -> None:
    flat_runtime._AiomqttPublisher = RecordingPublisher  # type: ignore[assignment]

    engine = DynamicSimulationEngine(config_path=CONFIG)
    await engine.initialize_async()

    runtime = await flat_runtime.start_clone(engine)
    await flat_runtime.publish_tick(runtime)

    recorder = LAST[-1]
    capture = as_capture(recorder.retained)

    # The SDK's redundant publish path fails silently against no paho client, so
    # check the two topics a consumer cannot reach ready without.
    body = capture.get("sim-40t-001", {})
    missing = [key for key in ("$description", "$state") if key not in body]
    if missing:
        raise SystemExit(f"capture is unusable: {missing} never landed")

    OUT.write_text(json.dumps(capture, indent=2, sort_keys=True) + "\n")

    topics = sum(len(v) for v in capture.values())
    print(f"devices: {len(capture)}   topics: {topics}   $state={body['$state']!r}")
    print("device ids:", sorted(capture))


asyncio.run(main())
