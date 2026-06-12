/* MoneyPilot UI core. Later tasks APPEND to this file:
   renderers.<name> render functions and the onboarding flow. */
"use strict";

const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => document.querySelectorAll(sel);
const ready = new Promise((res) => window.addEventListener("pywebviewready", res));

// Fix 4: module-level store for pending advisor action card
let pendingActionEl = null;

// Fix 5: module-level briefing cache
let briefingText = null;

async function api(method, ...args) {
  await ready;
  return window.pywebview.api[method](...args);
}

const renderers = {};            // tab renderers, registered by later tasks
async function refreshAll() {
  for (const fn of Object.values(renderers)) await fn();
}

function toast(msg, action) {            // action: {label, fn} optional
  const t = $("#toast");
  t.textContent = msg;
  if (action) {
    const b = document.createElement("button");
    b.className = "toast-btn";
    b.textContent = action.label;
    b.onclick = () => { action.fn(); t.classList.add("hidden"); };
    t.appendChild(b);
  }
  t.classList.remove("hidden");
  clearTimeout(toast._h);
  toast._h = setTimeout(() => t.classList.add("hidden"), 5000);
}

function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g,
    (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;",
              "'": "&#39;" }[c]));
}

// dim gauge glyph + one quiet line, for zero-row renders (presentational)
function emptyState(msg) {
  return `<div class="empty-state">
    <svg viewBox="0 0 24 24" width="36" height="36" aria-hidden="true">
      <path d="M6.34 18.16 A8 8 0 1 1 17.66 18.16" fill="none"
            stroke="currentColor" stroke-width="2" stroke-linecap="round"/>
      <line x1="12" y1="12.5" x2="15.57" y2="7.93" stroke="currentColor"
            stroke-width="2" stroke-linecap="round"/>
      <circle cx="12" cy="12.5" r="1.5" fill="currentColor"/>
    </svg>
    <span>${esc(msg)}</span></div>`;
}

/* --- tabs ------------------------------------------------------------- */
function initTabs() {
  document.querySelectorAll(".tab").forEach((b) =>
    b.addEventListener("click", () => {
      document.querySelectorAll(".tab").forEach((x) =>
        x.classList.toggle("active", x === b));
      document.querySelectorAll(".tabpane").forEach((p) =>
        p.classList.toggle("hidden", p.id !== "tab-" + b.dataset.tab));
    }));
}

/* --- entry bar + chips -------------------------------------------------- */
function addChip(text, cls) {
  const c = document.createElement("span");
  c.className = "chip " + cls;
  c.textContent = text;
  $("#chips").appendChild(c);
  return c;
}

function addUndo(chip, txnId) {
  const b = document.createElement("button");
  b.textContent = "undo";
  b.onclick = async () => {
    const r = await api("undo_txn", txnId);
    if (!r.ok) { toast(r.error); return; }
    chip.remove();
    refreshAll();
  };
  chip.appendChild(b);
}

async function submitEntry() {
  const input = $("#entry-input");
  const text = input.value.trim();
  if (!text) return;
  input.value = "";
  const pending = addChip("parsing…", "pending");
  const res = await api("add_entry", text);
  pending.remove();
  if (!res.ok) { toast(res.error); input.value = text; return; }
  for (const e of res.entries) {
    const cls = e.needs_review ? "review" : "ok";
    const icon = e.needs_review ? "⚠" : "✓";
    const chip = addChip(
      `${icon} ${e.category_name ?? "?"} · ${e.amount_fmt} · ${e.description}`,
      cls);
    addUndo(chip, e.id);
    setTimeout(() => chip.remove(), 20000);
  }
  if (res.source === "fallback")
    toast("AI offline — logged with my best guess, flagged for review.");
  refreshAll();
}

/* --- boot --------------------------------------------------------------- */
(async function boot() {
  initTabs();
  $("#entry-input").addEventListener("keydown",
    (e) => { if (e.key === "Enter") submitEntry(); });
  const st = await api("startup");
  if (!st.ok) { toast(st.error); return; }
  if (!st.onboarded) {
    if (typeof window.startOnboarding === "function") window.startOnboarding();
    else toast("Onboarding UI not built yet (Task 19).");
    return;
  }
  await refreshAll();
})();

