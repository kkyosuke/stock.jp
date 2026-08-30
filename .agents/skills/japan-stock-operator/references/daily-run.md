# Daily Run

Use this workflow when the request is “today's run,” a scheduled operation, or a resume of an incomplete run.

## Prepare or resume

1. Read `docs/daily-automation-runbook-v0.1.md`, `operations/private/operation-policy.json`, and the canonical rule files named in `SKILL.md`.
2. Run `scripts/daily_operation.py prepare --at <current aware JST timestamp>`.
3. Read the returned run paths, `operations/private/state.json`, the previous run's `handoff.json` when present, `portfolio-register.csv`, and `watchlist.csv`.
4. If the returned run status is already `completed`, report that the date was already closed. Do not create duplicate orders.
5. If it is `in_progress`, resume the existing files rather than replacing them.

## Cover the required universe

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

- `report.md` with coverage, exceptions, decisions, due reviews, data gaps, human actions, and the next run
- `sources.csv` with publication time, retrieval time, URL, and whether it is a primary source
- `orders.csv` only for actionable next-session proposals; use the policy-derived status (`PAPER_PROPOSED` in `PAPER`, `PROPOSED` only in an approved `LIVE` mode)
- `pretrade-check.md` with the target trade date when an order is proposed
- individual decision logs only for an actionable decision or a due periodic review
- `handoff.json` with `pending_reviews`, `pending_orders`, `data_gaps`, and `next_run_at_jst`

Reconcile fill or cancellation data already entered by the user before preparing another order. Never infer an execution from a proposed order.

## Close safely

Call `complete` only after all holdings, pending orders, and required disclosure sources have been checked through the declared cutoff. A non-critical data gap may remain only if it cannot affect an immediate action; explain it and queue a follow-up.

Call `fail` if a required source or a material part of the universe could not be checked. A failed run must not advance the successful disclosure cutoff. Leave partial files intact for audit and retry.

Never submit a brokerage order. Require the user to complete the 8:45–8:55 pre-trade checklist and manually approve each proposed order.

## Return

Lead with the run status: `COMPLETED`, `FAILED`, or `ALREADY-COMPLETED`. Then give:

1. cutoff and coverage;
2. urgent decisions;
3. next-session proposed orders;
4. human actions;
5. data gaps and queued reviews;
6. saved report, orders, and handoff paths.
