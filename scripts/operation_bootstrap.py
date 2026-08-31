"""Initialize the workspace and report whether nightly PAPER/LIVE operation is ready."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import os
import shutil
from collections.abc import Mapping
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

try:
    from scripts.operation_policy import policy_status
    from scripts.operation_state import (
        initialize_or_migrate_workspace,
        validate_workspace,
    )
except ModuleNotFoundError:  # Direct execution from scripts/
    from operation_policy import policy_status
    from operation_state import initialize_or_migrate_workspace, validate_workspace


PROJECT_ROOT = Path(__file__).resolve().parents[1]
JST = ZoneInfo("Asia/Tokyo")


def _parse_jst(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("datetime must include a UTC offset")
    return parsed.astimezone(JST)


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _active_target_codes(private: Path) -> set[str]:
    codes: set[str] = set()
    for filename, field, active in (
        ("portfolio-register.csv", "status", {"OPEN", "ACTIVE", "HELD"}),
        ("watchlist.csv", "active", {"TRUE", "1", "YES", "ACTIVE"}),
    ):
        with (private / filename).open(encoding="utf-8", newline="") as source:
            for row in csv.DictReader(source):
                if str(row.get(field, "")).strip().upper() in active:
                    code = str(row.get("code", "")).strip()
                    if code:
                        codes.add(code)
    return codes


def _price_snapshot_failures(
    *,
    root: Path,
    private: Path,
    config: dict[str, Any],
    now: datetime,
    active_targets: set[str],
) -> list[str]:
    failures: list[str] = []
    price_config = config.get("price_source", {})
    if not price_config.get("enabled"):
        return ["Yahoo price collection is disabled"]
    state_path = private / "market-data-state.json"
    if not state_path.is_file():
        return ["daily Yahoo price collection has never completed"]
    state = _read_json(state_path)
    relative = state.get("last_snapshot_path")
    if not relative:
        return ["daily Yahoo price collection has never completed"]
    snapshot_dir = (root / str(relative)).resolve()
    snapshot_root = (private / "market-snapshots").resolve()
    if not snapshot_dir.is_relative_to(snapshot_root):
        return ["latest Yahoo snapshot path is outside the private snapshot root"]
    manifest_path = snapshot_dir / "manifest.json"
    raw_path = snapshot_dir / "yahoo-raw.json"
    if not manifest_path.is_file() or not raw_path.is_file():
        return ["latest daily Yahoo snapshot files are missing"]
    manifest = _read_json(manifest_path)
    if manifest.get("status") != "COMPLETED" or manifest.get("scope") != "daily":
        failures.append("latest Yahoo snapshot is not a completed daily snapshot")
    raw_targets = manifest.get("target_codes")
    snapshot_targets = (
        {str(code).strip() for code in raw_targets if str(code).strip()}
        if isinstance(raw_targets, list)
        else None
    )
    if snapshot_targets is None:
        failures.append("latest Yahoo snapshot has invalid target-universe evidence")
    elif snapshot_targets != active_targets:
        failures.append("latest Yahoo snapshot does not match the current active universe")
    try:
        coverage = float(manifest["coverage_ratio"])
        required = float(price_config.get("minimum_daily_coverage", 1.0))
        if not math.isfinite(coverage) or not 0 <= coverage <= 1:
            raise ValueError
        if not math.isfinite(required) or not 0 <= required <= 1:
            raise ValueError
        if coverage < required:
            failures.append("latest Yahoo snapshot coverage is below the daily minimum")
    except (KeyError, TypeError, ValueError):
        failures.append("latest Yahoo snapshot has invalid coverage evidence")
    expected_sha = str(manifest.get("raw_sha256", ""))
    actual_sha = hashlib.sha256(raw_path.read_bytes()).hexdigest()
    if not expected_sha or expected_sha != actual_sha:
        failures.append("latest Yahoo snapshot checksum does not match")
    try:
        price_date = datetime.fromisoformat(str(manifest["price_date"])).date()
        maximum_age = int(price_config.get("maximum_latest_price_age_days", 7))
        if (now.date() - price_date).days > maximum_age:
            failures.append("latest Yahoo price date is stale")
        if price_date > now.date():
            failures.append("latest Yahoo price date is in the future")
    except (KeyError, TypeError, ValueError):
        failures.append("latest Yahoo snapshot has an invalid price date")
    if manifest.get("critical_failures"):
        failures.append("latest Yahoo snapshot has critical target failures")
    return failures


def _backup_failures(
    *, root: Path, private: Path, state: dict[str, Any], now: datetime
) -> list[str]:
    backup_at = state.get("last_backup_at_jst")
    relative = state.get("last_backup_path")
    expected_sha = str(state.get("last_backup_sha256") or "")
    if not backup_at or not relative or not expected_sha:
        return ["no verified operation backup has been recorded"]
    backup = (root / str(relative)).resolve()
    backup_root = (private / "backups").resolve()
    if not backup.is_relative_to(backup_root):
        return ["latest operation backup path is outside the private backup root"]
    if not backup.is_file():
        return ["latest verified operation backup archive is missing"]
    if hashlib.sha256(backup.read_bytes()).hexdigest() != expected_sha:
        return ["latest operation backup checksum does not match"]
    try:
        created = _parse_jst(str(backup_at))
    except ValueError:
        return ["latest operation backup timestamp is invalid"]
    if created > now:
        return ["latest operation backup timestamp is in the future"]
    if (now.date() - created.date()).days > 31:
        return ["latest verified operation backup is older than 31 days"]
    if state.get("last_backup_verified_before_encryption") is not True:
        return ["latest operation backup lacks creation-time verification evidence"]
    return []


def check_readiness(
    *,
    root: Path = PROJECT_ROOT,
    environ: Mapping[str, str] | None = None,
    fixture_dir: Path | None = None,
    at: str | None = None,
) -> dict[str, Any]:
    initialization = initialize_or_migrate_workspace(root)
    environment = os.environ if environ is None else environ
    private = root / "operations/private"
    workspace_errors = validate_workspace(root)
    warnings: list[str] = []
    policy = _read_json(private / "operation-policy.json")
    policy_result = policy_status(policy)
    config_template = _read_json(
        root / "operations/templates/source-config-template.json"
    )
    config = {**config_template, **_read_json(private / "source-config.json")}
    config["price_source"] = {
        **config_template.get("price_source", {}),
        **config.get("price_source", {}),
    }
    credentials: dict[str, bool] = {}
    automatic_source_blockers: list[str] = []
    for provider in ("edinet", "jquants"):
        provider_config = config.get(provider, {})
        variable = str(provider_config.get("api_key_env", ""))
        fixture_ready = fixture_dir is not None and fixture_dir.is_dir()
        configured = fixture_ready or bool(variable and environment.get(variable))
        credentials[provider] = configured
        if provider_config.get("enabled") and not configured:
            automatic_source_blockers.append(
                f"missing environment credential: {variable or provider}"
            )

    mode = str(policy.get("operation_mode", ""))
    now = _parse_jst(at) if at else datetime.now(JST)
    active_targets = _active_target_codes(private)
    active_target_count = len(active_targets)
    base_blockers = list(workspace_errors)
    if not policy_result["valid"]:
        base_blockers.extend(policy_result["validation_errors"])
    if policy.get("broker_submission") != "HUMAN_ONLY":
        base_blockers.append("broker submission must remain HUMAN_ONLY")
    if not active_target_count:
        base_blockers.append(
            "daily universe is empty; run the monthly scan and activate reviewed candidates"
        )
    if active_targets:
        base_blockers.extend(
            _price_snapshot_failures(
                root=root,
                private=private,
                config=config,
                now=now,
                active_targets=active_targets,
            )
        )
    state = _read_json(private / "state.json")
    backup_failures = _backup_failures(
        root=root, private=private, state=state, now=now
    )
    base_blockers.extend(backup_failures)

    paper_blockers = list(base_blockers)
    if mode != "PAPER":
        paper_blockers.append("operation_mode must be PAPER for PAPER operation")
    live_blockers = list(base_blockers)
    live_blockers.extend(automatic_source_blockers)
    if mode != "LIVE":
        live_blockers.append("operation_mode has not been explicitly promoted to LIVE")
    if not policy_result["live_orders_allowed"]:
        live_blockers.append(
            "LIVE gates incomplete: " + ", ".join(policy_result["live_gate_failures"])
        )
    if not shutil.which("age"):
        live_blockers.append("LIVE requires the age executable for encrypted backups")
    if not environment.get("OPERATION_BACKUP_AGE_RECIPIENT"):
        live_blockers.append("LIVE requires OPERATION_BACKUP_AGE_RECIPIENT")
    if backup_failures:
        warnings.append("no verified backup has been recorded yet")
    if not environment.get("OPERATION_BACKUP_AGE_RECIPIENT"):
        warnings.append(
            "OPERATION_BACKUP_AGE_RECIPIENT is unset; automatic encrypted backup is unavailable"
        )
    if not active_target_count:
        warnings.append("daily active universe is empty")
    if automatic_source_blockers and config.get("paper_manual_primary_source_fallback"):
        warnings.append(
            "official API credentials are missing; PAPER requires the AI run to complete "
            "and record manual company IR, TDnet/JPX, and EDINET checks"
        )
    warnings.append(
        "Yahoo Finance is an unofficial secondary price source; any incomplete or stale "
        "daily snapshot blocks decisions"
    )
    paper_blockers = sorted(set(paper_blockers))
    live_blockers = sorted(set(live_blockers))
    active_blockers = live_blockers if mode == "LIVE" else paper_blockers
    return {
        "status": "BLOCKED" if active_blockers else ("READY_WITH_WARNINGS" if warnings else "READY"),
        "ready": not active_blockers,
        "paper_go": not paper_blockers,
        "live_go": not live_blockers and policy_result["live_orders_allowed"],
        "operation_mode": mode,
        "active_rule_version": policy.get("active_rule_version"),
        "credentials": credentials,
        "broker_submission": policy.get("broker_submission"),
        "active_target_count": active_target_count,
        "blockers": active_blockers,
        "paper_blockers": paper_blockers,
        "live_blockers": live_blockers,
        "automatic_source_blockers": sorted(set(automatic_source_blockers)),
        "warnings": warnings,
        "created": initialization["created"],
        "migrated": initialization["migrated"],
        "next_command": (
            ".venv/bin/python scripts/nightly_operation.py start --at <JST> --cutoff <JST>"
            if not active_blockers
            else "resolve every blocker and rerun this check"
        ),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("check", nargs="?")
    parser.add_argument("--fixture-dir", type=Path)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    result = check_readiness(fixture_dir=args.fixture_dir)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["ready"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
