import json
import plistlib
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.update_source import app_info_from_ipa, read_source


ROOT = Path(__file__).resolve().parent.parent


class AppInfoFromIpaTests(unittest.TestCase):
    def create_ipa(self, root: Path, *, signed: bool = False) -> Path:
        ipa_path = root / "Example.ipa"
        main_info = {
            "CFBundleIdentifier": "com.example.app",
            "CFBundleShortVersionString": "1.2.3",
            "CFBundleVersion": "45",
            "MinimumOSVersion": "16.0",
            "NSCameraUsageDescription": "Scan a code.",
        }
        extension_info = {
            "CFBundleIdentifier": "com.example.app.widget",
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
                "Payload/Example.app/Frameworks/Example.framework/Info.plist",
                plistlib.dumps(framework_info, fmt=plistlib.FMT_BINARY),
            )
            if signed:
                archive.writestr(
                    "Payload/Example.app/_CodeSignature/CodeResources", b"signed"
                )

        return ipa_path

    def test_reads_main_metadata_and_all_app_privacy_descriptions(self) -> None:
        with tempfile.TemporaryDirectory() as temp_name:
            info, privacy = app_info_from_ipa(
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

    def test_rejects_signed_app_until_entitlements_are_reviewed(self) -> None:
        with tempfile.TemporaryDirectory() as temp_name:
            ipa_path = self.create_ipa(Path(temp_name), signed=True)
            with self.assertRaisesRegex(RuntimeError, "appPermissions.entitlements"):
                app_info_from_ipa(ipa_path)


class SplitSourceTests(unittest.TestCase):
    def test_source_is_built_from_config_and_split_app_files(self) -> None:
        source_config = json.loads(
            (ROOT / "config" / "source.json").read_text(encoding="utf-8")
        )
        split_apps = [
            json.loads((ROOT / "apps" / filename).read_text(encoding="utf-8"))
            for filename in ("mikan.json", "melox.json")
        ]
        source = read_source()

        self.assertNotIn("apps", source_config)
        self.assertEqual(
            {key: value for key, value in source.items() if key != "apps"},
            source_config,
        )
        self.assertEqual(source["apps"], split_apps)


if __name__ == "__main__":
    unittest.main()
