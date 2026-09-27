#!/usr/bin/env python3
"""Build the AltStore source from metadata and upstream release IPAs."""

from __future__ import annotations

import argparse
import json
import os
import plistlib
import re
import shutil
import struct
import tempfile
import tomllib
import urllib.request
import zipfile
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
GITHUB_API_VERSION = "2026-03-10"

SOURCES = {
    "main": {
        "config_path": ROOT / "data" / "sources" / "source.json",
        "output_path": ROOT / "dist" / "source.json",
    },
    "nsfw": {
        "config_path": ROOT / "data" / "sources" / "source-nsfw.json",
        "output_path": ROOT / "dist" / "source-nsfw.json",
    },
}

APPS_ROOT = ROOT / "data" / "apps"
APP_METADATA_FILENAME = "app.json"
APP_VERSIONS_FILENAME = "versions.json"
UPSTREAM_CONFIG_FILENAME = "upstream.toml"

IGNORED_ENTITLEMENTS = {
    "application-identifier",
    "com.app.developer.team-identifier",
    "com.apple.application-identifier",
    "com.apple.developer.team-identifier",
}


def load_upstream_apps() -> tuple[dict[str, Any], ...]:
    configs = []
    config_paths = sorted(APPS_ROOT.glob(f"*/{UPSTREAM_CONFIG_FILENAME}"))
    if not config_paths:
        raise RuntimeError(
            f"No {UPSTREAM_CONFIG_FILENAME} files found in {APPS_ROOT}"
        )

    for config_path in config_paths:
        with config_path.open("rb") as config_file:
            config = tomllib.load(config_file)
        context = config_path.relative_to(ROOT)

        for key in ("repo", "source"):
            value = config.get(key)
            if not isinstance(value, str) or not value:
                raise RuntimeError(f"{context}: {key} must be a non-empty string")

        if config["source"] not in SOURCES:
            raise RuntimeError(f"{context}: unknown source {config['source']}")

        asset_name = config.get("asset_name")
        asset_pattern = config.get("asset_pattern")
        if (asset_name is None) == (asset_pattern is None):
            raise RuntimeError(
                f"{context}: configure exactly one of asset_name or asset_pattern"
            )
        asset_key = "asset_name" if asset_name is not None else "asset_pattern"
        if not isinstance(config[asset_key], str) or not config[asset_key]:
            raise RuntimeError(
                f"{context}: {asset_key} must be a non-empty string"
            )

        app_path = config_path.parent / APP_METADATA_FILENAME
        if not app_path.is_file():
            raise RuntimeError(f"{context}: missing {APP_METADATA_FILENAME}")
        with app_path.open(encoding="utf-8") as app_file:
            app = json.load(app_file)
        bundle_identifier = app.get("bundleIdentifier")
        if not isinstance(bundle_identifier, str) or not bundle_identifier:
            raise RuntimeError(
                f"{app_path.relative_to(ROOT)}: bundleIdentifier must be a "
                "non-empty string"
            )
        if "versions" in app:
            raise RuntimeError(
                f"{app_path.relative_to(ROOT)}: versions must be stored in "
                f"{APP_VERSIONS_FILENAME}"
            )

        versions_path = config_path.parent / APP_VERSIONS_FILENAME
        if not versions_path.is_file():
            raise RuntimeError(f"{context}: missing {APP_VERSIONS_FILENAME}")

        config["bundle_identifier"] = bundle_identifier
        config["app_path"] = app_path.relative_to(ROOT).as_posix()
        config["versions_path"] = versions_path.relative_to(ROOT).as_posix()
        configs.append(config)

    return tuple(
        sorted(
            configs,
            key=lambda config: (
                config["source"],
                config["repo"],
            ),
        )
    )


UPSTREAM_APPS = load_upstream_apps()


def request(
    url: str, *, accept: str, authenticated: bool = False
) -> urllib.request.Request:
    headers = {
        "Accept": accept,
        "User-Agent": "altstore-aris-source-updater",
    }
    if authenticated:
        headers["X-GitHub-Api-Version"] = GITHUB_API_VERSION
        token = os.environ.get("GITHUB_TOKEN")
        if token:
            headers["Authorization"] = f"Bearer {token}"
    return urllib.request.Request(url, headers=headers)


def read_json(url: str) -> dict[str, Any]:
    api_request = request(
        url, accept="application/vnd.github+json", authenticated=True
    )
    with urllib.request.urlopen(api_request, timeout=30) as response:
        return json.load(response)


