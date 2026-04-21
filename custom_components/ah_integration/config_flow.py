from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlowWithReload,
)
from homeassistant.core import callback
from homeassistant.helpers.httpx_client import get_async_client
from homeassistant.helpers.selector import (
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
)

from .const import (
    AH_AUTHORIZE_URL,
    CONF_ACCESS_TOKEN,
    CONF_EXPIRES_AT,
    CONF_MEMBER_ID,
    CONF_RECEIPT_COUNT,
    CONF_REFRESH_TOKEN,
    CONF_TOKEN_TYPE,
    CONF_TRACKED_PRODUCTS,
    DEFAULT_RECEIPT_COUNT,
    DOMAIN,
)


def _product_label(p: dict) -> str:
    parts = [p["title"]]
    if p.get("unit_size"):
        parts.append(p["unit_size"])
    if p.get("price") is not None:
        parts.append(f"€{p['price']:.2f}")
    return ", ".join(parts) if len(parts) > 1 else parts[0]


class AHConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> AHOptionsFlow:
        return AHOptionsFlow()

    async def async_step_user(self, user_input: dict | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}

        if user_input is not None:
            raw = user_input["auth_code"].strip()
            try:
                token = await self._exchange_code(raw)
            except (RuntimeError, ValueError):
                errors["base"] = "invalid_auth"
            except Exception:
                errors["base"] = "cannot_connect"
            else:
                member_id = token.get(CONF_MEMBER_ID)
                if member_id:
                    await self.async_set_unique_id(str(member_id))
                    self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=(f"Albert Heijn ({member_id})" if member_id else "Albert Heijn"),
                    data={
                        CONF_ACCESS_TOKEN: token["access_token"],
                        CONF_REFRESH_TOKEN: token["refresh_token"],
                        CONF_TOKEN_TYPE: token.get("token_type", "Bearer"),
                        CONF_EXPIRES_AT: token.get("expires_at"),
                        CONF_MEMBER_ID: member_id,
                    },
                )

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({vol.Required("auth_code"): str}),
            errors=errors,
            description_placeholders={"auth_url": AH_AUTHORIZE_URL},
        )

    async def _exchange_code(self, raw_input: str) -> dict:
        from datetime import UTC, datetime, timedelta

        from .appie.auth import AHAuthClient
        from .appie.client import AHClient

        http_client = get_async_client(self.hass)
        code = AHClient._extract_code(raw_input)
        auth = AHAuthClient(http_client=http_client)
        token = await auth.login_with_code(code)
        expires_at = (datetime.now(UTC) + timedelta(seconds=token.expires_in)).isoformat()
        return {
            "access_token": token.access_token,
            "refresh_token": token.refresh_token,
            "token_type": token.token_type,
            "expires_at": expires_at,
            "member_id": token.member_id,
        }


class AHOptionsFlow(OptionsFlowWithReload):
    def __init__(self) -> None:
        self._search_results: list[dict[str, Any]] = []

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            action = user_input.get("action")
            if action == "add":
                return await self.async_step_search()
            if action == "remove":
                return await self.async_step_remove()
            if action == "settings":
                return await self.async_step_settings()

        tracked = self.config_entry.options.get(CONF_TRACKED_PRODUCTS, [])
        actions = [
            SelectOptionDict(value="add", label="Add product"),
            SelectOptionDict(value="settings", label="Settings"),
        ]
        if tracked:
            actions.insert(1, SelectOptionDict(value="remove", label="Remove product"))

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required("action"): SelectSelector(
                        SelectSelectorConfig(options=actions, mode=SelectSelectorMode.LIST)
                    )
                }
            ),
        )

    async def async_step_search(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}

        if user_input is not None:
            query = user_input.get("search_query", "").strip()
            if query:
                try:
                    self._search_results = await self._search_products(query)
                except Exception:
                    errors["base"] = "cannot_connect"
                else:
                    if not self._search_results:
                        errors["search_query"] = "no_results"
                    else:
                        return await self.async_step_pick()

        return self.async_show_form(
            step_id="search",
            data_schema=vol.Schema({vol.Required("search_query"): str}),
            errors=errors,
        )

    async def async_step_pick(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            chosen_id = int(user_input["product"])
            chosen = next(p for p in self._search_results if p["id"] == chosen_id)

            current = list(self.config_entry.options.get(CONF_TRACKED_PRODUCTS, []))
            if not any(p["id"] == chosen_id for p in current):
                current.append(chosen)

            return self._save({CONF_TRACKED_PRODUCTS: current})

        options = [
            SelectOptionDict(
                value=str(p["id"]),
                label=_product_label(p),
            )
            for p in self._search_results
        ]

        return self.async_show_form(
            step_id="pick",
            data_schema=vol.Schema(
                {
                    vol.Required("product"): SelectSelector(
                        SelectSelectorConfig(options=options, mode=SelectSelectorMode.LIST)
                    )
                }
            ),
        )

    async def async_step_remove(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        current = list(self.config_entry.options.get(CONF_TRACKED_PRODUCTS, []))

        if user_input is not None:
            to_remove = set(int(v) for v in user_input.get("products", []))
            remaining = [p for p in current if p["id"] not in to_remove]
            return self._save({CONF_TRACKED_PRODUCTS: remaining})

        options = [
            SelectOptionDict(
                value=str(p["id"]),
                label=_product_label(p),
            )
            for p in current
        ]

        return self.async_show_form(
            step_id="remove",
            data_schema=vol.Schema(
                {
                    vol.Required("products"): SelectSelector(
                        SelectSelectorConfig(
                            options=options,
                            mode=SelectSelectorMode.LIST,
                            multiple=True,
                        )
                    )
                }
            ),
        )

    async def _search_products(self, query: str) -> list[dict[str, Any]]:
        coordinator = self.config_entry.runtime_data
        products = await coordinator._appie_client.products.search(query, limit=10)
        return [
            {"id": p.id, "title": p.title, "price": p.price, "unit_size": p.unit_size}
            for p in products
        ]

    async def async_step_settings(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            return self._save({CONF_RECEIPT_COUNT: int(user_input[CONF_RECEIPT_COUNT])})

        current_count = int(self.config_entry.options.get(CONF_RECEIPT_COUNT, DEFAULT_RECEIPT_COUNT))
        return self.async_show_form(
            step_id="settings",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_RECEIPT_COUNT, default=current_count): vol.All(
                        int, vol.Range(min=1, max=10)
                    )
                }
            ),
        )

    def _save(self, updates: dict[str, Any]) -> ConfigFlowResult:
        new_options = {**self.config_entry.options, **updates}
        return self.async_create_entry(data=new_options)
