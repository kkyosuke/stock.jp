import csv
import json
import shutil
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from zoneinfo import ZoneInfo

from scripts.operation_state import PROJECT_ROOT, initialize_or_migrate_workspace
from scripts.yahoo_price_collector import collect_prices

JST = ZoneInfo("Asia/Tokyo")


class YahooPriceCollectorTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = TemporaryDirectory()
        self.root = Path(self.temporary.name)
        (self.root / "operations").mkdir()
        shutil.copytree(
            PROJECT_ROOT / "operations/templates", self.root / "operations/templates"
        )
        initialize_or_migrate_workspace(self.root)
        portfolio = self.root / "operations/private/portfolio-register.csv"
        with portfolio.open(encoding="utf-8", newline="") as source:
            fields = next(csv.reader(source))
        row = dict.fromkeys(fields, "")
        row.update(
            {
                "code": "1234",
                "company": "Example",
                "status": "OPEN",
                "entry_date": "2026-08-03",
                "average_cost": "100",
            }
        )
        with portfolio.open("a", encoding="utf-8", newline="") as destination:
            csv.DictWriter(destination, fieldnames=fields).writerow(row)
        self.fixture = self.root / "fixture"
        self.fixture.mkdir()
        (self.fixture / "universe.csv").write_text(
            "code,company,market\n1234,Example,Prime\n", encoding="utf-8"
        )
        start = datetime(2026, 8, 3, 15, 0, tzinfo=JST)
        days = [start + timedelta(days=index) for index in range(29)]
        days = [value for value in days if value.weekday() < 5]
        closes = [100 + index for index in range(len(days))]
        self.closes = closes
        response = {
            "timestamp": [int(value.timestamp()) for value in days],
            "indicators": {
                "quote": [
                    {
                        "open": closes,
                        "high": [value + 1 for value in closes],
                        "low": [value - 1 for value in closes],
                        "close": closes,
                        "volume": [1000 for _ in closes],
                    }
                ],
                "adjclose": [{"adjclose": closes}],
            },
        }
        payload = {
            "spark": {
                "result": [
                    {"symbol": "1234.T", "response": [response]}
                ],
                "error": None,
            }
        }
        (self.fixture / "yahoo-spark.json").write_text(
            json.dumps(payload), encoding="utf-8"
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_daily_fixture_updates_history_metrics_and_watermark_idempotently(self) -> None:
        first = collect_prices(
            at="2026-09-01T18:00:00+09:00",
            fixture_dir=self.fixture,
            scope="daily",
            root=self.root,
        )
        second = collect_prices(
            at="2026-09-01T18:00:00+09:00",
            fixture_dir=self.fixture,
            scope="daily",
            root=self.root,
        )
        self.assertEqual(first["status"], "COMPLETED")
        self.assertEqual(first["coverage_ratio"], 1.0)
        self.assertEqual(first["target_codes"], ["1234"])
        self.assertNotEqual(first["snapshot_path"], second["snapshot_path"])
        first_manifest = json.loads(
            (self.root / first["snapshot_path"] / "manifest.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(first_manifest["status"], "COMPLETED")
        self.assertEqual(second["successful_daily_price_dates"], 1)
        with (self.root / "operations/private/price-history.csv").open(
            encoding="utf-8", newline=""
        ) as source:
            history = list(csv.DictReader(source))
        self.assertEqual(len(history), len({row["price_date"] for row in history}))
        with (self.root / "operations/private/portfolio-register.csv").open(
            encoding="utf-8", newline=""
        ) as source:
            position = next(csv.DictReader(source))
        self.assertEqual(position["last_close"], str(self.closes[-1]))
        self.assertTrue(position["ma20"])
        self.assertTrue(position["dd20"])

    def test_missing_daily_target_fails_closed_without_advancing_state(self) -> None:
        payload_path = self.fixture / "yahoo-spark.json"
        payload = json.loads(payload_path.read_text(encoding="utf-8"))
        payload["spark"]["result"] = []
        payload_path.write_text(json.dumps(payload), encoding="utf-8")
        result = collect_prices(
            at="2026-09-01T18:00:00+09:00",
            fixture_dir=self.fixture,
            scope="daily",
            root=self.root,
        )
        state = json.loads(
            (self.root / "operations/private/market-data-state.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(result["status"], "BLOCKED")
        self.assertEqual(result["critical_failures"], ["1234"])
        self.assertIsNone(state["last_success_at_jst"])

    def test_monthly_scope_writes_a_machine_readable_full_market_screen(self) -> None:
        result = collect_prices(
            at="2026-09-01T18:00:00+09:00",
            fixture_dir=self.fixture,
            scope="monthly",
            root=self.root,
        )
        screen = (
            self.root / result["snapshot_path"] / "monthly-price-screen.csv"
        )
        with screen.open(encoding="utf-8", newline="") as source:
            rows = list(csv.DictReader(source))
        self.assertEqual(result["status"], "COMPLETED")
        self.assertEqual(result["monthly_screen_count"], 1)
        self.assertEqual(rows[0]["code"], "1234")
        self.assertIn("return_60d", rows[0])

    def test_monthly_close_only_response_never_claims_liquidity_coverage(self) -> None:
        payload_path = self.fixture / "yahoo-spark.json"
        payload = json.loads(payload_path.read_text(encoding="utf-8"))
        quote = payload["spark"]["result"][0]["response"][0]["indicators"]["quote"][0]
        quote.clear()
        quote["close"] = self.closes
        payload_path.write_text(json.dumps(payload), encoding="utf-8")
        result = collect_prices(
            at="2026-09-01T18:00:00+09:00",
            fixture_dir=self.fixture,
            scope="monthly",
            root=self.root,
        )
        screen = self.root / result["snapshot_path"] / "monthly-price-screen.csv"
        with screen.open(encoding="utf-8", newline="") as source:
            row = next(csv.DictReader(source))
        self.assertEqual(result["status"], "COMPLETED")
        self.assertEqual(row["average_turnover_20d_yen"], "")
        self.assertIn("LIQUIDITY_UNAVAILABLE", row["observation"])

    def test_daily_target_lagging_the_market_price_date_is_blocked(self) -> None:
        (self.fixture / "universe.csv").write_text(
            "code,company,market\n1234,Example,Prime\n5678,Lagging,Prime\n",
            encoding="utf-8",
        )
        payload_path = self.fixture / "yahoo-spark.json"
        payload = json.loads(payload_path.read_text(encoding="utf-8"))
        lagging = json.loads(json.dumps(payload["spark"]["result"][0]))
        lagging["symbol"] = "5678.T"
        response = lagging["response"][0]
        response["timestamp"] = response["timestamp"][:-1]
        for indicator_group in response["indicators"].values():
            for values in indicator_group[0].values():
                del values[-1]
        payload["spark"]["result"].append(lagging)
        payload_path.write_text(json.dumps(payload), encoding="utf-8")

        result = collect_prices(
            at="2026-09-01T18:00:00+09:00",
            fixture_dir=self.fixture,
            scope="daily",
            root=self.root,
        )

        self.assertEqual(result["status"], "BLOCKED")
        self.assertEqual(result["market_price_date"], "2026-08-31")
        self.assertIn("5678", result["stale_codes"])


if __name__ == "__main__":
    unittest.main()
