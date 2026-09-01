from __future__ import annotations

import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from scripts.live_gate_evidence import (
    evaluate_historical_replay,
    evaluate_point_in_time,
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


if __name__ == "__main__":
    unittest.main()
