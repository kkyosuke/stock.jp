"""Fail-closed validators for evidence used by LIVE promotion gates."""

from __future__ import annotations

import argparse
import csv
from datetime import date, datetime
import hashlib
import json
import math
import os
from pathlib import Path
import re
import tempfile
from typing import Any
from zoneinfo import ZoneInfo

try:
    from scripts.run_integrity import RunIntegrityError, validate_run_artifacts
except ModuleNotFoundError:  # Direct execution from scripts/
    from run_integrity import RunIntegrityError, validate_run_artifacts


PROJECT_ROOT = Path(__file__).resolve().parents[1]
JST = ZoneInfo("Asia/Tokyo")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
POINT_IN_TIME_GATE = "point_in_time_full_universe_validation"
HISTORICAL_REPLAY_GATE = "historical_replay_2025_2026_accepted"
PAPER_DURATION_GATE = "minimum_12_month_paper_trade"
SHADOW_RUN_GATE = "twenty_day_shadow_run"
OFFICIAL_COVERAGE_GATE = "official_source_coverage"
REPOSITORY_RECOVERY_GATE = "private_repository_recovery"
OFFICIAL_SOURCE_NAMES = ("tdnet", "edinet", "company_ir", "jpx")
OFFICIAL_SOURCE_ALIASES = {
    "tdnet": "tdnet",
    "edinet": "edinet",
    "company_ir": "company_ir",
    "ir": "company_ir",
    "jpx": "jpx",
    "jpx_notice": "jpx",
}


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


def _directory_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    for child in sorted(item for item in path.rglob("*") if item.is_file()):
        relative = child.relative_to(path).as_posix().encode()
        digest.update(len(relative).to_bytes(8, "big"))
        digest.update(relative)
        content = child.read_bytes()
        digest.update(len(content).to_bytes(8, "big"))
        digest.update(content)
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


