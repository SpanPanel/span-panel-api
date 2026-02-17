"""gRPC client for Gen3 SPAN panels (MAIN40 / MLO48).

Connects to the panel's TraitHandlerService on port 50065 (no authentication
required).  Discovers circuits via GetInstances, fetches names via
GetRevision, and streams real-time power metrics via Subscribe.

Manual protobuf encoding/decoding is used to avoid requiring generated stubs,
keeping the dependency footprint to a single optional ``grpcio`` package.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
import contextlib
import logging
import struct
from typing import Any

import grpc  # pylint: disable=import-error
import grpc.aio  # pylint: disable=import-error

from ..exceptions import SpanPanelGrpcConnectionError, SpanPanelGrpcError
from ..models import PanelCapability, PanelGeneration, SpanCircuitSnapshot, SpanPanelSnapshot
from .const import (
    BREAKER_OFF_VOLTAGE_MV,
    DEFAULT_GRPC_PORT,
    MAIN_FEED_IID,
    PRODUCT_GEN3_PANEL,
    TRAIT_CIRCUIT_NAMES,
    TRAIT_POWER_METRICS,
    VENDOR_SPAN,
)
from .models import CircuitInfo, CircuitMetrics, PanelData

_LOGGER = logging.getLogger(__name__)

# gRPC method paths
_SVC = "/io.span.panel.protocols.traithandler.TraitHandlerService"
_GET_INSTANCES = f"{_SVC}/GetInstances"
_SUBSCRIBE = f"{_SVC}/Subscribe"
_GET_REVISION = f"{_SVC}/GetRevision"

# ---------------------------------------------------------------------------
# Protobuf helpers — manual varint/field parsing
# ---------------------------------------------------------------------------

ProtobufValue = bytes | int


def _decode_varint(data: bytes, offset: int) -> tuple[int, int]:
    """Decode a protobuf varint, return (value, new_offset)."""
    result = 0
    shift = 0
    while offset < len(data):
        b = data[offset]
        offset += 1
        result |= (b & 0x7F) << shift
        if not b & 0x80:
            break
        shift += 7
    return result, offset


def _parse_protobuf_fields(data: bytes) -> dict[int, list[ProtobufValue]]:
    """Parse raw protobuf bytes into a dict of field_number -> [values]."""
    fields: dict[int, list[ProtobufValue]] = {}
    offset = 0
    while offset < len(data):
        tag, offset = _decode_varint(data, offset)
        field_num = tag >> 3
        wire_type = tag & 0x07

        value: ProtobufValue
        if wire_type == 0:  # varint
            int_val, offset = _decode_varint(data, offset)
            value = int_val
        elif wire_type == 1:  # 64-bit
            if offset + 8 > len(data):
                break
            value = struct.unpack_from("<Q", data, offset)[0]
            offset += 8
        elif wire_type == 2:  # length-delimited
            length, offset = _decode_varint(data, offset)
            if offset + length > len(data):
                break
            value = data[offset : offset + length]
            offset += length
        elif wire_type == 5:  # 32-bit
            if offset + 4 > len(data):
                break
            value = struct.unpack_from("<I", data, offset)[0]
            offset += 4
        else:
            break

        fields.setdefault(field_num, []).append(value)
    return fields


def _get_field(
    fields: dict[int, list[ProtobufValue]],
    num: int,
    default: ProtobufValue | None = None,
) -> ProtobufValue | None:
    """Return the first value for a field number, or *default*."""
    vals = fields.get(num)
    return vals[0] if vals else default


def _parse_min_max_avg(data: bytes) -> dict[str, int]:
    """Parse a min/max/avg sub-message (fields 1/2/3), returning raw int values."""
    fields = _parse_protobuf_fields(data)
    result: dict[str, int] = {}
    for key, field_num in (("min", 1), ("max", 2), ("avg", 3)):
        raw = _get_field(fields, field_num, 0)
        result[key] = raw if isinstance(raw, int) else 0
    return result


# ---------------------------------------------------------------------------
# Metric decoders — single-phase, dual-phase, and main feed
# ---------------------------------------------------------------------------


def _decode_single_phase(data: bytes) -> CircuitMetrics:
    """Decode single-phase (120 V) metrics from protobuf field 11."""
    fields = _parse_protobuf_fields(data)
    metrics = CircuitMetrics()

    current_data = _get_field(fields, 1)
    if isinstance(current_data, bytes):
        metrics.current_a = _parse_min_max_avg(current_data)["avg"] / 1000.0

    voltage_data = _get_field(fields, 2)
    if isinstance(voltage_data, bytes):
        metrics.voltage_v = _parse_min_max_avg(voltage_data)["avg"] / 1000.0

    power_data = _get_field(fields, 3)
    if isinstance(power_data, bytes):
        metrics.power_w = _parse_min_max_avg(power_data)["avg"] / 2000.0

    apparent_data = _get_field(fields, 4)
    if isinstance(apparent_data, bytes):
        metrics.apparent_power_va = _parse_min_max_avg(apparent_data)["avg"] / 2000.0

    reactive_data = _get_field(fields, 5)
    if isinstance(reactive_data, bytes):
        metrics.reactive_power_var = _parse_min_max_avg(reactive_data)["avg"] / 2000.0

    metrics.is_on = (metrics.voltage_v * 1000) > BREAKER_OFF_VOLTAGE_MV
    return metrics


def _decode_dual_phase(data: bytes) -> CircuitMetrics:
    """Decode dual-phase (240 V) metrics from protobuf field 12."""
    fields = _parse_protobuf_fields(data)
    metrics = CircuitMetrics()

    # Leg A (field 1)
    leg_a_data = _get_field(fields, 1)
    if isinstance(leg_a_data, bytes):
        leg_a = _parse_protobuf_fields(leg_a_data)
        current_data = _get_field(leg_a, 1)
        if isinstance(current_data, bytes):
            metrics.current_a_a = _parse_min_max_avg(current_data)["avg"] / 1000.0
        voltage_data = _get_field(leg_a, 2)
        if isinstance(voltage_data, bytes):
            metrics.voltage_a_v = _parse_min_max_avg(voltage_data)["avg"] / 1000.0

    # Leg B (field 2)
    leg_b_data = _get_field(fields, 2)
    if isinstance(leg_b_data, bytes):
        leg_b = _parse_protobuf_fields(leg_b_data)
        current_data = _get_field(leg_b, 1)
        if isinstance(current_data, bytes):
            metrics.current_b_a = _parse_min_max_avg(current_data)["avg"] / 1000.0
        voltage_data = _get_field(leg_b, 2)
        if isinstance(voltage_data, bytes):
            metrics.voltage_b_v = _parse_min_max_avg(voltage_data)["avg"] / 1000.0

    # Combined (field 3)
    combined_data = _get_field(fields, 3)
    if isinstance(combined_data, bytes):
        combined = _parse_protobuf_fields(combined_data)
        voltage_data = _get_field(combined, 2)
        if isinstance(voltage_data, bytes):
            metrics.voltage_v = _parse_min_max_avg(voltage_data)["avg"] / 1000.0
        power_data = _get_field(combined, 3)
        if isinstance(power_data, bytes):
            metrics.power_w = _parse_min_max_avg(power_data)["avg"] / 2000.0
        apparent_data = _get_field(combined, 4)
        if isinstance(apparent_data, bytes):
            metrics.apparent_power_va = _parse_min_max_avg(apparent_data)["avg"] / 2000.0
        reactive_data = _get_field(combined, 5)
        if isinstance(reactive_data, bytes):
            metrics.reactive_power_var = _parse_min_max_avg(reactive_data)["avg"] / 2000.0
        pf_data = _get_field(combined, 6)
        if isinstance(pf_data, bytes):
            metrics.power_factor = _parse_min_max_avg(pf_data)["avg"] / 2000.0

    # Frequency (field 4)
    freq_data = _get_field(fields, 4)
    if isinstance(freq_data, bytes):
        metrics.frequency_hz = _parse_min_max_avg(freq_data)["avg"] / 1000.0

    # Total current = leg A + leg B
    metrics.current_a = metrics.current_a_a + metrics.current_b_a

    metrics.is_on = (metrics.voltage_v * 1000) > BREAKER_OFF_VOLTAGE_MV
    return metrics


def _extract_deepest_value(data: bytes, target_field: int = 3) -> int:
    """Extract the largest non-zero varint value at *target_field* within nested sub-messages."""
    fields = _parse_protobuf_fields(data)
    best = 0
    for fn, vals in fields.items():
        for v in vals:
            if isinstance(v, bytes) and len(v) > 0:
                inner = _extract_deepest_value(v, target_field)
                best = max(best, inner)
            elif isinstance(v, int) and fn == target_field and v > best:
                best = v
    return best


def _decode_main_feed(data: bytes) -> CircuitMetrics:
    """Decode main feed metrics from protobuf field 14.

    Field 14 has deeper nesting than circuit fields 11/12.  Structure::

        14.1 = primary data block (leg A)
        14.2 = secondary data block (leg B)
        Each leg: {1: current stats, 2: voltage stats, 3: power stats, 4: frequency}
    """
    fields = _parse_protobuf_fields(data)
    main_data = _get_field(fields, 14)
    if not isinstance(main_data, bytes):
        return CircuitMetrics()

    metrics = CircuitMetrics()
    main_fields = _parse_protobuf_fields(main_data)

    # Primary data block (field 1 = leg A)
    leg_a = _get_field(main_fields, 1)
    if isinstance(leg_a, bytes):
        la_fields = _parse_protobuf_fields(leg_a)

        power_stats = _get_field(la_fields, 3)
        if isinstance(power_stats, bytes):
            metrics.power_w = _extract_deepest_value(power_stats) / 2000.0

        voltage_stats = _get_field(la_fields, 2)
        if isinstance(voltage_stats, bytes):
            vs_fields = _parse_protobuf_fields(voltage_stats)
            f2 = _get_field(vs_fields, 2)
            if isinstance(f2, bytes):
                inner = _parse_protobuf_fields(f2)
                v = _get_field(inner, 3, 0)
                if isinstance(v, int) and v > 0:
                    metrics.voltage_a_v = v / 1000.0

        freq_stats = _get_field(la_fields, 4)
        if isinstance(freq_stats, bytes):
            freq_fields = _parse_protobuf_fields(freq_stats)
            freq_val = _get_field(freq_fields, 3, 0)
            if isinstance(freq_val, int) and freq_val > 0:
                metrics.frequency_hz = freq_val / 1000.0

    # Leg B (field 2)
    leg_b = _get_field(main_fields, 2)
    if isinstance(leg_b, bytes):
        lb_fields = _parse_protobuf_fields(leg_b)
        power_stats = _get_field(lb_fields, 3)
        if isinstance(power_stats, bytes):
            lb_power = _extract_deepest_value(power_stats) / 2000.0
            if lb_power > 0:
                metrics.power_w += lb_power
        voltage_stats = _get_field(lb_fields, 2)
        if isinstance(voltage_stats, bytes):
            vs_fields = _parse_protobuf_fields(voltage_stats)
            f2 = _get_field(vs_fields, 2)
            if isinstance(f2, bytes):
                inner = _parse_protobuf_fields(f2)
                v = _get_field(inner, 3, 0)
                if isinstance(v, int) and v > 0:
                    metrics.voltage_b_v = v / 1000.0

    # Combined voltage (split-phase: leg A + leg B, or 2x leg A if symmetric)
    if metrics.voltage_b_v > 0:
        metrics.voltage_v = metrics.voltage_a_v + metrics.voltage_b_v
    else:
        metrics.voltage_v = metrics.voltage_a_v * 2

    # Derive current from power and voltage
    if metrics.voltage_v > 0:
        metrics.current_a = metrics.power_w / metrics.voltage_v

    metrics.is_on = True
    return metrics


# ---------------------------------------------------------------------------
# Protobuf encoding helpers
# ---------------------------------------------------------------------------


def _encode_varint(value: int) -> bytes:
    """Encode an integer as a protobuf varint."""
    parts: list[int] = []
    while value > 0x7F:
        parts.append((value & 0x7F) | 0x80)
        value >>= 7
    parts.append(value & 0x7F)
    return bytes(parts) if parts else b"\x00"


def _encode_varint_field(field_num: int, value: int) -> bytes:
    """Encode a varint field (tag + value)."""
    tag = (field_num << 3) | 0  # wire type 0 = varint
    return _encode_varint(tag) + _encode_varint(value)


def _encode_bytes_field(field_num: int, value: bytes) -> bytes:
    """Encode a length-delimited field (tag + length + value)."""
    tag = (field_num << 3) | 2  # wire type 2 = length-delimited
    return _encode_varint(tag) + _encode_varint(len(value)) + value


def _encode_string_field(field_num: int, value: str) -> bytes:
    """Encode a string field (tag + length + utf-8 bytes)."""
    return _encode_bytes_field(field_num, value.encode("utf-8"))


# ---------------------------------------------------------------------------
# gRPC Client
# ---------------------------------------------------------------------------


class SpanGrpcClient:
    """gRPC client for Gen3 SPAN panels.

    Connects to the panel's TraitHandlerService on port 50065 (no auth).
    Discovers circuits via GetInstances, fetches names via GetRevision,
    and streams real-time power metrics via Subscribe.

    Satisfies :class:`~span_panel_api.protocol.SpanPanelClientProtocol` and
    :class:`~span_panel_api.protocol.StreamingCapableProtocol`.
    """

    def __init__(self, host: str, port: int = DEFAULT_GRPC_PORT) -> None:
        self._host = host
        self._port = port
        self._channel: Any = None  # grpc.aio.Channel — optional dep
        self._stream_task: asyncio.Task[None] | None = None
        self._data = PanelData()
        self._callbacks: list[Callable[[], None]] = []
        self._connected = False
        # Reverse map built at connect time: metric IID -> positional circuit_id
        self._metric_iid_to_circuit: dict[int, int] = {}

    # ------------------------------------------------------------------
    # SpanPanelClientProtocol implementation
    # ------------------------------------------------------------------

    @property
    def capabilities(self) -> PanelCapability:
        """Return the capability flags for this Gen3 transport."""
        return PanelCapability.GEN3_INITIAL

    async def connect(self) -> bool:
        """Connect to the panel and perform initial circuit discovery."""
        try:
            self._channel = grpc.aio.insecure_channel(
                f"{self._host}:{self._port}",
                options=[
                    ("grpc.keepalive_time_ms", 30000),
                    ("grpc.keepalive_timeout_ms", 10000),
                    ("grpc.keepalive_permit_without_calls", True),
                ],
            )
            await self._fetch_instances()
            await self._fetch_circuit_names()
            self._connected = True
            _LOGGER.info(
                "Connected to Gen3 panel at %s:%s — %d circuits discovered",
                self._host,
                self._port,
                len(self._data.circuits),
            )
            return True
        except Exception:  # pylint: disable=broad-exception-caught
            _LOGGER.exception("Failed to connect to Gen3 panel at %s:%s", self._host, self._port)
            self._connected = False
            return False

    async def close(self) -> None:
        """Close the connection and cancel the streaming task."""
        await self._disconnect()

    async def ping(self) -> bool:
        """Return True if the panel is reachable via gRPC."""
        return await self.test_connection()

    async def get_snapshot(self) -> SpanPanelSnapshot:
        """Return the current streaming data as a unified transport-agnostic snapshot."""
        data = self._data
        circuits: dict[str, SpanCircuitSnapshot] = {}
        for cid, info in data.circuits.items():
            m = data.metrics.get(cid, CircuitMetrics())
            circuits[str(cid)] = SpanCircuitSnapshot(
                circuit_id=str(cid),
                name=info.name,
                power_w=m.power_w,
                voltage_v=m.voltage_v,
                current_a=m.current_a,
                is_on=m.is_on,
                is_dual_phase=info.is_dual_phase,
                apparent_power_va=m.apparent_power_va,
                reactive_power_var=m.reactive_power_var,
                frequency_hz=m.frequency_hz,
                power_factor=m.power_factor,
            )
        return SpanPanelSnapshot(
            panel_generation=PanelGeneration.GEN3,
            serial_number=data.serial,
            firmware_version=data.firmware,
            circuits=circuits,
            main_power_w=data.main_feed.power_w,
            main_voltage_v=data.main_feed.voltage_v,
            main_current_a=data.main_feed.current_a,
            main_frequency_hz=data.main_feed.frequency_hz,
        )

    # ------------------------------------------------------------------
    # StreamingCapableProtocol implementation
    # ------------------------------------------------------------------

    def register_callback(self, cb: Callable[[], None]) -> Callable[[], None]:
        """Register a callback invoked on every streaming update.

        Returns an unregister function; call it to remove the callback.
        """
        self._callbacks.append(cb)

        def unregister() -> None:
            self._callbacks.remove(cb)

        return unregister

    async def start_streaming(self) -> None:
        """Start the metric streaming background task."""
        if self._stream_task and not self._stream_task.done():
            return
        self._stream_task = asyncio.create_task(self._stream_loop())

    async def stop_streaming(self) -> None:
        """Stop the metric streaming background task."""
        if self._stream_task and not self._stream_task.done():
            self._stream_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._stream_task

    # ------------------------------------------------------------------
    # Additional helpers
    # ------------------------------------------------------------------

    @property
    def data(self) -> PanelData:
        """Return the raw panel data (circuit topology + latest metrics)."""
        return self._data

    @property
    def connected(self) -> bool:
        """Return True if the client is currently connected."""
        return self._connected

    async def test_connection(self) -> bool:
        """Test whether the panel is reachable without a full connect().

        Opens a temporary channel, sends a GetInstances probe, and closes
        the channel — suitable for auto-detection in the factory.
        """
        try:
            channel = grpc.aio.insecure_channel(
                f"{self._host}:{self._port}",
                options=[("grpc.initial_reconnect_backoff_ms", 1000)],
            )
            try:
                response: bytes = await asyncio.wait_for(
                    channel.unary_unary(_GET_INSTANCES)(b""),
                    timeout=5.0,
                )
                return len(response) > 0
            finally:
                await channel.close()
        except Exception:  # pylint: disable=broad-exception-caught
            return False

    # ------------------------------------------------------------------
    # Internal: disconnect
    # ------------------------------------------------------------------

    async def _disconnect(self) -> None:
        """Internal disconnect helper."""
        self._connected = False
        await self.stop_streaming()
        if self._channel is not None:
            await self._channel.close()
            self._channel = None

    # ------------------------------------------------------------------
    # Internal: instance discovery
    # ------------------------------------------------------------------

    async def _fetch_instances(self) -> None:
        """Fetch all trait instances to discover circuit topology."""
        if self._channel is None:
            raise SpanPanelGrpcError("Channel is not open")
        response: bytes = await self._channel.unary_unary(_GET_INSTANCES)(b"")
        self._parse_instances(response)

    def _parse_instances(self, data: bytes) -> None:
        """Parse GetInstancesResponse to discover circuit topology via positional pairing.

        Trait 16 IIDs (circuit names) and trait 26 IIDs (power metrics) are collected
        independently, sorted, deduplicated, and paired by position. This avoids any
        fixed IID offset assumption — the offset varies between panel models and firmware
        versions (e.g. MAIN40 uses offset ~27, MLO48 uses different offsets).
        """
        fields = _parse_protobuf_fields(data)
        items = fields.get(1, [])

        raw_name_iids: list[int] = []
        raw_metric_iids: list[int] = []

        for item_data in items:
            if not isinstance(item_data, bytes):
                continue
            item_fields = _parse_protobuf_fields(item_data)

            trait_info_data = _get_field(item_fields, 1)
            if not isinstance(trait_info_data, bytes):
                continue

            trait_info_fields = _parse_protobuf_fields(trait_info_data)

            external_data = _get_field(trait_info_fields, 2)
            if not isinstance(external_data, bytes):
                continue

            ext_fields = _parse_protobuf_fields(external_data)

            # resource_id (field 1)
            resource_data = _get_field(ext_fields, 1)
            resource_id_str = ""
            if isinstance(resource_data, bytes):
                rid_fields = _parse_protobuf_fields(resource_data)
                rid_val = _get_field(rid_fields, 1)
                if isinstance(rid_val, bytes):
                    resource_id_str = rid_val.decode("utf-8", errors="replace")

            # trait_info (field 2)
            inner_info = _get_field(ext_fields, 2)
            if not isinstance(inner_info, bytes):
                continue

            inner_fields = _parse_protobuf_fields(inner_info)

            meta_data = _get_field(inner_fields, 1)
            if not isinstance(meta_data, bytes):
                continue

            meta_fields = _parse_protobuf_fields(meta_data)
            vendor_id_raw = _get_field(meta_fields, 1, 0)
            product_id_raw = _get_field(meta_fields, 2, 0)
            trait_id_raw = _get_field(meta_fields, 3, 0)
            vendor_id = vendor_id_raw if isinstance(vendor_id_raw, int) else 0
            product_id = product_id_raw if isinstance(product_id_raw, int) else 0
            trait_id = trait_id_raw if isinstance(trait_id_raw, int) else 0

            instance_data = _get_field(inner_fields, 2)
            instance_id = 0
            if isinstance(instance_data, bytes):
                iid_fields = _parse_protobuf_fields(instance_data)
                iid_raw = _get_field(iid_fields, 1, 0)
                instance_id = iid_raw if isinstance(iid_raw, int) else 0

            # Capture panel resource_id
            if product_id == PRODUCT_GEN3_PANEL and resource_id_str and not self._data.panel_resource_id:
                self._data.panel_resource_id = resource_id_str

            if vendor_id != VENDOR_SPAN or instance_id <= 0:
                continue

            if trait_id == TRAIT_CIRCUIT_NAMES:
                raw_name_iids.append(instance_id)
            elif trait_id == TRAIT_POWER_METRICS and instance_id != MAIN_FEED_IID:
                raw_metric_iids.append(instance_id)

        # Deduplicate and sort both IID lists before pairing
        name_iids = sorted(set(raw_name_iids))
        metric_iids = sorted(set(raw_metric_iids))

        _LOGGER.debug(
            "Discovered %d name instances (trait 16) and %d metric instances (trait 26, excl main feed). "
            "Name IIDs: %s, Metric IIDs: %s",
            len(name_iids),
            len(metric_iids),
            name_iids[:10],
            metric_iids[:10],
        )
        if len(name_iids) != len(metric_iids):
            _LOGGER.warning(
                "Trait 16 has %d instances but trait 26 has %d — pairing by position (some circuits may be unnamed)",
                len(name_iids),
                len(metric_iids),
            )

        # Pair by position: circuit_id is a stable 1-based positional index
        for idx, metric_iid in enumerate(metric_iids):
            circuit_id = idx + 1
            name_iid = name_iids[idx] if idx < len(name_iids) else 0
            self._data.circuits[circuit_id] = CircuitInfo(
                circuit_id=circuit_id,
                name=f"Circuit {circuit_id}",
                metric_iid=metric_iid,
                name_iid=name_iid,
            )

        # Reverse map for O(1) lookup during streaming
        self._metric_iid_to_circuit = {info.metric_iid: cid for cid, info in self._data.circuits.items()}

    # ------------------------------------------------------------------
    # Internal: circuit names
    # ------------------------------------------------------------------

    async def _fetch_circuit_names(self) -> None:
        """Fetch circuit names from trait 16 via GetRevision."""
        for circuit_id, info in list(self._data.circuits.items()):
            if info.name_iid == 0:
                continue
            try:
                name = await self._get_circuit_name_by_iid(info.name_iid)
                if name:
                    self._data.circuits[circuit_id].name = name
            except Exception:  # pylint: disable=broad-exception-caught
                _LOGGER.debug("Failed to get name for circuit %d (name_iid=%d)", circuit_id, info.name_iid)

    async def _get_circuit_name_by_iid(self, name_iid: int) -> str | None:
        """Get a single circuit name via GetRevision on trait 16 using the trait instance ID."""
        if self._channel is None:
            return None
        request = self._build_get_revision_request(
            vendor_id=VENDOR_SPAN,
            product_id=PRODUCT_GEN3_PANEL,
            trait_id=TRAIT_CIRCUIT_NAMES,
            instance_id=name_iid,
        )
        try:
            response: bytes = await self._channel.unary_unary(_GET_REVISION)(request)
            return self._parse_circuit_name(response)
        except Exception:  # pylint: disable=broad-exception-caught
            return None

    def _build_get_revision_request(
        self,
        vendor_id: int,
        product_id: int,
        trait_id: int,
        instance_id: int,
    ) -> bytes:
        """Build a GetRevisionRequest protobuf message."""
        meta = _encode_varint_field(1, vendor_id)
        meta += _encode_varint_field(2, product_id)
        meta += _encode_varint_field(3, trait_id)
        meta += _encode_varint_field(4, 1)  # version

        resource_id_msg = _encode_string_field(1, self._data.panel_resource_id)

        iid_msg = _encode_varint_field(1, instance_id)
        instance_meta = _encode_bytes_field(1, resource_id_msg)
        instance_meta += _encode_bytes_field(2, iid_msg)

        req_metadata = _encode_bytes_field(2, resource_id_msg)
        revision_request = _encode_bytes_field(1, req_metadata)

        result = _encode_bytes_field(1, meta)
        result += _encode_bytes_field(2, instance_meta)
        result += _encode_bytes_field(3, revision_request)
        return result

    @staticmethod
    def _parse_circuit_name(data: bytes) -> str | None:
        """Parse circuit name from GetRevision response."""
        fields = _parse_protobuf_fields(data)

        sr_data = _get_field(fields, 3)
        if not isinstance(sr_data, bytes):
            return None

        sr_fields = _parse_protobuf_fields(sr_data)
        payload_data = _get_field(sr_fields, 2)
        if not isinstance(payload_data, bytes):
            return None

        pl_fields = _parse_protobuf_fields(payload_data)
        raw = _get_field(pl_fields, 1)
        if not isinstance(raw, bytes):
            return None

        name_fields = _parse_protobuf_fields(raw)
        name = _get_field(name_fields, 4)
        if isinstance(name, bytes):
            return name.decode("utf-8", errors="replace").strip()
        return None

    # ------------------------------------------------------------------
    # Internal: metric streaming
    # ------------------------------------------------------------------

    async def _stream_loop(self) -> None:
        """Streaming loop with automatic reconnection on errors."""
        while self._connected:
            try:
                await self._subscribe_stream()
            except asyncio.CancelledError:
                return
            except Exception:  # pylint: disable=broad-exception-caught
                _LOGGER.exception("Stream error, reconnecting in 5 s")
                await asyncio.sleep(5)

    async def _subscribe_stream(self) -> None:
        """Subscribe to the gRPC stream and dispatch notifications."""
        if self._channel is None:
            raise SpanPanelGrpcConnectionError("Channel is not open")
        stream = self._channel.unary_stream(_SUBSCRIBE)(b"")
        async for response in stream:
            try:
                self._process_notification(response)
            except Exception:  # pylint: disable=broad-exception-caught
                _LOGGER.debug("Error processing notification", exc_info=True)

    def _notify(self) -> None:
        """Invoke all registered callbacks."""
        for cb in self._callbacks:
            try:
                cb()
            except Exception:  # pylint: disable=broad-exception-caught
                _LOGGER.exception("Error in registered callback")

    def _process_notification(self, data: bytes) -> None:
        """Process a TraitInstanceNotification from the Subscribe stream."""
        fields = _parse_protobuf_fields(data)

        rti_data = _get_field(fields, 1)
        if not isinstance(rti_data, bytes):
            return

        rti_fields = _parse_protobuf_fields(rti_data)
        ext_data = _get_field(rti_fields, 2)
        if not isinstance(ext_data, bytes):
            return

        ext_fields = _parse_protobuf_fields(ext_data)
        info_data = _get_field(ext_fields, 2)
        if not isinstance(info_data, bytes):
            return

        info_fields = _parse_protobuf_fields(info_data)
        meta_data = _get_field(info_fields, 1)
        if not isinstance(meta_data, bytes):
            return

        meta_fields = _parse_protobuf_fields(meta_data)
        trait_id_raw = _get_field(meta_fields, 3, 0)
        trait_id = trait_id_raw if isinstance(trait_id_raw, int) else 0

        iid_data = _get_field(info_fields, 2)
        instance_id = 0
        if isinstance(iid_data, bytes):
            iid_fields = _parse_protobuf_fields(iid_data)
            iid_raw = _get_field(iid_fields, 1, 0)
            instance_id = iid_raw if isinstance(iid_raw, int) else 0

        # Only process trait 26 (power metrics)
        if trait_id != TRAIT_POWER_METRICS:
            return

        notify_data = _get_field(fields, 2)
        if not isinstance(notify_data, bytes):
            return

        notify_fields = _parse_protobuf_fields(notify_data)

        for metric_data in notify_fields.get(3, []):
            if not isinstance(metric_data, bytes):
                continue
            ml_fields = _parse_protobuf_fields(metric_data)
            for raw in ml_fields.get(3, []):
                if isinstance(raw, bytes):
                    self._decode_and_store_metric(instance_id, raw)

        self._notify()

    def _decode_and_store_metric(self, iid: int, raw: bytes) -> None:
        """Decode a raw metric payload and store it in self._data."""
        # Main feed (IID 1) uses field 14 with deeper nesting
        if iid == MAIN_FEED_IID:
            self._data.main_feed = _decode_main_feed(raw)
            return

        circuit_id = self._metric_iid_to_circuit.get(iid)
        if circuit_id is None:
            return

        top_fields = _parse_protobuf_fields(raw)

        # Dual-phase (field 12) — check first (more specific)
        dual_data = _get_field(top_fields, 12)
        if isinstance(dual_data, bytes):
            self._data.metrics[circuit_id] = _decode_dual_phase(dual_data)
            self._data.circuits[circuit_id].is_dual_phase = True
            return

        # Single-phase (field 11)
        single_data = _get_field(top_fields, 11)
        if isinstance(single_data, bytes):
            self._data.metrics[circuit_id] = _decode_single_phase(single_data)
            self._data.circuits[circuit_id].is_dual_phase = False
