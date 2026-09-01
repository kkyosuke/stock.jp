"""Fail-closed validators for evidence used by LIVE promotion gates."""

from __future__ import annotations

import argparse
from datetime import date, datetime
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
from typing import Any
from zoneinfo import ZoneInfo


PROJECT_ROOT = Path(__file__).resolve().parents[1]
JST = ZoneInfo("Asia/Tokyo")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
POINT_IN_TIME_GATE = "point_in_time_full_universe_validation"


def _read_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"JSON root must be an object: {path}")
    return value


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _safe_project_path(root: Path, relative: Any) -> Path | None:
    if not isinstance(relative, str) or not relative.strip():
        return None
    candidate = (root / relative).resolve()
    try:
        candidate.relative_to(root.resolve())
    except ValueError:
        return None
    return candidate


def _aware_datetime(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    return parsed.tzinfo is not None


def _iso_date(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    try:
        date.fromisoformat(value)
    except ValueError:
        return False
    return True


def _result(
    *,
    gate: str,
    blockers: list[str],
    metrics: dict[str, Any],
    inputs: list[dict[str, str]],
) -> dict[str, Any]:
    return {
        "schema_version": "1.0",
        "gate": gate,
        "evaluated_at_jst": datetime.now(tz=JST).isoformat(timespec="seconds"),
        "eligible": not blockers,
        "blockers": blockers,
        "metrics": metrics,
        "inputs": inputs,
    }


def evaluate_point_in_time(
    *, root: Path = PROJECT_ROOT, manifest_path: Path | None = None
) -> dict[str, Any]:
    """Validate a point-in-time full-universe replay manifest and its artifacts."""
    root = root.resolve()
    manifest_path = manifest_path or (
        root / "data/historical-replay/point-in-time-validation.json"
    )
    try:
        manifest_path.resolve().relative_to(root)
    except ValueError:
        return _result(
            gate=POINT_IN_TIME_GATE,
            blockers=["manifest path must stay under project root"],
            metrics={},
            inputs=[],
        )
    if not manifest_path.is_file():
        return _result(
            gate=POINT_IN_TIME_GATE,
            blockers=[f"manifest is missing: {manifest_path}"],
            metrics={},
            inputs=[],
        )

    try:
        manifest = _read_object(manifest_path)
    except (OSError, ValueError, json.JSONDecodeError) as error:
        return _result(
            gate=POINT_IN_TIME_GATE,
            blockers=[f"manifest is invalid: {error}"],
            metrics={},
            inputs=[],
        )

    blockers: list[str] = []
    if manifest.get("schema_version") != "1.0":
        blockers.append("schema_version must be 1.0")
    if manifest.get("status") != "COMPLETED":
        blockers.append("status must be COMPLETED")
    if not _iso_date(manifest.get("as_of_date")):
        blockers.append("as_of_date must be an ISO date")
    if not _aware_datetime(manifest.get("generated_at_jst")):
        blockers.append("generated_at_jst must include a UTC offset")

    universe = manifest.get("universe")
    if not isinstance(universe, dict):
        universe = {}
        blockers.append("universe must be an object")
    required_count = universe.get("required_count")
    evaluated_count = universe.get("evaluated_count")
    if not isinstance(required_count, int) or required_count <= 0:
        blockers.append("universe.required_count must be a positive integer")
    if not isinstance(evaluated_count, int) or evaluated_count != required_count:
        blockers.append("universe.evaluated_count must equal required_count")
    for field in (
        "point_in_time_security_master",
        "includes_delisted",
        "includes_mergers",
        "includes_corporate_actions",
    ):
        if universe.get(field) is not True:
            blockers.append(f"universe.{field} must be true")

    quality = manifest.get("quality")
    if not isinstance(quality, dict):
        quality = {}
        blockers.append("quality must be an object")
    for field in ("missing_hard_gate_inputs", "lookahead_violations"):
        if quality.get(field) != 0:
            blockers.append(f"quality.{field} must be 0")

    artifacts = manifest.get("artifacts")
    if not isinstance(artifacts, list) or not artifacts:
        artifacts = []
        blockers.append("artifacts must be a non-empty array")
    roles: set[str] = set()
    checked_inputs: list[dict[str, str]] = []
    for index, artifact in enumerate(artifacts):
        if not isinstance(artifact, dict):
            blockers.append(f"artifacts[{index}] must be an object")
            continue
        role = artifact.get("role")
        relative = artifact.get("path")
        expected_hash = artifact.get("sha256")
        if not isinstance(role, str) or not role:
            blockers.append(f"artifacts[{index}].role is required")
        else:
            roles.add(role)
        path = _safe_project_path(root, relative)
        if path is None:
            blockers.append(f"artifacts[{index}].path must stay under project root")
            continue
        if not path.is_file():
            blockers.append(f"artifact is missing: {relative}")
            continue
        if not isinstance(expected_hash, str) or not SHA256_RE.fullmatch(
            expected_hash
        ):
            blockers.append(f"artifacts[{index}].sha256 is invalid")
            continue
        actual_hash = _sha256(path)
        if actual_hash != expected_hash:
            blockers.append(f"artifact hash mismatch: {relative}")
            continue
        checked_inputs.append(
            {"role": str(role), "path": str(relative), "sha256": actual_hash}
        )
    for role in ("source_snapshot", "trade_log", "metrics"):
        if role not in roles:
            blockers.append(f"artifact role is missing: {role}")

    inputs = [
        {
            "role": "manifest",
            "path": os.path.relpath(manifest_path.resolve(), root),
            "sha256": _sha256(manifest_path),
        },
        *checked_inputs,
    ]
    metrics = {
        "required_count": required_count,
        "evaluated_count": evaluated_count,
        "missing_hard_gate_inputs": quality.get("missing_hard_gate_inputs"),
        "lookahead_violations": quality.get("lookahead_violations"),
        "verified_artifact_count": len(checked_inputs),
    }
    return _result(
        gate=POINT_IN_TIME_GATE,
        blockers=blockers,
        metrics=metrics,
        inputs=inputs,
    )


def write_private_evidence(*, root: Path, path: Path, result: dict[str, Any]) -> None:
    """Write only successful evidence, and only below operations/private/evidence."""
    if result.get("eligible") is not True:
        raise ValueError("ineligible gate evidence cannot be written")
    evidence_root = (root / "operations/private/evidence").resolve()
    target = path.resolve()
    try:
        target.relative_to(evidence_root)
    except ValueError as error:
        raise ValueError("evidence path must be under operations/private/evidence") from error
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=target.parent, delete=False
    ) as temporary:
        json.dump(result, temporary, ensure_ascii=False, indent=2)
        temporary.write("\n")
        temporary_path = Path(temporary.name)
    temporary_path.chmod(0o600)
    temporary_path.replace(target)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=PROJECT_ROOT)
    subparsers = parser.add_subparsers(dest="command", required=True)
    point_in_time = subparsers.add_parser(
        "point-in-time", help="validate the full-universe point-in-time replay"
    )
    point_in_time.add_argument("--manifest", type=Path)
    point_in_time.add_argument("--write-evidence", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    if args.command == "point-in-time":
        result = evaluate_point_in_time(
            root=args.root,
            manifest_path=args.manifest,
        )
    else:  # pragma: no cover - argparse prevents this branch
        raise AssertionError(args.command)
    if args.write_evidence:
        write_private_evidence(root=args.root, path=args.write_evidence, result=result)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["eligible"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
