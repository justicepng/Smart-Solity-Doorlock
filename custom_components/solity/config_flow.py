"""Config flow for Smart Solity."""
from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import SolityAuthError, SolityClient, SolityError, hash_password
from .const import (
    CONF_AUTO_CLOSE_SECONDS,
    CONF_BLE_APP_KEY,
    CONF_BLE_MAC,
    CONF_CONTROL_MODE,
    CONF_DEVICE_ID,
    CONF_EMAIL,
    CONF_HASHED_PWD,
    CONF_LOG_SECONDS,
    CONF_MEMBER_ID,
    CONF_NICKNAME,
    CONF_PASSWORD,
    CONF_STATUS_MINUTES,
    CONTROL_MODE_BLUETOOTH,
    CONTROL_MODE_CLOUD,
    CONTROL_MODE_HYBRID,
    DEFAULT_AUTO_CLOSE_SECONDS,
    DEFAULT_CONTROL_MODE,
    DEFAULT_LOG_SECONDS,
    DEFAULT_STATUS_MINUTES,
    DOMAIN,
    MAX_AUTO_CLOSE_SECONDS,
    MAX_LOG_SECONDS,
    MAX_STATUS_MINUTES,
    MIN_AUTO_CLOSE_SECONDS,
    MIN_LOG_SECONDS,
    MIN_STATUS_MINUTES,
)

USER_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_EMAIL): str,
        vol.Required(CONF_PASSWORD): str,
    }
)


class SolityConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle the Smart Solity config flow."""

    VERSION = 1

    def __init__(self) -> None:
        self._email: str | None = None
        self._hashed: str | None = None
        self._devices: list[dict] = []

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            self._email = user_input[CONF_EMAIL]
            self._hashed = hash_password(user_input[CONF_PASSWORD])
            session = async_get_clientsession(self.hass)
            client = SolityClient(session, self._email, self._hashed)
            try:
                await client.login()
                self._devices = await client.get_devices()
            except SolityAuthError:
                errors["base"] = "invalid_auth"
            except SolityError:
                errors["base"] = "cannot_connect"
            else:
                if not self._devices:
                    errors["base"] = "no_devices"
                elif len(self._devices) == 1:
                    return await self._create(self._devices[0])
                else:
                    return await self.async_step_select_device()

        return self.async_show_form(
            step_id="user", data_schema=USER_SCHEMA, errors=errors
        )

    async def async_step_select_device(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            chosen = next(
                d for d in self._devices if d.get("myDeviceId") == user_input[CONF_DEVICE_ID]
            )
            return await self._create(chosen)

        options = {
            d.get("myDeviceId"): (d.get("myDeviceNickName") or d.get("myDeviceId"))
            for d in self._devices
        }
        return self.async_show_form(
            step_id="select_device",
            data_schema=vol.Schema({vol.Required(CONF_DEVICE_ID): vol.In(options)}),
        )

    async def _create(self, device: dict) -> ConfigFlowResult:
        device_id = device.get("myDeviceId")
        await self.async_set_unique_id(device_id)
        self._abort_if_unique_id_configured()
        nickname = device.get("myDeviceNickName") or "Solity Doorlock"
        return self.async_create_entry(
            title=nickname,
            data={
                CONF_EMAIL: self._email,
                CONF_HASHED_PWD: self._hashed,
                CONF_DEVICE_ID: device_id,
                CONF_NICKNAME: nickname,
                CONF_BLE_MAC: device.get("myDeviceBleMacAddr") or "",
                CONF_BLE_APP_KEY: device.get("regDeviceAppKey") or "",
                CONF_MEMBER_ID: device.get("myDeviceMemberId") or "",
            },
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return SolityOptionsFlow(config_entry)


class SolityOptionsFlow(OptionsFlow):
    """Options: control mode, poll intervals, and face recognition names."""

    def __init__(self, config_entry: ConfigEntry | None = None) -> None:
        """Initialize options flow."""
        if config_entry is not None:
            self._config_entry = config_entry

    @property
    def config_entry(self) -> ConfigEntry:
        """Return the config entry."""
        if hasattr(self, "_config_entry"):
            return self._config_entry
        return super().config_entry

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        opts = self.config_entry.options
        entry_data = self.config_entry.data

        control_mode_default = opts.get(CONF_CONTROL_MODE, DEFAULT_CONTROL_MODE)
        ble_mac_default = opts.get(CONF_BLE_MAC) or entry_data.get(CONF_BLE_MAC) or ""

        log_default = opts.get(CONF_LOG_SECONDS, DEFAULT_LOG_SECONDS)
        status_default = opts.get(CONF_STATUS_MINUTES, DEFAULT_STATUS_MINUTES)
        auto_close_default = opts.get(
            CONF_AUTO_CLOSE_SECONDS, DEFAULT_AUTO_CLOSE_SECONDS
        )

        schema_dict = {
            vol.Required(CONF_CONTROL_MODE, default=control_mode_default): vol.In(
                {
                    CONTROL_MODE_HYBRID: "하이브리드 (Bluetooth 우선, Cloud 폴백)",
                    CONTROL_MODE_BLUETOOTH: "블루투스 (Bluetooth BLE - Bluetooth Proxy)",
                    CONTROL_MODE_CLOUD: "클라우드 (Cloud API)",
                }
            ),
            vol.Optional(CONF_BLE_MAC, default=ble_mac_default): str,
            vol.Required(CONF_LOG_SECONDS, default=log_default): vol.All(
                vol.Coerce(int),
                vol.Range(min=MIN_LOG_SECONDS, max=MAX_LOG_SECONDS),
            ),
            vol.Required(CONF_STATUS_MINUTES, default=status_default): vol.All(
                vol.Coerce(int),
                vol.Range(min=MIN_STATUS_MINUTES, max=MAX_STATUS_MINUTES),
            ),
            vol.Required(
                CONF_AUTO_CLOSE_SECONDS, default=auto_close_default
            ): vol.All(
                vol.Coerce(int),
                vol.Range(
                    min=MIN_AUTO_CLOSE_SECONDS, max=MAX_AUTO_CLOSE_SECONDS
                ),
            ),
        }

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(schema_dict),
        )

