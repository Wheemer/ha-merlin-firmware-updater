"""Coordinator for Merlin Firmware Updater."""

from __future__ import annotations

import asyncio
from datetime import timedelta
import logging
from pathlib import Path

import aiohttp
from asusrouter.error import AsusRouterError
from asusrouter.modules.data import AsusData
from asusrouter.modules.system import AsusSystem
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_SCAN_INTERVAL
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .const import (
    ASUSROUTER_DATA_KEY,
    ASUSROUTER_DOMAIN,
    CONF_ASUSROUTER_ENTRY_ID,
    DEFAULT_SCAN_INTERVAL,
    DEFAULT_UPDATE_INTERVAL,
    DOMAIN,
    MIN_SCAN_INTERVAL,
)
from .merlin import (
    async_prepare_merlin_firmware,
    async_validate_merlin_firmware,
    is_merlin_firmware,
)
from .models import FirmwareUpdateData

_LOGGER = logging.getLogger(__name__)

PREPARE_TIMEOUT = 20 * 60
INSTALL_TIMEOUT = 20 * 60
ROUTER_UPGRADE_WAIT = 120
ASUSROUTER_FIRMWARE_UPDATE_SUFFIXES = (
    "_firmware_update",
    "_firmware_update_beta",
)

try:
    _DISABLED_BY_INTEGRATION = er.RegistryEntryDisabler.INTEGRATION
except AttributeError:
    _DISABLED_BY_INTEGRATION = "integration"


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

    @property
    def asusrouter_entry_id(self) -> str:
        """Return the linked AsusRouter config entry id."""

        return self.entry.data[CONF_ASUSROUTER_ENTRY_ID]

    def router(self):
        """Return the loaded AsusRouter integration runtime object."""

        router_data = self.hass.data.get(ASUSROUTER_DOMAIN, {}).get(
            self.asusrouter_entry_id
        )
        if not router_data:
            raise ConfigEntryNotReady("Linked AsusRouter entry is not loaded")

        router = router_data.get(ASUSROUTER_DATA_KEY)
        if router is None:
            raise ConfigEntryNotReady("Linked AsusRouter runtime is missing")
        return router

    def api(self):
        """Return the linked AsusRouter API object."""

        router = self.router()
        bridge = getattr(router, "bridge", None)
        api = getattr(bridge, "api", None)
        if api is None:
            raise ConfigEntryNotReady("Linked AsusRouter API is unavailable")
        return api

    def supports_router_firmware_upgrade(self) -> bool:
        """Return whether the linked AsusRouter API can start firmware upgrade."""

        try:
            api = self.api()
        except ConfigEntryNotReady:
            return False

        return callable(getattr(api, "async_set_state", None))

    def cache_dir(self) -> Path:
        """Return the firmware cache directory."""

        return Path(self.hass.config.path(DOMAIN, "firmware_cache"))

    async def async_hide_stock_firmware_updates(self) -> None:
        """Disable linked AsusRouter firmware update entities."""

        registry = er.async_get(self.hass)
        entries = er.async_entries_for_config_entry(
            registry, self.asusrouter_entry_id
        )

        for entry in entries:
            if (
                entry.domain != "update"
                or entry.platform != ASUSROUTER_DOMAIN
                or not entry.unique_id
                or not entry.unique_id.endswith(
                    ASUSROUTER_FIRMWARE_UPDATE_SUFFIXES
                )
            ):
                continue

            if entry.disabled_by is not None:
                continue

            _LOGGER.info(
                "Disabling stock AsusRouter firmware update entity %s; "
                "Merlin Firmware Updater provides the guarded update path",
                entry.entity_id,
            )
            registry.async_update_entity(
                entry.entity_id,
                disabled_by=_DISABLED_BY_INTEGRATION,
            )

    async def _async_update_data(self) -> FirmwareUpdateData:
        """Refresh router firmware state and prepare Merlin firmware."""

        if not self._home_assistant_is_running():
            return self._remember(self._last_data)

        try:
            router = self.router()
            api = self.api()
            bridge = router.bridge
            if not getattr(bridge, "connected", False):
                raise ConfigEntryNotReady("Linked AsusRouter is not connected")

            identity = bridge.identity or api.description
            model = identity.product_id or identity.model
            current_version = str(identity.firmware) if identity.firmware else None

            firmware_state = await api.async_get_data(
                AsusData.FIRMWARE, force=True
            )
            if not isinstance(firmware_state, dict):
                return self._remember(
                    FirmwareUpdateData(
                        model=model,
                        mac=str(identity.mac) if identity.mac else None,
                        current_version=current_version,
                        status="no_router_update",
                    )
                )

            latest = firmware_state.get("available")
            latest_version = str(latest) if latest else None
            base = FirmwareUpdateData(
                model=model,
                mac=str(identity.mac) if identity.mac else None,
                current_version=current_version,
                latest_version=latest_version,
                release_note=firmware_state.get("release_note"),
                update_available=bool(
                    latest_version and latest_version != current_version
                ),
                status="no_router_update",
            )

            if not base.update_available or latest_version is None:
                return self._remember(base)

            await self.async_hide_stock_firmware_updates()

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
                async with aiohttp.ClientSession() as session:
                    info = await async_prepare_merlin_firmware(
                        session=session,
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
            if prepared and not self.supports_router_firmware_upgrade():
                base.status = "router_upgrade_unsupported"
                base.error = (
                    "The linked AsusRouter library can detect and prepare "
                    "firmware, but it cannot start the router firmware upgrade"
                )
            else:
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

    def _remember(self, data: FirmwareUpdateData) -> FirmwareUpdateData:
        """Store the latest coordinator data."""

        self._last_data = data
        return data

    def _home_assistant_is_running(self) -> bool:
        """Return whether Home Assistant has finished startup."""

        is_running = getattr(self.hass, "is_running", False)
        if callable(is_running):
            return bool(is_running())
        return bool(is_running)

    async def async_install_prepared(self) -> None:
        """Start the router-controlled install of the prepared Merlin update."""

        data = self.data
        if not data or not data.prepared or not data.firmware:
            raise AsusRouterError("No verified Merlin firmware is prepared")

        firmware_path = data.firmware.local_firmware_path
        if not firmware_path:
            raise AsusRouterError("Prepared Merlin firmware path is missing")

        api = self.api()
        set_state = getattr(api, "async_set_state", None)
        if not callable(set_state):
            raise AsusRouterError(
                "The installed AsusRouter library cannot start firmware upgrade"
            )

        self._install_progress = True
        self.async_update_listeners()

        try:
            async with asyncio.timeout(INSTALL_TIMEOUT):
                async with aiohttp.ClientSession() as session:
                    await async_validate_merlin_firmware(
                        session=session,
                        model=data.firmware.model,
                        version=data.firmware.version,
                        firmware_path=Path(firmware_path),
                    )

                result = await set_state(state=AsusSystem.FIRMWARE_UPGRADE)
                if not result:
                    raise AsusRouterError(
                        "The router rejected the firmware upgrade command"
                    )

                await asyncio.sleep(ROUTER_UPGRADE_WAIT)
        finally:
            self._install_progress = False
            self.async_update_listeners()
