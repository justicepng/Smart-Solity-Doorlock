"""Sensor platform for Smart Solity (battery + last access)."""
from __future__ import annotations

import logging
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
        self._attr_unique_id = f"{entry.data[CONF_DEVICE_ID]}_last_access"
        self._attr_device_info = _device_info(entry)

    @property
    def _latest(self) -> dict | None:
        logs = self.coordinator.data or []
        return logs[0] if logs else None

    @property
    def native_value(self) -> str | None:
        entry = self._latest
        if not entry:
            return None
        msg = entry.get("logMessage")
        who = entry.get("nickname") or ""
        method_code = str(entry.get("mediaType") or "")
        
        # Check inside opening
        is_inside = (not who) or ("실내" in (msg or "")) or ("수동" in (msg or "")) or (method_code in ("0", "5"))
        if is_inside:
            if msg and ("실내" in msg or "수동" in msg):
                return msg[:255]
            return "실내에서 문을 열었습니다."

        if msg:
            return msg[:255]
        method_name = METHOD_MAP.get(method_code, method_code)
        if who and method_name:
            return f"{who} ({method_name})"
        return (f"{who} {method_name}".strip() or None)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        entry = self._latest or {}
        method_code = str(entry.get("mediaType") or "")
        who = entry.get("nickname") or ""
        msg = entry.get("logMessage") or ""

        is_inside = (not who) or ("실내" in msg) or ("수동" in msg) or (method_code in ("0", "5"))
        if is_inside:
            display_who = "실내"
            method_name = METHOD_MAP.get(method_code, "실내 개폐")
            direction = "inside"
        else:
            display_who = who
            method_name = METHOD_MAP.get(method_code, method_code)
            direction = "outside"

        return {
            "datetime": entry.get("logDateTime"),
            "who": display_who,
            "method": method_name,
            "method_code": method_code,
            "direction": direction,
            "log_type": entry.get("logType"),
            "log_code": entry.get("logCode"),
            "message": entry.get("logMessage"),
        }
