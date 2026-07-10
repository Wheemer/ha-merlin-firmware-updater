# Merlin Firmware Updater

Home Assistant custom integration for Asuswrt-Merlin firmware updates.

This integration is intentionally separate from AsusRouter. Keep the normal
HACS AsusRouter integration installed for router sensors and controls. This
integration only creates a Merlin firmware update entity.

## What it does

- Logs in to the router after Home Assistant startup.
- Reads the router firmware update signal from the router.
- When the router reports an available firmware update, maps that version to
  the matching Asuswrt-Merlin release.
- Downloads the Merlin release zip, extracts the firmware image, and verifies
  the published SHA256.
- Exposes a Home Assistant update entity only after the Merlin firmware image
  is prepared and verified.
- Uploads the prepared firmware image when you press Install.

## Safety rules

- Firmware is not offered if the published SHA256 is missing.
- Firmware is not offered if the downloaded image hash does not match.
- Firmware is not offered until the image is already extracted and ready.
- Router/network errors are kept out of Home Assistant startup.

## Install

Add this repository to HACS as a custom integration:

```text
Wheemer/ha-merlin-firmware-updater
```

Then install **Merlin Firmware Updater** and restart Home Assistant.

## Configure

Go to Settings -> Devices & services -> Add integration and choose
**Merlin Firmware Updater**.

Use the same host, username, password, SSL, and port you use for the router
web UI.