/* --- OVERVIEW ------------------------------------------------------------ */
renderers.overview = async function renderOverview() {
  const o = await api("get_overview");
  if (!o.ok) { toast(o.error); return; }

  $("#ov-sts").textContent = o.safe_to_spend.today_fmt;
  $("#ov-sts-sub").textContent =
    `${o.safe_to_spend.remaining_fmt} left · ${o.safe_to_spend.days_left} days to salary`;
  const pct = Math.min(100,
    Math.round(100 * o.cycle.day_index / o.cycle.length));
  $("#ov-gauge").style.width = pct + "%";
  $("#ov-cycle-sub").textContent =
    `cycle day ${o.cycle.day_index} of ${o.cycle.length}`;
  $("#cycle-info").textContent =
    `CYCLE ${o.cycle.start} → ${o.cycle.end}`;

  $("#ov-cats").innerHTML = o.categories
    .filter((c) => !c.is_fixed)
    .map((c) => {
      const unbudgeted = c.pace_ratio === null;
      const used = c.budget_agorot ?
        Math.min(100, Math.round(100 * c.spent_agorot / c.budget_agorot)) : 0;
      const over = c.pace_ratio > 1.1 ? " over" : "";
      const ubClass = unbudgeted ? " unbudgeted" : "";
      const amtText = unbudgeted
        ? `₪${Math.round(c.spent_agorot / 100)} · unbudgeted`
        : `₪${Math.round(c.spent_agorot / 100)} / ₪${Math.round(c.budget_agorot / 100)}`;
      return `<div class="catrow${over}${ubClass}">
        <div class="meta"><span>${esc(c.emoji)} ${esc(c.name)}</span>
        <span>${amtText}</span></div>
        <div class="bar"><div class="fill" style="width:${used}%"></div></div>
      </div>`;
    }).join("");

  $("#ov-card").textContent = o.card.total_fmt;
  $("#ov-card-sub").textContent =
    `charges in ${o.card.days_to_charge}d (${o.card.charge_date})`;
  $("#ov-balance").innerHTML =
    `available ${esc(o.balance.available_fmt)}<br>` +
    `earmarked ₪${Math.round(o.balance.earmarked_agorot / 100)} · ` +
    `total ${esc(o.balance.total_fmt)}`;

  $("#ov-goals").innerHTML = o.goals.map((g) =>
    `<div class="catrow"><div class="meta">
      <span>${esc(g.emoji)} ${esc(g.name)}</span><span>${g.pct}%</span></div>
      <div class="bar"><div class="fill" style="width:${g.pct}%"></div></div>
    </div>`).join("") || `<span class="sub">no goals yet — Goals tab</span>`;

  $("#ov-recent").innerHTML = o.recent.map((r) =>
    `<div class="recent-row"><span>${esc(r.effective_date)} · ${
      esc(r.category_emoji ?? "")} ${esc(r.description)}</span>
     <span class="${r.amount_agorot < 0 ? "neg" : "pos"}">${
      esc(r.amount_fmt)}</span></div>`).join("");

  // Fix 5: only fetch briefing when cache is empty; render from cache otherwise
  if (briefingText === null) {
    const b = await api("get_briefing", false);
    briefingText = b.ok ? b.text : null;
  }
  $("#ov-briefing").textContent = briefingText ?? "briefing unavailable";
};

$("#ov-brief-refresh").addEventListener("click", async () => {
  $("#ov-briefing").textContent = "…";
  // Fix 5: force-refresh clears the cache then re-fetches
  briefingText = null;
  const b = await api("get_briefing", true);
  briefingText = b.ok ? b.text : null;
  $("#ov-briefing").textContent = briefingText ?? "briefing unavailable";
});

/* --- LEDGER ---------------------------------------------------------------- */
let lgCategories = [];

function lgFilters() {
  return { month: $("#lg-month").value || null,
           category_id: $("#lg-cat").value || null,
           text: $("#lg-text").value || null,
           needs_review: $("#lg-review").checked };
}

