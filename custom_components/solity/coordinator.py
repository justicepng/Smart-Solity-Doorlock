"""Data update coordinators for Smart Solity."""
from __future__ import annotations

import asyncio
import logging
import re
import time
from datetime import datetime, timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.event import async_call_later
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import SolityAuthError, SolityClient, SolityError
from .const import (
    CONF_BLE_APP_KEY,
    CONF_BLE_MAC,
    CONF_MEMBER_ID,
    DOMAIN,
    LOG_CODE_CLOSE,
    LOG_MEDIA_REMOTE,
    OPEN_LOG_CODES,
    RECOVER_DELAYS,
    TOLERATED_LOG_FAILURES,
    TOLERATED_STATUS_FAILURES,
    VERIFY_BACKOFF_MAX,
    VERIFY_CLOSED_FAST_TRIES,
    VERIFY_CLOSED_RECHECK,
    VERIFY_MAX_POLLS,
    VERIFY_OPEN_FIRST,
    VERIFY_POLL_RETRIES,
    VERIFY_POLL_TIMEOUT,
    VERIFY_TRACE_LEN,
    VERIFY_UNKNOWN_RECHECK,
    VERIFY_WINDOW,
    get_face_map,
)

_LOGGER = logging.getLogger(__name__)


def _as_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


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
        self._last_device_metadata: dict[str, Any] | None = None
        self.last_poll_ok = False
        self._verify_task: asyncio.Task | None = None
        self._recover_task: asyncio.Task | None = None
        self._last_log_dt: str | None = None
        self.last_verify_seconds: float | None = None
        self.last_verify_polls: int | None = None
        self.last_verify_result: str | None = None
        self.last_verify_trace: list[dict[str, Any]] = []
        self._fast = False
        self._door_assumed = False

    async def async_sync_face_nicknames(self, force: bool = False) -> dict[str, str]:
        """Fetch face key nicknames from Solity Cloud and update cache."""
        now = time.monotonic()
        if not force and (now - self._last_face_sync < 180):
            return self.face_nicknames

        try:
            devices = await self.client.get_devices()
            for dev in devices:
                if dev.get("myDeviceId") == self.device_id:
                    self._last_device_metadata = dev
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
        # Always sync latest face nicknames and device info from Solity Cloud (passive cloud read)
        await self.async_sync_face_nicknames(force=True)

        base_data: dict[str, Any] = {}
        if self._last_device_metadata:
            dev = self._last_device_metadata
            if dev.get("battery") is not None:
                base_data["battery"] = dev.get("battery")
            if dev.get("lockerStatus") is not None:
                # Solity Cloud lockerStatus: "0" = locked, "1" = unlocked
                # Deadbolt protocol: 1 = locked, 0 = unlocked
                base_data["deadBolt"] = 1 if str(dev.get("lockerStatus")) == "0" else 0

        def _get_merged_data() -> dict:
            return {**(self.data or {}), **base_data}

        # If gateway is offline, skip physical lock wake-up (get_status) to avoid timeouts and gateway buffer lockups
        if self.gateway_conn_status == "N":
            self.last_poll_ok = False
            _LOGGER.debug(
                "Solity gateway is offline (gatewayConnStatus=N); skipping wake-up get_status poll"
            )
            return _get_merged_data()

        try:
            if self._fast:
                async with asyncio.timeout(VERIFY_POLL_TIMEOUT):
                    data = await self.client.get_status(self.device_id)
            else:
                data = await self.client.get_status(self.device_id)
        except SolityAuthError as err:
            self.last_poll_ok = False
            raise ConfigEntryAuthFailed(str(err)) from err
        except (SolityError, TimeoutError) as err:
            self.last_poll_ok = False
            if self._fast and self.data:
                _LOGGER.debug("Solity fast status poll missed: %r", err)
                return self.data
            self._failures += 1
            self._schedule_recover()
            if self.data and self._failures <= TOLERATED_STATUS_FAILURES:
                _LOGGER.debug(
                    "Solity status miss %s/%s, keeping last data: %s",
                    self._failures,
                    TOLERATED_STATUS_FAILURES,
                    err,
                )
                return _get_merged_data()
            _LOGGER.debug("Solity status fetch failed (lock sleeping or no gateway): %s", err)
            return _get_merged_data()

        if not data:
            self.last_poll_ok = False
            if self._fast and self.data:
                return self.data
            self._failures += 1
            self._schedule_recover()
            if self.data and self._failures <= TOLERATED_STATUS_FAILURES:
                return _get_merged_data()
            return base_data

        self._failures = 0
        self.last_poll_ok = True
        self._door_assumed = False
        return {**base_data, **data}

    async def _async_fast_refresh(self) -> None:
        """One post-open status poll with the short timeout."""
        self._fast = True
        try:
            await self.async_refresh()
        finally:
            self._fast = False

    @property
    def is_locked(self) -> bool | None:
        """Real deadbolt state (1 = locked)."""
        value = _as_int((self.data or {}).get("deadBolt"))
        return None if value is None else value == 1

    @property
    def door_open(self) -> bool | None:
        """Door state from the door-detect latch (subLatch 0 = open)."""
        value = _as_int((self.data or {}).get("subLatch"))
        if value is not None:
            return value == 0
        if self.is_locked is True:
            return False
        return None

    @property
    def verifying(self) -> bool:
        return self._verify_task is not None and not self._verify_task.done()

    @property
    def recovering(self) -> bool:
        return self._recover_task is not None and not self._recover_task.done()

    @callback
    def mark_unlocked(self, assume_open: bool = False) -> None:
        """An open happened: drop the cached 'locked' so it reflects unlocked immediately."""
        data = dict(self.data or {})
        data["deadBolt"] = 0
        if assume_open:
            data["subLatch"] = 0
        else:
            data.pop("subLatch", None)
        self._door_assumed = assume_open
        self.last_poll_ok = False
        self.async_set_updated_data(data)

    @callback
    def mark_locked(self) -> None:
        """A close/lock happened: set deadBolt=1 and subLatch=1."""
        data = dict(self.data or {})
        data["deadBolt"] = 1
        data["subLatch"] = 1
        self._door_assumed = False
        self.last_poll_ok = True
        self.async_set_updated_data(data)

    @callback
    def _drop_assumed_door(self) -> None:
        """The lock didn't answer: don't keep showing a guessed 'open'."""
        if self._door_assumed and self.data:
            self._door_assumed = False
            data = dict(self.data)
            data.pop("subLatch", None)
            self.data = data

    @callback
    def start_verify(self) -> None:
        """(Re)start the post-open burst of status polls."""
        if self._verify_task is not None and not self._verify_task.done():
            self._verify_task.cancel()
        if self.recovering:
            if self._recover_task is not None:
                self._recover_task.cancel()
                self._recover_task = None
        self._verify_task = self.config_entry.async_create_background_task(
            self.hass, self._async_verify(), f"{DOMAIN}_verify_lock"
        )
        self.async_update_listeners()

    async def _async_verify(self) -> None:
        """Poll until the deadbolt reads locked, pacing by the door state."""
        loop = asyncio.get_running_loop()
        start = loop.time()
        polls = 0
        open_wait = VERIFY_OPEN_FIRST
        closed_streak = 0
        trace: list[dict[str, Any]] = []
        self.last_verify_trace = trace
        try:
            while polls < VERIFY_MAX_POLLS:
                for _ in range(1 + VERIFY_POLL_RETRIES):
                    await self._async_fast_refresh()
                    polls += 1
                    if self.last_poll_ok:
                        break
                elapsed = round(loop.time() - start, 1)

                if not self.last_poll_ok:
                    trace.append({"t": elapsed, "result": "no_answer"})
                    self._drop_assumed_door()
                    wait = VERIFY_UNKNOWN_RECHECK
                elif self.is_locked:
                    trace.append({"t": elapsed, "result": "locked"})
                    self._record_verify(elapsed, polls, "locked", trace)
                    return
                elif self.door_open is False:
                    trace.append({"t": elapsed, "result": "closed_unlocked"})
                    closed_streak += 1
                    if closed_streak <= VERIFY_CLOSED_FAST_TRIES:
                        wait = VERIFY_CLOSED_RECHECK
                    else:
                        wait = open_wait
                        open_wait = min(open_wait * 2, VERIFY_BACKOFF_MAX)
                elif self.door_open is True:
                    trace.append({"t": elapsed, "result": "open"})
                    closed_streak = 0
                    wait = open_wait
                    open_wait = min(open_wait * 2, VERIFY_BACKOFF_MAX)
                else:
                    trace.append({"t": elapsed, "result": "unlocked"})
                    wait = VERIFY_UNKNOWN_RECHECK

                del trace[:-VERIFY_TRACE_LEN]
                self.async_update_listeners()
                if loop.time() - start + wait > VERIFY_WINDOW:
                    break
                await asyncio.sleep(wait)
            answered = any(x["result"] != "no_answer" for x in trace)
            self._record_verify(
                round(loop.time() - start, 1),
                polls,
                "still_unlocked" if answered else "no_answer",
                trace,
            )
        finally:
            if self._verify_task is asyncio.current_task():
                self._verify_task = None
                if not self.last_poll_ok:
                    self._schedule_recover()
                self.async_update_listeners()

    @callback
    def _schedule_recover(self) -> None:
        """Lock not answering: re-poll slowly until it does."""
        if self.recovering or self.verifying or self.data is None:
            return
        self._recover_task = self.config_entry.async_create_background_task(
            self.hass, self._async_recover(), f"{DOMAIN}_recover_lock"
        )

    async def _async_recover(self) -> None:
        attempt = 0
        _LOGGER.warning(
            "Solity lock is not answering status requests; retrying every %s-%ss until it does",
            RECOVER_DELAYS[0],
            RECOVER_DELAYS[-1],
        )
        while True:
            await asyncio.sleep(RECOVER_DELAYS[min(attempt, len(RECOVER_DELAYS) - 1)])
            attempt += 1
            await self.async_refresh()
            if self.last_poll_ok:
                break
        _LOGGER.info("Solity lock answers again (after %s retries)", attempt)
        if self._recover_task is asyncio.current_task():
            self._recover_task = None
        if self.is_locked is False:
            self.start_verify()
        else:
            self.async_update_listeners()

    @callback
    def _record_verify(
        self, seconds: float, polls: int, result: str, trace: list[dict[str, Any]]
    ) -> None:
        self.last_verify_seconds = seconds
        self.last_verify_polls = polls
        self.last_verify_result = result
        self.last_verify_trace = trace[-VERIFY_TRACE_LEN:]
        _LOGGER.info(
            "Solity deadbolt verify: %s after %ss (%s polls) %s",
            result,
            seconds,
            polls,
            self.last_verify_trace,
        )

    @callback
    def attach_log(self, log: SolityLogCoordinator) -> CALLBACK_TYPE:
        """Watch the fast event log; on a new open, invalidate + verify."""
        logs = log.data or []
        self._last_log_dt = logs[0].get("logDateTime") if logs else None

        @callback
        def _on_log() -> None:
            logs = log.data or []
            if not logs:
                return
            newest = logs[0].get("logDateTime") or ""
            if self._last_log_dt is None:
                self._last_log_dt = newest
                return
            if newest <= self._last_log_dt:
                return
            new = [x for x in logs if (x.get("logDateTime") or "") > self._last_log_dt]
            self._last_log_dt = newest
            opens = [x for x in new if str(x.get("logCode")) in OPEN_LOG_CODES]
            if not opens:
                closes = [x for x in new if str(x.get("logCode")) == LOG_CODE_CLOSE]
                if closes:
                    self.mark_locked()
                return
            if all(str(x.get("mediaType")) == LOG_MEDIA_REMOTE for x in opens):
                if not self.verifying:
                    self.mark_unlocked()
                    self.start_verify()
                return
            self.mark_unlocked(assume_open=True)
            self.start_verify()

        return log.async_add_listener(_on_log)


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
        self._ble_retry_handles: list[CALLBACK_TYPE] = []

    @callback
    def _cancel_ble_pending(self) -> None:
        """Cancel pending fallback timer and scheduled cloud refresh retries."""
        if self._cancel_ble_fallback is not None:
            self._cancel_ble_fallback()
            self._cancel_ble_fallback = None
        for handle in self._ble_retry_handles:
            try:
                handle()
            except Exception:
                pass
        self._ble_retry_handles = []

    @callback
    def _async_retry_refresh(self, _now: Any) -> None:
        """Poll cloud if a newer log has not yet arrived."""
        real_logs = [x for x in (self.data or []) if not x.get("ble_fallback")]
        current_dt = (real_logs[0].get("logDateTime") or "") if real_logs else ""
        if current_dt <= getattr(self, "_ble_baseline_dt", ""):
            self.hass.async_create_task(self.async_request_refresh())

    @callback
    def handle_ble_wake(self, address: str, rssi: int | None = None) -> None:
        """React instantaneously to BLE wake advertisement from Bluetooth proxy."""
        now = time.monotonic()
        # Cooldown: 15 seconds debounce to avoid multiple triggers for a single door opening
        if now - self._last_ble_event_time < 15.0:
            return

        self._last_ble_event_time = now
        real_logs = [x for x in (self.data or []) if not x.get("ble_fallback")]
        self._ble_baseline_dt = (real_logs[0].get("logDateTime") or "") if real_logs else ""

        _LOGGER.warning(
            "Solity door lock BLE wake detected: mac=%s, rssi=%s. Triggering cloud refresh sequence...",
            address,
            rssi,
        )

        self._cancel_ble_pending()

        # Immediately trigger fast cloud refresh and schedule follow-up polls (4s, 10s, 18s)
        self.hass.async_create_task(self.async_request_refresh())
        self._ble_retry_handles = [
            async_call_later(self.hass, delay, self._async_retry_refresh)
            for delay in (4.0, 10.0, 18.0)
        ]

        @callback
        def _trigger_fallback(_now: Any) -> None:
            self._cancel_ble_pending()
            real_logs = [x for x in (self.data or []) if not x.get("ble_fallback")]
            current_dt = (real_logs[0].get("logDateTime") or "") if real_logs else ""
            if current_dt > self._ble_baseline_dt:
                # Cloud already brought the new detailed log!
                _LOGGER.info(
                    "Solity cloud log updated in time (%s > %s), skipping fallback",
                    current_dt,
                    self._ble_baseline_dt,
                )
                return

            _LOGGER.warning(
                "Solity cloud log not received within 25s of BLE wake (gateway offline/slow); triggering local BLE open event"
            )
            now_dt = dt_util.now().strftime("%Y-%m-%d %H:%M:%S")
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
            current = [x for x in (self.data or []) if not x.get("ble_fallback")]
            self.async_set_updated_data([ble_log] + current)

        # Wait 25 seconds for cloud log (gateway/wifi sync) before using fallback
        self._cancel_ble_fallback = async_call_later(self.hass, 25.0, _trigger_fallback)

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

                if is_face:
                    val1 = str(latest.get("logMsgVal1") or "").strip()
                    if not val1 and latest.get("logMessage"):
                        m = re.search(r"(\d+)번\s*얼굴", str(latest.get("logMessage")))
                        if m:
                            val1 = m.group(1)
                    if val1:
                        server_map = status_coord.face_nicknames if status_coord else None
                        face_map = get_face_map(self.config_entry.options, server_map)
                        if val1 not in face_map:
                            _LOGGER.warning(
                                "Unmapped face key slot %s detected! Prompting user via persistent notification",
                                val1,
                            )
                            if hasattr(self.hass.components, "persistent_notification"):
                                self.hass.components.persistent_notification.async_create(
                                    f"도어락에서 새로운 얼굴키({val1}번 슬롯) 출입이 감지되었습니다.\n\n"
                                    f"[설정 > 기기 및 서비스 > Smart Solity > 구성]에서 사용자 이름을 입력하시면 다음 출입부터 이름으로 자동 표시됩니다.",
                                    title="솔리티 도어락 새 얼굴키 감지",
                                    notification_id=f"solity_new_face_{val1}",
                                )

            # Check if cloud has caught up with a new log
            newest_cloud_dt = (data[0].get("logDateTime") or "") if data else ""
            if newest_cloud_dt > getattr(self, "_ble_baseline_dt", ""):
                self._cancel_ble_pending()
                if self._recent_ble_log:
                    _LOGGER.info(
                        "Solity cloud log received (%s > baseline %s); replacing BLE fallback with real cloud log",
                        newest_cloud_dt,
                        getattr(self, "_ble_baseline_dt", ""),
                    )
                    self._recent_ble_log = None
            elif self._recent_ble_log:
                if time.monotonic() - self._last_ble_event_time > 60.0:
                    # Expire stale BLE fallback after 60 seconds
                    self._recent_ble_log = None
                else:
                    data = [self._recent_ble_log] + data

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
