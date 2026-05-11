from __future__ import annotations

from datetime import datetime

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CURRENCY_EURO
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import (
    CONF_ENABLE_MONTHLY_BREAKDOWN,
    CONF_RECEIPT_COUNT,
    CONF_TRACKED_PRODUCTS,
    DEFAULT_ENABLE_MONTHLY_BREAKDOWN,
    DEFAULT_RECEIPT_COUNT,
)
from .coordinator import AHCoordinator, AHMonthlyBreakdownItem, AHReceiptData
from .entity import AHEntity

type AHConfigEntry = ConfigEntry[AHCoordinator]

BREAKDOWN_UNRECORDED_ATTRIBUTES = frozenset(
    {
        "item_breakdown",
        "item_breakdown_complete",
        "item_breakdown_missing_receipts",
    }
)


def serialize_breakdown(
    items: list[AHMonthlyBreakdownItem],
) -> dict[str, dict[str, float | int | str]] | None:
    if not items:
        return None
    return {
        str(item.product_id): {
            "name": item.name,
            "quantity": int(item.quantity)
            if float(item.quantity).is_integer()
            else item.quantity,
            "spent": item.spent,
        }
        for item in items
    }


def monthly_breakdown_enabled(coordinator: AHCoordinator) -> bool:
    return bool(
        coordinator.config_entry.options.get(
            CONF_ENABLE_MONTHLY_BREAKDOWN,
            DEFAULT_ENABLE_MONTHLY_BREAKDOWN,
        )
    )