renderers.ledger = async function renderLedger() {
  const res = await api("list_ledger", lgFilters());
  if (!res.ok) { toast(res.error); return; }
  lgCategories = res.categories;
  const catSel = $("#lg-cat");
  if (catSel.options.length === 1)
    for (const c of res.categories)
      catSel.add(new Option(`${c.emoji} ${c.name}`, c.id));
  // Fix 2: expose category_id on the row so lgEditRow can preselect it
  $("#lg-body").innerHTML = res.rows.map((r) => `
    <tr data-id="${r.id}" data-cat-id="${r.category_id ?? ''}" class="${r.needs_review ? "review" : ""}">
      <td>${esc(r.effective_date)}</td>
      <td>${esc(r.amount_fmt)}</td>
      <td>${esc(r.category_emoji ?? "")} ${esc(r.category_name ?? "")}</td>
      <td>${esc(r.description)}${r.people ? " · " + esc(r.people) : ""}</td>
      <td>${esc(r.payment_method)}</td>
      <td><button class="rowbtn" data-act="edit">✎</button>
          <button class="rowbtn" data-act="del">🗑</button></td>
    </tr>`).join("") ||
    `<tr><td colspan="6">${emptyState("no entries yet — log one above")}</td></tr>`;
};

function lgEditRow(tr) {
  const id = Number(tr.dataset.id);
  const cells = tr.children;
  const cur = { date: cells[0].textContent,
                amount: cells[1].textContent.replace(/[₪,]/g, ""),
                desc: cells[3].textContent.split(" · ")[0] };
  // Fix 2: read the row's current category so the select is preselected
  const curCat = tr.dataset.catId;
  const catOpts = lgCategories.map((c) =>
    `<option value="${c.id}" ${String(c.id) === curCat ? "selected" : ""}>${esc(c.emoji)} ${esc(c.name)}</option>`).join("");
  tr.innerHTML = `
    <td><input type="date" value="${esc(cur.date)}"></td>
    <td><input type="number" step="0.01" value="${esc(cur.amount)}"></td>
    <td><select>${catOpts}</select></td>
    <td><input value="${esc(cur.desc)}"></td>
    <td></td>
    <td><button class="rowbtn" data-act="save">✔</button></td>`;
  tr.querySelector("[data-act=save]").onclick = async () => {
    const [d, a, c, t] = tr.querySelectorAll("input, select");
    if (!d.value) { toast("date required"); return; }
    const ils = parseFloat(a.value);
    if (!Number.isFinite(ils)) { toast("amount must be a number"); return; }
    const res = await api("update_txn", id, {
      effective_date: d.value,
      amount_agorot: Math.round(ils * 100),   // sign as displayed (− = expense)
      category_id: Number(c.value),
      description: t.value,
      needs_review: 0,
    });
    if (!res.ok) { toast(res.error); return; }
    toast("saved — category rule learned if you re-categorized");
    refreshAll();
  };
}

$("#lg-body").addEventListener("click", async (e) => {
  const btn = e.target.closest("button.rowbtn");
  if (!btn) return;
  const tr = btn.closest("tr");
  const id = Number(tr.dataset.id);
  if (btn.dataset.act === "del") {
    const r = await api("undo_txn", id);
    if (!r.ok) { toast(r.error); return; }
    toast("Deleted.", { label: "UNDO", fn: async () => {
      const rr = await api("restore_txn", id);
      if (!rr.ok) { toast(rr.error); return; }
      refreshAll();
    }});
    refreshAll();
  } else if (btn.dataset.act === "edit") {
    lgEditRow(tr);
  }
});

for (const id of ["lg-month", "lg-cat", "lg-text", "lg-review"])
  $("#" + id).addEventListener("change", () => renderers.ledger());

$("#lg-export").addEventListener("click", async () => {
  const now = new Date();
  const month = $("#lg-month").value ||
    `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}`;
  const res = await api("export_csv", month);
  toast(res.ok ? "exported: " + res.path : res.error);
});

