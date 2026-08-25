# SPAN Panel API

[![GitHub Release](https://img.shields.io/github/v/release/SpanPanel/span-panel-api?filter=v*&style=flat-square&label=release)](https://github.com/SpanPanel/span-panel-api/releases)
[![PyPI Version](https://img.shields.io/pypi/v/span-panel-api?style=flat-square&label=span-panel-api)](https://pypi.org/project/span-panel-api/)
[![Python Versions](https://img.shields.io/pypi/pyversions/span-panel-api?style=flat-square)](https://pypi.org/project/span-panel-api/)
[![License](https://img.shields.io/github/license/SpanPanel/span-panel-api?style=flat-square)](https://github.com/SpanPanel/span-panel-api/blob/main/LICENSE)

[![schema-0](https://img.shields.io/pypi/v/span-panel-api-schema-0?style=flat-square&label=schema-0)](https://pypi.org/project/span-panel-api-schema-0/)
[![schema-1](https://img.shields.io/pypi/v/span-panel-api-schema-1?style=flat-square&label=schema-1)](https://pypi.org/project/span-panel-api-schema-1/)

[![CI Status](https://img.shields.io/github/actions/workflow/status/SpanPanel/span-panel-api/ci.yml?branch=main&style=flat-square&label=CI)](https://github.com/SpanPanel/span-panel-api/actions/workflows/ci.yml)

[![Code Quality](https://img.shields.io/codefactor/grade/github/SpanPanel/span-panel-api?style=flat-square)](https://www.codefactor.io/repository/github/spanpanel/span-panel-api)

[![Pre-commit](https://img.shields.io/badge/pre--commit-enabled-brightgreen?logo=pre-commit&style=flat-square)](https://github.com/pre-commit/pre-commit)
[![Linting: Ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json&style=flat-square)](https://github.com/astral-sh/ruff)
[![Type Checking: MyPy](https://img.shields.io/badge/type%20checking-mypy-blue?style=flat-square)](https://mypy-lang.org/)

[![Buy Me A Coffee](https://img.shields.io/badge/Buy%20Me%20A%20Coffee-support%20development-FFDD00?style=flat-square&logo=buy-me-a-coffee&logoColor=black)](https://www.buymeacoffee.com/cayossarian)

A Python client library for the SPAN Panel API, using MQTT/Homie for real-time push-based panel state.

## Version support

| Line    | Status                                                               | Needs                     |
| ------- | -------------------------------------------------------------------- | ------------------------- |
| **3.x** | Current. Transport and dispatcher; the parser is a separate install. | v2 firmware, Python 3.14+ |
| **2.x** | Superseded by 3.0.0 and no longer developed. Fixes land on 3.x only. | v2 firmware, Python 3.10+ |
| **1.x** | Deprecated.                                                          | v1 firmware               |

**1.x is built on the SPAN v1 REST API**, which SPAN retires when v1 firmware sunsets at the end of 2026. Nothing on that line will outlive the firmware it talks to.

**2.x still works against v2 firmware, but it is closed to new work.** Everything since — the parent/child data model, adoption, discovery, extension properties — landed on 3.x, and so will anything that comes next. Treat 2.x as a line to leave rather than
a line to stay on.

Moving from 2.x to 3.x is an install rather than only an upgrade: 3.0.0 removed the bundled parser, so `pip install -U span-panel-api` alone leaves you with a client that connects and then raises `SpanPanelAdapterMissingError`. See
[Installation](#installation) for the extra to name.

## Installation

Two packages: the transport, and a parser for your panel's schema. `span-panel-api` contains **no parser** — installing it alone gives a client that connects and then raises `SpanPanelAdapterMissingError`.

```bash
# flat schema, firmware r202603-r202627
pip install "span-panel-api[schema-0]"

# parent/child schema, firmware r202633+ (data-model-version 1.x)
pip install "span-panel-api[schema-1]"

# support either panel from one install
pip install "span-panel-api[schema-0,schema-1]"
```

The extras are the recommended spelling because they give `pip install -U` a correct upgrade path; naming `span-panel-api-schema-0` / `span-panel-api-schema-1` directly works too.

### The parser is hot-loaded, not imported

`span-panel-api` never imports a parser. Each wire format is its own distribution, registering itself under the `span_panel_api.schema_adapters` entry-point group, and the transport reaches it by key at runtime:

1. **Ask the panel first.** Before the broker is opened, the client fetches `GET /api/v2/homie/schema` over REST and reads `dataModelVersion`. Absence means the flat schema — a real signal, since the property arrived with the firmware that introduced
   parent/child. Current parent/child firmware reports the canonical `MAJOR.MINOR[.PATCH]` form — `1.0` — which selects `schema_1` outright. A value whose major is still unambiguous but whose form is not canonical (`1`, `1_0`) dispatches on that major and
   logs the deviation, so a firmware that changes format is visible before it is an outage. A value with no readable major (`v1.0`, `x`) raises `SpanPanelSchemaVersionError` rather than guessing: falling back to flat would hand a parent/child panel to the
   flat parser, which does not fail — it produces plausible but wrong figures.
2. **Enumerate without importing.** `installed_adapter_keys()` reads distribution metadata only. Nothing is imported to find out what is installed, so a flat panel never pays for `span-panel-api-schema-1` — nor for the eBus SDK underneath it.
3. **Resolve on demand, once.** The adapter for the selected key is imported the first time a panel asks for it, then cached. The async paths run enumeration and resolution in a thread, so neither blocks the event loop.
4. **Verify the contract before trusting it.** Every adapter declares `ADAPTER_CONTRACT` as a literal, and discovery rejects any that does not match this package's `ADAPTER_CONTRACT_VERSION`. Member presence is not the whole contract — a Protocol cannot
   express signatures at runtime — so this is what stops two packages built against different versions of each other failing much later as a bare `TypeError` inside the transport. A rejection is logged rather than raised, so one unusable third-party
   adapter cannot take down a panel whose own adapter is fine.
5. **Re-dispatch when the panel changes underneath you.** A panel that upgrades firmware from flat to parent/child mid-life drops MQTT, reboots and comes back on a new schema. The client refetches, resolves the new adapter **before** touching any state,
   and swaps the parser in place — no reload. An install with no adapter for the new generation logs which package to install and keeps the parser it has.

Three errors keep the failure modes apart, because the remedy differs: `SpanPanelAdapterMissingError` (install something), `SpanPanelSchemaVersionError` (a schema no adapter can even be named for), and `SpanPanelAdapterIncompatibleError` (installing more
cannot help). All are exported from the top-level package.

The consequence worth planning around: **supporting a new panel schema is an install, not an upgrade.** The distributions version independently — see [RELEASE.md](RELEASE.md).

### Dependencies

- `httpx` — v2 authentication and detection endpoints
- `paho-mqtt` — MQTT/Homie transport (real-time push)
- `pyyaml` — YAML parsing for configuration and API payloads

## Architecture

### Transport

The `SpanMqttClient` connects to the panel's MQTT broker (MQTTS or WebSocket) and subscribes to the Homie device tree. It owns the connection, the subscription and the dispatch decision — and nothing else. Everything that knows what a topic _means_ lives
in the adapter for that panel's schema:

- **The transport** (this package) makes one wildcard subscription, routes messages, tracks connection state, publishes commands, and hands raw messages to whichever parser was resolved for this panel.
- **The parser** (`span-panel-api-schema-0` or `span-panel-api-schema-1`) accumulates properties, decides when the panel is ready to read, and builds typed `SpanPanelSnapshot` dataclasses from what it has.

That boundary is why `HomiePropertyAccumulator`, `HomieLifecycle` and `HomieDeviceConsumer` are **not** exported from this package: all three are flat-schema-specific rather than Homie-convention-level. The accumulator filters every topic against a single
device's prefix and stores `node → prop`, which drops nearly every message under the parent/child model, and `HomieLifecycle`'s members are not Homie 5 `$state` values but a consumer-side progression encoding "one description received ⇒ ready". They live
in `span_panel_api_schema_0`, where that model is correct. The parent/child parser reaches the same result differently, replaying the retained tree through the eBus SDK and waiting for every declared device to describe itself at any depth.

Changes are pushed to consumers via callbacks. Dirty-node tracking allows the snapshot builder to skip unchanged nodes, reducing per-scan CPU cost on constrained hardware.

### Event-Loop-Driven I/O (Home Assistant Compatible)

The MQTT transport is designed around the Home Assistant core async pattern — all paho-mqtt I/O runs on the asyncio event loop with no background threads:

- **NullLock replacement** — paho-mqtt's seven internal threading locks are replaced with no-op `NullLock` instances at setup time, eliminating lock contention since all access is single-threaded on the event loop.
- **`add_reader` / `add_writer`** — `AsyncMqttBridge` registers the MQTT socket with the event loop via `loop.add_reader()` and `loop.add_writer()`, calling paho's `loop_read()` / `loop_write()` directly from I/O callbacks rather than from a `loop_start()`
  background thread.
- **Periodic misc** — A `loop.call_at()` timer fires every second to call `loop_misc()` for keepalive and timeout housekeeping.
- **Executor bridge for connect** — The initial TLS handshake and TCP connect are blocking operations, so they run in `loop.run_in_executor()`. Once the executor returns, socket callbacks are immediately switched from sync bridges (`call_soon_threadsafe`)
  back to the async-only versions.

This means the library can be dropped into any asyncio application — including Home Assistant — without spawning threads or requiring thread-safe wrappers.

### Circuit Name Synchronization

Circuit names arrive as MQTT retained messages that may land after the Homie device transitions to `$state=ready`. The client handles this with a bounded wait during `connect()`:

1. After the device reaches ready state, the client polls the resolved adapter's `circuit_nodes_missing_names()` every 250ms — a `SchemaAdapter` member, so both parsers answer it in their own terms.
2. As retained name properties arrive, the consumer stores them. Once all circuit-type nodes have a name, the wait returns immediately.
3. If names have not all arrived within 10 seconds, the timeout expires (non-fatal) and the client proceeds — circuits without names will use fallback identifiers.

This ensures that the first `get_snapshot()` after connect returns human-readable circuit names in the common case, while never blocking indefinitely on a missing retained message.

### Protocols

The library defines structural subtyping protocols (PEP 544). All are `runtime_checkable`, so a consumer asks `isinstance` before offering a control rather than assuming the panel in front of it supports one:

| Protocol                      | Purpose                                                                                    |
| ----------------------------- | ------------------------------------------------------------------------------------------ |
| `SpanPanelClientProtocol`     | Core lifecycle: `connect`, `close`, `ping`, `get_snapshot`, `register_connection_callback` |
| `CircuitControlProtocol`      | Relay and shed-priority control: `set_circuit_relay`, `set_circuit_priority`               |
| `PanelControlProtocol`        | Panel-level control: `set_dominant_power_source`                                           |
| `EvseControlProtocol`         | Per-charger control: `set_evse_charge_limit(node_id, amps)`                                |
| `AdoptedControlProtocol`      | Write to a settable property of a device this library models nothing for                   |
| `ControlInterceptionProtocol` | Install one veto-and-observe point for every control command: `set_control_interceptor`    |
| `StreamingCapableProtocol`    | Push-based updates: `register_snapshot_callback`, `start_streaming`, `stop_streaming`      |

The first five differ in subject, not just in name. `EvseControlProtocol` is separate from `PanelControlProtocol` because several chargers may be commissioned at once and every call names which one. `AdoptedControlProtocol` differs in kind: the curated
setters name a control this library understands and translate or bound the value on the way out, while this one names a property by its wire address and passes the value through, because the declaration is all anybody here knows about it. That write is
authorised by the snapshot rather than by its arguments — the transport resolves the property against the current `adopted_devices` and refuses anything it does not find carrying a set topic, so a device this library _does_ model cannot be addressed
through it.

`ControlInterceptionProtocol` is separate from the four control protocols rather than a member of them because adding it there would break every implementer of those protocols a second time in one release, and separate from `StreamingCapableProtocol`
because a transport could reasonably offer one and not the other.

One further protocol, `SchemaAdapter`, is the bootstrap-to-parser contract rather than a consumer-facing one; it is what an adapter distribution implements and what discovery checks. Integration code programs against the protocols above, not against
transport-specific classes.

### Snapshots

All panel state is represented as immutable, frozen dataclasses:

| Dataclass             | Content                                                                                                                                                            |
| --------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `SpanPanelSnapshot`   | Complete panel state: power, energy, grid/DSM state, hardware status, per-leg voltages, power flows, lugs current, shed forecast, circuits, battery, PV, EVSE, MID |
| `SpanCircuitSnapshot` | Per-circuit: power, energy, relay state, priority, tabs, device type, breaker rating, current, `$target` pending state                                             |
| `SpanBatterySnapshot` | BESS: SoC percentage, SoE kWh, own meter reading, communication state, link health, `model` / `part_number`, nameplate capacity                                    |
| `SpanPVSnapshot`      | PV inverter: link health, `model` / `part_number`, nameplate capacity                                                                                              |
| `SpanEvseSnapshot`    | EVSE (EV charger): status, lock state, advertised current, link health, `model` / `part_number` / serial / version metadata                                        |
| `SpanMidSnapshot`     | Microgrid Interconnect Device: islanding state, grid state, grid-forming entity                                                                                    |
| `AdoptedDevice`       | A device type this library models nothing for, carried whole: identity, readings, proxy link                                                                       |
| `ExtensionProperty`   | A vendor property on a device this library _does_ model, with its value and the subject it hangs off                                                               |

Identity is normalised across every DER class: **`model` is the human designation and `part_number` is the SKU**, on `battery`, `evse` and `pv` alike. `product_name` was retired in 3.0.0 — see the changelog, because `battery.model` changes value for
existing flat users at that upgrade.

`mid`, `adopted_devices`, `extension_properties` and the per-DER link-health fields exist only under the parent/child schema. They are `None` or empty on a flat panel rather than absent, so a consumer reads the same snapshot type either way.

## Usage

### Factory Pattern (Recommended)

The `create_span_client()` factory handles v2 registration and returns a configured `SpanMqttClient`:

```python
import asyncio
from span_panel_api import create_span_client

async def main():
    client = await create_span_client(
        host="192.168.1.100",
        passphrase="your-panel-passphrase",
    )

    try:
        await client.connect()

        # Get a point-in-time snapshot
        snapshot = await client.get_snapshot()
        # The upstream lugs' own meter. That is grid flow only where the lugs are
        # the utility connection point; a BESS wired ahead of them, or a panel fed
        # by another panel, makes it this panel's feed instead. `power_flow_grid`
        # is the site-level figure in every topology.
        if snapshot.lugs_at_service_entrance:
            print(f"Grid power: {snapshot.instant_grid_power_w}W")
        else:
            print(f"Panel feed: {snapshot.instant_grid_power_w}W")
            print(f"Grid power: {snapshot.power_flow_grid}W")
        print(f"Firmware: {snapshot.firmware_version}")
        print(f"Circuits: {len(snapshot.circuits)}")

        for cid, circuit in snapshot.circuits.items():
            print(f"  {circuit.name}: {circuit.instant_power_w}W ({circuit.relay_state})")

    finally:
        await client.close()

asyncio.run(main())
```

### Streaming Pattern

For real-time push updates without polling:

```python
import asyncio
from span_panel_api import create_span_client, SpanPanelSnapshot

async def on_snapshot(snapshot: SpanPanelSnapshot) -> None:
    print(f"Grid: {snapshot.instant_grid_power_w}W, Circuits: {len(snapshot.circuits)}")

async def main():
    client = await create_span_client(
        host="192.168.1.100",
        passphrase="your-panel-passphrase",
    )

    try:
        await client.connect()

        # Register callback and start streaming
        unsubscribe = client.register_snapshot_callback(on_snapshot)
        await client.start_streaming()

        # Run until interrupted
        await asyncio.Event().wait()

    finally:
        await client.stop_streaming()
        await client.close()

asyncio.run(main())
```

### Connection State Monitoring

Push consumers that need to react to broker disconnect/reconnect events — for example, to mark downstream entities offline within a second of a dropped connection rather than waiting on a fallback poll — can register a connection callback. The callback
fires `False` on disconnect and `True` on reconnect, edge-only (no synthetic call at registration time):

```python
def on_connection_change(connected: bool) -> None:
    if connected:
        print("Broker connection restored")
    else:
        print("Broker connection lost")

unsubscribe_connection = client.register_connection_callback(on_connection_change)

# Later, during teardown:
unsubscribe_connection()
```

To check the current connection state on demand (for example, just after registering), call `await client.ping()`.

When the client is not fully live (broker disconnected, or Homie device not yet ready), `await client.get_snapshot()` raises `SpanPanelStaleDataError` instead of returning cached data. Treat that exception as the canonical "panel currently unreachable"
signal — see [Error Handling](#error-handling) below.

### Pre-Built Config Pattern

If you already have MQTT broker credentials (e.g., stored from a previous registration):

```python
from span_panel_api import create_span_client, MqttClientConfig

config = MqttClientConfig(
    broker_host="192.168.1.100",
    username="stored-username",
    password="stored-password",
    mqtts_port=8883,
    ws_port=9001,
    wss_port=443,
)

client = await create_span_client(
    host="192.168.1.100",
    mqtt_config=config,
    serial_number="nj-2316-XXXX",
)
```

### Direct Client Construction

Consumers that manage their own registration and broker configuration can instantiate `SpanMqttClient` directly:

```python
from span_panel_api import SpanMqttClient, MqttClientConfig

config = MqttClientConfig(
    broker_host="192.168.1.100",
    username="stored-username",
    password="stored-password",
    mqtts_port=8883,
    ws_port=9001,
    wss_port=443,
)

client = SpanMqttClient(
    host="192.168.1.100",
    serial_number="nj-2316-XXXX",
    broker_config=config,
    snapshot_interval=1.0,
)
await client.connect()
```

### Scan Frequency

`set_snapshot_interval()` controls how often push-mode snapshot callbacks fire. Lower values mean lower latency; higher values reduce CPU usage on constrained hardware. Dirty-node caching (v2.5.0) further reduces per-scan cost by skipping unchanged nodes.

Passing `0` (or any non-positive value) disables debounce and dispatches a snapshot for every incoming property message — real-time mode, intended for fast consumers.

```python
# Reduce snapshot frequency to every 2 seconds
client.set_snapshot_interval(2.0)

# Real-time dispatch — every property update triggers a callback
client.set_snapshot_interval(0)
```

### Circuit Control

```python
# Set circuit relay (OPEN/CLOSED)
outcome = await client.set_circuit_relay("circuit-uuid", "OPEN")
await client.set_circuit_relay("circuit-uuid", "CLOSED")

# Set circuit shed priority (NEVER / SOC_THRESHOLD / OFF_GRID)
await client.set_circuit_priority("circuit-uuid", "NEVER")
```

Every setter returns a `PublishOutcome` saying how far the command got. Ignoring it is fine and behaves as it always did; reading it is how a caller tells a breaker that opened from a command that was never sent.

| `outcome.state`            | What it means                                                                   |
| -------------------------- | ------------------------------------------------------------------------------- |
| `PublishState.CONFIRMED`   | The property reported the requested value on its own topic                      |
| `PublishState.ACCEPTED`    | The broker acknowledged the message; no transition observed before the deadline |
| `PublishState.UNCONFIRMED` | Handed over, and nothing came back before the deadline                          |
| `PublishState.FAILED`      | Never handed to the broker, and will not be delivered                           |

`UNCONFIRMED` **is not an error and does not raise.** It is the expected result of writing a value that is already current — that case short-circuits immediately with `outcome.no_op` set rather than burning the deadline — and it is indistinguishable from a
silent policy rejection by the panel until SPAN ships a reason code. `FAILED` is the one state that is a promise about the future, which is why the transport refuses to publish while the broker is unreachable instead of letting paho queue the message and
deliver it minutes later. `CONFIRMED` is strong evidence rather than proof: the panel coalesces every API client into a single `USER` requester, so an observed transition cannot be attributed to one specific write. Nothing is retried — a relay write is not
idempotent in its physical effect.

### Control Interception

A consumer with a notion of who is asking can refuse a command before it is published, and record every command in one place rather than in five setters that will drift:

```python
class Gate:
    async def before_publish(self, command: ControlCommand) -> None:
        if not authorised(command):
            raise PermissionError(f"not allowed to write {command.property_id}")

    async def after_publish(self, command: ControlCommand, outcome: PublishOutcome) -> None:
        audit.record(command, outcome.state)

client.set_control_interceptor(Gate())
```

One interceptor at a time, replaceable; pass `None` to remove it. A veto's exception propagates to the caller **unchanged**, so a consumer raising a framework-specific error with a translated message gets it through intact. `after_publish` fires for
refusals too — with `FAILED` and a `vetoed` detail — because an audit that silently omits refusals is worse than no audit; it runs as a task rather than being awaited, so a sink that hangs cannot stall every control call, and ordering across commands is
therefore not guaranteed.

**This is a boundary against callers of this library and nothing more.** Anything holding the broker credential publishes to the panel directly and never reaches this code.

### Pinning the Panel CA

By default the MQTT bridge fetches the panel's CA over unauthenticated HTTP on every connect and trusts whatever answers, which also means a reconnect can silently re-anchor trust to a different CA. Supply the PEM instead and the anchor becomes a
configured value — no network call on connect or on rebuild:

```python
config = MqttClientConfig(..., ca_pem=stored_pem)

# The same context and the same fingerprint string the library uses, so a
# consumer's own HTTPS calls and its own pin cannot drift from the library's.
context = build_panel_ssl_context(stored_pem)
fingerprint = ca_fingerprint(stored_pem)
```

Leaving `ca_pem` unset keeps the previous behaviour, with one `WARNING` per bridge recording that the anchor was obtained unauthenticated. With it set, a certificate-verification failure is diagnosed rather than assumed: an expired leaf after a panel's
clock reset and a hostname mismatch after the panel moved both produce the identical error, so the library refetches the advertised CA for comparison only and keeps retrying unless the fingerprint has actually changed — at which point it raises
`SpanPanelCAChangedError` carrying both fingerprints and stops. Register `register_fatal_error_callback` to be told; a consumer that registers nothing still cannot mistake a dead bridge for a healthy one, because `ping()` and `get_snapshot()` re-raise.

The bootstrap REST calls take an `ssl_context` for the same purpose. `download_ca_cert` is the one exception and stays on plain HTTP — it fetches the anchor everything else is checked against, so it has nothing to check itself against, and its result must
be fingerprint-confirmed out of band before it is trusted.

### Pending-State Detection

When the panel publishes Homie `$target` properties, `SpanCircuitSnapshot` exposes the desired state alongside the actual state:

```python
for cid, circuit in snapshot.circuits.items():
    if circuit.relay_state_target and circuit.relay_state_target != circuit.relay_state:
        print(f"  {circuit.name}: relay transitioning {circuit.relay_state} → {circuit.relay_state_target}")
    if circuit.priority_target and circuit.priority_target != circuit.priority:
        print(f"  {circuit.name}: priority pending {circuit.priority} → {circuit.priority_target}")
```

### API Version Detection

Detect whether a panel supports v2 (unauthenticated probe):

```python
from span_panel_api import detect_api_version

result = await detect_api_version("192.168.1.100")
print(f"API version: {result.api_version}")  # "v1" or "v2"
if result.status_info:
    print(f"Serial: {result.status_info.serial_number}")
    print(f"Firmware: {result.status_info.firmware_version}")
```

### v2 Authentication Functions

Standalone async functions for v2-specific HTTP operations:

```python
from span_panel_api import (
    register_v2, download_ca_cert, get_homie_schema,
    regenerate_passphrase, get_v2_status,
    register_fqdn, get_fqdn, delete_fqdn,
)

# Register and obtain MQTT broker credentials
auth = await register_v2("192.168.1.100", "my-app", passphrase="panel-passphrase")
print(f"Broker: {auth.ebus_broker_host}:{auth.ebus_broker_mqtts_port}")
print(f"Serial: {auth.serial_number}")

# Download the panel's CA certificate (for TLS verification)
pem = await download_ca_cert("192.168.1.100")

# Fetch the Homie property schema (unauthenticated)
schema = await get_homie_schema("192.168.1.100")
print(f"Panel size: {schema.panel_size} spaces")
print(f"Schema hash: {schema.types_schema_hash}")

# Rotate MQTT broker password (invalidates previous password)
new_password = await regenerate_passphrase("192.168.1.100", token=auth.access_token)

# Get panel status (unauthenticated)
status = await get_v2_status("192.168.1.100")
print(f"Serial: {status.serial_number}, Firmware: {status.firmware_version}")

# FQDN management (for panel TLS certificate SAN)
await register_fqdn("192.168.1.100", "panel.local", token=auth.access_token)
fqdn = await get_fqdn("192.168.1.100", token=auth.access_token)
await delete_fqdn("192.168.1.100", token=auth.access_token)
```

## Error Handling

All exceptions inherit from `SpanPanelError`:

| Exception                  | Cause                                                                                              |
| -------------------------- | -------------------------------------------------------------------------------------------------- |
| `SpanPanelAuthError`       | Invalid passphrase, expired token, or missing credentials                                          |
| `SpanPanelConnectionError` | Cannot reach the panel (network/DNS) during initial connect                                        |
| `SpanPanelStaleDataError`  | `get_snapshot()` called while the broker is disconnected or the Homie device has not reached ready |
| `SpanPanelTimeoutError`    | Request or connection timed out                                                                    |
| `SpanPanelValidationError` | Data validation failure                                                                            |
| `SpanPanelAPIError`        | Unexpected HTTP response from v2 endpoints                                                         |
| `SpanPanelServerError`     | Panel answered 5xx, or answered `200` with a body that cannot be used — "not ready yet"            |

Three more are specific to the hot-loading model, and they are separate because the remedy differs:

| Exception                           | Cause                                                                     | Remedy                                       |
| ----------------------------------- | ------------------------------------------------------------------------- | -------------------------------------------- |
| `SpanPanelAdapterMissingError`      | Known schema, no installed parser for it                                  | Install the named package                    |
| `SpanPanelSchemaVersionError`       | The panel reports a `data-model-version` no adapter can even be named for | Nothing to install yet — report the value    |
| `SpanPanelAdapterIncompatibleError` | The required adapter is installed but was built against another contract  | Installing more cannot help — align versions |

Reporting the third as the first would send someone to install a package they already have.

`SpanPanelStaleDataError` is distinct from `SpanPanelConnectionError`: the former means the client is running but data cannot be trusted right now (transient disconnect, or panel-declared not-ready); the latter means the initial connect failed and the
client cannot be used at all. `SpanPanelServerError` covers the whole 5xx class deliberately: a booting panel brings its network stack and reverse proxy up before the application behind them, so it _answers_ rather than refuses, and that has to be
distinguishable from a 4xx that will not fix itself on its own.

```python
from span_panel_api import (
    SpanPanelAuthError,
    SpanPanelConnectionError,
    SpanPanelStaleDataError,
)

try:
    client = await create_span_client(host="192.168.1.100", passphrase="wrong")
except SpanPanelAuthError:
    print("Invalid passphrase")
except SpanPanelConnectionError:
    print("Cannot reach panel")

# Later, during normal operation:
try:
    snapshot = await client.get_snapshot()
except SpanPanelStaleDataError as err:
    # Broker dropped or panel declared not-ready — fall back to last-known
    # data, a grace-period value, or mark downstream state unavailable.
    print(f"Snapshot unavailable: {err}")
```

## Capabilities

The `PanelCapability` flag enum advertises transport features at runtime:

| Flag              | Meaning                               |
| ----------------- | ------------------------------------- |
| `EBUS_MQTT`       | Connected via MQTT/Homie transport    |
| `PUSH_STREAMING`  | Supports real-time push callbacks     |
| `CIRCUIT_CONTROL` | Can set relay state and shed priority |
| `BATTERY_SOE`     | Battery state-of-energy available     |

## Reference Payloads

Captures of what a panel actually serves, shipped as package data so a consumer can check its own assumptions against real bytes without vendoring a copy that silently goes stale:

```python
from span_panel_api.reference_payloads import homie_schema, homie_schema_types

document = homie_schema()        # the captured GET /api/v2/homie/schema response
types = homie_schema_types()     # its `types` map, typed as HomieSchemaTypes
```

`homie_schema_types()` returns exactly what `span_panel_api_schema_0.field_metadata.build_field_metadata` accepts, so building real adapter metadata to compare against is two lines and no file handling.

The parent/child device tree is the schema_1 counterpart and ships from that adapter, with the parser that can interpret it:

```python
from span_panel_api_schema_1.reference_payloads import devices_from_tree, parent_child_tree

devices = devices_from_tree(parent_child_tree())
```

Each payload carries the version of the release it shipped in. Pin a version and you read the bytes that version was written against.

## Project Structure

One repository, three distributions. The bootstrap is at the root; each parser is a workspace member under `packages/`, published separately and versioned on its own axis.

```text
src/span_panel_api/          # distribution: span-panel-api (no parser)
├── __init__.py              # Public API exports
├── _http.py                 # Shared httpx plumbing / client ownership rules
├── adapters.py              # installed_adapter_keys(), resolve_adapter() — metadata, then lazy import
├── auth.py                  # v2 HTTP provisioning (register, cert, schema, passphrase)
├── const.py                 # Panel state constants (DSM, relay)
├── detection.py             # detect_api_version() → DetectionResult
├── dispatch.py              # select_adapter_key() — what does this panel need?
├── exceptions.py            # Exception hierarchy
├── factory.py               # create_span_client() → SpanMqttClient
├── models.py                # Snapshot dataclasses (panel, circuit, battery, PV, EVSE, MID, adopted)
├── phase_validation.py      # Electrical phase utilities
├── protocol.py              # PEP 544 protocols, SchemaAdapter, PanelCapability flags
├── schema_drift.py          # Reporting a panel that outruns what we can read
├── reference_payloads/      # Captured GET /api/v2/homie/schema, shipped as package data
└── mqtt/
    ├── __init__.py
    ├── async_client.py      # NullLock + AsyncMQTTClient (HA core pattern)
    ├── client.py            # SpanMqttClient (transport + control protocols)
    ├── connection.py        # AsyncMqttBridge (event-loop-driven, no threads)
    ├── const.py             # MQTT/Homie constants + UUID helpers
    └── models.py            # MqttClientConfig, MqttTransport

packages/schema-0/           # distribution: span-panel-api-schema-0
└── src/span_panel_api_schema_0/
                             # Flat parser: HomiePropertyAccumulator, HomieLifecycle,
                             # HomieDeviceConsumer, field metadata, SCHEMA_ANCHOR

packages/schema-1/           # distribution: span-panel-api-schema-1
├── spec/                    # eBus capability catalogs, byte-copied; checked against, never parsed
└── src/span_panel_api_schema_1/
                             # Parent/child parser: ControllerRoutes, snapshot mapper,
                             # adoption, catalog validator, spec_lock.json, reference payloads
```

## Development

See [DEVELOPMENT.md](DEVELOPMENT.md) for setup, testing, and contribution guidelines.

## License

MIT License - see LICENSE file for details.
