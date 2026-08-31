"""Collect auditable JPX daily prices from Yahoo Finance for PAPER operation.

Yahoo Finance's chart/spark endpoints are unofficial.  The collector therefore
fails closed: an incomplete critical universe or stale response never advances
the successful market-data watermark and must not be used for a decision.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import ssl
import tempfile
import time
from collections.abc import Iterable
from datetime import date, datetime
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

import certifi
import xlrd

try:
    from scripts.operation_state import (
        initialize_or_migrate_workspace,
        secure_private_tree,
    )
except ModuleNotFoundError:  # Direct execution from scripts/
    from operation_state import initialize_or_migrate_workspace, secure_private_tree


PROJECT_ROOT = Path(__file__).resolve().parents[1]
JST = ZoneInfo("Asia/Tokyo")
PRICE_FIELDS = [
    "price_date", "code", "symbol", "open", "high", "low", "close",
    "adj_close", "volume", "turnover_yen", "source", "retrieved_at_jst",
    "quality_flags",
]
TLS_CONTEXT = ssl.create_default_context(cafile=certifi.where())


class PriceCollectionError(RuntimeError):
    pass


def _parse_jst(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("--at must include a UTC offset")
    return parsed.astimezone(JST)


def _atomic_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=path.parent, delete=False
    ) as target:
        target.write(value)
        temporary = Path(target.name)
    temporary.chmod(0o600)
    temporary.replace(path)


def _atomic_json(path: Path, value: dict[str, Any]) -> None:
    _atomic_text(path, json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise PriceCollectionError(f"JSON object required: {path}")
    return value


def _merged_config(root: Path) -> dict[str, Any]:
    template = _read_json(root / "operations/templates/source-config-template.json")
    private_path = root / "operations/private/source-config.json"
    private = _read_json(private_path)
    merged = dict(template)
    merged.update(private)
    price = dict(template.get("price_source", {}))
    price.update(private.get("price_source", {}))
    merged["price_source"] = price
    return merged


def _download_bytes(url: str, timeout: int) -> bytes:
    request = Request(url, headers={"User-Agent": "stock.jp PAPER collector/1.0"})
    try:
        with urlopen(request, timeout=timeout, context=TLS_CONTEXT) as response:
            return response.read()
    except (HTTPError, URLError) as error:
        raise PriceCollectionError(f"JPX universe download failed: {error}") from error


def _cell_text(value: Any) -> str:
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value or "").strip()


def _parse_jpx_xls(payload: bytes) -> list[dict[str, str]]:
    try:
        sheet = xlrd.open_workbook(file_contents=payload).sheet_by_index(0)
    except (xlrd.XLRDError, IndexError) as error:
        raise PriceCollectionError("invalid JPX listed-issues workbook") from error
    header_index = -1
    headers: list[str] = []
    for index in range(min(sheet.nrows, 20)):
        candidate = [_cell_text(value) for value in sheet.row_values(index)]
        if "コード" in candidate and "銘柄名" in candidate:
            header_index, headers = index, candidate
            break
    if header_index < 0:
        raise PriceCollectionError("JPX workbook header not found")
    code_index = headers.index("コード")
    name_index = headers.index("銘柄名")
    market_index = next(
        (index for index, name in enumerate(headers) if "市場・商品区分" in name),
        None,
    )
    rows: list[dict[str, str]] = []
    for index in range(header_index + 1, sheet.nrows):
        values = sheet.row_values(index)
        code = _cell_text(values[code_index])
        if not code or len(code) not in {4, 5}:
            continue
        market = _cell_text(values[market_index]) if market_index is not None else ""
        if market and "内国株式" not in market:
            continue
        rows.append(
            {
                "code": code,
                "company": _cell_text(values[name_index]),
                "market": market,
            }
        )
    if not rows:
        raise PriceCollectionError("JPX workbook contained no listed issues")
    return rows


def _csv_universe(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as source:
        rows = list(csv.DictReader(source))
    result = []
    for row in rows:
        code = str(row.get("code", "")).strip()
        if code:
            result.append(
                {
                    "code": code,
                    "company": str(row.get("company", "")).strip(),
                    "market": str(row.get("market", "")).strip(),
                }
            )
    if not result:
        raise PriceCollectionError(f"universe is empty: {path}")
    return result


def _active_codes(path: Path, active_field: str, statuses: set[str]) -> set[str]:
    with path.open(encoding="utf-8", newline="") as source:
        rows = list(csv.DictReader(source))
    result: set[str] = set()
    for row in rows:
        status = str(row.get(active_field, "")).strip().upper()
        if status in statuses:
            code = str(row.get("code", "")).strip()
            if code:
                result.add(code)
    return result


def _critical_codes(root: Path) -> set[str]:
    private = root / "operations/private"
    holdings = _active_codes(
        private / "portfolio-register.csv", "status", {"OPEN", "ACTIVE", "HELD"}
    )
    watchlist = _active_codes(
        private / "watchlist.csv", "active", {"TRUE", "1", "YES", "ACTIVE"}
    )
    return holdings | watchlist


def _symbol(code: str) -> str:
    return f"{code}.T"


def _chunks(values: list[str], size: int) -> Iterable[list[str]]:
    for start in range(0, len(values), size):
        yield values[start : start + size]


def _request_spark(
    *, symbols: list[str], config: dict[str, Any], timeout: int
) -> dict[str, Any]:
    params = urlencode(
        {
            "symbols": ",".join(symbols),
            "range": str(config.get("range", "3mo")),
            "interval": str(config.get("interval", "1d")),
            "events": "div,splits",
        },
        quote_via=quote,
    )
    attempts = max(1, int(config.get("max_attempts", 3)))
    errors: list[str] = []
    for attempt in range(attempts):
        for base_url in config.get("base_urls", []):
            url = f"{str(base_url).rstrip('/')}/v7/finance/spark?{params}"
            request = Request(
                url,
                headers={"User-Agent": "Mozilla/5.0 stock.jp PAPER collector/1.0"},
            )
            try:
                with urlopen(request, timeout=timeout, context=TLS_CONTEXT) as response:
                    payload = json.loads(response.read().decode("utf-8"))
                if not isinstance(payload, dict):
                    raise PriceCollectionError("Yahoo returned non-object JSON")
                return payload
            except (HTTPError, URLError, UnicodeDecodeError, json.JSONDecodeError) as error:
                errors.append(f"{base_url}: {error}")
        if attempt + 1 < attempts:
            time.sleep(0.5 * (2**attempt))
    raise PriceCollectionError("Yahoo batch failed: " + " | ".join(errors[-4:]))


def _request_chart(
    *, symbol: str, config: dict[str, Any], timeout: int
) -> dict[str, Any]:
    params = urlencode(
        {
            "range": str(config.get("range", "3mo")),
            "interval": str(config.get("interval", "1d")),
            "events": "div,splits",
        }
    )
    attempts = max(1, int(config.get("max_attempts", 3)))
    errors: list[str] = []
    for attempt in range(attempts):
        for base_url in config.get("base_urls", []):
            url = (
                f"{str(base_url).rstrip('/')}/v8/finance/chart/"
                f"{quote(symbol, safe='')}?{params}"
            )
            request = Request(
                url,
                headers={"User-Agent": "Mozilla/5.0 stock.jp PAPER collector/1.0"},
            )
            try:
                with urlopen(request, timeout=timeout, context=TLS_CONTEXT) as response:
                    payload = json.loads(response.read().decode("utf-8"))
                if not isinstance(payload, dict):
                    raise PriceCollectionError("Yahoo returned non-object JSON")
                return payload
            except (HTTPError, URLError, UnicodeDecodeError, json.JSONDecodeError) as error:
                errors.append(f"{base_url}: {error}")
        if attempt + 1 < attempts:
            time.sleep(0.5 * (2**attempt))
    raise PriceCollectionError("Yahoo chart failed: " + " | ".join(errors[-4:]))


def _number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _at(values: Any, index: int) -> Any:
    return values[index] if isinstance(values, list) and index < len(values) else None


def _response_rows(
    response: dict[str, Any], *, code: str, retrieved_at: str, cutoff_date: date,
    close_only_allowed: bool = False,
) -> list[dict[str, str]]:
    timestamps = response.get("timestamp", [])
    indicators = response.get("indicators", {})
    quotes = indicators.get("quote", [{}]) if isinstance(indicators, dict) else [{}]
    adjusted = indicators.get("adjclose", [{}]) if isinstance(indicators, dict) else [{}]
    quote_row = quotes[0] if quotes and isinstance(quotes[0], dict) else {}
    adjusted_row = adjusted[0] if adjusted and isinstance(adjusted[0], dict) else {}
    rows: list[dict[str, str]] = []
    seen: set[date] = set()
    for index, raw_timestamp in enumerate(timestamps if isinstance(timestamps, list) else []):
        try:
            price_date = datetime.fromtimestamp(int(raw_timestamp), tz=JST).date()
        except (TypeError, ValueError, OSError):
            continue
        close = _number(_at(quote_row.get("close"), index))
        volume = _number(_at(quote_row.get("volume"), index))
        if price_date > cutoff_date or price_date in seen or close is None:
            continue
        seen.add(price_date)
        quality: list[str] = []
        if (volume is None or volume <= 0) and close_only_allowed:
            quality.append("CLOSE_ONLY_SOURCE")
        elif volume is None or volume <= 0:
            quality.append("NON_TRADING_OR_ZERO_VOLUME")
        adj_close = _number(_at(adjusted_row.get("adjclose"), index))
        if adj_close is None:
            adj_close = close
            quality.append("ADJ_CLOSE_FALLBACK")
        values = {
            name: _number(_at(quote_row.get(name), index))
            for name in ("open", "high", "low")
        }
        turnover = close * volume if volume is not None and volume >= 0 else None
        def display(value: float | None) -> str:
            return "" if value is None else format(value, ".12g")
        rows.append(
            {
                "price_date": price_date.isoformat(),
                "code": code,
                "symbol": _symbol(code),
                "open": display(values["open"]),
                "high": display(values["high"]),
                "low": display(values["low"]),
                "close": display(close),
                "adj_close": display(adj_close),
                "volume": display(volume),
                "turnover_yen": display(turnover),
                "source": "yahoo_finance_unofficial",
                "retrieved_at_jst": retrieved_at,
                "quality_flags": "|".join(quality),
            }
        )
    return rows


def _parse_spark(
    payload: dict[str, Any], *, expected: dict[str, str], retrieved_at: str,
    cutoff_date: date, close_only_allowed: bool = False,
) -> tuple[list[dict[str, str]], set[str], list[str]]:
    spark = payload.get("spark", {})
    results = spark.get("result", []) if isinstance(spark, dict) else []
    errors: list[str] = []
    if isinstance(spark, dict) and spark.get("error"):
        errors.append(str(spark["error"]))
    rows: list[dict[str, str]] = []
    received: set[str] = set()
    for result in results if isinstance(results, list) else []:
        if not isinstance(result, dict):
            continue
        symbol = str(result.get("symbol", ""))
        code = expected.get(symbol)
        responses = result.get("response", [])
        if not code or not isinstance(responses, list) or not responses:
            continue
        parsed = _response_rows(
            responses[0], code=code, retrieved_at=retrieved_at,
            cutoff_date=cutoff_date, close_only_allowed=close_only_allowed,
        )
        if parsed:
            rows.extend(parsed)
            received.add(code)
    return rows, received, errors


def _parse_chart(
    payload: dict[str, Any], *, code: str, retrieved_at: str, cutoff_date: date,
    close_only_allowed: bool = False,
) -> list[dict[str, str]]:
    chart = payload.get("chart", {})
    if not isinstance(chart, dict) or chart.get("error"):
        raise PriceCollectionError(f"Yahoo chart error for {code}: {chart.get('error')}")
    results = chart.get("result", [])
    if not isinstance(results, list) or not results or not isinstance(results[0], dict):
        raise PriceCollectionError(f"Yahoo chart result missing for {code}")
    return _response_rows(
        results[0], code=code, retrieved_at=retrieved_at,
        cutoff_date=cutoff_date, close_only_allowed=close_only_allowed,
    )


def _write_csv(path: Path, rows: list[dict[str, str]], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", newline="", dir=path.parent, delete=False
    ) as target:
        writer = csv.DictWriter(target, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
        temporary = Path(target.name)
    temporary.chmod(0o600)
    temporary.replace(path)


def _monthly_price_screen(
    path: Path,
    rows: list[dict[str, str]],
    universe: list[dict[str, str]],
) -> int:
    identity = {row["code"]: row for row in universe}
    by_code: dict[str, list[dict[str, str]]] = {}
    for row in rows:
        if "NON_TRADING_OR_ZERO_VOLUME" not in row.get("quality_flags", ""):
            by_code.setdefault(row["code"], []).append(row)
    fields = [
        "code", "company", "market", "last_price_date", "close",
        "return_20d", "return_60d", "average_turnover_20d_yen",
        "drawdown_60d", "observation",
    ]
    screened: list[dict[str, str]] = []
    for code, prices in by_code.items():
        prices.sort(key=lambda row: row["price_date"])
        # Screen on observed closes; adj_close can include dividend adjustments.
        closes = [float(row["close"]) for row in prices]
        turnovers = [
            float(row["turnover_yen"])
            for row in prices[-20:] if row.get("turnover_yen")
        ]
        if len(closes) < 20:
            continue
        latest = closes[-1]
        return_20 = latest / closes[-20] - 1 if closes[-20] else 0
        return_60 = latest / closes[-60] - 1 if len(closes) >= 60 and closes[-60] else 0
        high_60 = max(closes[-60:])
        drawdown = latest / high_60 - 1 if high_60 else 0
        average_turnover = sum(turnovers) / 20 if len(turnovers) == 20 else None
        observations: list[str] = []
        if abs(return_20) >= 0.2:
            observations.append("20D_MOVE")
        if drawdown <= -0.3:
            observations.append("60D_DRAWDOWN")
        if average_turnover is None:
            observations.append("LIQUIDITY_UNAVAILABLE")
        elif average_turnover < 100_000_000:
            observations.append("LOW_LIQUIDITY")
        company = identity.get(code, {})
        screened.append(
            {
                "code": code,
                "company": company.get("company", ""),
                "market": company.get("market", ""),
                "last_price_date": prices[-1]["price_date"],
                "close": format(latest, ".12g"),
                "return_20d": format(return_20, ".12g"),
                "return_60d": format(return_60, ".12g"),
                "average_turnover_20d_yen": (
                    format(average_turnover, ".12g")
                    if average_turnover is not None else ""
                ),
                "drawdown_60d": format(drawdown, ".12g"),
                "observation": "|".join(observations),
            }
        )
    screened.sort(
        key=lambda row: (
            -float(row["average_turnover_20d_yen"] or 0), row["code"]
        )
    )
    _write_csv(path, screened, fields)
    return len(screened)


def _merge_history(path: Path, new_rows: list[dict[str, str]]) -> int:
    with path.open(encoding="utf-8", newline="") as source:
        old_rows = list(csv.DictReader(source))
    merged = {
        (row.get("price_date", ""), row.get("code", "")): row for row in old_rows
    }
    before = len(merged)
    for row in new_rows:
        merged[(row["price_date"], row["code"])] = row
    rows = [merged[key] for key in sorted(merged)]
    _write_csv(path, rows, PRICE_FIELDS)
    return len(merged) - before


def _update_portfolio(root: Path, all_rows: list[dict[str, str]], at: str) -> int:
    path = root / "operations/private/portfolio-register.csv"
    with path.open(encoding="utf-8", newline="") as source:
        reader = csv.DictReader(source)
        positions = list(reader)
        fields = list(reader.fieldnames or [])
    by_code: dict[str, list[dict[str, str]]] = {}
    for row in all_rows:
        if "NON_TRADING_OR_ZERO_VOLUME" not in row.get("quality_flags", ""):
            by_code.setdefault(row["code"], []).append(row)
    updated = 0
    for position in positions:
        prices = sorted(by_code.get(position.get("code", ""), []), key=lambda row: row["price_date"])
        if not prices:
            continue
        # The rule uses the observed close, not dividend-adjusted total return.
        closes = [float(row["close"]) for row in prices]
        ma20 = sum(closes[-20:]) / 20 if len(closes) >= 20 else None
        entry_date = position.get("entry_date", "")
        eligible = [
            sum(closes[index - 19 : index + 1]) / 20
            for index in range(19, len(closes))
            if not entry_date or prices[index]["price_date"] >= entry_date
        ]
        highest_ma20 = max(eligible) if eligible else ma20
        latest = closes[-1]
        average_cost = _number(position.get("average_cost"))
        position["as_of_jst"] = at
        position["last_close"] = format(latest, ".12g")
        position["current_multiple"] = (
            format(latest / average_cost, ".12g") if average_cost and average_cost > 0 else ""
        )
        position["ma20"] = format(ma20, ".12g") if ma20 is not None else ""
        position["highest_ma20"] = (
            format(highest_ma20, ".12g") if highest_ma20 is not None else ""
        )
        position["dd20"] = (
            format(latest / highest_ma20 - 1, ".12g")
            if highest_ma20 and highest_ma20 > 0 else ""
        )
        updated += 1
    _write_csv(path, positions, fields)
    return updated


def _update_state(
    *, root: Path, at: str, status: str, price_date: str | None,
    snapshot_path: str, scope: str,
) -> dict[str, Any]:
    path = root / "operations/private/market-data-state.json"
    state = _read_json(path)
    state["last_attempt_at_jst"] = at
    if status == "COMPLETED" and scope == "monthly":
        state["last_monthly_success_at_jst"] = at
        state["last_monthly_snapshot_path"] = snapshot_path
    if status == "COMPLETED" and price_date and scope == "daily":
        recent = [
            item for item in state.get("recent_daily_collections", [])
            if isinstance(item, dict) and item.get("price_date") != price_date
        ]
        recent.append({"price_date": price_date, "status": "COMPLETED", "snapshot_path": snapshot_path})
        recent = sorted(recent, key=lambda item: str(item["price_date"]))[-40:]
        state["last_success_at_jst"] = at
        state["last_price_date"] = price_date
        state["last_snapshot_path"] = snapshot_path
        state["recent_daily_collections"] = recent
        state["successful_daily_price_dates"] = len(recent)
    _atomic_json(path, state)
    return state


def _snapshot_directory(root: Path, collected_at: datetime, scope: str) -> Path:
    parent = (
        root
        / "operations/private/market-snapshots"
        / collected_at.date().isoformat()
        / scope
    )
    parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    stem = collected_at.strftime("%H%M%S")
    for attempt in range(10_000):
        name = stem if attempt == 0 else f"{stem}-{attempt}"
        candidate = parent / name
        try:
            candidate.mkdir(mode=0o700)
            return candidate
        except FileExistsError:
            continue
    raise PriceCollectionError("could not allocate a unique snapshot directory")


def collect_prices(
    *, at: str | None = None, root: Path = PROJECT_ROOT, fixture_dir: Path | None = None,
    universe_file: Path | None = None, max_symbols: int | None = None,
    scope: str = "daily",
) -> dict[str, Any]:
    initialize_or_migrate_workspace(root)
    collected_at = _parse_jst(at) if at else datetime.now(JST)
    config = _merged_config(root)
    price_config = config.get("price_source", {})
    if not price_config.get("enabled"):
        raise PriceCollectionError("Yahoo price source is disabled")
    timeout = int(config.get("request_timeout_seconds", 30))
    request_config = dict(price_config)
    request_config["range"] = str(
        price_config.get(
            "daily_range" if scope == "daily" else "monthly_range",
            "3mo" if scope == "daily" else "1y",
        )
    )
    if scope not in {"daily", "monthly"}:
        raise ValueError("scope must be daily or monthly")
    if fixture_dir:
        universe = _csv_universe(fixture_dir / "universe.csv")
    elif universe_file:
        universe = _csv_universe(universe_file)
    elif scope == "daily":
        active = _critical_codes(root)
        universe = [
            {"code": code, "company": "", "market": "active_targets"}
            for code in sorted(active)
        ]
        if not universe:
            raise PriceCollectionError(
                "daily universe is empty; add an active watchlist or portfolio target, "
                "or run --scope monthly first"
            )
    else:
        payload = _download_bytes(str(price_config["jpx_list_url"]), timeout)
        universe = _parse_jpx_xls(payload)
    deduped = {row["code"]: row for row in universe}
    universe = [deduped[code] for code in sorted(deduped)]
    if max_symbols is not None:
        if max_symbols < 1:
            raise ValueError("max_symbols must be >= 1")
        universe = universe[:max_symbols]
    expected_codes = {row["code"] for row in universe}
    critical = expected_codes if scope == "daily" else _critical_codes(root) & expected_codes
    symbols = [_symbol(code) for code in sorted(expected_codes)]
    expected_symbols = {_symbol(code): code for code in expected_codes}
    # Yahoo spark currently rejects larger batches with HTTP 400.
    batch_size = max(1, min(10, int(price_config.get("batch_size", 10))))
    snapshot_dir = _snapshot_directory(root, collected_at, scope)
    all_rows: list[dict[str, str]] = []
    received: set[str] = set()
    batch_errors: list[str] = []
    batches = list(_chunks(symbols, batch_size)) if scope == "monthly" else [[symbol] for symbol in symbols]
    if fixture_dir:
        payloads = [_read_json(fixture_dir / "yahoo-spark.json")]
        batches = [symbols]
    else:
        payloads = []
    raw_payloads: list[dict[str, Any]] = []
    market_price_date: date | None = None
    if scope == "daily" and not fixture_dir:
        try:
            benchmark = _request_chart(
                symbol=str(price_config.get("market_calendar_symbol", "^N225")),
                config=request_config,
                timeout=timeout,
            )
            raw_payloads.append({"market_calendar": benchmark})
            market_rows = _parse_chart(
                benchmark,
                code="__MARKET__",
                retrieved_at=collected_at.isoformat(timespec="seconds"),
                cutoff_date=collected_at.date(),
                close_only_allowed=True,
            )
            if not market_rows:
                raise PriceCollectionError("market calendar symbol returned no rows")
            market_price_date = max(
                date.fromisoformat(row["price_date"]) for row in market_rows
            )
        except PriceCollectionError as error:
            batch_errors.append(f"market calendar: {error}")
    for index, batch in enumerate(batches):
        try:
            if fixture_dir:
                payload = payloads[index]
            elif scope == "daily":
                payload = _request_chart(
                    symbol=batch[0], config=request_config, timeout=timeout
                )
            else:
                payload = _request_spark(
                    symbols=batch, config=request_config, timeout=timeout
                )
            raw_payloads.append(payload)
            if not fixture_dir and scope == "daily":
                code = expected_symbols[batch[0]]
                rows = _parse_chart(
                    payload, code=code,
                    retrieved_at=collected_at.isoformat(timespec="seconds"),
                    cutoff_date=collected_at.date(),
                )
                found = {code} if rows else set()
                errors = []
            else:
                rows, found, errors = _parse_spark(
                    payload,
                    expected={symbol: expected_symbols[symbol] for symbol in batch},
                    retrieved_at=collected_at.isoformat(timespec="seconds"),
                    cutoff_date=collected_at.date(),
                    close_only_allowed=scope == "monthly",
                )
            all_rows.extend(rows)
            received |= found
            batch_errors.extend(errors)
        except (PriceCollectionError, KeyError) as error:
            batch_errors.append(f"batch {index + 1}: {error}")
    _atomic_json(snapshot_dir / "yahoo-raw.json", {"batches": raw_payloads})
    usable = [
        row for row in all_rows
        if "NON_TRADING_OR_ZERO_VOLUME" not in row.get("quality_flags", "")
    ]
    latest_by_code: dict[str, date] = {}
    for row in usable:
        current = date.fromisoformat(row["price_date"])
        latest_by_code[row["code"]] = max(current, latest_by_code.get(row["code"], current))
    missing = expected_codes - received
    stale = {
        code for code, value in latest_by_code.items()
        if (collected_at.date() - value).days
        > int(price_config.get("maximum_latest_price_age_days", 7))
    }
    if scope == "daily":
        if market_price_date is None and latest_by_code:
            market_price_date = max(latest_by_code.values())
        if market_price_date:
            stale |= {
                code for code in expected_codes
                if latest_by_code.get(code) != market_price_date
            }
    critical_failures = sorted((critical & missing) | (critical & stale))
    fresh_codes = received - stale
    coverage = len(fresh_codes) / len(expected_codes) if expected_codes else 0.0
    coverage_key = (
        "minimum_daily_coverage" if scope == "daily"
        else "minimum_full_universe_coverage"
    )
    required_coverage = float(price_config.get(coverage_key, 1.0 if scope == "daily" else 0.99))
    status = (
        "COMPLETED"
        if coverage >= required_coverage and not critical_failures and not batch_errors
        else "BLOCKED"
    )
    price_date_value = market_price_date or (
        max(latest_by_code.values()) if latest_by_code else None
    )
    price_date = price_date_value.isoformat() if price_date_value else None
    history_added = 0
    positions_updated = 0
    screen_count = 0
    if status == "COMPLETED":
        history_path = root / "operations/private/price-history.csv"
        durable_rows = all_rows if scope == "daily" else []
        history_added = _merge_history(history_path, durable_rows)
        with history_path.open(encoding="utf-8", newline="") as source:
            positions_updated = _update_portfolio(
                root, list(csv.DictReader(source)), collected_at.isoformat(timespec="seconds")
            )
        if scope == "monthly":
            screen_count = _monthly_price_screen(
                snapshot_dir / "monthly-price-screen.csv", all_rows, universe
            )
    raw_sha = hashlib.sha256((snapshot_dir / "yahoo-raw.json").read_bytes()).hexdigest()
    relative = snapshot_dir.relative_to(root).as_posix()
    if fixture_dir:
        universe_source = "fixture"
    elif universe_file:
        universe_source = str(universe_file)
    elif scope == "daily":
        universe_source = "active portfolio and watchlist targets"
    else:
        universe_source = "JPX monthly listed issues"
    manifest = {
        "schema_version": "1.0",
        "status": status,
        "retrieved_at_jst": collected_at.isoformat(timespec="seconds"),
        "price_date": price_date,
        "market_price_date": (
            market_price_date.isoformat() if market_price_date else None
        ),
        "provider": "yahoo_finance_unofficial",
        "scope": scope,
        "universe_source": universe_source,
        "universe_count": len(expected_codes),
        "received_count": len(received),
        "fresh_count": len(fresh_codes),
        "coverage_ratio": coverage,
        "minimum_coverage_ratio": required_coverage,
        "critical_code_count": len(critical),
        "target_codes": sorted(expected_codes) if scope == "daily" else sorted(critical),
        "critical_failures": critical_failures,
        "missing_codes": sorted(missing),
        "stale_codes": sorted(stale),
        "batch_errors": batch_errors,
        "raw_sha256": raw_sha,
        "history_rows_added": history_added,
        "portfolio_rows_updated": positions_updated,
        "monthly_screen_count": screen_count,
        "decision_use": "PAPER_ONLY_SECONDARY_SOURCE",
    }
    _atomic_json(snapshot_dir / "manifest.json", manifest)
    state = _update_state(
        root=root, at=collected_at.isoformat(timespec="seconds"), status=status,
        price_date=price_date, snapshot_path=relative, scope=scope,
    )
    secure_private_tree(root)
    return {
        **manifest,
        "snapshot_path": relative,
        "successful_daily_price_dates": state.get("successful_daily_price_dates", 0),
    }


def status(root: Path = PROJECT_ROOT) -> dict[str, Any]:
    initialize_or_migrate_workspace(root)
    return _read_json(root / "operations/private/market-data-state.json")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    collect = commands.add_parser("collect")
    collect.add_argument("--at")
    collect.add_argument("--fixture-dir", type=Path)
    collect.add_argument("--universe-file", type=Path)
    collect.add_argument("--max-symbols", type=int)
    collect.add_argument("--scope", choices=("daily", "monthly"), default="daily")
    commands.add_parser("status")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.command == "status":
        result = status()
    else:
        result = collect_prices(
            at=args.at,
            fixture_dir=args.fixture_dir,
            universe_file=args.universe_file,
            max_symbols=args.max_symbols,
            scope=args.scope,
        )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("status", "COMPLETED") != "BLOCKED" else 1


if __name__ == "__main__":
    raise SystemExit(main())
