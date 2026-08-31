import csv
import hashlib
import json
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data/daily-prices"


class DailyPriceDatasetTest(unittest.TestCase):
    def test_latest_manifest_matches_latest_csv(self) -> None:
        manifest = json.loads((DATA / "latest.json").read_text(encoding="utf-8"))
        latest = manifest["latest_session"]
        csv_path = DATA / latest["file"]
        content = csv_path.read_bytes()
        with csv_path.open(encoding="utf-8", newline="") as source:
            rows = list(csv.DictReader(source))

        self.assertEqual(hashlib.sha256(content).hexdigest(), latest["sha256"])
        self.assertEqual(len(rows), manifest["universe"]["count"])
        self.assertEqual(len({row["銘柄コード"] for row in rows}), len(rows))
        self.assertTrue(all(row["日付"] == latest["date"] for row in rows))

        counts = {"OK": 0, "NO_QUOTE": 0, "FETCH_ERROR": 0}
        for row in rows:
            self.assertIn(row["取得状態"], counts)
            counts[row["取得状態"]] += 1
        self.assertEqual(counts["OK"], latest["quote_count"])
        self.assertEqual(counts["NO_QUOTE"], latest["no_quote_count"])
        self.assertEqual(counts["FETCH_ERROR"], latest["fetch_error_count"])
        self.assertEqual(manifest["fetch"]["error_count"], counts["FETCH_ERROR"])


if __name__ == "__main__":
    unittest.main()
