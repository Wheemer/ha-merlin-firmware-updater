"""Constants for Merlin Firmware Updater."""

from __future__ import annotations

from datetime import timedelta
from typing import Final

DOMAIN: Final = "merlin_firmware_updater"
PLATFORMS: Final = ["update"]

CONF_VERIFY_SSL: Final = "verify_ssl"

DEFAULT_NAME: Final = "Merlin Firmware Updater"
DEFAULT_PORT: Final = 0
DEFAULT_SSL: Final = False
DEFAULT_VERIFY_SSL: Final = False
DEFAULT_SCAN_INTERVAL: Final = 6 * 60 * 60
MIN_SCAN_INTERVAL: Final = 30 * 60

DEFAULT_UPDATE_INTERVAL: Final = timedelta(seconds=DEFAULT_SCAN_INTERVAL)

