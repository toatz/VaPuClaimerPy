from __future__ import annotations

import os
from pathlib import Path
import queue
import threading
import time
import tkinter as tk
from tkinter import messagebox, ttk

from .model import (
    CUSTOM_FACTION,
    CUSTOM_NAME_OPTION,
    Settings,
    Vehicle,
    VehicleDatabase,
    read_version,
)
from . import winapi


APP_DIR = Path(__file__).resolve().parent.parent
VEHICLE_FILE = APP_DIR / "vapu-vehicles.source.json"
SETTINGS_FILE = APP_DIR / "settings.ini"
VERSION_FILE = APP_DIR / "version.txt"

HOTKEY_START = 2001
HOTKEY_STOP = 2002
HOTKEY_PROBE = 2004

START_MODS = winapi.MOD_CONTROL | winapi.MOD_NOREPEAT
START_VK = winapi.VK_DELETE
START_NAME = "Ctrl+Delete"


class VaPuClaimerApp:
    BG = "#0b0d13"
    TOPBAR = "#12131c"
    PANEL = "#151823"
    PANEL2 = "#1c202d"
    TEXT = "#eef1f8"
    MUTED = "#8e95a8"
    ACCENT = "#ac60ff"
    CYAN = "#5be1ff"
    OK = "#62ebaa"
    DANGER = "#ff5870"
    BORDER = "#383c4f"
    GREEN = "#228058"
    RED = "#702131"

    def __init__(self) -> None:
        self.db = VehicleDatabase.load(VEHICLE_FILE)
        self.settings = Settings(SETTINGS_FILE)
        self.version = read_version(VERSION_FILE)

        self.claiming = False
        self.pending_button_start = False
        self.worker: threading.Thread | None = None
        self.stop_event = threading.Event()
        self.worker_events: queue.Queue[tuple[str, str]] = queue.Queue()

        self.target_class = self.settings.get_target_class()
        self.target_title = self.settings.get_target_title()
        self.last_foreign_class = ""

        self.start_held = False
        self.stop_held = False
        self.scope_active = False

        self.hotkeys = winapi.HotkeyManager()

        self.root = tk.Tk()
        self.root.title(f"VaPuClaimer {self.version}")
        self.root.geometry("500x800")
        self.root.resizable(False, False)
        self.root.configure(bg=self.BG)
        self.root.overrideredirect(True)
        self.root.protocol("WM_DELETE_WINDOW", self.close)

        self._drag_x = 0
        self._drag_y = 0
        self._build_styles()
        self._build_ui()
        self._load_settings_into_ui()
        self._center_window()

        self.root.after(20, self._process_hotkey_events)
        self.root.after(50, self._process_worker_events)
        self.root.after(200, self._poll_scope)

    # ---------- UI ----------

    def _build_styles(self) -> None:
        style = ttk.Style(self.root)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass

        style.configure(
            "VaPu.TCombobox",
            fieldbackground=self.PANEL2,
            background=self.PANEL2,
            foreground=self.TEXT,
            arrowcolor=self.CYAN,
            bordercolor=self.BORDER,
            lightcolor=self.BORDER,
            darkcolor=self.BORDER,
            padding=5,
        )
        style.map(
            "VaPu.TCombobox",
            fieldbackground=[("readonly", self.PANEL2)],
            foreground=[("readonly", self.TEXT)],
            selectbackground=[("readonly", self.PANEL2)],
            selectforeground=[("readonly", self.TEXT)],
        )

    def _center_window(self) -> None:
        self.root.update_idletasks()
        w, h = 500, 800
        x = max(0, (self.root.winfo_screenwidth() - w) // 2)
        y = max(0, (self.root.winfo_screenheight() - h) // 2)
        self.root.geometry(f"{w}x{h}+{x}+{y}")

    def _panel(self, x: int, y: int, w: int, h: int) -> tk.Frame:
        f = tk.Frame(self.root, bg=self.PANEL, highlightthickness=1, highlightbackground=self.BORDER)
        f.place(x=x, y=y, width=w, height=h)
        return f

    def _label(self, parent, text: str, x: int, y: int, *, color=None, size=9, bold=False, width=None):
        font = ("Segoe UI", size, "bold" if bold else "normal")
        lbl = tk.Label(parent, text=text, bg=parent.cget("bg"), fg=color or self.MUTED, font=font, anchor="w")
        kwargs = dict(x=x, y=y)
        if width is not None:
            kwargs["width"] = width
        lbl.place(**kwargs)
        return lbl

    def _build_ui(self) -> None:
        # Title bar
        top = tk.Frame(self.root, bg=self.TOPBAR)
        top.place(x=0, y=0, width=500, height=42)
        accent = tk.Frame(self.root, bg=self.ACCENT)
        accent.place(x=0, y=42, width=500, height=4)

        title = tk.Label(top, text="VaPuClaimer", bg=self.TOPBAR, fg=self.TEXT,
                         font=("Segoe UI", 11, "bold"))
        title.place(x=14, y=10)
        ver = tk.Label(top, text=self.version, bg=self.TOPBAR, fg=self.MUTED,
                       font=("Segoe UI", 8))
        ver.place(x=115, y=13)

        min_btn = tk.Button(top, text="—", command=self._minimize, bg=self.TOPBAR, fg=self.TEXT,
                            activebackground="#2a2d3c", activeforeground=self.TEXT,
                            relief="flat", bd=0, font=("Segoe UI", 12))
        min_btn.place(x=414, y=0, width=43, height=42)

        close_btn = tk.Button(top, text="×", command=self.close, bg=self.TOPBAR, fg=self.TEXT,
                              activebackground="#aa3448", activeforeground=self.TEXT,
                              relief="flat", bd=0, font=("Segoe UI", 13))
        close_btn.place(x=457, y=0, width=43, height=42)

        for widget in (top, title, ver):
            widget.bind("<ButtonPress-1>", self._drag_start)
            widget.bind("<B1-Motion>", self._drag_move)

        # Search
        search_panel = self._panel(18, 62, 464, 78)
        self._label(search_panel, "SEARCH", 12, 8)
        self.search_var = tk.StringVar()
        self.search_entry = tk.Entry(
            search_panel, textvariable=self.search_var, bg=self.PANEL2, fg=self.TEXT,
            insertbackground=self.TEXT, relief="flat", highlightthickness=1,
            highlightbackground=self.BORDER, highlightcolor=self.CYAN,
            font=("Segoe UI", 10),
        )
        self.search_entry.place(x=12, y=30, width=438, height=30)
        self.search_entry.bind("<KeyRelease>", self._on_search_key)
        self.search_entry.bind("<Down>", self._search_down)
        self.search_entry.bind("<Up>", self._search_up)
        self.search_entry.bind("<Return>", self._search_enter)
        self.search_entry.bind("<Escape>", lambda e: self._hide_results())

        self.results = tk.Listbox(
            self.root, bg=self.PANEL2, fg=self.TEXT, selectbackground="#33405b",
            selectforeground=self.TEXT, relief="flat", highlightthickness=1,
            highlightbackground=self.CYAN, font=("Segoe UI", 9),
        )
        self.results.bind("<Double-Button-1>", lambda e: self._apply_search_selection())
        self.results.bind("<Return>", lambda e: self._apply_search_selection())
        self.results.bind("<Escape>", lambda e: self._hide_results())
        self.search_matches: list[Vehicle] = []

        # Main selectors
        main = self._panel(18, 151, 464, 285)
        self._label(main, "FACTION", 12, 10)
        self.faction_var = tk.StringVar()
        self.faction_combo = ttk.Combobox(main, textvariable=self.faction_var, state="readonly", style="VaPu.TCombobox")
        self.faction_combo["values"] = self.db.factions()
        self.faction_combo.place(x=12, y=32, width=438, height=30)
        self.faction_combo.bind("<<ComboboxSelected>>", self._on_faction)

        self._label(main, "TYPE", 12, 72)
        self.type_var = tk.StringVar()
        self.type_combo = ttk.Combobox(main, textvariable=self.type_var, state="readonly", style="VaPu.TCombobox")
        self.type_combo.place(x=12, y=94, width=438, height=30)
        self.type_combo.bind("<<ComboboxSelected>>", self._on_type)

        self._label(main, "UNIT", 12, 134)
        self.unit_var = tk.StringVar()
        self.unit_combo = ttk.Combobox(main, textvariable=self.unit_var, state="readonly", style="VaPu.TCombobox")
        self.unit_combo.place(x=12, y=156, width=438, height=30)
        self.unit_combo.bind("<<ComboboxSelected>>", self._on_unit)

        self.custom_label = self._label(main, "CUSTOM NAME", 12, 192)
        self.custom_var = tk.StringVar()
        self.custom_entry = tk.Entry(
            main, textvariable=self.custom_var, bg=self.PANEL2, fg=self.TEXT,
            insertbackground=self.TEXT, relief="flat", highlightthickness=1,
            highlightbackground=self.BORDER, highlightcolor=self.CYAN,
            font=("Segoe UI", 10),
        )
        self.custom_entry.place(x=12, y=214, width=438, height=30)
        self.custom_entry.bind("<FocusOut>", lambda e: self._save_settings())

        self.lock_var = tk.BooleanVar(value=True)
        self.lock_btn = tk.Checkbutton(
            main, text="LOCK SQUAD   (1 = locked / 0 = open)",
            variable=self.lock_var, command=self._on_lock,
            bg=self.PANEL, fg=self.TEXT, selectcolor=self.PANEL2,
            activebackground=self.PANEL, activeforeground=self.TEXT,
            highlightthickness=0, bd=0, font=("Segoe UI", 9),
        )
        self.lock_btn.place(x=12, y=250)

        # Hotkeys
        hot = self._panel(18, 447, 464, 98)
        self._label(hot, "CONSOLE KEY", 12, 10)
        self._label(hot, "STOP HOTKEY", 244, 10)
        self.console_var = tk.StringVar()
        self.console_combo = ttk.Combobox(hot, textvariable=self.console_var, state="readonly", style="VaPu.TCombobox")
        self.console_combo["values"] = [x.name for x in winapi.CONSOLE_KEYS]
        self.console_combo.place(x=12, y=35, width=206, height=30)
        self.console_combo.bind("<<ComboboxSelected>>", self._on_console_key)

        self.stop_var = tk.StringVar()
        self.stop_combo = ttk.Combobox(hot, textvariable=self.stop_var, state="readonly", style="VaPu.TCombobox")
        self.stop_combo["values"] = [name for name, _ in winapi.HOTKEY_OPTIONS]
        self.stop_combo.place(x=244, y=35, width=206, height=30)
        self.stop_combo.bind("<<ComboboxSelected>>", self._on_stop_hotkey)

        # Status
        stat = self._panel(18, 556, 464, 82)
        self._label(stat, "STATUS", 12, 9)
        self.status_var = tk.StringVar(value="IDLE")
        self.status_label = tk.Label(stat, textvariable=self.status_var, bg=self.PANEL2, fg=self.CYAN,
                                     font=("Segoe UI", 11, "bold"))
        self.status_label.place(x=12, y=32, width=438, height=36)

        # Buttons
        self.start_btn = tk.Button(
            self.root, text="START CLAIM", command=self._start_button,
            bg=self.GREEN, fg="#f0fff8", activebackground="#299c6b",
            activeforeground="#f0fff8", relief="flat", bd=0,
            font=("Segoe UI", 10, "bold"),
        )
        self.start_btn.place(x=30, y=655, width=210, height=50)

        self.stop_btn = tk.Button(
            self.root, text="STOP", command=self.stop_claim,
            bg=self.RED, fg="#fff0f4", activebackground="#952b41",
            activeforeground="#fff0f4", relief="flat", bd=0,
            font=("Segoe UI", 10, "bold"),
        )
        self.stop_btn.place(x=260, y=655, width=210, height=50)

        self.hotkey_hint = tk.Label(
            self.root, text="", bg=self.BG, fg=self.MUTED, anchor="w",
            font=("Segoe UI", 8),
        )
        self.hotkey_hint.place(x=30, y=719, width=440, height=18)

        tk.Frame(self.root, bg=self.BORDER).place(x=30, y=747, width=440, height=1)
        self.footer = tk.Label(
            self.root, text="VaPu // starting", bg=self.BG, fg=self.MUTED,
            anchor="w", font=("Segoe UI", 8),
        )
        self.footer.place(x=30, y=760, width=440, height=20)

    def _drag_start(self, event) -> None:
        self._drag_x = event.x_root - self.root.winfo_x()
        self._drag_y = event.y_root - self.root.winfo_y()

    def _drag_move(self, event) -> None:
        self.root.geometry(f"+{event.x_root - self._drag_x}+{event.y_root - self._drag_y}")

    def _minimize(self) -> None:
        self.root.overrideredirect(False)
        self.root.iconify()
        self.root.bind("<Map>", self._restore_borderless, add="+")

    def _restore_borderless(self, _event=None) -> None:
        self.root.after(20, lambda: self.root.overrideredirect(True))

    # ---------- selections ----------

    def _load_settings_into_ui(self) -> None:
        factions = self.db.factions()
        if factions:
            self.faction_var.set(factions[0])
        self._refresh_types_units()

        console_id = self.settings.get_console_key()
        option = winapi.CONSOLE_BY_ID.get(console_id, winapi.CONSOLE_BY_ID["Tilde"])
        self.console_var.set(option.name)

        stop_name = self.settings.get_stop_hotkey()
        if stop_name not in winapi.HOTKEY_VK:
            stop_name = "Delete"
        self.stop_var.set(stop_name)

        self.lock_var.set(self.settings.get_locked())
        self.custom_var.set(self.settings.get_custom_name())
        self._update_custom_visibility()
        self._update_hint()

    def _refresh_types_units(self) -> None:
        faction = self.faction_var.get()
        types = self.db.types_for(faction)
        self.type_combo["values"] = types
        self.type_var.set(types[0] if types else "")

        units = self.db.units_for(faction, self.type_var.get())
        self.unit_combo["values"] = units
        self.unit_var.set(units[0] if units else "")
        self._update_custom_visibility()

    def _refresh_units(self) -> None:
        units = self.db.units_for(self.faction_var.get(), self.type_var.get())
        self.unit_combo["values"] = units
        self.unit_var.set(units[0] if units else "")
        self._update_custom_visibility()

    def _on_faction(self, _event=None) -> None:
        self._refresh_types_units()

    def _on_type(self, _event=None) -> None:
        self._refresh_units()

    def _on_unit(self, _event=None) -> None:
        self._update_custom_visibility()

    def _update_custom_visibility(self) -> None:
        custom = self.faction_var.get() == CUSTOM_FACTION and self.unit_var.get() == CUSTOM_NAME_OPTION
        if custom:
            self.custom_label.place()
            self.custom_entry.place()
        else:
            self.custom_label.place_forget()
            self.custom_entry.place_forget()

    def _on_lock(self) -> None:
        self._save_settings()

    def _selected_console_id(self) -> str:
        for option in winapi.CONSOLE_KEYS:
            if option.name == self.console_var.get():
                return option.id
        return "Tilde"

    def _selected_console_option(self):
        return winapi.CONSOLE_BY_ID.get(self._selected_console_id(), winapi.CONSOLE_BY_ID["Tilde"])

    def _on_console_key(self, _event=None) -> None:
        if not self._validate_key_conflict(show=True):
            self.console_var.set(winapi.CONSOLE_BY_ID[self.settings.get_console_key()].name)
            return
        self._save_settings()

    def _on_stop_hotkey(self, _event=None) -> None:
        if not self._validate_key_conflict(show=True):
            self.stop_var.set(self.settings.get_stop_hotkey())
            return

        vk = winapi.HOTKEY_VK.get(self.stop_var.get(), 0)
        if not vk or not self.hotkeys.probe(HOTKEY_PROBE, winapi.MOD_NOREPEAT, vk):
            messagebox.showerror(
                "VaPuClaimer",
                "Could not register the selected STOP hotkey.\nIt may already be used by another app.",
            )
            self.stop_var.set(self.settings.get_stop_hotkey())
            return

        self._save_settings()
        self._update_hint()

    def _validate_key_conflict(self, show: bool) -> bool:
        option = self._selected_console_option()
        stop_vk = winapi.HOTKEY_VK.get(self.stop_var.get(), 0)
        if option.vk and stop_vk and option.vk == stop_vk:
            if show:
                messagebox.showwarning(
                    "VaPuClaimer",
                    "Console key and STOP hotkey cannot be the same key.",
                )
            return False
        return True

    def _save_settings(self) -> None:
        self.settings.save(
            stop_hotkey=self.stop_var.get() or "Delete",
            console_key=self._selected_console_id(),
            locked=bool(self.lock_var.get()),
            target_class=self.target_class,
            target_title=self.target_title,
            custom_name=self.custom_var.get().strip(),
        )

    # ---------- search ----------

    def _on_search_key(self, event) -> None:
        if event.keysym in {"Up", "Down", "Return", "Escape"}:
            return
        self._update_search_results()

    def _update_search_results(self) -> None:
        self.search_matches = self.db.search(self.search_var.get(), limit=50)
        self.results.delete(0, tk.END)
        for v in self.search_matches:
            self.results.insert(tk.END, f"{v.unit}   -   {v.faction} / {v.type}")

        if not self.search_matches:
            self._hide_results()
            return

        self.results.selection_set(0)
        rows = min(8, len(self.search_matches))
        self.results.place(x=30, y=122, width=440, height=rows * 22 + 4)
        self.results.lift()

    def _hide_results(self) -> None:
        self.results.place_forget()

    def _search_down(self, _event=None):
        if not self.search_matches:
            self._update_search_results()
        if not self.search_matches:
            return "break"
        cur = self.results.curselection()
        idx = cur[0] if cur else -1
        idx = min(len(self.search_matches) - 1, idx + 1)
        self.results.selection_clear(0, tk.END)
        self.results.selection_set(idx)
        self.results.see(idx)
        return "break"

    def _search_up(self, _event=None):
        if not self.search_matches:
            return "break"
        cur = self.results.curselection()
        idx = cur[0] if cur else 0
        idx = max(0, idx - 1)
        self.results.selection_clear(0, tk.END)
        self.results.selection_set(idx)
        self.results.see(idx)
        return "break"

    def _search_enter(self, _event=None):
        self._apply_search_selection()
        return "break"

    def _apply_search_selection(self) -> None:
        cur = self.results.curselection()
        if not cur:
            return
        idx = cur[0]
        if idx >= len(self.search_matches):
            return
        vehicle = self.search_matches[idx]

        self.faction_var.set(vehicle.faction)
        types = self.db.types_for(vehicle.faction)
        self.type_combo["values"] = types
        self.type_var.set(vehicle.type)

        units = self.db.units_for(vehicle.faction, vehicle.type)
        self.unit_combo["values"] = units
        self.unit_var.set(vehicle.unit)

        self._update_custom_visibility()
        self._hide_results()
        self.search_entry.focus_set()
        self.search_entry.selection_range(0, tk.END)

    # ---------- scope / hotkeys ----------

    def _foreground_is_game(self) -> tuple[bool, str]:
        pid, cls, title = winapi.foreground_info()

        if pid and pid != os.getpid():
            self.last_foreign_class = cls

        # Unlike the C++ version, the Python port intentionally excludes its
        # own window here so START can never inject into VaPuClaimer itself.
        if pid == os.getpid():
            return False, cls

        if not self.target_class and not self.target_title:
            return True, cls

        class_ok = not self.target_class or cls.lower() == self.target_class.lower()
        title_ok = not self.target_title or self.target_title.lower() in title.lower()
        return class_ok and title_ok, cls

    def _poll_scope(self) -> None:
        try:
            in_game, _ = self._foreground_is_game()
            self.scope_active = in_game

            want_start = in_game and not self.claiming
            want_stop = self.claiming

            if want_start != self.start_held:
                ok = self.hotkeys.set_hotkey(HOTKEY_START, START_MODS, START_VK, want_start)
                self.start_held = want_start and ok

            if want_stop != self.stop_held:
                stop_vk = winapi.HOTKEY_VK.get(self.stop_var.get(), winapi.VK_DELETE)
                ok = self.hotkeys.set_hotkey(HOTKEY_STOP, winapi.MOD_NOREPEAT, stop_vk, want_stop)
                self.stop_held = want_stop and ok

            if self.pending_button_start and in_game and not self.claiming:
                self.pending_button_start = False
                self.start_claim()

            if self.claiming:
                state = "stop key armed"
                color = self.OK
            elif in_game and self.start_held:
                state = "start key armed"
                color = self.OK
            elif self.pending_button_start:
                state = "armed - focus Squad"
                color = self.CYAN
            else:
                state = "standby - focus Squad"
                color = self.MUTED

            extra = f"   [{self.last_foreign_class}]" if self.last_foreign_class else ""
            self.footer.config(text=f"VaPu // {state}{extra}", fg=color)
        finally:
            if self.root.winfo_exists():
                self.root.after(200, self._poll_scope)

    def _process_hotkey_events(self) -> None:
        try:
            while True:
                hotkey_id = self.hotkeys.events.get_nowait()
                if hotkey_id == HOTKEY_START:
                    self.start_claim()
                elif hotkey_id == HOTKEY_STOP:
                    self.stop_claim()
        except queue.Empty:
            pass
        if self.root.winfo_exists():
            self.root.after(20, self._process_hotkey_events)

    # ---------- claim ----------

    def _selected_unit(self) -> str:
        if self.faction_var.get() == CUSTOM_FACTION and self.unit_var.get() == CUSTOM_NAME_OPTION:
            return self.custom_var.get().strip()
        return self.unit_var.get().strip()

    def _start_button(self) -> None:
        in_game, _ = self._foreground_is_game()
        if in_game:
            self.start_claim()
        else:
            self.pending_button_start = True
            self.status_var.set("ARMED — FOCUS SQUAD")
            self.status_label.config(fg=self.CYAN)

    def start_claim(self) -> None:
        if self.claiming:
            return

        unit = self._selected_unit()
        if not unit:
            messagebox.showwarning("VaPuClaimer", "Select a unit or enter a custom squad name first.")
            return
        if '"' in unit or "\r" in unit or "\n" in unit:
            messagebox.showwarning(
                "VaPuClaimer",
                "Squad names cannot contain quotes or line breaks.",
            )
            return
        if not self._validate_key_conflict(show=True):
            return

        # A previous stop completes within one pass (~50 ms), but avoid two
        # clipboard workers ever running at once.
        if self.worker and self.worker.is_alive():
            self.stop_event.set()
            self.worker.join(timeout=0.25)

        self._save_settings()
        self.stop_event = threading.Event()
        self.claiming = True
        self.pending_button_start = False
        self._set_controls_enabled(False)
        self._hide_results()

        locked = bool(self.lock_var.get())
        console_key = winapi.resolve_console_key(self._selected_console_id())
        self.status_var.set(f"CLAIMING  {unit}" + ("" if locked else "  (OPEN)"))
        self.status_label.config(fg=self.OK)

        self.worker = threading.Thread(
            target=self._claim_worker,
            args=(unit, console_key, locked, self.stop_event),
            name="VaPuClaim",
            daemon=True,
        )
        self.worker.start()

    def stop_claim(self) -> None:
        self.pending_button_start = False
        if self.claiming:
            self.stop_event.set()
        self.claiming = False
        self._set_controls_enabled(True)
        self.status_var.set("IDLE")
        self.status_label.config(fg=self.CYAN)

    def _set_controls_enabled(self, enabled: bool) -> None:
        combo_state = "readonly" if enabled else "disabled"
        entry_state = "normal" if enabled else "disabled"

        for combo in (self.faction_combo, self.type_combo, self.unit_combo, self.console_combo, self.stop_combo):
            combo.configure(state=combo_state)
        self.search_entry.configure(state=entry_state)
        self.custom_entry.configure(state=entry_state if enabled else "disabled")
        self.lock_btn.configure(state=entry_state)
        self.start_btn.configure(state=entry_state)

    def _claim_worker(self, unit: str, console_key, locked: bool, stop_event: threading.Event) -> None:
        command = f'CreateSquad "{unit}" {"1" if locked else "0"}'
        had_text, saved_text = winapi.get_clipboard_text()
        clipboard_taken = False
        armed_seq = 0
        armed = False

        try:
            while not stop_event.is_set():
                if not armed or winapi.clipboard_sequence() != armed_seq:
                    armed = winapi.set_clipboard_text(command)
                    if not armed:
                        time.sleep(0.020)
                        continue
                    armed_seq = winapi.clipboard_sequence()
                    clipboard_taken = True

                winapi.tap_console_key(console_key)
                time.sleep(0.025)
                winapi.tap_ctrl_v()
                time.sleep(0.010)
                winapi.tap_vk(winapi.VK_RETURN)
                time.sleep(0.015)
        except Exception as exc:
            self.worker_events.put(("error", str(exc)))
        finally:
            if clipboard_taken and winapi.clipboard_sequence() == armed_seq:
                winapi.set_clipboard_text(saved_text if had_text else "")
            self.worker_events.put(("stopped", ""))

    def _process_worker_events(self) -> None:
        try:
            while True:
                kind, text = self.worker_events.get_nowait()
                if kind == "error":
                    messagebox.showerror("VaPuClaimer", f"Claim worker error:\n{text}")
                    self.stop_claim()
                elif kind == "stopped" and self.claiming and self.stop_event.is_set():
                    self.stop_claim()
        except queue.Empty:
            pass
        if self.root.winfo_exists():
            self.root.after(50, self._process_worker_events)

    def _update_hint(self) -> None:
        self.hotkey_hint.config(text=f"START: {START_NAME}    STOP: {self.stop_var.get()}")

    # ---------- close ----------

    def close(self) -> None:
        try:
            self._save_settings()
        except Exception:
            pass
        self.stop_event.set()
        self.claiming = False
        try:
            self.hotkeys.set_hotkey(HOTKEY_START, START_MODS, START_VK, False)
            stop_vk = winapi.HOTKEY_VK.get(self.stop_var.get(), winapi.VK_DELETE)
            self.hotkeys.set_hotkey(HOTKEY_STOP, winapi.MOD_NOREPEAT, stop_vk, False)
        except Exception:
            pass
        self.hotkeys.stop()
        self.root.destroy()

    def run(self) -> None:
        self.root.mainloop()


def main() -> None:
    if os.name != "nt":
        raise SystemExit("VaPuClaimer requires Windows.")
    VaPuClaimerApp().run()
