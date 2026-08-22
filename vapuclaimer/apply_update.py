from __future__ import annotations

import argparse
import ctypes
from ctypes import wintypes
from datetime import datetime
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import traceback


UPDATE_DIR_NAME = ".update"

# settings.ini is intentionally excluded.
MANAGED_TOP_LEVEL = (
    "VaPuClaimer.pyw",
    "run.bat",
    "version.txt",
    "vapu-vehicles.source.json",
    "settings.ini.example",
    "README.md",
    "CHANGELOG.md",
)
MANAGED_DIRECTORIES = ("vapuclaimer",)


def _wait_for_process_exit(pid: int, timeout_ms: int = 30_000) -> None:
    if os.name != "nt":
        # The app itself is Windows-only, but keep this helper understandable
        # if inspected or tested elsewhere.
        deadline = time.monotonic() + timeout_ms / 1000
        while time.monotonic() < deadline:
            try:
                os.kill(pid, 0)
            except OSError:
                return
            time.sleep(0.1)
        return

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    SYNCHRONIZE = 0x00100000
    WAIT_OBJECT_0 = 0x00000000
    WAIT_TIMEOUT = 0x00000102

    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel32.WaitForSingleObject.restype = wintypes.DWORD
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL

    handle = kernel32.OpenProcess(SYNCHRONIZE, False, pid)
    if not handle:
        # Parent already exited, or we cannot open it. In either case wait a
        # short grace period before replacing files.
        time.sleep(0.5)
        return

    try:
        result = kernel32.WaitForSingleObject(handle, timeout_ms)
        if result == WAIT_TIMEOUT:
            raise RuntimeError("VaPuClaimer did not exit before update timeout.")
        if result != WAIT_OBJECT_0:
            raise RuntimeError(f"Waiting for VaPuClaimer failed (code {result}).")
    finally:
        kernel32.CloseHandle(handle)


def _remove_path(path: Path) -> None:
    if path.is_symlink() or path.is_file():
        path.unlink(missing_ok=True)
    elif path.exists():
        shutil.rmtree(path)


def _copy_path(source: Path, destination: Path) -> None:
    if source.is_dir():
        shutil.copytree(source, destination, copy_function=shutil.copy2)
    else:
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)


def _managed_paths(base: Path):
    for name in MANAGED_TOP_LEVEL:
        yield base / name
    for name in MANAGED_DIRECTORIES:
        yield base / name


def _backup_current(install_dir: Path, backup_dir: Path) -> None:
    shutil.rmtree(backup_dir, ignore_errors=True)
    backup_dir.mkdir(parents=True, exist_ok=True)

    for source in _managed_paths(install_dir):
        if not source.exists():
            continue
        destination = backup_dir / source.name
        _copy_path(source, destination)


def _remove_current_managed(install_dir: Path) -> None:
    for path in _managed_paths(install_dir):
        _remove_path(path)


def _install_staged(staging_dir: Path, install_dir: Path) -> None:
    for name in MANAGED_TOP_LEVEL:
        source = staging_dir / name
        if source.exists():
            _copy_path(source, install_dir / name)

    for name in MANAGED_DIRECTORIES:
        source = staging_dir / name
        if not source.is_dir():
            raise RuntimeError(f"Update is missing required directory: {name}")
        _copy_path(source, install_dir / name)


def _restore_backup(backup_dir: Path, install_dir: Path) -> None:
    _remove_current_managed(install_dir)

    if not backup_dir.exists():
        return

    for source in backup_dir.iterdir():
        _copy_path(source, install_dir / source.name)


def _write_log(log_path: Path, message: str) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().isoformat(timespec="seconds")
    with log_path.open("a", encoding="utf-8") as handle:
        handle.write(f"[{stamp}] {message}\n")


def _restart(python_exe: Path, entrypoint: Path, install_dir: Path) -> None:
    creationflags = 0
    if os.name == "nt":
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)

    subprocess.Popen(
        [str(python_exe), str(entrypoint)],
        cwd=str(install_dir),
        close_fds=True,
        creationflags=creationflags,
    )


def apply_update(
    *,
    parent_pid: int,
    install_dir: Path,
    staging_dir: Path,
    python_exe: Path,
    entrypoint: Path,
    version: str,
) -> None:
    install_dir = install_dir.resolve()
    staging_dir = staging_dir.resolve()
    update_dir = install_dir / UPDATE_DIR_NAME
    backup_dir = update_dir / "backup"
    log_path = update_dir / "update.log"

    _write_log(log_path, f"Starting update to {version}")
    _wait_for_process_exit(parent_pid)
    _write_log(log_path, "Parent process exited")

    settings_before = install_dir / "settings.ini"
    settings_existed = settings_before.exists()

    try:
        _backup_current(install_dir, backup_dir)
        _write_log(log_path, f"Backup created at {backup_dir}")

        _remove_current_managed(install_dir)
        _install_staged(staging_dir, install_dir)

        # This should be impossible because settings.ini is not managed, but
        # keep an explicit guard so a future edit cannot silently delete it.
        if settings_existed and not (install_dir / "settings.ini").exists():
            raise RuntimeError("settings.ini disappeared during update.")

        installed_version = (install_dir / "version.txt").read_text(
            encoding="utf-8-sig"
        ).strip()
        if installed_version != version:
            raise RuntimeError(
                f"Installed version check failed: expected {version!r}, "
                f"got {installed_version!r}."
            )

        _write_log(log_path, f"Update to {version} installed successfully")

    except Exception:
        error = traceback.format_exc()
        _write_log(log_path, "Update failed; starting rollback")
        _write_log(log_path, error)

        try:
            _restore_backup(backup_dir, install_dir)
            _write_log(log_path, "Rollback completed")
        except Exception:
            _write_log(log_path, "ROLLBACK FAILED")
            _write_log(log_path, traceback.format_exc())

        raise

    finally:
        # Downloads and staging are no longer needed. If Windows happens to
        # keep this running script open, ignore cleanup failure; next launch
        # removes stale staging automatically.
        shutil.rmtree(update_dir / "download", ignore_errors=True)

    _restart(python_exe, entrypoint, install_dir)
    _write_log(log_path, "Restart requested")


def main() -> int:
    parser = argparse.ArgumentParser(description="VaPuClaimer update helper")
    parser.add_argument("--parent-pid", type=int, required=True)
    parser.add_argument("--install-dir", type=Path, required=True)
    parser.add_argument("--staging-dir", type=Path, required=True)
    parser.add_argument("--python-exe", type=Path, required=True)
    parser.add_argument("--entrypoint", type=Path, required=True)
    parser.add_argument("--version", required=True)
    args = parser.parse_args()

    try:
        apply_update(
            parent_pid=args.parent_pid,
            install_dir=args.install_dir,
            staging_dir=args.staging_dir,
            python_exe=args.python_exe,
            entrypoint=args.entrypoint,
            version=args.version,
        )
    except Exception:
        # The GUI is already closed at this point. The details remain in
        # .update/update.log for diagnosis.
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
