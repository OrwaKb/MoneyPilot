# MoneyPilot — Design Spec

**Date:** 2026-06-11
**Status:** Approved by user (brainstorming session, sections 1–3)
**Platform:** Windows 11, local desktop app

## 1. What this is

A local, single-user personal-finance cockpit. The user logs spending in natural language
("45 falafel with karim"), the app categorizes it automatically via Claude, tracks daily and
monthly budgets against the user's real salary/credit-card cycle, tracks savings goals, and
acts as a financial advisor (proactive daily briefing + on-demand chat over the user's data).

One click on a desktop shortcut opens the app; nothing runs in the background when closed.

### Decisions locked during brainstorming

| Topic | Decision |
|---|---|
| AI brain | Claude via the user's existing Claude Code subscription (Claude Agent SDK for Python); no API key, no per-use cost |
| Currency | ₪ (ILS) primary; occasional USD/EUR converted to ₪, original amount preserved |
| Money flow | One salary on a fixed day + one credit card with a fixed monthly charge day (set during onboarding) |
| Launch model | Desktop shortcut → full app window; no tray daemon, no hotkey service |
| Advisor | Both: proactive briefing on open + on-demand chat tab |
| Goals | "Save ₪X by date" + named purchase funds with progress |
| History | Fresh start; onboarding accepts a free-text brain-dump of current balances and month-to-date spending |
| Visual style | Dark "Mission Control" cockpit (monospace numbers, telemetry feel) |
| Layout | Tabbed cockpit: entry bar always on top; tabs Overview / Ledger / Goals / Advisor |
| Stack | Python 3.11 + pywebview (WebView2); UI in HTML/CSS/JS; SQLite storage |

### Non-goals (v1)

- No bank/credit-card statement import, no scraping, no Open Banking.
- No multi-user, no sync between machines (OneDrive backup only).
- No mobile app. No tray quick-capture.
- No mains-grade financial advice compliance — this is a personal tool.

## 2. Architecture

```
Desktop .lnk → pythonw -m app
  └─ pywebview window (WebView2)
       UI layer  : ui/index.html + app.css + app.js  (cockpit, 4 tabs + entry bar)
       Bridge    : pywebview js_api  (api.py — all commands cross here)
       Core      : engine/  (cycles, budget, goals, insights — pure Python, deterministic)
       Storage   : SQLite (WAL) at %LOCALAPPDATA%\MoneyPilot\ledger.db
       AI        : ai/  (Claude Agent SDK; parser, advisor, prompts; offline fallback + retry queue)
```

**Core principle:** every number shown (safe-to-spend, pace, projections, card-charge total)
is computed by deterministic Python. Claude only (a) converts user text into structured
entries and (b) writes briefing/chat prose. AI downtime degrades wording, never math, and
never loses an entry.

### Directory layout

```
Project - Finance Tracker/
  app/
    __main__.py        # entry point: single-instance guard, create window
    api.py             # JS↔Python bridge: one method per UI command
    db.py              # SQLite open/migrate/query helpers
    models.py          # dataclasses + pydantic schemas
    engine/
      cycles.py        # salary-cycle & card-statement calendar math
      budget.py        # budgets, safe-to-spend, pace
      goals.py         # goal pace, projection, safe-to-buy verdict
      insights.py      # fact-pack builder, trends, top categories
    ai/
      client.py        # Agent SDK wrapper: call, timeout, schema-validate, repair-retry
      parser.py        # NL → transactions (AI path + regex fallback path)
      advisor.py       # briefing generation, chat turns, action proposals
      prompts.py       # all prompt templates in one place
    ui/
      index.html  app.css  app.js  assets/
  scripts/
    setup.ps1          # venv, pip install, create Desktop shortcut with icon
    dev_seed.py        # fake-data dev mode seeding
  tests/               # pytest; Claude fully mocked
  docs/superpowers/specs/
  backups/             # nightly JSON exports (synced by OneDrive), keep last 30
```

### Dependencies (runtime)

`pywebview`, `claude-agent-sdk`, `pydantic`, `requests` (FX rates only). Dev: `pytest`.

## 3. Data model (SQLite, amounts in integer agorot)

- **transactions**: id, created_at, effective_date, amount_agorot (signed; expense negative),
  direction (expense|income|goal_contribution), currency_orig, amount_orig, fx_rate,
  category_id, description, merchant, people, payment_method (card|cash|transfer),
  goal_id (nullable), raw_text, source (ai|fallback|manual|onboarding), ai_confidence (0–1),
  needs_review (bool), deleted_at (soft delete).
- **categories**: id, name, emoji, color, is_income, sort. Seeded: Food out, Groceries,
  Transport/Fuel, Bills, Fun, Health, Education, Shopping, Gifts, Other; income: Salary,
  Other income.
- **category_rules**: id, pattern (merchant/keyword), category_id, created_from_txn —
  written when the user re-categorizes; exact-match fast path + injected into parse prompts.
- **budgets**: category_id, amount_agorot, cycle_anchor (per salary cycle).
- **goals**: id, name, emoji, type (save_by_date|purchase_fund), target_agorot,
  target_date (nullable for funds), status, created_at.
- **settings** (key/value): user_name, salary_day, salary_amount_agorot, card_charge_day,
  fixed_bills_total_agorot, fx_rates_json + fetched_at, schema_version.
- **chat_history**: id, ts, role, text (advisor tab continuity; cleared per user request).
- **briefings**: date, text, fact_pack_json (cache: one briefing per local date).

## 4. Cycle engine (`cycles.py`)

- **Budget month = salary cycle**: from salary day S to the day before the next S.
  Day clamping: if S=31 and month has 30 days → use month end. Same rule for card day C.
- **Card statement**: purchases with payment_method=card accumulate into the statement
  that charges on the next C. A purchase **on** day C belongs to the *next* statement.
  Overview shows: accrued statement total + days until charge.
- All math on local dates (`datetime.date`); no timezone handling needed.
- Explicit pytest cases: Feb/leap years, S or C = 29/30/31, purchase on boundary days,
  cycle straddling year end.

## 5. Entry pipeline

1. User types one or more lines into the entry bar (any tab) and hits Enter.
2. UI shows a pulsing "parsing…" chip; call is async (background thread), UI never blocks.
3. `parser.py` builds the prompt: raw text + today's date + category list + house rules
   (category_rules) + last 20 merchants. Claude must return **strict JSON**:
   `[{effective_date, amount, currency, direction, category, description, merchant?,
   people?, payment_method, goal_name?, confidence}]` — validated with pydantic.
4. Invalid JSON → one repair retry (error fed back) → still invalid → fallback path.
5. **Fallback path** (offline/timeout/refusal/invalid): regex extracts amount(s); keywords +
   category_rules guess the category (else "Other"); entry saved with needs_review=1.
   A background sweep re-parses queued entries on next successful AI call.
6. Result chip: "✓ Food · ₪45 · falafel with karim" with **Undo** and **Edit**;
   confidence < 0.7 → amber chip + needs_review=1.
7. Defaults: payment_method=card (configurable); relative dates resolved ("yesterday",
   weekday names → most recent past); multiple transactions per line supported;
   `salary landed` → income of settings amount; `put 500 in drone fund` → goal_contribution.
8. Foreign amounts converted with the stored rate table (weekly refresh from
   frankfurter.app when online; manual override in settings); original amount + rate stored.

## 6. AI integration (`ai/client.py`)

- **Transport:** Claude Agent SDK for Python (`claude-agent-sdk`), which uses the Claude
  Code installation/login already on this machine — the user's subscription, no API key.
  No fixed model pinned: the SDK uses the subscription's default model.
- One-shot `query()` per parse / per briefing; advisor chat replays recent chat_history
  into each turn's prompt together with a fresh fact-pack.
- Timeout 60 s per call; errors never propagate to UI as crashes.
- **Privacy:** prompts contain only the typed text and compact computed fact-packs
  (cycle summary, category totals, goal status, ≤50 recent transactions) — never the DB.
- **Refusals/odd replies** are treated identically to timeouts: fallback path.

### Fact-pack (`insights.py`)

Single JSON blob, deterministic, also used by briefing templates and tests:
cycle day/length, safe-to-spend, per-category spent vs budget + pace flag, statement
accrued + days-to-charge, per-goal progress/pace-needed/projection, last-cycle comparison,
notable streaks (e.g. "no eating-out 5 days").

### Advisor actions

Chat replies may include an `action` JSON block (create_goal, update_budget,
add_transaction, adjust_setting). The UI renders it as a confirm card; nothing is applied
without an explicit user click. Applied actions go through the same api.py code paths as
manual edits.

## 7. UI spec (dark Mission Control, tabbed cockpit)

- **Entry bar** (always visible, top): input + status chips area.
- **Overview**: Safe-to-spend today (hero number) = (discretionary budget remaining this
  cycle) ÷ (days left incl. today), where discretionary = total budget − fixed bills;
  cycle gauge (day X of N); category bars (spent vs budget, amber >100% pace); upcoming
  card charge panel (₪ accrued, T−n days); goal mini-bars; briefing panel; recent-entries
  ticker (last 5, with undo).
- **Ledger**: filterable table (month, category, free text); inline edit of amount /
  category / date / description; soft delete + undo; "needs review" filter; CSV export.
  Re-categorizing writes a category_rule (toast: "rule learned").
- **Goals**: card per goal — progress bar, ₪ saved / target, pace needed vs actual savings
  pace, projected completion date, safe-to-buy verdict (funds), add/edit/archive.
- **Advisor**: chat thread; briefing history; action confirm cards.
- **Onboarding (first run)**: chat-style wizard — name → salary amount + day → card charge
  day → fixed bills → brain-dump ("tell me what you have now and what you've spent this
  month so far") → Claude proposes opening balance, month-to-date entries, and per-category
  budgets → user reviews/edits → confirm writes everything in one transaction.
- Hebrew/Arabic/English input all supported (Claude handles language; UI is LTR English).

## 8. Reliability & error handling

- All Claude calls: background thread, timeout, pydantic validation, one repair retry,
  fallback path. The AI layer can never crash the app or block the UI.
- SQLite: WAL mode; all multi-row writes in transactions; schema_version + forward
  migrations in db.py.
- **Backups:** on app open, if last export < today: dump full DB to
  `backups/ledger-YYYYMMDD.json` (project folder → OneDrive syncs it); prune to 30 files.
  Restore = `python -m app --restore <file>`.
- Soft deletes everywhere a destructive action exists; undo on every entry chip.
- Single-instance: lockfile + localhost socket — second launch pings the first to focus
  its window and exits.
- Briefing/chat offline → deterministic template briefing from the fact-pack (numbers
  always correct); chat tab shows "advisor offline" state.

## 9. Testing

- pytest, Claude fully mocked: engine (all cycle edge cases listed in §4), budget math,
  goal projections, fallback parser, pydantic schemas against recorded real Claude
  responses, db migrations, backup/restore round-trip.
- `scripts/dev_seed.py` + `--dev` flag: seeded fake ledger for UI verification without
  touching real data.
- Definition of done for v1: all tests green; manual smoke of the four tabs + onboarding
  + an end-to-end NL entry on the dev seed.

## 10. Packaging & launch

- `scripts/setup.ps1`: create venv (py -3.11), pip install, write `MoneyPilot.lnk` on the
  Desktop pointing at `pythonw -m app` with the app icon.
- Window: pywebview, dark titlebar, remembered size/position.
- v2 option (not v1): PyInstaller single .exe.

## 11. Future ideas (explicitly out of v1)

Tray quick-capture hotkey; statement XLSX import; monthly PDF/email report; spending
heatmap calendar; shared budgets; voice entry; Android companion.
