from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import AHCoordinator


class AHEntity(CoordinatorEntity[AHCoordinator]):
    _attr_has_entity_name = True

    def __init__(self, coordinator: AHCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, DOMAIN)},
            name="Albert Heijn",
            manufacturer="Albert Heijn",
            entry_type=DeviceEntryType.SERVICE,
        )
