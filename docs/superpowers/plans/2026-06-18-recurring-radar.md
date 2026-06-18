# Recurring / Subscription Radar — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Detect recurring charges (subscriptions / standing orders) from the existing ledger and surface them in the briefing/advisor and a new Overview card.

**Architecture:** A stateless engine module (`app/engine/recurring.py`) groups expenses by merchant/description, classifies monthly/annual cadence, gates on regularity + amount stability + confidence, and derives next-expected date + monthly-equivalent cost. The only persisted state is a dismiss-list in the settings KV. Surfaced via a `summary()` helper consumed by `fact_pack`, two new `Api` methods, and an Overview UI card.

**Tech Stack:** Python 3.11, SQLite (WAL), pytest; pywebview UI (vanilla JS/CSS).

## Global Constraints

- Amounts are signed integer **agorot**; expenses are stored **negative** (DB CHECK enforces it). Detection flips to positive via `-amount_agorot`.
- **No schema change / no migration.** The dismiss-list lives in the `settings` table via `db.get_setting`/`db.set_setting` under key `recurring_dismissed` (JSON list).
- Engine functions are **pure & stateless** (mirror `budget`/`goals`) and take `(conn, today: dt.date, ...)`. Tests pin behavior with explicit dates.
- AI is **FACTS-only**: prompts may only *mention* what `fact_pack` already contains; never invent figures. Keep prompt edits minimal so existing JSON-contract tests pass.
- Run tests with `.venv/Scripts/python.exe -m pytest`. UI strings go through `esc()`.
- Commit after each task. End commit messages with the `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>` trailer.

---

### Task 1: Detection engine — `recurring.py` (`detect` + dismiss-list)

**Files:**
- Create: `app/engine/recurring.py`
- Test: `tests/test_recurring.py`

**Interfaces:**
- Produces:
  - `detect(conn, today: dt.date, *, lookback_days: int = 400, min_occurrences: int = 3) -> list[dict]` — confidence-sorted; each dict has keys `key, name, cadence("monthly"|"annual"), typical_agorot, last_charged, next_expected, monthly_equiv_agorot, occurrences, confidence, price_hike`.
  - `dismiss(conn, key: str) -> None`, `undismiss(conn, key: str) -> None`, `_dismissed(conn) -> set[str]`, `_norm(text: str) -> str`.

- [ ] **Step 1: Write failing tests** (`tests/test_recurring.py`)

