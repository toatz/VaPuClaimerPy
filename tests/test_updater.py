import tempfile
import unittest
from pathlib import Path
import zipfile

from vapuclaimer import updater


class VersionTests(unittest.TestCase):
    def test_newer_version(self):
        self.assertTrue(updater.is_newer_version("v1.3.0", "v1.2.0"))
        self.assertFalse(updater.is_newer_version("v1.2.0", "v1.2.0"))
        self.assertFalse(updater.is_newer_version("v1.1.9", "v1.2.0"))

    def test_stable_beats_prerelease(self):
        self.assertTrue(updater.is_newer_version("v1.2.0", "v1.2.0-beta.2"))


class AssetSelectionTests(unittest.TestCase):
    def test_python_assets(self):
        self.assertEqual(
            updater.expected_asset_names("v1.3.0", updater.MODE_PYTHON),
            (
                "VaPuClaimer-Python-v1.3.0.zip",
                "VaPuClaimer-Python-v1.3.0.zip.sha256",
            ),
        )

    def test_exe_assets(self):
        self.assertEqual(
            updater.expected_asset_names("v1.3.0", updater.MODE_EXE),
            (
                "VaPuClaimer.exe",
                "VaPuClaimer.exe.sha256",
            ),
        )


class ChecksumTests(unittest.TestCase):
    def test_checksum(self):
        digest = "a" * 64
        self.assertEqual(
            updater._parse_checksum(
                f"{digest}  VaPuClaimer.exe\n",
                "VaPuClaimer.exe",
            ),
            digest,
        )

    def test_wrong_filename(self):
        with self.assertRaises(updater.UpdateError):
            updater._parse_checksum(
                "b" * 64 + "  wrong.exe\n",
                "VaPuClaimer.exe",
            )


class ZipSafetyTests(unittest.TestCase):
    def test_normal_zip(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            archive = root / "ok.zip"
            out = root / "out"
            with zipfile.ZipFile(archive, "w") as zf:
                zf.writestr("vapuclaimer/test.py", "ok")
            updater.safe_extract_zip(archive, out)
            self.assertEqual((out / "vapuclaimer" / "test.py").read_text(), "ok")

    def test_traversal_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            archive = root / "bad.zip"
            out = root / "out"
            with zipfile.ZipFile(archive, "w") as zf:
                zf.writestr("../outside.txt", "nope")
            with self.assertRaises(updater.UpdateError):
                updater.safe_extract_zip(archive, out)



class PyInstallerEnvironmentTests(unittest.TestCase):
    def test_clean_environment_removes_internal_pyi_vars(self):
        import os
        old = os.environ.get("_PYI_TEST_MARKER")
        old_reset = os.environ.get("PYINSTALLER_RESET_ENVIRONMENT")
        try:
            os.environ["_PYI_TEST_MARKER"] = "old-parent"
            env = updater._clean_pyinstaller_environment()
            self.assertNotIn("_PYI_TEST_MARKER", env)
            self.assertEqual(env["PYINSTALLER_RESET_ENVIRONMENT"], "1")
        finally:
            if old is None:
                os.environ.pop("_PYI_TEST_MARKER", None)
            else:
                os.environ["_PYI_TEST_MARKER"] = old
            if old_reset is None:
                os.environ.pop("PYINSTALLER_RESET_ENVIRONMENT", None)
            else:
                os.environ["PYINSTALLER_RESET_ENVIRONMENT"] = old_reset


if __name__ == "__main__":
    unittest.main()
