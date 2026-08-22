from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import queue
import re
import shutil
import stat
import subprocess
import sys
import threading
import time
from tkinter import messagebox
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
import zipfile


REPOSITORY = "toatz/VaPuClaimerPy"
LATEST_RELEASE_API = f"https://api.github.com/repos/{REPOSITORY}/releases/latest"
USER_AGENT = "VaPuClaimer-Updater/1.0"

UPDATE_DIR_NAME = ".update"
ENTRYPOINT_NAME = "VaPuClaimer.pyw"

# settings.ini is deliberately NOT in this list.
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

_VERSION_RE = re.compile(
    r"^v?(?P<major>\d+)\.(?P<minor>\d+)\.(?P<patch>\d+)"
    r"(?:-(?P<pre>[0-9A-Za-z.-]+))?$"
)
_SHA256_RE = re.compile(r"^[0-9a-fA-F]{64}$")


@dataclass(frozen=True)
class ReleaseInfo:
    tag: str
    zip_name: str
    zip_url: str
    sha256_name: str
    sha256_url: str
    html_url: str = ""


@dataclass(frozen=True)
class PreparedUpdate:
    release: ReleaseInfo
    staging_dir: Path
    helper_script: Path


class UpdateError(RuntimeError):
    pass


def _parse_version(value: str) -> tuple[int, int, int, tuple]:
    """Parse the release format used by VaPuClaimer.

    Stable releases sort above prereleases with the same numeric version.
    """
    value = value.strip()
    match = _VERSION_RE.match(value)
    if not match:
        raise ValueError(f"Unsupported version format: {value!r}")

    major = int(match.group("major"))
    minor = int(match.group("minor"))
    patch = int(match.group("patch"))
    pre = match.group("pre")

    if pre is None:
        # Stable > prerelease.
        pre_key = (1,)
    else:
        parts = []
        for part in pre.split("."):
            if part.isdigit():
                parts.append((0, int(part)))
            else:
                parts.append((1, part.lower()))
        pre_key = (0, tuple(parts))

    return major, minor, patch, pre_key


def is_newer_version(candidate: str, current: str) -> bool:
    try:
        return _parse_version(candidate) > _parse_version(current)
    except ValueError:
        # Development/local versions such as v0.0.0-dev should still be able
        # to update to a normal release. Unknown remote formats are rejected.
        try:
            remote = _parse_version(candidate)
        except ValueError:
            return False

        try:
            local = _parse_version(current)
        except ValueError:
            return True

        return remote > local


def _request_bytes(url: str, *, timeout: float = 10.0, max_bytes: int | None = None) -> bytes:
    request = Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "application/vnd.github+json",
        },
    )

    try:
        with urlopen(request, timeout=timeout) as response:
            data = response.read(max_bytes + 1 if max_bytes else -1)
    except (HTTPError, URLError, TimeoutError, OSError) as exc:
        raise UpdateError(f"Network request failed: {exc}") from exc

    if max_bytes is not None and len(data) > max_bytes:
        raise UpdateError(f"Response from {url} was unexpectedly large.")

    return data


def fetch_latest_release(*, timeout: float = 8.0) -> ReleaseInfo:
    raw = _request_bytes(LATEST_RELEASE_API, timeout=timeout, max_bytes=2_000_000)

    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise UpdateError("GitHub returned an invalid release response.") from exc

    tag = str(payload.get("tag_name", "")).strip()
    if not tag:
        raise UpdateError("Latest GitHub release has no tag.")

    assets = payload.get("assets")
    if not isinstance(assets, list):
        raise UpdateError("Latest GitHub release has no asset list.")

    expected_zip = f"VaPuClaimer-Python-{tag}.zip"
    expected_sha = f"{expected_zip}.sha256"

    by_name: dict[str, dict] = {}
    for asset in assets:
        if not isinstance(asset, dict):
            continue
        name = str(asset.get("name", ""))
        if name:
            by_name[name] = asset

    zip_asset = by_name.get(expected_zip)
    sha_asset = by_name.get(expected_sha)

    if not zip_asset or not sha_asset:
        raise UpdateError(
            "Release is missing the expected updater assets:\n"
            f"  {expected_zip}\n"
            f"  {expected_sha}"
        )

    zip_url = str(zip_asset.get("browser_download_url", ""))
    sha_url = str(sha_asset.get("browser_download_url", ""))

    if not zip_url or not sha_url:
        raise UpdateError("Release assets are missing download URLs.")

    return ReleaseInfo(
        tag=tag,
        zip_name=expected_zip,
        zip_url=zip_url,
        sha256_name=expected_sha,
        sha256_url=sha_url,
        html_url=str(payload.get("html_url", "")),
    )


