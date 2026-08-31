# Daily Run

Use this workflow when the request is “today's run,” a scheduled operation, or a resume of an incomplete run.

## Start or resume once at night

1. Read `docs/nightly-operation-v0.1.md`, `docs/daily-automation-runbook-v0.1.md`, `operations/private/operation-policy.json`, and the canonical rule files named in `SKILL.md`.
2. Run `scripts/nightly_operation.py start --at <current aware JST timestamp> --cutoff <declared JST cutoff>` and retain the returned `run_token`. This validates state, prepares or resumes the run, scans official APIs, confirms the next trading date, and creates due work. If it returns `locked`, do not start a second run.
3. Read `provider-health.json`, `research-queue.json`, `work-plan.json`, `coverage.json`, `operations/private/state.json`, the previous run's `handoff.json` when present, `portfolio-register.csv`, `watchlist.csv`, and the trade, recovered-capital, cash, corporate-action, rebuy-restriction, and industry-exposure ledgers.
4. If the returned run status is already `completed`, report that the date was already closed. Do not create duplicate orders.
5. If it is `in_progress`, resume the existing files rather than replacing them.

## Cover the required universe

Process every task in both `research-queue.json` and `work-plan.json`. Check company IR and JPX notices in their primary sites even when the machine APIs succeed. Mark a task `COMPLETED` only with `evidence_source_ids`; defer it only by copying the same task ID into `handoff.pending_reviews`.

When an `operations_backup` task is due, run `scripts/operation_backup.py create --at <current aware JST timestamp>`. It uses `OPERATION_BACKUP_AGE_RECIPIENT` when configured. Never add `--allow-plaintext` unless the user explicitly authorized a plaintext PAPER backup. Record the resulting private archive path as `internal:backup:<path>` evidence; if encryption is unavailable, defer the task and report the setup action.

Check all current holdings and pending orders. Check active watchlist names for new official disclosures, but do not fully rescore every watchlist name every day. Use the prior successful disclosure cutoff as the lower bound and the declared current cutoff as the upper bound.

Include other modes only when due:

- Daily event checks on every run
- Weekly checks on Friday or when a missed weekly review is queued
- Monthly checks after the final trading session's values are available or when queued
- Quarterly checks when a new quarterly disclosure starts the five-trading-day review window
- Full-year checks when a new full-year disclosure starts the ten-trading-day review window

Record due work that cannot be completed in `handoff.json` under `pending_reviews`. Do not silently drop it.

## Write durable artifacts

Update the current run's:

- `work-plan.json` with every due review in `COMPLETED` or explicit `DEFERRED`
- `research-results.md` with a completed, human-readable summary of all due research
- `next-day-actions.csv` with exactly one auditable next-session action for every holding and watchlist code
- `report.md` with coverage, exceptions, decisions, due reviews, data gaps, human actions, and the next run
- `sources.csv` with publication time, retrieval time, URL, and whether it is a primary source
- `coverage.json` by moving every actually checked expected item to `checked` and marking evidenced official sources `CHECKED`
- `orders.csv` only through `scripts/order_ticket.py propose` for actionable next-session proposals; use the policy-derived status (`PAPER_PROPOSED` in `PAPER`, `PROPOSED` only in an approved `LIVE` mode)
- `pretrade-check.md` with the target trade date when an order is proposed
- individual decision logs only for an actionable decision or a due periodic review
- `handoff.json` with `pending_reviews`, `pending_orders`, `data_gaps`, and `next_run_at_jst`

Reconcile fill or cancellation data already entered by the user before preparing another order. Never infer an execution from a proposed order.

Append each proposal, paper fill, human-reported fill, cancellation, and expiration to `trade-event-ledger.csv`. Update position, capital, recovered-capital, rebuy-restriction, and industry ledgers only from a recorded event. Do not create another ticket for a code while its prior ticket remains unreconciled.

## Close safely

Call `scripts/nightly_operation.py finalize` with the same `run_token` only after all holdings, pending orders, required disclosure sources, due tasks, research results, and next-day actions have been checked through the declared cutoff. A non-critical data gap may remain only as a structured item with its impact and retry time.

Call `fail` with the same `run_token` if a required source or a material part of the universe could not be checked. A failed run must not advance the successful disclosure cutoff. Leave partial files intact for audit and retry.

Never submit a brokerage order. Require the user to complete the 8:45–8:55 pre-trade checklist and manually approve each `LIVE` proposed order. After a successful finalize, stop active work and wait for the next scheduled night; `PAPER` requires no morning action.

## Return

Lead with the run status: `COMPLETED`, `FAILED`, or `ALREADY-COMPLETED`. Then give:

1. cutoff and coverage;
2. urgent decisions;
3. next-session proposed orders;
4. human actions;
5. data gaps and queued reviews;
6. saved research, next-day actions, orders, report, and handoff paths;
7. the next scheduled nightly run time.
