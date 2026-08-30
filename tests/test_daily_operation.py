import csv
import json
from pathlib import Path
import shutil
from tempfile import TemporaryDirectory
import unittest

from scripts.daily_operation import (
    PROJECT_ROOT,
    complete_run,
    fail_run,
    initialize_workspace,
    prepare_run,
)


class DailyOperationTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = TemporaryDirectory()
        self.root = Path(self.temporary.name)
        (self.root / "operations").mkdir()
        shutil.copytree(
            PROJECT_ROOT / "operations/templates",
            self.root / "operations/templates",
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_init_is_idempotent_and_never_overwrites_private_files(self) -> None:
        first = initialize_workspace(self.root)
        watchlist = self.root / "operations/private/watchlist.csv"
        watchlist.write_text("private-value\n", encoding="utf-8")
        second = initialize_workspace(self.root)

        self.assertIn("operations/private/state.json", first["created"])
        self.assertIn("operations/private/watchlist.csv", second["existing"])
        self.assertEqual(watchlist.read_text(encoding="utf-8"), "private-value\n")

    def test_prepare_creates_daily_bundle_and_resumes_without_overwrite(self) -> None:
        first = prepare_run(at="2026-08-31T18:30:00+09:00", root=self.root)
        report = self.root / first["report"]
        report.write_text("work in progress", encoding="utf-8")
        second = prepare_run(at="2026-08-31T20:00:00+09:00", root=self.root)

        self.assertEqual(first["run_id"], "2026-08-31")
        self.assertFalse(first["resumed"])
        self.assertTrue(second["resumed"])
        self.assertEqual(second["status"], "in_progress")
        self.assertEqual(report.read_text(encoding="utf-8"), "work in progress")
        for filename in (
            "report.md",
            "orders.csv",
            "sources.csv",
            "pretrade-check.md",
            "handoff.json",
        ):
            self.assertTrue(
                (self.root / "operations/private/runs/2026-08-31" / filename).is_file()
            )

    def test_complete_advances_state_and_is_idempotent(self) -> None:
        prepared = prepare_run(at="2026-08-31T18:30:00+09:00", root=self.root)
        run_dir = self.root / prepared["run_dir"]
        with (run_dir / "orders.csv").open("a", encoding="utf-8", newline="") as file:
            csv.writer(file).writerow(
                [
                    "2026-08-31-1234-BUY",
                    "2026-08-31-1234-monthly",
                    "2026-08-31T18:50:00+09:00",
                    "2026-09-01",
                    "1234",
                    "Example",
                    "BUY",
                    "BUY",
                    "E-1",
                    "LIMIT",
                    "1000",
                    "100",
                    "1.0",
                    "2026-09-01T15:30:00+09:00",
                    "5.0",
                    "PROPOSED",
                ]
            )
        handoff_path = run_dir / "handoff.json"
        handoff = json.loads(handoff_path.read_text(encoding="utf-8"))
        handoff.update(
            {
                "pending_orders": ["2026-08-31-1234-BUY"],
                "pending_reviews": ["1234-quarterly"],
                "data_gaps": ["non-critical industry update"],
                "next_run_at_jst": "2026-09-01T18:30:00+09:00",
            }
        )
        handoff_path.write_text(
            json.dumps(handoff, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

        result = complete_run(
            run_id="2026-08-31",
            completed_at="2026-08-31T19:05:00+09:00",
            source_cutoff="2026-08-31T18:30:00+09:00",
            price_date="2026-08-31",
            summary="completed",
            alert_count=1,
            root=self.root,
        )
        repeated = complete_run(
            run_id="2026-08-31",
            completed_at="2026-08-31T19:05:00+09:00",
            source_cutoff="2026-08-31T18:30:00+09:00",
            price_date="2026-08-31",
            summary="completed",
            alert_count=1,
            root=self.root,
        )

        state = json.loads(
            (self.root / "operations/private/state.json").read_text(encoding="utf-8")
        )
        self.assertEqual(result["order_count"], 1)
        self.assertEqual(result["data_gap_count"], 1)
        self.assertTrue(repeated["already_closed"])
        self.assertEqual(state["last_run_id"], "2026-08-31")
        self.assertEqual(
            state["last_disclosure_cutoff_jst"], "2026-08-31T18:30:00+09:00"
        )
        self.assertEqual(state["pending_reviews"], ["1234-quarterly"])
        with (self.root / "operations/private/run-history.csv").open(
            encoding="utf-8", newline=""
        ) as history_file:
            history = list(csv.DictReader(history_file))
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0]["status"], "completed")
        self.assertEqual(history[0]["order_count"], "1")

    def test_failed_run_does_not_advance_cutoff_and_can_be_resumed(self) -> None:
        prepare_run(at="2026-08-31T18:30:00+09:00", root=self.root)
        fail_run(
            run_id="2026-08-31",
            completed_at="2026-08-31T18:45:00+09:00",
            summary="required source unavailable",
            root=self.root,
        )
        state = json.loads(
            (self.root / "operations/private/state.json").read_text(encoding="utf-8")
        )
        resumed = prepare_run(at="2026-08-31T19:00:00+09:00", root=self.root)
        handoff = json.loads(
            (
                self.root
                / "operations/private/runs/2026-08-31/handoff.json"
            ).read_text(encoding="utf-8")
        )

        self.assertIsNone(state["last_disclosure_cutoff_jst"])
        self.assertEqual(resumed["status"], "in_progress")
        self.assertEqual(handoff["attempt"], 2)
        self.assertEqual(handoff["status"], "in_progress")

    def test_prepare_rejects_datetime_without_offset(self) -> None:
        with self.assertRaises(ValueError):
            prepare_run(at="2026-08-31T18:30:00", root=self.root)


if __name__ == "__main__":
    unittest.main()