def check_for_update(current_version: str) -> ReleaseInfo | None:
    release = fetch_latest_release()
    return release if is_newer_version(release.tag, current_version) else None


def _parse_checksum(text: str, expected_filename: str) -> str:
    # Expected release format:
    # <64 hex chars>  VaPuClaimer-Python-vX.Y.Z.zip
    first_line = next((line.strip() for line in text.splitlines() if line.strip()), "")
    if not first_line:
        raise UpdateError("SHA256 file is empty.")

    parts = first_line.split()
    if not parts or not _SHA256_RE.fullmatch(parts[0]):
        raise UpdateError("SHA256 file has an invalid checksum.")

    if len(parts) >= 2:
        filename = parts[-1].lstrip("*")
        if Path(filename).name != expected_filename:
            raise UpdateError(
                f"SHA256 file names {filename!r}, expected {expected_filename!r}."
            )

    return parts[0].lower()


def _download_file(url: str, destination: Path, *, timeout: float = 20.0) -> str:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temp = destination.with_suffix(destination.suffix + ".part")
    temp.unlink(missing_ok=True)

    request = Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "application/octet-stream",
        },
    )

    digest = hashlib.sha256()

    try:
        with urlopen(request, timeout=timeout) as response, temp.open("wb") as out:
            while True:
                chunk = response.read(1024 * 256)
                if not chunk:
                    break
                digest.update(chunk)
                out.write(chunk)
    except (HTTPError, URLError, TimeoutError, OSError) as exc:
        temp.unlink(missing_ok=True)
        raise UpdateError(f"Could not download {destination.name}: {exc}") from exc

    temp.replace(destination)
    return digest.hexdigest().lower()


def _is_zip_symlink(info: zipfile.ZipInfo) -> bool:
    mode = (info.external_attr >> 16) & 0xFFFF
    return stat.S_ISLNK(mode)


def _safe_member_path(name: str) -> PurePosixPath:
    # ZIP paths are always slash-separated, even on Windows.
    normalized = name.replace("\\", "/")
    path = PurePosixPath(normalized)

    if not normalized or normalized.startswith("/"):
        raise UpdateError(f"Unsafe path in update ZIP: {name!r}")

    if path.is_absolute() or ".." in path.parts:
        raise UpdateError(f"Unsafe path in update ZIP: {name!r}")

    if path.parts and ":" in path.parts[0]:
        raise UpdateError(f"Unsafe drive path in update ZIP: {name!r}")

    return path