```python
import datetime as dt
import json

from app import db
from app.engine import recurring

TODAY = dt.date(2026, 6, 20)


def _add(conn, date, ils, merchant="Netflix", description="netflix sub"):
    db.add_transaction(conn, effective_date=date, amount_agorot=-int(ils * 100),
                       direction="expense", merchant=merchant, description=description)


def _series(conn, dates, ils=45.0, **kw):
    for d in dates:
        _add(conn, d, ils, **kw)


def test_detects_three_monthly_charges(conn):
    _series(conn, [dt.date(2026, 3, 21), dt.date(2026, 4, 20), dt.date(2026, 5, 20)])
    (r,) = recurring.detect(conn, TODAY)
    assert r["cadence"] == "monthly"
    assert r["typical_agorot"] == 4500
    assert r["monthly_equiv_agorot"] == 4500
    assert r["last_charged"] == "2026-05-20"
    assert r["next_expected"] == "2026-06-19"   # 2026-05-20 + 30 days
    assert r["occurrences"] == 3
    assert r["price_hike"] is False


def test_two_occurrences_not_recurring(conn):
    _series(conn, [dt.date(2026, 4, 20), dt.date(2026, 5, 20)])
    assert recurring.detect(conn, TODAY) == []


def test_irregular_gaps_rejected(conn):
    # median gap lands in the monthly window but spacing is wildly uneven
    _series(conn, [dt.date(2026, 3, 1), dt.date(2026, 3, 31), dt.date(2026, 5, 25)])
    assert recurring.detect(conn, TODAY) == []


def test_variable_amounts_rejected(conn):
    # same merchant, monthly-ish spacing, but amounts swing like a grocery run
    for d, ils in [(dt.date(2026, 3, 20), 40), (dt.date(2026, 4, 20), 230),
                   (dt.date(2026, 5, 20), 95)]:
        _add(conn, d, ils, merchant="Shufersal", description="groceries")
    assert recurring.detect(conn, TODAY) == []


def test_small_amount_variance_still_detected(conn):
    _series(conn, [dt.date(2026, 3, 21), dt.date(2026, 4, 20), dt.date(2026, 5, 20)],
            ils=45.0)
    # add a 4th within +-10%
    _add(conn, dt.date(2026, 2, 19), 48.0)
    (r,) = recurring.detect(conn, TODAY)
    assert r["cadence"] == "monthly" and r["occurrences"] == 4


def test_price_hike_flagged(conn):
    _add(conn, dt.date(2026, 3, 21), 30.0)
    _add(conn, dt.date(2026, 4, 20), 30.0)
    _add(conn, dt.date(2026, 5, 20), 45.0)   # latest is +50%
    (r,) = recurring.detect(conn, TODAY)
    assert r["price_hike"] is True
    assert r["typical_agorot"] == 3000       # median of 30/30/45


def test_annual_cadence(conn):
    _series(conn, [dt.date(2024, 6, 18), dt.date(2025, 6, 18), dt.date(2026, 6, 18)],
            ils=240.0, merchant="DomainCo", description="domain renewal")
    (r,) = recurring.detect(conn, TODAY)
    assert r["cadence"] == "annual"
    assert r["monthly_equiv_agorot"] == 2000   # round(24000 / 12)


def test_description_fallback_when_merchant_null(conn):
    _series(conn, [dt.date(2026, 3, 21), dt.date(2026, 4, 20), dt.date(2026, 5, 20)],
            merchant=None, description="gym membership")
    (r,) = recurring.detect(conn, TODAY)
    assert "gym" in r["key"]


def test_dismissed_key_excluded_then_restored(conn):
    _series(conn, [dt.date(2026, 3, 21), dt.date(2026, 4, 20), dt.date(2026, 5, 20)])
    (r,) = recurring.detect(conn, TODAY)
    recurring.dismiss(conn, r["key"])
    assert recurring.detect(conn, TODAY) == []
    assert json.loads(db.get_setting(conn, "recurring_dismissed")) == [r["key"]]
    recurring.undismiss(conn, r["key"])
    assert len(recurring.detect(conn, TODAY)) == 1
```

- [ ] **Step 2: Run tests, verify they fail**

Run: `.venv/Scripts/python.exe -m pytest tests/test_recurring.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.engine.recurring'`.

- [ ] **Step 3: Implement `app/engine/recurring.py`**

