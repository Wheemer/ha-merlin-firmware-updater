"""Compatibility exports for Asuswrt-Merlin firmware helpers."""

from __future__ import annotations

from asusrouter.error import AsusRouterMerlinFirmwareError
from asusrouter.modules.merlin import (
    DOWNLOAD_PAGE,
    FWUPDATE_MANIFEST_URL,
    MAX_DOWNLOAD_SIZE,
    SOURCEFORGE_RELEASE_URL,
    MerlinFirmwareInfo,
    ProgressCallback,
    async_download_merlin_firmware,
    async_get_merlin_firmware_info,
    async_prepare_merlin_firmware,
    async_validate_merlin_firmware,
    is_merlin_firmware,
)

MerlinFirmwareError = AsusRouterMerlinFirmwareError

__all__ = [
    "DOWNLOAD_PAGE",
    "FWUPDATE_MANIFEST_URL",
    "MAX_DOWNLOAD_SIZE",
    "SOURCEFORGE_RELEASE_URL",
    "AsusRouterMerlinFirmwareError",
    "MerlinFirmwareError",
    "MerlinFirmwareInfo",
    "ProgressCallback",
    "async_download_merlin_firmware",
    "async_get_merlin_firmware_info",
    "async_prepare_merlin_firmware",
    "async_validate_merlin_firmware",
    "is_merlin_firmware",
]
