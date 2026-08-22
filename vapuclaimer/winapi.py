from __future__ import annotations

import ctypes
from ctypes import wintypes
import os
import queue
import threading
import time
from dataclasses import dataclass

if os.name != "nt":
    raise RuntimeError("VaPuClaimer requires Windows.")

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

# Virtual keys
VK_CONTROL = 0x11
VK_RETURN = 0x0D
VK_DELETE = 0x2E
VK_INSERT = 0x2D
VK_HOME = 0x24
VK_END = 0x23
VK_PRIOR = 0x21
VK_NEXT = 0x22
VK_LEFT = 0x25
VK_UP = 0x26
VK_RIGHT = 0x27
VK_DOWN = 0x28
VK_DIVIDE = 0x6F
VK_NUMLOCK = 0x90
VK_F1 = 0x70
VK_NUMPAD0 = 0x60

# Input flags
INPUT_MOUSE = 0
INPUT_KEYBOARD = 1
INPUT_HARDWARE = 2
KEYEVENTF_EXTENDEDKEY = 0x0001
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_SCANCODE = 0x0008
MAPVK_VK_TO_VSC = 0

# Hotkeys / messages
WM_HOTKEY = 0x0312
PM_REMOVE = 0x0001
MOD_CONTROL = 0x0002
MOD_NOREPEAT = 0x4000

# Clipboard
CF_UNICODETEXT = 13
GMEM_MOVEABLE = 0x0002

# Window styles. A tk window with overrideredirect(True) is a plain popup, and
# Windows gives popups no taskbar button and no Alt+Tab entry, so the app has to
# ask for one itself.
GWL_EXSTYLE = -20
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_APPWINDOW = 0x00040000
SW_MINIMIZE = 6
SW_RESTORE = 9


ULONG_PTR = ctypes.c_size_t


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", wintypes.LONG),
        ("dy", wintypes.LONG),
        ("mouseData", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", wintypes.WORD),
        ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


class HARDWAREINPUT(ctypes.Structure):
    _fields_ = [
        ("uMsg", wintypes.DWORD),
        ("wParamL", wintypes.WORD),
        ("wParamH", wintypes.WORD),
    ]


class INPUT_UNION(ctypes.Union):
    # INPUT is a union of mouse, keyboard and hardware input. Even though
    # VaPuClaimer only sends keyboard input, all three members must exist so
    # ctypes.sizeof(INPUT) matches Win32's real INPUT structure.
    _fields_ = [
        ("mi", MOUSEINPUT),
        ("ki", KEYBDINPUT),
        ("hi", HARDWAREINPUT),
    ]


class INPUT(ctypes.Structure):
    _anonymous_ = ("u",)
    _fields_ = [
        ("type", wintypes.DWORD),
        ("u", INPUT_UNION),
    ]


@dataclass(frozen=True)
class ConsoleKeyPress:
    scan: int = 0x29
    extended: bool = False


@dataclass(frozen=True)
class ConsoleKeyOption:
    id: str
    name: str
    vk: int = 0
    raw_scan: int = 0


HOTKEY_OPTIONS = [
    *[(f"F{i}", VK_F1 + i - 1) for i in range(1, 13)],
    ("Insert", VK_INSERT),
    ("Home", VK_HOME),
    ("End", VK_END),
    ("PageUp", VK_PRIOR),
    ("PageDown", VK_NEXT),
    ("Delete", VK_DELETE),
    *[(f"Num {i}", VK_NUMPAD0 + i) for i in range(10)],
]
HOTKEY_VK = dict(HOTKEY_OPTIONS)

CONSOLE_KEYS = [
    ConsoleKeyOption("Tilde", "§ / ~  (left of 1)", raw_scan=0x29),
    ConsoleKeyOption("Insert", "Insert", VK_INSERT),
    ConsoleKeyOption("Home", "Home", VK_HOME),
    ConsoleKeyOption("End", "End", VK_END),
    ConsoleKeyOption("PageUp", "PageUp", VK_PRIOR),
    ConsoleKeyOption("PageDown", "PageDown", VK_NEXT),
    ConsoleKeyOption("Delete", "Delete", VK_DELETE),
    *[ConsoleKeyOption(f"F{i}", f"F{i}", VK_F1 + i - 1) for i in range(1, 13)],
    *[ConsoleKeyOption(f"Num{i}", f"Num {i}", VK_NUMPAD0 + i) for i in range(10)],
]
CONSOLE_BY_ID = {x.id: x for x in CONSOLE_KEYS}


# Function signatures
user32.SendInput.argtypes = [wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int]
user32.SendInput.restype = wintypes.UINT
user32.MapVirtualKeyExW.argtypes = [wintypes.UINT, wintypes.UINT, wintypes.HKL]
user32.MapVirtualKeyExW.restype = wintypes.UINT
user32.GetKeyboardLayout.argtypes = [wintypes.DWORD]
user32.GetKeyboardLayout.restype = wintypes.HKL
user32.GetForegroundWindow.restype = wintypes.HWND
user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
user32.GetWindowThreadProcessId.restype = wintypes.DWORD
user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
user32.GetClassNameW.restype = ctypes.c_int
user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
user32.GetWindowTextW.restype = ctypes.c_int
user32.RegisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT]
user32.RegisterHotKey.restype = wintypes.BOOL
user32.UnregisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int]
user32.UnregisterHotKey.restype = wintypes.BOOL
user32.PeekMessageW.argtypes = [
    ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT, wintypes.UINT
]
user32.PeekMessageW.restype = wintypes.BOOL
user32.OpenClipboard.argtypes = [wintypes.HWND]
user32.OpenClipboard.restype = wintypes.BOOL
user32.CloseClipboard.restype = wintypes.BOOL
user32.EmptyClipboard.restype = wintypes.BOOL
user32.IsClipboardFormatAvailable.argtypes = [wintypes.UINT]
user32.IsClipboardFormatAvailable.restype = wintypes.BOOL
user32.GetClipboardData.argtypes = [wintypes.UINT]
user32.GetClipboardData.restype = wintypes.HANDLE
user32.SetClipboardData.argtypes = [wintypes.UINT, wintypes.HANDLE]
user32.SetClipboardData.restype = wintypes.HANDLE
user32.GetClipboardSequenceNumber.restype = wintypes.DWORD

