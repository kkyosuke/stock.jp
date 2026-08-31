import json
from pathlib import Path
import shutil
from tempfile import TemporaryDirectory
import unittest

from scripts.daily_operation import prepare_run
from scripts.operation_backup import create_backup, stage_restore, verify_backup
from scripts.operation_bootstrap import check_readiness
from scripts.operation_smoke import simulate_operations
from scripts.operation_state import PROJECT_ROOT, initialize_or_migrate_workspace
from scripts.operation_watchdog import watchdog_status


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
        restored_state = (
            self.root / restored["destination"] / "state.json"
        )
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
            root=self.root, environ={}, fixture_dir=FIXTURES
        )
        self.assertIn(
            "LIVE requires OPERATION_BACKUP_AGE_RECIPIENT", readiness["blockers"]
        )

    def test_watchdog_detects_missed_schedule_and_stale_run(self) -> None:
        state_path = self.root / "operations/private/state.json"
        state = json.loads(state_path.read_text(encoding="utf-8"))
        state["last_run_id"] = "2026-08-28"
        state["next_run_at_jst"] = "2026-08-31T18:30:00+09:00"
        state_path.write_text(
            json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        missed = watchdog_status(
            at="2026-08-31T21:00:01+09:00", root=self.root
        )
        self.assertEqual(missed["status"], "MISSED")

        prepare_run(at="2026-09-01T18:30:00+09:00", root=self.root)
        stale = watchdog_status(at="2026-09-02T01:00:00+09:00", root=self.root)
        self.assertEqual(stale["status"], "STALE_RUN")
        self.assertEqual(stale["stale_runs"], ["2026-09-01"])

    def test_bootstrap_blocks_missing_credentials_and_fixture_mode_is_ready(self) -> None:
        blocked = check_readiness(root=self.root, environ={})
        ready = check_readiness(root=self.root, environ={}, fixture_dir=FIXTURES)
        nonexistent_fixture = check_readiness(
            root=self.root,
            environ={},
            fixture_dir=self.root / "does-not-exist",
        )
        self.assertFalse(blocked["ready"])
        self.assertTrue(any("EDINET_API_KEY" in item for item in blocked["blockers"]))
        self.assertEqual(set(blocked["credentials"]), {"edinet"})
        self.assertTrue(ready["ready"])
        self.assertFalse(nonexistent_fixture["ready"])
        self.assertEqual(ready["broker_submission"], "HUMAN_ONLY")

    def test_twenty_day_smoke_finishes_without_broker_submission(self) -> None:
        result = simulate_operations(
            days=20, start_date="2026-09-01", root=self.root
        )
        self.assertEqual(result["status"], "PASSED")
        self.assertEqual(result["completed_runs"], 20)
        self.assertEqual(result["consecutive_successful_runs"], 20)
        self.assertEqual(result["broker_orders_submitted"], 0)


if __name__ == "__main__":
    unittest.main()
