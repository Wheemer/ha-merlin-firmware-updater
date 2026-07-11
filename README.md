# Merlin Firmware Updater

Home Assistant custom integration for Asuswrt-Merlin firmware updates.

This integration is intentionally separate from AsusRouter. Keep the normal
HACS AsusRouter integration installed for router sensors and controls. This
integration only creates a Merlin firmware update entity.

## What it does

- Uses an existing AsusRouter integration entry. You do not enter router
  credentials again.
- Reads the router firmware update signal through AsusRouter.
- When the router reports an available firmware update, maps that version to
  the matching Asuswrt-Merlin release.
- Downloads the Merlin release zip, extracts the firmware image, and verifies
  the published SHA256.
- Exposes a Home Assistant update entity only after the Merlin firmware image
  is prepared and verified.
- Starts the router's own firmware upgrade path when you press Install.

## Safety rules

- Firmware is not offered if the published SHA256 is missing.
- Firmware is not offered if the downloaded image hash does not match.
- Firmware is not offered until the image is already extracted and ready.
- Install uses the same router-controlled upgrade command as AsusRouter.
- Router/network errors are kept out of Home Assistant startup.

## Install

Add this repository to HACS as a custom integration:

```text
Wheemer/ha-merlin-firmware-updater
```

Then install **Merlin Firmware Updater** and restart Home Assistant.

## Configure

First set up the normal HACS AsusRouter integration.

Then go to Settings -> Devices & services -> Add integration and choose
**Merlin Firmware Updater**. Select the existing AsusRouter device to extend.