user32.GetParent.argtypes = [wintypes.HWND]
user32.GetParent.restype = wintypes.HWND
user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
user32.ShowWindow.restype = wintypes.BOOL

# GetWindowLongPtrW only exists in the 64-bit user32; the 32-bit build keeps the
# old name. Both return the same thing for GWL_EXSTYLE.
_get_window_long = getattr(user32, "GetWindowLongPtrW", None) or user32.GetWindowLongW
_set_window_long = getattr(user32, "SetWindowLongPtrW", None) or user32.SetWindowLongW
_get_window_long.argtypes = [wintypes.HWND, ctypes.c_int]
_get_window_long.restype = ctypes.c_ssize_t
_set_window_long.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t]
_set_window_long.restype = ctypes.c_ssize_t

kernel32.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
kernel32.GlobalAlloc.restype = wintypes.HGLOBAL
kernel32.GlobalLock.argtypes = [wintypes.HGLOBAL]
kernel32.GlobalLock.restype = wintypes.LPVOID
kernel32.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
kernel32.GlobalUnlock.restype = wintypes.BOOL
kernel32.GlobalFree.argtypes = [wintypes.HGLOBAL]
kernel32.GlobalFree.restype = wintypes.HGLOBAL


def _input_vk(vk: int, key_up: bool = False) -> INPUT:
    return INPUT(
        type=INPUT_KEYBOARD,
        ki=KEYBDINPUT(
            wVk=vk,
            wScan=0,
            dwFlags=KEYEVENTF_KEYUP if key_up else 0,
            time=0,
            dwExtraInfo=0,
        ),
    )


def _input_scan(scan: int, key_up: bool = False, extended: bool = False) -> INPUT:
    flags = KEYEVENTF_SCANCODE
    if key_up:
        flags |= KEYEVENTF_KEYUP
    if extended:
        flags |= KEYEVENTF_EXTENDEDKEY
    return INPUT(
        type=INPUT_KEYBOARD,
        ki=KEYBDINPUT(
            wVk=0,
            wScan=scan,
            dwFlags=flags,
            time=0,
            dwExtraInfo=0,
        ),
    )