```python
"""Stateless recurring-charge ("subscription") detector.

Pure read over `transactions` (mirrors budget/goals): group expenses by
merchant/description, find a regular monthly/annual cadence with stable
amounts, and report next-expected date + monthly-equivalent cost. The only
persisted state is a user dismiss-list in the settings KV (no schema change).
"""
from __future__ import annotations

import datetime as dt
import json
import statistics

from app import db

_DISMISS_KEY = "recurring_dismissed"
_CONFIDENCE_FLOOR = 0.6
_AMOUNT_TOL = 0.15            # +-15% of the median counts as "stable"
_PRICE_HIKE_RATIO = 1.25
_CADENCE = {                 # name: (min_gap, max_gap, regularity_tol) in days
    "monthly": (26, 35, 7),
    "annual": (350, 380, 45),
}


def _norm(text) -> str:
    s = (text or "").strip().lower()
    s = s.split(" with ")[0]          # drop a trailing "with <people>" clause
    return " ".join(s.split())


def _dismissed(conn) -> set[str]:
    try:
        items = json.loads(db.get_setting(conn, _DISMISS_KEY, "[]"))
        return {str(k) for k in items} if isinstance(items, list) else set()
    except (ValueError, TypeError):
        return set()


def dismiss(conn, key: str) -> None:
    k = _norm(key)
    if not k:
        raise ValueError("nothing to dismiss")
    db.set_setting(conn, _DISMISS_KEY, json.dumps(sorted(_dismissed(conn) | {k})))


def undismiss(conn, key: str) -> None:
    db.set_setting(conn, _DISMISS_KEY,
                   json.dumps(sorted(_dismissed(conn) - {_norm(key)})))


def _classify(median_gap: float):
    for name, (lo, hi, _tol) in _CADENCE.items():
        if lo <= median_gap <= hi:
            return name
    return None


def _confidence(occ, gaps, median_gap, amounts, typical) -> float:
    occ_score = min(1.0, occ / 6)
    reg = (1 - max(abs(g - median_gap) for g in gaps) / median_gap
           if median_gap and gaps else 0.0)
    amt = (1 - max(abs(a - typical) for a in amounts) / typical
           if typical else 0.0)
    return 0.4 * occ_score + 0.35 * max(0.0, reg) + 0.25 * max(0.0, amt)


def detect(conn, today: dt.date, *, lookback_days: int = 400,
           min_occurrences: int = 3) -> list[dict]:
    since = (today - dt.timedelta(days=lookback_days)).isoformat()
    rows = conn.execute(
        "SELECT effective_date, amount_agorot, merchant, description"
        " FROM transactions WHERE deleted_at IS NULL AND direction='expense'"
        " AND effective_date >= ? AND effective_date <= ?"
        " ORDER BY effective_date",
        (since, today.isoformat())).fetchall()

    groups: dict[str, list] = {}
    for r in rows:
        key = _norm(r["merchant"] or r["description"])
        if key:
            groups.setdefault(key, []).append(r)

    dismissed = _dismissed(conn)
    out = []
    for key, occ in groups.items():
        if key in dismissed or len(occ) < min_occurrences:
            continue
        dates = [dt.date.fromisoformat(r["effective_date"]) for r in occ]
        amounts = [-r["amount_agorot"] for r in occ]      # expenses are negative
        gaps = [(dates[i] - dates[i - 1]).days for i in range(1, len(dates))]
        median_gap = statistics.median(gaps)
        cadence = _classify(median_gap)
        if cadence is None:
            continue
        if max(abs(g - median_gap) for g in gaps) > _CADENCE[cadence][2]:
            continue                                       # irregular spacing
        typical = int(statistics.median(amounts))
        if typical <= 0:
            continue
        stable = [a for a in amounts if abs(a - typical) <= _AMOUNT_TOL * typical]
        # tolerate at most ONE outlier, and only if it's the most-recent charge
        if len(stable) < len(amounts) - 1:
            continue
        if len(stable) == len(amounts) - 1 and \
                abs(amounts[-1] - typical) <= _AMOUNT_TOL * typical:
            continue                                       # the outlier isn't the latest
        confidence = _confidence(len(occ), gaps, median_gap, amounts, typical)
        if confidence < _CONFIDENCE_FLOOR:
            continue
        next_expected = dates[-1] + dt.timedelta(days=round(median_gap))
        monthly_equiv = typical if cadence == "monthly" else round(typical / 12)
        name = str(occ[-1]["merchant"] or occ[-1]["description"] or key).strip().title()
        out.append({
            "key": key, "name": name, "cadence": cadence,
            "typical_agorot": typical,
            "last_charged": dates[-1].isoformat(),
            "next_expected": next_expected.isoformat(),
            "monthly_equiv_agorot": monthly_equiv,
            "occurrences": len(occ),
            "confidence": round(confidence, 2),
            "price_hike": amounts[-1] > typical * _PRICE_HIKE_RATIO,
        })
    out.sort(key=lambda d: d["confidence"], reverse=True)
    return out
```

- [ ] **Step 4: Run tests, verify they pass**

Run: `.venv/Scripts/python.exe -m pytest tests/test_recurring.py -q`
Expected: PASS (all). If `next_expected` differs, confirm Python's `round(30.5) == 30` (banker's rounding) — the test dates use exact 30-day gaps so `median_gap == 30`.

- [ ] **Step 5: Commit**

```bash
git add app/engine/recurring.py tests/test_recurring.py
git commit -m "feat(recurring): stateless subscription detector + dismiss-list"
```

---

### Task 2: `summary()` aggregator

**Files:**
- Modify: `app/engine/recurring.py`
- Test: `tests/test_recurring.py`

**Interfaces:**
- Consumes: `detect()` (Task 1).
- Produces: `summary(conn, today, *, soon_days: int = 7) -> dict` with keys `items` (ALL detect dicts), `monthly_total_agorot` (sum of `monthly_equiv_agorot` over ALL items), `upcoming` (items whose `next_expected` is within `[today, today+soon_days]`).

