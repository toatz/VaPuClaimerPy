from __future__ import annotations

from dataclasses import dataclass
import configparser
import json
from pathlib import Path
from typing import Iterable

CUSTOM_FACTION = "CUSTOM"
CUSTOM_NAME_OPTION = "CUSTOM NAME..."


@dataclass(frozen=True)
class Vehicle:
    faction: str
    type: str
    unit: str

    @property
    def search_text(self) -> str:
        return f"{self.unit} {self.faction} {self.type}".lower()


class VehicleDatabase:
    def __init__(self, vehicles: Iterable[Vehicle]):
        self.vehicles = list(vehicles)

    @classmethod
    def load(cls, path: Path) -> "VehicleDatabase":
        raw = json.loads(path.read_text(encoding="utf-8"))
        vehicles: list[Vehicle] = []
        for faction, entries in raw["factions"].items():
            for entry in entries:
                vehicles.append(
                    Vehicle(
                        faction=str(faction),
                        type=str(entry.get("type", "")),
                        unit=str(entry.get("unit", "")),
                    )
                )
        return cls(vehicles)

    def factions(self) -> list[str]:
        return sorted({v.faction for v in self.vehicles})

    def types_for(self, faction: str) -> list[str]:
        return sorted({v.type for v in self.vehicles if v.faction == faction and v.type})

    def units_for(self, faction: str, vehicle_type: str) -> list[str]:
        out = sorted(
            v.unit
            for v in self.vehicles
            if v.faction == faction and v.type == vehicle_type and v.unit
        )
        if faction == CUSTOM_FACTION:
            out.append(CUSTOM_NAME_OPTION)
        return out

    def search(self, query: str, limit: int = 50) -> list[Vehicle]:
        words = [w for w in query.lower().split() if w]
        if not words:
            return []

        matches: list[Vehicle] = []
        for vehicle in self.vehicles:
            # Matches the C++ behavior: empty-type entries are not reachable
            # through the dropdowns, so do not offer them in search either.
            if not vehicle.type:
                continue
            if all(word in vehicle.search_text for word in words):
                matches.append(vehicle)
                if len(matches) >= limit:
                    break
        return matches


class Settings:
    def __init__(self, path: Path):
        self.path = path
        self.config = configparser.ConfigParser(interpolation=None)
        self.config.optionxform = str
        if path.exists():
            self.config.read(path, encoding="utf-8")

    def _get(self, section: str, key: str, default: str) -> str:
        try:
            return self.config.get(section, key)
        except (configparser.NoSectionError, configparser.NoOptionError):
            return default

    def get_stop_hotkey(self) -> str:
        return self._get("Hotkeys", "Stop", "Delete")

    def get_console_key(self) -> str:
        return self._get("Console", "Key", "Tilde")

    def get_locked(self) -> bool:
        return self._get("Squad", "Locked", "1") != "0"

    def get_target_class(self) -> str:
        return self._get("Target", "WindowClass", "UnrealWindow")

    def get_target_title(self) -> str:
        return self._get("Target", "WindowTitle", "squad")

    def get_custom_name(self) -> str:
        return self._get("Custom", "Name", "")

    def save(
        self,
        *,
        stop_hotkey: str,
        console_key: str,
        locked: bool,
        target_class: str,
        target_title: str,
        custom_name: str,
    ) -> None:
        values = {
            "Hotkeys": {"Stop": stop_hotkey},
            "Console": {"Key": console_key},
            "Squad": {"Locked": "1" if locked else "0"},
            "Target": {"WindowClass": target_class, "WindowTitle": target_title},
            "Custom": {"Name": custom_name},
        }
        for section, pairs in values.items():
            if not self.config.has_section(section):
                self.config.add_section(section)
            for key, value in pairs.items():
                self.config.set(section, key, value)

        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        with tmp.open("w", encoding="utf-8") as f:
            self.config.write(f)
        tmp.replace(self.path)


def read_version(path: Path) -> str:
    try:
        value = path.read_text(encoding="utf-8").strip()
        return value or "v0.0.0-dev"
    except OSError:
        return "v0.0.0-dev"
