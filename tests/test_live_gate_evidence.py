from __future__ import annotations

import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from scripts.live_gate_evidence import (
    evaluate_point_in_time,
    write_private_evidence,
)


def _write(path: Path, value: str) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value, encoding="utf-8")
    return hashlib.sha256(value.encode()).hexdigest()


class PointInTimeEvidenceTest(unittest.TestCase):
    def _valid_manifest(self, root: Path) -> Path:
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

    def test_missing_manifest_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            result = evaluate_point_in_time(root=Path(directory))
        self.assertFalse(result["eligible"])
        self.assertIn("manifest is missing", result["blockers"][0])

    def test_complete_hashed_full_universe_is_eligible(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._valid_manifest(root)
            result = evaluate_point_in_time(root=root)
        self.assertTrue(result["eligible"], result["blockers"])
        self.assertEqual(result["metrics"]["verified_artifact_count"], 3)

    def test_artifact_tampering_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._valid_manifest(root)
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
            self._valid_manifest(root)
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


if __name__ == "__main__":
    unittest.main()
