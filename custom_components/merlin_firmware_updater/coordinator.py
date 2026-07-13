"""Coordinator for Merlin Firmware Updater."""

from __future__ import annotations

import asyncio
from datetime import timedelta
import hashlib
import logging
from pathlib import Path

import aiohttp
from asusrouter.error import AsusRouterError
from asusrouter.modules.data import AsusData
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

CHUNK_SIZE = 1024 * 1024
PREPARE_TIMEOUT = 20 * 60
INSTALL_TIMEOUT = 20 * 60
ROUTER_UPGRADE_WAIT = 120
ASUSROUTER_FIRMWARE_UPDATE_SUFFIXES = (
    "_firmware_update",
    "_firmware_update_beta",
)
MERLIN_FIRMWARE_UPDATE_SUFFIX = "_merlin_firmware"

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
        """Return whether the linked AsusRouter API can install Merlin firmware."""

        return self.supports_merlin_firmware_install()

    def supports_merlin_firmware_install(self) -> bool:
        """Return whether the linked AsusRouter API can upload firmware."""

        try:
            api = self.api()
        except ConfigEntryNotReady:
            return False

        return callable(
            getattr(api, "async_install_merlin_firmware", None)
        ) or callable(getattr(api, "async_upload_firmware", None))

    def cache_dir(self) -> Path:
        """Return the firmware cache directory."""

        return Path(self.hass.config.path(DOMAIN, "firmware_cache"))

    def _entity_registry(self) -> er.EntityRegistry:
        """Return the entity registry."""

        return er.async_get(self.hass)

    def _own_update_entity_enabled(self) -> bool:
        """Return whether this integration's firmware entity can be shown."""

        unique_id = f"{self.entry.entry_id}{MERLIN_FIRMWARE_UPDATE_SUFFIX}"
        registry = self._entity_registry()
        return any(
            entry.platform == DOMAIN
            and entry.domain == "update"
            and entry.unique_id == unique_id
            and entry.disabled_by is None
            for entry in er.async_entries_for_config_entry(
                registry, self.entry.entry_id
            )
        )

    def _stock_firmware_update_entries(
        self, registry: er.EntityRegistry
    ) -> list[er.RegistryEntry]:
        """Return linked stock AsusRouter firmware update entity entries."""

        return [
            entry
            for entry in er.async_entries_for_config_entry(
                registry, self.asusrouter_entry_id
            )
            if entry.domain == "update"
            and entry.platform == ASUSROUTER_DOMAIN
            and entry.unique_id
            and entry.unique_id.endswith(ASUSROUTER_FIRMWARE_UPDATE_SUFFIXES)
        ]

    async def async_hide_stock_firmware_updates(self) -> bool:
        """Disable linked AsusRouter firmware updates when replacement is visible."""

        registry = self._entity_registry()
        if not self._own_update_entity_enabled():
            await self.async_show_stock_firmware_updates()
            _LOGGER.warning(
                "Not hiding stock AsusRouter firmware update because the "
                "Merlin firmware update entity is disabled or missing"
            )
            return False

        for entry in self._stock_firmware_update_entries(registry):
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
        return True

    async def async_show_stock_firmware_updates(self) -> None:
        """Re-enable stock AsusRouter firmware entities disabled by us."""

        registry = er.async_get(self.hass)
        for entry in self._stock_firmware_update_entries(registry):
            if entry.disabled_by != _DISABLED_BY_INTEGRATION:
                continue
            _LOGGER.info(
                "Re-enabling stock AsusRouter firmware update entity %s "
                "because Merlin Firmware Updater cannot take over",
                entry.entity_id,
            )
            registry.async_update_entity(
                entry.entity_id,
                disabled_by=None,
            )

    def _set_install_progress(self, progress: int | bool) -> None:
        """Update install progress and notify the update entity."""

        if type(progress) is int:
            progress = max(1, min(progress, 99))
        self._install_progress = progress
        self.async_update_listeners()

    def _install_progress_callback(self):
        """Return a callback suitable for the AsusRouter upload helper."""

        def _progress(progress: int) -> None:
            try:
                running_loop = asyncio.get_running_loop()
            except RuntimeError:
                running_loop = None

            if running_loop is self.hass.loop:
                self._set_install_progress(progress)
            else:
                self.hass.loop.call_soon_threadsafe(
                    self._set_install_progress, progress
                )

        return _progress

    async def _cached_firmware_still_valid(
        self, data: FirmwareUpdateData | None
    ) -> bool:
        """Return true when previous prepared firmware still matches SHA256."""

        if not data or not data.prepared or not data.firmware:
            return False

        info = data.firmware
        if (
            not info.local_firmware_path
            or not info.sha256
            or not info.manifest_version
            or info.manifest_version != data.latest_version
        ):
            return False

        firmware_path = Path(info.local_firmware_path)
        if not firmware_path.is_file():
            return False

        actual = await self.hass.async_add_executor_job(
            _sha256, firmware_path
        )
        return actual == info.sha256

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
                await self.async_show_stock_firmware_updates()
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
                await self.async_show_stock_firmware_updates()
                return self._remember(base)

            if not model:
                await self.async_show_stock_firmware_updates()
                base.status = "missing_model"
                base.error = "Router model is unavailable"
                return self._remember(base)

            if not is_merlin_firmware(latest_version):
                await self.async_show_stock_firmware_updates()
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
            if not prepared:
                await self.async_show_stock_firmware_updates()
                base.status = "prepare_failed"
                base.error = "Merlin firmware metadata was incomplete"
                return self._remember(base)

            if not self.supports_merlin_firmware_install():
                await self.async_show_stock_firmware_updates()
                base.status = "install_unsupported"
                base.error = (
                    "The linked AsusRouter library can detect and prepare "
                    "firmware, but it cannot upload the verified firmware image"
                )
                return self._remember(base)

            if not await self.async_hide_stock_firmware_updates():
                base.status = "merlin_entity_disabled"
                base.error = (
                    "Merlin firmware update entity is disabled; stock "
                    "AsusRouter firmware update was left visible"
                )
                return self._remember(base)

            base.status = "prepared"
            return self._remember(base)

        except Exception as ex:  # noqa: BLE001
            _LOGGER.warning("Merlin firmware refresh failed: %s", ex)
            previous = self._last_data
            if await self._cached_firmware_still_valid(previous):
                if (
                    self.supports_merlin_firmware_install()
                    and self._own_update_entity_enabled()
                ):
                    await self.async_hide_stock_firmware_updates()
                    return self._remember(
                        FirmwareUpdateData(
                            model=previous.model,
                            mac=previous.mac,
                            current_version=previous.current_version,
                            latest_version=previous.latest_version,
                            release_note=previous.release_note,
                            update_available=previous.update_available,
                            prepared=True,
                            status="error",
                            error=str(ex),
                            firmware=previous.firmware,
                        )
                    )

            await self.async_show_stock_firmware_updates()
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
        """Upload and install the prepared Merlin update."""

        data = self.data
        if not data or not data.prepared or not data.firmware:
            raise AsusRouterError("No verified Merlin firmware is prepared")

        firmware_path = data.firmware.local_firmware_path
        if not firmware_path:
            raise AsusRouterError("Prepared Merlin firmware path is missing")

        api = self.api()
        install_merlin = getattr(api, "async_install_merlin_firmware", None)
        upload_firmware = getattr(api, "async_upload_firmware", None)
        if not callable(install_merlin) and not callable(upload_firmware):
            raise AsusRouterError(
                "The installed AsusRouter library cannot upload firmware"
            )

        path = Path(firmware_path)
        self._set_install_progress(5)

        try:
            async with asyncio.timeout(INSTALL_TIMEOUT):
                async with aiohttp.ClientSession() as session:
                    await async_validate_merlin_firmware(
                        session=session,
                        model=data.firmware.model,
                        version=data.firmware.version,
                        firmware_path=path,
                    )

                self._set_install_progress(55)
                if callable(install_merlin):
                    await install_merlin(
                        model=data.firmware.model,
                        version=data.firmware.version,
                        firmware_path=path,
                        progress=self._install_progress_callback(),
                    )
                else:
                    await upload_firmware(path)
                    self._set_install_progress(90)

                self._set_install_progress(95)
                await asyncio.sleep(ROUTER_UPGRADE_WAIT)
        finally:
            self._set_install_progress(False)


def _sha256(path: Path) -> str:
    """Return the SHA256 digest for a local file."""

    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(CHUNK_SIZE), b""):
            digest.update(chunk)
    return digest.hexdigest()
