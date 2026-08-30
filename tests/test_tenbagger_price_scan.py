from datetime import date
import unittest

from scripts.tenbagger_price_scan import (
    Price,
    data_quality_flags,
    earliest_episode,
    month_ends,
    parse_prices,
)


class TenbaggerPriceScanTest(unittest.TestCase):
    def test_omits_zero_volume_holiday_placeholders(self) -> None:
        payload = {
            "chart": {
                "result": [
                    {
                        "timestamp": [1577833200, 1578006000],
                        "meta": {"exchangeTimezoneName": "Asia/Tokyo"},
                        "indicators": {
                            "quote": [
                                {
                                    "close": [100.0, 101.0],
                                    "volume": [0, 1000],
                                }
                            ]
                        },
                    }
                ]
            }
        }

        prices = parse_prices(payload)

        self.assertEqual(len(prices), 1)
        self.assertEqual(prices[0].close, 101.0)

    def test_uses_next_close_and_records_the_later_collapse(self) -> None:
        daily = [
            Price(date(2020, 1, 31), 8.0, 100),
            Price(date(2020, 2, 3), 10.0, 100),
            Price(date(2020, 2, 28), 12.0, 100),
            Price(date(2021, 1, 29), 70.0, 100),
            Price(date(2022, 1, 31), 100.0, 100),
            Price(date(2022, 6, 30), 120.0, 100),
            Price(date(2023, 1, 31), 60.0, 100),
        ]

        episode = earliest_episode(
            daily,
            month_ends(daily),
            scan_start=date(2020, 1, 1),
            data_end=date(2023, 2, 3),
            years=3,
            max_entry_lag_days=7,
        )

        self.assertIsNotNone(episode)
        assert episode is not None
        self.assertEqual(episode["entry_date"], "2020-02-03")
        self.assertEqual(episode["hit_date"], "2022-01-31")
        self.assertEqual(episode["latest_multiple"], 6.0)
        self.assertFalse(episode["retained_10x"])
        self.assertEqual(episode["post_hit_max_drawdown"], -0.5)

    def test_rejects_an_entry_that_is_too_far_from_the_evaluation_date(self) -> None:
        daily = [
            Price(date(2020, 1, 31), 1.0, 100),
            Price(date(2020, 2, 10), 1.0, 100),
            Price(date(2021, 1, 29), 10.0, 100),
            Price(date(2023, 1, 31), 10.0, 100),
        ]

        episode = earliest_episode(
            daily,
            month_ends(daily),
            scan_start=date(2020, 1, 1),
            data_end=date(2023, 2, 10),
            years=3,
            max_entry_lag_days=7,
        )

        self.assertIsNone(episode)

    def test_flags_impossible_returns_and_long_gaps(self) -> None:
        daily = [
            Price(date(2020, 1, 1), 10.0, 100),
            Price(date(2020, 1, 2), 30.0, 100),
            Price(date(2020, 3, 2), 30.0, 100),
        ]

        flags = data_quality_flags(
            daily,
            max_adjacent_multiple=2.5,
            max_trading_gap_days=45,
        )

        self.assertEqual(
            [flag["kind"] for flag in flags],
            ["impossible_adjacent_return", "long_trading_gap"],
        )


if __name__ == "__main__":
    unittest.main()
