import plistlib
import shutil
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from scripts.operation_state import PROJECT_ROOT
from scripts.render_price_schedule import render_schedule


class PriceScheduleTest(unittest.TestCase):
    def test_rendered_schedule_is_valid_and_cannot_escape_private_state(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "operations").mkdir()
            shutil.copytree(
                PROJECT_ROOT / "operations/templates", root / "operations/templates"
            )
            (root / "operations/private").mkdir()
            result = render_schedule(root=root)
            path = root / result["path"]
            payload = plistlib.loads(path.read_bytes())
            self.assertEqual(payload["Label"], "com.stockjp.price-collector")
            self.assertEqual(
                [entry["Weekday"] for entry in payload["StartCalendarInterval"]],
                [1, 2, 3, 4, 5],
            )
            self.assertTrue(
                all(
                    entry["Hour"] == 18 and entry["Minute"] == 0
                    for entry in payload["StartCalendarInterval"]
                )
            )
            self.assertNotIn("__PROJECT_ROOT__", path.read_text(encoding="utf-8"))
            with self.assertRaisesRegex(ValueError, "operations/private"):
                render_schedule(root=root, output=root / "outside.plist")


if __name__ == "__main__":
    unittest.main()