/* --- GOALS ------------------------------------------------------------------ */
renderers.goals = async function renderGoals() {
  const res = await api("get_goals");
  if (!res.ok) { toast(res.error); return; }
  $("#gl-cards").innerHTML = res.goals.map((g) => {
    const verdictCls = g.verdict === "ready" ? "ready"
      : g.verdict === "behind" ? "behind" : "";
    const pct = Math.max(0, Math.min(100, Number(g.pct) || 0));
    const lines = [
      `${g.progress_fmt} / ${g.target_fmt}`,
      g.pace_needed_fmt ? `needs ${g.pace_needed_fmt}/mo` : null,
      g.projected_date ? `projected ${g.projected_date}` : null,
    ].filter(Boolean).join(" · ");
    return `<div class="panel goalcard ${verdictCls}" data-id="${g.id}">
      <div class="meta" style="display:flex;justify-content:space-between">
        <b>${esc(g.emoji)} ${esc(g.name)}</b>
        <button class="rowbtn" data-act="arch" title="archive">✕</button></div>
      <div class="ring" style="--p:${pct}"><span class="ring-pct">${pct}%</span></div>
      <div class="sub">${esc(lines)}</div>
      <div class="sub verdict ${verdictCls}">${esc(g.verdict)}</div>
    </div>`;
  }).join("") || emptyState("no active goals — chart one below");
};

$("#gl-cards").addEventListener("click", async (e) => {
  const btn = e.target.closest("[data-act=arch]");
  if (!btn) return;
  const r = await api("archive_goal", Number(btn.closest(".goalcard").dataset.id));
  if (!r.ok) { toast(r.error); return; }
  refreshAll();
});

$("#gl-save").addEventListener("click", async () => {
  const res = await api("save_goal", {
    name: $("#gl-name").value.trim(),
    goal_type: $("#gl-type").value,
    target_ils: parseFloat($("#gl-target").value),
    target_date: $("#gl-date").value || null,
  });
  if (!res.ok) { toast(res.error); return; }
  $("#gl-name").value = $("#gl-target").value = $("#gl-date").value = "";
  refreshAll();
});

/* --- ADVISOR -------------------------------------------------------------------- */
function chatBubble(role, text) {
  const es = $("#ch-thread .empty-state");
  if (es) es.remove();
  const div = document.createElement("div");
  div.className = "bubble " + role;
  div.textContent = text;
  $("#ch-thread").appendChild(div);
  $("#ch-thread").scrollTop = $("#ch-thread").scrollHeight;
  return div;
}

function chatActionCard(action) {
  const div = document.createElement("div");
  div.className = "actioncard";
  div.textContent = "⚡ proposed: " + JSON.stringify(action);
  const btn = document.createElement("button");
  btn.className = "btn primary";
  btn.textContent = "APPLY";
  btn.onclick = async () => {
    const res = await api("chat_apply_action", action);
    toast(res.ok ? res.summary : res.error);
    if (res.ok) {
      // Fix 4: clear the pending card reference when it is applied
      pendingActionEl = null;
      div.remove();
      refreshAll();
    }
  };
  div.appendChild(btn);
  // Fix 4: store a reference so refreshAll can re-attach it
  pendingActionEl = div;
  $("#ch-thread").appendChild(div);
  $("#ch-thread").scrollTop = $("#ch-thread").scrollHeight;
}

renderers.advisor = async function renderAdvisor() {
  const res = await api("get_chat_history");
  if (!res.ok) return;
  const thread = $("#ch-thread");
  thread.innerHTML = "";
  for (const m of res.messages) chatBubble(m.role, m.text);
  // Fix 4: re-attach the pending action card after rebuilding the thread
  if (pendingActionEl) thread.appendChild(pendingActionEl);
  if (!thread.childElementCount)
    thread.innerHTML = emptyState("no transmissions yet — ask about your money");
};

async function chatSend() {
  const input = $("#ch-input");
  const text = input.value.trim();
  if (!text) return;
  input.value = "";
  chatBubble("user", text);
  const thinking = chatBubble("assistant", "…");
  const res = await api("chat_send", text);
  thinking.remove();
  if (!res.ok) { toast(res.error); return; }
  chatBubble("assistant", res.text);
  if (res.offline) toast("advisor offline — numbers on Overview are still live");
  if (res.action) chatActionCard(res.action);
}