def _send(*items: INPUT) -> None:
    if not items:
        return

    arr = (INPUT * len(items))(*items)
    sent = user32.SendInput(len(items), arr, ctypes.sizeof(INPUT))

    if sent != len(items):
        error = ctypes.get_last_error()
        if error:
            raise ctypes.WinError(error)
        raise OSError(
            f"SendInput only sent {sent} of {len(items)} input events "
            f"(INPUT size={ctypes.sizeof(INPUT)})."
        )


def tap_vk(vk: int) -> None:
    _send(_input_vk(vk), _input_vk(vk, True))


def send_scan(scan: int, key_up: bool = False, extended: bool = False) -> None:
    _send(_input_scan(scan, key_up, extended))


def is_extended_vk(vk: int) -> bool:
    return vk in {
        VK_INSERT, VK_DELETE, VK_HOME, VK_END, VK_PRIOR, VK_NEXT,
        VK_LEFT, VK_RIGHT, VK_UP, VK_DOWN, VK_DIVIDE, VK_NUMLOCK,
    }


def resolve_console_key(option_id: str) -> ConsoleKeyPress:
    option = CONSOLE_BY_ID.get(option_id, CONSOLE_BY_ID["Tilde"])
    if option.vk == 0:
        return ConsoleKeyPress(option.raw_scan, False)

    layout = user32.GetKeyboardLayout(0)
    scan = user32.MapVirtualKeyExW(option.vk, MAPVK_VK_TO_VSC, layout)
    if not scan:
        return ConsoleKeyPress()
    return ConsoleKeyPress(scan, is_extended_vk(option.vk))


def tap_console_key(key: ConsoleKeyPress) -> None:
    _send(
        _input_scan(key.scan, False, key.extended),
        _input_scan(key.scan, True, key.extended),
    )


def tap_ctrl_v() -> None:
    layout = user32.GetKeyboardLayout(0)
    ctrl_scan = user32.MapVirtualKeyExW(VK_CONTROL, MAPVK_VK_TO_VSC, layout)
    v_scan = user32.MapVirtualKeyExW(ord("V"), MAPVK_VK_TO_VSC, layout)
    if not ctrl_scan or not v_scan:
        raise OSError("Could not resolve Ctrl+V scan codes for the active keyboard layout.")

    send_scan(ctrl_scan, False)
    time.sleep(0.002)
    send_scan(v_scan, False)
    time.sleep(0.002)
    send_scan(v_scan, True)
    time.sleep(0.002)
    send_scan(ctrl_scan, True)


def toplevel_hwnd(tk_window_id: int) -> int:
    """The real top level window behind a tk window id.

    Tk hands out the handle of its own frame, which on Windows sits inside a
    wrapper window. The wrapper is the one the shell knows about, so styles have
    to be set there.
    """
    parent = user32.GetParent(tk_window_id)
    return int(parent) if parent else int(tk_window_id)


def show_in_taskbar(hwnd: int) -> bool:
    """Gives a borderless window a taskbar button and an Alt+Tab entry."""
    if not hwnd:
        return False

    style = _get_window_long(hwnd, GWL_EXSTYLE)
    wanted = (style & ~WS_EX_TOOLWINDOW) | WS_EX_APPWINDOW
    if style == wanted:
        return True

    _set_window_long(hwnd, GWL_EXSTYLE, wanted)
    return _get_window_long(hwnd, GWL_EXSTYLE) == wanted


def minimize_window(hwnd: int) -> None:
    # ShowWindow rather than tk's iconify(): iconify on an overrideredirect
    # window does not minimise reliably, and the old workaround of turning
    # overrideredirect off first flashed a native title bar and could leave the
    # window unreachable.
    if hwnd:
        user32.ShowWindow(hwnd, SW_MINIMIZE)


def foreground_info() -> tuple[int, str, str]:
    hwnd = user32.GetForegroundWindow()
    if not hwnd:
        return 0, "", ""

    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))

    cls = ctypes.create_unicode_buffer(128)
    title = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(hwnd, cls, len(cls))
    user32.GetWindowTextW(hwnd, title, len(title))
    return int(pid.value), cls.value, title.value


def clipboard_sequence() -> int:
    return int(user32.GetClipboardSequenceNumber())


