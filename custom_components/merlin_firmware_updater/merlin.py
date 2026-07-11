"""Asuswrt-Merlin firmware helpers."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
import hashlib
import logging
from pathlib import Path
import re
from urllib.parse import quote
from zipfile import BadZipFile, ZipFile

import aiohttp

_LOGGER = logging.getLogger(__name__)

CHUNK_SIZE = 1024 * 1024
DOWNLOAD_PAGE = "https://www.asuswrt-merlin.net/download"
FWUPDATE_MANIFEST_URL = "https://fwupdate.asuswrt-merlin.net/manifest2.txt"
MANIFEST_MIN_PARTS = 3
MAX_DOWNLOAD_SIZE = 512 * 1024 * 1024
SOURCEFORGE_RELEASE_URL = (
    "https://sourceforge.net/projects/asuswrt-merlin/files/"
    "{model}/Release/{zip_name}/download"
)
MERLIN_VERSION = re.compile(
    r"^3\.0\.0\.(?P<branch>[46])\.(?P<tail>\d+\.\d+_\d+(?:_rog)?)$"
)
FIRMWARE_EXTENSIONS = {".pkgtb", ".trx", ".w"}

ProgressCallback = Callable[[int], None]


class MerlinFirmwareError(Exception):
    """Asuswrt-Merlin firmware update error."""


@dataclass(frozen=True)
class MerlinFirmwareInfo:
    """Published Asuswrt-Merlin firmware metadata."""

    model: str
    version: str
    manifest_version: str
    zip_name: str
    firmware_prefix: str
    firmware_name: str | None
    sha256: str | None
    manifest_url: str
    download_url: str
    download_page: str
    local_firmware_path: str | None = None
    local_firmware_size: int | None = None
    sha256_verified: bool = False
    cached: bool = False


def is_merlin_firmware(version: str | None) -> bool:
    """Return true when the firmware version looks like Asuswrt-Merlin."""

    if not version:
        return False

    return bool(MERLIN_VERSION.match(version))


def _filename_token(version: str) -> str:
    match = MERLIN_VERSION.match(version)
    if not match:
        raise MerlinFirmwareError(
            f"`{version}` is not a supported Merlin version"
        )

    return f"300{match.group('branch')}_{match.group('tail')}"


def _sourceforge_url(model: str, version: str) -> tuple[str, str]:
    token = _filename_token(version)
    zip_name = f"{model}_{token}.zip"

    return zip_name, SOURCEFORGE_RELEASE_URL.format(
        model=quote(model, safe=""),
        zip_name=quote(zip_name, safe=""),
    )


def _firmware_prefix(model: str, version: str) -> str:
    return f"{model}_{_filename_token(version)}"


def _cache_token(value: str) -> str:
    token = re.sub(r"[^A-Za-z0-9_.-]+", "_", value).strip("._")
    if not token:
        raise MerlinFirmwareError(
            "Cannot build Merlin firmware cache path"
        )
    return token


def _firmware_extensions_pattern() -> str:
    return "|".join(
        re.escape(ext)
        for ext in sorted(FIRMWARE_EXTENSIONS, key=len, reverse=True)
    )


def _manifest_version(firmware: str, extension: str) -> str:
    match = re.fullmatch(r"(?P<base>300[46])\.(?P<tail>\d+\.\d+)", firmware)
    if not match or not extension.isdigit():
        raise MerlinFirmwareError(
            "Merlin update manifest contains an unsupported version format"
        )

    base = match.group("base")
    return f"3.0.0.{base[-1]}.{match.group('tail')}_{extension}"


async def _manifest_version_for_model(
    session: aiohttp.ClientSession,
    model: str,
) -> str:
    headers = {"User-Agent": "AsusRouter Merlin firmware updater"}

    try:
        async with session.get(
            FWUPDATE_MANIFEST_URL,
            headers=headers,
            timeout=aiohttp.ClientTimeout(total=60),
        ) as response:
            if response.status != 200:
                raise MerlinFirmwareError(
                    "Merlin update manifest failed with "
                    f"HTTP {response.status}"
                )
            manifest = await response.text()
    except aiohttp.ClientError as ex:
        raise MerlinFirmwareError(
            "Could not fetch Merlin update manifest"
        ) from ex

    for line in manifest.splitlines():
        parts = line.strip().split("#")
        if len(parts) < MANIFEST_MIN_PARTS or parts[0] != model:
            continue

        values = {}
        for part in parts[1:]:
            if part.startswith("FW"):
                values["FW"] = part[2:]
            elif part.startswith("EXT"):
                values["EXT"] = part[3:]

        manifest_firmware = values.get("FW")
        manifest_extension = values.get("EXT")
        if not manifest_firmware or manifest_extension is None:
            raise MerlinFirmwareError(
                f"Merlin update manifest entry for `{model}` is incomplete"
            )

        return _manifest_version(
            manifest_firmware,
            manifest_extension,
        )

    raise MerlinFirmwareError(
        f"Merlin update manifest does not contain `{model}`"
    )


async def _confirm_manifest_version(
    session: aiohttp.ClientSession,
    model: str,
    version: str,
) -> str:
    manifest_version = await _manifest_version_for_model(session, model)
    if manifest_version != version:
        raise MerlinFirmwareError(
            "Merlin update manifest reports "
            f"`{manifest_version}` for `{model}`, not `{version}`"
        )

    return manifest_version


async def _download_file(
    session: aiohttp.ClientSession,
    url: str,
    destination: Path,
) -> None:
    headers = {"User-Agent": "AsusRouter Merlin firmware updater"}

    try:
        async with session.get(
            url,
            headers=headers,
            allow_redirects=True,
            timeout=aiohttp.ClientTimeout(total=900),
        ) as response:
            if response.status != 200:
                raise MerlinFirmwareError(
                    f"Merlin download failed with HTTP {response.status}"
                )

            content_length = response.content_length
            if (
                content_length is not None
                and content_length > MAX_DOWNLOAD_SIZE
            ):
                raise MerlinFirmwareError(
                    "Merlin download is larger than the allowed firmware "
                    "size limit"
                )

            downloaded = 0
            with destination.open("wb") as file:
                async for chunk in response.content.iter_chunked(CHUNK_SIZE):
                    downloaded += len(chunk)
                    if downloaded > MAX_DOWNLOAD_SIZE:
                        raise MerlinFirmwareError(
                            "Merlin download exceeded the allowed firmware "
                            "size limit"
                        )
                    file.write(chunk)
    except Exception:
        destination.unlink(missing_ok=True)
        raise


def _extract_firmware(
    zip_path: Path,
    extract_dir: Path,
    model: str,
    version: str,
) -> Path:
    expected_prefix = _firmware_prefix(model, version)

    try:
        with ZipFile(zip_path) as archive:
            members = [
                member
                for member in archive.infolist()
                if not member.is_dir()
                and Path(member.filename).suffix.lower()
                in FIRMWARE_EXTENSIONS
            ]

            matches = [
                member
                for member in members
                if Path(member.filename).name.startswith(expected_prefix)
            ]

            if len(matches) != 1:
                raise MerlinFirmwareError(
                    "Merlin zip did not contain exactly one firmware image "
                    f"matching `{expected_prefix}`"
                )

            member = matches[0]
            firmware_name = Path(member.filename).name
            firmware_path = extract_dir / firmware_name

            with (
                archive.open(member) as source,
                firmware_path.open("wb") as destination,
            ):
                while chunk := source.read(CHUNK_SIZE):
                    destination.write(chunk)

    except BadZipFile as ex:
        raise MerlinFirmwareError(
            "Downloaded Merlin file is not a valid zip"
        ) from ex

    return firmware_path


async def _published_sha256(
    session: aiohttp.ClientSession,
    firmware_name: str,
) -> str | None:
    try:
        async with session.get(
            DOWNLOAD_PAGE,
            headers={"User-Agent": "AsusRouter Merlin firmware updater"},
            timeout=aiohttp.ClientTimeout(total=60),
        ) as response:
            if response.status != 200:
                return None
            text = await response.text()
    except (TimeoutError, aiohttp.ClientError):
        return None

    pattern = re.compile(
        rf"\b([a-fA-F0-9]{{64}})\s+{re.escape(firmware_name)}\b"
    )
    match = pattern.search(text)

    return match.group(1).lower() if match else None


async def _published_sha256_for_prefix(
    session: aiohttp.ClientSession,
    firmware_prefix: str,
) -> tuple[str | None, str | None]:
    try:
        async with session.get(
            DOWNLOAD_PAGE,
            headers={"User-Agent": "AsusRouter Merlin firmware updater"},
            timeout=aiohttp.ClientTimeout(total=60),
        ) as response:
            if response.status != 200:
                return None, None
            text = await response.text()
    except (TimeoutError, aiohttp.ClientError):
        return None, None

    pattern = re.compile(
        rf"\b([a-fA-F0-9]{{64}})\s+"
        rf"({re.escape(firmware_prefix)}[^\s<>]*"
        rf"(?:{_firmware_extensions_pattern()}))\b"
    )
    match = pattern.search(text)
    if not match:
        return None, None

    return match.group(2), match.group(1).lower()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        while chunk := file.read(CHUNK_SIZE):
            digest.update(chunk)

    return digest.hexdigest()


def _with_local_firmware(
    info: MerlinFirmwareInfo,
    firmware_path: Path,
    *,
    cached: bool,
) -> MerlinFirmwareInfo:
    return MerlinFirmwareInfo(
        model=info.model,
        version=info.version,
        manifest_version=info.manifest_version,
        zip_name=info.zip_name,
        firmware_prefix=info.firmware_prefix,
        firmware_name=info.firmware_name,
        sha256=info.sha256,
        manifest_url=info.manifest_url,
        download_url=info.download_url,
        download_page=info.download_page,
        local_firmware_path=str(firmware_path),
        local_firmware_size=firmware_path.stat().st_size,
        sha256_verified=True,
        cached=cached,
    )


async def async_get_merlin_firmware_info(
    session: aiohttp.ClientSession,
    model: str,
    version: str,
) -> MerlinFirmwareInfo:
    """Get published Merlin metadata without downloading the zip."""

    zip_name, download_url = _sourceforge_url(model, version)
    firmware_prefix = _firmware_prefix(model, version)
    manifest_version = await _confirm_manifest_version(
        session, model, version
    )
    firmware_name, sha256 = await _published_sha256_for_prefix(
        session, firmware_prefix
    )

    return MerlinFirmwareInfo(
        model=model,
        version=version,
        manifest_version=manifest_version,
        zip_name=zip_name,
        firmware_prefix=firmware_prefix,
        firmware_name=firmware_name,
        sha256=sha256,
        manifest_url=FWUPDATE_MANIFEST_URL,
        download_url=download_url,
        download_page=DOWNLOAD_PAGE,
    )


async def async_prepare_merlin_firmware(
    session: aiohttp.ClientSession,
    model: str,
    version: str,
    cache_dir: Path,
    progress: ProgressCallback | None = None,
) -> MerlinFirmwareInfo:
    """Download, extract, validate, and cache a Merlin firmware image."""

    if progress is None:
        def _ignore_progress(_: int) -> None:
            return None

        progress = _ignore_progress

    progress(5)
    info = await async_get_merlin_firmware_info(session, model, version)
    progress(10)

    if not info.firmware_name or not info.sha256:
        raise MerlinFirmwareError(
            "No published SHA256 found for the Merlin firmware image; "
            "refusing to prepare firmware"
        )

    target_dir = (
        cache_dir / _cache_token(model) / _cache_token(version)
    )
    target_dir.mkdir(parents=True, exist_ok=True)
    firmware_path = target_dir / info.firmware_name

    if firmware_path.exists():
        actual_sha = await asyncio.to_thread(_sha256, firmware_path)
        if actual_sha == info.sha256:
            progress(55)
            return _with_local_firmware(
                info, firmware_path, cached=True
            )
        firmware_path.unlink()

    zip_path = target_dir / info.zip_name
    download_path = target_dir / f"{info.zip_name}.download"
    if download_path.exists():
        download_path.unlink()

    _LOGGER.info("Downloading Merlin firmware `%s`", info.zip_name)
    await _download_file(session, info.download_url, download_path)
    download_path.replace(zip_path)
    progress(35)

    extracted_path = _extract_firmware(zip_path, target_dir, model, version)
    progress(45)

    if extracted_path.name != info.firmware_name:
        extracted_path.unlink(missing_ok=True)
        raise MerlinFirmwareError(
            "Downloaded Merlin zip contained "
            f"`{extracted_path.name}`, expected `{info.firmware_name}`"
        )

    actual_sha = await asyncio.to_thread(_sha256, extracted_path)
    if actual_sha != info.sha256:
        extracted_path.unlink(missing_ok=True)
        raise MerlinFirmwareError(
            "Downloaded Merlin firmware failed SHA256 validation"
        )

    zip_path.unlink(missing_ok=True)
    _LOGGER.info("Prepared Merlin firmware `%s`", extracted_path.name)
    progress(55)

    return _with_local_firmware(info, extracted_path, cached=False)


async def async_download_merlin_firmware(
    session: aiohttp.ClientSession,
    model: str,
    version: str,
    download_dir: Path,
    progress: ProgressCallback | None = None,
) -> Path:
    """Download and validate a Merlin firmware image."""

    if progress is None:
        def _ignore_progress(_: int) -> None:
            return None

        progress = _ignore_progress

    zip_name, _ = _sourceforge_url(model, version)
    info = await async_prepare_merlin_firmware(
        session=session,
        model=model,
        version=version,
        cache_dir=download_dir,
        progress=progress,
    )
    if not info.local_firmware_path:
        raise MerlinFirmwareError(
            f"Merlin firmware `{zip_name}` was not prepared"
        )

    return Path(info.local_firmware_path)


async def async_validate_merlin_firmware(
    session: aiohttp.ClientSession,
    model: str,
    version: str,
    firmware_path: Path,
) -> MerlinFirmwareInfo:
    """Validate a prepared Merlin firmware image against published metadata."""

    info = await async_get_merlin_firmware_info(session, model, version)

    if not info.firmware_name or not info.sha256:
        raise MerlinFirmwareError(
            "No published SHA256 found for the Merlin firmware image; "
            "refusing to upload firmware"
        )

    if firmware_path.name != info.firmware_name:
        raise MerlinFirmwareError(
            "Prepared Merlin firmware filename does not match published "
            f"metadata: `{firmware_path.name}` != `{info.firmware_name}`"
        )

    if not firmware_path.is_file():
        raise MerlinFirmwareError(
            f"Prepared Merlin firmware file does not exist: `{firmware_path}`"
        )

    actual_sha = await asyncio.to_thread(_sha256, firmware_path)
    if actual_sha != info.sha256:
        raise MerlinFirmwareError(
            "Prepared Merlin firmware failed SHA256 validation"
        )

    return _with_local_firmware(info, firmware_path, cached=True)
