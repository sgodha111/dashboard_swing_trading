const $ = (sel) => document.querySelector(sel);
let settings = null;
let status = "open";
let trades = [];

const SIGNAL_ORDER = { exit: 0, action: 1, watch: 2, hold: 3 };
const SIGNAL_LABEL = { exit: "EXIT", action: "ACTION", watch: "WATCH", hold: "HOLD" };

// ---------------------------------------------------------------- helpers
async function api(path, opts = {}) {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...opts,
    body: opts.body ? JSON.stringify(opts.body) : undefined,
  });
  if (!res.ok) {
    let msg = res.statusText;
    try {
      const j = await res.json();
      msg = typeof j.detail === "string" ? j.detail : j.detail.map((d) => `${d.loc.at(-1)}: ${d.msg}`).join(", ");
    } catch (_) {}
    throw new Error(msg);
  }
  return res.json();
}

function toast(msg, err = false) {
  const t = $("#toast");
  t.textContent = msg;
  t.className = "toast" + (err ? " err" : "");
  clearTimeout(t._h);
  t._h = setTimeout(() => t.classList.add("hidden"), 3500);
}

const cur = () => settings?.currency ?? "";
// Indian digit grouping (1,00,000) when trading in rupees
const locale = () => (settings?.currency === "₹" ? "en-IN" : undefined);
const num = (v, d = 2) => (v === null || v === undefined || Number.isNaN(v) ? "—" : Number(v).toLocaleString(locale(), { minimumFractionDigits: d, maximumFractionDigits: d }));
const money = (v, d = 2) => (v === null || v === undefined ? "—" : `${v < 0 ? "-" : ""}${cur()}${num(Math.abs(v), d)}`);
const signed = (v, fmt) => (v === null || v === undefined ? "—" : `<span class="${v >= 0 ? "pos" : "neg"}">${v > 0 ? "+" : ""}${fmt(v)}</span>`);
const pct = (v) => signed(v, (x) => `${num(x, 1)}%`);
const rmult = (v) => signed(v, (x) => `${num(x, 2)}R`);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);

function formData(form) {
  const out = {};
  for (const [k, v] of new FormData(form).entries()) {
    if (v === "") continue;
    out[k] = form.elements[k].type === "number" ? Number(v) : v;
  }
  return out;
}

// ---------------------------------------------------------------- settings & rules
async function loadSettings() {
  settings = await api("/api/settings");
  const s = settings;
  $("#rulesList").innerHTML = [
    `Risk <b>${s.risk_pct}%</b> of your equity per trade (starting capital ${money(s.account_size, 0)} + realised profit/loss). Shares = max loss ÷ (entry − stop), capped by the <b>cash still available</b> — money in open trades is subtracted.`,
    `Initial stop: your own (below the recent swing low) — or, if left blank, entry − <b>${s.atr_multiple}× ATR(14)</b>, falling back to <b>${s.default_stop_pct}%</b> below entry.`,
    `1R = entry − initial stop. <b>Target 1 = +${s.target1_rr}R</b> (sell ~half), <b>Target 2 = +${s.target2_rr}R</b> (exit the rest). "Planned profit" = half sold at Target 1 + half at Target 2. You can also set your own target price per trade.`,
    `After a close at <b>+${s.breakeven_r}R</b>, move the stop to breakeven. After Target 1, trail the stop at highest close − ${s.atr_multiple}× ATR.`,
    `Trend filter: price &gt; SMA50 &gt; SMA200. Exit if price closes clearly below the SMA50 (more than 0.5 ATR); a warning is shown when just below.`,
    `Time stop: after <b>${s.max_hold_days} days</b> without reaching +${s.breakeven_r}R, consider freeing the capital. Max <b>${s.max_open_positions}</b> open positions.`,
    s.exchange_suffix ? `Symbols without a suffix get <b>${esc(s.exchange_suffix)}</b> added (e.g. RELIANCE → RELIANCE${esc(s.exchange_suffix)}). Change this in Settings.` : `Enter full Yahoo symbols (e.g. RELIANCE.NS, AAPL).`,
  ].map((x) => `<li>${x}</li>`).join("");
}

