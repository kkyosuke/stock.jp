from __future__ import annotations

from pathlib import Path
import tomllib
import unittest


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/daily-stock-prices.yml"
OPERATION_TEST_WORKFLOW = ROOT / ".github/workflows/operation-tests.yml"


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

    def test_pat_authentication_triggers_required_pr_check(self) -> None:
        configuration = self.text.index("- name: Check automation PAT configuration")
        pull_request = self.text.index("- name: Create or update data pull request")
        auto_merge = self.text.index("- name: Enable auto-merge after successful validation")
        tests = self.text.index("- name: Run unit and integration tests before merge")
        self.assertLess(configuration, tests)
        self.assertLess(tests, pull_request)
        self.assertLess(pull_request, auto_merge)
        self.assertIn("token: ${{ secrets.AUTOMATION_PAT }}", self.text)
        self.assertIn("GH_TOKEN: ${{ secrets.AUTOMATION_PAT }}", self.text)
        self.assertNotIn("gh workflow run operation-tests.yml", self.text)
        self.assertNotIn("token: ${{ github.token }}", self.text)
        self.assertNotIn("create-github-app-token", self.text)
        self.assertNotIn("AUTOMATION_APP_", self.text)

        operation_tests = (
            ROOT / ".github/workflows/operation-tests.yml"
        ).read_text(encoding="utf-8")
        self.assertIn("pull_request:", operation_tests)

    def test_workflow_keeps_github_token_read_only(self) -> None:
        self.assertIn("permissions:\n  contents: read", self.text)
        self.assertNotIn("contents: write", self.text)
        self.assertNotIn("pull-requests: write", self.text)
        self.assertNotIn("actions: write", self.text)
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

    def test_operation_tests_install_every_project_runtime_dependency(self) -> None:
        operation_test_text = OPERATION_TEST_WORKFLOW.read_text(encoding="utf-8")
        with (ROOT / "pyproject.toml").open("rb") as source:
            project_dependencies = tomllib.load(source)["project"]["dependencies"]

        install_start = operation_test_text.index(
            "- name: Install runtime and skill-validator dependencies"
        )
        unit_test_start = operation_test_text.index(
            "- name: Unit tests",
            install_start,
        )
        install_step = operation_test_text[install_start:unit_test_start]

        for dependency in project_dependencies:
            with self.subTest(dependency=dependency):
                self.assertIn(dependency, install_step)

    def test_readme_installs_every_project_runtime_dependency(self) -> None:
        readme_text = (ROOT / "README.md").read_text(encoding="utf-8")
        with (ROOT / "pyproject.toml").open("rb") as source:
            project_dependencies = tomllib.load(source)["project"]["dependencies"]

        for dependency in project_dependencies:
            with self.subTest(dependency=dependency):
                self.assertIn(dependency, readme_text)


if __name__ == "__main__":
    unittest.main()