def _parse_aware_datetime(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("datetime must include a UTC offset")
    return parsed.astimezone(JST)


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


def evaluate_historical_replay(
    *,
    root: Path = PROJECT_ROOT,
    result_path: Path | None = None,
    review_path: Path | None = None,
) -> dict[str, Any]:
    """Validate the fixed 2025-2026 replay and a private human acceptance."""
    root = root.resolve()
    result_path = result_path or (
        root / "data/historical-replay/replay-result-2025-2026.json"
    )
    review_path = review_path or (
        root / "operations/private/evidence/historical-replay-review.json"
    )
    blockers: list[str] = []
    inputs: list[dict[str, str]] = []

    try:
        result_path.resolve().relative_to(root)
    except ValueError:
        blockers.append("replay result path must stay under project root")
    if not result_path.is_file():
        blockers.append(f"replay result is missing: {result_path}")
        result: dict[str, Any] = {}
    else:
        try:
            result = _read_object(result_path)
        except (OSError, ValueError, json.JSONDecodeError) as error:
            blockers.append(f"replay result is invalid: {error}")
            result = {}
        else:
            inputs.append(
                {
                    "role": "replay_result",
                    "path": os.path.relpath(result_path.resolve(), root),
                    "sha256": _sha256(result_path),
                }
            )

    private_root = (root / "operations/private").resolve()
    try:
        review_path.resolve().relative_to(private_root)
    except ValueError:
        blockers.append("review path must stay under operations/private")
    if not review_path.is_file():
        blockers.append(f"private replay review is missing: {review_path}")
        review: dict[str, Any] = {}
    else:
        try:
            review = _read_object(review_path)
        except (OSError, ValueError, json.JSONDecodeError) as error:
            blockers.append(f"private replay review is invalid: {error}")
            review = {}
        else:
            inputs.append(
                {
                    "role": "private_review",
                    "path": "operations/private/evidence/historical-replay-review.json",
                    "sha256": _sha256(review_path),
                }
            )

    if result:
        if result.get("schema_version") != "1.0":
            blockers.append("replay result schema_version must be 1.0")
        if result.get("status") != "COMPLETED":
            blockers.append("replay result status must be COMPLETED")
        if result.get("rule_version") != "v0.4":
            blockers.append("replay result rule_version must be v0.4")
        period = result.get("period")
        if not isinstance(period, dict):
            period = {}
            blockers.append("replay result period must be an object")
        if period.get("from") != "2025-01-01":
            blockers.append("replay period.from must be 2025-01-01")
        if period.get("through") != "2026-08-31":
            blockers.append("replay period.through must be 2026-08-31")
        if not _aware_datetime(result.get("generated_at_jst")):
            blockers.append("replay generated_at_jst must include a UTC offset")
        quality = result.get("quality")
        if not isinstance(quality, dict):
            quality = {}
            blockers.append("replay quality must be an object")
        for field in ("missing_hard_gate_inputs", "lookahead_violations"):
            if quality.get(field) != 0:
                blockers.append(f"replay quality.{field} must be 0")
        metrics = result.get("metrics")
        if not isinstance(metrics, dict):
            metrics = {}
            blockers.append("replay metrics must be an object")
        for field in (
            "trade_count",
            "total_return_pct",
            "max_drawdown_pct",
            "benchmark_return_pct",
        ):
            value = metrics.get(field)
            if (
                not isinstance(value, (int, float))
                or isinstance(value, bool)
                or not math.isfinite(value)
            ):
                blockers.append(f"replay metrics.{field} must be numeric")
        if not isinstance(metrics.get("trade_count"), int) or metrics.get(
            "trade_count", 0
        ) <= 0:
            blockers.append("replay metrics.trade_count must be a positive integer")

        point_manifest = (
            root / "data/historical-replay/point-in-time-validation.json"
        )
        point_evaluation = evaluate_point_in_time(
            root=root, manifest_path=point_manifest
        )
        if not point_evaluation["eligible"]:
            blockers.append("point-in-time full-universe validation is not eligible")
        expected_point_hash = result.get("point_in_time_manifest_sha256")
        if not point_manifest.is_file() or expected_point_hash != _sha256(
            point_manifest
        ):
            blockers.append("point-in-time manifest hash does not match replay result")

        artifacts = result.get("artifacts")
        if not isinstance(artifacts, list) or not artifacts:
            artifacts = []
            blockers.append("replay artifacts must be a non-empty array")
        roles: set[str] = set()
        for index, artifact in enumerate(artifacts):
            if not isinstance(artifact, dict):
                blockers.append(f"replay artifacts[{index}] must be an object")
                continue
            role = artifact.get("role")
            relative = artifact.get("path")
            expected_hash = artifact.get("sha256")
            if isinstance(role, str):
                roles.add(role)
            path = _safe_project_path(root, relative)
            if path is None or not path.is_file():
                blockers.append(f"replay artifact is missing or unsafe: {relative}")
            elif not isinstance(expected_hash, str) or not SHA256_RE.fullmatch(
                expected_hash
            ):
                blockers.append(f"replay artifacts[{index}].sha256 is invalid")
            elif _sha256(path) != expected_hash:
                blockers.append(f"replay artifact hash mismatch: {relative}")
            else:
                inputs.append(
                    {"role": str(role), "path": str(relative), "sha256": expected_hash}
                )
        for role in ("trade_log", "metrics", "monthly_returns"):
            if role not in roles:
                blockers.append(f"replay artifact role is missing: {role}")
    else:
        metrics = {}

    if review:
        if review.get("schema_version") != "1.0":
            blockers.append("private review schema_version must be 1.0")
        if review.get("decision") != "ACCEPT":
            blockers.append("private review decision must be ACCEPT")
        if review.get("rule_version") != "v0.4":
            blockers.append("private review rule_version must be v0.4")
        if not isinstance(review.get("accepted_by"), str) or not review.get(
            "accepted_by", ""
        ).strip():
            blockers.append("private review accepted_by is required")
        if not _aware_datetime(review.get("accepted_at_jst")):
            blockers.append("private review accepted_at_jst must include a UTC offset")
        if result_path.is_file() and review.get("replay_result_sha256") != _sha256(
            result_path
        ):
            blockers.append("private review does not bind the current replay result")
        for field in (
            "drawdown_reviewed",
            "concentration_loss_reviewed",
            "data_limitations_reviewed",
        ):
            if review.get(field) is not True:
                blockers.append(f"private review {field} must be true")

    return _result(
        gate=HISTORICAL_REPLAY_GATE,
        blockers=blockers,
        metrics={
            "period_from": result.get("period", {}).get("from")
            if isinstance(result.get("period"), dict)
            else None,
            "period_through": result.get("period", {}).get("through")
            if isinstance(result.get("period"), dict)
            else None,
            "trade_count": metrics.get("trade_count"),
            "max_drawdown_pct": metrics.get("max_drawdown_pct"),
            "human_accepted": review.get("decision") == "ACCEPT",
        },
        inputs=inputs,
    )


def evaluate_paper_duration(
    *, root: Path = PROJECT_ROOT, history_path: Path | None = None
) -> dict[str, Any]:
    """Require at least 365 elapsed days of recorded v0.4 PAPER operation."""
    root = root.resolve()
    private_root = (root / "operations/private").resolve()
    history_path = history_path or (private_root / "run-history.csv")
    blockers: list[str] = []
    inputs: list[dict[str, str]] = []
    try:
        history_path.resolve().relative_to(private_root)
    except ValueError:
        blockers.append("run history path must stay under operations/private")
    if not history_path.is_file():
        return _result(
            gate=PAPER_DURATION_GATE,
            blockers=[*blockers, f"run history is missing: {history_path}"],
            metrics={},
            inputs=[],
        )

    try:
        with history_path.open(encoding="utf-8", newline="") as source:
            reader = csv.DictReader(source)
            rows = list(reader)
            fields = set(reader.fieldnames or [])
    except (OSError, csv.Error) as error:
        return _result(
            gate=PAPER_DURATION_GATE,
            blockers=[*blockers, f"run history is invalid: {error}"],
            metrics={},
            inputs=[],
        )
    required_fields = {
        "run_id",
        "attempt",
        "completed_at_jst",
        "status",
        "operation_mode",
        "active_rule_version",
        "price_date",
        "report_path",
    }
    missing_fields = required_fields - fields
    if missing_fields:
        blockers.append(
            "run history fields are missing: " + ", ".join(sorted(missing_fields))
        )

    latest_by_run: dict[str, tuple[int, dict[str, str]]] = {}
    for index, row in enumerate(rows, start=2):
        run_id = row.get("run_id", "").strip()
        try:
            attempt = int(row.get("attempt", ""))
        except ValueError:
            blockers.append(f"run history row {index} has an invalid attempt")
            continue
        if not run_id:
            blockers.append(f"run history row {index} has no run_id")
            continue
        previous = latest_by_run.get(run_id)
        if previous is None or attempt > previous[0]:
            latest_by_run[run_id] = (attempt, row)

    latest_rows = [value[1] for value in latest_by_run.values()]
    if any(row.get("operation_mode", "").strip() == "LIVE" for row in latest_rows):
        blockers.append("pre-promotion LIVE run exists in run history")

    successful: list[tuple[datetime, date, str]] = []
    for row in latest_rows:
        if not (
            row.get("status", "").strip() == "COMPLETED"
            and row.get("operation_mode", "").strip() == "PAPER"
            and row.get("active_rule_version", "").strip() == "v0.4"
        ):
            continue
        run_id = row.get("run_id", "").strip()
        try:
            completed = _parse_aware_datetime(row.get("completed_at_jst", ""))
            price_date = date.fromisoformat(row.get("price_date", ""))
        except ValueError:
            blockers.append(f"completed v0.4 PAPER run has invalid dates: {run_id}")
            continue
        report_value = row.get("report_path", "").strip()
        if not report_value:
            blockers.append(f"completed run report path is unsafe: {run_id}")
            continue
        report = (root / report_value).resolve()
        try:
            report.relative_to(private_root)
        except ValueError:
            blockers.append(f"completed run report is outside private storage: {run_id}")
            continue
        if not report.is_file():
            blockers.append(f"completed run report is missing: {run_id}")
            continue
        successful.append((completed, price_date, run_id))

    successful.sort()
    if successful:
        first_completed = successful[0][0]
        last_completed = successful[-1][0]
        elapsed_days = (last_completed.date() - first_completed.date()).days
        months = sorted({completed.strftime("%Y-%m") for completed, _, _ in successful})
        if elapsed_days < 365:
            blockers.append("v0.4 PAPER elapsed duration is less than 365 days")
        if len(months) < 12:
            blockers.append("v0.4 PAPER does not contain successful runs in 12 months")
        cursor = date(first_completed.year, first_completed.month, 1)
        final_month = date(last_completed.year, last_completed.month, 1)
        expected_months: list[str] = []
        while cursor <= final_month:
            expected_months.append(cursor.strftime("%Y-%m"))
            if cursor.month == 12:
                cursor = date(cursor.year + 1, 1, 1)
            else:
                cursor = date(cursor.year, cursor.month + 1, 1)
        empty_months = sorted(set(expected_months) - set(months))
        if empty_months:
            blockers.append(
                "v0.4 PAPER has calendar months without a successful run: "
                + ", ".join(empty_months)
            )
    else:
        first_completed = None
        last_completed = None
        elapsed_days = 0
        months = []
        blockers.append("no completed v0.4 PAPER runs were found")

    inputs.append(
        {
            "role": "run_history",
            "path": "operations/private/run-history.csv",
            "sha256": _sha256(history_path),
        }
    )
    return _result(
        gate=PAPER_DURATION_GATE,
        blockers=blockers,
        metrics={
            "first_completed_at_jst": first_completed.isoformat(timespec="seconds")
            if first_completed
            else None,
            "last_completed_at_jst": last_completed.isoformat(timespec="seconds")
            if last_completed
            else None,
            "elapsed_days": elapsed_days,
            "successful_run_count": len(successful),
            "calendar_month_count": len(months),
        },
        inputs=inputs,
    )


def evaluate_shadow_run(
    *, root: Path = PROJECT_ROOT, history_path: Path | None = None
) -> dict[str, Any]:
    """Validate the latest 20 tracked trading sessions as real v0.4 PAPER runs."""
    root = root.resolve()
    private_root = (root / "operations/private").resolve()
    history_path = history_path or (private_root / "run-history.csv")
    blockers: list[str] = []
    inputs: list[dict[str, str]] = []
    if not history_path.is_file():
        return _result(
            gate=SHADOW_RUN_GATE,
            blockers=[f"run history is missing: {history_path}"],
            metrics={},
            inputs=[],
        )
    try:
        history_path.resolve().relative_to(private_root)
    except ValueError:
        blockers.append("run history path must stay under operations/private")
    try:
        with history_path.open(encoding="utf-8", newline="") as source:
            rows = list(csv.DictReader(source))
    except (OSError, csv.Error) as error:
        return _result(
            gate=SHADOW_RUN_GATE,
            blockers=[*blockers, f"run history is invalid: {error}"],
            metrics={},
            inputs=[],
        )

    latest_by_run: dict[str, tuple[int, dict[str, str]]] = {}
    for index, row in enumerate(rows, start=2):
        run_id = row.get("run_id", "").strip()
        try:
            attempt = int(row.get("attempt", ""))
        except ValueError:
            blockers.append(f"run history row {index} has an invalid attempt")
            continue
        if not run_id:
            blockers.append(f"run history row {index} has no run_id")
            continue
        previous = latest_by_run.get(run_id)
        if previous is None or attempt > previous[0]:
            latest_by_run[run_id] = (attempt, row)

    candidates: list[tuple[date, dict[str, str]]] = []
    for _, row in latest_by_run.values():
        if not (
            row.get("status", "").strip() == "COMPLETED"
            and row.get("operation_mode", "").strip() == "PAPER"
            and row.get("active_rule_version", "").strip() == "v0.4"
        ):
            continue
        try:
            price_date = date.fromisoformat(row.get("price_date", ""))
            _parse_aware_datetime(row.get("completed_at_jst", ""))
            _parse_aware_datetime(row.get("source_cutoff_jst", ""))
        except ValueError:
            blockers.append(
                f"completed shadow run has invalid timestamps: {row.get('run_id', '')}"
            )
            continue
        candidates.append((price_date, row))
    candidates.sort(key=lambda item: (item[0], item[1].get("run_id", "")))
    selected = candidates[-20:]
    selected_dates = [item[0] for item in selected]
    if len(selected) < 20:
        blockers.append("fewer than 20 completed v0.4 PAPER trading sessions")
    if len(set(selected_dates)) != len(selected_dates):
        blockers.append("shadow runs contain duplicate price dates")

    archive_dates: list[date] = []
    archive_by_date: dict[date, Path] = {}
    for price_file in (root / "data/daily-prices").glob("????/????-??-??.csv"):
        try:
            archive_date = date.fromisoformat(price_file.stem)
        except ValueError:
            continue
        archive_dates.append(archive_date)
        archive_by_date[archive_date] = price_file
    if selected_dates:
        expected_dates = sorted(
            item for item in archive_dates if item <= selected_dates[-1]
        )[-20:]
        if len(expected_dates) < 20:
            blockers.append("public price archive has fewer than 20 trading sessions")
        elif selected_dates != expected_dates:
            blockers.append(
                "latest shadow runs do not cover 20 consecutive tracked trading sessions"
            )
    else:
        expected_dates = []

    ticket_ids: set[str] = set()
    order_keys: set[tuple[str, str, str]] = set()
    order_count = 0
    for price_date, row in selected:
        run_id = row.get("run_id", "").strip()
        if run_id != price_date.isoformat():
            blockers.append(f"shadow run_id must equal price_date: {run_id}")
        try:
            alert_count = int(row.get("alert_count", ""))
            data_gap_count = int(row.get("data_gap_count", ""))
            recorded_order_count = int(row.get("order_count", ""))
        except ValueError:
            blockers.append(f"shadow run counts are invalid: {run_id}")
            continue
        if alert_count != 0:
            blockers.append(f"shadow run has alerts: {run_id}")
        if data_gap_count != 0:
            blockers.append(f"shadow run has data gaps: {run_id}")
        try:
            validate_run_artifacts(
                root=root,
                run_id=run_id,
                completed_at=row.get("completed_at_jst", ""),
                source_cutoff=row.get("source_cutoff_jst", ""),
                price_date=price_date.isoformat(),
            )
        except (RunIntegrityError, OSError, ValueError, json.JSONDecodeError) as error:
            blockers.append(f"shadow run integrity failed for {run_id}: {error}")
            continue
        run_dir = private_root / "runs" / run_id
        orders_path = run_dir / "orders.csv"
        try:
            with orders_path.open(encoding="utf-8", newline="") as source:
                orders = list(csv.DictReader(source))
        except (OSError, csv.Error) as error:
            blockers.append(f"shadow orders are invalid for {run_id}: {error}")
            continue
        if len(orders) != recorded_order_count:
            blockers.append(f"shadow order_count mismatch: {run_id}")
        order_count += len(orders)
        for order in orders:
            ticket_id = order.get("ticket_id", "").strip()
            key = (
                order.get("code", "").strip(),
                order.get("side", "").strip(),
                order.get("trade_date", "").strip(),
            )
            if ticket_id in ticket_ids:
                blockers.append(f"duplicate ticket_id across shadow runs: {ticket_id}")
            if key in order_keys:
                blockers.append("duplicate code/side/trade_date across shadow runs")
            ticket_ids.add(ticket_id)
            order_keys.add(key)
        inputs.append(
            {
                "role": "run_artifacts",
                "path": f"operations/private/runs/{run_id}",
                "sha256": _directory_sha256(run_dir),
            }
        )
        price_file = archive_by_date.get(price_date)
        if price_file:
            inputs.append(
                {
                    "role": "price_session",
                    "path": os.path.relpath(price_file, root),
                    "sha256": _sha256(price_file),
                }
            )

    inputs.insert(
        0,
        {
            "role": "run_history",
            "path": "operations/private/run-history.csv",
            "sha256": _sha256(history_path),
        },
    )
    return _result(
        gate=SHADOW_RUN_GATE,
        blockers=blockers,
        metrics={
            "consecutive_session_count": len(selected),
            "first_price_date": selected_dates[0].isoformat()
            if selected_dates
            else None,
            "last_price_date": selected_dates[-1].isoformat()
            if selected_dates
            else None,
            "alert_count": sum(
                int(row.get("alert_count", "0"))
                for _, row in selected
                if row.get("alert_count", "").isdigit()
            ),
            "data_gap_count": sum(
                int(row.get("data_gap_count", "0"))
                for _, row in selected
                if row.get("data_gap_count", "").isdigit()
            ),
            "order_count": order_count,
            "duplicate_ticket_count": len(
                [item for item in blockers if item.startswith("duplicate ticket_id")]
            ),
        },
        inputs=inputs,
    )


def evaluate_official_coverage(
    *, root: Path = PROJECT_ROOT, history_path: Path | None = None
) -> dict[str, Any]:
    """Validate official-source evidence for every run in the 20-day window."""
    root = root.resolve()
    private_root = (root / "operations/private").resolve()
    history_path = history_path or (private_root / "run-history.csv")
    blockers: list[str] = []
    shadow = evaluate_shadow_run(root=root, history_path=history_path)
    if not shadow["eligible"]:
        blockers.append("20-day shadow run is not eligible")
    if not history_path.is_file():
        return _result(
            gate=OFFICIAL_COVERAGE_GATE,
            blockers=[*blockers, f"run history is missing: {history_path}"],
            metrics={},
            inputs=[],
        )
    with history_path.open(encoding="utf-8", newline="") as source:
        rows = list(csv.DictReader(source))
    latest: dict[str, tuple[int, dict[str, str]]] = {}
    for row in rows:
        run_id = row.get("run_id", "").strip()
        try:
            attempt = int(row.get("attempt", ""))
        except ValueError:
            continue
        if not run_id:
            continue
        if run_id not in latest or attempt > latest[run_id][0]:
            latest[run_id] = (attempt, row)
    completed = [
        row
        for _, row in latest.values()
        if row.get("status", "").strip() == "COMPLETED"
        and row.get("operation_mode", "").strip() == "PAPER"
        and row.get("active_rule_version", "").strip() == "v0.4"
    ]
    completed.sort(key=lambda row: row.get("price_date", ""))
    selected = completed[-20:]
    if len(selected) != 20:
        blockers.append("official coverage requires exactly 20 completed shadow runs")

    inputs = [
        {
            "role": "run_history",
            "path": "operations/private/run-history.csv",
            "sha256": _sha256(history_path),
        }
    ]
    checked_counts = {name: 0 for name in OFFICIAL_SOURCE_NAMES}
    live_network_count = 0
    latest_cutoffs: dict[str, datetime] = {}
    for row in selected:
        run_id = row.get("run_id", "").strip()
        run_dir = private_root / "runs" / run_id
        coverage_path = run_dir / "coverage.json"
        health_path = run_dir / "provider-health.json"
        sources_path = run_dir / "sources.csv"
        for path in (coverage_path, health_path, sources_path):
            if not path.is_file():
                blockers.append(f"official coverage artifact is missing: {run_id}/{path.name}")
        if not all(path.is_file() for path in (coverage_path, health_path, sources_path)):
            continue
        try:
            coverage = _read_object(coverage_path)
            health = _read_object(health_path)
            with sources_path.open(encoding="utf-8", newline="") as source:
                source_rows = list(csv.DictReader(source))
        except (OSError, ValueError, json.JSONDecodeError, csv.Error) as error:
            blockers.append(f"official coverage artifacts are invalid for {run_id}: {error}")
            continue
        if coverage.get("run_id") != run_id or coverage.get("status") != "COMPLETED":
            blockers.append(f"coverage is not completed for {run_id}")
        if coverage.get("data_gaps") not in ([], None):
            unresolved = [
                gap
                for gap in coverage.get("data_gaps", [])
                if not isinstance(gap, dict)
                or str(gap.get("status", "OPEN")).upper() != "RESOLVED"
            ]
            if unresolved:
                blockers.append(f"coverage has unresolved data gaps for {run_id}")
        if health.get("run_id") != run_id:
            blockers.append(f"provider-health run_id mismatch for {run_id}")
        if health.get("input_mode") != "LIVE_NETWORK":
            blockers.append(f"provider-health is not a live network scan for {run_id}")
        else:
            live_network_count += 1
        if health.get("status") not in {"COMPLETED", "PARTIAL"}:
            blockers.append(f"provider-health has not completed for {run_id}")
        edinet_provider = health.get("providers", {}).get("edinet", {})
        if edinet_provider.get("status") != "OK" or not isinstance(
            edinet_provider.get("request_count"), int
        ) or edinet_provider.get("request_count", 0) < 1:
            blockers.append(f"EDINET network scan is incomplete for {run_id}")

        by_category: dict[str, list[dict[str, str]]] = {
            name: [] for name in OFFICIAL_SOURCE_NAMES
        }
        for source_row in source_rows:
            category = source_row.get("category", "").strip().lower().replace("-", "_")
            normalized = OFFICIAL_SOURCE_ALIASES.get(category, category)
            if normalized in by_category:
                by_category[normalized].append(source_row)
            if (
                source_row.get("used_for_decision", "").strip().lower()
                in {"true", "1"}
                and normalized in by_category
                and source_row.get("primary_source", "").strip().lower()
                not in {"true", "1"}
            ):
                blockers.append(
                    f"decision used a non-primary {normalized} source for {run_id}"
                )
        official = coverage.get("official_sources")
        if not isinstance(official, dict):
            blockers.append(f"official_sources is missing for {run_id}")
            official = {}
        for name in OFFICIAL_SOURCE_NAMES:
            item = official.get(name, {})
            required = item.get("required") is True
            source_status = item.get("status")
            if required and source_status != "CHECKED":
                blockers.append(f"required source {name} is not CHECKED for {run_id}")
                continue
            if not required and source_status not in {"CHECKED", "NOT_APPLICABLE"}:
                blockers.append(
                    f"optional source {name} is not terminal for {run_id}"
                )
                continue
            if source_status == "CHECKED":
                checked_counts[name] += 1
                if not by_category[name]:
                    blockers.append(f"source {name} has no evidence row for {run_id}")
                elif not any(
                    evidence.get("primary_source", "").strip().lower()
                    in {"true", "1"}
                    for evidence in by_category[name]
                ):
                    blockers.append(f"source {name} has no primary evidence for {run_id}")
        try:
            cutoff = _parse_aware_datetime(row.get("source_cutoff_jst", ""))
        except ValueError:
            blockers.append(f"source cutoff is invalid for {run_id}")
        else:
            for name in OFFICIAL_SOURCE_NAMES:
                if official.get(name, {}).get("status") == "CHECKED":
                    latest_cutoffs[name] = max(cutoff, latest_cutoffs.get(name, cutoff))
        inputs.extend(
            {
                "role": role,
                "path": f"operations/private/runs/{run_id}/{path.name}",
                "sha256": _sha256(path),
            }
            for role, path in (
                ("coverage", coverage_path),
                ("provider_health", health_path),
                ("sources", sources_path),
            )
        )

    watermarks_path = private_root / "source-watermarks.json"
    if not watermarks_path.is_file():
        blockers.append("source-watermarks.json is missing")
    else:
        try:
            watermarks = _read_object(watermarks_path).get("sources", {})
        except (OSError, ValueError, json.JSONDecodeError) as error:
            blockers.append(f"source-watermarks.json is invalid: {error}")
            watermarks = {}
        for name, cutoff in latest_cutoffs.items():
            value = watermarks.get(name, {}).get("last_successful_cutoff_jst")
            try:
                watermark = _parse_aware_datetime(value)
            except (TypeError, ValueError):
                blockers.append(f"source watermark is missing or invalid: {name}")
                continue
            if watermark < cutoff:
                blockers.append(f"source watermark is behind the shadow window: {name}")
        inputs.append(
            {
                "role": "source_watermarks",
                "path": "operations/private/source-watermarks.json",
                "sha256": _sha256(watermarks_path),
            }
        )

    return _result(
        gate=OFFICIAL_COVERAGE_GATE,
        blockers=blockers,
        metrics={
            "run_count": len(selected),
            "live_network_run_count": live_network_count,
            "checked_run_counts": checked_counts,
        },
        inputs=inputs,
    )


def evaluate_repository_recovery(
    *,
    root: Path = PROJECT_ROOT,
    drill_path: Path | None = None,
    at: datetime | None = None,
) -> dict[str, Any]:
    """Validate a recent clean-clone and mirror recovery drill."""
    root = root.resolve()
    private_root = (root / "operations/private").resolve()
    drill_path = drill_path or (private_root / "evidence/recovery-drill.json")
    blockers: list[str] = []
    try:
        drill_path.resolve().relative_to(private_root / "evidence")
    except ValueError:
        blockers.append("recovery drill path must stay under private evidence")
    if not drill_path.is_file():
        return _result(
            gate=REPOSITORY_RECOVERY_GATE,
            blockers=[*blockers, f"recovery drill is missing: {drill_path}"],
            metrics={},
            inputs=[],
        )
    try:
        drill = _read_object(drill_path)
    except (OSError, ValueError, json.JSONDecodeError) as error:
        return _result(
            gate=REPOSITORY_RECOVERY_GATE,
            blockers=[*blockers, f"recovery drill is invalid: {error}"],
            metrics={},
            inputs=[],
        )
    if drill.get("schema_version") != "1.0":
        blockers.append("recovery drill schema_version must be 1.0")
    if not isinstance(drill.get("drill_id"), str) or not drill.get(
        "drill_id", ""
    ).strip():
        blockers.append("recovery drill_id is required")
    if not isinstance(drill.get("operator"), str) or not drill.get(
        "operator", ""
    ).strip():
        blockers.append("recovery operator is required")
    if drill.get("repository_visibility") != "PRIVATE":
        blockers.append("repository_visibility must be PRIVATE")

    started: datetime | None = None
    completed: datetime | None = None
    try:
        started = _parse_aware_datetime(drill.get("started_at_jst", ""))
        completed = _parse_aware_datetime(drill.get("completed_at_jst", ""))
    except (TypeError, ValueError):
        blockers.append("recovery timestamps must include UTC offsets")
    reference_time = (at or datetime.now(tz=JST)).astimezone(JST)
    if started and completed:
        if completed < started:
            blockers.append("recovery completed_at_jst cannot precede started_at_jst")
        if completed > reference_time:
            blockers.append("recovery completed_at_jst cannot be in the future")
        if (completed - started).total_seconds() > 24 * 60 * 60:
            blockers.append("recovery drill duration exceeds 24 hours")
        if (reference_time - completed).total_seconds() > 90 * 24 * 60 * 60:
            blockers.append("recovery drill is older than 90 days")

    source_commit = drill.get("source_private_commit")
    recovered_commit = drill.get("recovered_private_commit")
    source_public = drill.get("source_public_submodule_commit")
    recovered_public = drill.get("recovered_public_submodule_commit")
    for name, value in (
        ("source_private_commit", source_commit),
        ("recovered_private_commit", recovered_commit),
        ("source_public_submodule_commit", source_public),
        ("recovered_public_submodule_commit", recovered_public),
    ):
        if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{40}", value):
            blockers.append(f"recovery {name} must be a full commit SHA")
    if source_commit != recovered_commit:
        blockers.append("recovered private commit does not match source")
    if source_public != recovered_public:
        blockers.append("recovered public submodule commit does not match source")

    checks = drill.get("checks")
    if not isinstance(checks, dict):
        checks = {}
        blockers.append("recovery checks must be an object")
    required_checks = (
        "clean_clone_with_submodules",
        "workspace_setup",
        "state_validation",
        "paper_bootstrap_check",
        "latest_successful_run",
        "latest_handoff",
        "all_ledgers",
        "unreconciled_orders",
        "private_remote_restore",
        "access_controlled_mirror_restore",
    )
    for name in required_checks:
        if checks.get(name) is not True:
            blockers.append(f"recovery check {name} must be true")
    if not isinstance(drill.get("result_notes"), str) or not drill.get(
        "result_notes", ""
    ).strip():
        blockers.append("recovery result_notes is required")

    return _result(
        gate=REPOSITORY_RECOVERY_GATE,
        blockers=blockers,
        metrics={
            "drill_id": drill.get("drill_id"),
            "completed_at_jst": completed.isoformat(timespec="seconds")
            if completed
            else None,
            "age_days": (reference_time - completed).days if completed else None,
            "required_check_count": len(required_checks),
            "passed_check_count": sum(checks.get(name) is True for name in required_checks),
        },
        inputs=[
            {
                "role": "recovery_drill",
                "path": "operations/private/evidence/recovery-drill.json",
                "sha256": _sha256(drill_path),
            }
        ],
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
    replay = subparsers.add_parser(
        "historical-replay", help="validate and accept the fixed 2025-2026 replay"
    )
    replay.add_argument("--result", type=Path)
    replay.add_argument("--review", type=Path)
    replay.add_argument("--write-evidence", type=Path)
    paper_duration = subparsers.add_parser(
        "paper-duration", help="validate at least 12 months of v0.4 PAPER runs"
    )
    paper_duration.add_argument("--history", type=Path)
    paper_duration.add_argument("--write-evidence", type=Path)
    shadow = subparsers.add_parser(
        "shadow-run", help="validate 20 consecutive real-data PAPER sessions"
    )
    shadow.add_argument("--history", type=Path)
    shadow.add_argument("--write-evidence", type=Path)
    coverage = subparsers.add_parser(
        "official-coverage", help="validate official sources across the shadow window"
    )
    coverage.add_argument("--history", type=Path)
    coverage.add_argument("--write-evidence", type=Path)
    recovery = subparsers.add_parser(
        "repository-recovery", help="validate a recent private recovery drill"
    )
    recovery.add_argument("--drill", type=Path)
    recovery.add_argument("--at", type=str)
    recovery.add_argument("--write-evidence", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    if args.command == "point-in-time":
        result = evaluate_point_in_time(
            root=args.root,
            manifest_path=args.manifest,
        )
    elif args.command == "historical-replay":
        result = evaluate_historical_replay(
            root=args.root,
            result_path=args.result,
            review_path=args.review,
        )
    elif args.command == "paper-duration":
        result = evaluate_paper_duration(
            root=args.root,
            history_path=args.history,
        )
    elif args.command == "shadow-run":
        result = evaluate_shadow_run(
            root=args.root,
            history_path=args.history,
        )
    elif args.command == "official-coverage":
        result = evaluate_official_coverage(
            root=args.root,
            history_path=args.history,
        )
    elif args.command == "repository-recovery":
        result = evaluate_repository_recovery(
            root=args.root,
            drill_path=args.drill,
            at=_parse_aware_datetime(args.at) if args.at else None,
        )
    else:  # pragma: no cover - argparse prevents this branch
        raise AssertionError(args.command)
    if args.write_evidence:
        write_private_evidence(root=args.root, path=args.write_evidence, result=result)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["eligible"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
