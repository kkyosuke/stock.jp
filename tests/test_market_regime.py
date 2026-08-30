import unittest

from scripts.market_regime import MarketRegimeInput, evaluate_market_regime


def inputs(**overrides) -> MarketRegimeInput:
    values = {
        "as_of": "2026-08-31",
        "topix_close": 101,
        "topix_ma200": 100,
        "growth_close": 101,
        "growth_ma200": 100,
        "breadth_pct": 50,
        "nikkei_vi": 20,
        "nikkei_vi_p80_3y": 20,
        "leading_ci": 101,
        "leading_ci_3m_ago": 100,
    }
    values.update(overrides)
    return MarketRegimeInput(**values)


class MarketRegimeTest(unittest.TestCase):
    def test_five_positive_components_are_normal(self) -> None:
        result = evaluate_market_regime(inputs())

        self.assertEqual(result.components, {f"M{n}": 1 for n in range(1, 6)})
        self.assertEqual(result.score, 5)
        self.assertEqual(result.state, "NORMAL")
        self.assertEqual(result.entry_multiplier, 1.0)

    def test_three_positive_components_are_caution(self) -> None:
        result = evaluate_market_regime(
            inputs(growth_close=99, leading_ci=99)
        )

        self.assertEqual(result.score, 3)
        self.assertEqual(result.state, "CAUTION")
        self.assertEqual(result.entry_multiplier, 0.5)

    def test_one_positive_component_is_stress(self) -> None:
        result = evaluate_market_regime(
            inputs(
                topix_close=99,
                growth_close=99,
                breadth_pct=49.9,
                nikkei_vi=21,
            )
        )

        self.assertEqual(result.score, 1)
        self.assertEqual(result.state, "STRESS")
        self.assertEqual(result.entry_multiplier, 0.0)

    def test_invalid_breadth_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "breadth_pct"):
            evaluate_market_regime(inputs(breadth_pct=100.1))

    def test_invalid_as_of_date_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            evaluate_market_regime(inputs(as_of="2026-02-30"))


if __name__ == "__main__":
    unittest.main()
