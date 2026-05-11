from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
import logging
import math
from typing import TYPE_CHECKING

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.httpx_client import get_async_client
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util
import httpx

from .appie.models import Product
from .const import (
    CONF_ACCESS_TOKEN,
    CONF_ENABLE_MONTHLY_BREAKDOWN,
    CONF_EXPIRES_AT,
    CONF_MEMBER_ID,
    CONF_RECEIPT_COUNT,
    CONF_REFRESH_TOKEN,
    CONF_TOKEN_TYPE,
    CONF_TRACKED_PRODUCTS,
    DEFAULT_ENABLE_MONTHLY_BREAKDOWN,
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
RECEIPT_PAGE_SIZE = 50
RECEIPT_DETAIL_CONCURRENCY = 5


@dataclass
class AHReceiptData:
    index: int
    receipt_id: str | None
    date: datetime | None
    total: float | None
    discount: float | None
    items: dict[str, float] | None


@dataclass
class AHMonthlyBreakdownItem:
    product_id: int
    name: str
    quantity: float
    spent: float


@dataclass
class AHData:
    receipts: list[AHReceiptData]
    receipt_count: int
    monthly_spent: float
    previous_month_spent: float
    current_month_breakdown: list[AHMonthlyBreakdownItem]
    current_month_breakdown_missing_receipts: int
    previous_month_breakdown: list[AHMonthlyBreakdownItem]
    previous_month_breakdown_missing_receipts: int
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
        self._receipt_detail_cache: dict[str, Receipt] = {}

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
        recent_receipts = await self._async_fetch_recent_receipts()
        monthly_receipts = await self._async_fetch_receipts_for_monthly_breakdown()
        current_month_receipts, previous_month_receipts = self._split_recent_receipts(
            monthly_receipts
        )
        detail_cache: dict[str, Receipt | None] = dict(self._receipt_detail_cache)
        monthly_breakdown_enabled = self._monthly_breakdown_enabled()
        receipt_count = len(current_month_receipts)
        monthly_spent = self._calculate_receipts_total(current_month_receipts)
        previous_month_spent = self._calculate_receipts_total(previous_month_receipts)
        if monthly_breakdown_enabled:
            (
                current_month_breakdown,
                current_month_breakdown_missing_receipts,
            ) = await self._async_build_monthly_breakdown(
                current_month_receipts, detail_cache
            )
            (
                previous_month_breakdown,
                previous_month_breakdown_missing_receipts,
            ) = await self._async_build_monthly_breakdown(
                previous_month_receipts, detail_cache
            )
        else:
            current_month_breakdown = []
            current_month_breakdown_missing_receipts = 0
            previous_month_breakdown = []
            previous_month_breakdown_missing_receipts = 0
        receipt_data = await self._async_build_receipt_data(recent_receipts, detail_cache)
        tracked_prices, tracked_products = await self._async_load_tracked_products()
        self._update_detail_cache(recent_receipts + monthly_receipts, detail_cache)

        return AHData(
            receipts=receipt_data,
            receipt_count=receipt_count,
            monthly_spent=monthly_spent,
            previous_month_spent=previous_month_spent,
            current_month_breakdown=current_month_breakdown,
            current_month_breakdown_missing_receipts=current_month_breakdown_missing_receipts,
            previous_month_breakdown=previous_month_breakdown,
            previous_month_breakdown_missing_receipts=previous_month_breakdown_missing_receipts,
            tracked_product_prices=tracked_prices,
            tracked_products=tracked_products,
        )

    def _monthly_breakdown_enabled(self) -> bool:
        return bool(
            self._entry.options.get(
                CONF_ENABLE_MONTHLY_BREAKDOWN,
                DEFAULT_ENABLE_MONTHLY_BREAKDOWN,
            )
        )

    def _require_client(self) -> AHClient:
        if self._auth is None or self._appie_client is None:
            raise UpdateFailed("Client not initialized")
        return self._appie_client

    async def _async_fetch_recent_receipts(self) -> list[Receipt]:
        receipt_limit = int(self._entry.options.get(CONF_RECEIPT_COUNT, DEFAULT_RECEIPT_COUNT))

        try:
            receipts = await self._require_client().receipts.list_all(
                limit=max(RECEIPT_PAGE_SIZE, receipt_limit)
            )
        except RECOVERABLE_FETCH_ERRORS as err:
            raise UpdateFailed(f"Error fetching receipts: {err}") from err

        return self._sort_receipts(receipts)

    async def _async_fetch_receipts_for_monthly_breakdown(self) -> list[Receipt]:
        previous_month_start = self._get_previous_month_start(dt_util.now())
        receipts: list[Receipt] = []
        offset = 0
        last_oldest_receipt: datetime | None = None
        can_stop_at_boundary = True

        try:
            while True:
                page = self._sort_receipts(
                    await self._require_client().receipts.list_all(
                    limit=RECEIPT_PAGE_SIZE, offset=offset
                    )
                )
                if not page:
                    break

                receipts.extend(page)
                if len(page) < RECEIPT_PAGE_SIZE:
                    break

                newest_receipt = self._get_receipt_local_datetime(page[0])
                oldest_receipt = self._get_receipt_local_datetime(page[-1])
                if (
                    last_oldest_receipt is not None
                    and newest_receipt > last_oldest_receipt
                    and can_stop_at_boundary
                ):
                    _LOGGER.warning(
                        "Receipt pagination order was not monotonic across pages; fetching all remaining pages for monthly breakdown"
                    )
                    can_stop_at_boundary = False

                last_oldest_receipt = oldest_receipt
                if can_stop_at_boundary and oldest_receipt < previous_month_start:
                    break

                offset += RECEIPT_PAGE_SIZE
        except RECOVERABLE_FETCH_ERRORS as err:
            raise UpdateFailed(f"Error fetching receipts: {err}") from err

        return self._sort_receipts(
            [
                receipt
                for receipt in receipts
                if self._get_receipt_local_datetime(receipt) >= previous_month_start
            ]
        )

    def _split_recent_receipts(
        self, receipts: list[Receipt]
    ) -> tuple[list[Receipt], list[Receipt]]:
        now = dt_util.now()
        current_month_start = self._get_month_start(now)
        previous_month_start = self._get_previous_month_start(now)
        current_month_receipts = [
            receipt
            for receipt in receipts
            if self._get_receipt_local_datetime(receipt) >= current_month_start
        ]
        previous_month_receipts = [
            receipt
            for receipt in receipts
            if previous_month_start
            <= self._get_receipt_local_datetime(receipt)
            < current_month_start
        ]
        return current_month_receipts, previous_month_receipts

    def _calculate_receipts_total(self, receipts: list[Receipt]) -> float:
        return round(
            sum(receipt.total for receipt in receipts if receipt.total is not None),
            2,
        )

    def _get_month_start(self, point_in_time: datetime) -> datetime:
        local_time = self._as_local_datetime(point_in_time)
        return local_time.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    def _get_previous_month_start(self, point_in_time: datetime) -> datetime:
        month_start = self._get_month_start(point_in_time)
        if month_start.month == 1:
            return month_start.replace(year=month_start.year - 1, month=12)
        return month_start.replace(month=month_start.month - 1)

    def _as_local_datetime(self, point_in_time: datetime) -> datetime:
        if point_in_time.tzinfo is None:
            point_in_time = point_in_time.replace(tzinfo=UTC)
        return dt_util.as_local(point_in_time)

    def _get_receipt_local_datetime(self, receipt: Receipt) -> datetime:
        return self._as_local_datetime(receipt.datetime)

    def _sort_receipts(self, receipts: list[Receipt]) -> list[Receipt]:
        return sorted(receipts, key=self._get_receipt_local_datetime, reverse=True)

    async def _async_build_receipt_data(
        self,
        receipts: list[Receipt],
        detail_cache: dict[str, Receipt | None],
    ) -> list[AHReceiptData]:
        receipt_limit = int(self._entry.options.get(CONF_RECEIPT_COUNT, DEFAULT_RECEIPT_COUNT))
        receipt_data = [
            await self._async_build_receipt_entry(index, receipt, detail_cache)
            for index, receipt in enumerate(receipts[:receipt_limit])
        ]
        return receipt_data or [self._empty_receipt_data()]

    async def _async_build_receipt_entry(
        self,
        index: int,
        receipt: Receipt,
        detail_cache: dict[str, Receipt | None],
    ) -> AHReceiptData:
        items, discount = await self._async_fetch_receipt_details(receipt, detail_cache)
        return AHReceiptData(
            index=index,
            receipt_id=receipt.id,
            date=receipt.datetime,
            total=receipt.total,
            discount=discount,
            items=items,
        )

    async def _async_fetch_receipt_details(
        self,
        receipt: Receipt,
        detail_cache: dict[str, Receipt | None],
    ) -> tuple[dict[str, float] | None, float | None]:
        detailed = await self._async_get_receipt_detail(receipt, detail_cache)
        if detailed is None:
            return None, None

        try:
            return (
                self._build_receipt_items(detailed.products),
                self._calculate_receipt_discount(detailed.products),
            )
        except (TypeError, ValueError):
            _LOGGER.warning(
                "Failed to format receipt details for %s; omitting receipt items",
                receipt.id,
                exc_info=True,
            )
            return None, None

    async def _async_get_receipt_detail(
        self,
        receipt: Receipt,
        detail_cache: dict[str, Receipt | None],
    ) -> Receipt | None:
        if receipt.id in detail_cache:
            return detail_cache[receipt.id]

        try:
            detailed = await self._require_client().receipts.get_pos_receipt(
                receipt.id, summary=receipt
            )
        except RECOVERABLE_FETCH_ERRORS:
            _LOGGER.warning(
                "Failed to fetch receipt details for %s; omitting receipt items",
                receipt.id,
                exc_info=True,
            )
            detail_cache[receipt.id] = None
            return None

        detail_cache[receipt.id] = detailed
        return detailed

    async def _async_build_monthly_breakdown(
        self,
        receipts: list[Receipt],
        detail_cache: dict[str, Receipt | None],
    ) -> tuple[list[AHMonthlyBreakdownItem], int]:
        detailed_receipts = await self._async_fetch_receipt_details_batch(
            receipts, detail_cache
        )
        aggregated: dict[int, AHMonthlyBreakdownItem] = {}
        missing_receipts = 0

        for detailed in detailed_receipts:
            if detailed is None:
                missing_receipts += 1
                continue

            for product in detailed.products:
                quantity = float(product.quantity)
                spent = float(product.total_price)
                item = aggregated.get(product.id)
                if item is None:
                    aggregated[product.id] = AHMonthlyBreakdownItem(
                        product_id=product.id,
                        name=product.name,
                        quantity=quantity,
                        spent=spent,
                    )
                    continue

                item.quantity += quantity
                item.spent += spent

        return (
            [
                AHMonthlyBreakdownItem(
                    product_id=item.product_id,
                    name=item.name,
                    quantity=self._round_quantity(item.quantity),
                    spent=round(item.spent, 2),
                )
                for item in sorted(
                    aggregated.values(),
                    key=lambda item: (-item.spent, item.name.casefold(), item.product_id),
                )
            ],
            missing_receipts,
        )

    async def _async_fetch_receipt_details_batch(
        self,
        receipts: list[Receipt],
        detail_cache: dict[str, Receipt | None],
    ) -> list[Receipt | None]:
        if not receipts:
            return []

        semaphore = asyncio.Semaphore(RECEIPT_DETAIL_CONCURRENCY)

        async def _fetch(receipt: Receipt) -> Receipt | None:
            async with semaphore:
                return await self._async_get_receipt_detail(receipt, detail_cache)

        return await asyncio.gather(*(_fetch(receipt) for receipt in receipts))

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

    def _round_quantity(self, quantity: float) -> float:
        numeric_quantity = float(quantity)
        if not math.isfinite(numeric_quantity):
            raise ValueError("Receipt quantity must be finite")
        return round(numeric_quantity, 3)

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

    def _update_detail_cache(
        self,
        receipts: list[Receipt],
        detail_cache: dict[str, Receipt | None],
    ) -> None:
        relevant_receipt_ids = {receipt.id for receipt in receipts}
        self._receipt_detail_cache = {
            receipt_id: receipt_detail
            for receipt_id, receipt_detail in detail_cache.items()
            if receipt_id in relevant_receipt_ids and receipt_detail is not None
        }

    async def async_close(self) -> None:
        if self._appie_client is not None:
            await self._appie_client.aclose()
