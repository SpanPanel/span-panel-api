"""Registration refused with HTTP 429.

The panel limits registrations per client address and answers 429 with a
`RateLimitOut` body once the limit is reached. The library does not retry; it
raises a distinct class carrying `Retry-After` so the caller can decide when.
"""

from __future__ import annotations

import json
import logging
from unittest.mock import AsyncMock

import httpx
import pytest

from span_panel_api import SpanPanelRateLimitError
from span_panel_api.auth import register_v2
from span_panel_api.exceptions import SpanPanelAPIError, SpanPanelAuthError

HOST = "panel.invalid"
SECRET = "correct-horse-battery-staple"


def _client(headers: dict[str, str] | None = None, body: object | None = None) -> AsyncMock:
    payload = {"error": "rate limit exceeded"} if body is None else body
    response = httpx.Response(
        429,
        content=json.dumps(payload).encode(),
        headers={"content-type": "application/json", **(headers or {})},
        request=httpx.Request("POST", f"http://{HOST}/api/v2/auth/register"),
    )
    injected = AsyncMock(spec=httpx.AsyncClient)
    injected.post = AsyncMock(return_value=response)
    return injected


async def _register(client: AsyncMock) -> None:
    await register_v2(HOST, "home-assistant", SECRET, httpx_client=client)


class TestRateLimited:
    @pytest.mark.asyncio
    async def test_raises_the_rate_limit_class_with_its_status(self) -> None:
        with pytest.raises(SpanPanelRateLimitError) as caught:
            await _register(_client({"Retry-After": "30"}))
        assert caught.value.status_code == 429
        assert caught.value.retry_after == 30.0

    @pytest.mark.asyncio
    async def test_is_an_api_error_and_not_an_auth_error(self) -> None:
        """Existing `except SpanPanelAPIError` clauses keep catching it; nothing calls the passphrase wrong."""
        with pytest.raises(SpanPanelAPIError) as caught:
            await _register(_client())
        assert not isinstance(caught.value, SpanPanelAuthError)

    @pytest.mark.asyncio
    async def test_is_not_retried(self) -> None:
        client = _client({"Retry-After": "1"})
        with pytest.raises(SpanPanelRateLimitError):
            await _register(client)
        assert client.post.await_count == 1

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "headers",
        [
            {},
            {"Retry-After": "Wed, 21 Oct 2026 07:28:00 GMT"},
            {"Retry-After": "-5"},
            {"Retry-After": "inf"},
            {"Retry-After": "nan"},
        ],
        ids=["absent", "http-date", "negative", "infinite", "nan"],
    )
    async def test_retry_after_is_none_without_a_usable_header(self, headers: dict[str, str]) -> None:
        with pytest.raises(SpanPanelRateLimitError) as caught:
            await _register(_client(headers))
        assert caught.value.retry_after is None

    @pytest.mark.asyncio
    async def test_the_body_stays_out_of_the_message(self, caplog: pytest.LogCaptureFixture) -> None:
        body = {"error": f"limit for {SECRET}"}
        with caplog.at_level(logging.DEBUG, logger="span_panel_api.auth"), pytest.raises(SpanPanelRateLimitError) as caught:
            await _register(_client(body=body))
        assert "limit for" not in str(caught.value)
        assert SECRET not in caplog.text
