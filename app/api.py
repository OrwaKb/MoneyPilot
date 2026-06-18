from __future__ import annotations

import csv
import datetime as dt
import functools
import re
import sqlite3
import threading
from pathlib import Path

from app import db
from app.ai import advisor, parser
from app.engine import budget, goals as goals_eng, insights, recurring
from app.models import fmt_ils, parse_iso_date, to_agorot, to_whole_agorot

ONBOARD_KEYS = ("user_name", "salary_day", "salary_amount_agorot",
                "card_charge_day")


def _safe(fn):
    """Every bridge method returns a dict; exceptions become {ok: False}.

    error_kind tags whether the message is meant for the user (a ValueError we
    raised on purpose) or is an unexpected internal failure. The desktop shows
    the message either way; the web layer uses the tag to hide internal detail
    (SQLite text, file paths) from a remote client."""
    @functools.wraps(fn)
    def wrapper(self, *args, **kwargs):
        try:
            out = fn(self, *args, **kwargs)
            out.setdefault("ok", True)
            return out
        except ValueError as e:           # deliberate, user-facing validation
            return {"ok": False, "error": str(e), "error_kind": "user"}
        except Exception as e:            # unexpected — message may leak internals
            return {"ok": False, "error": str(e), "error_kind": "internal"}
    return wrapper


def _txn_dict(row) -> dict:
    d = dict(row)
    d["amount_fmt"] = fmt_ils(row["amount_agorot"])
    return d