def download(url: str, destination: Path) -> None:
    # Release assets are public. Do not forward GITHUB_TOKEN to GitHub's
    # cross-origin release-assets redirect.
    asset_request = request(url, accept="application/octet-stream")
    with (
        urllib.request.urlopen(asset_request, timeout=120) as response,
        destination.open("wb") as output,
    ):
        shutil.copyfileobj(response, output)


def _macho_slices(data: bytes, context: str) -> list[bytes]:
    fat_formats = {
        b"\xca\xfe\xba\xbe": (">", False, 20),
        b"\xbe\xba\xfe\xca": ("<", False, 20),
        b"\xca\xfe\xba\xbf": (">", True, 32),
        b"\xbf\xba\xfe\xca": ("<", True, 32),
    }
    fat_format = fat_formats.get(data[:4])
    if fat_format is None:
        return [data]

    endian, is_64_bit, record_size = fat_format
    if len(data) < 8:
        raise RuntimeError(f"{context}: truncated universal Mach-O header")
    architecture_count = struct.unpack_from(f"{endian}I", data, 4)[0]
    slices = []
    for index in range(architecture_count):
        record_offset = 8 + index * record_size
        if record_offset + record_size > len(data):
            raise RuntimeError(f"{context}: truncated universal Mach-O record")
        if is_64_bit:
            slice_offset, slice_size = struct.unpack_from(
                f"{endian}QQ", data, record_offset + 8
            )
        else:
            slice_offset, slice_size = struct.unpack_from(
                f"{endian}II", data, record_offset + 8
            )
        if slice_offset + slice_size > len(data):
            raise RuntimeError(f"{context}: invalid universal Mach-O slice")
        slices.append(data[slice_offset : slice_offset + slice_size])
    return slices


def _entitlements_from_signature(signature: bytes, context: str) -> set[str]:
    if len(signature) < 12:
        raise RuntimeError(f"{context}: truncated code signature")
    magic, total_length, blob_count = struct.unpack_from(">III", signature)
    if magic != 0xFADE0CC0 or total_length > len(signature):
        raise RuntimeError(f"{context}: invalid embedded code signature")

    entitlements: set[str] = set()
    found_xml_entitlements = False
    found_der_entitlements = False
    for index in range(blob_count):
        entry_offset = 12 + index * 8
        if entry_offset + 8 > total_length:
            raise RuntimeError(f"{context}: truncated code signature index")
        slot_type, blob_offset = struct.unpack_from(">II", signature, entry_offset)
        if blob_offset + 8 > total_length:
            raise RuntimeError(f"{context}: invalid code signature blob offset")
        blob_magic, blob_length = struct.unpack_from(">II", signature, blob_offset)
        if blob_length < 8 or blob_offset + blob_length > total_length:
            raise RuntimeError(f"{context}: invalid code signature blob length")

        if slot_type == 5:
            if blob_magic != 0xFADE7171:
                raise RuntimeError(f"{context}: invalid entitlement blob")
            payload = signature[blob_offset + 8 : blob_offset + blob_length]
            values = plistlib.loads(payload.rstrip(b"\0"))
            if not isinstance(values, dict):
                raise RuntimeError(f"{context}: entitlements must be a dictionary")
            entitlements.update(values)
            found_xml_entitlements = True
        elif slot_type == 7:
            found_der_entitlements = True

    if found_der_entitlements and not found_xml_entitlements:
        raise RuntimeError(
            f"{context}: DER-only entitlements require manual review"
        )
    return entitlements


def _entitlements_from_macho(data: bytes, context: str) -> set[str]:
    macho_formats = {
        b"\xce\xfa\xed\xfe": ("<", 28),
        b"\xcf\xfa\xed\xfe": ("<", 32),
        b"\xfe\xed\xfa\xce": (">", 28),
        b"\xfe\xed\xfa\xcf": (">", 32),
    }
    entitlements: set[str] = set()
    for binary in _macho_slices(data, context):
        macho_format = macho_formats.get(binary[:4])
        if macho_format is None:
            raise RuntimeError(f"{context}: executable is not a Mach-O binary")
        endian, header_size = macho_format
        if len(binary) < header_size:
            raise RuntimeError(f"{context}: truncated Mach-O header")
        command_count = struct.unpack_from(f"{endian}I", binary, 16)[0]
        command_offset = header_size

        for _ in range(command_count):
            if command_offset + 8 > len(binary):
                raise RuntimeError(f"{context}: truncated Mach-O load command")
            command, command_size = struct.unpack_from(
                f"{endian}II", binary, command_offset
            )
            if command_size < 8 or command_offset + command_size > len(binary):
                raise RuntimeError(f"{context}: invalid Mach-O load command")
            if command == 0x1D:
                if command_size < 16:
                    raise RuntimeError(f"{context}: invalid code signature command")
                signature_offset, signature_size = struct.unpack_from(
                    f"{endian}II", binary, command_offset + 8
                )
                if signature_offset + signature_size > len(binary):
                    raise RuntimeError(f"{context}: invalid code signature range")
                signature = binary[
                    signature_offset : signature_offset + signature_size
                ]
                entitlements.update(
                    _entitlements_from_signature(signature, context)
                )
            command_offset += command_size
    return entitlements


