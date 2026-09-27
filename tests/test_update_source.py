import json
import plistlib
import struct
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.update_source import app_info_from_ipa, find_release_asset, read_source


ROOT = Path(__file__).resolve().parent.parent


class AppInfoFromIpaTests(unittest.TestCase):
    def create_macho(self, entitlements: dict | None = None) -> bytes:
        if entitlements is None:
            return struct.pack("<IiiIIIII", 0xFEEDFACF, 0, 0, 2, 0, 0, 0, 0)

        payload = plistlib.dumps(entitlements)
        entitlement_blob = struct.pack(
            ">II", 0xFADE7171, 8 + len(payload)
        ) + payload
        signature_length = 20 + len(entitlement_blob)
        signature = (
            struct.pack(">III", 0xFADE0CC0, signature_length, 1)
            + struct.pack(">II", 5, 20)
            + entitlement_blob
        )
        header = struct.pack("<IiiIIIII", 0xFEEDFACF, 0, 0, 2, 1, 16, 0, 0)
        command = struct.pack("<IIII", 0x1D, 16, 48, len(signature))
        return header + command + signature

    def create_ipa(
        self, root: Path, *, entitlements: dict | None = None
    ) -> Path:
        ipa_path = root / "Example.ipa"
        main_info = {
            "CFBundleIdentifier": "com.example.app",
            "CFBundleExecutable": "Example",
            "CFBundleShortVersionString": "1.2.3",
            "CFBundleVersion": "45",
            "MinimumOSVersion": "16.0",
            "NSCameraUsageDescription": "Scan a code.",
        }
        extension_info = {
            "CFBundleIdentifier": "com.example.app.widget",
            "CFBundleExecutable": "Widget",
            "NSMicrophoneUsageDescription": "Identify nearby music.",
        }
        framework_info = {
            "NSLocationWhenInUseUsageDescription": "Must not be included."
        }

        with zipfile.ZipFile(ipa_path, "w") as archive:
            archive.writestr(
                "Payload/Example.app/Info.plist",
                plistlib.dumps(main_info, fmt=plistlib.FMT_BINARY),
            )
            archive.writestr(
                "Payload/Example.app/PlugIns/Widget.appex/Info.plist",
                plistlib.dumps(extension_info, fmt=plistlib.FMT_BINARY),
            )
            archive.writestr(
                "Payload/Example.app/Example",
                self.create_macho(entitlements),
            )
            archive.writestr(
                "Payload/Example.app/PlugIns/Widget.appex/Widget",
                self.create_macho(),
            )
            archive.writestr(
                "Payload/Example.app/Frameworks/Example.framework/Info.plist",
                plistlib.dumps(framework_info, fmt=plistlib.FMT_BINARY),
            )
        return ipa_path

    def test_reads_main_metadata_and_all_app_privacy_descriptions(self) -> None:
        with tempfile.TemporaryDirectory() as temp_name:
            info, privacy, entitlements = app_info_from_ipa(
                self.create_ipa(Path(temp_name))
            )

        self.assertEqual(info["CFBundleIdentifier"], "com.example.app")
        self.assertEqual(
            privacy,
            {
                "NSCameraUsageDescription": "Scan a code.",
                "NSMicrophoneUsageDescription": "Identify nearby music.",
            },
        )
        self.assertEqual(entitlements, [])

    def test_reads_entitlements_and_omits_standard_identifiers(self) -> None:
        with tempfile.TemporaryDirectory() as temp_name:
            ipa_path = self.create_ipa(
                Path(temp_name),
                entitlements={
                    "application-identifier": "TEAM.com.example.app",
                    "com.apple.developer.team-identifier": "TEAM",
                    "com.apple.developer.associated-domains": [
                        "applinks:example.com"
                    ],
                },
            )
            _, _, entitlements = app_info_from_ipa(ipa_path)

        self.assertEqual(
            entitlements, ["com.apple.developer.associated-domains"]
        )


class SplitSourceTests(unittest.TestCase):
    def test_main_source_is_built_from_config_and_split_app_files(self) -> None:
        source_config = json.loads(
            (ROOT / "config" / "source.json").read_text(encoding="utf-8")
        )
        split_apps = [
            json.loads((ROOT / "apps" / filename).read_text(encoding="utf-8"))
            for filename in ("mikan.json", "melox.json", "venera-prime.json")
        ]
        source = read_source()

        self.assertNotIn("apps", source_config)
        self.assertEqual(
            {key: value for key, value in source.items() if key != "apps"},
            source_config,
        )
        self.assertEqual(source["apps"], split_apps)
        self.assertEqual(
            source["apps"][0]["versions"][0]["marketingVersion"], "2.3.6"
        )
        self.assertEqual(
            source["apps"][1]["versions"][0]["marketingVersion"], "1.2.1"
        )
        self.assertEqual(
            source["apps"][2]["versions"][0]["marketingVersion"], "2.4.1"
        )

    def test_nsfw_source_is_built_separately(self) -> None:
        source_config = json.loads(
            (ROOT / "config" / "source-nsfw.json").read_text(encoding="utf-8")
        )
        loveiwara = json.loads(
            (ROOT / "apps" / "nsfw" / "loveiwara.json").read_text(
                encoding="utf-8"
            )
        )

        source = read_source("nsfw")

        self.assertTrue(source["nsfw"])
        self.assertEqual(
            {key: value for key, value in source.items() if key != "apps"},
            source_config,
        )
        self.assertEqual(source["apps"], [loveiwara])
        self.assertEqual(
            source["apps"][0]["versions"][0]["marketingVersion"], "0.6.1"
        )


class ReleaseAssetTests(unittest.TestCase):
    def test_finds_asset_by_pattern(self) -> None:
        release = {
            "assets": [
                {"name": "venera-prime-2.4.1.apk"},
                {"name": "venera-prime-ios-2.4.1+241.ipa"},
            ]
        }
        config = {
            "repo": "venera-app/venera-prime",
            "asset_pattern": r"^venera-prime-ios-.*\.ipa$",
        }

        asset = find_release_asset(release, config)

        self.assertEqual(asset["name"], "venera-prime-ios-2.4.1+241.ipa")


if __name__ == "__main__":
    unittest.main()
