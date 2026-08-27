"""Bootstrapping a client over plaintext HTTP says so.

Without an `ssl_context` every bootstrap request is plain `http://`, and
registration is the one that carries the panel passphrase up and brings the
broker password back — so anything on the path between the host and the panel
reads both. That is the default, and it stays the default, because requiring a
pin would break every install on upgrade. But the MQTT bridge already warns once
when its trust anchor is unpinned, and the REST side said nothing at all: a
security property that is off by default is only a decision if the operator can
tell it is off.
"""

from __future__ import annotations

import json
import logging
import ssl
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from span_panel_api.auth import register_v2
from span_panel_api.mqtt.models import MqttClientConfig

from conftest import flat_schema

HOST = "panel.invalid"

V2_AUTH_JSON = {
    "accessToken": "jwt",
    "tokenType": "Bearer",
    "iatMs": 1700000000000,
    "ebusBrokerUsername": "user",
    "ebusBrokerPassword": "pass",
    "ebusBrokerHost": HOST,
    "ebusBrokerMqttsPort": 8883,
    "ebusBrokerWsPort": 9001,
    "ebusBrokerWssPort": 9002,
    "hostname": "spanpanel",
    "serialNumber": "SYN-0000-0001",
    "hopPassphrase": "hop",
}


def _auth_client() -> AsyncMock:
    response = httpx.Response(
        200,
        content=json.dumps(V2_AUTH_JSON).encode(),
        headers={"content-type": "application/json"},
        request=httpx.Request("POST", f"http://{HOST}/api/v2/auth/register"),
    )
    injected = AsyncMock(spec=httpx.AsyncClient)
    injected.post = AsyncMock(return_value=response)
    return injected


def _warnings(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [record.getMessage() for record in caplog.records if record.levelno >= logging.WARNING]


class TestRegisterV2:
    @pytest.mark.asyncio
    async def test_plaintext_registration_warns_once(self, caplog: pytest.LogCaptureFixture) -> None:
        with caplog.at_level(logging.WARNING):
            await register_v2(HOST, "home-assistant", "secret", httpx_client=_auth_client())
        warnings = _warnings(caplog)
        assert len(warnings) == 1
        assert HOST in warnings[0]
        assert "plaintext" in warnings[0]
        assert "ssl_context" in warnings[0]

    @pytest.mark.asyncio
    async def test_the_warning_never_repeats_the_credential(self, caplog: pytest.LogCaptureFixture) -> None:
        """It is a warning about a credential, not a place to put one."""
        with caplog.at_level(logging.WARNING):
            await register_v2(HOST, "home-assistant", "correct-horse-battery-staple", httpx_client=_auth_client())
        assert "correct-horse-battery-staple" not in caplog.text

    @pytest.mark.asyncio
    async def test_a_pinned_registration_is_silent(self, caplog: pytest.LogCaptureFixture) -> None:
        context = ssl.create_default_context()
        with patch("span_panel_api._http.httpx.AsyncClient") as mock_cls:
            dedicated = AsyncMock()
            dedicated.__aenter__ = AsyncMock(return_value=dedicated)
            dedicated.__aexit__ = AsyncMock(return_value=False)
            dedicated.post = AsyncMock(
                return_value=httpx.Response(
                    200,
                    content=json.dumps(V2_AUTH_JSON).encode(),
                    headers={"content-type": "application/json"},
                    request=httpx.Request("POST", f"https://{HOST}/api/v2/auth/register"),
                )
            )
            mock_cls.return_value = dedicated
            with caplog.at_level(logging.WARNING):
                await register_v2(HOST, "home-assistant", "secret", ssl_context=context)
        assert _warnings(caplog) == []


class TestCreateSpanClient:
    """The factory's other path bootstraps without ever calling `register_v2`."""

    @pytest.mark.asyncio
    async def test_a_prebuilt_config_still_warns_about_its_plaintext_bootstrap(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """A caller supplying `mqtt_config` sends no passphrase, but detection
        and the schema fetch are still plaintext and still expose the panel's
        topology — and nothing on this path had a warning in it."""
        from span_panel_api.factory import create_span_client

        config = MqttClientConfig(broker_host="broker.local", username="user", password="pass")
        with (
            patch("span_panel_api.factory.SpanMqttClient") as mock_cls,
            patch("span_panel_api.factory.get_homie_schema", return_value=flat_schema(32)),
        ):
            mock_cls.return_value.connect = AsyncMock()
            with caplog.at_level(logging.WARNING):
                await create_span_client(HOST, mqtt_config=config, serial_number="SYN-0000-0001")

        warnings = _warnings(caplog)
        assert len(warnings) == 1
        assert HOST in warnings[0]

    @pytest.mark.asyncio
    async def test_the_registering_path_warns_exactly_once(self, caplog: pytest.LogCaptureFixture) -> None:
        """`register_v2` already warned; the factory must not say it again."""
        from span_panel_api.factory import create_span_client

        with (
            patch("span_panel_api.factory.SpanMqttClient") as mock_cls,
            patch("span_panel_api.factory.get_homie_schema", return_value=flat_schema(32)),
        ):
            mock_cls.return_value.connect = AsyncMock()
            with caplog.at_level(logging.WARNING):
                await create_span_client(HOST, passphrase="secret", httpx_client=_auth_client())

        assert len(_warnings(caplog)) == 1