def app_info_from_ipa(
    ipa_path: Path,
) -> tuple[dict[str, Any], dict[str, str], list[str]]:
    with zipfile.ZipFile(ipa_path) as archive:
        names = archive.namelist()
        main_plists = [
            name
            for name in names
            if name.startswith("Payload/")
            and name.count("/") == 2
            and name.endswith(".app/Info.plist")
        ]
        if len(main_plists) != 1:
            raise RuntimeError(
                f"Expected exactly one main app Info.plist, found {main_plists}"
            )

        main_info = plistlib.loads(archive.read(main_plists[0]))
        privacy: dict[str, str] = {}
        entitlements: set[str] = set()

        for name in names:
            if (
                not name.startswith("Payload/")
                or not name.endswith("/Info.plist")
                or not (
                    name.endswith(".app/Info.plist")
                    or name.endswith(".appex/Info.plist")
                )
            ):
                continue

            info = plistlib.loads(archive.read(name))
            for key, value in info.items():
                if not (key.startswith("NS") and key.endswith("UsageDescription")):
                    continue
                if not isinstance(value, str):
                    raise RuntimeError(f"{name}: {key} must be a string")
                if key in privacy and privacy[key] != value:
                    raise RuntimeError(
                        f"Conflicting {key} values in app and extension Info.plists"
                    )
                privacy[key] = value

            executable = required_string(info, "CFBundleExecutable")
            executable_path = name[: -len("Info.plist")] + executable
            if executable_path not in names:
                raise RuntimeError(f"{name}: missing executable {executable}")
            entitlements.update(
                _entitlements_from_macho(
                    archive.read(executable_path), executable_path
                )
            )

    return main_info, privacy, sorted(entitlements - IGNORED_ENTITLEMENTS)


def required_string(info: dict[str, Any], key: str) -> str:
    value = info.get(key)
    if not isinstance(value, str) or not value:
        raise RuntimeError(f"IPA Info.plist is missing {key}")
    return value


def find_release_asset(
    release: dict[str, Any], config: dict[str, Any]
) -> dict[str, Any]:
    asset_name = config.get("asset_name")
    asset_pattern = config.get("asset_pattern")
    if (asset_name is None) == (asset_pattern is None):
        raise RuntimeError(
            f"{config['repo']}: configure exactly one of asset_name or asset_pattern"
        )

    assets = release.get("assets", [])
    if not isinstance(assets, list):
        raise RuntimeError(f"{config['repo']}: release assets must be a list")

    if asset_name is not None:
        matching_assets = [
            asset for asset in assets if asset.get("name") == asset_name
        ]
        expected = asset_name
    else:
        pattern = re.compile(asset_pattern)
        matching_assets = [
            asset
            for asset in assets
            if isinstance(asset.get("name"), str)
            and pattern.fullmatch(asset["name"])
        ]
        expected = f"an asset matching {asset_pattern}"

    if len(matching_assets) != 1:
        raise RuntimeError(
            f"{config['repo']}: expected one {expected}, "
            f"found {len(matching_assets)}"
        )
    return matching_assets[0]


def read_source(source_name: str = "main") -> dict[str, Any]:
    if source_name not in SOURCES:
        raise RuntimeError(f"Unknown source {source_name}")
    source_config = SOURCES[source_name]
    with source_config["config_path"].open(encoding="utf-8") as source_file:
        source = json.load(source_file)

    apps = []
    for config in UPSTREAM_APPS:
        if config["source"] != source_name:
            continue
        app_path = ROOT / config["app_path"]
        with app_path.open(encoding="utf-8") as app_file:
            app = json.load(app_file)
        versions_path = ROOT / config["versions_path"]
        with versions_path.open(encoding="utf-8") as versions_file:
            versions = json.load(versions_file)
        if not isinstance(versions, list):
            raise RuntimeError(f"{versions_path}: expected a JSON array")
        if app.get("bundleIdentifier") != config["bundle_identifier"]:
            raise RuntimeError(
                f"{app_path}: expected bundle ID {config['bundle_identifier']}, "
                f"found {app.get('bundleIdentifier')}"
            )
        app["versions"] = versions
        apps.append(app)

    source["apps"] = apps
    return source


