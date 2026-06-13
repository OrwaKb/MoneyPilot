# MoneyPilot — Rolling Daily Allowance (safe-to-spend rework)

**Date:** 2026-06-14
**Status:** approved (design), implementing
**Touches:** `app/engine/budget.py` (core), `app/engine/insights.py`, `app/ai/prompts.py`,
`app/ui/app.js`, `app/ui/widget.{html,js,css}`, tests.

## Problem

The current `budget.safe_to_spend` computes `today = remaining // days_left`. Because it
divides by *days remaining*, every purchase silently re-spreads the rest of the money over
the remaining days — spending never visibly "costs" a day; the number just drifts. The user
wants a **rolling daily allowance (envelope)**: a fixed amount accrues each day, unspent days
bank forward, an overspent day shows negative and is dug out of the next day.

User's worked example (allowance ₪70/day):
- Day 1 spend ₪0 → shows ₪70; next day spend ₪0 → ₪140 (banked).
- Day 1 spend ₪100 → shows −₪30; next day → ₪70 − ₪30 = **₪40**.

Decision (confirmed): **fixed bills (`is_fixed` categories) are "pocket money"** — they do NOT
count against the daily allowance. When a bill posts it quietly lowers the allowance for the
rest of the cycle (no scary one-day crash). No bill-budgeting required. The hero **may show
negative**.

## Model (stateless re-derivation — no schema, no migration, no cron)

All quantities derived from dates + ledger on each call, so the allowance "catches up"
automatically if the app isn't opened for days. The allowance is **stable against spending by
construction** (the `disc_spent` term cancels), so rollover is clean.

```
cycle        = salary_cycle(today, salary_day)          # existing
available    = available_balance(conn)                  # existing (all txns, active-goal-aware)
opening_date = settings.opening_balance_date
spend_start  = max(cycle.start, opening_date)           # avoids double-counting onboarding spend
                                                        #   (== existing income_start)
income_so_far = income in [spend_start, today]
expected_salary = max(0, salary_setting − income_so_far)   # existing salary anticipation
goal_reserve = goals.cycle_savings_reserve(conn, today)    # existing

disc_spent   = Σ discretionary expenses in [spend_start, today]   # excludes is_fixed + income
remaining    = available + expected_salary − goal_reserve         # money left this cycle (unchanged meaning)
pool         = remaining + disc_spent                             # cycle spending pool (stable vs today's spend)

total_days   = (cycle.end − spend_start).days + 1        # = cycle length, except the first partial cycle
day_index    = (today − spend_start).days + 1            # 1-based, from spend_start
daily_allowance = max(0, pool) // total_days             # the ₪/day (floored at 0)
today        = daily_allowance × day_index − disc_spent + min(0, pool)   # ROLLING hero (may be negative)
```

> **Amendment (post-audit, 2026-06-14):** two corrections from the adversarial
> review, both with regression tests:
> 1. **Overdrawn cycles** (`pool < 0`, e.g. a bill you can't cover): the floored
>    `daily_allowance` alone made `today` read a misleading `₪0` (or under-report
>    the shortfall). The `+ min(0, pool)` term adds the negative pool back so the
>    hero reads honestly negative and the `today ≤ remaining` invariant holds
>    always. `daily_allowance` still floors at 0 (no "+₪-46/day").
> 2. **`available` is taken `as_of=today`** (`available_balance(conn, as_of=today)`):
>    a future-dated entry no longer moves the allowance, so the
>    "stable-against-spending" property holds for planned spends too. The balance
>    panel still calls `available_balance` uncapped.

Why fixed bills behave as "pocket money": they are excluded from `disc_spent` (so they never
spike the hero) but stay subtracted inside `available` (so they lower `pool` → lower
`daily_allowance` for the remaining days). A *discretionary* spend of X on the current day
lowers `today` by exactly X (daily stable, `disc_spent += X`).

### Engine return shape (`safe_to_spend`)
Keep: `available_agorot`, `expected_salary_agorot`, `goal_reserve_agorot`, `remaining_agorot`,
`days_left`, `cycle`. **Redefine** `today_agorot` = rolling balance. **Add**
`daily_allowance_agorot`, `cycle_spent_agorot` (= `disc_spent`).

Invariant: `today ≤ remaining` always (you can never be told today's safe-to-spend exceeds the
money left for the whole cycle); on the last cycle day `today ≈ remaining` (minus floor remainder).

## Plumbing

- **`insights.fact_pack`**: add `daily_allowance_agorot/_fmt`, `cycle_spent_agorot/_fmt`; keep
  `today/remaining/available/goal_reserve` `_fmt`s; `today_fmt` now formats the rolling number
  (handles negative via existing `fmt_ils`).
- **`prompts.py`**: BRIEFING_SYSTEM + CHAT_SYSTEM gain one sentence explaining `today_agorot`
  is a rolling allowance (banks/over) and `daily_allowance_agorot` is the per-day accrual.
- **Cockpit `app.js`**: hero = `today_fmt` (already). Sub-line →
  `+₪70/day · ₪1,800 left this cycle · 22 days to salary` (+ existing goal-reserve clause).
- **Widget**: hero unchanged (`today_fmt`); add a `+₪70/day` line under the hero
  (`#w-allowance`), styled in `widget.css`.

## Tests (TDD)

Rewrite the 4 old-formula cases in `test_budget.py`
(`projects_unreceived_salary`, `matches_user_worked_example`, `clamps_to_zero_when_overdrawn`
→ now negative, `subtracts_goal_savings` today-assertion). Keep all goal-reserve / available /
card / cycle_net / pace tests (unaffected). Add envelope tests:
1. unspent days bank forward (today grows by `daily` per elapsed day, spend 0);
2. user's overspend example: spend > daily → negative, next day recovers by `daily`;
3. `daily_allowance` stable across same-day discretionary spends (only `today` drops);
4. discretionary spend lowers `today` by exactly the spent amount (same day);
5. a fixed (`is_fixed`) bill does NOT drop `today` by its full amount (excluded from `disc_spent`),
   but does lower `daily_allowance`;
6. opening-date guard: mid-cycle `opening_date` → accrual + `disc_spent` start at `opening_date`,
   no double-count of onboarding spend.

Update `test_insights` to assert the new fields. `test_api`/`test_widget` only check field
presence — no change expected.

## Out of scope (noted)
- "Pre-reserve budgeted fixed bills" (option C) — one-line future upgrade (subtract budgeted-
  unpaid fixed from `pool`); deferred per user choice of "pocket money".
- No persisted snapshot / midnight job — intentionally stateless.
