from __future__ import annotations

from datetime import date
from typing import Any, Protocol

import httpx

from .models import DiscountLabel, Product


class RequestingClient(Protocol):
    async def request(self, method: str, url: str, **kwargs: Any) -> httpx.Response: ...


_SEARCH_URL = "/mobile-services/product/search/v2"
_DETAIL_URL = "/mobile-services/product/detail/v4/fir/{}"
_BARCODE_URL = "/mobile-services/product/search/v1/gtin/{}"


class ProductsAPI:
    def __init__(self, client: RequestingClient) -> None:
        self._client = client

    async def search(
        self,
        query: str,
        *,
        limit: int = 10,
        page: int = 0,
        sort: str = "RELEVANCE",
        bonus_only: bool = False,
    ) -> list[Product]:
        params: dict[str, Any] = {
            "query": query,
            "sortOn": sort,
            "size": limit,
            "page": page,
        }
        if bonus_only:
            params["bonus"] = "true"
        response = await self._client.request("GET", _SEARCH_URL, params=params)
        return _parse_products(response.json())

    async def search_all_pages(
        self,
        query: str,
        *,
        sort: str = "RELEVANCE",
        bonus_only: bool = False,
        page_size: int = 30,
        max_pages: int = 10,
    ) -> list[Product]:
        results: list[Product] = []
        for page in range(max_pages):
            params: dict[str, Any] = {
                "query": query,
                "sortOn": sort,
                "size": page_size,
                "page": page,
            }
            if bonus_only:
                params["bonus"] = "true"
            response = await self._client.request("GET", _SEARCH_URL, params=params)
            payload = response.json()
            results.extend(_parse_products(payload))
            page_info = payload.get("page", {})
            if page >= page_info.get("totalPages", 1) - 1:
                break
        return results

    async def search_bonus(self, *, limit: int = 50) -> list[Product]:
        params: dict[str, Any] = {"bonus": "true", "sortOn": "RELEVANCE", "size": limit, "page": 0}
        response = await self._client.request("GET", _SEARCH_URL, params=params)
        return _parse_products(response.json())

    async def get(self, product_id: int) -> Product:
        response = await self._client.request("GET", _DETAIL_URL.format(product_id))
        payload = response.json()
        product_card = payload.get("productCard")
        if not isinstance(product_card, dict):
            raise LookupError(f"Product {product_id} detail payload missing productCard")
        return _map_product(product_card)

    async def get_by_barcode(self, barcode: str) -> Product:
        response = await self._client.request("GET", _BARCODE_URL.format(barcode))
        payload = response.json()
        card = payload.get("productCard") or payload
        return _map_product(card)


def _parse_products(payload: dict) -> list[Product]:
    items = payload.get("products") or payload.get("data") or []
    result = []
    for item in items:
        try:
            result.append(_map_product(item))
        except (KeyError, ValueError):
            pass
    return result


def _map_product(p: dict) -> Product:
    property_labels = _extract_property_labels(p)
    is_bonus = _extract_is_bonus(p)
    is_bonus_price = bool(p.get("isBonusPrice"))
    current = _extract_current_price(p)
    before_bonus = _extract_before_bonus_price(p)
    discount_labels = _extract_discount_labels(p)
    if is_bonus_price and current is not None and before_bonus is not None and before_bonus > current:
        price = before_bonus
        discount_price = current
    else:
        fixed = next((lbl.price for lbl in discount_labels if lbl.code == "DISCOUNT_FIXED_PRICE" and lbl.price is not None), None)
        price = before_bonus if before_bonus is not None else current
        discount_price = fixed
    return Product(
        id=_extract_id(p),
        title=_extract_title(p),
        brand=p.get("brand") or None,
        price=price,
        original_price=before_bonus,
        discount_price=discount_price,
        is_bonus=is_bonus,
        bonus_label=_extract_bonus_label(p),
        bonus_start_date=_parse_date(p.get("bonusStartDate")),
        bonus_end_date=_parse_date(p.get("bonusEndDate")),
        discount_type=p.get("discountType") or None,
        segment_type=p.get("segmentType") or None,
        promotion_type=p.get("promotionType") or None,
        is_stapel_bonus=_coerce_bool(p.get("isStapelBonus")),
        is_infinite_bonus=_coerce_bool(p.get("isInfiniteBonus")),
        multiple_item_promotion=_coerce_bool(p.get("multipleItemPromotion")),
        bonus_segment_description=p.get("bonusSegmentDescription") or None,
        extra_descriptions=[s for s in (p.get("extraDescriptions") or []) if isinstance(s, str)],
        discount_labels=discount_labels,
        is_organic=_extract_is_organic(p, property_labels),
        property_labels=property_labels,
        unit_size=p.get("salesUnitSize") or p.get("unitSize") or p.get("unitPriceDescription") or None,
        image_url=_extract_best_image(p),
        main_category=p.get("mainCategory") or None,
        sub_category=p.get("subCategory") or None,
        nutriscore=p.get("nutriscore") or None,
        is_orderable=_coerce_bool(p.get("isOrderable")),
    )


