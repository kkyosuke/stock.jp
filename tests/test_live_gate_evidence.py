from __future__ import annotations

import hashlib
import json
import csv
from datetime import date, datetime
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from scripts.live_gate_evidence import (
    evaluate_historical_replay,
    evaluate_official_coverage,
    evaluate_paper_duration,
    evaluate_point_in_time,
    evaluate_repository_recovery,
    evaluate_shadow_run,
    write_private_evidence,
)


def _write(path: Path, value: str) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value, encoding="utf-8")
    return hashlib.sha256(value.encode()).hexdigest()


def _valid_point_manifest(root: Path) -> Path:
    artifacts = []
    for role in ("source_snapshot", "trade_log", "metrics"):
        relative = f"data/replay/{role}.json"
        digest = _write(root / relative, f'{{"role":"{role}"}}\n')
        artifacts.append({"role": role, "path": relative, "sha256": digest})
    manifest = {
        "schema_version": "1.0",
        "status": "COMPLETED",
        "generated_at_jst": "2026-09-01T20:00:00+09:00",
        "as_of_date": "2026-08-31",
        "universe": {
            "required_count": 3800,
            "evaluated_count": 3800,
            "point_in_time_security_master": True,
            "includes_delisted": True,
            "includes_mergers": True,
            "includes_corporate_actions": True,
        },
        "quality": {
            "missing_hard_gate_inputs": 0,
            "lookahead_violations": 0,
        },
        "artifacts": artifacts,
    }
    path = root / "data/historical-replay/point-in-time-validation.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(manifest), encoding="utf-8")
    return path


