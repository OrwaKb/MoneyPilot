/* MoneyPilot UI core. Later tasks APPEND to this file:
   renderers.<name> render functions and the onboarding flow. */
"use strict";

const $ = (sel) => document.querySelector(sel);
const ready = new Promise((res) => window.addEventListener("pywebviewready", res));

async function api(method, ...args) {
  await ready;
  return window.pywebview.api[method](...args);
}

const renderers = {};            // tab renderers, registered by later tasks
async function refreshAll() {
  for (const fn of Object.values(renderers)) await fn();
}

function toast(msg) {
  const t = $("#toast");
  t.textContent = msg;
  t.classList.remove("hidden");
  clearTimeout(toast._h);
  toast._h = setTimeout(() => t.classList.add("hidden"), 5000);
}

function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g,
    (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;",
              "'": "&#39;" }[c]));
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
    await api("undo_txn", txnId);
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

  const b = await api("get_briefing", false);
  $("#ov-briefing").textContent = b.ok ? b.text : "briefing unavailable";
};

$("#ov-brief-refresh").addEventListener("click", async () => {
  $("#ov-briefing").textContent = "…";
  const b = await api("get_briefing", true);
  $("#ov-briefing").textContent = b.ok ? b.text : "briefing unavailable";
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
  $("#lg-body").innerHTML = res.rows.map((r) => `
    <tr data-id="${r.id}" class="${r.needs_review ? "review" : ""}">
      <td>${esc(r.effective_date)}</td>
      <td>${esc(r.amount_fmt)}</td>
      <td>${esc(r.category_emoji ?? "")} ${esc(r.category_name ?? "")}</td>
      <td>${esc(r.description)}${r.people ? " · " + esc(r.people) : ""}</td>
      <td>${esc(r.payment_method)}</td>
      <td><button class="rowbtn" data-act="edit">✎</button>
          <button class="rowbtn" data-act="del">🗑</button></td>
    </tr>`).join("");
};

function lgEditRow(tr) {
  const id = Number(tr.dataset.id);
  const cells = tr.children;
  const cur = { date: cells[0].textContent,
                amount: cells[1].textContent.replace(/[₪,]/g, ""),
                desc: cells[3].textContent.split(" · ")[0] };
  const catOpts = lgCategories.map((c) =>
    `<option value="${c.id}">${esc(c.emoji)} ${esc(c.name)}</option>`).join("");
  tr.innerHTML = `
    <td><input type="date" value="${esc(cur.date)}"></td>
    <td><input type="number" step="0.01" value="${esc(cur.amount)}"></td>
    <td><select>${catOpts}</select></td>
    <td><input value="${esc(cur.desc)}"></td>
    <td></td>
    <td><button class="rowbtn" data-act="save">✔</button></td>`;
  tr.querySelector("[data-act=save]").onclick = async () => {
    const [d, a, c, t] = tr.querySelectorAll("input, select");
    const ils = parseFloat(a.value);
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
    await api("undo_txn", id);
    toast("deleted (soft) — restore from a fresh entry chip or DB if needed");
    refreshAll();
  } else if (btn.dataset.act === "edit") {
    lgEditRow(tr);
  }
});

for (const id of ["lg-month", "lg-cat", "lg-text", "lg-review"])
  $("#" + id).addEventListener("change", () => renderers.ledger());

$("#lg-export").addEventListener("click", async () => {
  const month = $("#lg-month").value ||
    new Date().toISOString().slice(0, 7);
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
    const lines = [
      `${g.progress_fmt} / ${g.target_fmt}`,
      g.pace_needed_fmt ? `needs ${g.pace_needed_fmt}/mo` : null,
      g.projected_date ? `projected ${g.projected_date}` : null,
    ].filter(Boolean).join(" · ");
    return `<div class="panel goalcard" data-id="${g.id}">
      <div class="meta" style="display:flex;justify-content:space-between">
        <b>${esc(g.emoji)} ${esc(g.name)}</b>
        <button class="rowbtn" data-act="arch" title="archive">✕</button></div>
      <div class="bar"><div class="fill" style="width:${g.pct}%"></div></div>
      <div class="sub">${esc(lines)}</div>
      <div class="sub verdict ${verdictCls}">${esc(g.verdict)} · ${g.pct}%</div>
    </div>`;
  }).join("") || `<span class="sub">no active goals</span>`;
};

$("#gl-cards").addEventListener("click", async (e) => {
  const btn = e.target.closest("[data-act=arch]");
  if (!btn) return;
  await api("archive_goal", Number(btn.closest(".goalcard").dataset.id));
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
    if (res.ok) { div.remove(); refreshAll(); }
  };
  div.appendChild(btn);
  $("#ch-thread").appendChild(div);
  $("#ch-thread").scrollTop = $("#ch-thread").scrollHeight;
}

renderers.advisor = async function renderAdvisor() {
  const res = await api("get_chat_history");
  if (!res.ok) return;
  $("#ch-thread").innerHTML = "";
  for (const m of res.messages) chatBubble(m.role, m.text);
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
      `<div class="prow">opening balance ₪
        <input id="obp-balance" value="${p.opening_balance_ils ?? 0}"></div>`,
      `<div class="prow"><b>month so far:</b></div>`,
      ...(p.transactions || []).map((t, i) =>
        `<div class="prow">${esc(t.effective_date)} · ${esc(t.category)} ·
          ${esc(t.description)} ₪<input data-pi="${i}" value="${t.amount}"></div>`),
      `<div class="prow"><b>suggested budgets (₪/mo):</b></div>`,
      ...Object.entries(p.suggested_budgets || {}).map(([name, ils]) =>
        `<div class="prow">${esc(name)} ₪
          <input data-pb="${esc(name)}" value="${ils}"></div>`),
    ];
    $("#ob-proposal").innerHTML = rows.join("");
  }

  $("#ob-next").onclick = async () => {
    if (step === 0 && !$("#ob-name").value.trim()) return;
    if (step < 3) { show(step + 1); return; }
    if (step === 3) {
      $("#ob-status").textContent = "Claude is reading your dump…";
      const res = await api("onboarding_braindump", $("#ob-dump").value);
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
    proposal.opening_balance_ils = parseFloat($("#obp-balance").value) || 0;
    document.querySelectorAll("[data-pi]").forEach((inp) => {
      proposal.transactions[Number(inp.dataset.pi)].amount =
        parseFloat(inp.value) || 0;
    });
    proposal.transactions = proposal.transactions.filter((t) => t.amount > 0);
    const budgets = {};
    document.querySelectorAll("[data-pb]").forEach((inp) => {
      budgets[inp.dataset.pb] = parseFloat(inp.value) || 0;
    });
    proposal.suggested_budgets = budgets;
    const res = await api("onboarding_complete", {
      user_name: $("#ob-name").value.trim(),
      salary_day: $("#ob-salary-day").value || "1",
      salary_amount_agorot: String(
        Math.round((parseFloat($("#ob-salary").value) || 0) * 100)),
      card_charge_day: $("#ob-card-day").value || "1",
    }, proposal);
    if (!res.ok) { toast(res.error); return; }
    $("#onboarding").classList.add("hidden");
    await refreshAll();
  };

  show(0);
};
