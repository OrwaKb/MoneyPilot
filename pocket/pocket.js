"use strict";
/* MoneyPilot Pocket — offline capture + sync to the home desktop.
 * Entries live in IndexedDB on the phone and work with zero signal; when a home
 * address+token is paired (Settings) and reachable, unsynced entries POST to
 * {url}/pocket/sync and are marked synced from the response. */

const DB_NAME = "moneypilot-pocket";
const STORE = "entries";

/* ---- tiny IndexedDB wrapper ------------------------------------------------ */
function openDB() {
  return new Promise((resolve, reject) => {
    const req = indexedDB.open(DB_NAME, 1);
    req.onupgradeneeded = () => req.result.createObjectStore(STORE, { keyPath: "uuid" });
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(req.error);
  });
}
async function idb(mode, fn) {
  const db = await openDB();
  return new Promise((resolve, reject) => {
    const tx = db.transaction(STORE, mode);
    const store = tx.objectStore(STORE);
    const out = fn(store);
    tx.oncomplete = () => resolve(out && out.result !== undefined ? out.result : out);
    tx.onerror = () => reject(tx.error);
  });
}
const putEntry = (e) => idb("readwrite", (s) => s.put(e));
const delEntry = (uuid) => idb("readwrite", (s) => s.delete(uuid));
function allEntries() {
  return idb("readonly", (s) => s.getAll()).then((r) =>
    (r || []).sort((a, b) => (a.created_at < b.created_at ? 1 : -1)));
}
async function markSynced(uuids) {
  const set = new Set(uuids);
  const all = await allEntries();
  await Promise.all(all.filter((e) => set.has(e.uuid) && !e.synced)
    .map((e) => putEntry({ ...e, synced: true })));
}

/* ---- config (home address + token) ---------------------------------------- */
const cfg = {
  get url() { return localStorage.getItem("mp_url") || ""; },
  get token() { return localStorage.getItem("mp_token") || ""; },
  set(url, token) { localStorage.setItem("mp_url", url); localStorage.setItem("mp_token", token); },
};

/* ---- helpers --------------------------------------------------------------- */
const $ = (id) => document.getElementById(id);
function guessAmount(text) {
  const m = String(text).replace(/[,₪$]/g, "").match(/\d+(\.\d+)?/);
  return m ? parseFloat(m[0]) : 0;
}
function fmt(n) {
  const v = Math.round(n * 100) / 100;
  return "₪" + (Number.isInteger(v) ? v : v.toFixed(2));
}
function isToday(iso) {
  const d = new Date(iso), n = new Date();
  return d.getFullYear() === n.getFullYear() && d.getMonth() === n.getMonth()
    && d.getDate() === n.getDate();
}
function timeLabel(iso) {
  const d = new Date(iso);
  return d.toLocaleString([], { hour: "2-digit", minute: "2-digit", month: "short", day: "numeric" });
}
let toastTimer;
function toast(msg) {
  const t = $("toast"); t.textContent = msg; t.classList.remove("hidden");
  clearTimeout(toastTimer); toastTimer = setTimeout(() => t.classList.add("hidden"), 2200);
}
function esc(s) {
  const d = document.createElement("div"); d.textContent = s; return d.innerHTML;
}

/* ---- render ---------------------------------------------------------------- */
async function render() {
  const entries = await allEntries();
  const today = entries.filter((e) => isToday(e.created_at))
    .reduce((s, e) => s + (e.amount_guess || 0), 0);
  $("today-total").textContent = fmt(today);
  $("empty").style.display = entries.length ? "none" : "block";

  $("list").innerHTML = entries.map((e) => `
    <li class="item ${e.synced ? "is-synced" : ""}" data-uuid="${esc(e.uuid)}">
      <span class="txt">
        <span class="desc">${esc(e.raw_text)}</span>
        <span class="meta">${esc(timeLabel(e.created_at))}</span>
      </span>
      <span class="amt">${e.amount_guess ? fmt(e.amount_guess) : ""}</span>
      <span class="state ${e.synced ? "synced" : "wait"}">${e.synced ? "✓" : "⏳"}</span>
      <button class="del" data-del="${esc(e.uuid)}" aria-label="delete">✕</button>
    </li>`).join("");

  const waiting = entries.filter((e) => !e.synced).length;
  const badge = $("sync-badge");
  if (!cfg.url) { badge.textContent = "not paired"; badge.className = "sync-badge off"; }
  else if (waiting === 0) { badge.textContent = "✓ synced"; badge.className = "sync-badge ok"; }
  else { badge.textContent = `⏳ ${waiting} waiting`; badge.className = "sync-badge wait"; }
}

/* ---- add + sync ------------------------------------------------------------ */
async function addEntry(rawText) {
  const text = rawText.trim();
  if (!text) return;
  const entry = {
    uuid: (crypto.randomUUID ? crypto.randomUUID()
      : String(Date.now()) + Math.random().toString(16).slice(2)),
    raw_text: text,
    amount_guess: guessAmount(text),
    created_at: new Date().toISOString(),
    synced: false,
  };
  await putEntry(entry);
  await render();
  sync();
}

let syncing = false;
async function sync() {
  if (syncing) return;
  if (!cfg.url || !navigator.onLine) { return; }
  const pending = (await allEntries()).filter((e) => !e.synced);
  if (!pending.length) return;
  syncing = true;
  try {
    const res = await fetch(cfg.url.replace(/\/$/, "") + "/pocket/sync", {
      method: "POST",
      headers: { "Content-Type": "application/json", "Authorization": "Bearer " + cfg.token },
      body: JSON.stringify({
        entries: pending.map((e) => ({
          uuid: e.uuid, raw_text: e.raw_text,
          amount_guess: e.amount_guess, created_at: e.created_at,
        })),
      }),
    });
    if (!res.ok) throw new Error("HTTP " + res.status);
    const data = await res.json();
    await markSynced(data.synced || []);
    await render();
    if ((data.synced || []).length) toast("Synced to home ✓");
  } catch (err) {
    // offline / PC asleep / not paired yet — entries stay queued, try again later
  } finally {
    syncing = false;
  }
}

/* ---- events ---------------------------------------------------------------- */
$("add-form").addEventListener("submit", (e) => {
  e.preventDefault();
  const input = $("add-input");
  addEntry(input.value);
  input.value = "";
  input.focus();
});

$("list").addEventListener("click", async (e) => {
  const uuid = e.target?.dataset?.del;
  if (!uuid) return;
  await delEntry(uuid);
  await render();
});

$("open-settings").addEventListener("click", () => {
  $("cfg-url").value = cfg.url;
  $("cfg-token").value = cfg.token;
  $("settings").showModal();
});
$("settings").addEventListener("close", () => {
  if ($("settings").returnValue === "save") {
    cfg.set($("cfg-url").value.trim(), $("cfg-token").value.trim());
    toast(cfg.url ? "Paired — will sync when home" : "Cleared");
    render(); sync();
  }
});

window.addEventListener("online", sync);
document.addEventListener("visibilitychange", () => { if (!document.hidden) sync(); });

if ("serviceWorker" in navigator) {
  window.addEventListener("load", () => navigator.serviceWorker.register("sw.js").catch(() => {}));
}

render().then(sync);
