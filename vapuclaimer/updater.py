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
from tkinter import messagebox
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
import zipfile

REPOSITORY = "toatz/VaPuClaimerPy"
LATEST_RELEASE_API = f"https://api.github.com/repos/{REPOSITORY}/releases/latest"
USER_AGENT = "VaPuClaimer-Updater/2.0"
UPDATE_DIR_NAME = ".update"
ENTRYPOINT_NAME = "VaPuClaimer.pyw"
MODE_PYTHON = "python"
MODE_EXE = "exe"

_VERSION_RE = re.compile(
    r"^v?(?P<major>\d+)\.(?P<minor>\d+)\.(?P<patch>\d+)"
    r"(?:-(?P<pre>[0-9A-Za-z.-]+))?$"
)
_SHA256_RE = re.compile(r"^[0-9a-fA-F]{64}$")


@dataclass(frozen=True)
class ReleaseInfo:
    tag: str
    mode: str
    asset_name: str
    asset_url: str
    sha256_name: str
    sha256_url: str
    html_url: str = ""


@dataclass(frozen=True)
class PreparedUpdate:
    release: ReleaseInfo
    payload_path: Path
    staging_dir: Path | None = None
    helper_script: Path | None = None


class UpdateError(RuntimeError):
    pass


def running_mode() -> str:
    return MODE_EXE if bool(getattr(sys, "frozen", False)) else MODE_PYTHON


def expected_asset_names(tag: str, mode: str) -> tuple[str, str]:
    if mode == MODE_EXE:
        asset = "VaPuClaimer.exe"
    elif mode == MODE_PYTHON:
        asset = f"VaPuClaimer-Python-{tag}.zip"
    else:
        raise ValueError(f"Unknown updater mode: {mode!r}")
    return asset, f"{asset}.sha256"


def _parse_version(value: str) -> tuple[int, int, int, tuple]:
    value = value.strip()
    match = _VERSION_RE.match(value)
    if not match:
        raise ValueError(f"Unsupported version format: {value!r}")

    major = int(match.group("major"))
    minor = int(match.group("minor"))
    patch = int(match.group("patch"))
    pre = match.group("pre")

    if pre is None:
        pre_key = (1,)
    else:
        parts = []
        for part in pre.split("."):
            parts.append((0, int(part)) if part.isdigit() else (1, part.lower()))
        pre_key = (0, tuple(parts))

    return major, minor, patch, pre_key


def is_newer_version(candidate: str, current: str) -> bool:
    try:
        return _parse_version(candidate) > _parse_version(current)
    except ValueError:
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


def fetch_latest_release(*, mode: str | None = None, timeout: float = 8.0) -> ReleaseInfo:
    mode = mode or running_mode()
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

    expected_asset, expected_sha = expected_asset_names(tag, mode)
    by_name = {
        str(asset.get("name", "")): asset
        for asset in assets
        if isinstance(asset, dict) and asset.get("name")
    }

    release_asset = by_name.get(expected_asset)
    sha_asset = by_name.get(expected_sha)
    if not release_asset or not sha_asset:
        raise UpdateError(
            "Release is missing the expected updater assets:\n"
            f"  {expected_asset}\n"
            f"  {expected_sha}"
        )

    asset_url = str(release_asset.get("browser_download_url", ""))
    sha_url = str(sha_asset.get("browser_download_url", ""))
    if not asset_url or not sha_url:
        raise UpdateError("Release assets are missing download URLs.")

    return ReleaseInfo(
        tag=tag,
        mode=mode,
        asset_name=expected_asset,
        asset_url=asset_url,
        sha256_name=expected_sha,
        sha256_url=sha_url,
        html_url=str(payload.get("html_url", "")),
    )


def check_for_update(current_version: str, *, mode: str | None = None) -> ReleaseInfo | None:
    release = fetch_latest_release(mode=mode)
    return release if is_newer_version(release.tag, current_version) else None


def _parse_checksum(text: str, expected_filename: str) -> str:
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


def _download_file(url: str, destination: Path, *, timeout: float = 30.0) -> str:
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
    return stat.S_ISLNK((info.external_attr >> 16) & 0xFFFF)


