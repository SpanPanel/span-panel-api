"""The auth-failure path must never carry a credential out of the library.

`register_v2` is the one call that sends the panel passphrase, and the panel's
validation layer echoes what it rejected. Everything here is about that echo:
that it does not reach the exception message, and that where it *is* kept — the
DEBUG log, which a user chasing a registration failure needs — the credential is
gone and the surrounding detail is not.
"""

from __future__ import annotations

import json
import logging
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from span_panel_api.auth import _redact, register_v2
from span_panel_api.exceptions import SpanPanelAuthError

# The shape a FastAPI-style 422 takes: the submitted body is echoed back under
# `detail[].input`, which is two levels below anything a top-level key scan sees.
SECRET = "correct-horse-battery-staple"

VALIDATION_422 = {
    "detail": [
        {
            "type": "value_error",
            "loc": ["body", "hopPassphrase"],
            "msg": "Value error, passphrase does not match",
            "input": {"name": "home-assistant-0badcafe", "hopPassphrase": SECRET},
        }
    ]
}


def _response(status_code: int, *, json_data: object | None = None, text: str = "") -> httpx.Response:
    if json_data is not None:
        return httpx.Response(
            status_code=status_code,
            content=json.dumps(json_data).encode(),
            headers={"content-type": "application/json"},
            request=httpx.Request("POST", "http://panel.invalid/api/v2/auth/register"),
        )
    return httpx.Response(
        status_code=status_code,
        content=text.encode(),
        headers={"content-type": "text/html"},
        request=httpx.Request("POST", "http://panel.invalid/api/v2/auth/register"),
    )


async def _register_against(response: httpx.Response) -> SpanPanelAuthError:
    """Drive `register_v2` against one canned response and return what it raised."""
    injected = AsyncMock(spec=httpx.AsyncClient)
    injected.post = AsyncMock(return_value=response)
    with pytest.raises(SpanPanelAuthError) as excinfo:
        await register_v2("panel.invalid", "home-assistant", SECRET, httpx_client=injected)
    return excinfo.value


class TestRedactWalk:
    def test_redacts_nested_credential(self) -> None:
        redacted = _redact(VALIDATION_422)
        assert SECRET not in json.dumps(redacted)

    def test_keeps_the_diagnostic_around_the_credential(self) -> None:
        """Redaction must not flatten the body — the `loc` is what makes it useful."""
        redacted = _redact(VALIDATION_422)
        rendered = json.dumps(redacted)
        assert "hopPassphrase" in rendered  # the key, as a location, is not a secret
        assert "value_error" in rendered
        assert "home-assistant-0badcafe" in rendered

    def test_case_insensitive_keys(self) -> None:
        assert _redact({"HopPassphrase": SECRET, "ACCESSTOKEN": "jwt"}) == {
            "HopPassphrase": "***",
            "ACCESSTOKEN": "***",
        }

    def test_walks_lists_of_lists(self) -> None:
        assert _redact([[{"ebusBrokerPassword": SECRET}]]) == [[{"ebusBrokerPassword": "***"}]]

    def test_scalars_pass_through(self) -> None:
        assert _redact(7) == 7
        assert _redact(None) is None
        assert _redact("plain") == "plain"


class TestRegisterV2AuthFailure:
    @pytest.mark.asyncio
    async def test_secret_is_not_in_the_exception(self) -> None:
        exc = await _register_against(_response(422, json_data=VALIDATION_422))
        assert SECRET not in str(exc)
        assert "422" in str(exc)

    @pytest.mark.asyncio
    async def test_secret_is_not_logged_at_info_or_above(self, caplog: pytest.LogCaptureFixture) -> None:
        with caplog.at_level(logging.INFO, logger="span_panel_api.auth"):
            await _register_against(_response(422, json_data=VALIDATION_422))
        assert SECRET not in caplog.text

    @pytest.mark.asyncio
    async def test_debug_keeps_the_body_with_the_credential_removed(self, caplog: pytest.LogCaptureFixture) -> None:
        with caplog.at_level(logging.DEBUG, logger="span_panel_api.auth"):
            await _register_against(_response(422, json_data=VALIDATION_422))
        assert SECRET not in caplog.text
        # The reason the body is kept at all: it names the field that failed.
        assert "passphrase does not match" in caplog.text

    @pytest.mark.asyncio
    async def test_non_json_body_is_described_not_shown(self, caplog: pytest.LogCaptureFixture) -> None:
        """A proxy's HTML error page has no structure to redact, so only its shape is logged."""
        body = f"<html><body>rejected {SECRET}</body></html>"
        with caplog.at_level(logging.DEBUG, logger="span_panel_api.auth"):
            exc = await _register_against(_response(401, text=body))
        assert SECRET not in caplog.text
        assert SECRET not in str(exc)
        assert str(len(body.encode())) in caplog.text
        assert "text/html" in caplog.text

    @pytest.mark.asyncio
    async def test_403_takes_the_same_path(self) -> None:
        exc = await _register_against(_response(403, json_data=VALIDATION_422))
        assert SECRET not in str(exc)
        assert "403" in str(exc)