- [ ] **Step 1: Write failing tests** (append to `tests/test_recurring.py`)

```python
def test_summary_monthly_total_sums_all_items(conn):
    _series(conn, [dt.date(2026, 3, 21), dt.date(2026, 4, 20), dt.date(2026, 5, 20)],
            ils=45.0, merchant="Netflix", description="netflix")
    _series(conn, [dt.date(2026, 3, 10), dt.date(2026, 4, 9), dt.date(2026, 5, 9)],
            ils=20.0, merchant="Spotify", description="spotify")
    s = recurring.summary(conn, TODAY)
    assert len(s["items"]) == 2
    assert s["monthly_total_agorot"] == 6500     # 4500 + 2000


def test_summary_upcoming_window(conn):
    # next_expected == 2026-06-19 (within 7 days of TODAY 2026-06-20? it's in the past
    # by 1 day -> NOT upcoming). Use a series whose next lands a few days ahead.
    _series(conn, [dt.date(2026, 3, 25), dt.date(2026, 4, 24), dt.date(2026, 5, 24)],
            merchant="Netflix", description="netflix")
    s = recurring.summary(conn, TODAY)         # next = 2026-06-23, within 7d
    assert [i["name"] for i in s["upcoming"]] == ["Netflix"]


def test_summary_excludes_far_future_from_upcoming(conn):
    _series(conn, [dt.date(2024, 6, 18), dt.date(2025, 6, 18), dt.date(2026, 6, 18)],
            ils=240.0, merchant="DomainCo", description="domain")
    s = recurring.summary(conn, TODAY)         # annual next ~2027 -> not upcoming
    assert s["upcoming"] == []
    assert len(s["items"]) == 1
```

- [ ] **Step 2: Run, verify fail**

Run: `.venv/Scripts/python.exe -m pytest tests/test_recurring.py -q -k summary`
Expected: FAIL — `AttributeError: module 'app.engine.recurring' has no attribute 'summary'`.

- [ ] **Step 3: Implement (append to `app/engine/recurring.py`)**

```python
def summary(conn, today: dt.date, *, soon_days: int = 7) -> dict:
    items = detect(conn, today)
    horizon = (today + dt.timedelta(days=soon_days)).isoformat()
    t = today.isoformat()
    return {
        "items": items,
        "monthly_total_agorot": sum(i["monthly_equiv_agorot"] for i in items),
        "upcoming": [i for i in items if t <= i["next_expected"] <= horizon],
    }
```

- [ ] **Step 4: Run, verify pass**

Run: `.venv/Scripts/python.exe -m pytest tests/test_recurring.py -q`
Expected: PASS (all).

- [ ] **Step 5: Commit**

```bash
git add app/engine/recurring.py tests/test_recurring.py
git commit -m "feat(recurring): summary() — monthly burn + upcoming window"
```

---

### Task 3: API surface — `get_recurring` / `dismiss_recurring`

**Files:**
- Modify: `app/api.py` (import + two `@_safe` methods), `web/server.py` (ALLOWED set)
- Test: `tests/test_api.py`

**Interfaces:**
- Consumes: `recurring.summary` / `recurring.dismiss` (Tasks 1–2).
- Produces: `Api.get_recurring()` → `{ok, items:[...+typical_fmt,monthly_equiv_fmt], upcoming:[...], monthly_total_agorot, monthly_total_fmt}`; `Api.dismiss_recurring(key)` → `{ok}`.

- [ ] **Step 1: Write failing tests** (append to `tests/test_api.py`)

```python
def test_get_recurring_and_dismiss(tmp_path):
    import datetime as dt
    from app.api import Api
    from app import db
    api = Api(tmp_path / "l.db", backup_dir=tmp_path / "b",
              today_fn=lambda: dt.date(2026, 6, 20))
    for d in ("2026-03-21", "2026-04-20", "2026-05-20"):
        db.add_transaction(api.conn, effective_date=d, amount_agorot=-4500,
                           direction="expense", merchant="Netflix",
                           description="netflix")
    res = api.get_recurring()
    assert res["ok"] is True
    assert len(res["items"]) == 1
    item = res["items"][0]
    assert item["typical_fmt"].startswith("₪") and "monthly_equiv_fmt" in item
    assert res["monthly_total_fmt"].startswith("₪")

    assert api.dismiss_recurring(item["key"])["ok"] is True
    assert api.get_recurring()["items"] == []
```