def _safe_member_path(name: str) -> PurePosixPath:
    normalized = name.replace("\\", "/")
    path = PurePosixPath(normalized)
    if (
        not normalized
        or normalized.startswith("/")
        or path.is_absolute()
        or ".." in path.parts
        or (path.parts and ":" in path.parts[0])
    ):
        raise UpdateError(f"Unsafe path in update ZIP: {name!r}")
    return path


def safe_extract_zip(zip_path: Path, destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    root = destination.resolve()

    with zipfile.ZipFile(zip_path, "r") as archive:
        for info in archive.infolist():
            member = _safe_member_path(info.filename)
            if _is_zip_symlink(info):
                raise UpdateError(f"Symlinks are not allowed in update ZIP: {info.filename!r}")

            target = (destination / Path(*member.parts)).resolve()
            try:
                target.relative_to(root)
            except ValueError as exc:
                raise UpdateError(f"ZIP entry escapes staging directory: {info.filename!r}") from exc

            if info.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue

            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(info, "r") as source, target.open("wb") as out:
                shutil.copyfileobj(source, out)


def _validate_python_staging(staging_dir: Path, release: ReleaseInfo) -> None:
    required = (
        staging_dir / ENTRYPOINT_NAME,
        staging_dir / "version.txt",
        staging_dir / "vapuclaimer",
        staging_dir / "vapuclaimer" / "apply_update.py",
    )
    missing = [str(path.relative_to(staging_dir)) for path in required if not path.exists()]
    if missing:
        raise UpdateError(
            "Downloaded Python release is incomplete. Missing:\n  "
            + "\n  ".join(missing)
        )

    packaged_version = (staging_dir / "version.txt").read_text(
        encoding="utf-8-sig"
    ).strip()
    if packaged_version != release.tag:
        raise UpdateError(
            f"Release tag is {release.tag!r}, but version.txt contains {packaged_version!r}."
        )


def cleanup_stale_workspace(install_dir: Path) -> None:
    update_dir = install_dir / UPDATE_DIR_NAME
    for name in ("download", "staging"):
        path = update_dir / name
        if path.exists():
            shutil.rmtree(path, ignore_errors=True)
    (update_dir / "apply_exe_update.ps1").unlink(missing_ok=True)


def prepare_update(release: ReleaseInfo, install_dir: Path) -> PreparedUpdate:
    install_dir = install_dir.resolve()
    update_dir = install_dir / UPDATE_DIR_NAME
    download_dir = update_dir / "download"

    shutil.rmtree(download_dir, ignore_errors=True)
    download_dir.mkdir(parents=True, exist_ok=True)

    checksum_raw = _request_bytes(
        release.sha256_url,
        timeout=10.0,
        max_bytes=64 * 1024,
    )
    try:
        checksum_text = checksum_raw.decode("ascii")
    except UnicodeDecodeError as exc:
        raise UpdateError("SHA256 file is not valid ASCII.") from exc

    expected_hash = _parse_checksum(checksum_text, release.asset_name)
    payload_path = download_dir / release.asset_name
    actual_hash = _download_file(release.asset_url, payload_path)

    if actual_hash != expected_hash:
        payload_path.unlink(missing_ok=True)
        raise UpdateError(
            "Downloaded update failed SHA256 verification.\n\n"
            f"Expected: {expected_hash}\n"
            f"Actual:   {actual_hash}"
        )

    if release.mode == MODE_EXE:
        if payload_path.suffix.lower() != ".exe":
            raise UpdateError("Windows update asset is not an EXE.")
        return PreparedUpdate(release=release, payload_path=payload_path)

    staging_root = update_dir / "staging"
    staging_dir = staging_root / release.tag
    shutil.rmtree(staging_root, ignore_errors=True)
    staging_dir.mkdir(parents=True, exist_ok=True)

    safe_extract_zip(payload_path, staging_dir)
    _validate_python_staging(staging_dir, release)

    return PreparedUpdate(
        release=release,
        payload_path=payload_path,
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


def _launch_python_update(prepared: PreparedUpdate, install_dir: Path) -> None:
    if prepared.helper_script is None or prepared.staging_dir is None:
        raise UpdateError("Python update was not staged correctly.")

    python_exe = _pythonw_executable()
    entrypoint = install_dir.resolve() / ENTRYPOINT_NAME
    args = [
        str(python_exe),
        str(prepared.helper_script),
        "--parent-pid", str(os.getpid()),
        "--install-dir", str(install_dir.resolve()),
        "--staging-dir", str(prepared.staging_dir.resolve()),
        "--python-exe", str(python_exe),
        "--entrypoint", str(entrypoint),
        "--version", prepared.release.tag,
    ]
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0

    try:
        subprocess.Popen(
            args,
            cwd=str(install_dir.resolve()),
            close_fds=True,
            creationflags=creationflags,
        )
    except OSError as exc:
        raise UpdateError(f"Could not start Python update helper: {exc}") from exc


_EXE_UPDATE_SCRIPT = r'''param(
    [Parameter(Mandatory=$true)][int]$ParentPid,
    [Parameter(Mandatory=$true)][string]$CurrentExe,
    [Parameter(Mandatory=$true)][string]$TargetExe,
    [Parameter(Mandatory=$true)][string]$NewExe,
    [Parameter(Mandatory=$true)][string]$LogPath
)

$ErrorActionPreference = "Stop"

function Log([string]$Message) {
    $Stamp = Get-Date -Format "yyyy-MM-ddTHH:mm:ss"
    Add-Content -LiteralPath $LogPath -Value "[$Stamp] $Message" -Encoding UTF8
}

Get-ChildItem Env: |
    Where-Object { $_.Name -like "_PYI_*" } |
    ForEach-Object { Remove-Item ("Env:" + $_.Name) -ErrorAction SilentlyContinue }

$env:PYINSTALLER_RESET_ENVIRONMENT = "1"

$Backup = "$TargetExe.old"
$OldVersionedExe = $null

try {
    Log "Waiting for VaPuClaimer PID $ParentPid"
    Wait-Process -Id $ParentPid -ErrorAction SilentlyContinue
    Start-Sleep -Milliseconds 700

    if (-not (Test-Path -LiteralPath $NewExe)) {
        throw "Downloaded EXE is missing: $NewExe"
    }

    if (
        $CurrentExe -ne $TargetExe -and
        (Test-Path -LiteralPath $CurrentExe)
    ) {
        $OldVersionedExe = $CurrentExe
        Log "Migrating versioned EXE name to stable VaPuClaimer.exe"
    }

    if (Test-Path -LiteralPath $Backup) {
        Remove-Item -LiteralPath $Backup -Force
    }

    if (Test-Path -LiteralPath $TargetExe) {
        Log "Backing up existing stable EXE"
        Move-Item -LiteralPath $TargetExe -Destination $Backup -Force
    }
    elseif ($OldVersionedExe) {
        Log "Backing up old versioned EXE"
        Copy-Item -LiteralPath $OldVersionedExe -Destination $Backup -Force
    }

    try {
        Log "Installing new VaPuClaimer.exe"
        Copy-Item -LiteralPath $NewExe -Destination $TargetExe -Force

        if (-not (Test-Path -LiteralPath $TargetExe)) {
            throw "New VaPuClaimer.exe was not installed."
        }

        Log "Restarting updated VaPuClaimer"
        $Process = Start-Process `
            -FilePath $TargetExe `
            -WorkingDirectory (Split-Path -Parent $TargetExe) `
            -PassThru

        Start-Sleep -Seconds 3

        if ($Process.HasExited) {
            throw "Updated VaPuClaimer exited immediately with code $($Process.ExitCode)."
        }

        Log "Updated EXE started successfully"

        if (
            $OldVersionedExe -and
            (Test-Path -LiteralPath $OldVersionedExe)
        ) {
            Remove-Item -LiteralPath $OldVersionedExe -Force -ErrorAction SilentlyContinue
            Log "Removed old versioned EXE"
        }

        Remove-Item -LiteralPath $Backup -Force -ErrorAction SilentlyContinue
    }
    catch {
        Log "Install/restart failed: $($_.Exception.Message)"
        Remove-Item -LiteralPath $TargetExe -Force -ErrorAction SilentlyContinue

        if (Test-Path -LiteralPath $Backup) {
            if ($OldVersionedExe) {
                Copy-Item -LiteralPath $Backup -Destination $OldVersionedExe -Force
                Log "Rollback restored old versioned EXE"
                Start-Process `
                    -FilePath $OldVersionedExe `
                    -WorkingDirectory (Split-Path -Parent $OldVersionedExe)
            }
            else {
                Move-Item -LiteralPath $Backup -Destination $TargetExe -Force
                Log "Rollback restored previous stable EXE"
                Start-Process `
                    -FilePath $TargetExe `
                    -WorkingDirectory (Split-Path -Parent $TargetExe)
            }
        }

        throw
    }
}
catch {
    Log "EXE update failed: $($_.Exception.Message)"
    exit 1
}
'''


def _clean_pyinstaller_environment() -> dict[str, str]:
    env = os.environ.copy()
    for key in list(env):
        if key.startswith("_PYI_"):
            env.pop(key, None)
    env["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
    return env


def _launch_exe_update(prepared: PreparedUpdate, install_dir: Path) -> None:
    if running_mode() != MODE_EXE:
        raise UpdateError("EXE updater was requested from a non-frozen Python process.")

    install_dir = install_dir.resolve()
    current_exe = Path(sys.executable).resolve()
    target_exe = install_dir / "VaPuClaimer.exe"

    update_dir = install_dir / UPDATE_DIR_NAME
    update_dir.mkdir(parents=True, exist_ok=True)

    helper = update_dir / "apply_exe_update.ps1"
    log_path = update_dir / "update-exe.log"
    helper.write_text(_EXE_UPDATE_SCRIPT, encoding="utf-8-sig")

    powershell = shutil.which("powershell.exe") or shutil.which("powershell")
    if not powershell:
        raise UpdateError("Windows PowerShell was not found.")

    args = [
        powershell,
        "-NoLogo",
        "-NoProfile",
        "-NonInteractive",
        "-ExecutionPolicy", "Bypass",
        "-File", str(helper),
        "-ParentPid", str(os.getpid()),
        "-CurrentExe", str(current_exe),
        "-TargetExe", str(target_exe),
        "-NewExe", str(prepared.payload_path.resolve()),
        "-LogPath", str(log_path.resolve()),
    ]

    try:
        subprocess.Popen(
            args,
            cwd=str(install_dir),
            close_fds=True,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            env=_clean_pyinstaller_environment(),
        )
    except OSError as exc:
        raise UpdateError(f"Could not start EXE update helper: {exc}") from exc


def launch_apply_helper(prepared: PreparedUpdate, install_dir: Path) -> None:
    if prepared.release.mode == MODE_EXE:
        _launch_exe_update(prepared, install_dir)
    else:
        _launch_python_update(prepared, install_dir)


class AutoUpdater:
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
        self.mode = running_mode()

        self.events: queue.Queue[tuple[str, object]] = queue.Queue()
        self.busy = False
        self.pending_release: ReleaseInfo | None = None

        cleanup_stale_workspace(self.install_dir)
        self.app.update_in_progress = False

        self.root.after(250, self._poll_events)
        self.root.after(self.initial_delay_ms, self.check_async)

    def check_async(self) -> None:
        if self.busy:
            return
        self.busy = True

        def worker() -> None:
            try:
                release = check_for_update(self.current_version, mode=self.mode)
                self.events.put(("check_done", release))
            except Exception as exc:
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
        if self.app.claiming:
            self.pending_release = release
            self.root.after(1000, self._prompt_pending_when_idle)
            return

        package_name = "standalone Windows EXE" if release.mode == MODE_EXE else "Python ZIP"
        answer = messagebox.askyesno(
            "VaPuClaimer update",
            "A new VaPuClaimer version is available.\n\n"
            f"Installed: {self.current_version}\n"
            f"Latest:     {release.tag}\n"
            f"Package:    {package_name}\n\n"
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
                    self.busy = False

                elif kind == "prepare_error":
                    self._restore_idle_ui()
                    messagebox.showerror(
                        "VaPuClaimer update",
                        f"The update could not be prepared.\n\n{payload}",
                    )

                elif kind == "prepared":
                    if not isinstance(payload, PreparedUpdate):
                        continue
                    try:
                        self.app.status_var.set(f"RESTARTING  {payload.release.tag}")
                        launch_apply_helper(payload, self.install_dir)
                    except Exception as exc:
                        self._restore_idle_ui()
                        messagebox.showerror(
                            "VaPuClaimer update",
                            f"Could not start the update helper.\n\n{exc}",
                        )
                        continue

                    self.app.close()
                    return

        except queue.Empty:
            pass

        try:
            if self.root.winfo_exists():
                self.root.after(250, self._poll_events)
        except Exception:
            pass
