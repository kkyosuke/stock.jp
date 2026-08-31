import csv
import json
import shutil
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from scripts.daily_operation import prepare_run
from scripts.operation_backup import create_backup, stage_restore, verify_backup
from scripts.operation_bootstrap import check_readiness
from scripts.operation_smoke import simulate_operations
from scripts.operation_state import PROJECT_ROOT, initialize_or_migrate_workspace
from scripts.operation_watchdog import watchdog_status
from tests.operation_test_support import write_price_archive


FIXTURES = PROJECT_ROOT / "tests/fixtures/official-source-scan"


class OperationResilienceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = TemporaryDirectory()
        self.root = Path(self.temporary.name)
        (self.root / "operations").mkdir()
        shutil.copytree(
            PROJECT_ROOT / "operations/templates", self.root / "operations/templates"
        )
        initialize_or_migrate_workspace(self.root)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _add_holding(self, code: str = "1234") -> None:
        portfolio = self.root / "operations/private/portfolio-register.csv"
        with portfolio.open(encoding="utf-8", newline="") as source:
            fields = next(csv.reader(source))
        row = dict.fromkeys(fields, "")
        row.update({"code": code, "company": "Example", "status": "OPEN"})
        with portfolio.open("a", encoding="utf-8", newline="") as destination:
            csv.DictWriter(destination, fieldnames=fields).writerow(row)

    def test_plaintext_backup_is_explicit_verified_and_restored_to_staging(self) -> None:
        with self.assertRaisesRegex(PermissionError, "explicit --allow-plaintext"):
            create_backup(at="2026-08-31T20:00:00+09:00", root=self.root)
        created = create_backup(
            at="2026-08-31T20:00:00+09:00",
            allow_plaintext=True,
            root=self.root,
        )
        verified = verify_backup(archive=Path(created["archive"]), root=self.root)
        restored = stage_restore(
            archive=Path(created["archive"]),
            destination=Path("operations/private/restores/drill-20260831"),
            root=self.root,
        )
        restored_state = self.root / restored["destination"] / "state.json"
        self.assertTrue(verified["valid"])
        self.assertGreater(verified["file_count"], 10)
        self.assertTrue(restored_state.is_file())
        self.assertEqual(restored["status"], "STAGED")

    def test_live_backup_refuses_plaintext(self) -> None:
        path = self.root / "operations/private/operation-policy.json"
        policy = json.loads(path.read_text(encoding="utf-8"))
        policy["operation_mode"] = "LIVE"
        path.write_text(
            json.dumps(policy, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        with self.assertRaisesRegex(PermissionError, "LIVE backup requires age"):
            create_backup(
                at="2026-08-31T20:00:00+09:00",
                allow_plaintext=True,
                root=self.root,
            )
        readiness = check_readiness(
            root=self.root,
            environ={},
            fixture_dir=FIXTURES,
            at="2026-09-01T18:30:00+09:00",
        )
        self.assertIn(
            "LIVE requires OPERATION_BACKUP_AGE_RECIPIENT", readiness["blockers"]
        )

    def test_live_readiness_requires_real_private_evidence_files(self) -> None:
        self._add_holding()
        write_price_archive(self.root, ["1234"])
        create_backup(
            at="2026-09-01T17:00:00+09:00",
            allow_plaintext=True,
            root=self.root,
        )
        policy_path = self.root / "operations/private/operation-policy.json"
        policy = json.loads(policy_path.read_text(encoding="utf-8"))
        policy["operation_mode"] = "LIVE"
        for gate in policy["live_gates"]:
            policy["live_gates"][gate] = True
            policy["live_gate_evidence"][gate] = (
                f"operations/private/evidence/{gate}.md"
            )
        policy["approval"] = {
            "approved_by": "human",
            "approved_at_jst": "2026-09-01T18:00:00+09:00",
            "evidence_path": "operations/private/approvals/live.md",
        }
        policy_path.write_text(json.dumps(policy), encoding="utf-8")

        readiness = check_readiness(
            root=self.root,
            environ={
                "EDINET_API_KEY": "fixture-key",
                "OPERATION_BACKUP_AGE_RECIPIENT": "age1test",
            },
            at="2026-09-01T18:30:00+09:00",
        )

        self.assertFalse(readiness["live_go"])
        self.assertTrue(
            any(
                "evidence file is missing or empty" in blocker
                for blocker in readiness["live_blockers"]
            )
        )

    def test_watchdog_detects_missed_schedule_and_stale_run(self) -> None:
        state_path = self.root / "operations/private/state.json"
        state = json.loads(state_path.read_text(encoding="utf-8"))
        state["last_run_id"] = "2026-08-28"
        state["next_run_at_jst"] = "2026-08-31T18:30:00+09:00"
        state_path.write_text(
            json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        missed = watchdog_status(at="2026-08-31T21:00:01+09:00", root=self.root)
        self.assertEqual(missed["status"], "MISSED")

        prepare_run(at="2026-09-01T18:30:00+09:00", root=self.root)
        stale = watchdog_status(at="2026-09-02T01:00:00+09:00", root=self.root)
        self.assertEqual(stale["status"], "STALE_RUN")
        self.assertEqual(stale["stale_runs"], ["2026-09-01"])

    def test_paper_can_use_manual_primary_source_fallback(self) -> None:
        self._add_holding()
        write_price_archive(self.root, ["1234"])
        create_backup(
            at="2026-09-01T17:00:00+09:00",
            allow_plaintext=True,
            root=self.root,
        )

        result = check_readiness(
            root=self.root, environ={}, at="2026-09-01T18:30:00+09:00"
        )

        self.assertTrue(result["paper_go"])
        self.assertTrue(result["ready"])
        self.assertFalse(result["live_go"])
        self.assertEqual(result["price_snapshot"]["target_codes"], ["1234"])
        self.assertIn(
            "missing environment credential: EDINET_API_KEY",
            result["automatic_source_blockers"],
        )

    def test_paper_blocks_missing_target_price_or_backup_archive(self) -> None:
        self._add_holding()
        write_price_archive(self.root, ["5678"])
        created = create_backup(
            at="2026-09-01T17:00:00+09:00",
            allow_plaintext=True,
            root=self.root,
        )
        missing_target = check_readiness(
            root=self.root, environ={}, at="2026-09-01T18:30:00+09:00"
        )
        self.assertIn(
            "tracked Yahoo archive does not cover every active target",
            missing_target["paper_blockers"],
        )

        write_price_archive(self.root, ["1234"])
        (self.root / created["archive"]).unlink()
        missing_backup = check_readiness(
            root=self.root, environ={}, at="2026-09-01T18:30:00+09:00"
        )
        self.assertIn(
            "latest verified operation backup archive is missing",
            missing_backup["paper_blockers"],
        )

    def test_bootstrap_rejects_naive_reference_time(self) -> None:
        with self.assertRaisesRegex(ValueError, "UTC offset"):
            check_readiness(root=self.root, environ={}, at="2026-09-01T18:30:00")

    def test_twenty_day_smoke_finishes_without_broker_submission(self) -> None:
        result = simulate_operations(days=20, start_date="2026-09-01", root=self.root)
        self.assertEqual(result["status"], "PASSED")
        self.assertEqual(result["completed_runs"], 20)
        self.assertEqual(result["consecutive_successful_runs"], 20)
        self.assertEqual(result["broker_orders_submitted"], 0)


if __name__ == "__main__":
    unittest.main()
