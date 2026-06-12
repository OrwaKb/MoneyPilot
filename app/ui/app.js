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
