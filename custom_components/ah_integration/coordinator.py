from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.httpx_client import get_async_client
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
import httpx

from .const import (
    CONF_ACCESS_TOKEN,
    CONF_EXPIRES_AT,
    CONF_REFRESH_TOKEN,
    CONF_TOKEN_TYPE,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)

_AH_BASE = "https://api.ah.nl"
_AH_RECEIPTS_URL = f"{_AH_BASE}/mobile-services/v1/receipts"
_AH_CLIENT_ID = "appie-ios"
_AH_CLIENT_VERSION = "9.28"
_AH_USER_AGENT = "Appie/9.28 (iPhone17,3; iPhone; CPU OS 26_1 like Mac OS X)"


@dataclass
class AHData:
    last_receipt_total: float | None
    last_receipt_date: datetime | None
    receipt_count: int
    last_receipt_discount: float | None
    bonus_savings: float | None
    last_receipt_items: str | None


def _default_headers() -> dict[str, str]:
    return {
        "User-Agent": _AH_USER_AGENT,
        "x-client-name": _AH_CLIENT_ID,
        "x-client-version": _AH_CLIENT_VERSION,
        "x-application": "AHWEBSHOP",
        "Accept": "application/json",
        "Content-Type": "application/json",
    }


class _HAAuthClient:
    def __init__(
        self,
        http_client: httpx.AsyncClient,
        hass: HomeAssistant,
        entry: ConfigEntry,
    ) -> None:
        from appie.models import StoredToken, TokenResponse

        self._hass = hass
        self._entry = entry
        self._StoredToken = StoredToken
        self._TokenResponse = TokenResponse
        self._http = http_client

        expires_at_str = entry.data.get(CONF_EXPIRES_AT)
        expires_at = (
            datetime.fromisoformat(expires_at_str)
            if expires_at_str
            else datetime.now(UTC) + timedelta(hours=2)
        )

        self._stored_token = StoredToken(
            access_token=entry.data[CONF_ACCESS_TOKEN],
            refresh_token=entry.data[CONF_REFRESH_TOKEN],
            token_type=entry.data.get(CONF_TOKEN_TYPE, "Bearer"),
            expires_in=7200,
            expires_at=expires_at,
        )

    def token_is_expiring(self) -> bool:
        deadline = datetime.now(UTC) + timedelta(seconds=60)
        return self._stored_token.expires_at <= deadline

    async def ensure_valid_token(self):
        if self.token_is_expiring():
            return await self._do_refresh(self._stored_token.refresh_token)
        return self._stored_token.to_token_response()

    async def _do_refresh(self, refresh_token: str):
        resp = await self._http.post(
            f"{_AH_BASE}/mobile-auth/v1/auth/token/refresh",
            headers=_default_headers(),
            json={"clientId": _AH_CLIENT_ID, "refreshToken": refresh_token},
        )
        resp.raise_for_status()
        token = self._TokenResponse.model_validate(resp.json())
        expires_at = datetime.now(UTC) + timedelta(seconds=token.expires_in)
        self._stored_token = self._StoredToken.from_token_response(
            token, expires_at=expires_at
        )
        self._hass.config_entries.async_update_entry(
            self._entry,
            data={
                **self._entry.data,
                CONF_ACCESS_TOKEN: token.access_token,
                CONF_REFRESH_TOKEN: token.refresh_token,
                CONF_TOKEN_TYPE: token.token_type,
                CONF_EXPIRES_AT: expires_at.isoformat(),
            },
        )
        return token

    async def aclose(self) -> None:
        pass


class AHCoordinator(DataUpdateCoordinator[AHData]):
    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(minutes=DEFAULT_SCAN_INTERVAL),
            config_entry=entry,
        )
        self._entry = entry
        self._auth: _HAAuthClient | None = None
        self._appie_client = None

    async def _async_setup(self) -> None:
        from appie.client import AHClient

        http = get_async_client(self.hass)
        self._auth = _HAAuthClient(http, self.hass, self._entry)
        self._appie_client = AHClient(http_client=http, auth_client=self._auth)

    async def _async_update_data(self) -> AHData:
        if self._auth is None or self._appie_client is None:
            raise UpdateFailed("Client not initialized")

        http = get_async_client(self.hass)
        try:
            receipts = await self._appie_client.receipts.list_all(limit=50)

            token = await self._auth.ensure_valid_token()
            bearer = f"{token.token_type} {token.access_token}"
            resp = await http.get(
                _AH_RECEIPTS_URL,
                headers={**_default_headers(), "Authorization": bearer},
            )
            resp.raise_for_status()
            raw = resp.json()
        except Exception as err:
            raise UpdateFailed(f"Error fetching receipts: {err}") from err

        raw_items: list[dict] = (
            raw if isinstance(raw, list) else raw.get("receipts", [])
        )

        if not receipts:
            return AHData(
                last_receipt_total=None,
                last_receipt_date=None,
                receipt_count=0,
                last_receipt_discount=None,
                bonus_savings=None,
                last_receipt_items=None,
            )

        sorted_receipts = sorted(receipts, key=lambda r: r.datetime, reverse=True)
        latest = sorted_receipts[0]

        try:
            detailed = await self._appie_client.receipts.get_pos_receipt(latest.id)
            items_text = (
                "\n".join(
                    f"{p.quantity}x {p.name} €{p.total_price:.2f}"
                    for p in detailed.products
                )
                or None
            )
        except Exception:
            items_text = None

        discount_by_id: dict[str, float] = {
            r.get("id", ""): float(r.get("totalDiscount", 0) or 0) for r in raw_items
        }
        total_discount = sum(discount_by_id.values())
        last_discount = discount_by_id.get(str(latest.id), 0.0)

        return AHData(
            last_receipt_total=latest.total,
            last_receipt_date=latest.datetime,
            receipt_count=len(receipts),
            last_receipt_discount=last_discount if last_discount else None,
            bonus_savings=total_discount if total_discount else None,
            last_receipt_items=items_text,
        )

    async def async_close(self) -> None:
        if self._appie_client is not None:
            await self._appie_client.aclose()
