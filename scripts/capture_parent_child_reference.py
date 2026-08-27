"""Capture the parent/child emitter's retained surface, without a broker.

Produces `tests/reference_payloads/parent_child_tree.json`, the schema_1
reference tree that fifteen test modules here replay through `devices_from_tree`.
A repository fixture, not package data: it sat inside the adapter's package
directory until 3.1.0 and was carried in the wheel for it, and no consumer of
either distribution reads it at runtime.

Run it from the **emitter's** environment, not this one — it imports
`ebus_panel_sim`, which caps `ebus-sdk` below the version this repo installs:

    cd ../distribution-enclosure-simulator
    uv run python ../span-panel-api/scripts/capture_parent_child_reference.py \\
        ../span-panel-api/tests/reference_payloads/parent_child_tree.json

`PANEL_SIM_DIR` overrides where the checkout is looked for; it defaults to a
`distribution-enclosure-simulator` directory beside this repo. Passing no output
path writes `parent_child_capture.json` in the working directory, which is the
safe way to look at a capture before adopting it.

**What the emitter is, and why depending on it is right.** `ebus-panel-sim` is
published by electrification-bus, the organisation that writes the eBus
specification, and is conformed against live panel output. It is the
specification in runnable form and the designated checkpoint for correctness —
not a third-party imitation to be second-guessed. Its `.ebus-spec.json` names the
specification commit it implements, and `test_the_emitters_pin_matches_ours`
checks that against ours, so a disagreement between this parser and a capture is
a disagreement about one document rather than about two.

What went wrong was never the dependency. It was depending on a **frozen,
unrecorded** copy: the reference tree was captured once, nothing wrote down what
made it, and when the emitter was corrected the capture silently was not — so
this repository went on asserting a producer defect as fact across roughly thirty
test files. Three things fix that, and all three are here: the pin lives in
`spec_lock.json`, this script reads it rather than restating it, and the capture
is refused when the installed emitter is not the pinned release.

Substitutes the transport rather than reassembling the emitter: the recorder is
handed to `Emitter(mqttc=...)`, the producer's own bring-your-own-transport
seam, and `start()` / `publish_tick()` then run their ordinary path — real
graph builder, real profiles, real BESS dispatch, real relay resolution, real
diff/publish loop. Only the socket is different, which is the point of the
capture. Reassembling instead would prove less than it appears to, because a
capture taken through different wiring than a real panel uses is a capture of
the wiring. `examples/run_forty_tab_minimal.py` in the emitter is the reference
for how it is driven; the difference is that it reads the tree back through a
broker and this records it at the transport.

**The manifest is `scripts/reference_panel.yaml`, in this repository.** Not the
emitter's `examples/forty_tab_minimal.yaml`, and that file says at its head
exactly which two things it changes and why — spec-legal shed priorities in place
of a value the emitter degrades to `UNKNOWN`
(electrification-bus/distribution-enclosure-simulator#51), and the identity
properties a real panel publishes. The cost of that choice is real and worth
naming: the capture is no longer reproducible by running an example anyone can
find in the emitter, so the manifest is committed here and pinned in
`spec_lock.json` as `peers.ebus-panel-sim.manifest`.

**Shape-stable, not byte-stable.** Every `$description` carries a `version`
minted from the wall clock when its device is built, so all fourteen move on
every run. Nothing here reads it — it is Homie's own change counter — but it
does mean a recapture always shows fourteen diffs, and that a diff confined to
those lines says the producer did not move.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import hashlib
import json
import os
import pathlib
import sys

_REPO = pathlib.Path(__file__).resolve().parent.parent
# Run as a file — which is the only way this is run, and from the emitter's
# working directory at that — the interpreter puts *this* directory on the path
# and not the repository above it, so `scripts._lock` is not importable until we
# say where the repository is. Appended rather than prepended: this process is
# somebody else's, and the emitter's own imports get to resolve first.
sys.path.append(str(_REPO))

SIM = pathlib.Path(os.environ.get("PANEL_SIM_DIR", _REPO.parent / "distribution-enclosure-simulator"))
if not (SIM / "src").is_dir():
    raise SystemExit(f"no emitter checkout at {SIM}; set PANEL_SIM_DIR")
sys.path.insert(0, str(SIM / "src"))

import yaml  # noqa: E402

from scripts._lock import mapping as _mapping, peer, string  # noqa: E402

from ebus_panel_sim import (  # noqa: E402
    BESSConfig,
    ChargeMode,
    DeviceInstance,
    DeviceManifest,
    Emitter,
    PanelEnvelopeTick,
    SetterRegistry,
    TickInputs,
    __version__ as PRODUCER_VERSION,
)

MANIFEST = _REPO / "scripts" / "reference_panel.yaml"
PEER = "ebus-panel-sim"

OUT = pathlib.Path(sys.argv[1]) if len(sys.argv) > 1 else pathlib.Path("parent_child_capture.json")

_ID_NAMESPACE = "panel-sim-example"
_VALID_RELAY_BEHAVIORS = frozenset({"controllable", "non-controllable", "always-on"})
_VALID_INVERTER_TYPES = frozenset({"hybrid", "ac-coupled"})


# ---------------------------------------------------------------------------
# Reading YAML without giving up on types
#
# The mapping check itself is `scripts/_lock.py`'s, because reading the lockfile
# needs the same one and two copies of it would be two things to keep honest.
# What is below is the rest of the manifest's vocabulary, which only this script
# reads.
# ---------------------------------------------------------------------------


def _optional_mapping(value: object) -> dict[str, object]:
    return _mapping(value, "") if isinstance(value, Mapping) else {}


def _sequence(value: object, where: str) -> list[object]:
    if not isinstance(value, Sequence) or isinstance(value, str | bytes):
        raise SystemExit(f"{where} must be a list, got {type(value).__name__}")
    return list(value)


def _mappings(value: object, where: str) -> list[dict[str, object]]:
    return [_mapping(item, f"{where}[{index}]") for index, item in enumerate(_sequence(value, where))]


def _text(source: Mapping[str, object], key: str, default: str) -> str:
    value = source.get(key)
    return default if value is None else str(value)


def _number(value: object, where: str) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float | str):
        raise SystemExit(f"{where} must be a number, got {value!r}")
    return float(value)


def _decimal(source: Mapping[str, object], key: str, default: float) -> float:
    value = source.get(key)
    return default if value is None else _number(value, key)


def _flag(source: Mapping[str, object], key: str, *, default: bool = False) -> bool:
    value = source.get(key)
    return default if value is None else bool(value)


def _bool_str(value: bool) -> str:
    return "true" if value else "false"


def circuit_id(source_id: str) -> str:
    """The emitter example's own circuit-id derivation, reproduced exactly.

    Hashed rather than named so the ids in the capture look like the opaque
    32-hex ids a real panel publishes, and reproducible so a recapture does not
    rewrite every circuit key.
    """
    return hashlib.sha256(f"{_ID_NAMESPACE}:{source_id}".encode()).hexdigest()[:32]


# ---------------------------------------------------------------------------
# The pin, which lives in exactly one place
# ---------------------------------------------------------------------------


def pinned_release() -> str:
    """The `ebus-panel-sim` release this capture is a capture of.

    Read out of `spec_lock.json` rather than restated here. A constant in this
    file would be a second home for the pin, and the two would agree right up
    until somebody recaptured and updated only one -- which is the failure this
    whole change exists to make impossible.
    """
    return string(peer(PEER), "version", f"peers.{PEER}")


# ---------------------------------------------------------------------------
# The manifest
# ---------------------------------------------------------------------------


class Profile:
    """`reference_panel.yaml`, read once and answered from."""

    def __init__(self, path: pathlib.Path) -> None:
        with path.open(encoding="utf-8") as handle:
            loaded: object = yaml.safe_load(handle)
        self._root = _mapping(loaded, str(path))
        self.panel = _mapping(self._root["panel_config"], "panel_config")
        self.bess = _optional_mapping(self._root.get("bess"))
        self.templates = _optional_mapping(self._root.get("circuit_templates"))
        self.circuits = _mappings(self._root.get("circuits", []), "circuits")
        self.tick_rows = _mappings(self._root.get("ticks", []), "ticks")

    @property
    def panel_id(self) -> str:
        return _text(self.panel, "serial_number", "")

    def template_of(self, circuit: Mapping[str, object]) -> dict[str, object]:
        return _optional_mapping(self.templates.get(_text(circuit, "template", "")))

    def circuits_of_type(self, device_type: str) -> list[dict[str, object]]:
        return [c for c in self.circuits if _text(self.template_of(c), "device_type", "") == device_type]


def relay_behavior(template: Mapping[str, object]) -> str:
    candidate = _text(template, "relay_behavior", "controllable").lower().replace("_", "-")
    return candidate if candidate in _VALID_RELAY_BEHAVIORS else "controllable"


def circuit_instance(profile: Profile, circuit: Mapping[str, object], pcs_priority: int) -> DeviceInstance:
    template = profile.template_of(circuit)
    behavior = relay_behavior(template)
    tabs = ",".join(str(int(_number(tab, "tabs"))) for tab in _sequence(circuit["tabs"], "tabs"))
    return DeviceInstance(
        "circuit",
        circuit_id(_text(circuit, "id", "")),
        _text(circuit, "name", _text(circuit, "id", "")),
        metadata={
            "tab-numbers": tabs,
            "breaker-rating-a": str(_decimal(template, "breaker_rating", _decimal(circuit, "breaker_rating", 20.0))),
            "default-priority": _text(template, "priority", "").upper(),
            "relay-behavior": behavior,
            "placement": _text(circuit, "placement", "downstream-of-lugs"),
            "always-on": _bool_str(behavior == "always-on"),
            # The other commissioning lock (ebus-panel-sim 0.8.0): read by the
            # emitter's `manifest_physics.never_backup`, which pins the priority
            # and drops `$settable` from `load-shed/priority`.
            "never-backup": _bool_str(_flag(template, "never_backup") or _flag(circuit, "never_backup")),
            "pcs-priority": str(pcs_priority),
        },
    )


def bess_instances(profile: Profile) -> list[DeviceInstance]:
    """The battery, and the MID an islandable enclosure hosts beside it.

    Together because the MID's identity is derived from the battery's -- it is
    the `<bess-id>-mid` child a grid-forming BESS exposes on a real panel, and
    its serial is the battery's with a suffix.
    """
    if not _flag(profile.bess, "enabled"):
        return []
    bess_id = _text(profile.bess, "instance_id", "bess")
    vendor = _text(profile.bess, "vendor", "Span")
    serial = _text(profile.bess, "serial_number", "")
    battery = DeviceInstance(
        "bess",
        bess_id,
        "Battery",
        metadata={
            "vendor-name": vendor,
            "model": _text(profile.bess, "product_name", "Battery"),
            "part-number": _text(profile.bess, "part_number", ""),
            "serial-number": serial,
            "firmware-version": _text(profile.bess, "firmware_version", ""),
            "nameplate-capacity-kwh": str(_decimal(profile.bess, "nameplate_capacity_kwh", 13.5)),
            "initial-soe-kwh": str(_decimal(profile.bess, "initial_soe_kwh", 0.0)),
            "relative-position": _text(profile.bess, "relative_position", "UPSTREAM"),
        },
    )
    if not _flag(profile.panel, "islandable"):
        return [battery]
    mid = DeviceInstance(
        "mid",
        f"{bess_id}-mid",
        "Microgrid Interconnect Device",
        metadata={
            "vendor-name": vendor,
            "model": _text(profile.bess, "mid_product_name", ""),
            "serial-number": f"{serial}-mid",
            "firmware-version": _text(profile.bess, "mid_firmware_version", ""),
            "hardware-version": _text(profile.bess, "mid_hardware_version", ""),
        },
    )
    return [battery, mid]


def pv_instance(profile: Profile) -> list[DeviceInstance]:
    feeds = profile.circuits_of_type("pv")
    if not feeds:
        return []
    template = profile.template_of(feeds[0])
    inverter = _text(template, "inverter_type", "ac-coupled").lower().replace("_", "-")
    return [
        DeviceInstance(
            "pv",
            "pv",
            "Solar",
            metadata={
                "vendor-name": "Enphase",
                "model": "IQ8PLUS-72-2-US",
                "firmware-version": _text(template, "firmware_version", ""),
                "nominal-power-w": str(_decimal(template, "nameplate_capacity_w", 5000.0)),
                "inverter-type": inverter if inverter in _VALID_INVERTER_TYPES else "ac-coupled",
                "relative-position": "IN_PANEL",
                "feed": circuit_id(_text(feeds[0], "id", "")),
            },
        )
    ]


def evse_instances(profile: Profile) -> list[DeviceInstance]:
    instances: list[DeviceInstance] = []
    for index, circuit in enumerate(profile.circuits_of_type("evse"), start=1):
        suffix = "" if index == 1 else f"-{index}"
        instances.append(
            DeviceInstance(
                "evse",
                f"evse{suffix}",
                _text(circuit, "name", "EV Charger"),
                metadata={
                    "vendor-name": "SPAN",
                    "model": "SPAN Drive",
                    "part-number": "SPN-DRV-001",
                    "serial-number": f"SIM-EVSE-{profile.panel_id}{suffix}",
                    "firmware-version": _text(profile.panel, "firmware_version", ""),
                    "max-current-a": "32.0",
                    "feed": circuit_id(_text(circuit, "id", "")),
                },
            )
        )
    return instances


def manifest(profile: Profile) -> DeviceManifest:
    """The commissioned panel this capture describes."""
    total_tabs = int(_decimal(profile.panel, "total_tabs", 40))
    instances: list[DeviceInstance] = [
        DeviceInstance(
            "panel",
            profile.panel_id,
            _text(profile.panel, "display_name", "Panel"),
            metadata={
                "vendor-name": "Span",
                "serial-number": profile.panel_id,
                "firmware-version": _text(profile.panel, "firmware_version", ""),
                "hardware-version": _text(profile.panel, "hardware_version", ""),
                "panel-size": str(total_tabs),
                "main-breaker-rating-a": str(int(_decimal(profile.panel, "main_size", 200))),
                "panel-model": f"MAIN_{total_tabs}",
                "postal-code": _text(profile.panel, "postal_code", ""),
                "time-zone": _text(profile.panel, "time_zone", ""),
                "service-voltage-v": str(_decimal(profile.panel, "service_voltage_v", 240.0)),
                "line-voltage-v": str(_decimal(profile.panel, "line_voltage_v", 120.0)),
                "islandable": _bool_str(_flag(profile.panel, "islandable")),
            },
        ),
        DeviceInstance("lugs", "lugs-upstream", "Upstream lugs", {"direction": "upstream"}),
        DeviceInstance("lugs", "lugs-downstream", "Downstream lugs", {"direction": "downstream"}),
    ]
    instances.extend(
        circuit_instance(profile, circuit, index) for index, circuit in enumerate(profile.circuits, start=1)
    )
    instances.extend(bess_instances(profile)[:1])
    instances.extend(pv_instance(profile))
    instances.extend(evse_instances(profile))
    # The MID last, matching the emitter example's ordering. Order is not load
    # bearing -- the capture is regrouped by device id and written sorted -- but
    # matching it keeps the two manifests diffable.
    instances.extend(bess_instances(profile)[1:])
    return DeviceManifest(instances=tuple(instances))


def bess_config(profile: Profile) -> tuple[BESSConfig, ...]:
    if not _flag(profile.bess, "enabled"):
        return ()
    mode: ChargeMode = "backup-only" if _text(profile.bess, "charge_mode", "") == "backup-only" else "self-consumption"
    return (
        BESSConfig(
            instance_id=_text(profile.bess, "instance_id", "bess"),
            nameplate_capacity_kwh=_decimal(profile.bess, "nameplate_capacity_kwh", 13.5),
            max_charge_w=_decimal(profile.bess, "max_charge_w", 3500.0),
            max_discharge_w=_decimal(profile.bess, "max_discharge_w", 3500.0),
            backup_reserve_pct=_decimal(profile.bess, "backup_reserve_pct", 20.0),
            charge_mode=mode,
        ),
    )


def ticks(profile: Profile) -> list[TickInputs]:
    """The driving signal, one entry per `ticks:` row in the manifest."""
    envelope = PanelEnvelopeTick(wifi_ssid=_text(profile.panel, "wifi_ssid", "") or None)
    evse_feeds = {
        ("evse" if index == 1 else f"evse-{index}"): circuit_id(_text(circuit, "id", ""))
        for index, circuit in enumerate(profile.circuits_of_type("evse"), start=1)
    }
    built: list[TickInputs] = []
    for row in profile.tick_rows:
        powers = {
            circuit_id(str(source_id)): _number(power, f"ticks.circuits.{source_id}")
            for source_id, power in _mapping(row["circuits"], "ticks.circuits").items()
        }
        built.append(
            TickInputs(
                current_time=_decimal(row, "current_time", float(len(built) * 60)),
                grid_online=_flag(row, "grid_online", default=True),
                circuits=powers,
                evse={evse_id: powers.get(feed, 0.0) for evse_id, feed in evse_feeds.items()},
                envelope=envelope,
            )
        )
    return built


# ---------------------------------------------------------------------------
# The recorder
# ---------------------------------------------------------------------------


class RecordingTransport:
    """Satisfies `ebus_sdk.MqttDeviceTransport`, keeping last-wins retained state.

    Last-wins because that is what a broker's retained store holds, and therefore
    what a consumer replays on connect. Non-retained publishes are dropped for
    the same reason: they are not in the store a consumer subscribes to.

    The SDK never starts or stops an injected client, which is why this has no
    lifecycle methods to implement — see `MqttTransport`'s own note on that.
    """

    is_running = True

    def __init__(self) -> None:
        self.retained: dict[str, str] = {}

    def publish(self, topic: str, data: str, qos: int = 1, retain: bool = False) -> object:
        del qos
        if retain:
            self.retained[topic] = data
        return None

    def subscribe(self, sub: str, param: object, qos: int = 1) -> object:
        del sub, param, qos
        return None

    def is_connected(self) -> bool:
        return True


def as_capture(retained: dict[str, str]) -> dict[str, dict[str, str]]:
    """Regroup `ebus/5/<device>/<node>/<property>` topics by device.

    The shape `device_from_topics` replays: `{device_id: {topic: payload}}`,
    every value a string, `$description` a JSON *string* exactly as retained.
    """
    devices: dict[str, dict[str, str]] = {}
    for topic, payload in sorted(retained.items()):
        parts = topic.split("/")
        if len(parts) < 4:
            continue
        devices.setdefault(parts[2], {})["/".join(parts[3:])] = payload
    return devices


def main() -> None:
    expected = pinned_release()
    if PRODUCER_VERSION != expected:
        raise SystemExit(
            f"{SIM} is ebus-panel-sim {PRODUCER_VERSION}, and spec_lock.json records the reference "
            f"tree as a capture of {expected}. Capturing anyway would put bytes in this repository "
            "that the lockfile attributes to a release that did not make them. Move the checkout to "
            "the pinned release, or take the new capture deliberately: update peers.ebus-panel-sim's "
            "version, tag and commit in spec_lock.json, and the provenance section of "
            "tests/reference_payloads/README.md, in the same change."
        )

    profile = Profile(MANIFEST)
    recorder = RecordingTransport()
    emitter = Emitter(manifest(profile), SetterRegistry(), mqttc=recorder, bess_configs=bess_config(profile))
    emitter.start()
    try:
        for tick in ticks(profile):
            emitter.publish_tick(tick)
        # Read the store while the tree is up. `stop()` republishes `$state`, and
        # a capture of a panel shutting down is not what a consumer replays.
        capture = as_capture(recorder.retained)
    finally:
        emitter.stop(graceful=True)

    # An injected transport publishes nothing the SDK does not ask it to, so check
    # the two topics a consumer cannot reach `ready` without rather than trusting
    # that they landed.
    body = capture.get(profile.panel_id, {})
    missing = [key for key in ("$description", "$state") if key not in body]
    if missing:
        raise SystemExit(f"capture is unusable: {missing} never landed")
    if body["$state"] != "ready":
        raise SystemExit(f"capture is of a panel in {body['$state']!r}, not ready")

    OUT.write_text(json.dumps(capture, indent=2, sort_keys=True) + "\n")

    topics = sum(len(value) for value in capture.values())
    print(f"producer: ebus-panel-sim {PRODUCER_VERSION}   manifest: {MANIFEST.name}")
    print(f"devices: {len(capture)}   topics: {topics}   -> {OUT}")
    print("device ids:", sorted(capture))


main()