def safe_extract_zip(zip_path: Path, destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    destination_resolved = destination.resolve()

    with zipfile.ZipFile(zip_path, "r") as archive:
        for info in archive.infolist():
            member = _safe_member_path(info.filename)

            if _is_zip_symlink(info):
                raise UpdateError(f"Symlinks are not allowed in update ZIP: {info.filename!r}")

            target = (destination / Path(*member.parts)).resolve()
            try:
                target.relative_to(destination_resolved)
            except ValueError as exc:
                raise UpdateError(f"ZIP entry escapes staging directory: {info.filename!r}") from exc

            if info.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue

            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(info, "r") as source, target.open("wb") as out:
                shutil.copyfileobj(source, out)


def _validate_staged_release(staging_dir: Path, release: ReleaseInfo) -> None:
    required = (
        staging_dir / ENTRYPOINT_NAME,
        staging_dir / "version.txt",
        staging_dir / "vapuclaimer",
        staging_dir / "vapuclaimer" / "apply_update.py",
    )

    missing = [str(path.relative_to(staging_dir)) for path in required if not path.exists()]
    if missing:
        raise UpdateError(
            "Downloaded release is incomplete. Missing:\n  " + "\n  ".join(missing)
        )

    packaged_version = (staging_dir / "version.txt").read_text(
        encoding="utf-8-sig"
    ).strip()

    if packaged_version != release.tag:
        raise UpdateError(
            f"Release tag is {release.tag!r}, but version.txt contains "
            f"{packaged_version!r}."
        )


def cleanup_stale_workspace(install_dir: Path) -> None:
    """Remove old downloads/staging but preserve the last backup and log."""
    update_dir = install_dir / UPDATE_DIR_NAME
    for name in ("download", "staging"):
        path = update_dir / name
        if path.exists():
            shutil.rmtree(path, ignore_errors=True)


def prepare_update(release: ReleaseInfo, install_dir: Path) -> PreparedUpdate:
    install_dir = install_dir.resolve()
    update_dir = install_dir / UPDATE_DIR_NAME
    download_dir = update_dir / "download"
    staging_root = update_dir / "staging"
    staging_dir = staging_root / release.tag

    shutil.rmtree(download_dir, ignore_errors=True)
    shutil.rmtree(staging_root, ignore_errors=True)
    download_dir.mkdir(parents=True, exist_ok=True)
    staging_dir.mkdir(parents=True, exist_ok=True)

    checksum_raw = _request_bytes(
        release.sha256_url,
        timeout=10.0,
        max_bytes=64 * 1024,
    )
    try:
        checksum_text = checksum_raw.decode("ascii")
    except UnicodeDecodeError as exc:
        raise UpdateError("SHA256 file is not valid ASCII.") from exc

    expected_hash = _parse_checksum(checksum_text, release.zip_name)

    zip_path = download_dir / release.zip_name
    actual_hash = _download_file(release.zip_url, zip_path)

    if actual_hash != expected_hash:
        zip_path.unlink(missing_ok=True)
        raise UpdateError(
            "Downloaded update failed SHA256 verification.\n\n"
            f"Expected: {expected_hash}\n"
            f"Actual:   {actual_hash}"
        )

    safe_extract_zip(zip_path, staging_dir)
    _validate_staged_release(staging_dir, release)

    return PreparedUpdate(
        release=release,
        staging_dir=staging_dir,
        helper_script=staging_dir / "vapuclaimer" / "apply_update.py",
    )


def _pythonw_executable() -> Path:
    current = Path(sys.executable).resolve()

    if current.name.lower() == "pythonw.exe":
        return current

    if current.name.lower() == "python.exe":
        candidate = current.with_name("pythonw.exe")
        if candidate.exists():
            return candidate

    return current


def launch_apply_helper(prepared: PreparedUpdate, install_dir: Path) -> None:
    python_exe = _pythonw_executable()
    entrypoint = install_dir.resolve() / ENTRYPOINT_NAME

    args = [
        str(python_exe),
        str(prepared.helper_script),
        "--parent-pid",
        str(os.getpid()),
        "--install-dir",
        str(install_dir.resolve()),
        "--staging-dir",
        str(prepared.staging_dir.resolve()),
        "--python-exe",
        str(python_exe),
        "--entrypoint",
        str(entrypoint),
        "--version",
        prepared.release.tag,
    ]

    creationflags = 0
    if os.name == "nt":
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)

    try:
        subprocess.Popen(
            args,
            cwd=str(install_dir.resolve()),
            close_fds=True,
            creationflags=creationflags,
        )
    except OSError as exc:
        raise UpdateError(f"Could not start update helper: {exc}") from exc


