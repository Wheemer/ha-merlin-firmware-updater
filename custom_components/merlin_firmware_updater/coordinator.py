"""Coordinator for Merlin Firmware Updater."""

from __future__ import annotations

import asyncio
from datetime import timedelta
import logging
from pathlib import Path
from typing import Any

from asusrouter import AsusRouter
from asusrouter.config.connection import ARConnectionConfigKey as ARCCKey
from asusrouter.error import AsusRouterError
from asusrouter.modules.firmware import ARFirmwareSourceUniversal
from asusrouter.modules.firmware import ARFirmwareState
from asusrouter.modules.merlin import is_merlin_firmware
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    CONF_HOST,
    CONF_PASSWORD,
    CONF_PORT,
    CONF_SCAN_INTERVAL,
    CONF_SSL,
    CONF_USERNAME,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .const import (
    CONF_VERIFY_SSL,
    DEFAULT_SCAN_INTERVAL,
    DEFAULT_SSL,
    DEFAULT_UPDATE_INTERVAL,
    DEFAULT_VERIFY_SSL,
    DOMAIN,
    MIN_SCAN_INTERVAL,
)
from .models import FirmwareUpdateData

_LOGGER = logging.getLogger(__name__)

CONNECT_TIMEOUT = 30
PREPARE_TIMEOUT = 20 * 60
INSTALL_TIMEOUT = 20 * 60


class MerlinFirmwareCoordinator(DataUpdateCoordinator[FirmwareUpdateData]):
    """Fetch router firmware state and prepare verified Merlin images."""

    config_entry: ConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        *,
        scan_interval_seconds: int = DEFAULT_SCAN_INTERVAL,
    ) -> None:
        """Initialize the coordinator."""

        self.entry = entry
        self._last_data = FirmwareUpdateData(status="idle")
        self._install_progress: int | bool = False

        scan_interval = timedelta(
            seconds=max(scan_interval_seconds, MIN_SCAN_INTERVAL)
        )
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=scan_interval or DEFAULT_UPDATE_INTERVAL,
            config_entry=entry,
        )
        self.data = self._last_data

    @property
    def install_progress(self) -> int | bool:
        """Return firmware install progress for the update entity."""

        return self._install_progress

    def api(self) -> AsusRouter:
        """Build an AsusRouter API instance for this config entry."""

        data = self.entry.data
        port = data.get(CONF_PORT)
        return AsusRouter(
            hostname=data[CONF_HOST],
            username=data[CONF_USERNAME],
            password=data[CONF_PASSWORD],
            port=port or None,
            use_ssl=data.get(CONF_SSL, DEFAULT_SSL),
            connection_config={
                ARCCKey.VERIFY_SSL: data.get(
                    CONF_VERIFY_SSL, DEFAULT_VERIFY_SSL
                ),
            },
        )

    def cache_dir(self) -> Path:
        """Return the firmware cache directory."""

        return Path(self.hass.config.path(DOMAIN, "firmware_cache"))

    async def _async_update_data(self) -> FirmwareUpdateData:
        """Refresh router firmware state and prepare Merlin firmware."""

        api = self.api()
        try:
            async with asyncio.timeout(CONNECT_TIMEOUT):
                connected = await api.async_connect()
            if not connected:
                raise AsusRouterError("Router login did not complete")

            identity = api.description
            model = identity.product_id or identity.model
            current_version = str(identity.firmware) if identity.firmware else None

            firmware_result = await api.async_get_data(
                ARFirmwareSourceUniversal, force=True
            )
            firmware_state = (
                firmware_result.get(ARFirmwareSourceUniversal)
                if isinstance(firmware_result, dict)
                else None
            )
            if not isinstance(firmware_state, ARFirmwareState):
                return self._remember(
                    FirmwareUpdateData(
                        model=model,
                        mac=str(identity.mac) if identity.mac else None,
                        current_version=current_version,
                        status="no_router_update",
                    )
                )

            latest = firmware_state.web.available
            latest_version = str(latest) if latest else None
            base = FirmwareUpdateData(
                model=model,
                mac=str(identity.mac) if identity.mac else None,
                current_version=current_version,
                latest_version=latest_version,
                release_note=firmware_state.web.release_note,
                update_available=bool(firmware_state.web.state),
                status="no_router_update",
            )

            if not base.update_available or latest_version is None:
                return self._remember(base)

            if not model:
                base.status = "missing_model"
                base.error = "Router model is unavailable"
                return self._remember(base)

            if not is_merlin_firmware(latest_version):
                base.status = "not_merlin"
                base.error = (
                    "Router reported an update, but the version does not "
                    "look like Asuswrt-Merlin"
                )
                return self._remember(base)

            async with asyncio.timeout(PREPARE_TIMEOUT):
                info = await api.async_prepare_merlin_firmware(
                    model=model,
                    version=latest_version,
                    cache_dir=self.cache_dir(),
                )

            prepared = bool(
                info.firmware_name
                and info.sha256
                and info.local_firmware_path
                and Path(info.local_firmware_path).is_file()
                and info.manifest_version == latest_version
            )
            base.firmware = info
            base.prepared = prepared
            base.status = "prepared" if prepared else "prepare_failed"
            if not prepared:
                base.error = "Merlin firmware metadata was incomplete"
            return self._remember(base)

        except Exception as ex:  # noqa: BLE001
            _LOGGER.warning("Merlin firmware refresh failed: %s", ex)
            previous = self._last_data
            return self._remember(
                FirmwareUpdateData(
                    model=previous.model,
                    mac=previous.mac,
                    current_version=previous.current_version,
                    latest_version=previous.latest_version,
                    release_note=previous.release_note,
                    update_available=False,
                    prepared=False,
                    status="error",
                    error=str(ex),
                    firmware=None,
                )
            )
        finally:
            await api.async_disconnect()

    def _remember(self, data: FirmwareUpdateData) -> FirmwareUpdateData:
        """Store the latest coordinator data."""

        self._last_data = data
        return data

    async def async_install_prepared(self) -> None:
        """Install the prepared Merlin firmware image."""

        data = self.data
        if not data or not data.prepared or not data.firmware:
            raise AsusRouterError("No verified Merlin firmware is prepared")

        firmware_path = data.firmware.local_firmware_path
        if not firmware_path:
            raise AsusRouterError("Prepared Merlin firmware path is missing")

        api = self.api()
        self._install_progress = 1
        self.async_update_listeners()

        def _set_progress(progress: int) -> None:
            self._install_progress = progress
            self.hass.loop.call_soon_threadsafe(self.async_update_listeners)

        try:
            async with asyncio.timeout(CONNECT_TIMEOUT):
                connected = await api.async_connect()
            if not connected:
                raise AsusRouterError("Router login did not complete")

            async with asyncio.timeout(INSTALL_TIMEOUT):
                await api.async_install_merlin_firmware(
                    model=data.firmware.model,
                    version=data.firmware.version,
                    firmware_path=Path(firmware_path),
                    progress=_set_progress,
                )
        finally:
            self._install_progress = False
            self.async_update_listeners()
            await api.async_disconnect()