def get_clipboard_text() -> tuple[bool, str]:
    if not user32.IsClipboardFormatAvailable(CF_UNICODETEXT):
        return False, ""

    for _ in range(4):
        if user32.OpenClipboard(None):
            try:
                handle = user32.GetClipboardData(CF_UNICODETEXT)
                if not handle:
                    return False, ""
                ptr = kernel32.GlobalLock(handle)
                if not ptr:
                    return False, ""
                try:
                    return True, ctypes.wstring_at(ptr)
                finally:
                    kernel32.GlobalUnlock(handle)
            finally:
                user32.CloseClipboard()
        time.sleep(0.005)
    return False, ""


def set_clipboard_text(text: str) -> bool:
    for _ in range(4):
        if user32.OpenClipboard(None):
            try:
                if not user32.EmptyClipboard():
                    return False

                buffer = ctypes.create_unicode_buffer(text)
                size = ctypes.sizeof(buffer)
                handle = kernel32.GlobalAlloc(GMEM_MOVEABLE, size)
                if not handle:
                    return False

                ptr = kernel32.GlobalLock(handle)
                if not ptr:
                    kernel32.GlobalFree(handle)
                    return False

                try:
                    ctypes.memmove(ptr, ctypes.addressof(buffer), size)
                finally:
                    kernel32.GlobalUnlock(handle)

                if not user32.SetClipboardData(CF_UNICODETEXT, handle):
                    kernel32.GlobalFree(handle)
                    return False

                # Ownership transfers to the OS on success.
                return True
            finally:
                user32.CloseClipboard()
        time.sleep(0.005)
    return False


class HotkeyManager:
    """RegisterHotKey message loop living on its own Windows thread."""

    def __init__(self):
        self.events: queue.Queue[int] = queue.Queue()
        self.commands: queue.Queue[tuple] = queue.Queue()
        self._registered: dict[int, tuple[int, int]] = {}
        self._stop = threading.Event()
        self._ready = threading.Event()
        self._thread = threading.Thread(target=self._run, name="VaPuHotkeys", daemon=True)
        self._thread.start()
        self._ready.wait(2.0)

    def _run(self) -> None:
        # Calling PeekMessage once creates the thread message queue.
        msg = wintypes.MSG()
        user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, PM_REMOVE)
        self._ready.set()

        while not self._stop.is_set():
            try:
                while True:
                    cmd = self.commands.get_nowait()
                    action = cmd[0]

                    if action == "set":
                        _, hotkey_id, mods, vk, enabled, done, result = cmd

                        if hotkey_id in self._registered:
                            user32.UnregisterHotKey(None, hotkey_id)
                            self._registered.pop(hotkey_id, None)

                        ok = True

                        if enabled:
                            ok = bool(user32.RegisterHotKey(None, hotkey_id, mods, vk))
                            if ok:
                                self._registered[hotkey_id] = (mods, vk)

                        result.append(ok)
                        done.set()

                    elif action == "probe":
                        _, hotkey_id, mods, vk, done, result = cmd

                        if hotkey_id in self._registered:
                            result.append(True)
                        else:
                            ok = bool(user32.RegisterHotKey(None, hotkey_id, mods, vk))
                            if ok:
                                user32.UnregisterHotKey(None, hotkey_id)
                            result.append(ok)

                        done.set()

            except queue.Empty:
                pass

            while user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, PM_REMOVE):
                if msg.message == WM_HOTKEY:
                    self.events.put(int(msg.wParam))

            time.sleep(0.008)

        for hotkey_id in list(self._registered):
            user32.UnregisterHotKey(None, hotkey_id)
        self._registered.clear()

    def set_hotkey(self, hotkey_id: int, mods: int, vk: int, enabled: bool) -> bool:
        done = threading.Event()
        result: list[bool] = []
        self.commands.put(("set", hotkey_id, mods, vk, enabled, done, result))
        done.wait(1.0)
        return bool(result and result[0])

    def probe(self, hotkey_id: int, mods: int, vk: int) -> bool:
        done = threading.Event()
        result: list[bool] = []
        self.commands.put(("probe", hotkey_id, mods, vk, done, result))
        done.wait(1.0)
        return bool(result and result[0])

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(timeout=1.5)
