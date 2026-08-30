"""Calculate the frozen MRS-v0.1 market-regime risk overlay."""

from __future__ import annotations

import argparse
import json
import math
from dataclasses import asdict, dataclass
from datetime import date


@dataclass(frozen=True)
class MarketRegimeInput:
    as_of: str
    topix_close: float
    topix_ma200: float
    growth_close: float
    growth_ma200: float
    breadth_pct: float
    nikkei_vi: float
    nikkei_vi_p80_3y: float
    leading_ci: float
    leading_ci_3m_ago: float


@dataclass(frozen=True)
class MarketRegimeResult:
    as_of: str
    rule_version: str
    components: dict[str, int]
    score: int
    state: str
    entry_multiplier: float


def _validate(values: MarketRegimeInput) -> None:
    date.fromisoformat(values.as_of)
    numeric = {
        key: value
        for key, value in asdict(values).items()
        if key != "as_of"
    }
    for key, value in numeric.items():
        if not math.isfinite(value):
            raise ValueError(f"{key} must be finite")
        if key != "breadth_pct" and value <= 0:
            raise ValueError(f"{key} must be positive")
    if not 0 <= values.breadth_pct <= 100:
        raise ValueError("breadth_pct must be between 0 and 100")


def evaluate_market_regime(values: MarketRegimeInput) -> MarketRegimeResult:
    """Apply the five frozen MRS-v0.1 binary conditions."""

    _validate(values)
    components = {
        "M1": int(values.topix_close >= values.topix_ma200),
        "M2": int(values.growth_close >= values.growth_ma200),
        "M3": int(values.breadth_pct >= 50),
        "M4": int(values.nikkei_vi <= values.nikkei_vi_p80_3y),
        "M5": int(values.leading_ci >= values.leading_ci_3m_ago),
    }
    score = sum(components.values())
    if score >= 4:
        state, multiplier = "NORMAL", 1.0
    elif score >= 2:
        state, multiplier = "CAUTION", 0.5
    else:
        state, multiplier = "STRESS", 0.0
    return MarketRegimeResult(
        as_of=values.as_of,
        rule_version="MRS-v0.1",
        components=components,
        score=score,
        state=state,
        entry_multiplier=multiplier,
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--as-of", required=True)
    parser.add_argument("--topix-close", type=float, required=True)
    parser.add_argument("--topix-ma200", type=float, required=True)
    parser.add_argument("--growth-close", type=float, required=True)
    parser.add_argument("--growth-ma200", type=float, required=True)
    parser.add_argument("--breadth-pct", type=float, required=True)
    parser.add_argument("--nikkei-vi", type=float, required=True)
    parser.add_argument("--nikkei-vi-p80-3y", type=float, required=True)
    parser.add_argument("--leading-ci", type=float, required=True)
    parser.add_argument("--leading-ci-3m-ago", type=float, required=True)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    result = evaluate_market_regime(
        MarketRegimeInput(
            as_of=args.as_of,
            topix_close=args.topix_close,
            topix_ma200=args.topix_ma200,
            growth_close=args.growth_close,
            growth_ma200=args.growth_ma200,
            breadth_pct=args.breadth_pct,
            nikkei_vi=args.nikkei_vi,
            nikkei_vi_p80_3y=args.nikkei_vi_p80_3y,
            leading_ci=args.leading_ci,
            leading_ci_3m_ago=args.leading_ci_3m_ago,
        )
    )
    print(json.dumps(asdict(result), ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
