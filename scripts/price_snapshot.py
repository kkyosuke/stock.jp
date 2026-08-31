"""Validate the tracked Yahoo/JPX daily archive for an active PAPER universe."""

from __future__ import annotations

import csv
import hashlib
import json
import math
from datetime import datetime
from pathlib import Path
from typing import Any


REQUIRED_FIELDS = {
    "日付",
    "銘柄コード",
    "始値",
    "高値",
    "安値",
    "終値",
    "売買高(株)",
    "取得状態",
}


def normalize_code(value: Any) -> str:
    code = str(value or "").strip().upper()
    if len(code) == 5 and code.endswith("0") and code[:4].isdigit():
        return code[:4]
    return code


def active_target_codes(private: Path) -> set[str]:
    result: set[str] = set()
    for filename, requires_active_flag in (
        ("portfolio-register.csv", False),
        ("watchlist.csv", True),
    ):
        with (private / filename).open(encoding="utf-8", newline="") as source:
            for row in csv.DictReader(source):
                status = str(row.get("status", "")).strip().upper()
                if status in {"CLOSED", "SOLD", "EXITED", "INACTIVE", "REJECTED"}:
                    continue
                if requires_active_flag and str(row.get("active", "")).strip().upper() not in {
                    "TRUE",
                    "1",
                    "YES",
                    "ACTIVE",
                }:
                    continue
                code = normalize_code(row.get("code"))
                if code:
                    result.add(code)
    return result


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _finite_ratio(value: Any) -> float:
    ratio = float(value)
    if not math.isfinite(ratio) or not 0 <= ratio <= 1:
        raise ValueError("ratio must be finite and between zero and one")
    return ratio