$("#ch-send").addEventListener("click", chatSend);
$("#ch-input").addEventListener("keydown",
  (e) => { if (e.key === "Enter") chatSend(); });

/* --- ONBOARDING -------------------------------------------------------------------- */
window.startOnboarding = function startOnboarding() {
  $("#onboarding").classList.remove("hidden");
  let step = 0;
  let proposal = null;

  function show(n) {
    step = n;
    document.querySelectorAll(".ob-step").forEach((s) =>
      s.classList.toggle("hidden", Number(s.dataset.step) !== n));
    $("#ob-next").textContent = n === 4 ? "CONFIRM ✓" : "NEXT ▸";
  }

  function renderProposal(p) {
    const rows = [
      // Fix 1: coerce to Number so an LLM-produced string cannot break out of the attribute
      `<div class="prow">opening balance ₪
        <input id="obp-balance" value="${Number(p.opening_balance_ils) || 0}"></div>`,
      `<div class="prow"><b>month so far:</b></div>`,
      ...(p.transactions || []).map((t, i) =>
        // Fix 1: coerce t.amount
        `<div class="prow">${esc(t.effective_date)} · ${esc(t.category)} ·
          ${esc(t.description)} ₪<input data-pi="${i}" value="${Number(t.amount) || 0}"></div>`),
      `<div class="prow"><b>suggested budgets (₪/mo):</b></div>`,
      ...Object.entries(p.suggested_budgets || {}).map(([name, ils]) =>
        // Fix 1: coerce ils
        `<div class="prow">${esc(name)} ₪
          <input data-pb="${esc(name)}" value="${Number(ils) || 0}"></div>`),
    ];
    $("#ob-proposal").innerHTML = rows.join("");
  }

  $("#ob-next").onclick = async () => {
    if (step === 0 && !$("#ob-name").value.trim()) return;
    if (step < 3) { show(step + 1); return; }
    if (step === 3) {
      $("#ob-status").textContent = "Claude is reading your dump…";
      const res = await api("onboarding_braindump", $("#ob-dump").value, {
        salary_amount_agorot: String(
          Math.round((parseFloat($("#ob-salary").value) || 0) * 100)),
        salary_day: $("#ob-salary-day").value || "1",
      });
      $("#ob-status").textContent = "";
      if (!res.ok) {
        toast("AI unreachable — starting with a blank slate. " + res.error);
        proposal = { opening_balance_ils: 0, transactions: [],
                     suggested_budgets: {} };
      } else {
        proposal = res.proposal;
      }
      renderProposal(proposal);
      show(4);
      return;
    }
    // step 4 → confirm
    // Fix 3: build fresh copies each attempt so proposal is never mutated;
    // a server-side failure followed by a retry no longer crashes.
    const opening = parseFloat($("#obp-balance").value) || 0;
    const txns = [];
    $$("#ob-proposal [data-pi]").forEach((inp) => {
      const t = { ...proposal.transactions[Number(inp.dataset.pi)] };
      t.amount = parseFloat(inp.value) || 0;
      if (t.amount > 0) txns.push(t);
    });
    const budgets = {};
    $$("#ob-proposal [data-pb]").forEach((inp) => {
      budgets[inp.dataset.pb] = parseFloat(inp.value) || 0;
    });
    const res = await api("onboarding_complete", {
      user_name: $("#ob-name").value.trim(),
      salary_day: $("#ob-salary-day").value || "1",
      salary_amount_agorot: String(
        Math.round((parseFloat($("#ob-salary").value) || 0) * 100)),
      card_charge_day: $("#ob-card-day").value || "1",
    }, { ...proposal, opening_balance_ils: opening,
         transactions: txns, suggested_budgets: budgets });
    if (!res.ok) { toast(res.error); return; }
    $("#onboarding").classList.add("hidden");
    await refreshAll();
  };

  show(0);
};