def read_sources() -> dict[str, dict[str, Any]]:
    return {source_name: read_source(source_name) for source_name in SOURCES}


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def write_source(source: dict[str, Any], source_name: str = "main") -> None:
    if source_name not in SOURCES:
        raise RuntimeError(f"Unknown source {source_name}")
    for config in UPSTREAM_APPS:
        if config["source"] != source_name:
            continue
        matching_apps = [
            app
            for app in source["apps"]
            if app.get("bundleIdentifier") == config["bundle_identifier"]
        ]
        if len(matching_apps) != 1:
            raise RuntimeError(
                "Expected exactly one app with bundle ID "
                f"{config['bundle_identifier']}"
            )
        app = matching_apps[0]
        versions = app.get("versions")
        if not isinstance(versions, list):
            raise RuntimeError(
                f"Expected versions for app with bundle ID "
                f"{config['bundle_identifier']} to be an array"
            )
        app_metadata = {
            key: value for key, value in app.items() if key != "versions"
        }
        write_json(ROOT / config["app_path"], app_metadata)
        write_json(ROOT / config["versions_path"], versions)

    write_json(SOURCES[source_name]["output_path"], source)


def write_sources(sources: dict[str, dict[str, Any]]) -> None:
    for source_name, source in sources.items():
        write_source(source, source_name)


def update_app(source: dict[str, Any], config: dict[str, Any], temp_dir: Path) -> None:
    release = read_json(
        f"https://api.github.com/repos/{config['repo']}/releases/latest"
    )
    asset = find_release_asset(release, config)
    ipa_name = required_string(asset, "name")
    if Path(ipa_name).name != ipa_name:
        raise RuntimeError(f"{config['repo']}: unsafe asset name {ipa_name}")
    ipa_path = temp_dir / ipa_name
    download_url = required_string(asset, "browser_download_url")
    download(download_url, ipa_path)
    actual_size = ipa_path.stat().st_size
    expected_size = asset.get("size")
    if not isinstance(expected_size, int) or expected_size != actual_size:
        raise RuntimeError(
            f"{config['repo']}: downloaded size {actual_size} does not match "
            f"GitHub asset size {expected_size}"
        )
    info, privacy, entitlements = app_info_from_ipa(ipa_path)

    bundle_identifier = required_string(info, "CFBundleIdentifier")
    if bundle_identifier != config["bundle_identifier"]:
        raise RuntimeError(
            f"{config['repo']}: expected bundle ID {config['bundle_identifier']}, "
            f"found {bundle_identifier}"
        )

    apps = [
        app
        for app in source["apps"]
        if app.get("bundleIdentifier") == bundle_identifier
    ]
    if len(apps) != 1:
        raise RuntimeError(
            f"source must contain exactly one app with bundle ID {bundle_identifier}"
        )
    app = apps[0]

    version_number = required_string(info, "CFBundleShortVersionString")
    build_number = required_string(info, "CFBundleVersion")
    minimum_os = required_string(info, "MinimumOSVersion")
    release_date = required_string(release, "published_at")[:10]
    tag_name = required_string(release, "tag_name")

    matching_versions = [
        version
        for version in app["versions"]
        if version.get("version") == version_number
        and version.get("buildVersion") == build_number
    ]
    description = (
        matching_versions[0].get("localizedDescription")
        if matching_versions
        else f"同步上游 {tag_name} 版本。"
    )

    latest_version = {
        "version": version_number,
        "buildVersion": build_number,
        "marketingVersion": (
            tag_name[1:] if tag_name.lower().startswith("v") else tag_name
        ),
        "date": release_date,
        "localizedDescription": description,
        "downloadURL": download_url,
        "size": actual_size,
        "minOSVersion": minimum_os,
    }
    older_versions = [
        version
        for version in app["versions"]
        if not (
            version.get("version") == version_number
            and version.get("buildVersion") == build_number
        )
    ]
    app["versions"] = [latest_version, *older_versions]
    app["appPermissions"]["entitlements"] = entitlements
    app["appPermissions"]["privacy"] = privacy

    print(
        f"{config['repo']}: {tag_name} -> "
        f"{version_number} ({build_number}), {actual_size} bytes"
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--build-only",
        action="store_true",
        help="build source JSON files without checking GitHub releases",
    )
    args = parser.parse_args()
    sources = read_sources()

    if not args.build_only:
        with tempfile.TemporaryDirectory(prefix="altstore-source-") as temp_name:
            temp_root = Path(temp_name)
            for index, config in enumerate(UPSTREAM_APPS):
                app_temp = temp_root / str(index)
                app_temp.mkdir()
                update_app(sources[config["source"]], config, app_temp)

    write_sources(sources)


if __name__ == "__main__":
    main()
