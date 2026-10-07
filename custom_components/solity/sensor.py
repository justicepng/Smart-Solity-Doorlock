"""Sensor platform for Smart Solity (battery + last access)."""
from __future__ import annotations

import logging
import re
from typing import Any

from homeassistant.components.sensor import (
    RestoreSensor,
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.const import PERCENTAGE, EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import SolityConfigEntry
from .const import (
    CONF_DEVICE_ID,
    CONF_NICKNAME,
    DOMAIN,
    MANUFACTURER,
    METHOD_MAP,
    MODEL,
    format_access_log,
    get_face_map,
)
from .coordinator import SolityLogCoordinator, SolityStatusCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SolityConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the Solity sensors."""
    data = entry.runtime_data
    async_add_entities(
        [
            SolityBatterySensor(data.status, data.log, entry),
            SolityLastAccessSensor(data.log, entry),
        ]
    )


def _device_info(entry: SolityConfigEntry) -> DeviceInfo:
    device_id = entry.data[CONF_DEVICE_ID]
    return DeviceInfo(
        identifiers={(DOMAIN, device_id)},
        name=entry.data.get(CONF_NICKNAME) or "Solity Doorlock",
        manufacturer=MANUFACTURER,
        model=MODEL,
    )


class SolityBatterySensor(CoordinatorEntity[SolityStatusCoordinator], RestoreSensor):
    """Battery level reported by the door lock."""

    _attr_has_entity_name = True
    _attr_translation_key = "battery"
    _attr_device_class = SensorDeviceClass.BATTERY
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(
        self,
        coordinator: SolityStatusCoordinator,
        log_coord: SolityLogCoordinator,
        entry: SolityConfigEntry,
    ) -> None:
        super().__init__(coordinator)
        self._log_coord = log_coord
        self._attr_unique_id = f"{entry.data[CONF_DEVICE_ID]}_battery"
        self._attr_device_info = _device_info(entry)
        self._restored_battery: int | None = None

    @property
    def available(self) -> bool:
        """Keep available as long as we have a restored value or log coordinator is ok."""
        if self._restored_battery is not None:
            return True
        return self._log_coord.last_update_success

    async def async_added_to_hass(self) -> None:
        """Handle entity which will be added."""
        await super().async_added_to_hass()
        if (last_state := await self.async_get_last_sensor_data()) is not None:
            try:
                if last_state.native_value is not None:
                    self._restored_battery = int(last_state.native_value)
            except (ValueError, TypeError):
                pass

    @property
    def native_value(self) -> int | None:
        value = (self.coordinator.data or {}).get("battery")
        if value is not None:
            try:
                parsed = int(value)
                self._restored_battery = parsed
                return parsed
            except (ValueError, TypeError):
                pass
        return self._restored_battery


class SolityLastAccessSensor(CoordinatorEntity[SolityLogCoordinator], SensorEntity):
    """Most recent access-log entry (who / how / when)."""

    _attr_has_entity_name = True
    _attr_translation_key = "last_access"
    _attr_icon = "mdi:history"

    def __init__(self, coordinator: SolityLogCoordinator, entry: SolityConfigEntry) -> None:
        super().__init__(coordinator)
        self._entry = entry
        self._attr_unique_id = f"{entry.data[CONF_DEVICE_ID]}_last_access"
        self._attr_device_info = _device_info(entry)

    @property
    def _latest(self) -> dict | None:
        logs = self.coordinator.data or []
        return logs[0] if logs else None

    @property
    def _face_map(self) -> dict[str, str]:
        return get_face_map(self._entry.options)

    @property
    def native_value(self) -> str | None:
        entry = self._latest
        if not entry:
            return None
        parsed = format_access_log(entry, self._face_map)
        msg = parsed.get("message")
        if msg:
            return msg[:255]

        who = parsed.get("who") or ""
        method_name = parsed.get("method") or ""
        if who and method_name:
            return f"{who} ({method_name})"
        return (who or method_name or None)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        entry = self._latest or {}
        parsed = format_access_log(entry, self._face_map)

        return {
            "datetime": entry.get("logDateTime"),
            "who": parsed["who"],
            "method": parsed["method"],
            "method_code": parsed["method_code"],
            "direction": parsed["direction"],
            "log_type": entry.get("logType"),
            "log_code": entry.get("logCode"),
            "message": parsed["message"],
        }