class PointInTimeEvidenceTest(unittest.TestCase):

    def test_missing_manifest_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            result = evaluate_point_in_time(root=Path(directory))
        self.assertFalse(result["eligible"])
        self.assertIn("manifest is missing", result["blockers"][0])

    def test_complete_hashed_full_universe_is_eligible(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _valid_point_manifest(root)
            result = evaluate_point_in_time(root=root)
        self.assertTrue(result["eligible"], result["blockers"])
        self.assertEqual(result["metrics"]["verified_artifact_count"], 3)

    def test_artifact_tampering_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _valid_point_manifest(root)
            (root / "data/replay/trade_log.json").write_text("tampered\n")
            result = evaluate_point_in_time(root=root)
        self.assertFalse(result["eligible"])
        self.assertIn(
            "artifact hash mismatch: data/replay/trade_log.json",
            result["blockers"],
        )

    def test_manifest_outside_project_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            container = Path(directory)
            root = container / "project"
            root.mkdir()
            manifest = container / "manifest.json"
            manifest.write_text("{}", encoding="utf-8")
            result = evaluate_point_in_time(root=root, manifest_path=manifest)
        self.assertFalse(result["eligible"])
        self.assertEqual(
            result["blockers"], ["manifest path must stay under project root"]
        )

    def test_only_successful_private_evidence_can_be_written(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _valid_point_manifest(root)
            result = evaluate_point_in_time(root=root)
            target = root / "operations/private/evidence/point-in-time.json"
            write_private_evidence(root=root, path=target, result=result)
            self.assertTrue(target.is_file())
            with self.assertRaisesRegex(ValueError, "must be under"):
                write_private_evidence(
                    root=root, path=root / "public.json", result=result
                )
            result["eligible"] = False
            with self.assertRaisesRegex(ValueError, "ineligible"):
                write_private_evidence(root=root, path=target, result=result)


class HistoricalReplayEvidenceTest(unittest.TestCase):
    def _valid_replay(self, root: Path) -> tuple[Path, Path]:
        point_manifest = _valid_point_manifest(root)
        artifacts = []
        for role in ("trade_log", "metrics", "monthly_returns"):
            relative = f"data/replay/final-{role}.csv"
            digest = _write(root / relative, f"{role}\n")
            artifacts.append({"role": role, "path": relative, "sha256": digest})
        result = {
            "schema_version": "1.0",
            "status": "COMPLETED",
            "rule_version": "v0.4",
            "generated_at_jst": "2026-09-01T20:00:00+09:00",
            "period": {"from": "2025-01-01", "through": "2026-08-31"},
            "point_in_time_manifest_sha256": hashlib.sha256(
                point_manifest.read_bytes()
            ).hexdigest(),
            "quality": {"missing_hard_gate_inputs": 0, "lookahead_violations": 0},
            "metrics": {
                "trade_count": 42,
                "total_return_pct": 8.1,
                "max_drawdown_pct": -12.5,
                "benchmark_return_pct": 5.0,
            },
            "artifacts": artifacts,
        }
        result_path = root / "data/historical-replay/replay-result-2025-2026.json"
        result_path.write_text(json.dumps(result), encoding="utf-8")
        review = {
            "schema_version": "1.0",
            "decision": "ACCEPT",
            "rule_version": "v0.4",
            "accepted_by": "portfolio-owner",
            "accepted_at_jst": "2026-09-02T08:00:00+09:00",
            "replay_result_sha256": hashlib.sha256(
                result_path.read_bytes()
            ).hexdigest(),
            "drawdown_reviewed": True,
            "concentration_loss_reviewed": True,
            "data_limitations_reviewed": True,
        }
        review_path = root / "operations/private/evidence/historical-replay-review.json"
        review_path.parent.mkdir(parents=True)
        review_path.write_text(json.dumps(review), encoding="utf-8")
        return result_path, review_path

    def test_complete_replay_and_bound_human_review_are_eligible(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._valid_replay(root)
            result = evaluate_historical_replay(root=root)
        self.assertTrue(result["eligible"], result["blockers"])
        self.assertEqual(result["metrics"]["trade_count"], 42)

    def test_replay_without_private_acceptance_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _, review_path = self._valid_replay(root)
            review_path.unlink()
            result = evaluate_historical_replay(root=root)
        self.assertFalse(result["eligible"])
        self.assertTrue(
            any("private replay review is missing" in item for item in result["blockers"])
        )

    def test_review_is_invalid_after_replay_changes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            result_path, _ = self._valid_replay(root)
            replay = json.loads(result_path.read_text(encoding="utf-8"))
            replay["metrics"]["max_drawdown_pct"] = -30.0
            result_path.write_text(json.dumps(replay), encoding="utf-8")
            result = evaluate_historical_replay(root=root)
        self.assertFalse(result["eligible"])
        self.assertIn(
            "private review does not bind the current replay result",
            result["blockers"],
        )


class PaperDurationEvidenceTest(unittest.TestCase):
    FIELDS = [
        "run_id",
        "attempt",
        "started_at_jst",
        "completed_at_jst",
        "status",
        "operation_mode",
        "active_rule_version",
        "source_cutoff_jst",
        "price_date",
        "report_path",
        "order_count",
        "alert_count",
        "data_gap_count",
        "next_run_at_jst",
        "summary",
    ]

    def _write_history(self, root: Path, *, short: bool = False) -> Path:
        private = root / "operations/private"
        private.mkdir(parents=True)
        rows = []
        for offset in range(13):
            year = 2025 + (8 + offset) // 12
            month = (8 + offset) % 12 + 1
            run_date = date(year, month, 1)
            if short and offset > 2:
                break
            run_id = run_date.isoformat()
            report = private / f"runs/{run_id}/report.md"
            _write(report, f"# {run_id}\n")
            rows.append(
                {
                    "run_id": run_id,
                    "attempt": "1",
                    "started_at_jst": f"{run_id}T18:30:00+09:00",
                    "completed_at_jst": f"{run_id}T19:00:00+09:00",
                    "status": "COMPLETED",
                    "operation_mode": "PAPER",
                    "active_rule_version": "v0.4",
                    "source_cutoff_jst": f"{run_id}T18:30:00+09:00",
                    "price_date": run_id,
                    "report_path": f"operations/private/runs/{run_id}/report.md",
                    "order_count": "0",
                    "alert_count": "0",
                    "data_gap_count": "0",
                    "next_run_at_jst": "",
                    "summary": "test",
                }
            )
        history = private / "run-history.csv"
        with history.open("w", encoding="utf-8", newline="") as target:
            writer = csv.DictWriter(target, fieldnames=self.FIELDS)
            writer.writeheader()
            writer.writerows(rows)
        return history

    def test_365_days_with_every_calendar_month_is_eligible(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._write_history(root)
            result = evaluate_paper_duration(root=root)
        self.assertTrue(result["eligible"], result["blockers"])
        self.assertEqual(result["metrics"]["elapsed_days"], 365)
        self.assertEqual(result["metrics"]["calendar_month_count"], 13)

    def test_short_paper_history_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._write_history(root, short=True)
            result = evaluate_paper_duration(root=root)
        self.assertFalse(result["eligible"])
        self.assertIn(
            "v0.4 PAPER elapsed duration is less than 365 days", result["blockers"]
        )

    def test_missing_report_is_not_counted(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._write_history(root)
            (root / "operations/private/runs/2025-12-01/report.md").unlink()
            result = evaluate_paper_duration(root=root)
        self.assertFalse(result["eligible"])
        self.assertIn("completed run report is missing: 2025-12-01", result["blockers"])

    def test_any_live_run_before_promotion_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            history = self._write_history(root)
            text = history.read_text(encoding="utf-8")
            history.write_text(text.replace(",PAPER,v0.4,", ",LIVE,v0.4,", 1))
            result = evaluate_paper_duration(root=root)
        self.assertFalse(result["eligible"])
        self.assertIn("pre-promotion LIVE run exists in run history", result["blockers"])


class ShadowRunEvidenceTest(unittest.TestCase):
    def _write_shadow(self, root: Path, *, count: int = 20) -> Path:
        private = root / "operations/private"
        private.mkdir(parents=True)
        rows = []
        day = date(2026, 8, 1)
        trading_dates = []
        while len(trading_dates) < count:
            if day.weekday() < 5:
                trading_dates.append(day)
            day = date.fromordinal(day.toordinal() + 1)
        for run_date in trading_dates:
            run_id = run_date.isoformat()
            price = root / f"data/daily-prices/{run_date.year}/{run_id}.csv"
            _write(price, "code,close\n1301,1000\n")
            run_dir = private / "runs" / run_id
            _write(run_dir / "report.md", f"# {run_id}\n")
            _write(run_dir / "orders.csv", "ticket_id,code,side,trade_date\n")
            rows.append(
                {
                    "run_id": run_id,
                    "attempt": "1",
                    "started_at_jst": f"{run_id}T18:30:00+09:00",
                    "completed_at_jst": f"{run_id}T19:00:00+09:00",
                    "status": "COMPLETED",
                    "operation_mode": "PAPER",
                    "active_rule_version": "v0.4",
                    "source_cutoff_jst": f"{run_id}T18:30:00+09:00",
                    "price_date": run_id,
                    "report_path": f"operations/private/runs/{run_id}/report.md",
                    "order_count": "0",
                    "alert_count": "0",
                    "data_gap_count": "0",
                    "next_run_at_jst": "",
                    "summary": "test",
                }
            )
        history = private / "run-history.csv"
        with history.open("w", encoding="utf-8", newline="") as target:
            writer = csv.DictWriter(
                target, fieldnames=PaperDurationEvidenceTest.FIELDS
            )
            writer.writeheader()
            writer.writerows(rows)
        return history

    @patch("scripts.live_gate_evidence.validate_run_artifacts")
    def test_latest_20_tracked_sessions_are_eligible(self, validate) -> None:
        validate.return_value = {}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._write_shadow(root)
            result = evaluate_shadow_run(root=root)
        self.assertTrue(result["eligible"], result["blockers"])
        self.assertEqual(result["metrics"]["consecutive_session_count"], 20)
        self.assertEqual(validate.call_count, 20)

    @patch("scripts.live_gate_evidence.validate_run_artifacts")
    def test_nineteen_sessions_fail_closed(self, validate) -> None:
        validate.return_value = {}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._write_shadow(root, count=19)
            result = evaluate_shadow_run(root=root)
        self.assertFalse(result["eligible"])
        self.assertIn(
            "fewer than 20 completed v0.4 PAPER trading sessions", result["blockers"]
        )

    @patch("scripts.live_gate_evidence.validate_run_artifacts")
    def test_duplicate_ticket_across_runs_is_rejected(self, validate) -> None:
        validate.return_value = {}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            history = self._write_shadow(root)
            with history.open(encoding="utf-8", newline="") as source:
                rows = list(csv.DictReader(source))
            for row in rows[:2]:
                row["order_count"] = "1"
                order = root / f"operations/private/runs/{row['run_id']}/orders.csv"
                _write(
                    order,
                    "ticket_id,code,side,trade_date\n"
                    f"DUPLICATE,1301,BUY,{row['price_date']}\n",
                )
            with history.open("w", encoding="utf-8", newline="") as target:
                writer = csv.DictWriter(
                    target, fieldnames=PaperDurationEvidenceTest.FIELDS
                )
                writer.writeheader()
                writer.writerows(rows)
            result = evaluate_shadow_run(root=root)
        self.assertFalse(result["eligible"])
        self.assertIn(
            "duplicate ticket_id across shadow runs: DUPLICATE", result["blockers"]
        )


class OfficialCoverageEvidenceTest(unittest.TestCase):
    def _write_coverage(self, root: Path) -> list[str]:
        history = ShadowRunEvidenceTest()._write_shadow(root)
        with history.open(encoding="utf-8", newline="") as source:
            rows = list(csv.DictReader(source))
        for row in rows:
            run_id = row["run_id"]
            run_dir = root / f"operations/private/runs/{run_id}"
            coverage = {
                "schema_version": "1.0",
                "run_id": run_id,
                "status": "COMPLETED",
                "official_sources": {
                    "tdnet": {"required": True, "status": "CHECKED"},
                    "edinet": {"required": True, "status": "CHECKED"},
                    "company_ir": {"required": False, "status": "NOT_APPLICABLE"},
                    "jpx": {"required": True, "status": "CHECKED"},
                },
                "data_gaps": [],
            }
            health = {
                "schema_version": "1.0",
                "run_id": run_id,
                "input_mode": "LIVE_NETWORK",
                "status": "COMPLETED",
                "providers": {
                    "edinet": {"status": "OK", "request_count": 1}
                },
                "blocking_gaps": [],
            }
            _write(run_dir / "coverage.json", json.dumps(coverage))
            _write(run_dir / "provider-health.json", json.dumps(health))
            _write(
                run_dir / "sources.csv",
                "source_id,category,primary_source,used_for_decision,url\n"
                "tdnet-check,tdnet,true,true,https://www.release.tdnet.info/\n"
                "edinet-check,edinet,true,true,https://api.edinet-fsa.go.jp/\n"
                "jpx-check,jpx,true,true,https://www.jpx.co.jp/\n",
            )
        latest_cutoff = rows[-1]["source_cutoff_jst"]
        watermarks = {
            "sources": {
                name: {"last_successful_cutoff_jst": latest_cutoff}
                for name in ("tdnet", "edinet", "jpx")
            }
        }
        _write(
            root / "operations/private/source-watermarks.json",
            json.dumps(watermarks),
        )
        return [row["run_id"] for row in rows]

    @patch("scripts.live_gate_evidence.evaluate_shadow_run")
    def test_20_live_network_official_checks_are_eligible(self, shadow) -> None:
        shadow.return_value = {"eligible": True}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._write_coverage(root)
            result = evaluate_official_coverage(root=root)
        self.assertTrue(result["eligible"], result["blockers"])
        self.assertEqual(result["metrics"]["checked_run_counts"]["edinet"], 20)

    @patch("scripts.live_gate_evidence.evaluate_shadow_run")
    def test_fixture_scan_is_rejected(self, shadow) -> None:
        shadow.return_value = {"eligible": True}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            run_ids = self._write_coverage(root)
            path = root / f"operations/private/runs/{run_ids[-1]}/provider-health.json"
            health = json.loads(path.read_text(encoding="utf-8"))
            health["input_mode"] = "FIXTURE"
            path.write_text(json.dumps(health), encoding="utf-8")
            result = evaluate_official_coverage(root=root)
        self.assertFalse(result["eligible"])
        self.assertTrue(any("not a live network scan" in item for item in result["blockers"]))

    @patch("scripts.live_gate_evidence.evaluate_shadow_run")
    def test_non_primary_decision_source_is_rejected(self, shadow) -> None:
        shadow.return_value = {"eligible": True}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            run_ids = self._write_coverage(root)
            path = root / f"operations/private/runs/{run_ids[-1]}/sources.csv"
            text = path.read_text(encoding="utf-8").replace(
                "tdnet-check,tdnet,true,true", "tdnet-check,tdnet,false,true"
            )
            path.write_text(text, encoding="utf-8")
            result = evaluate_official_coverage(root=root)
        self.assertFalse(result["eligible"])
        self.assertTrue(any("non-primary tdnet" in item for item in result["blockers"]))


class RepositoryRecoveryEvidenceTest(unittest.TestCase):
    def _write_drill(self, root: Path) -> Path:
        commit = "1" * 40
        public_commit = "2" * 40
        drill = {
            "schema_version": "1.0",
            "drill_id": "recovery-2026-q3",
            "operator": "portfolio-owner",
            "repository_visibility": "PRIVATE",
            "started_at_jst": "2026-08-31T20:00:00+09:00",
            "completed_at_jst": "2026-08-31T21:00:00+09:00",
            "source_private_commit": commit,
            "recovered_private_commit": commit,
            "source_public_submodule_commit": public_commit,
            "recovered_public_submodule_commit": public_commit,
            "checks": {
                name: True
                for name in (
                    "clean_clone_with_submodules",
                    "workspace_setup",
                    "state_validation",
                    "paper_bootstrap_check",
                    "latest_successful_run",
                    "latest_handoff",
                    "all_ledgers",
                    "unreconciled_orders",
                    "private_remote_restore",
                    "access_controlled_mirror_restore",
                )
            },
            "result_notes": "clean checkout and protected mirror both reconciled",
        }
        path = root / "operations/private/evidence/recovery-drill.json"
        _write(path, json.dumps(drill))
        return path

    def test_recent_complete_recovery_is_eligible(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._write_drill(root)
            result = evaluate_repository_recovery(
                root=root,
                at=datetime.fromisoformat("2026-09-01T09:00:00+09:00"),
            )
        self.assertTrue(result["eligible"], result["blockers"])
        self.assertEqual(result["metrics"]["passed_check_count"], 10)

    def test_stale_recovery_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._write_drill(root)
            result = evaluate_repository_recovery(
                root=root,
                at=datetime.fromisoformat("2026-12-15T09:00:00+09:00"),
            )
        self.assertFalse(result["eligible"])
        self.assertIn("recovery drill is older than 90 days", result["blockers"])

    def test_commit_mismatch_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = self._write_drill(root)
            drill = json.loads(path.read_text(encoding="utf-8"))
            drill["recovered_private_commit"] = "3" * 40
            path.write_text(json.dumps(drill), encoding="utf-8")
            result = evaluate_repository_recovery(
                root=root,
                at=datetime.fromisoformat("2026-09-01T09:00:00+09:00"),
            )
        self.assertFalse(result["eligible"])
        self.assertIn("recovered private commit does not match source", result["blockers"])


if __name__ == "__main__":
    unittest.main()
