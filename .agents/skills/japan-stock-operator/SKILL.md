---
name: japan-stock-operator
description: Review Japanese stocks under this repository's frozen tenbagger rules and produce auditable BUY, WATCH, WAIT, KEEP, ADD, REDUCE, SELL, or NO-ACTION decisions. Use for live purchase timing, pre-order checks, daily disclosure monitoring, weekly or monthly reviews, quarterly progress reviews, market-regime sizing, exit checks, and creation or updating of operation decision logs for Japanese equities.
---

# Japan Stock Operator

Apply the repository's frozen rules to current public information. Produce a reviewable decision record; never submit a brokerage order.

## Load the canonical rules

From the repository root, read these files before judging a stock:

1. `docs/live-operation-playbook-v0.1.md`
2. `docs/tenbagger-rule-v0.3.md`
3. Only the relevant inherited sections of `docs/tenbagger-rule-v0.2.md`

Read [references/review-checklists.md](references/review-checklists.md) for the checklist matching the requested review mode. Treat the canonical documents as authoritative if this skill conflicts with them.

## Choose one review mode

- `new-entry`: full gate, score, reverse-tenbagger, liquidity, regime, and order-plan review
- `daily-event`: check new official disclosures and immediate `S-A` triggers only
- `weekly`: check missed disclosures, liquidity, concentration, upcoming reviews, and a provisional regime snapshot; normally return `NO-ACTION`
- `monthly`: run candidate selection, v0.3 price/time rules, concentration, formal regime score, and next-day order planning
- `quarterly`: fully rescore company progress and decide `KEEP`, `ADD`, `REDUCE`, or `SELL`
- `ad-hoc`: evaluate a specified public event without inventing a general review

If the user does not identify the mode, infer it from the request and state the chosen mode.

## Establish the information cutoff

1. Record the evaluation time in JST.
2. Search current official sources. Prefer TDnet, EDINET, JPX, the company's IR site, Cabinet Office, and Bank of Japan.
3. Record each source's publication time, retrieval time, and URL.
4. Use only information public by the declared cutoff.
5. Label press reports and social posts as discovery sources; confirm their claims in primary sources.
6. Return `WAIT` when a hard-gate input, current filing, share-count input, or required market-regime input is missing.

Never infer an unpublished KPI, silently carry forward a stale score, or treat search snippets as evidence.

## Separate fact, calculation, and judgment

- `FACT`: reproduce the disclosed meaning with units, period, and source.
- `CALCULATION`: show the formula, denominator, dilution basis, currency, and rounding.
- `JUDGMENT`: cite the exact rule ID and explain why the facts and calculations satisfy it.

Do not use narrative confidence as a substitute for a missing rule input.

## Apply decisions in order

1. Check all immediate `S-A` exit rules.
2. Check quarterly `S-B` reduction and exit rules when applicable.
3. Check v0.3 price/time full exits.
4. Check v0.3 5x and 10x partial profit-taking.
5. Check concentration reduction.
6. For new or additional purchases, calculate `MRS-v0.1` and apply its entry multiplier.
7. Apply liquidity, lot-size, position, industry, candidate-pool, and maximum-holdings caps.

Market regime may reduce or pause a new or additional purchase. It must not trigger a sale by itself or override a company-specific exit.

## Calculate the market regime

Use point-in-time month-end inputs and run:

```bash
.venv/bin/python scripts/market_regime.py <frozen inputs>
```

Use `NORMAL`, `CAUTION`, or `STRESS` exactly as returned. If any required input is unavailable, record `UNAVAILABLE` and do not create a new or additional position. Preserve the unscaled counterfactual amount in the log.

## Create the decision log

Generate a private draft when a concrete company is under review:

```bash
.venv/bin/python scripts/new_operation_log.py \
  --date YYYY-MM-DD --code CODE --company NAME --mode MODE
```

Fill every applicable field. Store exact quantities, wealth, tax, and account details only under ignored `operations/private/`. Never expose account numbers, credentials, personal identifiers, or total personal assets in chat or tracked files.

## Return a fixed summary

Lead with one action: `BUY`, `WATCH`, `WAIT`, `KEEP`, `ADD`, `REDUCE`, `SELL`, or `NO-ACTION`.

Then report:

1. Evaluation time and mode
2. Rule IDs that determined the action
3. Position-size ceiling as a percentage, not a personal currency amount
4. `MRS-v0.1` score, state, and component results
5. Three strongest supporting facts
6. Three strongest risks or missing inputs
7. Exact order plan or reason no order is allowed
8. Next review trigger and date
9. Primary-source links
10. Private decision-log path, if created

Do not write “buy” or “sell” without rule IDs and evidence. Do not imply certainty or guaranteed returns. Require human confirmation before any order entry.
