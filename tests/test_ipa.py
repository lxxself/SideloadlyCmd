from __future__ import annotations

import plistlib
import stat
import struct
import tempfile
import unittest
import zipfile
from pathlib import Path

from sideloadlycmd.ipa import IPAError, inspect_ipa


def info_plist(platform_name: str = "iphoneos") -> bytes:
    return plistlib.dumps(
        {
            "CFBundleDisplayName": "Fixture App",
            "CFBundleIdentifier": "com.example.fixture",
            "CFBundleExecutable": "Fixture",
            "CFBundleShortVersionString": "1.2.3",
            "CFBundleVersion": "42",
            "MinimumOSVersion": "16.0",
            "DTPlatformName": platform_name,
            "CFBundleSupportedPlatforms": ["iPhoneOS" if platform_name == "iphoneos" else "iPhoneSimulator"],
            "UIDeviceFamily": [1, 2],
        }
    )


class IPAInspectionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def create_ipa(self, name: str = "fixture.ipa", entries: dict[str, bytes] | None = None) -> Path:
        path = self.root / name
        payload = entries or {
            "Payload/Fixture.app/Info.plist": info_plist(),
            "Payload/Fixture.app/Fixture": b"not a real executable; structural fixture only",
        }
        with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for member, data in payload.items():
                archive.writestr(member, data)
        return path

    def test_inspects_valid_device_ipa_without_extracting(self) -> None:
        path = self.create_ipa()
        result = inspect_ipa(path)
        self.assertEqual(result.bundle_id, "com.example.fixture")
        self.assertEqual(result.version, "1.2.3")
        self.assertEqual(result.app_name, "Fixture App")
        self.assertNotIn("path", result.public_dict())

    def test_rejects_archive_path_traversal(self) -> None:
        path = self.create_ipa(
            entries={
                "Payload/Fixture.app/Info.plist": info_plist(),
                "Payload/../../outside.txt": b"bad",
            }
        )
        with self.assertRaisesRegex(IPAError, "path traversal"):
            inspect_ipa(path)

    def test_rejects_symlink_that_escapes_the_archive(self) -> None:
        path = self.root / "symlink.ipa"
        link = zipfile.ZipInfo("Payload/Fixture.app/link")
        link.create_system = 3
        link.external_attr = (stat.S_IFLNK | 0o777) << 16
        with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("Payload/Fixture.app/Info.plist", info_plist())
            archive.writestr(link, b"../../../../outside")
        with self.assertRaisesRegex(IPAError, "symbolic link"):
            inspect_ipa(path)

    def test_rejects_simulator_archive(self) -> None:
        path = self.create_ipa(
            entries={
                "Payload/Fixture.app/Info.plist": info_plist("iphonesimulator"),
                "Payload/Fixture.app/Fixture": b"fixture",
            }
        )
        with self.assertRaisesRegex(IPAError, "iPhoneOS|simulator"):
            inspect_ipa(path)

    def test_rejects_multiple_top_level_apps(self) -> None:
        path = self.create_ipa(
            entries={
                "Payload/Fixture.app/Info.plist": info_plist(),
                "Payload/Second.app/Info.plist": info_plist(),
            }
        )
        with self.assertRaisesRegex(IPAError, "exactly one app"):
            inspect_ipa(path)

    def test_reads_macho_encryption_info(self) -> None:
        header = struct.pack("<IiiIIIII", 0xFEEDFACF, 0x01000007, 3, 2, 1, 24, 0, 0)
        encrypted_command = struct.pack("<IIIIII", 0x2C, 24, 0, 1, 1, 0)
        plain_command = struct.pack("<IIIIII", 0x2C, 24, 0, 1, 0, 0)
        encrypted = self.create_ipa(
            name="encrypted.ipa",
            entries={"Payload/Fixture.app/Info.plist": info_plist(), "Payload/Fixture.app/Fixture": header + encrypted_command},
        )
        plain = self.create_ipa(
            name="plain.ipa",
            entries={"Payload/Fixture.app/Info.plist": info_plist(), "Payload/Fixture.app/Fixture": header + plain_command},
        )
        self.assertEqual(inspect_ipa(encrypted).encryption_status, "encrypted")
        self.assertEqual(inspect_ipa(plain).encryption_status, "not_encrypted")

    def test_rejects_non_ipa_and_corrupt_archive(self) -> None:
        wrong_suffix = self.root / "fixture.zip"
        wrong_suffix.write_bytes(b"not a zip")
        with self.assertRaisesRegex(IPAError, r"\.ipa"):
            inspect_ipa(wrong_suffix)
        corrupt = self.root / "corrupt.ipa"
        corrupt.write_bytes(b"not a zip")
        with self.assertRaisesRegex(IPAError, "corrupt or unreadable"):
            inspect_ipa(corrupt)


if __name__ == "__main__":
    unittest.main()