async def async_setup_entry(
    hass: HomeAssistant,
    entry: AHConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    coordinator: AHCoordinator = entry.runtime_data
    n = int(entry.options.get(CONF_RECEIPT_COUNT, DEFAULT_RECEIPT_COUNT))
    entities: list[AHEntity] = [
        AHReceiptCountSensor(coordinator),
        AHMonthlySpentSensor(coordinator),
        AHPreviousMonthSpentSensor(coordinator),
    ]
    entities += [AHReceiptSensor(coordinator, entry, idx) for idx in range(n)]
    entities += [
        AHProductSensor(coordinator, entry, product)
        for product in entry.options.get(CONF_TRACKED_PRODUCTS, [])
    ]
    async_add_entities(entities)


class AHReceiptCountSensor(AHEntity, SensorEntity):
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_has_entity_name = True
    _attr_name = "Receipts this month"
    _attr_translation_key = "receipt_count"

    def __init__(self, coordinator: AHCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.config_entry.entry_id}_receipt_count"

    @property
    def native_value(self) -> int | None:
        if self.coordinator.data is None:
            return None
        return self.coordinator.data.receipt_count


class AHMonthlySpentSensor(AHEntity, SensorEntity):
    _attr_device_class = SensorDeviceClass.MONETARY
    _attr_state_class = SensorStateClass.TOTAL
    _attr_native_unit_of_measurement = CURRENCY_EURO
    _attr_suggested_display_precision = 2
    _attr_has_entity_name = True
    _attr_name = "Spent this month"
    _attr_translation_key = "monthly_spent"
    _unrecorded_attributes = BREAKDOWN_UNRECORDED_ATTRIBUTES

    def __init__(self, coordinator: AHCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.config_entry.entry_id}_monthly_spent"

    @property
    def native_value(self) -> float | None:
        if self.coordinator.data is None:
            return None
        return self.coordinator.data.monthly_spent

    @property
    def extra_state_attributes(self) -> dict | None:
        if self.coordinator.data is None:
            return None
        if not monthly_breakdown_enabled(self.coordinator):
            return None
        breakdown = serialize_breakdown(self.coordinator.data.current_month_breakdown)
        missing_receipts = self.coordinator.data.current_month_breakdown_missing_receipts
        attrs: dict[str, object] = {}
        if breakdown is not None:
            attrs["item_breakdown"] = breakdown
        if breakdown is not None or missing_receipts > 0:
            attrs["item_breakdown_complete"] = missing_receipts == 0
        if missing_receipts > 0:
            attrs["item_breakdown_missing_receipts"] = missing_receipts
        return attrs or None


class AHPreviousMonthSpentSensor(AHEntity, SensorEntity):
    _attr_device_class = SensorDeviceClass.MONETARY
    _attr_state_class = SensorStateClass.TOTAL
    _attr_native_unit_of_measurement = CURRENCY_EURO
    _attr_suggested_display_precision = 2
    _attr_has_entity_name = True
    _attr_name = "Spent last month"
    _attr_translation_key = "previous_month_spent"
    _unrecorded_attributes = BREAKDOWN_UNRECORDED_ATTRIBUTES

    def __init__(self, coordinator: AHCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.config_entry.entry_id}_previous_month_spent"

    @property
    def native_value(self) -> float | None:
        if self.coordinator.data is None:
            return None
        return self.coordinator.data.previous_month_spent

    @property
    def extra_state_attributes(self) -> dict | None:
        if self.coordinator.data is None:
            return None
        if not monthly_breakdown_enabled(self.coordinator):
            return None
        breakdown = serialize_breakdown(self.coordinator.data.previous_month_breakdown)
        missing_receipts = self.coordinator.data.previous_month_breakdown_missing_receipts
        attrs: dict[str, object] = {}
        if breakdown is not None:
            attrs["item_breakdown"] = breakdown
        if breakdown is not None or missing_receipts > 0:
            attrs["item_breakdown_complete"] = missing_receipts == 0
        if missing_receipts > 0:
            attrs["item_breakdown_missing_receipts"] = missing_receipts
        return attrs or None


class AHReceiptSensor(AHEntity, SensorEntity):
    _attr_device_class = SensorDeviceClass.MONETARY
    _attr_native_unit_of_measurement = CURRENCY_EURO
    _attr_suggested_display_precision = 2
    _attr_has_entity_name = True
    _unrecorded_attributes = frozenset({"items"})

    def __init__(self, coordinator: AHCoordinator, entry: AHConfigEntry, index: int) -> None:
        super().__init__(coordinator)
        self._index = index
        self._attr_unique_id = f"{entry.entry_id}_receipt_{index}"
        self._attr_name = "Last receipt" if index == 0 else f"Receipt #{index + 1}"
        self._attr_translation_key = "receipt" if index == 0 else None

    def _receipt(self) -> AHReceiptData | None:
        if self.coordinator.data is None:
            return None
        receipts = self.coordinator.data.receipts
        return receipts[self._index] if self._index < len(receipts) else None

    @property
    def native_value(self) -> float | None:
        r = self._receipt()
        return r.total if r else None

    @property
    def extra_state_attributes(self) -> dict | None:
        r = self._receipt()
        if r is None:
            return None
        attrs: dict = {}
        if r.receipt_id is not None:
            attrs["receipt_id"] = r.receipt_id
        if r.date is not None:
            attrs["date"] = r.date.isoformat()
        if r.discount is not None:
            attrs["discount"] = r.discount
        if r.items is not None:
            attrs["items"] = r.items
        return attrs or None


class AHProductSensor(AHEntity, SensorEntity):
    _attr_device_class = SensorDeviceClass.MONETARY
    _attr_native_unit_of_measurement = CURRENCY_EURO
    _attr_suggested_display_precision = 2
    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: AHCoordinator,
        entry: AHConfigEntry,
        product: dict,
    ) -> None:
        super().__init__(coordinator)
        self._product_meta = product
        self._product_id = int(product["id"])
        self._attr_unique_id = f"{entry.entry_id}_product_{self._product_id}"
        title = product["title"]
        unit_size = product.get("unit_size")
        self._attr_name = f"{title}, {unit_size}" if unit_size else title

    @property
    def native_value(self) -> float | None:
        if self.coordinator.data is None:
            return None
        return self.coordinator.data.tracked_product_prices.get(self._product_id)

    @property
    def extra_state_attributes(self) -> dict | None:
        if self.coordinator.data is None:
            return None
        product = self.coordinator.data.tracked_products.get(self._product_id)
        if product is None:
            return None
        attrs: dict = {}
        if product.brand is not None:
            attrs["brand"] = product.brand
        if product.is_bonus is not None:
            attrs["is_bonus"] = product.is_bonus
        if product.price is not None:
            attrs["regular_price"] = product.price
        if product.discount_price is not None:
            attrs["bonus_price"] = product.discount_price
        if product.bonus_label is not None:
            attrs["bonus_label"] = product.bonus_label
        if product.bonus_start_date is not None:
            attrs["bonus_start_date"] = product.bonus_start_date.isoformat()
        if product.bonus_end_date is not None:
            attrs["bonus_end_date"] = product.bonus_end_date.isoformat()
        if product.discount_type is not None:
            attrs["discount_type"] = product.discount_type
        if product.segment_type is not None:
            attrs["segment_type"] = product.segment_type
        if product.promotion_type is not None:
            attrs["promotion_type"] = product.promotion_type
        if product.is_stapel_bonus is not None:
            attrs["is_stapel_bonus"] = product.is_stapel_bonus
        if product.is_infinite_bonus is not None:
            attrs["is_infinite_bonus"] = product.is_infinite_bonus
        if product.multiple_item_promotion is not None:
            attrs["multiple_item_promotion"] = product.multiple_item_promotion
        if product.bonus_segment_description is not None:
            attrs["bonus_segment_description"] = product.bonus_segment_description
        if product.extra_descriptions:
            attrs["extra_descriptions"] = product.extra_descriptions
        if product.discount_labels:
            attrs["discount_labels"] = [
                {k: v for k, v in lbl.model_dump().items() if v is not None}
                for lbl in product.discount_labels
            ]
        if product.unit_size is not None:
            attrs["unit_size"] = product.unit_size
        if product.main_category is not None:
            attrs["main_category"] = product.main_category
        if product.sub_category is not None:
            attrs["sub_category"] = product.sub_category
        if product.nutriscore is not None:
            attrs["nutriscore"] = product.nutriscore
        if product.image_url is not None:
            attrs["image_url"] = product.image_url
        if product.is_organic is not None:
            attrs["is_organic"] = product.is_organic
        return attrs or None
