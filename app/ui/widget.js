const $ = (s) => document.querySelector(s);

const ready = new Promise((resolve) => {
  if (window.pywebview && window.pywebview.api) resolve();
  else window.addEventListener("pywebviewready", resolve);
});
function api(method, ...args) {
  return ready.then(() => window.pywebview.api[method](...args));
}

function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g,
    (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;",
              "'": "&#39;" }[c]));
}

let pinned = true;

function showSetup() {
  $("#w-setup").classList.remove("hidden");
  $("#w-main").classList.add("hidden");
}
function hideSetup() {
  $("#w-setup").classList.add("hidden");
  $("#w-main").classList.remove("hidden");
}
function setError(on) { $("#w-err").classList.toggle("hidden", !on); }

function render(ov) {
  const sts = ov.safe_to_spend, cyc = ov.cycle, card = ov.card, bal = ov.balance;
  $("#w-hero").textContent = sts.today_fmt;
  // arc fills with cycle progress: (length - days_left) / length
  const frac = cyc.length
    ? Math.min(1, Math.max(0, (cyc.length - sts.days_left) / cyc.length)) : 0;
  $("#w-gauge").style.strokeDashoffset = String(100 - frac * 100);
  $("#w-day").textContent = "DAY " + (cyc.day_index ?? "—");
  $("#w-balance").textContent = bal.available_fmt;
  $("#w-card").textContent = card.total_fmt + " · " + card.days_to_charge + "d";
}

async function refresh() {
  const ob = await api("is_onboarded");
  if (ob && ob.onboarded === false) { showSetup(); return; }
  const ov = await api("get_overview");
  if (!ov || ov.ok === false) { setError(true); return; }  // keep last-known
  setError(false);
  hideSetup();
  render(ov);
}

function chip(entry, offline) {
  const box = $("#w-chip");
  box.innerHTML = "";
  box.classList.toggle("offline", !!offline);
  const span = document.createElement("span");
  const tail = offline ? " · offline" : "";
  span.textContent = `${entry.category_emoji || "•"} `
    + `${entry.description || entry.category_name || ""} `
    + `${entry.amount_fmt}${tail}`;
  const undo = document.createElement("button");
  undo.textContent = "undo";
  undo.onclick = async () => {
    await api("undo_txn", Number(entry.id));
    box.classList.add("hidden");
    refresh();
  };
  box.appendChild(span);
  box.appendChild(undo);
  box.classList.remove("hidden");
  clearTimeout(chip._h);
  chip._h = setTimeout(() => box.classList.add("hidden"), 3000);
}

async function quickAdd() {
  const inp = $("#w-add");
  const text = inp.value.trim();
  if (!text) return;
  inp.value = "";
  const res = await api("add_entry", text);
  if (!res || res.ok === false || !res.entries || !res.entries.length) {
    setError(true); return;
  }
  chip(res.entries[0], res.used_ai === false);
  refresh();
}

$("#w-add").addEventListener("keydown",
  (e) => { if (e.key === "Enter") quickAdd(); });
$("#w-close").addEventListener("click", () => api("close"));
$("#w-setup-btn").addEventListener("click", () => api("open_main_app"));
$(".w-brand").addEventListener("dblclick", () => api("open_main_app"));
$("#w-pin").addEventListener("click", async () => {
  pinned = !pinned;
  $("#w-pin").classList.toggle("on", pinned);
  await api("set_pin", pinned);
});

ready.then(() => {
  $("#w-pin").classList.add("on");          // starts pinned
  refresh();
  setInterval(refresh, 30000);              // poll every 30s
  window.addEventListener("focus", refresh);
});
