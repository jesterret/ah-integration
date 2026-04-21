from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
import logging
import math
from typing import TYPE_CHECKING

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.httpx_client import get_async_client
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
import httpx

from .appie.models import Product
from .const import (
    CONF_ACCESS_TOKEN,
    CONF_EXPIRES_AT,
    CONF_MEMBER_ID,
    CONF_RECEIPT_COUNT,
    CONF_REFRESH_TOKEN,
    CONF_TOKEN_TYPE,
    CONF_TRACKED_PRODUCTS,
    DEFAULT_RECEIPT_COUNT,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)

if TYPE_CHECKING:
    from .appie.client import AHClient
    from .appie.models import Receipt, ReceiptProduct


RECOVERABLE_FETCH_ERRORS = (
    httpx.HTTPError,
    KeyError,
    LookupError,
    TypeError,
    ValueError,
    RuntimeError,
)


@dataclass
class AHReceiptData:
    index: int
    receipt_id: str | None
    date: datetime | None
    total: float | None
    discount: float | None
    items: dict[str, float] | None


@dataclass
class AHData:
    receipts: list[AHReceiptData]
    receipt_count: int
    monthly_spent: float
    tracked_product_prices: dict[int, float | None]
    tracked_products: dict[int, Product]


class _HAAuthClient:
    def __init__(
        self,
        http_client: httpx.AsyncClient,
        hass: HomeAssistant,
        entry: ConfigEntry,
    ) -> None:
        from .appie.models import StoredToken, TokenResponse

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
            member_id=entry.data.get(CONF_MEMBER_ID),
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
        from .appie.auth import BASE_URL, DEFAULT_CLIENT_ID, _DEFAULT_HEADERS

        resp = await self._http.post(
            f"{BASE_URL}/mobile-auth/v1/auth/token/refresh",
            headers=_DEFAULT_HEADERS,
            json={"clientId": DEFAULT_CLIENT_ID, "refreshToken": refresh_token},
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
                CONF_MEMBER_ID: token.member_id or self._entry.data.get(CONF_MEMBER_ID),
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
        self._appie_client: AHClient | None = None

    async def _async_setup(self) -> None:
        await self._async_ensure_client()

    async def _async_ensure_client(self) -> None:
        if self._auth is not None and self._appie_client is not None:
            return

        from .appie.client import AHClient

        http = get_async_client(self.hass)
        self._auth = _HAAuthClient(http, self.hass, self._entry)
        self._appie_client = AHClient(http_client=http, auth_client=self._auth)

    async def _async_update_data(self) -> AHData:
        await self._async_ensure_client()
        receipts = await self._async_fetch_receipts()
        receipt_count, monthly_spent = self._calculate_monthly_summary(receipts)
        receipt_data = await self._async_build_receipt_data(receipts)
        tracked_prices, tracked_products = await self._async_load_tracked_products()

        return AHData(
            receipts=receipt_data,
            receipt_count=receipt_count,
            monthly_spent=monthly_spent,
            tracked_product_prices=tracked_prices,
            tracked_products=tracked_products,
        )

    def _require_client(self) -> AHClient:
        if self._auth is None or self._appie_client is None:
            raise UpdateFailed("Client not initialized")
        return self._appie_client

    async def _async_fetch_receipts(self) -> list[Receipt]:
        try:
            return await self._require_client().receipts.list_all(limit=50)
        except RECOVERABLE_FETCH_ERRORS as err:
            raise UpdateFailed(f"Error fetching receipts: {err}") from err

    def _calculate_monthly_summary(self, receipts: list[Receipt]) -> tuple[int, float]:
        now = datetime.now(UTC)
        monthly_receipts = [
            receipt for receipt in receipts if self._is_receipt_in_current_month(receipt, now)
        ]
        monthly_spent = round(
            sum(receipt.total for receipt in monthly_receipts if receipt.total is not None),
            2,
        )
        return len(monthly_receipts), monthly_spent

    def _is_receipt_in_current_month(self, receipt: Receipt, now: datetime) -> bool:
        return receipt.datetime.year == now.year and receipt.datetime.month == now.month

    async def _async_build_receipt_data(self, receipts: list[Receipt]) -> list[AHReceiptData]:
        receipt_limit = int(self._entry.options.get(CONF_RECEIPT_COUNT, DEFAULT_RECEIPT_COUNT))
        receipt_data = [
            await self._async_build_receipt_entry(index, receipt)
            for index, receipt in enumerate(receipts[:receipt_limit])
        ]
        return receipt_data or [self._empty_receipt_data()]

    async def _async_build_receipt_entry(
        self, index: int, receipt: Receipt
    ) -> AHReceiptData:
        items, discount = await self._async_fetch_receipt_details(receipt)
        return AHReceiptData(
            index=index,
            receipt_id=receipt.id,
            date=receipt.datetime,
            total=receipt.total,
            discount=discount,
            items=items,
        )

    async def _async_fetch_receipt_details(
        self, receipt: Receipt
    ) -> tuple[dict[str, float] | None, float | None]:
        try:
            detailed = await self._require_client().receipts.get_pos_receipt(receipt.id)
        except RECOVERABLE_FETCH_ERRORS as err:
            _LOGGER.warning(
                "Failed to fetch receipt details for %s; omitting receipt items",
                receipt.id,
                exc_info=True,
            )
            return None, None

        try:
            return (
                self._build_receipt_items(detailed.products),
                self._calculate_receipt_discount(detailed.products),
            )
        except (TypeError, ValueError) as err:
            _LOGGER.warning(
                "Failed to format receipt details for %s; omitting receipt items",
                receipt.id,
                exc_info=True,
            )
            return None, None

    def _build_receipt_items(
        self, products: list[ReceiptProduct]
    ) -> dict[str, float] | None:
        items = {
            f"{self._format_receipt_quantity(product.quantity)}x {product.name}": product.total_price
            for product in products
        }
        return items or None

    def _format_receipt_quantity(self, quantity: float) -> str:
        numeric_quantity = float(quantity)
        if not math.isfinite(numeric_quantity):
            raise ValueError("Receipt quantity must be finite")
        if numeric_quantity.is_integer():
            return str(int(numeric_quantity))
        return f"{numeric_quantity:g}"

    def _calculate_receipt_discount(
        self, products: list[ReceiptProduct]
    ) -> float | None:
        discount = round(
            sum(
                max(0.0, product.price_per_unit * product.quantity - product.total_price)
                for product in products
                if product.price_per_unit is not None and product.total_price is not None
            ),
            2,
        )
        return discount or None

    async def _async_load_tracked_products(
        self,
    ) -> tuple[dict[int, float | None], dict[int, Product]]:
        tracked_prices: dict[int, float | None] = {}
        tracked_products: dict[int, Product] = {}

        for product_meta in self._entry.options.get(CONF_TRACKED_PRODUCTS, []):
            product_id = self._get_tracked_product_id(product_meta)
            if product_id is None:
                continue

            try:
                product = await self._require_client().products.get(product_id)
            except RECOVERABLE_FETCH_ERRORS as err:
                _LOGGER.warning(
                    "Failed to fetch tracked product %s; leaving state unavailable",
                    product_id,
                    exc_info=True,
                )
                tracked_prices[product_id] = None
                continue

            raw_price = (
                product.discount_price
                if product.discount_price is not None
                else product.price
            )
            tracked_prices[product_id] = round(raw_price, 2) if raw_price is not None else None
            tracked_products[product_id] = product

        return tracked_prices, tracked_products

    def _get_tracked_product_id(self, product_meta: object) -> int | None:
        if not isinstance(product_meta, dict):
            _LOGGER.warning(
                "Skipping invalid tracked product config entry; expected dict but got %s",
                type(product_meta).__name__,
            )
            return None

        try:
            return int(product_meta["id"])
        except (KeyError, TypeError, ValueError):
            _LOGGER.warning(
                "Skipping tracked product config without a valid integer id; keys=%s",
                sorted(product_meta),
            )
            return None

    def _empty_receipt_data(self) -> AHReceiptData:
        return AHReceiptData(
            index=0,
            receipt_id=None,
            date=None,
            total=None,
            discount=None,
            items=None,
        )

    async def async_close(self) -> None:
        if self._appie_client is not None:
            await self._appie_client.aclose()
