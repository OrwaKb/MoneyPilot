# Recurring / Subscription Radar — design

**Date:** 2026-06-18
**Status:** approved (scope: Full v1)
**Sub-project 1 of the "proactive financial intelligence" chain** (radar → payday-runway → anomaly nudges). This spec covers ONLY the radar + its surfacing; runway and anomaly are separate later specs that depend on this.

## Goal

Detect recurring charges — subscriptions (Netflix, Spotify), standing orders / *horaot keva* (rent, gym, insurance), regular bills — from the existing transaction history, with a next-expected date and a monthly-equivalent cost, and surface them so the user sees what is quietly draining their money each month.

This turns MoneyPilot from a passive tracker into something that *notices*. It is the strongest differentiator vs Mint/YNAB for an Israeli salaried user.

## Principles (inherited from the codebase)

- **Stateless re-derivation.** Like `budget.safe_to_spend` / `goals`, the detector computes everything from `transactions` on every call. No schema change, no migration, no cron. The ONE exception is a user dismiss-list, stored in the existing settings KV (not a new table).
- **FACTS-only AI.** The advisor/briefing receive pre-computed numbers in `fact_pack`; they never invent figures. The radar feeds `fact_pack`; prompts only gain permission to *mention* what's already there.
- **Start strict.** False positives destroy trust in a money tool. Better to miss a real subscription than to flag weekly groceries as one.

## Module: `app/engine/recurring.py`

A pure module mirroring `budget`/`goals`/`cycles`. Public entry point:

```
detect(conn, today: dt.date, *, lookback_days=400, min_occurrences=3) -> list[dict]
```

Returns a list of detected recurring charges, highest-confidence first, EXCLUDING dismissed keys.

### Algorithm

