from __future__ import annotations

from copy import deepcopy
import unittest

from scripts.validate_scheduled_price_update import (
    ScheduledUpdateError,
    validate_summary,
)


def _valid_summary() -> dict:
    return {
        "universe": {"count": 3713},
        "fetch": {"success_count": 3713, "error_count": 0},
        "latest_trading_date": "2026-09-01",
        "latest_session": {
            "quote_count": 3669,
            "no_quote_count": 44,
            "fetch_error_count": 0,
        },
        "changed_files": [
            "2026/2026-08-31.csv",
            "2026/2026-09-01.csv",
            "latest.json",
        ],
    }


class ScheduledPriceUpdateValidationTest(unittest.TestCase):
    def test_zero_error_recent_update_is_eligible(self) -> None:
        validate_summary(_valid_summary(), lookback_days=7)

    def test_any_fetch_error_requires_human_review(self) -> None:
        summary = deepcopy(_valid_summary())
        summary["fetch"] = {"success_count": 3712, "error_count": 1}
        summary["latest_session"]["fetch_error_count"] = 1
        with self.assertRaisesRegex(ScheduledUpdateError, "zero fetch errors"):
            validate_summary(summary, lookback_days=7)

    def test_old_session_rewrite_is_rejected(self) -> None:
        summary = deepcopy(_valid_summary())
        summary["changed_files"].append("2026/2026-08-01.csv")
        with self.assertRaisesRegex(ScheduledUpdateError, "outside the lookback"):
            validate_summary(summary, lookback_days=7)

    def test_unexpected_generated_path_is_rejected(self) -> None:
        summary = deepcopy(_valid_summary())
        summary["changed_files"].append("docs/report.md")
        with self.assertRaisesRegex(ScheduledUpdateError, "unexpected generated path"):
            validate_summary(summary, lookback_days=7)

    def test_latest_counts_must_cover_the_universe(self) -> None:
        summary = deepcopy(_valid_summary())
        summary["latest_session"]["no_quote_count"] = 43
        with self.assertRaisesRegex(ScheduledUpdateError, "does not match"):
            validate_summary(summary, lookback_days=7)

    def test_csv_change_requires_the_manifest(self) -> None:
        summary = deepcopy(_valid_summary())
        summary["changed_files"].remove("latest.json")
        with self.assertRaisesRegex(ScheduledUpdateError, "latest.json"):
            validate_summary(summary, lookback_days=7)


if __name__ == "__main__":
    unittest.main()
