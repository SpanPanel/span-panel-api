"""What the divergent registration acceptance tests assert after their divergent line, and more.

`expected_failures.py` lists two tests in `test_register_rate_limit.py` as
divergent: they read the delay as `retry_after` and expect `None` for an
HTTP-date, where this library reports `retry_after_s` in seconds and reads an
HTTP-date as the seconds until then. A strict xfail stops at that line, so the
cases here pin the delay and the statuses around 429, over the same responses.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from email.utils import format_datetime
import json
from unittest.mock import AsyncMock

import httpx
import pytest

from span_panel_api import SpanPanelRateLimitError
from span_panel_api.auth import _retry_delay
from span_panel_api.exceptions import SpanPanelAPIError, SpanPanelAuthError
from test_register_rate_limit import HOST, _client, _register


def _future(seconds: float) -> str:
    """An HTTP-date `seconds` from now."""
    return format_datetime(datetime.now(UTC) + timedelta(seconds=seconds), usegmt=True)


class TestRetryAfterSeconds:
    @pytest.mark.asyncio
    async def test_delta_seconds_are_the_delay_and_the_status_is_429(self) -> None:
        with pytest.raises(SpanPanelRateLimitError) as caught:
            await _register(_client({"Retry-After": "30"}))
        assert caught.value.status_code == 429
        assert caught.value.retry_after_s == 30.0

    @pytest.mark.asyncio
    async def test_an_http_date_is_the_seconds_until_then(self) -> None:
        with pytest.raises(SpanPanelRateLimitError) as caught:
            await _register(_client({"Retry-After": _future(120)}))
        assert caught.value.retry_after_s == pytest.approx(120, abs=5)

    @pytest.mark.asyncio
    async def test_an_http_date_already_passed_is_no_wait(self) -> None:
        with pytest.raises(SpanPanelRateLimitError) as caught:
            await _register(_client({"Retry-After": "Wed, 21 Oct 2015 07:28:00 GMT"}))
        assert caught.value.retry_after_s == 0.0

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "headers",
        [{}, {"Retry-After": "-5"}, {"Retry-After": "inf"}, {"Retry-After": "nan"}, {"Retry-After": "soon"}],
        ids=["absent", "negative", "infinite", "nan", "neither-form"],
    )
    async def test_the_delay_is_none_without_a_usable_header(self, headers: dict[str, str]) -> None:
        with pytest.raises(SpanPanelRateLimitError) as caught:
            await _register(_client(headers))
        assert caught.value.retry_after_s is None


class TestOtherStatusesUnchanged:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("status", [401, 403, 422])
    async def test_a_rejected_credential_is_still_an_auth_error(self, status: int) -> None:
        with pytest.raises(SpanPanelAuthError):
            await _register(_answering(status))

    @pytest.mark.asyncio
    async def test_another_4xx_is_still_an_api_error_and_not_a_rate_limit(self) -> None:
        with pytest.raises(SpanPanelAPIError) as caught:
            await _register(_answering(400))
        assert not isinstance(caught.value, SpanPanelRateLimitError)


def _answering(status: int) -> AsyncMock:
    response = httpx.Response(
        status,
        content=json.dumps({"detail": "refused"}).encode(),
        headers={"content-type": "application/json"},
        request=httpx.Request("POST", f"http://{HOST}/api/v2/auth/register"),
    )
    injected = AsyncMock(spec=httpx.AsyncClient)
    injected.post = AsyncMock(return_value=response)
    return injected


def test_the_retry_backoff_takes_an_http_date_and_never_waits_forever() -> None:
    """The CA download's 429 retry reads `Retry-After` through the same parser."""
    assert _retry_delay(_future(60), attempt=1, backoff_s=1.5) == pytest.approx(60, abs=5)
    assert _retry_delay("inf", attempt=2, backoff_s=1.5) == 3.0
