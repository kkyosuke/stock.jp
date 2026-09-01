"""Validate the private PAPER/LIVE operation policy."""

from __future__ import annotations

import argparse
from datetime import datetime
import json
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_POLICY = PROJECT_ROOT / "operations/private/operation-policy.json"
POLICY_SCHEMA_VERSION = "1.3"
VALID_MODES = {"PAPER", "LIVE", "PAUSED"}
VALID_SUBMISSION = {"HUMAN_ONLY"}
REQUIRED_LIVE_GATES = {
    "point_in_time_full_universe_validation",
    "historical_replay_2025_2026_accepted",
    "official_source_coverage",
    "private_repository_recovery",
    "personal_risk_and_broker_check",
}
VALID_RULE_VERSIONS = {"v0.2", "v0.3", "v0.4"}


def _is_aware_timestamp(value: Any) -> bool:
    if not isinstance(value, str) or not value.strip():
        return False
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    return parsed.tzinfo is not None and parsed.utcoffset() is not None


def load_policy(path: Path = DEFAULT_POLICY) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def validate_policy(policy: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if policy.get("schema_version") != POLICY_SCHEMA_VERSION:
        errors.append(f"schema_version must be {POLICY_SCHEMA_VERSION}")
    if policy.get("operation_mode") not in VALID_MODES:
        errors.append("operation_mode must be PAPER, LIVE, or PAUSED")
    if policy.get("broker_submission") not in VALID_SUBMISSION:
        errors.append("broker_submission must be HUMAN_ONLY")
    active = policy.get("active_rule_version")
    if active not in VALID_RULE_VERSIONS:
        errors.append("active_rule_version must be v0.2, v0.3, or v0.4")
    shadows = policy.get("shadow_rule_versions")
    if not isinstance(shadows, list):
        errors.append("shadow_rule_versions must be a list")
    else:
        invalid_shadows = [value for value in shadows if value not in VALID_RULE_VERSIONS]
        if invalid_shadows:
            errors.append("shadow_rule_versions contains an unsupported version")
        if len(shadows) != len(set(map(str, shadows))):
            errors.append("shadow_rule_versions must not contain duplicates")
        if active in shadows:
            errors.append("active_rule_version cannot also be a shadow version")
    if not _is_aware_timestamp(policy.get("effective_at_jst")):
        errors.append("effective_at_jst must be an aware ISO timestamp")
    gates = policy.get("live_gates")
    if not isinstance(gates, dict):
        errors.append("live_gates must be an object")
        gates = {}
    missing = REQUIRED_LIVE_GATES - set(gates)
    if missing:
        errors.append(f"missing live gates: {', '.join(sorted(missing))}")
    for name in REQUIRED_LIVE_GATES & set(gates):
        if not isinstance(gates[name], bool):
            errors.append(f"live gate {name} must be boolean")
    evidence = policy.get("live_gate_evidence")
    if not isinstance(evidence, dict):
        errors.append("live_gate_evidence must be an object")
        evidence = {}
    missing_evidence = REQUIRED_LIVE_GATES - set(evidence)
    if missing_evidence:
        errors.append(
            f"missing live gate evidence: {', '.join(sorted(missing_evidence))}"
        )
    for name in REQUIRED_LIVE_GATES & set(evidence):
        if evidence[name] is not None and not isinstance(evidence[name], str):
            errors.append(f"live gate evidence {name} must be a path or null")
    if not isinstance(policy.get("v03_holdout_promotion"), bool):
        errors.append("v03_holdout_promotion must be boolean")
    if not isinstance(policy.get("v04_holdout_promotion"), bool):
        errors.append("v04_holdout_promotion must be boolean")
    approval = policy.get("approval")
    if not isinstance(approval, dict):
        errors.append("approval must be an object")
    else:
        for name in ("approved_by", "approved_at_jst", "evidence_path"):
            if approval.get(name) is not None and not isinstance(approval.get(name), str):
                errors.append(f"approval.{name} must be a string or null")
        approved_at = approval.get("approved_at_jst")
        if approved_at is not None and not _is_aware_timestamp(approved_at):
            errors.append("approval.approved_at_jst must be an aware ISO timestamp or null")
    return errors


def live_gate_failures(policy: dict[str, Any]) -> list[str]:
    failures = [
        name
        for name in sorted(REQUIRED_LIVE_GATES)
        if policy.get("live_gates", {}).get(name) is not True
    ]
    failures.extend(
        f"live_gate_evidence.{name}"
        for name in sorted(REQUIRED_LIVE_GATES)
        if policy.get("live_gates", {}).get(name) is True
        and not policy.get("live_gate_evidence", {}).get(name)
    )
    if policy.get("active_rule_version") == "v0.3" and not policy.get(
        "v03_holdout_promotion"
    ):
        failures.append("v03_holdout_promotion")
    if policy.get("active_rule_version") == "v0.4" and not policy.get(
        "v04_holdout_promotion"
    ):
        failures.append("v04_holdout_promotion")
    approval = policy.get("approval", {})
    for name in ("approved_by", "approved_at_jst", "evidence_path"):
        if not approval.get(name):
            failures.append(f"approval.{name}")
    return failures


def policy_status(policy: dict[str, Any]) -> dict[str, Any]:
    errors = validate_policy(policy)
    failures = live_gate_failures(policy) if not errors else []
    mode = policy.get("operation_mode")
    live_orders_allowed = mode == "LIVE" and not errors and not failures
    if errors:
        ticket_status = "BLOCKED"
    elif mode == "PAPER":
        ticket_status = "PAPER_PROPOSED"
    elif live_orders_allowed:
        ticket_status = "PROPOSED"
    else:
        ticket_status = "BLOCKED"
    return {
        "valid": not errors,
        "operation_mode": mode,
        "active_rule_version": policy.get("active_rule_version"),
        "broker_submission": policy.get("broker_submission"),
        "live_orders_allowed": live_orders_allowed,
        "ticket_status": ticket_status,
        "validation_errors": errors,
        "live_gate_failures": failures,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("validate", "status"))
    parser.add_argument("--policy", type=Path, default=DEFAULT_POLICY)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    status = policy_status(load_policy(args.policy))
    print(json.dumps(status, ensure_ascii=False, indent=2, sort_keys=True))
    if args.command == "validate" and not status["valid"]:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
