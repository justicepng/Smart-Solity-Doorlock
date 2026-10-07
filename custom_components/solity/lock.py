"""Lock platform for Smart Solity.

The deadbolt state comes from the slow status poll (``get_status``), but that
wakes the lock, so it runs only every ~30 min — far too slow to show a door
opening. So the lock ALSO watches the fast event-log coordinator: when a new
open/close entry appears (within seconds) it reflects that on the tile for a
short window, then reverts to locked. With auto-lock the deadbolt re-engages
almost immediately, so this provides accurate activity windows.
"""
from __future__ import annotations

import logging
import re
from typing import Any

from homeassistant.components.lock import LockEntity, LockEntityFeature
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import async_call_later
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import SolityConfigEntry
from .const import (
    CONF_AUTO_CLOSE_SECONDS,
    CONF_DEVICE_ID,
    CONF_NICKNAME,
    DEFAULT_AUTO_CLOSE_SECONDS,
    DOMAIN,
    LOG_CODE_CLOSE,
    LOG_CODE_OPEN,
    LOG_CODE_OPEN_LONG,
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
    """Set up the Solity lock."""
    data = entry.runtime_data
    async_add_entities([SolityLock(data.status, data.log, entry)])


class SolityLock(CoordinatorEntity[SolityStatusCoordinator], LockEntity, RestoreEntity):
    """A Solity door lock controlled through the cloud API.

    Availability tracks the fast log coordinator so the lock remains available
    even if background get_status polling times out or fails (e.g. asleep lock).
    """

    _attr_has_entity_name = True
    _attr_name = None  # use the device name
    _attr_supported_features = LockEntityFeature.OPEN

    def __init__(
        self,
        status_coord: SolityStatusCoordinator,
        log_coord: SolityLogCoordinator,
        entry: SolityConfigEntry,
    ) -> None:
        super().__init__(status_coord)
        self._entry = entry
        self._log_coord = log_coord
        self._device_id = entry.data[CONF_DEVICE_ID]
        self._attr_unique_id = f"{self._device_id}_lock"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, self._device_id)},
            name=entry.data.get(CONF_NICKNAME) or "Solity Doorlock",
            manufacturer=MANUFACTURER,
            model=MODEL,
        )
        self._auto_close = entry.options.get(
            CONF_AUTO_CLOSE_SECONDS, DEFAULT_AUTO_CLOSE_SECONDS
        )
        self._last_log_dt: str | None = None
        self._override: bool | None = None  # transient lock state: True=locked, False=unlocked
        self._cancel_revert = None
        self._is_locking: bool = False
        self._is_unlocking: bool = False

    @property
    def _face_map(self) -> dict[str, str]:
        return get_face_map(self._entry.options)

    @property
    def available(self) -> bool:
        """Lock stays available as long as log coordinator is connected."""
        return self._log_coord.last_update_success

    async def async_added_to_hass(self) -> None:
        """Seed the log baseline and subscribe to the fast log coordinator."""
        await super().async_added_to_hass()

        # Restore last state if available
        last_state = await self.async_get_last_state()
        if last_state is not None and last_state.state in ("locked", "unlocked"):
            self._override = (last_state.state == "locked")
        else:
            self._override = True  # Default to locked for auto-locking doorlocks

        logs = self._log_coord.data or []
        if logs:
            self._last_log_dt = logs[0].get("logDateTime")

        self.async_on_remove(
            self._log_coord.async_add_listener(self._handle_log_update)
        )

    @callback
    def _handle_log_update(self) -> None:
        """React to a brand-new access-log entry (newest-first list)."""
        logs = self._log_coord.data or []
        if not logs:
            return
        newest_dt = logs[0].get("logDateTime") or ""
        if self._last_log_dt is None:
            self._last_log_dt = newest_dt
            if self._override is None:
                self._override = True
            self.async_write_ha_state()
            return

        if newest_dt <= self._last_log_dt:
            return

        self._last_log_dt = newest_dt
        code = str(logs[0].get("logCode"))
        _LOGGER.info("Solity new log detected: code=%s, dt=%s, log=%s", code, newest_dt, logs[0])

        if code in (LOG_CODE_OPEN, LOG_CODE_OPEN_LONG):
            self._set_override(False)  # unlocked
        elif code == LOG_CODE_CLOSE:
            self._set_override(True)  # locked

    @callback
    def _set_override(self, locked: bool) -> None:
        """Show a transient lock state, auto-reverting to locked state."""
        self._override = locked
        if self._cancel_revert is not None:
            self._cancel_revert()
            self._cancel_revert = None

        # When unlocked (open event detected), schedule auto-revert to locked
        if not locked:
            self._cancel_revert = async_call_later(
                self.hass, self._auto_close, self._revert_to_locked
            )
        self.async_write_ha_state()

    @callback
    def _revert_to_locked(self, _now) -> None:
        """Revert lock to locked state after auto-close timeout."""
        _LOGGER.debug("Solity auto-close delay passed -> reverting to locked")
        self._override = True  # Solity auto-locks physically!
        self._cancel_revert = None
        self.async_write_ha_state()

    @property
    def is_locking(self) -> bool:
        """Return True if the lock is currently locking."""
        return self._is_locking

    @property
    def is_unlocking(self) -> bool:
        """Return True if the lock is currently unlocking."""
        return self._is_unlocking

    @property
    def is_locked(self) -> bool | None:
        """Return True if locked, False if unlocked."""
        if self._is_unlocking:
            return False
        if self._is_locking:
            return True
        if self._override is not None:
            return self._override
        data = self.coordinator.data or {}
        value = data.get("deadBolt")
        if value is not None:
            return int(value) == 1
        # Default for auto-locking doorlocks: always physically locked
        return True

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        data = self.coordinator.data or {}
        logs = self._log_coord.data or []
        latest = logs[0] if logs else {}
        parsed = format_access_log(latest, self._face_map)

        attrs = {
            "sub_latch": data.get("subLatch"),
            "system_mode": data.get("systemMode"),
            "card_count": data.get("cardCount"),
            "password_count": data.get("passwordCount"),
            "fingerprint_count": data.get("fingerPrintCount"),
            "live_state": self._override is not None,
            "last_access_who": parsed["who"],
            "last_access_method": parsed["method"],
            "last_access_time": latest.get("logDateTime"),
        }
        return attrs

    async def async_lock(self, **kwargs: Any) -> None:
        """Lock the door with duplicate click guard and progress feedback."""
        if self._is_locking or self._is_unlocking:
            _LOGGER.warning("Solity doorlock command already in progress, ignoring duplicate lock request")
            return

        self._is_locking = True
        self.async_write_ha_state()

        try:
            res = await self.coordinator.client.close(self._device_id)
            _LOGGER.info("Solity lock command response: %s", res)
            self._set_override(True)
            await self._log_coord.async_request_refresh()
        except Exception as err:
            _LOGGER.error("Solity remote lock failed: %s", err)
            raise
        finally:
            self._is_locking = False
            self.async_write_ha_state()

    async def async_unlock(self, **kwargs: Any) -> None:
        """Unlock the door with duplicate click guard and progress feedback."""
        if self._is_unlocking or self._is_locking:
            _LOGGER.warning("Solity doorlock command already in progress, ignoring duplicate unlock request")
            return

        self._is_unlocking = True
        self.async_write_ha_state()

        try:
            res = await self.coordinator.client.open(self._device_id)
            _LOGGER.info("Solity unlock command response: %s", res)
            self._set_override(False)
            await self._log_coord.async_request_refresh()
        except Exception as err:
            _LOGGER.error("Solity remote unlock failed: %s", err)
            raise
        finally:
            self._is_unlocking = False
            self.async_write_ha_state()

    async def async_open(self, **kwargs: Any) -> None:
        """Open (unlock) the door."""
        await self.async_unlock(**kwargs)
