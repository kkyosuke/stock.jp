"""Shared builders for isolated operation-readiness tests."""

from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path

from scripts.collect_daily_prices import CSV_FIELDS


def write_price_archive(
    root: Path, codes: list[str], *, price_date: str = "2026-08-31"
) -> Path:
    archive = root / "data/daily-prices"
    session = archive / price_date[:4] / f"{price_date}.csv"
    session.parent.mkdir(parents=True, exist_ok=True)
    with session.open("w", encoding="utf-8", newline="") as destination:
        writer = csv.DictWriter(destination, fieldnames=CSV_FIELDS, lineterminator="\n")
        writer.writeheader()
        for code in codes:
            writer.writerow(
                {
                    "日付": price_date,
                    "銘柄コード": code,
                    "銘柄名": f"Example {code}",
                    "市場・商品区分": "プライム（内国株式）",
                    "33業種区分": "情報・通信業",
                    "始値": "100",
                    "高値": "105",
                    "安値": "99",
                    "終値": "103",
                    "前日比": "3",
                    "前日比％": "3",
                    "売買高(株)": "1000",
                    "取得状態": "OK",
                }
            )
    digest = hashlib.sha256(session.read_bytes()).hexdigest()
    manifest = {
        "schema_version": 1,
        "source": {
            "provider": "Yahoo Finance",
            "endpoint": "/v8/finance/chart/<code>.T",
            "official": False,
        },
        "universe": {
            "provider": "JPX TSE-listed issues monthly spreadsheet",
            "scope": "current domestic stocks",
            "count": len(codes),
        },
        "latest_trading_date": price_date,
        "fetch": {
            "success_count": len(codes),
            "error_count": 0,
            "error_codes": [],
        },
        "latest_session": {
            "date": price_date,
            "file": f"{price_date[:4]}/{price_date}.csv",
            "sha256": digest,
            "quote_count": len(codes),
            "no_quote_count": 0,
            "fetch_error_count": 0,
        },
    }
    (archive / "latest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return session


def record_verified_backup(
    root: Path, *, at: str = "2026-07-31T18:00:00+09:00"
) -> Path:
    private = root / "operations/private"
    archive = private / "backups/operation-test.zip"
    archive.parent.mkdir(parents=True, exist_ok=True)
    archive.write_bytes(b"checksum-verified test backup")
    state_path = private / "state.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["last_backup_at_jst"] = at
    state["last_backup_path"] = archive.relative_to(root).as_posix()
    state["last_backup_sha256"] = hashlib.sha256(archive.read_bytes()).hexdigest()
    state["last_backup_verified_before_encryption"] = True
    state_path.write_text(json.dumps(state), encoding="utf-8")
    return archive
