"""Create and validate the nightly work plan, research result, and next-day actions."""

from __future__ import annotations

import csv
from datetime import date, datetime, timedelta
import json
from pathlib import Path
import tempfile
from typing import Any
from zoneinfo import ZoneInfo


PROJECT_ROOT = Path(__file__).resolve().parents[1]
JST = ZoneInfo("Asia/Tokyo")
ACTION_FIELDS = [
    "action_id",
    "priority",
    "trade_date",
    "code",
    "company",
    "current_status",
    "next_action",
    "rule_ids",
    "trigger_type",
    "trigger_condition",
    "limit_price",
    "position_pct",
    "ticket_id",
    "human_action",
    "review_by_jst",
    "evidence_source_ids",
    "decision_log_path",
    "notes",
]
VALID_ACTIONS = {
    "BUY", "WATCH", "WAIT", "KEEP", "ADD", "REDUCE", "SELL", "NO-ACTION"
}
TRADE_ACTIONS = {"BUY", "ADD", "REDUCE", "SELL"}
VALID_PRIORITIES = {"CRITICAL", "HIGH", "NORMAL", "LOW"}
TERMINAL_TASK_STATUSES = {"COMPLETED", "DEFERRED"}


class NightlyArtifactError(ValueError):
    pass


def _parse_jst(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("datetime must include a UTC offset")
    return parsed.astimezone(JST)


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _atomic_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=path.parent, delete=False
    ) as temporary:
        json.dump(value, temporary, ensure_ascii=False, indent=2)
        temporary.write("\n")
        temporary_path = Path(temporary.name)
    temporary_path.chmod(0o600)
    temporary_path.replace(path)


