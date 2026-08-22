# VaPuClaimer Python

Python port of **VaPuClaimer**, keeping the current C++ version's claim behavior while avoiding distribution of a custom unsigned `.exe`.

The port uses only Python's standard library: **no pip packages are required**.

## Requirements

- Windows 10/11
- Python 3.11+ from python.org
- Tkinter (included with the normal python.org Windows installer)

## Run

Double-click:

```text
run.bat
```

or run:

```powershell
pyw -3 VaPuClaimer.pyw
```

## Features

- VaPu-style dark GUI
- Faction → Type → Unit selection
- Search across every faction by unit, faction, and type
- `CUSTOM NAME...` support under the `CUSTOM` faction
- Exact unit names from `vapu-vehicles.source.json`
- Fixed start hotkey: `Ctrl+Delete`
- Configurable stop hotkey
- Configurable Squad console key
- Start hotkey only arms while Squad is actually focused
- Stop hotkey stays available while a claim is running
- LOCK SQUAD (`1` locked / `0` open)
- Clipboard-based claim loop:
  - Console key
  - Ctrl+V
  - Enter
- Text clipboard restoration after the claim
- Local `settings.ini`
- No network code
- No updater/downloader
- No third-party dependencies

## Why clipboard paste?

Older command typing could accidentally hit Squad keybinds if the console missed a toggle. In particular `J`, `K`, and `L` can open chat.

The Python port keeps the safer current behavior:

```text
Console key → Ctrl+V → Enter
```

It never falls back to typing the `CreateSquad` command character-by-character.

## Claim command

Locked:

```text
CreateSquad "MRH-90" 1
```

Open:

```text
CreateSquad "MRH-90" 0
```

## Search

Examples:

```text
mrh
adf heli
bmp rgf
```

Every search word must appear somewhere in:

```text
unit + faction + type
```

Press `Up` / `Down`, then `Enter`, or double-click a result.

## Custom squad names

Choose:

```text
Faction: CUSTOM
Unit: CUSTOM NAME...
```

A text field appears for your own squad name.

Quotes and line breaks are rejected so the generated `CreateSquad` command stays valid.

## Hotkeys

Start is fixed:

```text
Ctrl+Delete
```

Stop can be selected from:

- F1–F12
- Insert / Home / End
- PageUp / PageDown / Delete
- Numpad 0–9

The Python version also rejects a configuration where **Console Key** and **Stop Hotkey** are the same key, because the claimer could otherwise stop itself.

## Focus handling

By default the game target is:

```ini
[Target]
WindowClass = UnrealWindow
WindowTitle = squad
```

Unlike the older C++ behavior, the Python port intentionally does **not** treat its own window as a valid Squad target.

Clicking **START CLAIM** while Squad is not focused arms the claim. It begins automatically after you focus Squad.

## Settings

Created next to the program as:

```text
settings.ini
```

Example:

```ini
[Hotkeys]
Stop = Delete

[Console]
Key = Tilde

[Squad]
Locked = 1

[Target]
WindowClass = UnrealWindow
WindowTitle = squad

[Custom]
Name = MY SQUAD
```

## Vehicle data

The Python version reads:

```text
vapu-vehicles.source.json
```

directly. There is no generated C++ header.

This makes vehicle list updates simpler: edit the JSON and restart VaPuClaimer.

## Clipboard limitation

Like the current C++ implementation, the claimer can restore previous **text** clipboard content.

If the clipboard originally contains only an image or copied files, that non-text clipboard data cannot be restored after VaPuClaimer puts the claim command on the clipboard.

## Smart App Control

This repository intentionally distributes **Python source**, not a PyInstaller executable.

Building the Python project into a new unsigned EXE would bring back the same code-signing / reputation problem that motivated the Python port.

Use the normal python.org `pythonw.exe` / `pyw.exe` to run the source.

## GitHub Actions

### `check.yml`

On pushes and pull requests it:

1. installs Python on a Windows runner
2. syntax-checks the project
3. runs unit tests
4. validates the vehicle JSON
5. creates a source ZIP artifact

### `release.yml`

Manual workflow:

1. enter a version such as `v0.1.0`
2. runs the checks
3. writes that version to `version.txt`
4. creates `VaPuClaimer-Python-v0.1.0.zip`
5. creates a SHA-256 file
6. publishes both as GitHub Release assets

No EXE is generated.

## Project layout

```text
VaPuClaimer-Python/
├─ VaPuClaimer.pyw
├─ run.bat
├─ version.txt
├─ vapu-vehicles.source.json
├─ settings.ini.example
├─ vapuclaimer/
│  ├─ __init__.py
│  ├─ app.py
│  ├─ model.py
│  └─ winapi.py
├─ tests/
│  └─ test_model.py
└─ .github/
   └─ workflows/
      ├─ check.yml
      └─ release.yml
```