- [ ] **Step 2: Run, verify fail**

Run: `.venv/Scripts/python.exe -m pytest tests/test_api.py::test_get_recurring_and_dismiss -q`
Expected: FAIL — `AttributeError: 'Api' object has no attribute 'get_recurring'`.

- [ ] **Step 3: Implement**

In `app/api.py`, extend the engine import (currently `from app.engine import budget, goals as goals_eng, insights`):

```python
from app.engine import budget, goals as goals_eng, insights, recurring
```

Add these two methods to `Api` (after `get_goals`/`save_goal`, before the advisor section):

```python
    @_safe
    def get_recurring(self):
        s = recurring.summary(self.conn, self._today())

        def _fmt(i):
            return {**i, "typical_fmt": fmt_ils(i["typical_agorot"]),
                    "monthly_equiv_fmt": fmt_ils(i["monthly_equiv_agorot"])}

        return {"items": [_fmt(i) for i in s["items"]],
                "upcoming": [_fmt(i) for i in s["upcoming"]],
                "monthly_total_agorot": s["monthly_total_agorot"],
                "monthly_total_fmt": fmt_ils(s["monthly_total_agorot"])}

    @_safe
    def dismiss_recurring(self, key: str):
        with self._lock:
            recurring.dismiss(self.conn, str(key))
        return {}
```

In `web/server.py`, add both names to the `ALLOWED` set (find the set literal of permitted method names and add `"get_recurring"`, `"dismiss_recurring"`).

- [ ] **Step 4: Run, verify pass**

Run: `.venv/Scripts/python.exe -m pytest tests/test_api.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/api.py web/server.py tests/test_api.py
git commit -m "feat(api): get_recurring / dismiss_recurring (+ web ALLOWED)"
```

---

### Task 4: `fact_pack` integration

**Files:**
- Modify: `app/engine/insights.py`
- Test: `tests/test_insights.py`

**Interfaces:**
- Consumes: `recurring.summary` (Task 2).
- Produces: a `recurring` key in `fact_pack` output: `{monthly_total_agorot, monthly_total_fmt, count, items:[{name,cadence,typical_fmt,next_expected,monthly_equiv_fmt} ...≤5], upcoming:[{name,typical_fmt,next_expected} ...]}`.