def _extract_id(p: dict) -> int:
    raw = p.get("webshopId") or p.get("id")
    if raw is None:
        raise KeyError("No webshopId or id in product payload")
    return int(raw)


def _extract_title(p: dict) -> str:
    title = p.get("title") or p.get("description")
    if not title:
        raise KeyError("No title or description in product payload")
    return title


def _raw_price(value: Any) -> float | None:
    if isinstance(value, dict):
        value = value.get("amount")
    return round(float(value), 2) if value is not None else None


def _extract_current_price(p: dict) -> float | None:
    price_obj = p.get("price")
    if isinstance(price_obj, dict):
        for key in ("current", "now", "amount"):
            val = _raw_price(price_obj.get(key))
            if val is not None:
                return val
    return _raw_price(p.get("currentPrice"))


def _extract_before_bonus_price(p: dict) -> float | None:
    price_obj = p.get("price")
    if isinstance(price_obj, dict):
        raw = price_obj.get("was") or price_obj.get("before") or price_obj.get("old")
        return _raw_price(raw)
    return _raw_price(p.get("priceBeforeBonus"))


def _extract_price(p: dict) -> float | None:
    return _extract_current_price(p)


def _extract_original_price(p: dict) -> float | None:
    return _extract_before_bonus_price(p)


def _extract_is_bonus(p: dict) -> bool | None:
    for key in ("isBonus", "isBonusPrice"):
        val = p.get(key)
        if val is not None:
            return bool(val)
    if p.get("bonusMechanism") or p.get("bonusStartDate"):
        return True
    return None


def _extract_bonus_label(p: dict) -> str | None:
    mechanism = p.get("bonusMechanism")
    if isinstance(mechanism, str) and mechanism.strip():
        return mechanism.strip()
    for desc in (p.get("extraDescriptions") or []):
        if isinstance(desc, str) and "korting" in desc.lower():
            return desc
    return None


def _extract_is_organic(p: dict, labels: list[str]) -> bool | None:
    val = p.get("isOrganic")
    if val is not None:
        return bool(val)
    organic_set = {"biologisch", "organic", "bio", "np_biologisch"}
    if {lbl.strip().lower() for lbl in labels} & organic_set:
        return True
    return None


def _extract_property_labels(p: dict) -> list[str]:
    labels: list[str] = []
    icons = p.get("propertyIcons")
    if isinstance(icons, list):
        labels += [x for x in icons if isinstance(x, str)]
    for key in ("properties", "labels"):
        for item in (p.get(key) or []):
            if isinstance(item, str):
                labels.append(item)
            elif isinstance(item, dict):
                label = item.get("label") or item.get("name") or item.get("title") or item.get("id")
                if isinstance(label, str):
                    labels.append(label)
    return list(dict.fromkeys(lbl for lbl in labels if lbl))


def _extract_best_image(p: dict) -> str | None:
    images = p.get("images")
    if isinstance(images, list) and images:
        best = max(
            (img for img in images if isinstance(img, dict) and img.get("url")),
            key=lambda img: img.get("width", 0),
            default=None,
        )
        if best:
            return best["url"]
    return p.get("imageUrl") or None


def _parse_date(value: Any) -> date | None:
    if isinstance(value, str) and value:
        try:
            return date.fromisoformat(value)
        except ValueError:
            return None
    return None


def _extract_discount_labels(p: dict) -> list[DiscountLabel]:
    raw = p.get("discountLabels")
    if not isinstance(raw, list):
        return []
    result = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        code = item.get("code")
        desc = item.get("defaultDescription") or item.get("description") or ""
        if not code:
            continue
        result.append(DiscountLabel(
            code=code,
            description=desc,
            percentage=_raw_price(item.get("percentage") or item.get("precisePercentage")),
            amount=_raw_price(item.get("amount")),
            price=_raw_price(item.get("price")),
            count=int(item["count"]) if item.get("count") is not None else None,
        ))
    return result


def _coerce_bool(value: Any) -> bool | None:
    if value is None:
        return None
    return bool(value)
