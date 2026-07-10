"""Config flow for Merlin Firmware Updater."""

from __future__ import annotations

import asyncio
from typing import Any

from asusrouter import AsusRouter
from asusrouter.config.connection import ARConnectionConfigKey as ARCCKey
from asusrouter.error import (
    AsusRouterAccessError,
    AsusRouterConnectionError,
    AsusRouterError,
    AsusRouterTimeoutError,
)
import voluptuous as vol

from homeassistant import config_entries
from homeassistant.const import (
    CONF_HOST,
    CONF_PASSWORD,
    CONF_PORT,
    CONF_SCAN_INTERVAL,
    CONF_SSL,
    CONF_USERNAME,
)
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers import config_validation as cv

from .const import (
    CONF_VERIFY_SSL,
    DEFAULT_PORT,
    DEFAULT_SCAN_INTERVAL,
    DEFAULT_SSL,
    DEFAULT_VERIFY_SSL,
    DOMAIN,
    MIN_SCAN_INTERVAL,
)

CONNECT_TIMEOUT = 30
PORT_VALIDATOR = vol.All(vol.Coerce(int), vol.Range(min=0, max=65535))


async def _async_validate_input(
    hass: HomeAssistant, data: dict[str, Any]
) -> dict[str, str]:
    """Validate router credentials and return discovered title data."""

    api = AsusRouter(
        hostname=data[CONF_HOST],
        username=data[CONF_USERNAME],
        password=data[CONF_PASSWORD],
        port=data.get(CONF_PORT) or None,
        use_ssl=data.get(CONF_SSL, DEFAULT_SSL),
        connection_config={
            ARCCKey.VERIFY_SSL: data.get(CONF_VERIFY_SSL, DEFAULT_VERIFY_SSL),
        },
    )
    try:
        async with asyncio.timeout(CONNECT_TIMEOUT):
            connected = await api.async_connect()
        if not connected:
            raise AsusRouterConnectionError("Router login did not complete")

        identity = api.description
        title = identity.model or data[CONF_HOST]
        return {
            "title": f"{title} Merlin firmware",
            "unique_id": str(identity.mac) if identity.mac else data[CONF_HOST],
        }
    finally:
        await api.async_disconnect()


def _data_schema(user_input: dict[str, Any] | None = None) -> vol.Schema:
    """Return the setup form schema."""

    user_input = user_input or {}
    return vol.Schema(
        {
            vol.Required(
                CONF_HOST, default=user_input.get(CONF_HOST, "")
            ): cv.string,
            vol.Required(
                CONF_USERNAME,
                default=user_input.get(CONF_USERNAME, "admin"),
            ): cv.string,
            vol.Required(
                CONF_PASSWORD,
                default=user_input.get(CONF_PASSWORD, ""),
            ): cv.string,
            vol.Optional(
                CONF_PORT,
                default=user_input.get(CONF_PORT, DEFAULT_PORT),
            ): PORT_VALIDATOR,
            vol.Optional(
                CONF_SSL,
                default=user_input.get(CONF_SSL, DEFAULT_SSL),
            ): cv.boolean,
            vol.Optional(
                CONF_VERIFY_SSL,
                default=user_input.get(
                    CONF_VERIFY_SSL, DEFAULT_VERIFY_SSL
                ),
            ): cv.boolean,
            vol.Optional(
                CONF_SCAN_INTERVAL,
                default=user_input.get(
                    CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL
                ),
            ): vol.All(cv.positive_int, vol.Range(min=MIN_SCAN_INTERVAL)),
        }
    )


class MerlinFirmwareConfigFlow(
    config_entries.ConfigFlow, domain=DOMAIN
):
    """Handle a config flow for Merlin Firmware Updater."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle the initial step."""

        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                info = await _async_validate_input(self.hass, user_input)
            except AsusRouterAccessError:
                errors["base"] = "invalid_auth"
            except (AsusRouterConnectionError, AsusRouterTimeoutError, TimeoutError):
                errors["base"] = "cannot_connect"
            except AsusRouterError:
                errors["base"] = "unknown"
            except Exception:
                errors["base"] = "unknown"
            else:
                await self.async_set_unique_id(info["unique_id"])
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=info["title"],
                    data=user_input,
                )

        return self.async_show_form(
            step_id="user",
            data_schema=_data_schema(user_input),
            errors=errors,
        )

    @staticmethod
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> MerlinFirmwareOptionsFlow:
        """Return the options flow."""

        return MerlinFirmwareOptionsFlow()


class MerlinFirmwareOptionsFlow(config_entries.OptionsFlow):
    """Handle options for Merlin Firmware Updater."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Manage updater options."""

        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        current = {
            **self.config_entry.data,
            **self.config_entry.options,
        }
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Optional(
                        CONF_SCAN_INTERVAL,
                        default=current.get(
                            CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL
                        ),
                    ): vol.All(
                        cv.positive_int, vol.Range(min=MIN_SCAN_INTERVAL)
                    )
                }
            ),
        )
