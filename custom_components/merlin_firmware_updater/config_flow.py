"""Config flow for Merlin Firmware Updater."""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.const import CONF_SCAN_INTERVAL
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers import config_validation as cv

from .const import (
    ASUSROUTER_DOMAIN,
    CONF_ASUSROUTER_ENTRY_ID,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    MIN_SCAN_INTERVAL,
)


def _asusrouter_entries(
    hass, current_entry_id: str | None = None
) -> dict[str, str]:
    """Return selectable AsusRouter config entries."""

    entries = {}
    for entry in hass.config_entries.async_entries(ASUSROUTER_DOMAIN):
        if current_entry_id and entry.entry_id != current_entry_id:
            continue
        title = entry.title or entry.data.get("host") or entry.entry_id
        entries[entry.entry_id] = title
    return entries


class MerlinFirmwareConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Merlin Firmware Updater."""

    VERSION = 2

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Select the AsusRouter entry to extend."""

        entries = _asusrouter_entries(self.hass)
        if not entries:
            return self.async_abort(reason="no_asusrouter")

        errors: dict[str, str] = {}
        if user_input is not None:
            entry_id = user_input[CONF_ASUSROUTER_ENTRY_ID]
            if entry_id not in entries:
                errors["base"] = "unknown"
            else:
                await self.async_set_unique_id(entry_id)
                self._abort_if_unique_id_configured()
                title = entries[entry_id]
                return self.async_create_entry(
                    title=f"{title} Merlin firmware",
                    data={
                        CONF_ASUSROUTER_ENTRY_ID: entry_id,
                    },
                    options={
                        CONF_SCAN_INTERVAL: user_input[CONF_SCAN_INTERVAL],
                    },
                )

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_ASUSROUTER_ENTRY_ID): vol.In(entries),
                    vol.Optional(
                        CONF_SCAN_INTERVAL,
                        default=DEFAULT_SCAN_INTERVAL,
                    ): vol.All(
                        cv.positive_int, vol.Range(min=MIN_SCAN_INTERVAL)
                    ),
                }
            ),
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