- [ ] **Step 1: Write failing test** (append to `tests/test_insights.py`; if the file doesn't exist, create it with the same `conn` fixture style — it uses the shared `conftest.py` fixtures)

```python
def test_fact_pack_includes_recurring(conn):
    import datetime as dt
    from app import db
    from app.engine import insights
    for d in ("2026-03-21", "2026-04-20", "2026-05-20"):
        db.add_transaction(conn, effective_date=d, amount_agorot=-4500,
                           direction="expense", merchant="Netflix",
                           description="netflix")
    fp = insights.fact_pack(conn, dt.date(2026, 6, 20))
    assert fp["recurring"]["count"] == 1
    assert fp["recurring"]["monthly_total_fmt"].startswith("₪")
    assert fp["recurring"]["items"][0]["name"] == "Netflix"
```

- [ ] **Step 2: Run, verify fail**

Run: `.venv/Scripts/python.exe -m pytest tests/test_insights.py::test_fact_pack_includes_recurring -q`
Expected: FAIL — `KeyError: 'recurring'`.

- [ ] **Step 3: Implement**

In `app/engine/insights.py`, extend the engine import to include `recurring`:

```python
from app.engine import budget, cycles, goals, recurring
```

Inside `fact_pack`, before the `return`, compute the block:

```python
    rec = recurring.summary(conn, today)
```

Add this key to the returned dict (e.g. after `"goals": [...]`):

```python
        "recurring": {
            "monthly_total_agorot": rec["monthly_total_agorot"],
            "monthly_total_fmt": fmt_ils(rec["monthly_total_agorot"]),
            "count": len(rec["items"]),
            "items": [{"name": i["name"], "cadence": i["cadence"],
                       "typical_fmt": fmt_ils(i["typical_agorot"]),
                       "next_expected": i["next_expected"],
                       "monthly_equiv_fmt": fmt_ils(i["monthly_equiv_agorot"])}
                      for i in rec["items"][:5]],
            "upcoming": [{"name": i["name"],
                          "typical_fmt": fmt_ils(i["typical_agorot"]),
                          "next_expected": i["next_expected"]}
                         for i in rec["upcoming"]],
        },
```

- [ ] **Step 4: Run, verify pass**

Run: `.venv/Scripts/python.exe -m pytest tests/test_insights.py tests/test_advisor.py -q`
Expected: PASS (advisor tests use `fact_pack`; confirm none break).

- [ ] **Step 5: Commit**

```bash
git add app/engine/insights.py tests/test_insights.py
git commit -m "feat(insights): expose recurring radar in fact_pack"
```

---

### Task 5: Prompt wiring (briefing + advisor)

**Files:**
- Modify: `app/ai/prompts.py`
- Test: `tests/test_advisor.py` (a light assertion that the briefing can reference recurring; the existing contract tests must still pass)

**Interfaces:**
- Consumes: the `recurring` FACTS block (Task 4).
- Produces: no new symbols; additive sentences in `BRIEFING_SYSTEM` and `CHAT_SYSTEM`.

- [ ] **Step 1: Write failing test** (append to `tests/test_advisor.py`)

```python
def test_briefing_can_mention_recurring(seeded, monkeypatch):
    # Prove the briefing PROMPT now carries recurring guidance (so the model is
    # allowed to mention an imminent charge). We assert on the prompt text the
    # advisor would send, captured via the ask_claude stub.
    from app.ai import prompts
    assert "recurring" in prompts.BRIEFING_SYSTEM.lower()
    assert "recurring" in prompts.CHAT_SYSTEM.lower()
```

- [ ] **Step 2: Run, verify fail**

Run: `.venv/Scripts/python.exe -m pytest tests/test_advisor.py::test_briefing_can_mention_recurring -q`
Expected: FAIL — `AssertionError` (prompts don't mention "recurring" yet).

- [ ] **Step 3: Implement**

In `app/ai/prompts.py`, append to the end of the `BRIEFING_SYSTEM` string (inside the closing `"""`):

```
 The FACTS may include a `recurring` block (detected subscriptions / standing orders): if `recurring.upcoming` is non-empty, you may lead with the imminent charge (e.g. "Spotify ~₪20 hits in 3 days"); otherwise you may note `recurring.monthly_total_fmt` as the monthly recurring burn. Use only these FACTS figures.
```

Append to the end of `CHAT_SYSTEM`:

```
 If the user asks what they're subscribed to or what their recurring/monthly committed spending is, answer from the FACTS `recurring` block (its items, next_expected dates, and monthly_total_fmt) — never invent subscriptions.
```

- [ ] **Step 4: Run, verify pass**

Run: `.venv/Scripts/python.exe -m pytest tests/test_advisor.py -q`
Expected: PASS (all — including `test_chat_extracts_action_block`, `test_chat_strips_every_action_fence`, etc., which don't depend on the appended sentences).

- [ ] **Step 5: Commit**

```bash
git add app/ai/prompts.py tests/test_advisor.py
git commit -m "feat(prompts): let briefing/advisor speak to the recurring radar"
```

---

### Task 6: Overview "Recurring" card (UI)

**Files:**
- Modify: `app/ui/index.html` (add a panel), `app/ui/app.js` (render fn + call), `app/ui/app.css` (minor styles)

**Interfaces:**
- Consumes: `Api.get_recurring` / `Api.dismiss_recurring` (Task 3).
- Produces: a `#ov-recurring` panel rendered by `renderRecurring()`, called from the Overview render.

No automated test (no JS harness). Verify with `node --check app/ui/app.js` and a Playwright mocked-bridge render (optional, manual).

- [ ] **Step 1: Add the panel to `index.html`**

Inside `<div class="grid-ov">`, after the GOALS panel (`<div class="panel"><div class="label">GOALS</div>...</div>`, ends ~line 176) and before the RECENT panel, insert:

```html
      <div class="panel">
        <div class="label">RECURRING
          <span id="ov-rec-total" class="rec-total"></span>
        </div>
        <div id="ov-recurring"></div>
      </div>
```

- [ ] **Step 2: Add `renderRecurring()` to `app.js`**

After the Overview render function (right after the `$("#ov-brief-refresh")` listener block, ~line 347) add:

```javascript
async function renderRecurring() {
  const res = await api("get_recurring");
  const box = $("#ov-recurring");
  if (!res || res.ok === false) { box.innerHTML = ""; return; }
  $("#ov-rec-total").textContent =
    res.items.length ? `${res.monthly_total_fmt}/mo` : "";
  if (!res.items.length) {
    box.innerHTML = `<span class="sub">No recurring charges detected yet — `
      + `they'll appear as your history builds.</span>`;
    return;
  }
  box.innerHTML = res.items.map((i) => `
    <div class="rec-row">
      <div class="meta">
        <span>${esc(i.name)}${i.price_hike ? ' <span class="rec-hike">↑</span>' : ""}</span>
        <span>${esc(i.typical_fmt)} · ${esc(i.cadence)}</span>
      </div>
      <div class="rec-sub">
        <span class="sub">next ${esc(i.next_expected)}</span>
        <button class="rec-x" data-key="${esc(i.key)}" title="not a subscription">×</button>
      </div>
    </div>`).join("");
  box.querySelectorAll(".rec-x").forEach((btn) => {
    btn.onclick = async () => {
      await api("dismiss_recurring", btn.dataset.key);
      renderRecurring();
    };
  });
}
```

- [ ] **Step 3: Call it from the Overview render**

In the Overview render function, just before its closing `};` (right after the briefing block, ~line 337), add:

```javascript
  renderRecurring();
