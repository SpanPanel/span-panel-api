"""Tests for the span_panel_api exception hierarchy."""

from __future__ import annotations

from span_panel_api.exceptions import SpanPanelError


def test_stale_data_error_derives_from_span_panel_error() -> None:
    from span_panel_api.exceptions import SpanPanelStaleDataError

    err = SpanPanelStaleDataError("example")
    assert isinstance(err, SpanPanelError)
    assert str(err) == "example"


def test_stale_data_error_is_distinct_from_connection_error() -> None:
    from span_panel_api.exceptions import (
        SpanPanelConnectionError,
        SpanPanelStaleDataError,
    )

    err = SpanPanelStaleDataError("example")
    assert not isinstance(err, SpanPanelConnectionError)
