from __future__ import annotations

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/daily-stock-prices.yml"


class DailyStockPriceWorkflowTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.text = WORKFLOW.read_text(encoding="utf-8")

    def test_validation_finishes_before_pull_request_creation(self) -> None:
        validation = self.text.index("- name: Validate generated archive")
        tests = self.text.index("- name: Run unit and integration tests before merge")
        pull_request = self.text.index("- name: Create or update data pull request")
        self.assertLess(validation, tests)
        self.assertLess(tests, pull_request)
        self.assertIn("python -m unittest discover -s tests -v", self.text)
        self.assertIn("python -m compileall -q scripts tests", self.text)
        self.assertIn("broker_orders_submitted", (ROOT / "scripts/operation_smoke.py").read_text())

    def test_only_a_created_or_updated_pr_enables_auto_merge(self) -> None:
        self.assertIn("steps.data-pr.outputs.pull-request-number != ''", self.text)
        self.assertIn('gh pr merge "$PR_NUMBER"', self.text)
        for option in ("--auto", "--squash", "--delete-branch"):
            self.assertIn(option, self.text)

    def test_workflow_has_only_repository_write_permissions(self) -> None:
        self.assertIn("contents: write", self.text)
        self.assertIn("pull-requests: write", self.text)
        self.assertNotIn("id-token: write", self.text)


if __name__ == "__main__":
    unittest.main()
