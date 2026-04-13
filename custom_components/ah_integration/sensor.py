from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CURRENCY_EURO
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import AHCoordinator, AHData
from .entity import AHEntity

type AHConfigEntry = ConfigEntry[AHCoordinator]


@dataclass(frozen=True, kw_only=True)
class AHSensorDescription(SensorEntityDescription):
    value_fn: Callable[[AHData], float | int | str | datetime | None]


SENSOR_DESCRIPTIONS: tuple[AHSensorDescription, ...] = (
    AHSensorDescription(
        key="last_receipt_total",
        translation_key="last_receipt_total",
        name="Last receipt total",
        device_class=SensorDeviceClass.MONETARY,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=CURRENCY_EURO,
        value_fn=lambda d: d.last_receipt_total,
    ),
    AHSensorDescription(
        key="last_receipt_discount",
        translation_key="last_receipt_discount",
        name="Last receipt discount",
        device_class=SensorDeviceClass.MONETARY,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=CURRENCY_EURO,
        value_fn=lambda d: d.last_receipt_discount,
    ),
    AHSensorDescription(
        key="last_receipt_date",
        translation_key="last_receipt_date",
        name="Last receipt date",
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=lambda d: d.last_receipt_date,
    ),
    AHSensorDescription(
        key="receipt_count",
        translation_key="receipt_count",
        name="Receipt count",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda d: d.receipt_count,
    ),
    AHSensorDescription(
        key="bonus_savings",
        translation_key="bonus_savings",
        name="Total bonus savings",
        device_class=SensorDeviceClass.MONETARY,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=CURRENCY_EURO,
        value_fn=lambda d: d.bonus_savings,
    ),
    AHSensorDescription(
        key="last_receipt_items",
        translation_key="last_receipt_items",
        name="Last receipt items",
        value_fn=lambda d: d.last_receipt_items,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: AHConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    coordinator: AHCoordinator = entry.runtime_data
    async_add_entities(
        AHSensor(coordinator, description) for description in SENSOR_DESCRIPTIONS
    )


class AHSensor(AHEntity, SensorEntity):
    entity_description: AHSensorDescription

    def __init__(
        self,
        coordinator: AHCoordinator,
        description: AHSensorDescription,
    ) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = f"{coordinator.config_entry.entry_id}_{description.key}"

    @property
    def native_value(self) -> float | int | datetime | None:
        if self.coordinator.data is None:
            return None
        return self.entity_description.value_fn(self.coordinator.data)
