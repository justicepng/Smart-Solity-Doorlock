"""Lock platform for Smart Solity."""
from __future__ import annotations

from typing import Any

from homeassistant.components.lock import LockEntity, LockEntityFeature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import SolityConfigEntry
from .const import CONF_DEVICE_ID, CONF_NICKNAME, DOMAIN, MANUFACTURER, MODEL
from .coordinator import SolityStatusCoordinator


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SolityConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the Solity lock."""
    async_add_entities([SolityLock(entry.runtime_data.status, entry)])


class SolityLock(CoordinatorEntity[SolityStatusCoordinator], LockEntity):
    """A Solity door lock controlled through the cloud API."""

    _attr_has_entity_name = True
    _attr_name = None  # use the device name
    _attr_supported_features = LockEntityFeature.OPEN

    def __init__(self, coordinator: SolityStatusCoordinator, entry: SolityConfigEntry) -> None:
        super().__init__(coordinator)
        device_id = entry.data[CONF_DEVICE_ID]
        self._attr_unique_id = f"{device_id}_lock"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, device_id)},
            name=entry.data.get(CONF_NICKNAME) or "Solity Doorlock",
            manufacturer=MANUFACTURER,
            model=MODEL,
        )

    @property
    def is_locked(self) -> bool | None:
        """Return True if the deadbolt is engaged."""
        data = self.coordinator.data or {}
        value = data.get("deadBolt")
        if value is None:
            return None
        return int(value) == 1

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        data = self.coordinator.data or {}
        return {
            "sub_latch": data.get("subLatch"),
            "system_mode": data.get("systemMode"),
            "card_count": data.get("cardCount"),
            "password_count": data.get("passwordCount"),
            "fingerprint_count": data.get("fingerPrintCount"),
        }

    async def async_lock(self, **kwargs: Any) -> None:
        await self.coordinator.client.close(self.coordinator.device_id)
        await self.coordinator.async_request_refresh()

    async def async_unlock(self, **kwargs: Any) -> None:
        await self.coordinator.client.open(self.coordinator.device_id)
        await self.coordinator.async_request_refresh()

    async def async_open(self, **kwargs: Any) -> None:
        await self.coordinator.client.open(self.coordinator.device_id)
        await self.coordinator.async_request_refresh()
