import csv
import json
from pathlib import Path
import shutil
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import MagicMock, patch
from urllib.error import URLError

from scripts.daily_operation import complete_run, prepare_run
from scripts.official_source_scan import SOURCE_FIELDS, _append_sources, _request_json, scan_sources
from scripts.operation_state import PROJECT_ROOT, initialize_or_migrate_workspace


FIXTURES = PROJECT_ROOT / "tests/fixtures/official-source-scan"


class OfficialSourceScanTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = TemporaryDirectory()
        self.root = Path(self.temporary.name)
        (self.root / "operations").mkdir()
        shutil.copytree(
            PROJECT_ROOT / "operations/templates",
            self.root / "operations/templates",
        )
        initialize_or_migrate_workspace(self.root)
        portfolio = self.root / "operations/private/portfolio-register.csv"
        with portfolio.open(encoding="utf-8", newline="") as source:
            fields = next(csv.reader(source))
        position = dict.fromkeys(fields, "")
        position.update({"code": "1234", "company": "Example", "status": "OPEN"})
        with portfolio.open("a", encoding="utf-8", newline="") as destination:
            csv.DictWriter(destination, fieldnames=fields).writerow(position)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _prepare(self) -> dict[str, object]:
        return prepare_run(at="2026-08-31T18:30:00+09:00", root=self.root)

    def test_fixture_scan_builds_evidence_health_and_research_queue(self) -> None:
        prepared = self._prepare()
        result = scan_sources(
            run_id="2026-08-31",
            run_token=str(prepared["run_token"]),
            cutoff="2026-08-31T18:30:00+09:00",
            at="2026-08-31T18:45:00+09:00",
            fixture_dir=FIXTURES,
            root=self.root,
            environ={},
        )
        run_dir = self.root / str(prepared["run_dir"])
        health = json.loads(
            (run_dir / "provider-health.json").read_text(encoding="utf-8")
        )
        coverage = json.loads(
            (run_dir / "coverage.json").read_text(encoding="utf-8")
        )
        queue = json.loads(
            (run_dir / "research-queue.json").read_text(encoding="utf-8")
        )
        with (run_dir / "sources.csv").open(encoding="utf-8", newline="") as source:
            sources = list(csv.DictReader(source))

        self.assertEqual(result["status"], "COMPLETED")
        self.assertEqual(health["providers"]["edinet"]["status"], "OK")
        self.assertEqual(health["providers"]["jquants_tdnet"]["status"], "OK")
        self.assertEqual(coverage["official_sources"]["edinet"]["status"], "CHECKED")
        self.assertEqual(coverage["official_sources"]["tdnet"]["status"], "CHECKED")
        self.assertEqual(coverage["official_sources"]["company_ir"]["status"], "PENDING")
        self.assertIn("tdnet-20260831000001", {row["source_id"] for row in sources})
        self.assertIn("edinet-S100TEST", {row["source_id"] for row in sources})
        self.assertIn(
            "manual-company-ir-2026-08-31-1234",
            {task["task_id"] for task in queue["tasks"]},
        )
        self.assertTrue((run_dir / "raw-sources/edinet-2026-08-31.json").is_file())

    def test_missing_credentials_create_blocking_gaps_without_network(self) -> None:
        prepared = self._prepare()
        result = scan_sources(
            run_id="2026-08-31",
            run_token=str(prepared["run_token"]),
            cutoff="2026-08-31T18:30:00+09:00",
            at="2026-08-31T18:45:00+09:00",
            root=self.root,
            environ={},
        )
        run_dir = self.root / str(prepared["run_dir"])
        coverage = json.loads(
            (run_dir / "coverage.json").read_text(encoding="utf-8")
        )

        self.assertEqual(result["status"], "PARTIAL")
        self.assertGreaterEqual(result["blocking_gap_count"], 4)
        self.assertEqual(
            coverage["official_sources"]["edinet"]["status"], "UNAVAILABLE"
        )
        self.assertEqual(
            coverage["official_sources"]["tdnet"]["status"], "UNAVAILABLE"
        )
        self.assertTrue(
            all(gap["severity"] == "CRITICAL" for gap in coverage["data_gaps"])
        )

    def test_source_append_deduplicates_within_one_batch(self) -> None:
        path = self.root / "sources.csv"
        with path.open("w", encoding="utf-8", newline="") as destination:
            csv.DictWriter(destination, fieldnames=SOURCE_FIELDS).writeheader()
        row = dict.fromkeys(SOURCE_FIELDS, "")
        row["source_id"] = "same-source"

        added = _append_sources(path, [row, dict(row)])

        with path.open(encoding="utf-8", newline="") as source:
            rows = list(csv.DictReader(source))
        self.assertEqual(added, 1)
        self.assertEqual(len(rows), 1)

    @patch("scripts.official_source_scan.time_module.sleep")
    @patch("scripts.official_source_scan.urlopen")
    def test_transient_network_error_is_retried(self, mocked_urlopen, mocked_sleep) -> None:
        response = MagicMock()
        response.__enter__.return_value.read.return_value = b'{"results": []}'
        mocked_urlopen.side_effect = [URLError("temporary"), response]

        payload = _request_json(
            base_url="https://example.com",
            path="documents.json",
            params={},
            headers={},
            timeout=1,
            backoff_seconds=0,
        )

        self.assertEqual(payload, {"results": []})
        self.assertEqual(mocked_urlopen.call_count, 2)
        mocked_sleep.assert_called_once()

    def test_carried_critical_gap_keeps_scan_partial(self) -> None:
        prepared = self._prepare()
        run_dir = self.root / str(prepared["run_dir"])
        gap = {
            "gap_id": "carried-company-ir",
            "status": "OPEN",
            "severity": "CRITICAL",
            "source": "company_ir",
            "impact": "manual IR check remains incomplete",
            "retry_after_jst": "2026-09-01T18:30:00+09:00",
            "resolved_at_jst": None,
            "resolution_evidence": None,
        }
        for name in ("coverage.json", "handoff.json"):
            path = run_dir / name
            document = json.loads(path.read_text(encoding="utf-8"))
            document["data_gaps"] = [gap]
            path.write_text(
                json.dumps(document, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )

        result = scan_sources(
            run_id="2026-08-31",
            run_token=str(prepared["run_token"]),
            cutoff="2026-08-31T18:30:00+09:00",
            at="2026-08-31T18:45:00+09:00",
            fixture_dir=FIXTURES,
            root=self.root,
            environ={},
        )

        self.assertEqual(result["status"], "PARTIAL")
        self.assertEqual(result["blocking_gap_count"], 1)

    def test_agent_can_close_fixture_run_after_manual_primary_checks(self) -> None:
        prepared = self._prepare()
        scan_sources(
            run_id="2026-08-31",
            run_token=str(prepared["run_token"]),
            cutoff="2026-08-31T18:30:00+09:00",
            at="2026-08-31T18:45:00+09:00",
            fixture_dir=FIXTURES,
            root=self.root,
            environ={},
        )
        run_dir = self.root / str(prepared["run_dir"])
        with (run_dir / "sources.csv").open("a", encoding="utf-8", newline="") as destination:
            writer = csv.DictWriter(destination, fieldnames=(
                "source_id", "category", "code", "title", "published_at_jst",
                "retrieved_at_jst", "url", "primary_source", "used_for_decision", "notes",
            ))
            common = {
                "published_at_jst": "2026-08-31T18:00:00+09:00",
                "retrieved_at_jst": "2026-08-31T18:50:00+09:00",
                "primary_source": "true",
                "used_for_decision": "true",
                "notes": "manual fixture evidence",
            }
            writer.writerow({
                **common,
                "source_id": "company-ir-1234",
                "category": "company_ir",
                "code": "1234",
                "title": "Company IR checked",
                "url": "https://example.com/company-ir/1234",
            })
            writer.writerow({
                **common,
                "source_id": "jpx-notices-check",
                "category": "jpx",
                "code": "",
                "title": "JPX notices checked",
                "url": "https://www.jpx.co.jp/",
            })

        queue_path = run_dir / "research-queue.json"
        queue = json.loads(queue_path.read_text(encoding="utf-8"))
        for task in queue["tasks"]:
            task["status"] = "COMPLETED"
            task["evidence_source_ids"] = task["source_ids"] or [
                "company-ir-1234" if task["code"] else "jpx-notices-check"
            ]
        queue_path.write_text(
            json.dumps(queue, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        coverage_path = run_dir / "coverage.json"
        coverage = json.loads(coverage_path.read_text(encoding="utf-8"))
        coverage["status"] = "COMPLETED"
        coverage["source_window"]["through_inclusive_jst"] = (
            "2026-08-31T18:30:00+09:00"
        )
        coverage["universe"]["holdings"]["checked"] = ["1234"]
        coverage["official_sources"]["company_ir"]["status"] = "CHECKED"
        coverage["official_sources"]["jpx"]["status"] = "CHECKED"
        coverage["completed_at_jst"] = "2026-08-31T19:00:00+09:00"
        coverage_path.write_text(
            json.dumps(coverage, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        report_path = run_dir / "report.md"
        report = report_path.read_text(encoding="utf-8")
        report = (
            report.replace("- 実行状態: `IN-PROGRESS`", "- 実行状態: `COMPLETED`")
            .replace(
                "- 今回の開示カットオフ（JST）: 未確定",
                "- 今回の開示カットオフ（JST）: 2026-08-31T18:30:00+09:00",
            )
            .replace("- 株価基準日: 未確定", "- 株価基準日: 2026-08-31")
            .replace("- 総合結果: 未確定", "- 総合結果: 確認完了")
        )
        report_path.write_text(report, encoding="utf-8")

        result = complete_run(
            run_id="2026-08-31",
            completed_at="2026-08-31T19:00:00+09:00",
            source_cutoff="2026-08-31T18:30:00+09:00",
            price_date="2026-08-31",
            summary="fixture scan and manual primary checks completed",
            run_token=str(prepared["run_token"]),
            root=self.root,
        )

        self.assertEqual(result["status"], "completed")


if __name__ == "__main__":
    unittest.main()
