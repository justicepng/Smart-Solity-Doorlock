"""Lock platform for Smart Solity.

The deadbolt state comes from the slow status poll (``get_status``), but that
wakes the lock, so it runs only every ~30 min — far too slow to show a door
opening. So the lock ALSO watches the fast event-log coordinator: when a new
open/close entry appears (within seconds) it reflects that on the tile for a
short window, then reverts to the authoritative status poll. With auto-lock the
deadbolt re-engages almost immediately, so this is a visible activity window.
"""
from __future__ import annotations

from typing import Any

from homeassistant.components.lock import LockEntity, LockEntityFeature
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import async_call_later
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
    MODEL,
)
from .coordinator import SolityLogCoordinator, SolityStatusCoordinator


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SolityConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the Solity lock."""
    data = entry.runtime_data
    async_add_entities([SolityLock(data.status, data.log, entry)])


class SolityLock(CoordinatorEntity[SolityStatusCoordinator], LockEntity):
    """A Solity door lock controlled through the cloud API.

    Availability tracks the status coordinator; the live open/close state is
    layered on top from the log coordinator.
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
        self._log_coord = log_coord
        device_id = entry.data[CONF_DEVICE_ID]
        self._attr_unique_id = f"{device_id}_lock"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, device_id)},
            name=entry.data.get(CONF_NICKNAME) or "Solity Doorlock",
            manufacturer=MANUFACTURER,
            model=MODEL,
        )
        self._auto_close = entry.options.get(
            CONF_AUTO_CLOSE_SECONDS, DEFAULT_AUTO_CLOSE_SECONDS
        )
        self._last_log_dt: str | None = None
        self._override: bool | None = None  # transient lock state from the log
        self._cancel_revert = None

    async def async_added_to_hass(self) -> None:
        """Seed the log baseline and subscribe to the fast log coordinator."""
        await super().async_added_to_hass()
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
            return
        if newest_dt <= self._last_log_dt:
            return

        self._last_log_dt = newest_dt
        code = logs[0].get("logCode")
        if code in (LOG_CODE_OPEN, LOG_CODE_OPEN_LONG):
            self._set_override(False)  # unlocked
        elif code == LOG_CODE_CLOSE:
            self._set_override(True)  # locked
        # Other codes (battery, settings, etc.) don't change the lock state.

    @callback
    def _set_override(self, locked: bool) -> None:
        """Show a transient lock state, auto-reverting to the status poll."""
        self._override = locked
        if self._cancel_revert is not None:
            self._cancel_revert()
            self._cancel_revert = None
        # Only an 'open' override auto-reverts (back to the locked deadbolt);
        # a 'locked' override just holds until the next signal.
        if not locked:
            self._cancel_revert = async_call_later(
                self.hass, self._auto_close, self._clear_override
            )
        self.async_write_ha_state()

    @callback
    def _clear_override(self, _now) -> None:
        self._override = None
        self._cancel_revert = None
        self.async_write_ha_state()

    @property
    def is_locked(self) -> bool | None:
        """Prefer the live log-derived state, else the deadbolt from status."""
        if self._override is not None:
            return self._override
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
            "live_state": self._override is not None,
        }

    async def async_lock(self, **kwargs: Any) -> None:
        await self.coordinator.client.close(self.coordinator.device_id)
        self._set_override(True)
        await self.coordinator.async_request_refresh()

    async def async_unlock(self, **kwargs: Any) -> None:
        await self.coordinator.client.open(self.coordinator.device_id)
        self._set_override(False)
        await self.coordinator.async_request_refresh()

    async def async_open(self, **kwargs: Any) -> None:
        await self.coordinator.client.open(self.coordinator.device_id)
        self._set_override(False)
        await self.coordinator.async_request_refresh()