def _read_csv(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    with path.open(encoding="utf-8", newline="") as source:
        reader = csv.DictReader(source)
        return list(reader.fieldnames or []), list(reader)


def _write_csv(path: Path, rows: list[dict[str, str]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as destination:
        writer = csv.DictWriter(destination, fieldnames=ACTION_FIELDS, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def _active_targets(path: Path, *, holding: bool) -> list[dict[str, str]]:
    if not path.is_file():
        return []
    _, rows = _read_csv(path)
    targets: list[dict[str, str]] = []
    for row in rows:
        code = row.get("code", "").strip()
        status = row.get("status", "").strip().upper()
        if not code or status in {"CLOSED", "SOLD", "EXITED", "INACTIVE", "REJECTED"}:
            continue
        if not holding and row.get("active", "").strip().lower() not in {
            "1", "true", "yes", "active"
        }:
            continue
        targets.append(
            {
                "code": code,
                "company": row.get("company", "").strip(),
                "current_status": status or ("OPEN" if holding else "WATCH"),
                "target_type": "holding" if holding else "watchlist",
            }
        )
    return targets


def next_trading_date(run_dir: Path, after_date: date) -> tuple[str | None, bool]:
    path = run_dir / "trading-calendar.json"
    if not path.is_file():
        return None, False
    calendar = _read_json(path)
    trading_dates: list[str] = []
    for row in calendar.get("rows", []):
        value = str(row.get("date", ""))
        try:
            candidate = date.fromisoformat(value)
        except ValueError:
            continue
        division = str(row.get("holiday_division", "0")).strip()
        if candidate > after_date and division not in {"", "0"}:
            trading_dates.append(candidate.isoformat())
    return (min(trading_dates), True) if trading_dates else (None, False)


def _task(
    task_id: str,
    task_type: str,
    priority: str,
    reason: str,
    due_at: str,
) -> dict[str, Any]:
    return {
        "task_id": task_id,
        "task_type": task_type,
        "priority": priority,
        "reason": reason,
        "due_at_jst": due_at,
        "status": "PENDING",
        "evidence_source_ids": [],
        "decision_log_path": None,
        "notes": "",
    }


def build_due_tasks(
    *,
    run_id: str,
    at: str,
    next_trade_date: str | None,
    policy: dict[str, Any],
    research_queue: dict[str, Any],
    pending_review_ids: list[str] | None = None,
    source_rows: list[dict[str, str]] | None = None,
    last_backup_at: str | None = None,
) -> list[dict[str, Any]]:
    current = _parse_jst(at)
    due_at = current.replace(hour=23, minute=59, second=59).isoformat(timespec="seconds")
    tasks = [
        _task(f"{run_id}-daily-event", "daily_event", "HIGH", "毎日の開示・即時撤退条件確認", due_at)
    ]
    backup_due = not last_backup_at
    if last_backup_at:
        try:
            backup_due = _parse_jst(last_backup_at) <= current - timedelta(days=31)
        except ValueError:
            backup_due = True
    if backup_due:
        tasks.append(
            _task(
                f"{run_id}-operations-backup",
                "operations_backup",
                "HIGH",
                "暗号化バックアップが未作成または31日超過",
                due_at,
            )
        )
    for review_id in pending_review_ids or []:
        if review_id:
            tasks.append(
                _task(
                    f"{run_id}-carried-{review_id}",
                    "carried_review",
                    "HIGH",
                    f"前回からの未完了レビュー: {review_id}",
                    due_at,
                )
            )
    if current.weekday() == 4:
        tasks.append(_task(f"{run_id}-weekly", "weekly", "NORMAL", "金曜の週次確認", due_at))
    if next_trade_date:
        following = date.fromisoformat(next_trade_date)
        if following.month != current.date().month:
            tasks.append(_task(f"{run_id}-monthly", "monthly", "NORMAL", "月末後の月次判定", due_at))
            if current.month in {3, 6, 9, 12}:
                tasks.append(
                    _task(f"{run_id}-quarterly-performance", "quarterly_performance", "NORMAL", "四半期運用成績確認", due_at)
                )
    for name, due in policy.get("next_rule_reviews", {}).items():
        if not due:
            continue
        try:
            due_date = date.fromisoformat(str(due)[:10])
        except ValueError:
            continue
        if due_date <= current.date():
            tasks.append(
                _task(f"{run_id}-rule-{name}", "rule_review", "NORMAL", f"ルールレビュー期限: {name}", due_at)
            )
    disclosure_words = ("決算短信", "四半期", "通期", "annual", "quarter")
    for queued in research_queue.get("tasks", []):
        if not isinstance(queued, dict):
            continue
        text = " ".join(str(queued.get(key, "")) for key in ("task_type", "reason"))
        if any(word.lower() in text.lower() for word in disclosure_words):
            task_id = str(queued.get("task_id", "")).strip()
            if task_id:
                tasks.append(
                    _task(f"{run_id}-disclosure-{task_id}", "disclosure_review", "HIGH", text.strip(), due_at)
                )
    for source in source_rows or []:
        title = source.get("title", "")
        normalized = title.lower()
        source_id = source.get("source_id", "").strip()
        code = source.get("code", "").strip() or "GLOBAL"
        if not source_id:
            continue
        if any(word in normalized for word in ("annual", "full-year", "通期", "本決算")):
            tasks.append(
                _task(
                    f"{run_id}-full-year-{code}-{source_id}",
                    "full_year_review",
                    "HIGH",
                    f"本決算後10営業日以内の全面レビュー: {title}",
                    due_at,
                )
            )
        elif any(word in normalized for word in ("quarter", "四半期")):
            tasks.append(
                _task(
                    f"{run_id}-quarterly-{code}-{source_id}",
                    "quarterly_review",
                    "HIGH",
                    f"四半期開示後5営業日以内の再採点: {title}",
                    due_at,
                )
            )
    unique: dict[str, dict[str, Any]] = {}
    for task in tasks:
        unique[task["task_id"]] = task
    return [unique[key] for key in sorted(unique)]


def create_nightly_artifacts(
    *, run_id: str, at: str, root: Path = PROJECT_ROOT
) -> dict[str, Any]:
    run_dir = root / "operations/private/runs" / run_id
    if not run_dir.is_dir():
        raise FileNotFoundError(f"run does not exist: {run_id}")
    next_date, confirmed = next_trading_date(run_dir, date.fromisoformat(run_id))
    plan_path = run_dir / "work-plan.json"
    if not plan_path.is_file():
        plan = _read_json(root / "operations/templates/work-plan-template.json")
        plan["run_id"] = run_id
        plan["generated_at_jst"] = _parse_jst(at).isoformat(timespec="seconds")
        plan["next_trading_date"] = next_date
        plan["trading_calendar_confirmed"] = confirmed
        _, source_rows = _read_csv(run_dir / "sources.csv")
        coverage = _read_json(run_dir / "coverage.json")
        state = _read_json(root / "operations/private/state.json")
        plan["tasks"] = build_due_tasks(
            run_id=run_id,
            at=at,
            next_trade_date=next_date,
            policy=_read_json(root / "operations/private/operation-policy.json"),
            research_queue=_read_json(run_dir / "research-queue.json"),
            pending_review_ids=[
                str(value) for value in coverage.get("universe", {}).get("due_reviews", {}).get("expected", [])
            ],
            source_rows=source_rows,
            last_backup_at=state.get("last_backup_at_jst"),
        )
        _atomic_json(plan_path, plan)
    else:
        plan = _read_json(plan_path)
        if plan.get("status") == "IN_PROGRESS":
            prior_tasks = {
                str(task.get("task_id")): task
                for task in plan.get("tasks", [])
                if isinstance(task, dict) and task.get("task_id")
            }
            _, source_rows = _read_csv(run_dir / "sources.csv")
            coverage = _read_json(run_dir / "coverage.json")
            state = _read_json(root / "operations/private/state.json")
            refreshed = build_due_tasks(
                run_id=run_id,
                at=at,
                next_trade_date=next_date,
                policy=_read_json(root / "operations/private/operation-policy.json"),
                research_queue=_read_json(run_dir / "research-queue.json"),
                pending_review_ids=[
                    str(value) for value in coverage.get("universe", {}).get("due_reviews", {}).get("expected", [])
                ],
                source_rows=source_rows,
                last_backup_at=state.get("last_backup_at_jst"),
            )
            plan["generated_at_jst"] = plan.get("generated_at_jst") or _parse_jst(at).isoformat(timespec="seconds")
            plan["next_trading_date"] = next_date
            plan["trading_calendar_confirmed"] = confirmed
            plan["tasks"] = [
                {**task, **prior_tasks.get(task["task_id"], {})} for task in refreshed
            ]
            _atomic_json(plan_path, plan)

    research_path = run_dir / "research-results.md"
    if not research_path.is_file():
        text = (root / "operations/templates/research-results-template.md").read_text(encoding="utf-8")
        research_path.write_text(text.replace("{{RUN_ID}}", run_id), encoding="utf-8")

    actions_path = run_dir / "next-day-actions.csv"
    if not actions_path.is_file():
        private = root / "operations/private"
        targets = _active_targets(private / "portfolio-register.csv", holding=True)
        targets += _active_targets(private / "watchlist.csv", holding=False)
        rows: list[dict[str, str]] = []
        seen: set[str] = set()
        for target in targets:
            code = target["code"]
            if code in seen:
                continue
            seen.add(code)
            rows.append(
                {
                    **dict.fromkeys(ACTION_FIELDS, ""),
                    "action_id": f"{run_id}-{code}-next",
                    "priority": "NORMAL",
                    "trade_date": next_date or "",
                    "code": code,
                    "company": target["company"],
                    "current_status": target["current_status"],
                    "next_action": "WAIT",
                    "trigger_type": "nightly_review",
                    "trigger_condition": "一次資料による判定待ち",
                    "human_action": "エージェントが根拠とルールIDを確定する",
                    "review_by_jst": _parse_jst(at).replace(hour=23, minute=59, second=59).isoformat(timespec="seconds"),
                    "notes": target["target_type"],
                }
            )
        if not rows:
            rows.append(
                {
                    **dict.fromkeys(ACTION_FIELDS, ""),
                    "action_id": f"{run_id}-GLOBAL-next",
                    "priority": "NORMAL",
                    "trade_date": next_date or "",
                    "code": "GLOBAL",
                    "company": "対象なし",
                    "current_status": "EMPTY",
                    "next_action": "NO-ACTION",
                    "rule_ids": "OPS-EMPTY-UNIVERSE",
                    "trigger_type": "nightly_review",
                    "trigger_condition": "保有・監視対象なし",
                    "human_action": "なし",
                    "review_by_jst": _parse_jst(at).replace(hour=23, minute=59, second=59).isoformat(timespec="seconds"),
                    "evidence_source_ids": "internal:portfolio-register;internal:watchlist",
                }
            )
        _write_csv(actions_path, rows)
    elif plan.get("status") == "IN_PROGRESS":
        fields, rows = _read_csv(actions_path)
        if fields == ACTION_FIELDS:
            changed = False
            for row in rows:
                if next_date and not row.get("trade_date", "").strip():
                    row["trade_date"] = next_date
                    changed = True
            if changed:
                _write_csv(actions_path, rows)
    return {
        "work_plan": plan_path.relative_to(root).as_posix(),
        "research_results": research_path.relative_to(root).as_posix(),
        "next_day_actions": actions_path.relative_to(root).as_posix(),
        "next_trading_date": plan.get("next_trading_date"),
        "trading_calendar_confirmed": plan.get("trading_calendar_confirmed") is True,
        "due_task_ids": [task.get("task_id") for task in plan.get("tasks", [])],
    }


def validate_nightly_artifacts(
    *,
    root: Path,
    run_id: str,
    handoff: dict[str, Any],
    coverage: dict[str, Any],
    orders: list[dict[str, str]],
) -> list[str]:
    run_dir = root / "operations/private/runs" / run_id
    errors: list[str] = []
    _, source_rows = _read_csv(run_dir / "sources.csv")
    valid_evidence_ids = {
        row.get("source_id", "").strip() for row in source_rows
        if row.get("source_id", "").strip()
    } | {"internal:portfolio-register", "internal:watchlist"}

    def evidence_ids(value: Any) -> list[str]:
        if isinstance(value, list):
            return [str(item).strip() for item in value if str(item).strip()]
        text = str(value or "").replace("|", ";").replace(",", ";")
        return [item.strip() for item in text.split(";") if item.strip()]

    def unknown_evidence(values: list[str]) -> list[str]:
        return sorted(
            value
            for value in set(values)
            if value not in valid_evidence_ids and not value.startswith("internal:")
        )

    plan = _read_json(run_dir / "work-plan.json")
    if plan.get("run_id") != run_id or not plan.get("generated_at_jst"):
        errors.append("work plan was not generated for this run")
    if plan.get("status") != "COMPLETED":
        errors.append("work plan status must be COMPLETED")
    pending_reviews = {
        str(value.get("review_id") if isinstance(value, dict) else value)
        for value in handoff.get("pending_reviews", [])
    }
    for task in plan.get("tasks", []):
        task_id = str(task.get("task_id", "")).strip()
        status = str(task.get("status", "")).upper()
        if status not in TERMINAL_TASK_STATUSES:
            errors.append(f"work-plan task is not terminal: {task_id or '<blank>'}")
        if status == "COMPLETED" and not task.get("evidence_source_ids"):
            errors.append(f"completed work-plan task lacks evidence: {task_id or '<blank>'}")
        if status == "COMPLETED":
            unknown = unknown_evidence(evidence_ids(task.get("evidence_source_ids")))
            if unknown:
                errors.append(
                    f"work-plan task {task_id or '<blank>'} cites unknown evidence: {', '.join(unknown)}"
                )
        if status == "DEFERRED" and task_id not in pending_reviews:
            errors.append(f"deferred work-plan task missing from handoff: {task_id or '<blank>'}")

    research = (run_dir / "research-results.md").read_text(encoding="utf-8")
    if "- 状態: `COMPLETED`" not in research:
        errors.append("research results status must be COMPLETED")
    for marker in ("{{RUN_ID}}", "未確定", "一次資料の確認結果を、事実・計算・判断に分けて記録する。"):
        if marker in research:
            errors.append(f"research results contains unresolved marker: {marker}")

    fields, actions = _read_csv(run_dir / "next-day-actions.csv")
    if fields != ACTION_FIELDS:
        return errors + ["next-day-actions.csv schema mismatch"]
    if not actions:
        return errors + ["next-day-actions.csv requires at least one action"]
    ids: list[str] = []
    action_by_ticket: dict[str, dict[str, str]] = {}
    target_codes = set(map(str, coverage.get("universe", {}).get("holdings", {}).get("expected", [])))
    target_codes |= set(map(str, coverage.get("universe", {}).get("watchlist", {}).get("expected", [])))
    action_codes: set[str] = set()
    order_by_ticket = {row.get("ticket_id", "").strip(): row for row in orders}
    for row in actions:
        action_id = row.get("action_id", "").strip()
        action = row.get("next_action", "").strip().upper()
        code = row.get("code", "").strip()
        ids.append(action_id)
        action_codes.add(code)
        if not action_id:
            errors.append("every next-day action requires action_id")
        if row.get("priority", "").strip().upper() not in VALID_PRIORITIES:
            errors.append(f"action {action_id or '<blank>'} has invalid priority")
        if action not in VALID_ACTIONS:
            errors.append(f"action {action_id or '<blank>'} has invalid action")
        if not row.get("rule_ids", "").strip():
            errors.append(f"action {action_id or '<blank>'} requires rule_ids")
        if not row.get("evidence_source_ids", "").strip():
            errors.append(f"action {action_id or '<blank>'} requires evidence_source_ids")
        unknown = unknown_evidence(evidence_ids(row.get("evidence_source_ids")))
        if unknown:
            errors.append(
                f"action {action_id or '<blank>'} cites unknown evidence: {', '.join(unknown)}"
            )
        if action in TRADE_ACTIONS:
            ticket_id = row.get("ticket_id", "").strip()
            if not plan.get("trading_calendar_confirmed") or not plan.get("next_trading_date"):
                errors.append(f"trade action {action_id} requires a confirmed trading date")
            if row.get("trade_date", "").strip() != str(plan.get("next_trading_date") or ""):
                errors.append(f"trade action {action_id} trade_date mismatch")
            if not ticket_id or ticket_id not in order_by_ticket:
                errors.append(f"trade action {action_id} requires a matching order ticket")
            else:
                order = order_by_ticket[ticket_id]
                if order.get("code", "").strip() != code or order.get("action", "").strip().upper() != action:
                    errors.append(f"trade action {action_id} does not match order {ticket_id}")
                action_by_ticket[ticket_id] = row
    duplicates = sorted({value for value in ids if value and ids.count(value) > 1})
    if duplicates:
        errors.append(f"duplicate action_id: {', '.join(duplicates)}")
    missing_targets = sorted(target_codes - action_codes)
    if missing_targets:
        errors.append(f"next-day actions missing target codes: {', '.join(missing_targets)}")
    if not target_codes and "GLOBAL" not in action_codes:
        errors.append("empty universe requires a GLOBAL action")
    for order in orders:
        ticket_id = order.get("ticket_id", "").strip()
        if ticket_id not in action_by_ticket:
            errors.append(f"order ticket has no matching trade action: {ticket_id or '<blank>'}")
    return errors
