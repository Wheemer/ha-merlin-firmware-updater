"""Data models for Merlin Firmware Updater."""

from __future__ import annotations

from dataclasses import dataclass

from asusrouter.modules.merlin import MerlinFirmwareInfo


@dataclass(slots=True)
class FirmwareUpdateData:
    """Prepared firmware update state."""

    model: str | None = None
    mac: str | None = None
    current_version: str | None = None
    latest_version: str | None = None
    release_note: str | None = None
    update_available: bool = False
    prepared: bool = False
    status: str = "idle"
    error: str | None = None
    firmware: MerlinFirmwareInfo | None = None

