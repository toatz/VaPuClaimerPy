from __future__ import annotations

import hashlib
from pathlib import Path
import tempfile
import unittest
import zipfile

from vapuclaimer import updater


class VersionTests(unittest.TestCase):
    def test_newer_stable_version(self):
        self.assertTrue(updater.is_newer_version("v1.2.0", "v1.1.9"))
        self.assertFalse(updater.is_newer_version("v1.2.0", "v1.2.0"))
        self.assertFalse(updater.is_newer_version("v1.1.9", "v1.2.0"))

    def test_stable_beats_prerelease(self):
        self.assertTrue(updater.is_newer_version("v1.0.0", "v1.0.0-beta.2"))

    def test_release_beats_unknown_dev_version(self):
        self.assertTrue(updater.is_newer_version("v1.0.0", "local-dev"))


class ChecksumTests(unittest.TestCase):
    def test_checksum_parser(self):
        digest = "a" * 64
        text = f"{digest}  VaPuClaimer-Python-v1.0.0.zip\n"
        self.assertEqual(
            updater._parse_checksum(text, "VaPuClaimer-Python-v1.0.0.zip"),
            digest,
        )

    def test_wrong_filename_rejected(self):
        digest = "b" * 64
        with self.assertRaises(updater.UpdateError):
            updater._parse_checksum(
                f"{digest}  wrong.zip\n",
                "VaPuClaimer-Python-v1.0.0.zip",
            )


class ZipSafetyTests(unittest.TestCase):
    def test_normal_zip_extracts(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            archive = root / "ok.zip"
            out = root / "out"

            with zipfile.ZipFile(archive, "w") as zf:
                zf.writestr("vapuclaimer/test.py", "ok")

            updater.safe_extract_zip(archive, out)
            self.assertEqual((out / "vapuclaimer" / "test.py").read_text(), "ok")

    def test_path_traversal_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            archive = root / "bad.zip"
            out = root / "out"

            with zipfile.ZipFile(archive, "w") as zf:
                zf.writestr("../outside.txt", "nope")

            with self.assertRaises(updater.UpdateError):
                updater.safe_extract_zip(archive, out)


if __name__ == "__main__":
    unittest.main()
