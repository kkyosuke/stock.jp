from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[1]
GOVERNANCE = ROOT / "docs/operation-governance-v0.1.md"


class OperationGovernanceTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.text = GOVERNANCE.read_text(encoding="utf-8")

    def test_local_links_resolve(self) -> None:
        for target in re.findall(r"\[[^]]+\]\(([^)]+)\)", self.text):
            if "://" in target:
                continue
            path = target.split("#", 1)[0]
            self.assertTrue((GOVERNANCE.parent / path).resolve().exists(), target)

    def test_go_live_and_rule_review_are_explicit(self) -> None:
        for phrase in (
            "現在の運用モード: `PAPER`",
            "2025年通年と2026年1月1日から8月31日まで",
            "historical-replay-2025-2026.md",
            "20営業日連続",
            "発注権限は `HUMAN_ONLY`",
            "月次",
            "四半期",
            "暦年終了時",
            "同じ未見期間を見て閾値を再調整しない",
        ):
            self.assertIn(phrase, self.text)

    def test_paper_baseline_and_ticket_boundary_are_explicit(self) -> None:
        self.assertIn("`v0.2` は現行ベースライン", self.text)
        self.assertIn("`v0.3` はシャドー比較専用", self.text)
        self.assertIn("`PAPER_PROPOSED`", self.text)
        self.assertIn("証券会社へ入力しない", self.text)


if __name__ == "__main__":
    unittest.main()
