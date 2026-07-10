"""Constants for Merlin Firmware Updater."""

from __future__ import annotations

from datetime import timedelta
from typing import Final

DOMAIN: Final = "merlin_firmware_updater"
PLATFORMS: Final = ["update"]

CONF_ASUSROUTER_ENTRY_ID: Final = "asusrouter_entry_id"
ASUSROUTER_DOMAIN: Final = "asusrouter"
ASUSROUTER_DATA_KEY: Final = "asusrouter"

DEFAULT_NAME: Final = "Merlin Firmware Updater"
DEFAULT_SCAN_INTERVAL: Final = 6 * 60 * 60
MIN_SCAN_INTERVAL: Final = 30 * 60

DEFAULT_UPDATE_INTERVAL: Final = timedelta(seconds=DEFAULT_SCAN_INTERVAL)
