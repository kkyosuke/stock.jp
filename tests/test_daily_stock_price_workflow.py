from __future__ import annotations

from pathlib import Path
import tomllib
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
        self.assertIn("validate_scheduled_price_update.py", self.text)
        self.assertIn("python -m unittest discover -s tests -v", self.text)
        self.assertIn("python -m compileall -q scripts tests", self.text)
        self.assertIn("broker_orders_submitted", (ROOT / "scripts/operation_smoke.py").read_text())

    def test_only_a_created_or_updated_pr_enables_auto_merge(self) -> None:
        self.assertIn("steps.data-pr.outputs.pull-request-number != ''", self.text)
        self.assertIn('gh pr merge "$PR_NUMBER"', self.text)
        for option in ("--auto", "--squash", "--delete-branch"):
            self.assertIn(option, self.text)

    def test_created_pr_dispatches_required_check_before_auto_merge(self) -> None:
        pull_request = self.text.index("- name: Create or update data pull request")
        dispatch = self.text.index("- name: Dispatch required operation tests")
        auto_merge = self.text.index("- name: Enable auto-merge after successful validation")
        self.assertLess(pull_request, dispatch)
        self.assertLess(dispatch, auto_merge)
        self.assertIn("gh workflow run operation-tests.yml", self.text)
        self.assertIn('--ref "$PR_BRANCH"', self.text)

        operation_tests = (
            ROOT / ".github/workflows/operation-tests.yml"
        ).read_text(encoding="utf-8")
        self.assertIn("workflow_dispatch:", operation_tests)

    def test_workflow_has_required_repository_permissions(self) -> None:
        self.assertIn("actions: write", self.text)
        self.assertIn("contents: write", self.text)
        self.assertIn("pull-requests: write", self.text)
        self.assertNotIn("id-token: write", self.text)

    def test_workflow_installs_every_project_runtime_dependency(self) -> None:
        with (ROOT / "pyproject.toml").open("rb") as source:
            project_dependencies = tomllib.load(source)["project"]["dependencies"]

        install_start = self.text.index("- name: Install dependencies")
        collect_start = self.text.index(
            "- name: Collect all current TSE domestic stocks",
            install_start,
        )
        install_step = self.text[install_start:collect_start]

        for dependency in project_dependencies:
            with self.subTest(dependency=dependency):
                self.assertIn(dependency, install_step)


if __name__ == "__main__":
    unittest.main()
