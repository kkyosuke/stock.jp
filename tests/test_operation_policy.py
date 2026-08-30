import copy
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from scripts.daily_operation import initialize_workspace
from scripts.operation_policy import load_policy, policy_status, validate_policy


ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / "operations/templates/operation-policy.json"


class OperationPolicyTest(unittest.TestCase):
    def setUp(self) -> None:
        self.policy = json.loads(TEMPLATE.read_text(encoding="utf-8"))

    def test_default_is_valid_paper_with_no_live_order(self) -> None:
        status = policy_status(self.policy)

        self.assertTrue(status["valid"])
        self.assertEqual(status["operation_mode"], "PAPER")
        self.assertEqual(status["active_rule_version"], "v0.2")
        self.assertEqual(status["ticket_status"], "PAPER_PROPOSED")
        self.assertFalse(status["live_orders_allowed"])

    def test_live_requires_every_gate_and_approval(self) -> None:
        policy = copy.deepcopy(self.policy)
        policy["operation_mode"] = "LIVE"
        blocked = policy_status(policy)
        for gate in policy["live_gates"]:
            policy["live_gates"][gate] = True
            policy["live_gate_evidence"][gate] = f"operations/private/evidence/{gate}.md"
        policy["approval"] = {
            "approved_by": "human",
            "approved_at_jst": "2027-09-01T00:00:00+09:00",
            "evidence_path": "operations/private/approvals/live.md",
        }
        allowed = policy_status(policy)

        self.assertFalse(blocked["live_orders_allowed"])
        self.assertTrue(allowed["live_orders_allowed"])
        self.assertEqual(allowed["ticket_status"], "PROPOSED")

    def test_v03_needs_separate_holdout_promotion(self) -> None:
        policy = copy.deepcopy(self.policy)
        policy["operation_mode"] = "LIVE"
        policy["active_rule_version"] = "v0.3"
        for gate in policy["live_gates"]:
            policy["live_gates"][gate] = True
            policy["live_gate_evidence"][gate] = f"operations/private/evidence/{gate}.md"
        policy["approval"] = {
            "approved_by": "human",
            "approved_at_jst": "2027-09-01T00:00:00+09:00",
            "evidence_path": "operations/private/approvals/live.md",
        }

        self.assertIn("v03_holdout_promotion", policy_status(policy)["live_gate_failures"])
        policy["v03_holdout_promotion"] = True
        self.assertTrue(policy_status(policy)["live_orders_allowed"])

    def test_invalid_policy_is_rejected(self) -> None:
        self.policy["broker_submission"] = "AUTOMATIC"
        self.assertIn(
            "broker_submission must be HUMAN_ONLY", validate_policy(self.policy)
        )

    def test_true_live_gate_without_evidence_is_blocked(self) -> None:
        self.policy["operation_mode"] = "LIVE"
        for gate in self.policy["live_gates"]:
            self.policy["live_gates"][gate] = True
        failures = policy_status(self.policy)["live_gate_failures"]

        self.assertIn(
            "live_gate_evidence.point_in_time_full_universe_validation", failures
        )

    def test_init_copies_policy_without_overwriting(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "operations").mkdir()
            templates = root / "operations/templates"
            templates.mkdir()
            for name in (
                "daily-run-state-template.json",
                "operation-policy.json",
                "rule-review-log.csv",
                "watchlist.csv",
                "portfolio-register.csv",
                "run-history-template.csv",
            ):
                (templates / name).write_bytes(
                    (ROOT / "operations/templates" / name).read_bytes()
                )
            initialize_workspace(root)
            private_policy = root / "operations/private/operation-policy.json"
            original = load_policy(private_policy)
            private_policy.write_text('{"private": true}\n', encoding="utf-8")
            initialize_workspace(root)

            self.assertEqual(original["operation_mode"], "PAPER")
            self.assertEqual(
                private_policy.read_text(encoding="utf-8"), '{"private": true}\n'
            )


if __name__ == "__main__":
    unittest.main()
