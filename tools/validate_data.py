import json
from pathlib import Path

path = Path("vapu-vehicles.source.json")
data = json.loads(path.read_text(encoding="utf-8"))
assert isinstance(data.get("factions"), dict) and data["factions"], "Missing factions"

count = 0
empty_types = []
for faction, rows in data["factions"].items():
    assert isinstance(rows, list), f"{faction} is not a list"
    for row in rows:
        unit = str(row.get("unit", "")).strip()
        vehicle_type = str(row.get("type", "")).strip()
        assert unit, f"Empty unit in {faction}"
        if not vehicle_type:
            empty_types.append(f"{faction}: {unit}")
        count += 1

print(f"Validated {count} vehicle entries.")
if empty_types:
    raise SystemExit("Entries with empty type:\n" + "\n".join(empty_types))