$("#settingsBtn").onclick = () => {
  const f = $("#settingsForm");
  for (const [k, v] of Object.entries(settings)) if (f.elements[k]) f.elements[k].value = v;
  $("#settingsDlg").showModal();
};
$("#settingsDlg").addEventListener("close", async () => {
  if ($("#settingsDlg").returnValue !== "save") return;
  try {
    await api("/api/settings", { method: "PUT", body: formData($("#settingsForm")) });
    await loadSettings();
    await refresh();
    toast("Settings saved");
  } catch (e) { toast(e.message, true); }
});

// ---------------------------------------------------------------- KPIs
async function loadSummary() {
  const s = await api("/api/summary");
  const kpi = (label, value) => `<div class="kpi"><span>${label}</span><b>${value}</b></div>`;
  $("#kpis").innerHTML = [
    kpi("Available cash", `<span class="${s.available_capital < 0 ? "neg" : ""}">${money(s.available_capital, 0)}</span>`),
    kpi("Capital in trades", `${money(s.capital_deployed, 0)} <small class="muted">${num(s.capital_deployed_pct, 0)}%</small>`),
    kpi("Account value", `${money(s.account_value, 0)} <small class="muted">cash + positions</small>`),
    kpi("Open positions", `${s.open_positions} / ${s.max_open_positions}`),
    kpi("Profit at Target 1", signed(s.potential_target1, (x) => money(x, 0))),
    kpi("Profit at Target 2", signed(s.potential_target2, (x) => money(x, 0))),
    kpi("Planned profit", `${signed(s.potential_plan, (x) => money(x, 0))} <small class="muted">½ T1 + ½ T2</small>`),
    kpi("Loss if all stops hit", money(-s.max_loss_total, 0)),
    kpi("Exit signals", `<span class="${s.exit_signals ? "neg" : ""}">${s.exit_signals}</span>${s.action_signals ? ` <small class="muted">+${s.action_signals} action</small>` : ""}`),
    kpi("Unrealised P&L", signed(s.unrealized_pnl, (x) => money(x))),
    kpi("Open risk (to stops)", money(s.open_risk)),
    kpi("Realised P&L", signed(s.total_pnl, (x) => money(x))),
    kpi("Equity", `${money(s.equity, 0)} <small class="muted">start ${money(s.account_size, 0)}</small>`),
    kpi("Win rate", s.win_rate === null ? "—" : `${num(s.win_rate, 0)}% <small class="muted">${s.wins}W/${s.losses}L</small>`),
    kpi("Expectancy", s.avg_r === null ? "—" : rmult(s.avg_r)),
    kpi("Avg win / loss", `${s.avg_win === null ? "—" : money(s.avg_win, 0)} / ${s.avg_loss === null ? "—" : money(s.avg_loss, 0)}`),
    kpi("Profit factor", s.profit_factor === null ? "—" : num(s.profit_factor, 2)),
    kpi("Max drawdown", money(-s.max_drawdown, 0)),
  ].join("");
}

// ---------------------------------------------------------------- trades table
function progressBar(t) {
  if (t.progress === null || t.progress === undefined) return "—";
  const t1 = ((t.target1 - t.stop_price) / (t.target2 - t.stop_price)) * 100;
  const entry = ((t.entry_price - t.stop_price) / (t.target2 - t.stop_price)) * 100;
  return `<span class="bar" title="stop → entry → T1 → T2"><i style="width:${t.progress}%"></i><em style="left:${entry}%"></em><em style="left:${t1}%"></em></span>`;
}

