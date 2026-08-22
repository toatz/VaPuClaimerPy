import json
import tempfile
import unittest
from pathlib import Path

from vapuclaimer.model import (
    CUSTOM_FACTION,
    CUSTOM_NAME_OPTION,
    Settings,
    VehicleDatabase,
)


class VehicleDatabaseTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        data = {
            "factions": {
                "ADF": [
                    {"unit": "MRH-90", "type": "Heli"},
                    {"unit": "ASLAV", "type": "IFV"},
                ],
                "CUSTOM": [
                    {"unit": "PULTTIBOIS", "type": "Vapu inf"},
                ],
            }
        }
        path = self.root / "vehicles.json"
        path.write_text(json.dumps(data), encoding="utf-8")
        self.db = VehicleDatabase.load(path)

    def tearDown(self):
        self.tmp.cleanup()

    def test_search_matches_all_words_any_order(self):
        rows = self.db.search("heli adf")
        self.assertEqual([v.unit for v in rows], ["MRH-90"])

    def test_custom_name_option_is_appended_to_custom_faction(self):
        units = self.db.units_for(CUSTOM_FACTION, "Vapu inf")
        self.assertEqual(units[-1], CUSTOM_NAME_OPTION)

    def test_custom_name_option_is_appended_to_normal_faction(self):
        units = self.db.units_for("ADF", "IFV")
        self.assertEqual(units, ["ASLAV", CUSTOM_NAME_OPTION])

    def test_settings_round_trip(self):
        path = self.root / "settings.ini"
        s = Settings(path)
        s.save(
            stop_hotkey="F8",
            console_key="Tilde",
            locked=False,
            target_class="UnrealWindow",
            target_title="squad",
            custom_name="MY SQUAD",
        )
        s2 = Settings(path)
        self.assertEqual(s2.get_stop_hotkey(), "F8")
        self.assertFalse(s2.get_locked())
        self.assertEqual(s2.get_custom_name(), "MY SQUAD")


if __name__ == "__main__":
    unittest.main()
