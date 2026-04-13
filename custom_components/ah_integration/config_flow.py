from __future__ import annotations

import voluptuous as vol
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.helpers.httpx_client import get_async_client

from .const import (
    AH_AUTHORIZE_URL,
    CONF_ACCESS_TOKEN,
    CONF_EXPIRES_AT,
    CONF_REFRESH_TOKEN,
    CONF_TOKEN_TYPE,
    DOMAIN,
)


class AHConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input: dict | None = None) -> ConfigFlowResult:
        await self.async_set_unique_id(DOMAIN)
        self._abort_if_unique_id_configured()

        errors: dict[str, str] = {}

        if user_input is not None:
            raw = user_input["auth_code"].strip()
            try:
                token = await self._exchange_code(raw)
            except RuntimeError:
                errors["base"] = "invalid_auth"
            except Exception:
                errors["base"] = "cannot_connect"
            else:
                return self.async_create_entry(
                    title="Albert Heijn",
                    data={
                        CONF_ACCESS_TOKEN: token["access_token"],
                        CONF_REFRESH_TOKEN: token["refresh_token"],
                        CONF_TOKEN_TYPE: token.get("token_type", "Bearer"),
                        CONF_EXPIRES_AT: token.get("expires_at"),
                    },
                )

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({vol.Required("auth_code"): str}),
            errors=errors,
            description_placeholders={"auth_url": AH_AUTHORIZE_URL},
        )

    async def _exchange_code(self, raw_input: str) -> dict:
        import tempfile
        from pathlib import Path
        from appie import AHAuthClient
        from appie.client import AHClient

        http_client = get_async_client(self.hass)
        code = AHClient._extract_code(raw_input)

        with tempfile.TemporaryDirectory() as tmp:
            token_path = Path(tmp) / "tokens.json"
            async with AHAuthClient(
                http_client=http_client, token_path=token_path
            ) as auth:
                token = await auth.login_with_code(code)
                stored = auth._stored_token
                return {
                    "access_token": token.access_token,
                    "refresh_token": token.refresh_token,
                    "token_type": token.token_type,
                    "expires_at": stored.expires_at.isoformat() if stored else None,
                }