function openRow(t) {
  const trend = t.trend?.ok === true ? "trend-ok" : t.trend?.ok === false ? "trend-bad" : "muted";
  const priceSub = t.price_source === "manual" ? "manual" : t.price_date ?? (t.data_error ? "no data" : "");
  return `<tr class="${t.signal}">
    <td class="l"><b>${esc(t.symbol)}</b><span class="sub">${esc(t.setup ?? "")} · ${t.entry_date}</span></td>
    <td>${num(t.entry_price)}<span class="sub">${t.shares} sh · ${money(t.position_value, 0)}</span></td>
    <td>${num(t.price)}<span class="sub" title="${esc(t.data_error ?? "")}">${esc(priceSub)}</span></td>
    <td>${signed(t.pnl, (x) => money(x))}<span class="sub">${pct(t.pnl_pct)} · ${rmult(t.r_multiple)}</span></td>
    <td>${num(t.stop_price)}<span class="sub">${esc(t.stop_method)} · −${num(t.stop_pct, 1)}%</span></td>
    <td><b>${num(t.current_stop)}</b><span class="sub">${esc(t.stop_label)}</span></td>
    <td>${num(t.target1)}<span class="sub pos">+${money(t.profit_target1, 0)}</span></td>
    <td>${num(t.target2)}<span class="sub pos">+${money(t.profit_target2, 0)}</span></td>
    <td>${t.target_price ? `${num(t.target_price)}<span class="sub pos">+${money(t.profit_target, 0)} · ${num(t.target_rr, 1)}R</span>` : `<span class="muted">—</span>`}</td>
    <td class="pos">+${money(t.profit_plan, 0)}<span class="sub">${t.to_target2 !== null && t.to_target2 > 0 ? `${money(t.to_target2, 0)} left to T2` : ""}</span></td>
    <td>${progressBar(t)}</td>
    <td>${money(t.max_loss)}<span class="sub">open risk ${money(t.open_risk)}</span></td>
    <td class="l"><span class="${trend}">${t.trend?.ok === true ? "✓ Up" : t.trend?.ok === false ? "✗ Weak" : "?"}</span><span class="sub">SMA50 ${num(t.sma50)} · 200 ${num(t.sma200)}</span></td>
    <td>${t.days_held}d</td>
    <td class="action"><span class="badge ${t.signal}">${SIGNAL_LABEL[t.signal]}</span><br>${esc(t.action)}</td>
    <td><button class="small ghost" data-edit="${t.id}">Edit</button> <button class="small" data-close="${t.id}">Close</button></td>
  </tr>`;
}

function closedRow(t) {
  return `<tr class="${t.pnl > 0 ? "hold" : "exit"}">
    <td class="l"><b>${esc(t.symbol)}</b><span class="sub">${esc(t.setup ?? "")}</span></td>
    <td>${t.entry_date}<span class="sub">${num(t.entry_price)}</span></td>
    <td>${t.exit_date}<span class="sub">${num(t.exit_price)}</span></td>
    <td>${t.shares}</td>
    <td>${num(t.stop_price)}</td>
    <td>${signed(t.pnl, (x) => money(x))}</td>
    <td>${pct(t.pnl_pct)}</td>
    <td>${rmult(t.r_multiple)}</td>
    <td>${t.days_held}d</td>
    <td class="l">${esc(t.exit_reason ?? "")}<span class="sub">${esc(t.notes ?? "")}</span></td>
    <td><button class="small ghost" data-reopen="${t.id}">Reopen</button> <button class="small danger" data-delete="${t.id}">Delete</button></td>
  </tr>`;
}

function renderTable() {
  const table = $("#tradesTable");
  if (status === "open") {
    const rows = [...trades].sort((a, b) => SIGNAL_ORDER[a.signal] - SIGNAL_ORDER[b.signal]);
    table.innerHTML = `<thead><tr>
      <th class="l">Symbol</th><th>Entry</th><th>Price</th><th>P&amp;L</th><th>Initial stop</th>
      <th>Current stop</th><th>Target 1</th><th>Target 2</th><th>Your target</th><th>Planned profit</th><th>Progress</th><th>Max loss</th>
      <th class="l">Trend</th><th>Held</th><th class="l">What to do</th><th></th></tr></thead>
      <tbody>${rows.map(openRow).join("") || `<tr><td colspan="16" class="l muted">No open positions — add one above.</td></tr>`}</tbody>`;
    $("#legend").innerHTML = "Sorted by urgency. <b>EXIT</b> = sell now per your rules · <b>ACTION</b> = adjust stop / take partial profit · <b>WATCH</b> = needs attention · <b>HOLD</b> = plan unchanged. Progress bar: stop → entry | T1 | T2.";
  } else {
    table.innerHTML = `<thead><tr>
      <th class="l">Symbol</th><th>Entry</th><th>Exit</th><th>Shares</th><th>Stop</th><th>P&amp;L</th>
      <th>%</th><th>R</th><th>Held</th><th class="l">Reason / notes</th><th></th></tr></thead>
      <tbody>${trades.map(closedRow).join("") || `<tr><td colspan="11" class="l muted">No closed trades yet.</td></tr>`}</tbody>`;
    $("#legend").textContent = "Review weekly: which setups work, average R, and whether you followed your rules.";
  }
}

async function loadTrades(refreshPrices = false) {
  trades = await api(`/api/trades?status=${status}${refreshPrices ? "&refresh=true" : ""}`);
  renderTable();
}