1. **Candidate rows.** Non-deleted `direction='expense'` transactions with `effective_date >= today - lookback_days`. All payment methods (a *horaa keva* is often `transfer`, a subscription `card`). Lookback 400d so an annual charge has a chance and ~13 monthly cycles fit.
2. **Grouping key.** `key = _norm(merchant or description)` where `_norm` lowercases, collapses whitespace, and strips a trailing `" with <people>"` clause (matches how `db.match_rule` keys off description). Rows whose key is empty are skipped. Rationale: real ledgers often have `merchant=NULL` (the fallback parser doesn't always extract one), so description is the necessary fallback.
3. **Per-group analysis** (only groups with `>= min_occurrences` rows):
   - Sort occurrences by `effective_date`. Compute consecutive gaps (days).
   - `median_gap = median(gaps)`.
   - **Cadence classification** by `median_gap`:
     - `monthly` if `26 <= median_gap <= 35`
     - `annual` if `350 <= median_gap <= 380`
     - else → not recurring (drop the group). *Weekly is intentionally unsupported in v1.*
   - **Interval regularity gate:** drop if gaps are too irregular — require `max(|gap - median_gap|) <= regularity_tol` where `regularity_tol = 7` days for monthly, `45` for annual. (A clean subscription has near-constant spacing.)
   - **Amount stability:** `typical = median(amounts_agorot)`. Require every amount within `±15%` of `typical`, OR allow one outlier that is the most-recent charge (a price hike — see flags). Otherwise drop (variable spend ≠ subscription).
   - **Confidence** `0..1`: blend of occurrence count (more = higher, saturating ~6), interval regularity (lower variance = higher), and amount stability. Surface only if `confidence >= 0.6`.
4. **Per-detection output dict:**
   ```
   {
     "key": str,                      # normalized grouping key (for dismiss)
     "name": str,                     # display name: title-cased merchant/description of the most recent row
     "cadence": "monthly"|"annual",
     "typical_agorot": int,           # median charge
     "last_charged": "YYYY-MM-DD",
     "next_expected": "YYYY-MM-DD",   # last_charged + median_gap, clamped to a valid date
     "monthly_equiv_agorot": int,     # typical if monthly; round(typical/12) if annual
     "occurrences": int,
     "confidence": float,
     "price_hike": bool,              # most recent amount > typical * 1.25
   }
   ```
5. **Dismiss-list.** `_dismissed(conn) -> set[str]` reads setting `recurring_dismissed` (JSON list; default `[]`). `detect` filters these keys out. Helpers `dismiss(conn, key)` / `undismiss(conn, key)` mutate the list (dedup, store JSON).

### Summary helper

```
summary(conn, today, *, soon_days=7) -> dict
```

Returns `{ "items": [<ALL detect() dicts, confidence-sorted>], "monthly_total_agorot": int, "upcoming": [<items whose next_expected within soon_days>] }`. Runs `detect` once per refresh so callers don't repeat the work.

**Consistency rule:** `monthly_total_agorot` sums `monthly_equiv_agorot` over **ALL** detected (post-dismiss) items — never a truncated subset, or the "monthly burn" would under-count. `items` carries the full list; **display caps are applied by the consumer, not here** (`fact_pack` takes `items[:5]` for prompt brevity; the API/UI show the full list).

## API: `app/api.py`

- `get_recurring()` → `{ "items": [...with _fmt fields...], "monthly_total_agorot", "monthly_total_fmt", "upcoming": [...] }`. Adds `typical_fmt` / `monthly_equiv_fmt` per item.
- `dismiss_recurring(key: str)` → `{}` (validates key is a non-empty str; under `self._lock`).
- Both registered in `web/server.ALLOWED` (read + dismiss are safe for the web build).

## `fact_pack` (insights.py)

Add a compact `recurring` block so the AI sees it without bloating FACTS:
```
"recurring": {
  "monthly_total_agorot": int, "monthly_total_fmt": str,
  "count": int,
  "items": [ {"name","cadence","typical_fmt","next_expected","monthly_equiv_fmt"} ... top 5 ],
  "upcoming": [ {"name","typical_fmt","next_expected"} ... due within 7 days ]
}
```
Pre-formatted, capped — keep it small.

## Prompts (prompts.py)

- **BRIEFING_SYSTEM:** one added sentence — if `recurring.upcoming` is non-empty, the briefing may lead with the imminent charge ("Spotify ~₪20 hits in 3 days"); otherwise it may note the monthly recurring burn. Numbers ONLY from FACTS.
- **CHAT_SYSTEM:** one added sentence — the advisor can answer "what subscriptions / recurring charges do I have?" and "what's my monthly recurring burn?" from `recurring`, citing only FACTS figures.
- Wording kept minimal so the existing JSON-contract tests are unaffected.

## UI: Overview "Recurring" card (`app/ui/index.html`, `app.js`, `app.css`)

- A new card in the Overview grid (alongside card-accrual / goals). Header "RECURRING" + total monthly burn (e.g. "₪430/mo").
- Rows: `emoji?/name · ₪amount · cadence · next: <date>`; a price-hike row gets a subtle "↑" marker. Each row has a small "×" → `dismiss_recurring(key)` → re-render.
- Empty state: "No recurring charges detected yet — they'll appear as your history builds." (honest, given sparse early data).
- `get_overview` is NOT changed to carry recurring (keeps that payload lean); the card calls `get_recurring()` on Overview render, like other lazy panels. Dismiss re-fetches.
- All dynamic strings through `esc()`; the dismiss key passed back verbatim to the API (validated server-side).

## Testing (TDD, fixed-date synthetic ledgers — repo style with `today_fn`/explicit dates)

**Engine (`tests/test_recurring.py`):**
- 3 monthly same-merchant charges (~30d apart, stable amount) → detected, cadence monthly, correct `next_expected`, `monthly_equiv == typical`.
- 2 occurrences → NOT detected (below `min_occurrences`).
- Irregular gaps (10d, 40d, 5d) → NOT detected (regularity gate).
- Same merchant, wildly varying amounts (grocery store) → NOT detected (amount stability).
- ±10% amount variance → still detected.
- Most-recent charge +30% → detected with `price_hike=True`.
- 3 annual charges (~365d) → detected, cadence annual, `monthly_equiv == round(typical/12)`.
- `merchant=NULL`, recurring by description → detected (description fallback).
- Dismissed key → excluded; `undismiss` restores it.
- `summary`: monthly_total = sum of monthly-equivalents; `upcoming` contains an item whose next_expected is within 7 days and excludes a far one.

**API (`tests/test_api.py`):** `get_recurring` shape + `_fmt` fields; `dismiss_recurring` round-trips (a detected item disappears after dismiss).

**fact_pack (`tests/test_insights.py`):** `recurring` key present with expected sub-shape.

## Edge cases / risks

- **Sparse data (now):** with ~10 days of history nothing will detect — by design. Empty states everywhere; no errors. Value accrues over months.
- **Salary / income:** only `expense` rows are considered, so recurring salary income isn't mislabeled a "subscription."
- **Goal contributions:** excluded (not expenses in the recurring sense).
- **Clamping next_expected:** `last_charged + median_gap` may land on an invalid civil date for annual leap cases — clamp via `cycles.clamped_date`-style logic.
- **Performance:** one grouped pass over ≤400 days of expenses; trivial at this scale. `summary` memoizes the `detect` call per refresh.
- **Dismiss key drift:** the normalized key is stable as long as merchant/description is stable; acceptable. A renamed merchant simply re-appears (user can re-dismiss).

## Non-goals (v1)

- Payday-runway forecast (next sub-project; will consume `next_expected` dates).
- Anomaly / unusual-spend nudges (separate).
- Editing a detected subscription's amount or confirming it as "real" (only detect + dismiss).
- Notifications / OS alerts.
- Weekly cadence.