def validate_tracked_price_snapshot(
    *,
    root: Path,
    active_targets: set[str],
    cutoff: datetime,
    config: dict[str, Any],
) -> tuple[dict[str, Any] | None, list[str]]:
    """Return checksum-verified archive evidence or fail-closed reasons."""

    price = config.get("price_source", {})
    if not price.get("enabled"):
        return None, ["tracked Yahoo price archive is disabled"]
    if price.get("provider") != "yahoo_finance_unofficial_tracked_archive":
        return None, ["tracked Yahoo price source identity is invalid"]
    root_resolved = root.resolve()
    archive_root = (root_resolved / "data/daily-prices").resolve()
    manifest = (root_resolved / str(price.get("manifest_path", ""))).resolve()
    if not manifest.is_relative_to(archive_root) or manifest.name != "latest.json":
        return None, ["tracked Yahoo manifest path is outside the daily-price archive"]
    if not manifest.is_file():
        return None, ["tracked Yahoo latest.json is missing"]
    try:
        metadata = json.loads(manifest.read_text(encoding="utf-8"))
        if metadata.get("schema_version") != 1:
            raise ValueError("unexpected manifest schema")
        source = metadata["source"]
        if source.get("provider") != "Yahoo Finance" or source.get("official") is not False:
            raise ValueError("unexpected source identity")
        universe = metadata["universe"]
        if (
            universe.get("provider") != "JPX TSE-listed issues monthly spreadsheet"
            or universe.get("scope") != "current domestic stocks"
        ):
            raise ValueError("unexpected universe identity")
        price_date = datetime.fromisoformat(str(metadata["latest_trading_date"])).date()
        session = metadata["latest_session"]
        if datetime.fromisoformat(str(session["date"])).date() != price_date:
            raise ValueError("session date mismatch")
        universe_count = int(universe["count"])
        quote_count = int(session["quote_count"])
        required_archive = _finite_ratio(
            price.get("minimum_daily_archive_coverage", 0.98)
        )
        required_targets = _finite_ratio(
            price.get("minimum_active_target_coverage", 1.0)
        )
        maximum_age = int(price.get("maximum_latest_price_age_days", 7))
        if universe_count < 1 or quote_count < 0 or quote_count > universe_count:
            raise ValueError("invalid universe counts")
        if maximum_age < 0:
            raise ValueError("maximum age must be non-negative")
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        return None, ["tracked Yahoo latest.json has invalid readiness evidence"]

    failures: list[str] = []
    archive_coverage = quote_count / universe_count
    if archive_coverage < required_archive:
        failures.append("tracked Yahoo full-universe coverage is below the daily minimum")
    if price_date > cutoff.date():
        failures.append("tracked Yahoo price date is in the future")
    elif (cutoff.date() - price_date).days > maximum_age:
        failures.append("tracked Yahoo price date is stale")

    data_file = (archive_root / str(session.get("file", ""))).resolve()
    if not data_file.is_relative_to(archive_root):
        failures.append("tracked Yahoo session path escapes the daily-price archive")
    elif not data_file.is_file():
        failures.append("tracked Yahoo latest session CSV is missing")
    elif data_file.stem != price_date.isoformat():
        failures.append("tracked Yahoo session filename does not match the price date")
    else:
        expected_sha = str(session.get("sha256") or "")
        if not expected_sha or _sha256(data_file) != expected_sha:
            failures.append("tracked Yahoo session checksum does not match")

    target_failures: list[str] = []
    target_rows: dict[str, dict[str, str]] = {}
    if not failures and data_file.is_file():
        try:
            with data_file.open(encoding="utf-8-sig", newline="") as source_file:
                reader = csv.DictReader(source_file)
                if not REQUIRED_FIELDS.issubset(set(reader.fieldnames or [])):
                    raise ValueError("daily-price CSV schema is incomplete")
                seen: set[str] = set()
                ok_count = 0
                row_count = 0
                for row in reader:
                    code = normalize_code(row.get("銘柄コード"))
                    if not code:
                        raise ValueError("blank code")
                    if code in seen:
                        raise ValueError(f"duplicate code {code}")
                    seen.add(code)
                    row_count += 1
                    if row.get("日付") != price_date.isoformat():
                        raise ValueError("row date mismatch")
                    if row.get("取得状態") == "OK":
                        ok_count += 1
                    if code in active_targets:
                        target_rows[code] = row
                if row_count != universe_count or ok_count != quote_count:
                    raise ValueError("manifest counts do not match CSV")
        except (OSError, ValueError):
            failures.append("tracked Yahoo latest session CSV is invalid")

    if not failures:
        for code in sorted(active_targets):
            row = target_rows.get(code)
            if row is None or row.get("取得状態") != "OK":
                target_failures.append(code)
                continue
            try:
                open_price, high, low, close = (
                    float(row[field]) for field in ("始値", "高値", "安値", "終値")
                )
                prices = [open_price, high, low, close]
                volume = float(row["売買高(株)"])
                if not all(math.isfinite(value) and value > 0 for value in prices):
                    raise ValueError
                if high < max(open_price, low, close) or low > min(
                    open_price, high, close
                ):
                    raise ValueError
                if not math.isfinite(volume) or volume < 0 or not volume.is_integer():
                    raise ValueError
            except (KeyError, TypeError, ValueError):
                target_failures.append(code)
        target_coverage = (
            (len(active_targets) - len(target_failures)) / len(active_targets)
            if active_targets
            else 0.0
        )
        if target_coverage < required_targets:
            failures.append("tracked Yahoo archive does not cover every active target")
    else:
        target_coverage = 0.0

    if failures:
        return None, sorted(set(failures))
    return {
        "status": "COMPLETED",
        "provider": "yahoo_finance_unofficial_tracked_archive",
        "manifest_path": manifest.relative_to(root_resolved).as_posix(),
        "session_path": data_file.relative_to(root_resolved).as_posix(),
        "price_date": price_date.isoformat(),
        "session_sha256": str(session["sha256"]),
        "universe_count": universe_count,
        "quote_count": quote_count,
        "coverage_ratio": archive_coverage,
        "target_codes": sorted(active_targets),
        "target_count": len(active_targets),
        "target_coverage_ratio": target_coverage,
        "target_failures": target_failures,
        "decision_use": "PAPER_SECONDARY_SOURCE_REQUIRES_OFFICIAL_CONFIRMATION",
    }, []
