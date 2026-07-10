"""Update entity for Merlin Firmware Updater."""

from __future__ import annotations

from typing import Any

from asusrouter.error import AsusRouterError, AsusRouterMerlinFirmwareError
from homeassistant.components.update import UpdateEntity, UpdateEntityFeature
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import MerlinFirmwareConfigEntry
from .const import DOMAIN
from .coordinator import MerlinFirmwareCoordinator


async def async_setup_entry(
    hass: HomeAssistant,
    entry: MerlinFirmwareConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the Merlin firmware update entity."""

    async_add_entities([MerlinFirmwareUpdateEntity(entry.runtime_data)])


class MerlinFirmwareUpdateEntity(
    CoordinatorEntity[MerlinFirmwareCoordinator], UpdateEntity
):
    """A verified Merlin firmware update entity."""

    _attr_has_entity_name = True
    _attr_name = "Firmware"
    _attr_supported_features = (
        UpdateEntityFeature.INSTALL
        | UpdateEntityFeature.PROGRESS
        | UpdateEntityFeature.RELEASE_NOTES
    )

    def __init__(self, coordinator: MerlinFirmwareCoordinator) -> None:
        """Initialize the update entity."""

        super().__init__(coordinator)
        entry: ConfigEntry = coordinator.entry
        self._attr_unique_id = f"{entry.entry_id}_merlin_firmware"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, entry.entry_id)},
            "name": entry.title,
            "manufacturer": "Asuswrt-Merlin",
        }

    @property
    def device_info(self) -> dict[str, Any]:
        """Return device info."""

        info = dict(self._attr_device_info)
        if url := self._configuration_url():
            info["configuration_url"] = url
        return info

    def _configuration_url(self) -> str | None:
        """Return the router web UI URL."""

        try:
            return self.coordinator.router().bridge.configuration_url
        except Exception:  # noqa: BLE001
            return None

    @property
    def installed_version(self) -> str | None:
        """Return the installed firmware version."""

        return self.coordinator.data.current_version if self.coordinator.data else None

    @property
    def latest_version(self) -> str | None:
        """Return the latest prepared Merlin firmware version."""

        data = self.coordinator.data
        if not data or not data.prepared:
            return self.installed_version
        return data.latest_version

    @property
    def in_progress(self) -> int | bool:
        """Return update progress."""

        return self.coordinator.install_progress

    @property
    def release_summary(self) -> str | None:
        """Return release summary."""

        return self.coordinator.data.release_note if self.coordinator.data else None

    def release_notes(self) -> str | None:
        """Return release notes."""

        return self.release_summary

    @property
    def available(self) -> bool:
        """Return true when coordinator data is usable."""

        data = self.coordinator.data
        return bool(data and data.current_version)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return firmware preparation details."""

        data = self.coordinator.data
        if not data:
            return {"status": "idle"}

        info = data.firmware
        return {
            "status": data.status,
            "error": data.error,
            "router_reported_update": data.update_available,
            "prepared": data.prepared,
            "model": data.model,
            "manifest_version": info.manifest_version if info else None,
            "firmware_name": info.firmware_name if info else None,
            "firmware_sha256": info.sha256 if info else None,
            "download_url": info.download_url if info else None,
            "download_page": info.download_page if info else None,
            "local_firmware_path": info.local_firmware_path if info else None,
            "local_firmware_size": info.local_firmware_size if info else None,
            "cached": info.cached if info else None,
        }

    async def async_update(self) -> None:
        """Refresh firmware state on demand."""

        await self.coordinator.async_request_refresh()

    async def async_install(
        self, version: str | None, backup: bool, **kwargs: Any
    ) -> None:
        """Install the prepared Merlin firmware update."""

        data = self.coordinator.data
        target = version or (data.latest_version if data else None)
        if not data or not data.prepared or target != data.latest_version:
            raise HomeAssistantError(
                "No verified Merlin firmware update is prepared."
            )

        try:
            await self.coordinator.async_install_prepared()
        except (AsusRouterError, AsusRouterMerlinFirmwareError) as ex:
            raise HomeAssistantError(str(ex)) from ex
        except Exception as ex:
            raise HomeAssistantError(
                "Failed to install the prepared Merlin firmware update."
            ) from ex
        finally:
            await self.coordinator.async_request_refresh()