async function refresh(refreshPrices = false) {
  await loadTrades(refreshPrices);
  await loadSummary();
}

document.querySelectorAll(".tab").forEach((b) => {
  b.onclick = async () => {
    document.querySelectorAll(".tab").forEach((x) => x.classList.toggle("active", x === b));
    status = b.dataset.status;
    await loadTrades();
  };
});

$("#refreshBtn").onclick = async () => {
  const b = $("#refreshBtn");
  b.disabled = true;
  try {
    await refresh(true);
    const failed = trades.filter((t) => t.data_error && t.price_source !== "manual");
    toast(failed.length ? `Prices updated — no data for ${failed.map((t) => t.symbol).join(", ")}` : "Prices updated", failed.length > 0);
  } catch (e) { toast(e.message, true); }
  b.disabled = false;
};

// ---------------------------------------------------------------- row buttons
let activeId = null;
$("#tradesTable").addEventListener("click", async (ev) => {
  const btn = ev.target.closest("button");
  if (!btn) return;
  const id = Number(btn.dataset.edit || btn.dataset.close || btn.dataset.reopen || btn.dataset.delete);
  const t = trades.find((x) => x.id === id);
  activeId = id;
  try {
    if (btn.dataset.close) {
      $("#closeSymbol").textContent = t.symbol;
      const f = $("#closeForm");
      f.elements.exit_price.value = t.price ?? "";
      f.elements.exit_date.value = new Date().toISOString().slice(0, 10);
      const a = t.action ?? "";
      f.elements.exit_reason.value = a.includes("Target 2") ? "Target 2" : a.includes("your target") ? "Your target" : a.includes("SMA50") ? "Trend broken (SMA50)" : a.includes("rail") ? "Trailing stop" : a.includes("stop hit") ? "Stop hit" : a.includes("Time") ? "Time stop" : "Discretionary";
      $("#closeDlg").showModal();
    } else if (btn.dataset.edit) {
      $("#editSymbol").textContent = t.symbol;
      const f = $("#editForm");
      f.elements.stop_price.value = t.stop_price;
      f.elements.shares.value = t.shares;
      f.elements.manual_price.value = t.manual_price ?? "";
      f.elements.target_price.value = t.target_price ?? "";
      f.elements.notes.value = t.notes ?? "";
      $("#editDlg").showModal();
    } else if (btn.dataset.reopen) {
      await api(`/api/trades/${id}/reopen`, { method: "POST" });
      await refresh();
      toast(`${t.symbol} reopened`);
    } else if (btn.dataset.delete) {
      if (!confirm(`Delete ${t.symbol} permanently?`)) return;
      await api(`/api/trades/${id}`, { method: "DELETE" });
      await refresh();
    }
  } catch (e) { toast(e.message, true); }
});

$("#closeDlg").addEventListener("close", async () => {
  if ($("#closeDlg").returnValue !== "save") return;
  try {
    const r = await api(`/api/trades/${activeId}/close`, { method: "POST", body: formData($("#closeForm")) });
    await refresh();
    toast(`${r.symbol} closed: ${r.pnl >= 0 ? "+" : ""}${money(r.pnl)} (${num(r.r_multiple, 2)}R)`);
  } catch (e) { toast(e.message, true); }
});

$("#editDlg").addEventListener("close", async () => {
  const action = $("#editDlg").returnValue;
  try {
    if (action === "delete") {
      if (!confirm("Delete this trade permanently?")) return;
      await api(`/api/trades/${activeId}`, { method: "DELETE" });
    } else if (action === "save") {
      const f = $("#editForm");
      const body = formData(f);
      body.manual_price = f.elements.manual_price.value === "" ? 0 : Number(f.elements.manual_price.value);
      body.target_price = f.elements.target_price.value === "" ? 0 : Number(f.elements.target_price.value);
      body.notes = f.elements.notes.value;
      await api(`/api/trades/${activeId}`, { method: "PUT", body });
    } else return;
    await refresh();
  } catch (e) { toast(e.message, true); }
});

