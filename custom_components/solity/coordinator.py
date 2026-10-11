"""Data update coordinators for Smart Solity."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.event import async_call_later
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
        self.gateway_conn_status: str | None = None

    async def async_sync_face_nicknames(self, force: bool = False) -> dict[str, str]:
        """Fetch face key nicknames from Solity Cloud and update cache."""
        now = time.monotonic()
        if not force and (now - self._last_face_sync < 180):
            return self.face_nicknames

        try:
            devices = await self.client.get_devices()
            for dev in devices:
                if dev.get("myDeviceId") == self.device_id:
                    self.member_id = dev.get("myDeviceMemberId") or self.member_id
                    self.ble_mac = dev.get("myDeviceBleMacAddr") or self.ble_mac
                    self.ble_app_key = dev.get("regDeviceAppKey") or self.ble_app_key
                    self.gateway_conn_status = dev.get("gatewayConnStatus")
                    break
        except Exception as err:
            _LOGGER.debug("Could not refresh device metadata: %s", err)

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
        self._recent_ble_log: dict | None = None
        self._last_ble_event_time: float = 0.0
        self._cancel_ble_fallback = None

    @callback
    def handle_ble_wake(self, address: str, rssi: int | None = None) -> None:
        """React instantaneously to BLE wake advertisement from Bluetooth proxy."""
        now = time.monotonic()
        # Cooldown: 15 seconds debounce to avoid multiple triggers for a single door opening
        if now - self._last_ble_event_time < 15.0:
            return

        self._last_ble_event_time = now
        baseline_dt = (self.data[0].get("logDateTime") or "") if self.data else ""

        # Immediately trigger fast cloud refresh
        self.hass.async_create_task(self.async_request_refresh())

        if self._cancel_ble_fallback is not None:
            self._cancel_ble_fallback()
            self._cancel_ble_fallback = None

        @callback
        def _trigger_fallback(_now: Any) -> None:
            self._cancel_ble_fallback = None
            current_dt = (self.data[0].get("logDateTime") or "") if self.data else ""
            if current_dt > baseline_dt:
                # Cloud already brought the new detailed log!
                return

            _LOGGER.info(
                "Solity cloud log not received within 3.5s of BLE wake (gateway offline); triggering local BLE open event"
            )
            now_dt = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            ble_log = {
                "logDateTime": now_dt,
                "logCode": "1",  # Open
                "logType": "LOCKER_STATUS_TYPE",
                "mediaType": "ble",
                "nickname": "",
                "logMessage": "현관 도어락이 열렸습니다. (블루투스 감지)",
                "ble_fallback": True,
            }
            self._recent_ble_log = ble_log
            current = self.data or []
            self.async_set_updated_data([ble_log] + current)

        self._cancel_ble_fallback = async_call_later(self.hass, 3.5, _trigger_fallback)

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

            # Preserve recent BLE fallback log at head if cloud hasn't caught up
            if self._recent_ble_log and data:
                newest_cloud_dt = data[0].get("logDateTime") or ""
                if newest_cloud_dt < self._recent_ble_log.get("logDateTime", ""):
                    data = [self._recent_ble_log] + data
                else:
                    self._recent_ble_log = None

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
