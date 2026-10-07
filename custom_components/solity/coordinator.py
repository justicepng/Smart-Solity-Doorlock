"""Data update coordinators for Smart Solity."""
from __future__ import annotations

import logging
import time
from datetime import timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import SolityAuthError, SolityClient, SolityError
from .const import (
    CONF_BLE_APP_KEY,
    CONF_BLE_MAC,
    CONF_MEMBER_ID,
    DOMAIN,
    TOLERATED_LOG_FAILURES,
    TOLERATED_STATUS_FAILURES,
)

_LOGGER = logging.getLogger(__name__)


class SolityStatusCoordinator(DataUpdateCoordinator[dict]):
    """Polls door-lock status (battery, deadbolt). Wakes the lock, so run slow."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        client: SolityClient,
        device_id: str,
        status_minutes: int,
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN}_status",
            update_interval=timedelta(minutes=status_minutes),
        )
        self.config_entry = entry
        self.client = client
        self.device_id = device_id
        self._failures = 0
        self.face_nicknames: dict[str, str] = {}
        self._last_face_sync: float = 0.0
        self.ble_mac: str = entry.data.get(CONF_BLE_MAC) or ""
        self.ble_app_key: str = entry.data.get(CONF_BLE_APP_KEY) or ""
        self.member_id: str = entry.data.get(CONF_MEMBER_ID) or ""

    async def async_sync_face_nicknames(self, force: bool = False) -> dict[str, str]:
        """Fetch face key nicknames from Solity Cloud and update cache."""
        now = time.monotonic()
        if not force and (now - self._last_face_sync < 180):
            return self.face_nicknames

        if not self.member_id or not self.ble_mac:
            try:
                devices = await self.client.get_devices()
                for dev in devices:
                    if dev.get("myDeviceId") == self.device_id:
                        self.member_id = dev.get("myDeviceMemberId") or self.member_id
                        self.ble_mac = dev.get("myDeviceBleMacAddr") or self.ble_mac
                        self.ble_app_key = dev.get("regDeviceAppKey") or self.ble_app_key
                        break
            except Exception as err:
                _LOGGER.debug("Could not refresh device metadata for face sync: %s", err)

        if self.member_id:
            try:
                nicks = await self.client.get_face_nicknames(self.device_id, self.member_id)
                if nicks:
                    self.face_nicknames = nicks
                    self._last_face_sync = now
                    _LOGGER.info(
                        "Synchronized %d face key nicknames from Solity Cloud: %s",
                        len(nicks),
                        list(nicks.values()),
                    )
            except Exception as err:
                _LOGGER.debug("Could not fetch face nicknames: %s", err)

        return self.face_nicknames

    async def _async_update_data(self) -> dict:
        # Always sync latest face nicknames from Solity Cloud
        await self.async_sync_face_nicknames(force=True)

        try:
            data = await self.client.get_status(self.device_id)
        except SolityAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except SolityError as err:
            # A sleeping lock / cloud timeout misses a poll. Don't flap the
            # entities to 'unavailable' on the first miss — keep last values.
            self._failures += 1
            if self.data and self._failures <= TOLERATED_STATUS_FAILURES:
                _LOGGER.debug(
                    "Solity status miss %s/%s, keeping last data: %s",
                    self._failures,
                    TOLERATED_STATUS_FAILURES,
                    err,
                )
                return self.data
            _LOGGER.debug("Solity status fetch failed (lock sleeping or no gateway): %s", err)
            # Return empty dict instead of raising UpdateFailed to avoid flapping entities
            return self.data or {}

        if not data:
            self._failures += 1
            if self.data and self._failures <= TOLERATED_STATUS_FAILURES:
                return self.data
            return {}

        self._failures = 0
        return data


class SolityLogCoordinator(DataUpdateCoordinator[list[dict]]):
    """Polls the access log (cloud-stored). Cheap — run fast for live events."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        client: SolityClient,
        device_id: str,
        log_seconds: int,
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN}_log",
            update_interval=timedelta(seconds=log_seconds),
        )
        self.config_entry = entry
        self.client = client
        self.device_id = device_id
        self._failures = 0

    async def _async_update_data(self) -> list[dict]:
        try:
            data = await self.client.retrieve_log(self.device_id, length=20)
            self._failures = 0

            # If recent logs mention face recognition, ensure face nicknames are fresh
            if data and hasattr(self.config_entry, "runtime_data") and self.config_entry.runtime_data:
                latest = data[0]
                is_face = (str(latest.get("mediaType")) == "15") or ("얼굴" in (latest.get("logMessage") or ""))
                status_coord = self.config_entry.runtime_data.status
                if status_coord:
                    now = time.monotonic()
                    last_sync = getattr(status_coord, "_last_face_sync", 0.0)
                    if (is_face and (now - last_sync > 60)) or (now - last_sync > 600):
                        await status_coord.async_sync_face_nicknames(force=True)

            return data
        except SolityAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except SolityError as err:
            self._failures += 1
            if self.data and self._failures <= TOLERATED_LOG_FAILURES:
                _LOGGER.debug(
                    "Solity log miss %s/%s, keeping last data: %s",
                    self._failures,
                    TOLERATED_LOG_FAILURES,
                    err,
                )
                return self.data
            _LOGGER.warning("Solity log fetch failed: %s", err)
            raise UpdateFailed(str(err)) from err
