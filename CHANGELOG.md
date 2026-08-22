# Changelog

## v0.1.0-dev

### Added

- Initial Python port of VaPuClaimer.
- VaPu-style Tkinter GUI.
- Faction / Type / Unit browsing.
- Cross-faction vehicle search.
- CUSTOM NAME support.
- Fixed `Ctrl+Delete` start hotkey.
- Configurable stop hotkey and console key.
- Squad foreground-window scoping.
- Clipboard-based `CreateSquad` loop.
- LOCK SQUAD support.
- `settings.ini` persistence.
- Source-only GitHub Actions artifacts and releases.
- Windows API integration through `ctypes`.
- Unit tests for search, custom names, and settings.

### Changed from the C++ build

- Vehicle JSON is loaded directly instead of compiled into a header.
- No networking or updater code is included.
- START button arms until Squad receives focus rather than injecting into the VaPuClaimer window.
- Console key / Stop hotkey conflicts are rejected.
- Distribution is Python source rather than an unsigned custom executable.
