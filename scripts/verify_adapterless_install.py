"""Verify the bootstrap distribution works with no adapter installed.

This is the acceptance check for the Phase 1 packaging split, and it cannot be
written as a unit test: the thing under test *is* the installed distribution
metadata — which wheel carries the entry point, and whether the bootstrap's
import graph reaches a parser. A test running in the development workspace
always has the adapter importable, so it can never observe the failure this
guards against.

Run it in a virtualenv that has ONLY span-panel-api installed:

    uv venv /tmp/bootstrap-only
    VIRTUAL_ENV=/tmp/bootstrap-only uv pip install dist/span_panel_api-*.whl
    VIRTUAL_ENV=/tmp/bootstrap-only uv run --no-project \
        python scripts/verify_adapterless_install.py

Exits non-zero with a description of the first failure.
"""

from __future__ import annotations

import sys


def _fail(message: str) -> None:
    print(f"FAIL: {message}", file=sys.stderr)
    raise SystemExit(1)


def main() -> None:
    # 1. The transport must import. Before the split this raised
    #    ModuleNotFoundError, because mqtt/__init__ and mqtt/client both reached
    #    into _impl/schema_0 at module scope.
    try:
        import span_panel_api  # noqa: F401
        from span_panel_api.mqtt.client import SpanMqttClient
    except ModuleNotFoundError as exc:
        _fail(f"bootstrap import reaches an adapter package: {exc}")

    # 2. No adapter should be discoverable. If one is, the bootstrap wheel is
    #    still carrying the entry point and the split did not actually happen.
    from span_panel_api.adapters import DEFAULT_ADAPTER_KEY, discover_adapters

    registry = discover_adapters()
    if registry:
        _fail(f"bootstrap-only install discovered adapters {sorted(registry)}; the entry point did not move")

    # 3. Constructing a client must still work — only building a parser needs an
    #    adapter. This is what keeps the failure at an actionable point.
    from span_panel_api.exceptions import SpanPanelAdapterMissingError
    from span_panel_api.mqtt.models import MqttClientConfig

    client = SpanMqttClient(
        "panel.local",
        "SERIAL123",
        MqttClientConfig(broker_host="broker.local", username="u", password="p"),
    )

    # 4. Building a parser must raise the named error, not an opaque one, and
    #    must say which adapter was wanted. A flat schema is used because that
    #    is the case a bootstrap-only install is expected to fail on: every
    #    panel in the field today reports no data-model-version, so dispatch
    #    asks for the default key and finds nothing providing it.
    from span_panel_api.models import V2HomieSchema

    flat_schema = V2HomieSchema(
        firmware_version="spanos2/r202603/05",
        types_schema_hash="sha256:0000000000000000",
        types={"energy.ebus.device.circuit": {"space": {"datatype": "integer", "format": "1:32:1"}}},
    )

    try:
        client._build_adapter(flat_schema)  # pylint: disable=protected-access
    except SpanPanelAdapterMissingError as exc:
        if exc.needed != DEFAULT_ADAPTER_KEY:
            _fail(f"error names adapter {exc.needed!r}, expected {DEFAULT_ADAPTER_KEY!r}")
        if exc.available:
            _fail(f"error reports installed adapters {exc.available} in a bootstrap-only install")
    except Exception as exc:  # pylint: disable=broad-exception-caught
        _fail(f"expected SpanPanelAdapterMissingError, got {type(exc).__name__}: {exc}")
    else:
        _fail("building a parser with no adapter installed did not raise")

    print(f"OK: span-panel-api {span_panel_api.__version__} imports and fails by name with no adapter installed")


if __name__ == "__main__":
    main()