class AutoUpdater:
    """Small Tk-friendly controller that keeps network work off the UI thread."""

    def __init__(
        self,
        app,
        *,
        install_dir: Path,
        current_version: str,
        initial_delay_ms: int = 2500,
    ) -> None:
        self.app = app
        self.root = app.root
        self.install_dir = install_dir.resolve()
        self.current_version = current_version
        self.initial_delay_ms = initial_delay_ms

        self.events: queue.Queue[tuple[str, object]] = queue.Queue()
        self.busy = False
        self.pending_release: ReleaseInfo | None = None

        cleanup_stale_workspace(self.install_dir)

        # app.py checks this flag before starting a claim.
        self.app.update_in_progress = False

        self.root.after(250, self._poll_events)
        self.root.after(self.initial_delay_ms, self.check_async)

    def check_async(self) -> None:
        if self.busy:
            return

        self.busy = True

        def worker() -> None:
            try:
                release = check_for_update(self.current_version)
                self.events.put(("check_done", release))
            except Exception as exc:
                # Update checks are deliberately non-fatal.
                self.events.put(("check_error", exc))

        threading.Thread(target=worker, name="VaPuUpdateCheck", daemon=True).start()

    def _download_async(self, release: ReleaseInfo) -> None:
        self.busy = True
        self.app.update_in_progress = True
        self.app.pending_button_start = False

        if not self.app.claiming:
            self.app._set_controls_enabled(False)
            self.app.status_var.set(f"UPDATING  {release.tag}")
            self.app.status_label.config(fg=self.app.CYAN)

        def worker() -> None:
            try:
                prepared = prepare_update(release, self.install_dir)
                self.events.put(("prepared", prepared))
            except Exception as exc:
                self.events.put(("prepare_error", exc))

        threading.Thread(target=worker, name="VaPuUpdateDownload", daemon=True).start()

    def _prompt_release(self, release: ReleaseInfo) -> None:
        # Never interrupt an active claim.
        if self.app.claiming:
            self.pending_release = release
            self.root.after(1000, self._prompt_pending_when_idle)
            return

        answer = messagebox.askyesno(
            "VaPuClaimer update",
            "A new VaPuClaimer version is available.\n\n"
            f"Installed: {self.current_version}\n"
            f"Latest:     {release.tag}\n\n"
            "Download, verify and install it now?\n\n"
            "Your settings.ini will be preserved.",
        )

        if answer:
            self._download_async(release)

    def _prompt_pending_when_idle(self) -> None:
        if self.pending_release is None:
            return

        if self.app.claiming:
            self.root.after(1000, self._prompt_pending_when_idle)
            return

        release = self.pending_release
        self.pending_release = None
        self._prompt_release(release)

    def _restore_idle_ui(self) -> None:
        self.app.update_in_progress = False
        self.busy = False

        if not self.app.claiming:
            self.app._set_controls_enabled(True)
            self.app.status_var.set("IDLE")
            self.app.status_label.config(fg=self.app.CYAN)

    def _poll_events(self) -> None:
        try:
            while True:
                kind, payload = self.events.get_nowait()

                if kind == "check_done":
                    self.busy = False
                    if isinstance(payload, ReleaseInfo):
                        self._prompt_release(payload)

                elif kind == "check_error":
                    # Silent by design: no internet should never stop VaPuClaimer.
                    self.busy = False

                elif kind == "prepare_error":
                    self._restore_idle_ui()
                    messagebox.showerror(
                        "VaPuClaimer update",
                        f"The update could not be installed.\n\n{payload}",
                    )

                elif kind == "prepared":
                    prepared = payload
                    if not isinstance(prepared, PreparedUpdate):
                        continue

                    try:
                        self.app.status_var.set(f"RESTARTING  {prepared.release.tag}")
                        launch_apply_helper(prepared, self.install_dir)
                    except Exception as exc:
                        self._restore_idle_ui()
                        messagebox.showerror(
                            "VaPuClaimer update",
                            f"Could not start the update helper.\n\n{exc}",
                        )
                        continue

                    # The helper waits for this process to exit before touching files.
                    self.app.close()
                    return

        except queue.Empty:
            pass

        try:
            if self.root.winfo_exists():
                self.root.after(250, self._poll_events)
        except Exception:
            pass
