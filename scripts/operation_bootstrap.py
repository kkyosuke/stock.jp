"""Initialize the workspace and report whether nightly PAPER/LIVE operation is ready."""

from __future__ import annotations

import argparse
import csv
import json
import os
from pathlib import Path
import shutil
from typing import Any, Mapping

try:
    from scripts.operation_policy import policy_status
    from scripts.operation_state import initialize_or_migrate_workspace, validate_workspace
except ModuleNotFoundError:  # Direct execution from scripts/
    from operation_policy import policy_status
    from operation_state import initialize_or_migrate_workspace, validate_workspace


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def check_readiness(
    *,
    root: Path = PROJECT_ROOT,
    environ: Mapping[str, str] | None = None,
    fixture_dir: Path | None = None,
) -> dict[str, Any]:
    initialization = initialize_or_migrate_workspace(root)
    environment = os.environ if environ is None else environ
    private = root / "operations/private"
    blockers = validate_workspace(root)
    warnings: list[str] = []
    policy = _read_json(private / "operation-policy.json")
    policy_result = policy_status(policy)
    if not policy_result["valid"]:
        blockers.extend(policy_result["validation_errors"])

    config = _read_json(private / "source-config.json")
    credentials: dict[str, bool] = {}
    for provider in ("edinet", "jquants"):
        provider_config = config.get(provider, {})
        variable = str(provider_config.get("api_key_env", ""))
        fixture_ready = fixture_dir is not None and fixture_dir.is_dir()
        configured = fixture_ready or bool(variable and environment.get(variable))
        credentials[provider] = configured
        if provider_config.get("enabled") and not configured:
            blockers.append(f"missing environment credential: {variable or provider}")

    mode = str(policy.get("operation_mode", ""))
    if mode == "LIVE":
        if not policy_result["live_orders_allowed"]:
            blockers.append(
                "LIVE gates incomplete: " + ", ".join(policy_result["live_gate_failures"])
            )
        if not shutil.which("age"):
            blockers.append("LIVE requires the age executable for encrypted backups")
        if not environment.get("OPERATION_BACKUP_AGE_RECIPIENT"):
            blockers.append("LIVE requires OPERATION_BACKUP_AGE_RECIPIENT")
    if not _read_json(private / "state.json").get("last_backup_at_jst"):
        warnings.append("no verified backup has been recorded yet")
    if not environment.get("OPERATION_BACKUP_AGE_RECIPIENT"):
        warnings.append(
            "OPERATION_BACKUP_AGE_RECIPIENT is unset; automatic encrypted backup is unavailable"
        )
    with (private / "portfolio-register.csv").open(
        encoding="utf-8", newline=""
    ) as source:
        if not list(csv.DictReader(source)):
            warnings.append("portfolio register has no holdings")
    warnings.append(
        "scheduled task availability is external; confirm the desktop app and local project are available at 18:30 JST"
    )
    return {
        "status": "BLOCKED" if blockers else ("READY_WITH_WARNINGS" if warnings else "READY"),
        "ready": not blockers,
        "operation_mode": mode,
        "active_rule_version": policy.get("active_rule_version"),
        "credentials": credentials,
        "broker_submission": policy.get("broker_submission"),
        "blockers": sorted(set(blockers)),
        "warnings": warnings,
        "created": initialization["created"],
        "migrated": initialization["migrated"],
        "next_command": (
            ".venv/bin/python scripts/nightly_operation.py start --at <JST> --cutoff <JST>"
            if not blockers
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