// ---------------------------------------------------------------- add trade / preview
function renderPreview(p) {
  const item = (label, value, sub = "") => `<div class="item"><span>${label}</span><b>${value}</b>${sub ? `<span>${sub}</span>` : ""}</div>`;
  const trend = p.trend_at_entry;
  const notes = [];
  if (p.data_error) notes.push(`⚠ Market data unavailable (${esc(p.data_error)}) — stop uses the % fallback unless you enter one.`);
  if (trend?.ok === false) notes.push(`⚠ Trend filter failed at entry: ${esc(trend.text)}`);
  else if (trend?.ok) notes.push(`✓ Trend filter: ${esc(trend.text)}`);
  notes.push(`Symbol: <b>${esc(p.symbol)}</b>`);
  if (p.available_after < 0) notes.push(`⚠ Not enough cash: this trade needs ${money(p.position_value, 0)} but only ${money(p.available_capital, 0)} is available.`);
  if (p.shares !== p.suggested_shares) notes.push(`You entered ${p.shares} shares; the risk rule suggests ${p.suggested_shares}.`);
  if (p.risk_pct_of_account > settings.risk_pct + 0.01) notes.push(`⚠ This risks ${num(p.risk_pct_of_account, 2)}% of the account (limit ${settings.risk_pct}%).`);
  $("#preview").innerHTML = [
    item("Stop loss", num(p.stop_price), `${esc(p.stop_method)} · −${num(p.stop_pct, 1)}%`),
    item("Risk / share (1R)", money(p.risk_per_share)),
    item("Shares", p.shares, `suggested ${p.suggested_shares}`),
    item("Position value", money(p.position_value, 0)),
    item("Max loss", money(p.max_loss), `${num(p.risk_pct_of_account, 2)}% of equity`),
    item("Cash left after", `<span class="${p.available_after < 0 ? "neg" : ""}">${money(p.available_after, 0)}</span>`, `available now ${money(p.available_capital, 0)}`),
    item("Breakeven trigger", num(p.breakeven_trigger), `+${settings.breakeven_r}R → stop to entry`),
    item("Target 1", num(p.target1), `+${settings.target1_rr}R · sell ~half`),
    item("Target 2", num(p.target2), `+${settings.target2_rr}R · exit rest`),
    item("Profit at Target 1", `<span class="pos">+${money(p.profit_target1)}</span>`, `all ${p.shares} shares`),
    item("Profit at Target 2", `<span class="pos">+${money(p.profit_target2)}</span>`, `all ${p.shares} shares`),
    item("Planned profit", `<span class="pos">+${money(p.profit_plan)}</span>`, "½ at T1 + ½ at T2"),
    p.target_price ? item("Profit at your target", `<span class="pos">+${money(p.profit_target)}</span>`, `${num(p.target_price)} · +${num(p.target_pct, 1)}% · ${num(p.target_rr, 1)}R`) : "",
    item("ATR(14) at entry", num(p.atr_at_entry)),
    notes.length ? `<div class="note">${notes.join("<br>")}</div>` : "",
  ].join("");
  $("#preview").classList.remove("hidden");
}

$("#previewBtn").onclick = async () => {
  const f = $("#tradeForm");
  if (!f.reportValidity()) return;
  const b = $("#previewBtn");
  b.disabled = true;
  try { renderPreview(await api("/api/preview", { method: "POST", body: formData(f) })); }
  catch (e) { toast(e.message, true); }
  b.disabled = false;
};

$("#tradeForm").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const f = ev.target;
  try {
    const summary = await api("/api/summary");
    if (summary.open_positions >= summary.max_open_positions &&
        !confirm(`You already have ${summary.open_positions} open positions (max ${summary.max_open_positions}). Add anyway?`)) return;
    const p = await api("/api/preview", { method: "POST", body: formData(f) });
    if (p.available_after < 0 &&
        !confirm(`This trade costs ${money(p.position_value, 0)} but only ${money(p.available_capital, 0)} cash is available. Add anyway?`)) return;
    const t = await api("/api/trades", { method: "POST", body: formData(f) });
    toast(`${t.symbol} added: ${t.shares} shares, stop ${num(t.stop_price)}, profit at T1 +${money(t.profit_target1, 0)}`);
    f.reset();
    f.elements.entry_date.value = new Date().toISOString().slice(0, 10);
    $("#preview").classList.add("hidden");
    if (status !== "open") document.querySelector('.tab[data-status="open"]').click();
    else await refresh();
    await loadSummary();
  } catch (e) { toast(e.message, true); }
});

// ---------------------------------------------------------------- boot
(async () => {
  $("#tradeForm").elements.entry_date.value = new Date().toISOString().slice(0, 10);
  try {
    await loadSettings();
    await refresh();
  } catch (e) { toast(e.message, true); }
})();
