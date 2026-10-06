"""Data update coordinators for Smart Solity."""
from __future__ import annotations

import logging
from datetime import timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import SolityAuthError, SolityClient, SolityError
from .const import DOMAIN, TOLERATED_LOG_FAILURES, TOLERATED_STATUS_FAILURES

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

    async def _async_update_data(self) -> dict:
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
