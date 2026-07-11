"""Merlin Firmware Updater integration."""

from __future__ import annotations

import asyncio

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_SCAN_INTERVAL, EVENT_HOMEASSISTANT_STARTED
from homeassistant.core import Event, HomeAssistant, callback

from .const import DEFAULT_SCAN_INTERVAL, DOMAIN, PLATFORMS, STARTUP_REFRESH_DELAY
from .coordinator import MerlinFirmwareCoordinator

type MerlinFirmwareConfigEntry = ConfigEntry[MerlinFirmwareCoordinator]


async def async_setup_entry(
    hass: HomeAssistant, entry: MerlinFirmwareConfigEntry
) -> bool:
    """Set up Merlin Firmware Updater from a config entry."""

    coordinator = MerlinFirmwareCoordinator(
        hass,
        entry,
        scan_interval_seconds=entry.options.get(
            CONF_SCAN_INTERVAL,
            entry.data.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL),
        ),
    )
    entry.runtime_data = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    async def async_deferred_refresh(event: Event | None = None) -> None:
        """Refresh router data outside the startup-critical setup path."""

        await asyncio.sleep(STARTUP_REFRESH_DELAY)
        await coordinator.async_request_refresh()

    @callback
    def async_schedule_deferred_refresh(event: Event | None = None) -> None:
        """Schedule the initial firmware check after HA starts."""

        hass.async_create_task(
            async_deferred_refresh(event),
            f"{DOMAIN} deferred startup refresh",
            eager_start=False,
        )

    is_running = getattr(hass, "is_running", False)
    if callable(is_running):
        is_running = is_running()

    if is_running:
        async_schedule_deferred_refresh()
    else:
        hass.bus.async_listen_once(
            EVENT_HOMEASSISTANT_STARTED,
            async_schedule_deferred_refresh,
        )

    return True


async def async_unload_entry(
    hass: HomeAssistant, entry: MerlinFirmwareConfigEntry
) -> bool:
    """Unload a config entry."""

    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
