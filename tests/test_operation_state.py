import csv
import json
import os
from pathlib import Path
import shutil
from tempfile import TemporaryDirectory
import unittest

from scripts.operation_state import (
    PROJECT_ROOT,
    initialize_or_migrate_workspace,
    validate_workspace,
)


LEGACY_PORTFOLIO_HEADER = (
    "as_of_jst,code,company,status,entry_date,average_cost,position_cost_pct,"
    "position_market_pct,q0_normalized,current_fraction,score,market_space_score,"
    "reverse_score,thesis_status,kpi_status,last_quarterly_review,next_review,"
    "active_rule_ids,private_log_path\n"
)


class OperationStateTest(unittest.TestCase):
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

    def test_initializes_v2_ledgers_and_secure_permissions(self) -> None:
        result = initialize_or_migrate_workspace(self.root)
        private = self.root / "operations/private"
        state = json.loads((private / "state.json").read_text(encoding="utf-8"))

        self.assertEqual(result["schema_version"], "2.0")
        self.assertEqual(state["schema_version"], "2.0")
        for filename in (
            "trade-event-ledger.csv",
            "recovered-capital-ledger.csv",
            "capital-ledger.csv",
            "corporate-actions.csv",
            "rebuy-restrictions.csv",
            "industry-exposure.csv",
        ):
            self.assertTrue((private / filename).is_file())
        if os.name == "posix":
            self.assertEqual(private.stat().st_mode & 0o777, 0o700)
            self.assertEqual((private / "state.json").stat().st_mode & 0o777, 0o600)
        self.assertEqual(validate_workspace(self.root), [])

    def test_migrates_legacy_state_and_portfolio_without_losing_values(self) -> None:
        private = self.root / "operations/private"
        private.mkdir()
        (private / "runs").mkdir()
        legacy_state = {
            "schema_version": "1.0",
            "timezone": "Asia/Tokyo",
            "schedule": "weekdays 18:30",
            "last_successful_run_at_jst": "2026-08-28T19:00:00+09:00",
            "last_disclosure_cutoff_jst": "2026-08-28T18:30:00+09:00",
            "last_price_date": "2026-08-28",
            "last_run_id": "2026-08-28",
            "pending_reviews": ["1234-quarterly"],
            "pending_orders": ["ticket-1"],
            "data_gaps": [],
            "next_run_at_jst": "2026-08-31T18:30:00+09:00",
        }
        (private / "state.json").write_text(
            json.dumps(legacy_state, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        portfolio_row = (
            "2026-08-28T18:30:00+09:00,1234,Example,OPEN,2026-01-05,1000,"
            "1.0,1.2,1.0,1.0,75,10,12,VALID,ON_TRACK,2026-08-01,"
            "2026-11-01,E-1,operations/private/decisions/example.md\n"
        )
        (private / "portfolio-register.csv").write_text(
            LEGACY_PORTFOLIO_HEADER + portfolio_row, encoding="utf-8"
        )

        first = initialize_or_migrate_workspace(self.root)
        second = initialize_or_migrate_workspace(self.root)
        state = json.loads((private / "state.json").read_text(encoding="utf-8"))
        with (private / "portfolio-register.csv").open(
            encoding="utf-8", newline=""
        ) as source:
            positions = list(csv.DictReader(source))
        with (private / "schema-migration-log.csv").open(
            encoding="utf-8", newline=""
        ) as source:
            migrations = list(csv.DictReader(source))

        self.assertEqual(state["schema_version"], "2.0")
        self.assertEqual(state["pending_orders"], ["ticket-1"])
        self.assertEqual(state["unreconciled_ticket_ids"], ["ticket-1"])
        self.assertEqual(positions[0]["code"], "1234")
        self.assertEqual(positions[0]["average_cost"], "1000")
        self.assertEqual(positions[0]["five_x_taken"], "")
        self.assertIn("operations/private/state.json", first["migrated"])
        self.assertIsNotNone(first["backup_dir"])
        self.assertEqual(second["migrated"], [])
        self.assertEqual(len(migrations), 1)

    def test_rejects_duplicate_ledger_ids_and_invalid_position_state(self) -> None:
        initialize_or_migrate_workspace(self.root)
        private = self.root / "operations/private"
        portfolio = private / "portfolio-register.csv"
        with portfolio.open(encoding="utf-8", newline="") as source:
            fields = next(csv.reader(source))
        invalid_position = dict.fromkeys(fields, "")
        invalid_position.update(
            {
                "code": "1234",
                "five_x_taken": "maybe",
                "sb_consecutive_quarters": "-1",
                "corporate_action_factor": "0",
            }
        )
        with portfolio.open("a", encoding="utf-8", newline="") as destination:
            csv.DictWriter(destination, fieldnames=fields).writerow(invalid_position)

        ledger = private / "trade-event-ledger.csv"
        with ledger.open(encoding="utf-8", newline="") as source:
            ledger_fields = next(csv.reader(source))
        event = dict.fromkeys(ledger_fields, "")
        event["event_id"] = "event-1"
        with ledger.open("a", encoding="utf-8", newline="") as destination:
            writer = csv.DictWriter(destination, fieldnames=ledger_fields)
            writer.writerow(event)
            writer.writerow(event)

        errors = validate_workspace(self.root)

        self.assertIn("1234: five_x_taken must be boolean", errors)
        self.assertIn("1234: sb_consecutive_quarters must be >= 0", errors)
        self.assertIn("1234: corporate_action_factor must be > 0", errors)
        self.assertIn(
            "duplicate trade-event-ledger.csv event_id: event-1", errors
        )


if __name__ == "__main__":
    unittest.main()