class Api:
    def __init__(self, db_path, *, backup_dir, today_fn=dt.date.today,
                 init=True):
        self.conn = db.connect(db_path)
        if init:                       # web uses per-request connections and
            db.init_db(self.conn)      # inits the schema once per user instead
        self.backup_dir = Path(backup_dir)
        self._today = today_fn
        self._lock = threading.Lock()
        self._window = None  # set by __main__ for focus/quit

    # --- lifecycle -----------------------------------------------------------

    def is_onboarded(self) -> bool:
        return db.get_setting(self.conn, "salary_day") is not None

    @_safe
    def startup(self):
        """Called by the UI once on load: backup + best-effort review resweep."""
        from app import version
        with self._lock:
            try:
                db.write_daily_backup(self.conn, self.backup_dir, self._today())
            except OSError:
                pass  # backup is best-effort; never break startup
            try:
                parser.resweep(self.conn, self._today())
            except Exception:
                pass  # offline is fine
        return {"onboarded": self.is_onboarded(),
                "user_name": db.get_setting(self.conn, "user_name", ""),
                "version": version.__version__}

    @_safe
    def check_update(self):
        """Whether a newer build exists (desktop only). Fail-silent in update.py
        so a slow/absent network never blocks or breaks the UI."""
        from app import update
        return update.check_for_update()

    @_safe
    def open_external(self, url: str):
        """Open a link in the user's real browser (e.g. the update download).
        http/https only — never a file:// or other scheme from a reply."""
        import webbrowser
        u = str(url or "")
        if not (u.startswith("https://") or u.startswith("http://")):
            raise ValueError("refusing to open a non-web link")
        webbrowser.open(u)
        return {}

    # --- entries ---------------------------------------------------------------

    @_safe
    def add_entry(self, text: str):
        with self._lock:
            res = parser.parse_and_store(self.conn, text, self._today())
        ids = res["entries"]
        qmarks = ",".join("?" * len(ids))
        rows = self.conn.execute(
            "SELECT t.*, c.name AS category_name, c.emoji AS category_emoji"
            " FROM transactions t LEFT JOIN categories c ON c.id=t.category_id"
            f" WHERE t.id IN ({qmarks})", ids).fetchall()
        return {"entries": [_txn_dict(r) for r in rows],
                "used_ai": res["used_ai"], "source": res["source"]}

    @_safe
    def undo_txn(self, txn_id: int):
        with self._lock:
            db.soft_delete_transaction(self.conn, txn_id)
        return {}

    @_safe
    def restore_txn(self, txn_id: int):
        with self._lock:
            db.undelete_transaction(self.conn, txn_id)
        return {}

    @_safe
    def update_txn(self, txn_id: int, fields: dict):
        allowed = {"description", "amount_agorot", "category_id",
                   "effective_date", "payment_method", "needs_review"}
        clean = {k: v for k, v in fields.items() if k in allowed}
        with self._lock:
            old = self.conn.execute(
                "SELECT * FROM transactions WHERE id=?", (txn_id,)).fetchone()
            try:
                db.update_transaction(self.conn, txn_id, **clean)
            except sqlite3.IntegrityError as e:
                # the txn table has 3 constraints that all raise IntegrityError:
                # the amount-sign CHECK, the category FK, the payment_method CHECK
                # — report the one the edit actually hit, not always "sign".
                msg = str(e).upper()
                if "FOREIGN KEY" in msg:
                    raise ValueError("that category no longer exists") from e
                if "amount_agorot" in clean:
                    raise ValueError("amount sign must match direction"
                                     " (income +, expense/goal -)") from e
                raise ValueError("could not update transaction"
                                 " (invalid value)") from e
            if ("category_id" in clean and old
                    and clean["category_id"] != old["category_id"]
                    and old["description"]):
                cat = self.conn.execute(
                    "SELECT is_income FROM categories WHERE id=?",
                    (clean["category_id"],)).fetchone()
                if cat and not cat["is_income"]:
                    key = old["description"].strip().lower().split(" with ")[0]
                    if key and db.match_rule(self.conn, key) != clean["category_id"]:
                        db.add_rule(self.conn, key, clean["category_id"],
                                    created_from_txn=txn_id)
        return {}

    # --- views -----------------------------------------------------------------

    @_safe
    def get_overview(self):
        today = self._today()
        # get_overview never returns fp["recurring"] (the Overview card fetches
        # it via get_recurring), so skip that detect() pass here.
        fp = insights.fact_pack(self.conn, today, include_recurring=False)
        cycle_start = dt.date.fromisoformat(fp["cycle"]["start"])
        return {"safe_to_spend": fp["safe_to_spend"],
                "categories": fp["categories"], "card": fp["card"],
                "goals": fp["goals"], "cycle": fp["cycle"],
                "balance": fp["balance"],
                # daily expense totals (positive agorot), cycle start -> today
                "spark": budget.daily_expenses(self.conn, cycle_start, today),
                "recent": [
                    _txn_dict(r) for r in db.list_transactions(self.conn, limit=5)]}

    @_safe
    def list_ledger(self, filters: dict):
        kw = {}
        if filters.get("month"):           # "2026-06"
            y, m = map(int, filters["month"].split("-"))
            kw["start"] = dt.date(y, m, 1)
            kw["end"] = (dt.date(y + 1, 1, 1) if m == 12
                         else dt.date(y, m + 1, 1)) - dt.timedelta(days=1)
        if filters.get("category_id"):
            kw["category_id"] = int(filters["category_id"])
        if filters.get("text"):
            kw["text"] = filters["text"]
        if filters.get("needs_review"):
            kw["needs_review"] = True
        rows = db.list_transactions(self.conn, limit=500, **kw)
        return {"rows": [_txn_dict(r) for r in rows],
                "categories": [dict(c) for c in db.categories(self.conn)]}

    @_safe
    def get_goals(self):
        reports = goals_eng.goal_report(self.conn, self._today())
        for g in reports:
            g["target_fmt"] = fmt_ils(g["target_agorot"])
            g["progress_fmt"] = fmt_ils(g["progress_agorot"])
            if g["pace_needed_agorot"] is not None:
                g["pace_needed_fmt"] = fmt_ils(g["pace_needed_agorot"])
            if isinstance(g.get("projected_date"), dt.date):
                g["projected_date"] = g["projected_date"].isoformat()
        return {"goals": reports}

    @_safe
    def save_goal(self, g: dict):
        with self._lock:
            name = str(g.get("name") or "").strip()
            if not name:
                raise ValueError("goal name must not be empty")
            try:
                target = to_agorot(g.get("target_ils"))
            except ValueError:
                raise ValueError("enter the goal's target as a number of shekels")
            if target <= 0:
                raise ValueError("goal target must be positive")
            target_date = (parse_iso_date(g["target_date"],
                                          label="the goal's target date")
                           if g.get("target_date") else None)
            if g.get("id"):
                db.update_goal(self.conn, int(g["id"]), name=name,
                               target_agorot=target, target_date=target_date)
            else:
                db.add_goal(self.conn, name=name,
                            emoji=g.get("emoji", "🎯"),
                            type=("save_by_date" if g.get("goal_type") ==
                                  "save_by_date" else "purchase_fund"),
                            target_agorot=target, target_date=target_date)
        return {}

    @_safe
    def archive_goal(self, goal_id: int):
        with self._lock:
            db.update_goal(self.conn, goal_id, status="archived")
        return {}

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

    # --- advisor -----------------------------------------------------------------

    @_safe
    def get_briefing(self, force: bool = False):
        with self._lock:
            return advisor.get_briefing(self.conn, self._today(), force=force)

    @_safe
    def chat_send(self, text: str, conversation_id=None):
        conversation_id = int(conversation_id) if conversation_id is not None else None
        with self._lock:
            return advisor.chat(self.conn, text, self._today(),
                                conversation_id=conversation_id)

    @_safe
    def chat_apply_action(self, action: dict):
        with self._lock:
            return advisor.apply_action(self.conn, action, self._today())

    @_safe
    def ai_status(self):
        """Whether the AI is connected on THIS machine (desktop only). The web
        build always uses the host's login, so its UI never asks."""
        from app.ai import client
        return client.ai_auth_status()

    @_safe
    def connect_ai(self):
        """Open the Claude sign-in flow so a friend can enable AI on their PC."""
        from app.ai import client
        client.start_ai_login()
        return {}

    @_safe
    def pocket_info(self):
        """Pairing info for the phone capture app (desktop only): the GitHub
        Pages URL, the pairing token, this PC's Tailscale URL (best-effort), and
        a one-tap pairing link that pre-fills both on the phone."""
        from urllib.parse import urlencode
        from app import pocket, sync_server, version
        owner, _, repo = (version.GITHUB_REPO or "/").partition("/")
        page = f"https://{owner.lower()}.github.io/{repo}/pocket/"
        token = pocket.get_token(self.conn)
        url = pocket.tailscale_url()
        pair_link = (page + "#" + urlencode({"url": url, "token": token})
                     if url else None)
        return {"page": page, "token": token, "url": url,
                "pair_link": pair_link, "port": sync_server.DEFAULT_PORT}

    @_safe
    def get_chat_history(self, conversation_id=None):
        return {"messages": [dict(c)
                             for c in db.recent_chat(self.conn, 50,
                                                     conversation_id)]}

    @_safe
    def list_chats(self):
        return {"chats": db.list_conversations(self.conn)}

    @_safe
    def delete_chat(self, conversation_id: int):
        with self._lock:
            db.delete_conversation(self.conn, int(conversation_id))
        return {}

    # --- settings & onboarding ------------------------------------------------------

    @_safe
    def get_app_settings(self):
        return {"settings": db.get_settings(self.conn),
                "categories": [dict(c) for c in db.categories(self.conn)],
                "budgets": {str(k): v for k, v in db.get_budgets(self.conn).items()}}

    @_safe
    def save_settings(self, settings: dict):
        # validate the known keys BEFORE writing any, so a bad value can't leave
        # a half-applied settings change (db helpers autocommit per call)
        clean = {}
        for k, v in settings.items():
            if v is None:
                continue
            v = str(v).strip()
            if k in ("salary_day", "card_charge_day"):
                if not v.isdigit() or not 1 <= int(v) <= 31:
                    raise ValueError(f"{k.replace('_', ' ')} must be a day 1–31")
                v = str(int(v))
            elif k == "salary_amount_agorot":
                if not v.isdigit() or int(v) <= 0 or int(v) % 100 != 0:
                    raise ValueError("salary must be a positive whole number"
                                     " of shekels")
                v = str(int(v))
            elif k == "default_payment_method":
                if v not in ("card", "cash", "transfer"):
                    raise ValueError("payment method must be card, cash or transfer")
            clean[k] = v
        with self._lock:
            for k, v in clean.items():
                db.set_setting(self.conn, k, v)
        return {}

    @_safe
    def remove_category_budget(self, category_id: int):
        with self._lock:
            db.delete_budget(self.conn, int(category_id))
        return {}

    @_safe
    def set_category_budget(self, category_id: int, amount_ils):
        with self._lock:
            try:
                amount = to_agorot(amount_ils)
            except ValueError:
                raise ValueError("enter the budget as a number of shekels")
            if amount <= 0:
                raise ValueError("budget must be positive")
            db.set_budget(self.conn, int(category_id), amount)
        return {}

    @_safe
    def onboarding_braindump(self, text: str, profile: dict | None = None):
        return {"proposal": advisor.onboarding_propose(self.conn, text,
                                                       self._today(),
                                                       profile=profile)}

    @_safe
    def onboarding_complete(self, profile: dict, proposal: dict):
        from app.models import ParsedTxn
        # Validate everything BEFORE any write — db helpers autocommit, so
        # failing early is the only way to keep onboarding all-or-nothing.
        profile_clean = {}
        for k in ONBOARD_KEYS:
            if k not in profile:
                continue
            v = str(profile[k]).strip()
            if k in ("salary_day", "card_charge_day"):
                if not v.isdigit() or not 1 <= int(v) <= 31:
                    raise ValueError(f"{k} must be an integer 1..31")
                v = str(int(v))
            elif k == "salary_amount_agorot":
                if not v.isdigit() or int(v) <= 0:
                    raise ValueError("salary_amount_agorot must be a positive"
                                     " integer (agorot = shekels x 100)")
                v = str(int(v))
            profile_clean[k] = v
        # First Flight amounts are whole shekels only: reject fractions and
        # zeros (the opening balance may be 0 — "I'm broke" is valid).
        ob_agorot = to_whole_agorot(proposal.get("opening_balance_ils") or 0,
                                    allow_zero=True, label="opening balance")
        txns = []
        for t in proposal.get("transactions", []):
            desc = (t or {}).get("description") or (t or {}).get("category") \
                or "transaction"
            to_whole_agorot((t or {}).get("amount"), label=f"the '{desc}' amount")
            txns.append(ParsedTxn(**t))
        budgets = []
        for name, ils in (proposal.get("suggested_budgets") or {}).items():
            agorot = to_whole_agorot(ils, label=f"the {name} budget")
            cid = db.category_id_by_name(self.conn, str(name))
            if cid:
                budgets.append((cid, agorot))
        with self._lock:
            for k, v in profile_clean.items():
                db.set_setting(self.conn, k, v)
            db.set_setting(self.conn, "opening_balance_agorot", ob_agorot)
            db.set_setting(self.conn, "opening_balance_date",
                           self._today().isoformat())
            for p in txns:
                parser._store(self.conn, p, raw_text="(onboarding)",
                              source="onboarding")
            for cid, agorot in budgets:
                db.set_budget(self.conn, cid, agorot)
        return {}

    # --- export ----------------------------------------------------------------------

    @_safe
    def export_csv(self, month: str, out_dir=None):
        if not re.fullmatch(r"\d{4}-\d{2}", str(month or "")):
            raise ValueError("month must look like YYYY-MM")
        y, m = int(month[:4]), int(month[5:7])
        if not 1 <= m <= 12:
            raise ValueError("month must look like YYYY-MM")
        start = dt.date(y, m, 1)
        end = (dt.date(y + 1, 1, 1) if m == 12
               else dt.date(y, m + 1, 1)) - dt.timedelta(days=1)
        rows = db.list_transactions(self.conn, start=start, end=end)
        out_dir = (Path(out_dir) if out_dir is not None
                   else Path(__file__).resolve().parent.parent / "exports")
        out_dir.mkdir(exist_ok=True)
        path = out_dir / f"moneypilot-{month}.csv"
        with open(path, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.writer(f)
            w.writerow(["date", "amount_ils", "direction", "category",
                        "description", "people", "method", "source"])
            for r in rows:
                w.writerow([r["effective_date"], f"{r['amount_agorot']/100:.2f}",
                            r["direction"], r["category_name"] or "",
                            r["description"], r["people"] or "",
                            r["payment_method"], r["source"]])
        return {"path": str(path)}
