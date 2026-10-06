"""Event platform for Smart Solity — fires on each new access-log entry."""
from __future__ import annotations

from datetime import datetime
import logging

from homeassistant.components.event import EventEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import async_call_later
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import SolityConfigEntry
from .const import (
    AUTO_CLOSE_LOG_TYPE,
    AUTO_CLOSE_MESSAGE,
    AUTO_CLOSE_METHOD,
    CONF_AUTO_CLOSE_SECONDS,
    CONF_DEVICE_ID,
    CONF_NICKNAME,
    DEFAULT_AUTO_CLOSE_SECONDS,
    DOMAIN,
    EVENT_CLOSE,
    EVENT_OPEN,
    EVENT_OTHER,
    EVENT_TYPES,
    LOG_CODE_CLOSE,
    LOG_CODE_OPEN,
    LOG_CODE_OPEN_LONG,
    MANUFACTURER,
    METHOD_MAP,
    MODEL,
)
from .coordinator import SolityLogCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SolityConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the Solity door-event entity."""
    async_add_entities([SolityDoorEvent(entry.runtime_data.log, entry)])


def _event_type(log_code: str | None) -> str:
    code = str(log_code)
    if code in (LOG_CODE_OPEN, LOG_CODE_OPEN_LONG):
        return EVENT_OPEN
    if code == LOG_CODE_CLOSE:
        return EVENT_CLOSE
    return EVENT_OTHER


class SolityDoorEvent(CoordinatorEntity[SolityLogCoordinator], EventEntity, RestoreEntity):
    """Fires an HA event for every new access-log entry (open/close/other)."""

    _attr_has_entity_name = True
    _attr_translation_key = "door_event"
    _attr_event_types = EVENT_TYPES
    _attr_icon = "mdi:door"

    def __init__(self, coordinator: SolityLogCoordinator, entry: SolityConfigEntry) -> None:
        super().__init__(coordinator)
        self._device_id = entry.data[CONF_DEVICE_ID]
        self._attr_unique_id = f"{self._device_id}_door_event"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, self._device_id)},
            name=entry.data.get(CONF_NICKNAME) or "Solity Doorlock",
            manufacturer=MANUFACTURER,
            model=MODEL,
        )
        self._auto_close = entry.options.get(
            CONF_AUTO_CLOSE_SECONDS, DEFAULT_AUTO_CLOSE_SECONDS
        )
        self._last_dt: str | None = None
        self._cancel_close = None

    async def async_added_to_hass(self) -> None:
        """Seed the baseline so startup does not replay history."""
        await super().async_added_to_hass()
        logs = self.coordinator.data or []
        if logs:
            self._last_dt = logs[0].get("logDateTime")

    @callback
    def _handle_coordinator_update(self) -> None:
        logs = self.coordinator.data or []
        if not logs:
            return

        if self._last_dt is None:
            self._last_dt = logs[0].get("logDateTime")
            super()._handle_coordinator_update()
            return

        # Collect entries newer than the last seen, oldest-first.
        new_entries = [
            log
            for log in logs
            if (log.get("logDateTime") or "") > self._last_dt
        ]
        for log in reversed(new_entries):
            method_code = str(log.get("mediaType") or "")
            who = log.get("nickname") or ""
            msg = log.get("logMessage") or ""

            is_inside = (not who) or ("실내" in msg) or ("수동" in msg) or (method_code in ("0", "5"))
            if is_inside:
                display_who = "실내"
                method_name = METHOD_MAP.get(method_code, "실내 개폐")
                direction = "inside"
            else:
                display_who = who
                method_name = METHOD_MAP.get(method_code, method_code)
                direction = "outside"

            self._trigger_event(
                _event_type(log.get("logCode")),
                {
                    "datetime": log.get("logDateTime"),
                    "who": display_who,
                    "method": method_name,
                    "method_code": method_code,
                    "direction": direction,
                    "log_type": log.get("logType"),
                    "log_code": log.get("logCode"),
                    "message": log.get("logMessage"),
                    "synthetic": False,
                },
            )

        if new_entries:
            self._last_dt = logs[0].get("logDateTime")
            # The device logs opens only. If the newest event is an open,
            # schedule a synthesized 'close' to mark the auto-lock.
            if _event_type(logs[0].get("logCode")) == EVENT_OPEN:
                self._schedule_auto_close()

        super()._handle_coordinator_update()

    @callback
    def _schedule_auto_close(self) -> None:
        if self._cancel_close is not None:
            self._cancel_close()
        self._cancel_close = async_call_later(
            self.hass, self._auto_close, self._fire_auto_close
        )

    @callback
    def _fire_auto_close(self, _now) -> None:
        self._cancel_close = None
        self._trigger_event(
            EVENT_CLOSE,
            {
                "datetime": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "who": None,
                "method": METHOD_MAP.get(AUTO_CLOSE_METHOD, AUTO_CLOSE_METHOD),
                "method_code": AUTO_CLOSE_METHOD,
                "direction": "auto",
                "log_type": AUTO_CLOSE_LOG_TYPE,
                "log_code": LOG_CODE_CLOSE,
                "message": AUTO_CLOSE_MESSAGE,
                "synthetic": True,
            },
        )
        self.async_write_ha_state()