```

(Not awaited — it populates its own panel independently and shouldn't block the hero/dial paint.)

- [ ] **Step 4: Add CSS to `app.css`** (append near the other Overview row styles)

```css
.rec-total { float: right; color: var(--accent); font-weight: 600; }
.rec-row { padding: 6px 0; border-bottom: 1px solid var(--line, #1c2230); }
.rec-row:last-child { border-bottom: 0; }
.rec-row .meta { display: flex; justify-content: space-between; gap: 8px; }
.rec-sub { display: flex; justify-content: space-between; align-items: center; }
.rec-hike { color: var(--amber); }
.rec-x { background: none; border: 0; color: var(--muted, #6b7488);
         cursor: pointer; font-size: 15px; line-height: 1; padding: 0 4px; }
.rec-x:hover { color: var(--danger, #ff6b6b); }
```

(If `--line`/`--muted`/`--danger` aren't defined, the fallbacks apply — verify against `:root` in app.css and use the existing token if present.)

- [ ] **Step 5: Verify + commit**

Run: `node --check app/ui/app.js` → no output (syntax OK).
Run: `.venv/Scripts/python.exe -m pytest -q` → full suite green.

```bash
git add app/ui/index.html app/ui/app.js app/ui/app.css
git commit -m "feat(ui): Overview Recurring card with per-row dismiss"
```

---

## Self-Review

**Spec coverage:**
- Stateless `recurring.py` detector → Task 1 ✓
- Dismiss-list in settings KV → Task 1 ✓
- `summary()` with full monthly_total + upcoming → Task 2 ✓
- API `get_recurring`/`dismiss_recurring` + web ALLOWED → Task 3 ✓
- `fact_pack` `recurring` block (capped items, full total) → Task 4 ✓
- Prompt wiring (briefing + advisor) → Task 5 ✓
- Overview UI card + dismiss + empty state → Task 6 ✓
- Detection gates (min_occurrences, cadence monthly/annual, regularity, ±15% stability, confidence ≥0.6, price_hike, description fallback) → Task 1 tests ✓
- Non-goals (runway/anomaly/weekly/notifications) → out of scope, not implemented ✓

**Placeholder scan:** none — every step has runnable code/commands.

**Type consistency:** `detect()` dict keys are referenced identically by `summary` (Task 2), `get_recurring` (Task 3), `fact_pack` (Task 4), and the UI (`name`, `key`, `typical_fmt`, `monthly_equiv_fmt`, `next_expected`, `cadence`, `price_hike`). `summary` returns `items`/`monthly_total_agorot`/`upcoming`, consumed consistently. `dismiss(conn, key)` signature matches the API call. ✓
