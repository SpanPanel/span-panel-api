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

# The other shape the same validation layer produces, and the one a key scan
# cannot see at all: pydantic v2 reports a *field-level* failure by pointing
# `loc` at the field and putting the rejected value itself under `input`. The
# credential is a bare string there, so nothing about the key it arrived under
# says "secret" — only the sibling `loc` does.
FIELD_LEVEL_422 = {
    "detail": [
        {
            "type": "string_too_short",
            "loc": ["body", "hopPassphrase"],
            "msg": "String should have at least 8 characters",
            "input": SECRET,
            "ctx": {"min_length": 8},
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


class TestRedactBySiblingLocation:
    """A scalar is judged by the `loc` beside it when its own key says nothing."""

    def test_redacts_a_scalar_input_named_by_loc(self) -> None:
        redacted = _redact(FIELD_LEVEL_422)
        assert SECRET not in json.dumps(redacted)

    def test_keeps_the_reason_the_field_was_rejected(self) -> None:
        rendered = json.dumps(_redact(FIELD_LEVEL_422))
        assert "string_too_short" in rendered
        assert "at least 8 characters" in rendered
        assert "hopPassphrase" in rendered

    def test_a_loc_naming_no_credential_leaves_its_input_alone(self) -> None:
        """The rule is scoped to credential fields — an ordinary 422 stays readable."""
        body = {"detail": [{"type": "missing", "loc": ["body", "name"], "msg": "Field required", "input": "ha"}]}
        assert _redact(body) == body

    def test_a_container_input_is_still_walked_not_flattened(self) -> None:
        """A nested echo keeps everything the key scan can clear individually."""
        rendered = json.dumps(_redact(VALIDATION_422))
        assert SECRET not in rendered
        assert "home-assistant-0badcafe" in rendered


class TestRedactByValue:
    """What the caller already knows is a secret is removed wherever it appears.

    Structure runs out: a validator is free to fold the rejected value into its
    own prose, and no key or `loc` marks that. The one call that sends a
    credential knows exactly what it sent, so it can say so.
    """

    def test_removes_the_secret_from_free_prose(self) -> None:
        body = {"detail": f"passphrase {SECRET} was rejected"}
        rendered = json.dumps(_redact(body, (SECRET,)))
        assert SECRET not in rendered
        assert "was rejected" in rendered

    def test_removes_the_secret_from_an_unnamed_scalar(self) -> None:
        assert _redact({"echo": SECRET}, (SECRET,)) == {"echo": "***"}

    def test_an_empty_secret_does_not_shred_the_body(self) -> None:
        """A door-bypass registration sends no passphrase; `""` must be inert."""
        assert _redact({"msg": "fine"}, ("",)) == {"msg": "fine"}

    def test_no_secrets_leaves_the_body_alone(self) -> None:
        assert _redact({"msg": "fine"}, ()) == {"msg": "fine"}


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
    async def test_a_field_level_422_does_not_log_the_rejected_passphrase(self, caplog: pytest.LogCaptureFixture) -> None:
        """The scalar echo, end to end: nothing about `input`'s key marks it secret."""
        with caplog.at_level(logging.DEBUG, logger="span_panel_api.auth"):
            exc = await _register_against(_response(422, json_data=FIELD_LEVEL_422))
        assert SECRET not in caplog.text
        assert SECRET not in str(exc)
        assert "at least 8 characters" in caplog.text

    @pytest.mark.asyncio
    async def test_the_passphrase_is_removed_wherever_the_panel_put_it(self, caplog: pytest.LogCaptureFixture) -> None:
        """No key and no `loc` names it — only the caller knows what it sent."""
        body = {"error": f"the passphrase {SECRET} is not the one on the label"}
        with caplog.at_level(logging.DEBUG, logger="span_panel_api.auth"):
            await _register_against(_response(401, json_data=body))
        assert SECRET not in caplog.text
        assert "not the one on the label" in caplog.text

    @pytest.mark.asyncio
    async def test_403_takes_the_same_path(self) -> None:
        exc = await _register_against(_response(403, json_data=VALIDATION_422))
        assert SECRET not in str(exc)
        assert "403" in str(exc)
