import csv
import hashlib
import json
import shutil
import unittest
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory

from scripts.nightly_artifacts import next_trading_date, validate_nightly_artifacts
from scripts.nightly_operation import finalize_nightly_run, start_nightly_run
from scripts.operation_state import PROJECT_ROOT, initialize_or_migrate_workspace
from scripts.order_ticket import propose_order

FIXTURES = PROJECT_ROOT / "tests/fixtures/official-source-scan"


class NightlyOperationTest(unittest.TestCase):
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
        path = self.root / "operations/private/portfolio-register.csv"
        with path.open(encoding="utf-8", newline="") as source:
            fields = next(csv.reader(source))
        row = dict.fromkeys(fields, "")
        row.update({"code": code, "company": "Example", "status": "OPEN"})
        with path.open("a", encoding="utf-8", newline="") as destination:
            csv.DictWriter(destination, fieldnames=fields).writerow(row)

    def _start(self) -> dict[str, object]:
        private = self.root / "operations/private"
        targets: set[str] = set()
        for filename, field, statuses in (
            ("portfolio-register.csv", "status", {"OPEN", "ACTIVE", "HELD"}),
            ("watchlist.csv", "active", {"TRUE", "1", "YES", "ACTIVE"}),
        ):
            with (private / filename).open(encoding="utf-8", newline="") as source:
                for row in csv.DictReader(source):
                    if str(row.get(field, "")).strip().upper() in statuses:
                        targets.add(str(row.get("code", "")).strip())
        snapshot = private / "market-snapshots/2026-08-31/daily/180000"
        snapshot.mkdir(parents=True)
        raw = snapshot / "yahoo-raw.json"
        raw.write_text('{"batches": []}\n', encoding="utf-8")
        (snapshot / "manifest.json").write_text(
            json.dumps(
                {
                    "status": "COMPLETED",
                    "scope": "daily",
                    "price_date": "2026-08-31",
                    "target_codes": sorted(targets),
                    "coverage_ratio": 1.0,
                    "critical_failures": [],
                    "raw_sha256": hashlib.sha256(raw.read_bytes()).hexdigest(),
                }
            ),
            encoding="utf-8",
        )
        market_state_path = private / "market-data-state.json"
        market_state = json.loads(market_state_path.read_text(encoding="utf-8"))
        market_state["last_snapshot_path"] = snapshot.relative_to(self.root).as_posix()
        market_state_path.write_text(json.dumps(market_state), encoding="utf-8")
        state_path = private / "state.json"
        state = json.loads(state_path.read_text(encoding="utf-8"))
        state["last_backup_at_jst"] = "2026-07-31T18:00:00+09:00"
        backup = private / "backups/operation-test.zip"
        backup.parent.mkdir()
        backup.write_bytes(b"verified test backup")
        state["last_backup_path"] = backup.relative_to(self.root).as_posix()
        state["last_backup_sha256"] = hashlib.sha256(backup.read_bytes()).hexdigest()
        state["last_backup_verified_before_encryption"] = True
        state_path.write_text(json.dumps(state), encoding="utf-8")
        return start_nightly_run(
            at="2026-08-31T18:45:00+09:00",
            cutoff="2026-08-31T18:30:00+09:00",
            fixture_dir=FIXTURES,
            root=self.root,
        )

    def _complete_research_queue(self, run_dir: Path) -> None:
        path = run_dir / "research-queue.json"
        queue = json.loads(path.read_text(encoding="utf-8"))
        for task in queue["tasks"]:
            task["status"] = "COMPLETED"
            task["evidence_source_ids"] = task["source_ids"] or ["manual-jpx"]
        path.write_text(
            json.dumps(queue, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )

    def test_start_confirms_next_trade_date_and_builds_due_work(self) -> None:
        self._add_holding()
        result = self._start()
        run_dir = self.root / str(result["run_dir"])
        plan = json.loads((run_dir / "work-plan.json").read_text(encoding="utf-8"))
        with (run_dir / "next-day-actions.csv").open(
            encoding="utf-8", newline=""
        ) as source:
            actions = list(csv.DictReader(source))

        self.assertEqual(result["source_scan_status"], "COMPLETED")
        self.assertTrue(result["paper_go"])
        self.assertFalse(result["live_go"])
        self.assertTrue(result["trading_calendar_confirmed"])
        self.assertEqual(result["next_trading_date"], "2026-09-01")
        self.assertIn("2026-08-31-daily-event", result["due_task_ids"])
        self.assertEqual(actions[0]["code"], "1234")
        self.assertEqual(actions[0]["trade_date"], "2026-09-01")
        self.assertEqual(plan["status"], "IN_PROGRESS")

    def test_cash_equity_calendar_skips_holiday_trading_division(self) -> None:
        run_dir = self.root / "calendar"
        run_dir.mkdir()
        (run_dir / "trading-calendar.json").write_text(
            json.dumps(
                {
                    "rows": [
                        {"date": "2026-09-01", "holiday_division": "3"},
                        {"date": "2026-09-02", "holiday_division": "1"},
                    ]
                }
            ),
            encoding="utf-8",
        )

        next_date, confirmed = next_trading_date(
            run_dir, date.fromisoformat("2026-08-31")
        )

        self.assertTrue(confirmed)
        self.assertEqual(next_date, "2026-09-02")

    def test_paper_proposal_updates_action_handoff_and_trade_ledger_once(self) -> None:
        self._add_holding()
        started = self._start()
        run_dir = self.root / str(started["run_dir"])
        self._complete_research_queue(run_dir)
        actions_path = run_dir / "next-day-actions.csv"
        with actions_path.open(encoding="utf-8", newline="") as source:
            reader = csv.DictReader(source)
            fields = list(reader.fieldnames or [])
            actions = list(reader)
        actions[0].update(
            {
                "next_action": "BUY",
                "rule_ids": "E-1;E-2",
                "trigger_condition": "全エントリー条件を充足",
                "evidence_source_ids": "tdnet-20260831000001",
            }
        )
        with actions_path.open("w", encoding="utf-8", newline="") as destination:
            writer = csv.DictWriter(destination, fieldnames=fields)
            writer.writeheader()
            writer.writerows(actions)

        args = {
            "run_id": "2026-08-31",
            "run_token": str(started["run_token"]),
            "action_id": "2026-08-31-1234-next",
            "code": "1234",
            "company": "Example",
            "side": "BUY",
            "action": "BUY",
            "rule_ids": "E-1;E-2",
            "trade_date": "2026-09-01",
            "limit_price": "1000",
            "quantity": "100",
            "position_pct": "1.0",
            "valid_until": "2026-09-01T15:30:00+09:00",
            "participation_cap_pct": "5.0",
            "decision_id": "operations/private/decisions/2026-08-31-1234.md",
            "at": "2026-08-31T19:00:00+09:00",
            "root": self.root,
        }
        decision_path = self.root / args["decision_id"]
        decision_path.write_text("# decision\n", encoding="utf-8")
        first = propose_order(**args)
        second = propose_order(**args)

        self.assertEqual(first["status"], "PAPER_PROPOSED")
        self.assertFalse(first["broker_submitted"])
        self.assertTrue(second["already_proposed"])
        with (run_dir / "orders.csv").open(encoding="utf-8", newline="") as source:
            orders = list(csv.DictReader(source))
        with (self.root / "operations/private/trade-event-ledger.csv").open(
            encoding="utf-8", newline=""
        ) as source:
            events = list(csv.DictReader(source))
        handoff = json.loads((run_dir / "handoff.json").read_text(encoding="utf-8"))
        self.assertEqual(len(orders), 1)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["event_type"], "PAPER_PROPOSED")
        self.assertIn(first["ticket_id"], handoff["pending_orders"])

    def test_deferred_research_cannot_support_an_order(self) -> None:
        self._add_holding()
        started = self._start()
        run_dir = self.root / str(started["run_dir"])
        queue_path = run_dir / "research-queue.json"
        queue = json.loads(queue_path.read_text(encoding="utf-8"))
        queue["tasks"][0]["status"] = "DEFERRED"
        queue_path.write_text(json.dumps(queue), encoding="utf-8")
        handoff_path = run_dir / "handoff.json"
        handoff = json.loads(handoff_path.read_text(encoding="utf-8"))
        handoff["pending_reviews"] = [queue["tasks"][0]["task_id"]]
        handoff_path.write_text(json.dumps(handoff), encoding="utf-8")
        actions_path = run_dir / "next-day-actions.csv"
        with actions_path.open(encoding="utf-8", newline="") as source:
            reader = csv.DictReader(source)
            fields = list(reader.fieldnames or [])
            actions = list(reader)
        actions[0].update(
            {
                "next_action": "BUY",
                "rule_ids": "E-1",
                "evidence_source_ids": "tdnet-20260831000001",
            }
        )
        with actions_path.open("w", encoding="utf-8", newline="") as destination:
            writer = csv.DictWriter(destination, fieldnames=fields)
            writer.writeheader()
            writer.writerows(actions)
        decision_path = self.root / "operations/private/decisions/deferred.md"
        decision_path.write_text("# decision\n", encoding="utf-8")

        with self.assertRaisesRegex(ValueError, "must be completed"):
            propose_order(
                run_id="2026-08-31",
                run_token=str(started["run_token"]),
                action_id="2026-08-31-1234-next",
                code="1234",
                company="Example",
                side="BUY",
                action="BUY",
                rule_ids="E-1",
                trade_date="2026-09-01",
                limit_price="1000",
                quantity="100",
                position_pct="1",
                valid_until="2026-09-01T15:30:00+09:00",
                participation_cap_pct="5",
                decision_id="operations/private/decisions/deferred.md",
                at="2026-08-31T19:00:00+09:00",
                root=self.root,
            )

    def test_paused_policy_blocks_order_ticket(self) -> None:
        self._add_holding()
        started = self._start()
        run_dir = self.root / str(started["run_dir"])
        self._complete_research_queue(run_dir)
        policy_path = self.root / "operations/private/operation-policy.json"
        policy = json.loads(policy_path.read_text(encoding="utf-8"))
        policy["operation_mode"] = "PAUSED"
        policy_path.write_text(
            json.dumps(policy, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        actions_path = run_dir / "next-day-actions.csv"
        with actions_path.open(encoding="utf-8", newline="") as source:
            reader = csv.DictReader(source)
            fields = list(reader.fieldnames or [])
            actions = list(reader)
        actions[0].update(
            {
                "next_action": "BUY",
                "rule_ids": "E-1",
                "evidence_source_ids": "source-1",
            }
        )
        with actions_path.open("w", encoding="utf-8", newline="") as destination:
            writer = csv.DictWriter(destination, fieldnames=fields)
            writer.writeheader()
            writer.writerows(actions)
        with self.assertRaisesRegex(PermissionError, "blocks order tickets"):
            propose_order(
                run_id="2026-08-31",
                run_token=str(started["run_token"]),
                action_id="2026-08-31-1234-next",
                code="1234",
                company="Example",
                side="BUY",
                action="BUY",
                rule_ids="E-1",
                trade_date="2026-09-01",
                limit_price="1000",
                quantity="100",
                position_pct="1",
                valid_until="2026-09-01T15:30:00+09:00",
                participation_cap_pct="5",
                decision_id="decision-1",
                at="2026-08-31T19:00:00+09:00",
                root=self.root,
            )

    def test_trade_action_without_order_is_rejected(self) -> None:
        self._add_holding()
        started = self._start()
        run_dir = self.root / str(started["run_dir"])
        actions_path = run_dir / "next-day-actions.csv"
        with actions_path.open(encoding="utf-8", newline="") as source:
            reader = csv.DictReader(source)
            fields = list(reader.fieldnames or [])
            actions = list(reader)
        actions[0].update(
            {
                "next_action": "BUY",
                "rule_ids": "E-1",
                "evidence_source_ids": "source-1",
            }
        )
        with actions_path.open("w", encoding="utf-8", newline="") as destination:
            writer = csv.DictWriter(destination, fieldnames=fields)
            writer.writeheader()
            writer.writerows(actions)
        errors = validate_nightly_artifacts(
            root=self.root,
            run_id="2026-08-31",
            handoff=json.loads((run_dir / "handoff.json").read_text(encoding="utf-8")),
            coverage=json.loads((run_dir / "coverage.json").read_text(encoding="utf-8")),
            orders=[],
        )
        self.assertTrue(any("requires a matching order ticket" in error for error in errors))

    def test_honest_uncertainty_text_is_not_treated_as_a_template_marker(self) -> None:
        self._add_holding()
        started = self._start()
        run_dir = self.root / str(started["run_dir"])
        (run_dir / "research-results.md").write_text(
            "# 夜間調査結果\n\n- 状態: `COMPLETED`\n"
            "- 情報カットオフ（JST）: 2026-08-31T18:30:00+09:00\n"
            "- 翌営業日: 2026-09-01\n- 対象件数: 0\n"
            "- 未解決事項: なし\n\n将来の影響は未確定であり、追加確認する。\n",
            encoding="utf-8",
        )
        (run_dir / "global-risk.md").write_text(
            "# 世界情勢・市場リスク確認\n\n- 状態: `COMPLETED`\n"
            "- 情報カットオフ（JST）: 2026-08-31T18:30:00+09:00\n"
            "- 判定: `NORMAL`\n\n公的情報を確認済み。\n",
            encoding="utf-8",
        )

        errors = validate_nightly_artifacts(
            root=self.root,
            run_id="2026-08-31",
            handoff=json.loads((run_dir / "handoff.json").read_text(encoding="utf-8")),
            coverage=json.loads((run_dir / "coverage.json").read_text(encoding="utf-8")),
            orders=[],
        )

        self.assertFalse(any("unresolved marker: 未確定" in error for error in errors))

    def test_completed_backup_task_requires_an_existing_private_archive(self) -> None:
        self._add_holding()
        started = self._start()
        run_dir = self.root / str(started["run_dir"])
        plan_path = run_dir / "work-plan.json"
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
        plan["status"] = "COMPLETED"
        backup_task = next(
            task for task in plan["tasks"] if task["task_type"] == "operations_backup"
        )
        backup_task["status"] = "COMPLETED"
        backup_task["evidence_source_ids"] = [
            "internal:backup:operations/private/backups/missing.zip"
        ]
        plan_path.write_text(json.dumps(plan), encoding="utf-8")

        errors = validate_nightly_artifacts(
            root=self.root,
            run_id="2026-08-31",
            handoff=json.loads((run_dir / "handoff.json").read_text(encoding="utf-8")),
            coverage=json.loads((run_dir / "coverage.json").read_text(encoding="utf-8")),
            orders=[],
        )

        self.assertTrue(any("archive does not exist" in error for error in errors))

    def test_empty_universe_is_blocked_before_run_creation(self) -> None:
        with self.assertRaisesRegex(PermissionError, "daily universe is empty"):
            self._start()
        self.assertFalse(
            (self.root / "operations/private/runs/2026-08-31").exists()
        )

    def test_failed_finalize_restores_next_run_handoff(self) -> None:
        self._add_holding()
        started = self._start()
        run_dir = self.root / str(started["run_dir"])
        handoff_path = run_dir / "handoff.json"
        before = json.loads(handoff_path.read_text(encoding="utf-8"))

        with self.assertRaises(ValueError):
            finalize_nightly_run(
                run_id="2026-08-31",
                run_token=str(started["run_token"]),
                completed_at="2026-08-31T19:05:00+09:00",
                source_cutoff="2026-08-31T18:30:00+09:00",
                price_date="2026-08-31",
                summary="must fail",
                root=self.root,
            )

        after = json.loads(handoff_path.read_text(encoding="utf-8"))
        self.assertEqual(after.get("next_run_at_jst"), before.get("next_run_at_jst"))


if __name__ == "__main__":
    unittest.main()
