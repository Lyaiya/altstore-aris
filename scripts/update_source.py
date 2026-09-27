#!/usr/bin/env python3
"""Build the AltStore source from metadata and upstream release IPAs."""

from __future__ import annotations

import argparse
import json
import os
import plistlib
import shutil
import tempfile
import urllib.request
import zipfile
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
SOURCE_CONFIG_PATH = ROOT / "config" / "source.json"
SOURCE_OUTPUT_PATH = ROOT / "dist" / "source.json"
GITHUB_API_VERSION = "2026-03-10"

UPSTREAM_APPS = (
    {
        "repo": "iota9star/mikan_flutter",
        "asset_name": "ios-release.ipa",
        "bundle_identifier": "io.nichijou.flutter.mikan",
        "app_path": "apps/mikan.json",
    },
    {
        "repo": "youshen2/MeloX",
        "asset_name": "MeloX-iOS-unsigned.ipa",
        "bundle_identifier": "moye.MeloX",
        "app_path": "apps/melox.json",
    },
)


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


def app_info_from_ipa(ipa_path: Path) -> tuple[dict[str, Any], dict[str, str]]:
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

        main_app_prefix = main_plists[0][: -len("Info.plist")]
        signature_resources = f"{main_app_prefix}_CodeSignature/CodeResources"
        if signature_resources in names:
            raise RuntimeError(
                "The main app is signed. Review and update appPermissions.entitlements "
                "before publishing this release."
            )

        main_info = plistlib.loads(archive.read(main_plists[0]))
        privacy: dict[str, str] = {}

        for name in names:
            if (
                not name.startswith("Payload/")
                or not name.endswith("/Info.plist")
                or "/Frameworks/" in name
                or ".bundle/Info.plist" in name
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

    return main_info, privacy


def required_string(info: dict[str, Any], key: str) -> str:
    value = info.get(key)
    if not isinstance(value, str) or not value:
        raise RuntimeError(f"IPA Info.plist is missing {key}")
    return value


def read_source() -> dict[str, Any]:
    with SOURCE_CONFIG_PATH.open(encoding="utf-8") as source_file:
        source = json.load(source_file)

    apps = []
    for config in UPSTREAM_APPS:
        app_path = ROOT / config["app_path"]
        with app_path.open(encoding="utf-8") as app_file:
            app = json.load(app_file)
        if app.get("bundleIdentifier") != config["bundle_identifier"]:
            raise RuntimeError(
                f"{app_path}: expected bundle ID {config['bundle_identifier']}, "
                f"found {app.get('bundleIdentifier')}"
            )
        apps.append(app)

    source["apps"] = apps
    return source


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def write_source(source: dict[str, Any]) -> None:
    for config in UPSTREAM_APPS:
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
        write_json(ROOT / config["app_path"], matching_apps[0])

    write_json(SOURCE_OUTPUT_PATH, source)


def update_app(source: dict[str, Any], config: dict[str, Any], temp_dir: Path) -> None:
    release = read_json(
        f"https://api.github.com/repos/{config['repo']}/releases/latest"
    )
    assets = [
        asset
        for asset in release.get("assets", [])
        if asset.get("name") == config["asset_name"]
    ]
    if len(assets) != 1:
        raise RuntimeError(
            f"{config['repo']}: expected one {config['asset_name']} asset, found {len(assets)}"
        )

    asset = assets[0]
    ipa_path = temp_dir / config["asset_name"]
    download_url = required_string(asset, "browser_download_url")
    download(download_url, ipa_path)
    actual_size = ipa_path.stat().st_size
    expected_size = asset.get("size")
    if not isinstance(expected_size, int) or expected_size != actual_size:
        raise RuntimeError(
            f"{config['repo']}: downloaded size {actual_size} does not match "
            f"GitHub asset size {expected_size}"
        )
    info, privacy = app_info_from_ipa(ipa_path)

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
        help="build dist/source.json without checking GitHub releases",
    )
    args = parser.parse_args()
    source = read_source()

    if not args.build_only:
        with tempfile.TemporaryDirectory(prefix="altstore-source-") as temp_name:
            temp_root = Path(temp_name)
            for index, config in enumerate(UPSTREAM_APPS):
                app_temp = temp_root / str(index)
                app_temp.mkdir()
                update_app(source, config, app_temp)

    write_source(source)


if __name__ == "__main__":
    main()
