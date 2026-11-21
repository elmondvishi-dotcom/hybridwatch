// HybridWatch analyst console. Plain JS, no build step.
const $ = (s, el = document) => el.querySelector(s);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const api = async (path, opts) => {
  const r = await fetch(path, opts);
  if (!r.ok) throw new Error(`${r.status} ${path}`);
  return r.json();
};
const post = (path, body) => api(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
// Human dates in data time (UTC): "2 Nov, 07:31"
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const pad2 = (n) => String(n).padStart(2, "0");
const fmt = (ts) => {
  if (!ts) return "—";
  const d = new Date(ts * 1000);
  return `${d.getUTCDate()} ${MONTHS[d.getUTCMonth()]}, ${pad2(d.getUTCHours())}:${pad2(d.getUTCMinutes())}`;
};
const fmtTime = (ts) => { const d = new Date(ts * 1000); return `${pad2(d.getUTCHours())}:${pad2(d.getUTCMinutes())}`; };
const duration = (s) => { const m = Math.round(s / 60), h = Math.floor(m / 60); return h ? `${h}h ${pad2(m % 60)}m` : `${m}m`; };
const plural = (n, word) => `${Number(n).toLocaleString()} ${word}${n === 1 ? "" : "s"}`;
const LANG = { sq: "Albanian", sr: "Serbian", en: "English", unk: "Other" };
const SRC = { x: "X", facebook: "Facebook", telegram: "Telegram", news: "News", rss: "News site", replay: "Replay", tiktok: "TikTok", youtube: "YouTube" };
const src = (s) => SRC[s] || s || "";
const GROWTH = (g) => g >= 3 ? ["⇈ Growing fast", "g-fast"] : g >= 1.5 ? ["↑ Growing", "g-up"] : g >= 0.5 ? ["→ Stable", "g-stable"] : ["↓ Slowing", "g-stable"];
const TYPE = { hybrid: "Hybrid threat: cyber + information", coordination: "Coordination suspected", debunk_match: "Matches debunked claim", trending: "Trending" };
const TYPE_TAG = { hybrid: "hyb", coordination: "red", debunk_match: "orange", trending: "blue" };
const FOLDERS = [["inbox", "Inbox"], ["review", "Needs review"], ["watching", "Watching"], ["confirmed", "Confirmed"], ["dismissed", "Dismissed"], ["info", "Info"]];
// threat levels (threat.py): Info grey, Watch blue, Threat orange, Critical red
const LEVEL = { info: "Info", watch: "Watch", threat: "Threat", critical: "Critical" };
const FACTOR = { target: "Target", scale: "Scale", hostility: "Hostility", credibility: "Credibility", escalation: "Escalation" };
// "Why this is a threat / Why not higher / where the boundary is", shown on alerts, events and cyber events
function threatHTML(t, level) {
  if (!t || !t.factors) return "";
  return `<div class="dsec threat-box"><h3>Threat assessment</h3>
    ${scoreBreakdownHTML(t, level)}
    <div class="muted small">A threat needs a sensitive target AND a confirmed hostile signal.</div>
    <div class="why2">
      <div><h4>Why this is a threat</h4>${t.why && t.why.length ? `<ul>${t.why.map((w) => `<li>${esc(w)}</li>`).join("")}</ul>` : '<div class="muted">No factor is high.</div>'}</div>
      <div><h4>Why not higher</h4>${t.why_not && t.why_not.length ? `<ul>${t.why_not.map((w) => `<li>${esc(w)}</li>`).join("")}</ul>` : '<div class="muted">Highest level.</div>'}</div>
    </div>
    ${(t.notes || []).map((n) => `<div class="boundary"><b>Not a threat (${esc(n.label)}):</b> ${esc(n.note)}</div>`).join("")}
  </div>`;
}

const state = { view: "overview", folder: "inbox", paused: false, snapshot: null, alerts: [], selected: null, detail: null, settings: null, undo: null };

const SIG = { coordination: "Coordination", new_accounts: "New accounts", no_credible_first: "No credible outlet first",
  debunk_match: "Debunked claim", trending: "Trending" };
const STATUS = { new: "Inbox", needs_review: "Needs review", watching: "Watching", confirmed: "Confirmed", dismissed: "Dismissed", merged: "Merged" };

// toast(msg) or toast(msg, {label, run}) -> shows a button (e.g. Undo) and stays a bit longer
function toast(msg, action) {
  const t = $("#toast");
  t.innerHTML = `<span>${esc(msg)}</span>` + (action ? ` <button class="btn undo" id="toast-act">${esc(action.label)}</button>` : "");
  t.classList.remove("hidden");
  if (action) $("#toast-act").onclick = () => { t.classList.add("hidden"); action.run(); };
  clearTimeout(toast.h); toast.h = setTimeout(() => { t.classList.add("hidden"); state.undo = null; }, action ? 8000 : 2500);
}

// ------------------------------------------------------------------ navigation
const VIEW_TITLE = {
  overview: ["Overview", "the highest current threat level, where, and what is growing"],
  situation: ["Situation", "the picture for decision makers"], events: ["Events", "what happened, where, how sure"],
  alerts: ["Information threats", "disinformation and coordinated campaigns"], cyber: ["Cyber", "every claim is verified before it counts"],
  activity: ["Activity", "what the system processed"], settings: ["Settings", "sources and focus"] };
function showView(name) {
  document.querySelectorAll(".nav").forEach((x) => x.classList.toggle("active", x.dataset.view === name));
  state.view = name;
  document.querySelectorAll(".view").forEach((v) => v.classList.toggle("hidden", v.id !== `view-${name}`));
  $("#crumb").textContent = VIEW_TITLE[name][0];
  $("#crumb-sub").textContent = VIEW_TITLE[name][1];
  // pause freezes the inbox and the Overview; "N new" belongs to the inbox
  $("#pause").classList.toggle("dim", !["alerts", "overview"].includes(name));
  $("#pending").classList.toggle("dim", name !== "alerts");
  if (name === "overview") loadOverview(true);
  if (name === "situation") loadSituation();
  if (name === "events") loadEvents();
  if (name === "alerts") refresh();
  if (name === "cyber") loadCyber();
  if (name === "activity") loadActivity();
  if (name === "settings") loadSettings();
}
document.querySelectorAll(".nav").forEach((b) => b.onclick = () => showView(b.dataset.view));
// cross-links between events, information alerts and cyber events
function openAlert(id) { if (state.view !== "alerts") showView("alerts"); select(id); }
function openCyber(id) { if (state.view !== "cyber") showView("cyber"); selectCyber(id); }
function openEvent(id) {
  if (String(id).startsWith("c")) return openCyber(+String(id).slice(1));
  if (state.view !== "events") showView("events");
  selectEvent(+String(id).replace("e", ""));
}
function bindGoto(root) {
  root.querySelectorAll("[data-goto]").forEach((el) => el.onclick = () => openAlert(+el.dataset.goto));
  root.querySelectorAll("[data-cyber]").forEach((el) => el.onclick = () => openCyber(+el.dataset.cyber));
  root.querySelectorAll("[data-event]").forEach((el) => el.onclick = () => openEvent(el.dataset.event));
}

// ------------------------------------------------------------------ pause
$("#pause").onclick = () => {
  state.paused = !state.paused;
  state.snapshot = state.paused ? Date.now() / 1000 : null;
  $("#pause").textContent = state.paused ? "▶ Resume" : "⏸ Pause";
  const b = $("#livebadge");
  b.textContent = state.paused ? "❚❚ PAUSED — still monitoring" : "● LIVE";
  b.className = "badge " + (state.paused ? "live-off" : "live-on");
  refresh();
  if (state.view === "overview") loadOverview(!state.paused);
};
$("#pending").onclick = () => { state.snapshot = Date.now() / 1000; refresh(); };

// ------------------------------------------------------------------ list
async function refresh() {
  try {
    const st = await api("/api/status");
    $("#clock").textContent = `Last update ${fmt(st.sim_now)} · ${plural(st.posts, "post")} · ${plural(st.sources, "source")}` +
      (st.worker_alive ? "" : " · ⚠ analysis not running");
    renderFolders(st.folders);
    renderFilterBar();
    const badge = (id, n, cls = "") => { const b = $(id); b.textContent = n || ""; b.className = "nb " + (n ? cls : ""); };
    badge("#nb-alerts", st.folders.inbox, "blue");
    badge("#nb-cyber", st.cyber_open, "orange");
    badge("#nb-events", st.events_critical, "red");
    const q = state.paused ? `&snapshot=${state.snapshot}` : "";
    const res = await api(`/api/alerts?folder=${state.folder}${q}${filterQuery()}`);
    state.alerts = res.alerts;
    const p = $("#pending");
    p.classList.toggle("hidden", !res.pending);
    p.textContent = `${res.pending} new — show`;
    renderList();
    if (!state.selected && state.view === "alerts") renderOverview();
  } catch (e) { console.error(e); }
}

function renderFolders(counts) {
  $("#folders").innerHTML = FOLDERS.map(([k, n]) =>
    `<button class="folder ${k === state.folder ? "active" : ""}" data-f="${k}">${n}<span class="n">${counts[k] || ""}</span></button>`).join("");
  document.querySelectorAll("#folders .folder").forEach((b) => b.onclick = () => { state.folder = b.dataset.f; state.filter = null; state.selected = null; refresh(); showEmpty(); });
}

function renderList() {
  const el = $("#list");
  if (!state.alerts.length) {
    el.innerHTML = `<div class="empty">${state.filter ? "No alert matches this filter right now." : state.folder === "inbox" ? "All caught up. Still monitoring." : "Nothing here."}</div>`;
    return;
  }
  el.innerHTML = state.alerts.map((a) => {
    const [grow, growCls] = GROWTH(a.growth);
    return `
    <div class="row ${a.unread ? "unread" : ""} ${a.id === state.selected ? "sel" : ""}" data-id="${a.id}">
      <div class="bar sev-${a.severity}"></div>
      <div class="main">
        <div class="title" title="${esc(a.title)}">${esc(a.title)}</div>
        <div class="meta">
          <span class="pill sev-${a.severity}">${a.severity}</span>
          <span>${TYPE[a.type] || a.type}</span>
          <span>${plural(a.posts, "post")}</span>
          <span class="${growCls}">${grow}</span>
          ${a.previously_dismissed ? '<span class="tag orange">re-opened</span>' : ""}
          ${a.new_posts ? `<span class="tag blue">+${a.new_posts} new</span>` : ""}
          <span class="when">${fmt(a.bumped_sim)}</span>
        </div>
      </div>
    </div>`;
  }).join("");
  el.querySelectorAll(".row").forEach((r) => r.onclick = () => select(+r.dataset.id));
}

function showEmpty() { state.selected = null; state.detail = null; renderOverview(); }

// ------------------------------------------------------------------ overview (nothing selected)
async function renderOverview() {
  let o;
  try { o = await api("/api/overview"); } catch (e) { return; }
  if (state.selected) return;  // an alert was opened meanwhile
  const big = (v, k, cls = "") => `<div class="ov-card ${cls}"><div class="v">${v}</div><div class="k">${k}</div></div>`;
  const nets = o.networks.length;
  const html = `<div class="overview">
    <h2>Situation overview</h2>
    <p class="muted">Open alerts across Inbox, Needs review and Watching.</p>
    <div class="ov-cards">
      ${big(o.critical, "critical alerts", o.critical ? "crit" : "")}
      ${big(o.threat, "threat alerts", o.threat ? "high" : "")}
      ${big(o.coordinated, "coordinated campaigns active", o.coordinated ? "coord" : "")}
    </div>
    <h3 class="ov-h">Last 24 hours</h3>
    <div class="ov-mini">
      ${[[o.last24h.hybrid, "hybrid threats", "hyb"], [o.last24h.confirmed_incidents, "confirmed cyber incidents", "crit"],
         [o.last24h.open_claims, "open cyber claims", "high"], [o.last24h.refuted_claims, "refuted cyber claims", "ok"],
         [o.last24h.campaigns, "active disinformation campaigns", "coord"]]
        .map(([v, k, c]) => `<div class="mini ${v ? c : ""}"><div class="v">${v}</div><div class="k">${k}</div></div>`).join("")}
    </div>
    ${o.hybrid.length ? `<div class="ov-hybrid"><h3>Hybrid threats: cyber event + disinformation narrative</h3>
      ${o.hybrid.map((h) => `<a class="link-plain" data-goto="${h.id}"><span class="pill sev-${h.severity}">${h.severity}</span> #${h.id} ${esc(h.title)}</a>`).join("")}</div>` : ""}
    <div class="ov-row">
      <div class="ov-box"><h3>Linked networks</h3>
        ${o.linked_alerts ? `<b>${o.linked_alerts} alerts</b> share accounts with each other — ${nets === 1 ? "likely one network" : `${nets} separate networks`}.
          <div class="ov-links">${o.networks.map((g) => g.map((id) => `<a class="tag red link" data-goto="${id}">#${id}</a>`).join(" ")).join('<span class="muted"> · </span>')}</div>`
          : `<span class="muted">No alerts share accounts yet.</span>`}
      </div>
      <div class="ov-box"><h3>Latest alert</h3>
        ${o.latest ? `<a class="link-plain" data-goto="${o.latest.id}"><span class="pill sev-${o.latest.severity}">${o.latest.severity}</span> ${esc(o.latest.title)}</a>
          <div class="muted">raised ${fmt(o.latest.t)}</div>` : `<span class="muted">No alerts yet. Still monitoring.</span>`}
      </div>
    </div>
    ${o.top ? `<button class="btn primary big" data-goto="${o.top.id}">Open most critical alert →</button>
      <div class="muted small">${esc(o.top.title)}</div>` : ""}
    <div class="keys">Keyboard: <kbd>J</kbd>/<kbd>K</kbd> next/previous · <kbd>C</kbd> confirm · <kbd>D</kbd> dismiss · <kbd>R</kbd> needs review · <kbd>U</kbd> undo · <kbd>Esc</kbd> close</div>
  </div>`;
  if (!$("#detail .overview") || renderOverview.last !== html) $("#detail").innerHTML = html;  // no flicker on refresh
  renderOverview.last = html;
  bindGoto($("#detail"));
}

// ------------------------------------------------------------------ detail
async function select(id) {
  state.selected = id;
  renderList();
  const d = await api(`/api/alerts/${id}`);
  if (state.selected !== id) return;  // another alert was opened meanwhile
  state.detail = d;
  renderDetail(d);
  $("#detail").scrollTop = 0;
}

function renderDetail(d) {
  const conf = d.confidence >= 0.75 ? "High" : d.confidence >= 0.5 ? "Medium" : "Low";
  const langs = Object.entries(d.stats.langs || {}).sort((a, b) => b[1] - a[1]).map(([k, v]) => `${LANG[k] || k} ${v}`).join(" · ");
  const q = d.quote;
  $("#detail").innerHTML = `
    <div class="actions">
      <button class="btn" id="a-confirm">✓ Confirm</button>
      <button class="btn" id="a-dismiss">✕ Dismiss</button>
      <button class="btn" id="a-review">? Needs review</button>
      <button class="btn" id="a-watch">👁 Watch</button>
    </div>
    ${sinceLastHTML(d.since_last)}
    <div class="dsec">
      <div class="title-row">
        <span class="pill sev-${d.severity}">${d.severity}</span>
        <span class="status badge">${esc(STATUS[d.status] || d.status)}</span>
        ${d.title_source === "llm" ? '<span class="tag ai">AI title</span>' : ""}
        ${d.title_source === "factcheck" ? '<span class="tag muted-tag" title="Title taken from the matched fact-check">claim as described by fact-checker</span>' : ""}
      </div>
      <h2 class="dtitle">${esc(d.title)}</h2>
      ${d.tags && d.tags.length ? `<div class="chips concept">${d.tags.map((t) => `<span class="tag">${esc(t)}</span>`).join("")}</div>` : ""}
      ${q ? `<blockquote class="quote">“${esc(q.text)}”
        <div class="qmeta">${esc(LANG[q.lang] || q.lang)} · ${esc(src(q.source_type))}${q.translation ? ` · <span class="tr">EN: ${esc(q.translation)}</span>` : ""}</div></blockquote>` : ""}
      <div class="chips">
        <span class="tag ${TYPE_TAG[d.type] || ""}">${TYPE[d.type] || d.type}</span>
        <span class="tag" title="System confidence ${d.confidence}">${conf} confidence</span>
        <span class="tag">${plural(d.stats.size, "post")} · ${plural(d.stats.accounts, "account")}</span>
        <span class="tag">first seen ${fmt(d.stats.first_ts)}</span>
        <span class="tag">${esc(langs)}</span>
        ${(d.places || []).map((p) => `<span class="tag" title="Municipality named in the posts">📍 ${esc(p.name)}</span>`).join("")}
      </div>
    </div>
    ${d.summary ? `<div class="dsec"><div class="verdict sevb-${d.severity}">${esc(d.summary)}</div></div>` : ""}
    ${threatHTML(d.threat, d.severity)}
    <div class="dsec"><h3>Why flagged</h3>
      <ul class="why">${d.reasons.map((r) => `<li class="${r.startsWith("Previously") ? "prev" : ""}">${esc(r)}</li>`).join("")}</ul>
    </div>
    ${linkedCyberHTML(d.linked_cyber)}
    ${relatedHTML(d.related)}
    ${d.factcheck ? `<div class="dsec"><h3>Matched fact-check</h3><div class="fcbox">
      <b>${esc(d.factcheck.verdict)}</b> — ${esc(d.factcheck.source)} (${esc(d.factcheck.date)})<br>
      <span class="muted">${esc(d.factcheck.claim)}</span><br>
      ${d.factcheck.url && d.factcheck.url.startsWith("http") ? `<a href="${esc(d.factcheck.url)}" target="_blank" rel="noopener">Open fact-check →</a>` : ""}
    </div></div>` : ""}
    ${alertChartsHTML(d)}
    <div class="dsec"><h3>Evidence</h3>${(d.evidence || []).map(evidenceHTML).join("")}</div>
    <div class="dsec"><h3>Accounts (in order of first post)</h3>
      <table><tr><th>Account</th><th>Posts</th><th>Followers</th><th>Account age at first post</th><th>First post</th></tr>
      ${(d.accounts || []).map((a) => {
        const age = a.created ? Math.round((a.first - a.created) / 86400) : null;
        return `<tr><td>${esc(a.author)}</td><td>${a.posts}</td><td>${(a.followers || 0).toLocaleString()}</td>
        <td>${age === null ? "—" : age + " days"} ${age !== null && age < 30 ? '<span class="tag orange">new</span>' : ""}</td><td>${fmt(a.first)}
        ${(a.also_in || []).map((id) => `<a class="tag red link" data-goto="${id}">also in #${id}</a>`).join(" ")}</td></tr>`;
      }).join("")}</table>
    </div>
    <div class="dsec"><h3>History</h3><div class="hist">
      ${(d.history || []).map((h) => `<div>${fmt(h.sim)} — ${esc(h.event)}</div>`).join("")}</div></div>`;
  $("#a-confirm").onclick = (e) => openConfirm(e.target);
  $("#a-dismiss").onclick = (e) => openDismiss(e.target);
  $("#a-review").onclick = (e) => openReview(e.target);
  $("#a-watch").onclick = () => decide({ action: "watch" });
  bindGoto($("#detail"));
  drawAlertCharts(d);
}

// Cyber events linked to this narrative (the hybrid threat)
function linkedCyberHTML(links) {
  if (!links || !links.length) return "";
  return `<div class="dsec linked-cyber"><h3>Linked cyber event — hybrid threat</h3>
    ${links.map((l) => `<div class="cy-card" data-cyber="${l.event_id}">
      <div class="cy-icon">${KIND_ICON[l.kind] || "•"}</div>
      <div class="grow"><b>#${l.event_id} ${esc(l.title)}</b>
        <div class="muted">${esc(l.reason)}</div></div>
      <div class="cy-side"><span class="cpill st-${l.status}">${CY_STATUS[l.status]}</span>
        <span class="adm" title="${esc(admiraltyText(l.admiralty))}">${esc(l.admiralty)}</span></div>
    </div>`).join("")}
    <p class="muted small">A cyber incident and a disinformation narrative about the same target, close in time. The link is evidence for the analyst, not a conclusion.</p>
  </div>`;
}

// "What changed since you last looked" — shown only when something actually changed
function sinceLastHTML(s) {
  if (!s) return "";
  const parts = [];
  if (s.posts > 0) parts.push(`<b>+${s.posts}</b> posts`);
  if (s.accounts > 0) parts.push(`<b>+${s.accounts}</b> accounts`);
  if (s.new_signals.length) parts.push(`new signal: ${s.new_signals.map((k) => `<span class="tag red">${esc(SIG[k] || k)}</span>`).join(" ")}`);
  if (s.severity) parts.push(`severity <span class="c-${s.severity[0]}">${s.severity[0]}</span> → <b class="c-${s.severity[1]}">${s.severity[1]}</b>`);
  return `<div class="dsec since"><h3>New since you last looked · ${fmt(s.since_sim)}</h3>
    <div class="chips">${parts.map((p) => `<span>${p}</span>`).join('<span class="muted">·</span>')}</div>
    ${s.events.length ? `<ul class="since-ev">${s.events.map((e) => `<li>${esc(e)}</li>`).join("")}</ul>` : ""}</div>`;
}

// Other alerts pushed by the same coordinated accounts
function relatedHTML(rel) {
  if (!rel || !rel.length) return "";
  return `<div class="dsec related"><h3>Same network — linked alerts</h3>
    ${rel.map((r) => `<div class="rel" data-goto="${r.id}">
      <div class="bar sev-${r.severity}"></div>
      <div class="grow"><b>#${r.id} ${esc(r.title)}</b>
        <div class="muted">${r.shared} of this alert's ${r.of} coordinated accounts also pushed it
          <span class="tag ${r.status === "confirmed" ? "red" : ""}">${esc(STATUS[r.status] || r.status)}</span></div></div>
      <div class="pct">${Math.round(r.share * 100)}%<div class="muted">shared</div></div>
    </div>`).join("")}
    <p class="muted small">Shared accounts suggest one actor behind several campaigns. Evidence only — attribution is the analyst's call.</p>
  </div>`;
}

function evidenceHTML(p) {
  const who = `${esc(src(p.source_type))} · ${esc(p.author)}`;
  return `<div class="ev">
    <div class="head">${p.key ? '<span class="tag ai">★ key</span>' : ""}<b>${who}</b><span>${fmt(p.ts)}</span>
      <span>${(p.followers || 0).toLocaleString()} followers</span>
      ${p.credibility === "credible" ? '<span class="tag blue">credible outlet</span>' : ""}
      ${p.credibility === "low" ? '<span class="tag orange">low-credibility channel</span>' : ""}
      ${p.copy_paste ? '<span class="tag red">copy-paste</span>' : ""}
      ${p.new_account ? '<span class="tag orange">new account</span>' : ""}
      ${p.debunk_id ? '<span class="tag red">repeats debunked claim</span>' : ""}
    </div>
    <div class="txt">${esc(p.text)}</div>
    ${p.translation ? `<div class="tr">EN: ${esc(p.translation)}</div>` : ""}
    <div class="head"><a href="${esc(p.url)}" target="_blank" rel="noopener">original</a> · archived copy stored</div>
  </div>`;
}

// ------------------------------------------------------------------ decisions
function popover(anchor, html, onSubmit) {
  closePop();
  const p = document.createElement("div");
  p.className = "pop"; p.id = "pop"; p.innerHTML = html + `<div class="foot"><button class="btn" id="p-cancel">Cancel</button><button class="btn primary" id="p-ok">Submit</button></div>`;
  document.body.appendChild(p);
  const r = anchor.getBoundingClientRect();
  p.style.left = Math.min(r.left, window.innerWidth - 440) + "px";
  p.style.top = r.bottom + 6 + "px";
  $("#p-cancel").onclick = closePop;
  $("#p-ok").onclick = () => { const body = onSubmit(p); if (body) { decide(body); closePop(); } };
}
function closePop() { const p = $("#pop"); if (p) p.remove(); }

function aiField(key, inner, sug) {
  const s = sug[key];
  return `<div data-k="${key}" class="${s && s.source === "ai" ? "aiwrap" : ""}">${inner}${s && s.reason ? `<div class="why-ai">AI: ${esc(s.reason)}</div>` : ""}</div>`;
}

function openConfirm(anchor) {
  const d = state.detail, s = d.suggestion || {};
  const types = ["False claim", "Misleading context", "Manipulated media", "Coordinated amplification"];
  const sugTypes = (s.type && s.type.value) || [];
  const sel = (opts, v) => opts.map((o) => `<option ${o === v ? "selected" : ""}>${o}</option>`).join("");
  const tech = (s.techniques && s.techniques.value) || [];
  popover(anchor, `
    <h4>Confirm disinformation</h4>
    <label>Type</label>
    ${types.map((t) => `<div class="opt"><input type="checkbox" name="type" value="${t}" ${sugTypes.includes(t) ? "checked" : ""}> ${t} ${sugTypes.includes(t) ? '<span class="tag ai">AI</span>' : ""}</div>`).join("")}
    <label>Severity</label>
    ${aiField("severity", `<select id="f-severity" class="field-ai">${sel(["info", "watch", "threat", "critical"], s.severity?.value)}</select>`, s)}
    <label>Note (optional)</label><textarea id="f-note" rows="2"></textarea>
    <details><summary>Add details (optional — AI pre-filled, change anything)</summary>
      <label>Analyst confidence</label>
      ${aiField("analyst_confidence", `<select id="f-analyst_confidence" class="field-ai">${sel(["Unknown", "almost certain", "high", "medium", "low"], s.analyst_confidence?.value)}</select>`, s)}
      <label>Suspected actor</label>${aiField("actor", `<input id="f-actor" class="field-ai" value="${esc(s.actor?.value || "Unknown")}">`, s)}
      <label>Target</label>${aiField("target", `<input id="f-target" class="field-ai" value="${esc(s.target?.value || "Unknown")}">`, s)}
      <label>Apparent aim</label>${aiField("aim", `<input id="f-aim" class="field-ai" value="${esc(s.aim?.value || "Unknown")}">`, s)}
      <label>Techniques</label>
      ${tech.map((t) => `<div class="opt"><input type="checkbox" name="tech" value="${esc(t.tag)}" checked> ${esc(t.tag)} <span class="why-ai">(${esc(t.reason)})</span></div>`).join("") || '<div class="muted">none suggested</div>'}
      <label>Source rating (Admiralty: reliability A–F / credibility 1–6)</label>
      <input id="f-admiralty" placeholder="e.g. F6 = reliability unknown, cannot be judged" value="">
      <label>Recommended action</label>
      ${aiField("recommended_action", `<select id="f-recommended_action" class="field-ai">${sel(["Monitor", "Review", "Escalate", "Notify partner", "Request takedown", "Public correction"], s.recommended_action?.value)}</select>`, s)}
    </details>`,
  (p) => {
    const type = [...p.querySelectorAll('input[name="type"]:checked')].map((x) => x.value);
    if (!type.length) { toast("Pick at least one type"); return null; }
    const v = (id) => p.querySelector(id)?.value;
    const fields = { type, severity: v("#f-severity") };
    if (p.querySelector("details").open) {
      Object.assign(fields, {
        analyst_confidence: v("#f-analyst_confidence"), actor: v("#f-actor"), target: v("#f-target"), aim: v("#f-aim"),
        techniques: [...p.querySelectorAll('input[name="tech"]:checked')].map((x) => x.value),
        admiralty: v("#f-admiralty") || "Unknown", recommended_action: v("#f-recommended_action"),
      });
    }
    return { action: "confirm", fields, note: v("#f-note") || null };
  });
  // a field the analyst touches is no longer "AI"
  document.querySelectorAll("#pop .field-ai").forEach((f) => f.addEventListener("change", () => f.classList.remove("field-ai")));
}

function openDismiss(anchor) {
  const reasons = [["organic", "Real event, organic coverage"], ["not_relevant", "Not relevant to us"], ["false_positive", "False positive (e.g. news agencies sharing a wire story)"], ["duplicate", "Duplicate of another alert"]];
  popover(anchor, `<h4>Why dismiss?</h4>
    ${reasons.map(([k, n], i) => `<div class="opt"><input type="radio" name="r" value="${k}" ${i === 0 ? "checked" : ""}> ${n}</div>`).join("")}
    <label>Duplicate of alert # (only for duplicates)</label><input id="f-dup" type="number">
    <label>Note (optional)</label><textarea id="f-note" rows="2"></textarea>
    <p class="muted" style="font-size:12px">Dismissed alerts stay quiet but are still watched: they re-open if a new kind of signal appears (e.g. coordination).</p>`,
  (p) => ({ action: "dismiss", reason: p.querySelector('input[name="r"]:checked').value,
    duplicate_of: +p.querySelector("#f-dup").value || null, note: p.querySelector("#f-note").value || null }));
}

function openReview(anchor) {
  popover(anchor, `<h4>Needs more review</h4>
    <label>Assign to (optional)</label><input id="f-who" placeholder="colleague / senior analyst">
    <label>What is unclear?</label><textarea id="f-note" rows="3"></textarea>`,
  (p) => ({ action: "review", reason: p.querySelector("#f-who").value || null, note: p.querySelector("#f-note").value || null }));
}

async function decide(body) {
  const id = state.selected;
  const idx = state.alerts.findIndex((a) => a.id === id);
  const res = await post(`/api/alerts/${id}/decision`, body);
  const label = state.detail?.title || `#${id}`;
  const msg = body.action === "confirm" && res.changed_fields?.length
    ? `Confirmed — you changed: ${res.changed_fields.join(", ")}` : `${res.event.split(" — ")[0]}: ${label}`;
  state.undo = () => undoDecision(id);
  toast(msg, { label: "Undo (U)", run: state.undo });
  await refresh();
  // auto-advance to the next alert
  const next = state.alerts[Math.min(idx, state.alerts.length - 1)];
  if (next) select(next.id); else showEmpty();
}

async function undoDecision(id) {
  state.undo = null;
  try {
    const res = await post(`/api/alerts/${id}/undo`, {});
    state.folder = FOLDERS_BY_STATUS[res.status] || state.folder;
    toast("Undone");
    await refresh();
    select(id);
  } catch (e) { toast("Nothing to undo"); }
}
const FOLDERS_BY_STATUS = { new: "inbox", needs_review: "review", watching: "watching", confirmed: "confirmed", dismissed: "dismissed" };

// ------------------------------------------------------------------ keyboard
document.addEventListener("keydown", (e) => {
  if (state.view !== "alerts" || ["INPUT", "TEXTAREA", "SELECT"].includes(document.activeElement.tagName)) return;
  if (e.key === "Escape") return closePop();
  if ((e.key === "u" || (e.key === "z" && (e.ctrlKey || e.metaKey))) && state.undo) { e.preventDefault(); $("#toast").classList.add("hidden"); return state.undo(); }
  const i = state.alerts.findIndex((a) => a.id === state.selected);
  if (e.key === "j" && state.alerts.length) select(state.alerts[Math.min(i + 1, state.alerts.length - 1)].id);
  if (e.key === "k" && state.alerts.length) select(state.alerts[Math.max(i - 1, 0)].id);
  if (!state.selected || $("#pop")) return;
  if (e.key === "c") $("#a-confirm").click();
  if (e.key === "d") $("#a-dismiss").click();
  if (e.key === "r") $("#a-review").click();
});

// ------------------------------------------------------------------ activity page
async function loadActivity() {
  const a = await api("/api/activity");
  const t = a.throughput;
  $("#act-cards").innerHTML = [
    [t.posts.toLocaleString(), "posts processed"],
    [Math.round(t.avg_per_sec).toLocaleString() + "/s", "average processing speed"],
    [Math.round(t.peak_per_sec).toLocaleString() + "/s", "peak processing speed"],
    [a.alerts.length, "alerts raised"],
    a.ai_prefill.confirmed ? [`${a.ai_prefill.fields_changed} edits / ${a.ai_prefill.confirmed}`, "analyst edits to AI pre-fill (per confirmed case)"] : null,
  ].filter(Boolean).map(([v, k]) => `<div class="card"><div class="v">${v}</div><div class="k">${k}</div></div>`).join("");
  const b = a.buckets;
  if (!b.length) { $("#act-chart").innerHTML = '<div class="muted">No data yet.</div>'; return; }
  const W = 1000, H = 220, pad = 30, t0 = b[0].t, t1 = b[b.length - 1].t + 3600;
  const max = Math.max(...b.map((x) => x.posts));
  const x = (t) => pad + ((t - t0) / (t1 - t0)) * (W - 2 * pad);
  const y = (v) => H - pad - (v / max) * (H - 2 * pad);
  const bw = (W - 2 * pad) / ((t1 - t0) / 3600) - 2;
  const col = { coordination: "#ff5d5d", debunk_match: "#ff9f43", trending: "#4c9aff" };
  $("#act-chart").innerHTML = `<svg viewBox="0 0 ${W} ${H}">
    ${b.map((d) => `<rect x="${x(d.t)}" y="${y(d.posts)}" width="${bw}" height="${H - pad - y(d.posts)}" fill="#4c9aff" opacity=".4"><title>${fmt(d.t)}: ${d.posts}</title></rect>`).join("")}
    ${a.alerts.map((al) => `<line x1="${x(al.t)}" x2="${x(al.t)}" y1="${pad}" y2="${H - pad}" stroke="${col[al.type] || "#888"}" stroke-width="2"><title>#${al.id} ${esc(al.title || "")}</title></line>`).join("")}
    <text x="${pad}" y="${H - 8}" fill="#8b96a5" font-size="11">${fmt(t0)}</text>
    <text x="${W - pad}" y="${H - 8}" fill="#8b96a5" font-size="11" text-anchor="end">${fmt(t1)}</text>
    <text x="${pad}" y="16" fill="#8b96a5" font-size="11">max ${max} posts/hour · lines = alerts (red coordination, orange debunk match, blue trending)</text></svg>`;
  $("#act-alerts").innerHTML = `<table><tr><th>#</th><th>Raised</th><th>Type</th><th>Severity</th><th>Narrative</th></tr>${a.alerts.map((al) =>
    `<tr><td>${al.id}</td><td class="nowrap">${fmt(al.t)}</td><td>${TYPE[al.type] || al.type}</td><td><span class="pill sev-${al.severity}">${al.severity}</span></td><td>${esc(al.title || "")}</td></tr>`).join("")}</table>`;
}

// ------------------------------------------------------------------ settings page
async function loadSettings() {
  state.settings = await api("/api/settings");
  renderSettings();
}
function renderSettings() {
  const s = state.settings;
  const rows = (list, kind) => list.map((x, i) => `<div class="srow">
      <input type="checkbox" data-kind="${kind}" data-i="${i}" ${x.enabled ? "checked" : ""} style="width:auto">
      <b>${esc(x.name)}</b><span class="tag">${esc(x.type)}</span>${x.credibility ? `<span class="tag">${esc(x.credibility)}</span>` : ""}
      <span class="grow muted">${esc(x.url || "(no URL set — add one to enable)")}</span>
      <button class="btn" data-del="${kind}" data-i="${i}">Remove</button></div>`).join("");
  $("#set-sources").innerHTML = rows(s.sources || [], "sources");
  $("#set-cyber").innerHTML = rows(s.cyber_sources || [], "cyber_sources");
  $("#set-fc").innerHTML = rows(s.factcheckers || [], "factcheckers");
  $("#set-keywords").value = (s.focus?.keywords || []).join(", ");
  $("#set-regions").value = (s.focus?.regions || []).join(", ");
  document.querySelectorAll('.srow input[type="checkbox"]').forEach((c) => c.onchange = () => { s[c.dataset.kind][+c.dataset.i].enabled = c.checked; });
  document.querySelectorAll("[data-del]").forEach((b) => b.onclick = () => { s[b.dataset.del].splice(+b.dataset.i, 1); renderSettings(); });
}
$("#src-add").onclick = () => {
  const name = $("#src-name").value.trim(); if (!name) return toast("Name required");
  state.settings.sources.push({ name, type: $("#src-type").value, url: $("#src-url").value.trim(), credibility: $("#src-cred").value, enabled: true });
  renderSettings();
};
$("#cy-add").onclick = () => {
  const name = $("#cy-name").value.trim(); if (!name) return toast("Name required");
  (state.settings.cyber_sources ||= []).push({ name, type: $("#cy-type").value, url: $("#cy-url").value.trim(), enabled: true });
  renderSettings();
};
$("#fc-add").onclick = () => {
  const name = $("#fc-name").value.trim(); if (!name) return toast("Name required");
  state.settings.factcheckers.push({ name, type: $("#fc-type").value, url: $("#fc-url").value.trim(), enabled: true });
  renderSettings();
};
$("#save-settings").onclick = async () => {
  const s = state.settings;
  s.focus = { keywords: $("#set-keywords").value.split(",").map((x) => x.trim()).filter(Boolean), regions: $("#set-regions").value.split(",").map((x) => x.trim()).filter(Boolean) };
  await api("/api/settings", { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(s) });
  $("#save-msg").textContent = "Saved."; toast("Settings saved");
};

// ------------------------------------------------------------------ cyber view
const CY_STATUS = { unverified: "Unverified", partly_confirmed: "Partly confirmed", confirmed: "Confirmed", refuted: "Refuted" };
const CY_FOLDERS = [["all", "All"], ["open", "Open claims"], ["confirmed", "Confirmed"], ["refuted", "Refuted"]];
const KIND_ICON = { ddos_claim: "⇶", ransomware_claim: "🔒", defacement: "✎", data_breach_claim: "⇪", outage: "⏻", vulnerability: "⚠" };
const SECTOR = { government: "Government", energy: "Energy", telecom: "Telecom", finance: "Finance", health: "Health", other: "Other" };
// Admiralty code as used here: reliability from the source type, credibility from the checks
const REL = { A: "completely reliable (official CERT / CISA)", B: "usually reliable (official body, established media, measurement)",
  C: "fairly reliable (news outlet)", D: "not usually reliable (aggregator of criminal listings)", E: "unreliable (self-interested claimant)",
  F: "reliability cannot be judged" };
const CRED = { 1: "confirmed by several independent sources", 2: "confirmed by technical and independent checks",
  3: "partly confirmed", 4: "not verified yet", 5: "not verified yet, low-reliability source", 6: "contradicted by the checks" };
// events (reports of what happened) use corroboration wording; cyber claims use verification wording
const EV_CRED = { 1: "confirmed by independent sources, including an official statement", 2: "reported by several independent credible sources",
  3: "reported by one credible source", 4: "only social media, many accounts", 5: "only a few social media accounts" };
const admiraltyText = (code, ev) => code ? `Admiralty ${code}: source ${code[0]} = ${REL[code[0]] || "?"}; information ${code.slice(1)} = ${(ev ? EV_CRED : CRED)[code.slice(1)] || "?"}` : "";
const RESULT = { supports: ["✓", "sup", "supports the claim"], contradicts: ["✗", "con", "contradicts the claim"],
  inconclusive: ["?", "inc", "inconclusive"], error: ["!", "err", "check failed"] };
Object.assign(state, { cyFolder: "all", cySel: null, cyEvents: [] });

async function loadCyber() {
  try {
    const res = await api(`/api/cyber/events?folder=${state.cyFolder}${state.cyHours ? `&hours=${state.cyHours}` : ""}`);
    state.cyEvents = res.events;
    $("#cy-folders").innerHTML = CY_FOLDERS.map(([k, n]) =>
      `<button class="folder ${k === state.cyFolder ? "active" : ""}" data-f="${k}">${n}<span class="n">${res.counts[k] || ""}</span></button>`).join("") +
      `<button class="folder ${state.cySel ? "" : "active"}" id="cy-sites" title="Monitored sites: up / slow / down">◱ Sites</button>`;
    document.querySelectorAll("#cy-folders .folder[data-f]").forEach((b) => b.onclick = () => { state.cyFolder = b.dataset.f; state.cyHours = null; loadCyber(); });
    $("#cy-sites").onclick = () => { state.cySel = null; renderCyberList(); renderSites(); loadCyber(); };
    const fb = $("#cy-filterbar");
    fb.classList.toggle("hidden", !state.cyHours);
    if (state.cyHours) {
      fb.innerHTML = `<span class="muted">Filtered from Overview:</span> <span class="tag">${CY_FOLDERS.find(([k]) => k === state.cyFolder)[1]}</span>
        <span class="tag">claimed in the last ${state.cyHours} h</span> <button class="btn small-btn" id="cy-filter-clear">✕ Clear</button>`;
      $("#cy-filter-clear").onclick = () => { state.cyHours = null; state.cyFolder = "all"; loadCyber(); };
    }
    renderCyberList();
    if (!state.cySel) renderSites();
  } catch (e) { console.error(e); }
}

function renderCyberList() {
  const el = $("#cy-list");
  if (!state.cyEvents.length) { el.innerHTML = `<div class="empty">${state.cyHours ? `No cyber claim of this kind in the last ${state.cyHours} h.` : "No cyber events here."}</div>`; return; }
  el.innerHTML = state.cyEvents.map((e) => `
    <div class="row cyrow ${e.id === state.cySel ? "sel" : ""}" data-id="${e.id}">
      <div class="bar sev-${e.severity}"></div>
      <div class="cy-icon" title="${esc(e.kind_label)}">${KIND_ICON[e.kind] || "•"}</div>
      <div class="main">
        <div class="title" title="${esc(e.title)}">${esc(e.title)}</div>
        <div class="meta">
          <span class="cpill st-${e.status}">${CY_STATUS[e.status]}</span>
          <span class="adm" title="${esc(admiraltyText(e.admiralty))}">${esc(e.admiralty)}</span>
          <span>${esc(SECTOR[e.sector] || e.sector)}</span>
          ${e.claimed_actor ? `<span title="claimed actor">${esc(e.claimed_actor.replace(" (synthetic)", ""))}</span>` : ""}
          ${e.linked ? '<span class="tag hyb">hybrid</span>' : ""}
          <span class="when">${fmt(e.claimed_at)}</span>
        </div>
      </div>
    </div>`).join("");
  el.querySelectorAll(".row").forEach((r) => r.onclick = () => selectCyber(+r.dataset.id));
}

async function selectCyber(id) {
  state.cySel = id;
  renderCyberList();
  const e = await api(`/api/cyber/events/${id}`);
  if (state.cySel !== id) return;
  renderCyberDetail(e);
  $("#cy-detail").scrollTop = 0;
}

function renderCyberDetail(e) {
  const d = e.details || {};
  const facts = [["Kind", e.kind_label], ["Target", e.target], ["Sector", SECTOR[e.sector] || e.sector],
    ["Claimed actor", e.claimed_actor], ["Claimed at", fmt(e.claimed_at)], ["First seen", fmt(e.first_seen)],
    ["Domain", d.domain], ["ASN", d.asn], ["CVE", d.cve], ["Product", d.vendor && `${d.vendor} ${d.product || ""}`],
    ["Country", d.country], ["Description", d.description]].filter(([, v]) => v);
  $("#cy-detail").innerHTML = `
    <div class="dsec">
      <div class="title-row">
        <span class="pill sev-${e.severity}">${e.severity}</span>
        <span class="cpill st-${e.status}">${CY_STATUS[e.status]}</span>
        <span class="adm big" title="${esc(admiraltyText(e.admiralty))}">Admiralty ${esc(e.admiralty)}</span>
        <span class="tag" title="0-1, derived from source reliability and information credibility">confidence ${Math.round(e.confidence * 100)}%</span>
        ${e.synthetic ? '<span class="tag muted-tag">synthetic demo event</span>' : ""}
      </div>
      <h2 class="dtitle"><span class="cy-icon inline">${KIND_ICON[e.kind] || ""}</span> ${esc(e.title)}</h2>
      <div class="muted small">${esc(admiraltyText(e.admiralty))}</div>
    </div>
    ${threatHTML(e.threat, e.severity)}
    <div class="dsec"><div class="verdict cyb-${e.status}">${esc(e.summary)}</div>
      ${["unverified", "partly_confirmed"].includes(e.status) ? `<div class="muted small">Re-checked automatically until ${fmt(e.recheck_until)}.</div>` : ""}</div>
    ${e.alerts.length ? `<div class="dsec linked-cyber"><h3>Linked disinformation alerts — hybrid threat</h3>
      ${e.alerts.map((a) => `<div class="cy-card" data-goto="${a.id}">
        <span class="pill sev-${a.severity}">${a.severity}</span>
        <div class="grow"><b>#${a.id} ${esc(a.title)}</b><div class="muted">${esc(a.reason)}</div></div>
        <div class="cy-side"><span class="muted">link score ${Math.round(a.score * 100)}%</span></div></div>`).join("")}</div>` : ""}
    ${realityHTML(e)}
    <div class="dsec"><h3>Verification</h3>
      <div class="checks">${e.checks.map((c) => {
        const [mark, cls, word] = RESULT[c.result] || ["•", "", c.result];
        return `<div class="check ${cls}">
          <div class="mark" title="${word}">${mark}</div>
          <div class="grow"><b>${esc(c.label)}</b> <span class="tag cat">${c.category}</span>
            <span class="tag muted-tag" title="where the result came from">${c.origin === "own_data" ? "our data" : esc(c.origin || "")}</span>
            <div>${esc(c.detail || "")}</div>
            ${c.evidence_url ? `<a href="${esc(c.evidence_url)}" target="_blank" rel="noopener">evidence →</a>` : ""}</div>
          <div class="when">${fmt(c.checked_at)}</div></div>`;
      }).join("") || '<div class="muted">No checks yet.</div>'}</div>
      <div class="legend"><span>✓ supports the claim</span><span>✗ contradicts it</span><span>? inconclusive</span><span>! check failed</span>
        <span>technical = measured · independent = other sources · context = background</span></div>
    </div>
    <div class="dsec"><h3>Timeline: claim vs checks</h3>${cyberTimeline(e)}</div>
    <div class="dsec"><h3>Details</h3><table>${facts.map(([k, v]) => `<tr><th>${k}</th><td>${esc(v)}</td></tr>`).join("")}</table></div>
    <div class="dsec"><h3>Sources</h3><table><tr><th>Role</th><th>Source</th><th>Type</th><th>Link</th></tr>
      ${e.sources.map((s) => `<tr><td>${esc(s.role)}</td><td>${esc(s.name || "—")}${s.reliability ? ` <span class="adm" title="${esc(REL[s.reliability] || "")}">${s.reliability}</span>` : ""}</td>
        <td>${esc(s.type)}</td><td>${s.url ? `<a href="${esc(s.url)}" target="_blank" rel="noopener">${esc(s.url.replace(/^https?:\/\//, "").slice(0, 60))}</a>` : "—"}</td></tr>`).join("")}</table></div>`;
  $("#cy-detail").__hw = null;   // the sites grid uses HW.put on this pane
  bindGoto($("#cy-detail"));
  drawReality(e);
}

// claim, first seen, each check (dot by result, lane by category) and the verdict
function cyberTimeline(e) {
  const marks = [e.claimed_at, e.first_seen, e.verdict_at, ...e.checks.map((c) => c.checked_at)].filter(Boolean);
  let t0 = Math.min(...marks), t1 = Math.max(...marks);
  const pad = Math.max(600, (t1 - t0) * 0.08); t0 -= pad; t1 += pad;
  const W = 760, H = 150, L = 92, R = 16;
  const x = (t) => L + ((t - t0) / (t1 - t0)) * (W - L - R);
  const lanes = { technical: 52, independent: 82, context: 112 };
  const col = { supports: "#4c9aff", contradicts: "#ff9f43", inconclusive: "#8b96a5", error: "#ff5d5d" };
  const vline = (t, label, color, row) => t ? `<line x1="${x(t)}" x2="${x(t)}" y1="${18 + row * 12}" y2="${H - 22}" stroke="${color}" stroke-dasharray="4 3"/>
    <text x="${x(t)}" y="${12 + row * 12}" fill="${color}" font-size="10" font-weight="600" text-anchor="${x(t) > W - 100 ? "end" : "middle"}">${label} ${fmtTime(t)}</text>` : "";
  const dots = e.checks.map((c) => `<circle cx="${x(c.checked_at)}" cy="${lanes[c.category]}" r="6" fill="${col[c.result]}"><title>${esc(c.label)}: ${esc(c.detail || "")} (${fmt(c.checked_at)})</title></circle>`).join("");
  const span = (t1 - t0) / 3600, step = [0.25, 0.5, 1, 2, 3, 6, 12, 24].find((h) => span / h <= 6) * 3600;
  let ticks = "";
  for (let t = Math.ceil(t0 / step) * step; t <= t1; t += step)
    ticks += `<text x="${x(t)}" y="${H - 6}" fill="#8b96a5" font-size="10" text-anchor="middle">${fmtTime(t)}</text>`;
  const vcolor = { confirmed: "#ff5d5d", refuted: "#3ccf8e", partly_confirmed: "#ff9f43", unverified: "#8b96a5" }[e.status];
  return `<div class="chart"><svg viewBox="0 0 ${W} ${H}">
    ${Object.entries(lanes).map(([k, y]) => `<text x="8" y="${y + 4}" fill="#8b96a5" font-size="10">${k}</text><line x1="${L}" x2="${W - R}" y1="${y}" y2="${y}" stroke="#2a323d"/>`).join("")}
    ${vline(e.claimed_at, "Claim", "#f5d547", 0)}${vline(e.verdict_at, CY_STATUS[e.status], vcolor, 1)}
    ${dots}${ticks}</svg></div>
    <div class="legend"><span><i class="sw" style="background:#4c9aff"></i>supports</span><span><i class="sw" style="background:#ff9f43"></i>contradicts</span>
      <span><i class="sw" style="background:#8b96a5"></i>inconclusive</span><span><i class="ln" style="border-color:#f5d547"></i>claim</span>
      <span><i class="ln" style="border-color:${vcolor}"></i>verdict</span></div>`;
}

// ------------------------------------------------------------------ categories (events)
const CAT = {
  // colours avoid the four level colours (Info grey, Watch blue, Threat orange, Critical red) and were checked with the
  // dataviz palette validator on the dark surface (stack order below: adjacent CVD ΔE 9.9, normal vision 19.3).
  // Category is never colour alone: the icon and the name go with it.
  security_incident: { label: "Security incident", short: "Security", color: "#c98500", icon: "✸" },
  organized_activity: { label: "Organized activity", short: "Organized", color: "#d55181", icon: "⚑" },
  infrastructure: { label: "Infrastructure disruption", short: "Infrastructure", color: "#0ca30c", icon: "⚡" },
  regional: { label: "Regional development", short: "Regional", color: "#9085e9", icon: "◉" },
  disinformation: { label: "Disinformation", short: "Disinformation", color: "#199e70", icon: "❝" },
  cyber: { label: "Cyber incident", short: "Cyber", color: "#2dd4bf", icon: "⌁" },
};
const EV_STATUS = { developing: "Developing", ongoing: "Ongoing", quiet: "Quiet", ...CY_STATUS };
const catChip = (c) => `<span class="catchip" style="--c:${CAT[c].color}"><b>${CAT[c].icon}</b>${CAT[c].label}</span>`;
const sevRank = { info: 0, watch: 1, threat: 2, critical: 3, low: 0, medium: 1, high: 2 };

// ------------------------------------------------------------------ situation picture
Object.assign(state, { sitHours: 48, map: null, mapLayer: null, evCat: "all", evSel: null, events: [] });
document.querySelectorAll("#sit-window button").forEach((b) => b.onclick = () => {
  state.sitHours = +b.dataset.h;
  document.querySelectorAll("#sit-window button").forEach((x) => x.classList.toggle("on", x === b));
  loadSituation();
});
$("#brief-copy").onclick = () => {
  navigator.clipboard?.writeText($("#sit-brief").innerText).then(() => toast("Brief copied"), () => toast("Copy not available"));
};

async function loadSituation() {
  let s;
  try { s = await api(`/api/situation?hours=${state.sitHours}`); } catch (e) { return console.error(e); }
  $("#sit-sub").textContent = `Data to ${fmt(s.now)} · ${plural(s.kpis.posts, "post")} from ${plural(s.kpis.sources, "source")} · ` +
    `${plural(s.kpis.events, "event")}, ${s.kpis.critical} critical, ${s.kpis.high} high` + (s.kpis.hybrid ? ` · ${s.kpis.hybrid} hybrid` : "");
  $("#sit-kpis").innerHTML = s.tiles.map((t) => `
    <button class="kpi" style="--c:${CAT[t.category].color}" data-cat="${t.category}" title="${esc(t.title || "")}">
      <div class="kpi-top"><span class="kpi-ic">${CAT[t.category].icon}</span>${t.top ? `<span class="pill sev-${t.top}">${t.top}</span>` : ""}</div>
      <div class="kpi-v">${t.count}</div><div class="kpi-k">${CAT[t.category].label}</div>
    </button>`).join("");
  document.querySelectorAll("#sit-kpis .kpi").forEach((b) => b.onclick = () => { state.evCat = b.dataset.cat; showView("events"); });
  $("#sit-priority").innerHTML = s.priority.map((e) => eventRowHTML(e, true)).join("") || '<div class="empty">No events in this window.</div>';
  bindRows($("#sit-priority"));
  $("#sit-brief").innerHTML = s.brief.map((l, i) => `<p class="${i < 2 ? "lead-line" : ""}">${esc(l)}</p>`).join("");
  $("#sit-lanes").innerHTML = lanesSVG(s);
  bindGoto($("#sit-lanes"));
  renderMap(s.points);
}

function eventRowHTML(e, compact) {
  const c = CAT[e.category];
  return `<div class="erow ${state.evSel === e.id ? "sel" : ""}" data-id="${e.id}">
    <div class="eic" style="--c:${c.color}">${c.icon}</div>
    <div class="main">
      <div class="title" title="${esc(e.title)}">${esc(e.title)}</div>
      <div class="meta">
        <span class="pill sev-${e.severity}">${e.severity}</span>
        <span class="cname" style="color:${c.color}">${compact ? c.short : c.label}</span>
        ${e.place ? `<span>📍 ${esc(e.place)}</span>` : ""}
        ${e.admiralty ? `<span class="adm" title="${esc(admiraltyText(e.admiralty, e.kind === "event"))}">${e.admiralty}</span>` : ""}
        ${e.hybrid ? '<span class="tag hyb">hybrid</span>' : ""}
        <span class="st st-${e.status}">${EV_STATUS[e.status] || e.status}</span>
        <span class="when">${fmt(e.last_ts)}</span>
      </div>
    </div></div>`;
}
function bindRows(root) { root.querySelectorAll(".erow").forEach((r) => r.onclick = () => openEvent(r.dataset.id)); }

function renderMap(points) {
  const el = $("#map");
  if (!window.L) {   // no internet: the CDN map library did not load
    el.innerHTML = '<div class="empty">Map needs an internet connection (map tiles). Events are listed on the right.</div>';
    return;
  }
  if (!state.map) {
    state.map = L.map(el, { zoomControl: true, attributionControl: true, scrollWheelZoom: false }).setView([42.62, 20.9], 8);
    // OpenStreetMap tiles, darkened with a CSS filter (.dark-tiles) to match the console
    L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
      maxZoom: 13, className: "dark-tiles",
      attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
    }).addTo(state.map);
    state.mapLayer = L.layerGroup().addTo(state.map);
  }
  setTimeout(() => state.map.invalidateSize(), 50);
  state.mapLayer.clearLayers();
  const seen = {};
  let outside = 0;
  points.forEach((p) => {
    const k = `${p.lat},${p.lon}`; const n = seen[k] = (seen[k] || 0) + 1;   // spread events at the same place
    const ang = n * 2.1, off = n > 1 ? 0.035 * Math.sqrt(n) : 0;
    const ll = [p.lat + off * Math.sin(ang), p.lon + off * Math.cos(ang)];
    if (p.lat > 43.35 || p.lat < 41.8 || p.lon < 19.9 || p.lon > 21.9) outside++;
    const c = CAT[p.category];
    L.circleMarker(ll, { radius: 7 + sevRank[p.severity] * 3, color: c.color, weight: 2, fillColor: c.color, fillOpacity: 0.35 })
      .bindTooltip(`<b>${esc(p.title)}</b><br>${c.label} · ${p.severity} · ${esc(p.place)}<br>${fmt(p.last_ts)}`)
      .on("click", () => openEvent(p.id)).addTo(state.mapLayer);
  });
  $("#map-note").textContent = outside ? ` · ${outside} regional event(s) outside Kosovo: zoom out` : "";
  $("#map-legend").innerHTML = Object.values(CAT).map((c) => `<span><i class="sw" style="background:${c.color}"></i>${c.short}</span>`).join("") +
    "<span>circle size = severity</span>";
}

function lanesSVG(s) {
  const W = 640, row = 30, L0 = 104, R = 10, H = row * 6 + 26;
  const t1 = s.now, t0 = s.now - s.hours * 3600;
  const x = (t) => L0 + ((Math.max(t, t0) - t0) / (t1 - t0)) * (W - L0 - R);
  let body = "";
  Object.keys(CAT).forEach((k, i) => {
    const y = 8 + i * row;
    body += `<text x="4" y="${y + 15}" fill="${CAT[k].color}" font-size="11">${CAT[k].icon} ${CAT[k].short}</text>
      <line x1="${L0}" x2="${W - R}" y1="${y + 11}" y2="${y + 11}" stroke="#2a323d"/>`;
    (s.lanes[k] || []).forEach((e) => {
      const a = x(e.t0), b = Math.max(x(e.t1), a + 6);
      body += `<rect data-event="${e.id}" x="${a}" y="${y + 3}" width="${b - a}" height="16" rx="4" fill="${CAT[k].color}"
        opacity="${0.35 + sevRank[e.severity] * 0.2}" style="cursor:pointer"><title>${esc(e.title)} (${e.severity})</title></rect>`;
    });
  });
  const step = s.hours <= 24 ? 6 : s.hours <= 48 ? 12 : 24;
  for (let t = Math.ceil(t0 / (step * 3600)) * step * 3600; t <= t1; t += step * 3600)
    body += `<text x="${x(t)}" y="${H - 4}" fill="#8b96a5" font-size="10" text-anchor="middle">${new Date(t * 1000).getUTCHours() ? fmtTime(t) : fmt(t).split(",")[0]}</text>`;
  return `<div class="chart"><svg viewBox="0 0 ${W} ${H}">${body}</svg></div><div class="muted small">Each bar is one event, from first to last report. Click to open.</div>`;
}

// ------------------------------------------------------------------ events view
async function loadEvents() {
  let res;
  try { res = await api(`/api/events?category=${state.evCat}&hours=168`); } catch (e) { return console.error(e); }
  state.events = res.events;
  $("#ev-folders").innerHTML = [["all", "All", "#8b96a5"], ...Object.entries(CAT).map(([k, c]) => [k, c.short, c.color])]
    .map(([k, n, col]) => `<button class="folder ${k === state.evCat ? "active" : ""}" data-f="${k}" style="--c:${col}">${k !== "all" ? `<i class="dot"></i>` : ""}${n}<span class="n">${res.counts[k] || ""}</span></button>`).join("");
  document.querySelectorAll("#ev-folders .folder").forEach((b) => b.onclick = () => { state.evCat = b.dataset.f; loadEvents(); });
  $("#ev-list").innerHTML = state.events.map((e) => eventRowHTML(e, false)).join("") || '<div class="empty">No events in this category.</div>';
  bindRows($("#ev-list"));
}

async function selectEvent(id) {
  state.evSel = `e${id}`;
  document.querySelectorAll("#ev-list .erow").forEach((r) => r.classList.toggle("sel", r.dataset.id === state.evSel));
  let e;
  try { e = await api(`/api/events/${id}`); } catch (err) { $("#ev-detail").innerHTML = '<div class="empty">This event was merged or no longer exists.</div>'; return; }
  if (state.evSel !== `e${id}`) return;
  renderEventDetail(e);
  $("#ev-detail").scrollTop = 0;
}

function renderEventDetail(e) {
  const c = CAT[e.category];
  const langs = Object.entries(e.langs || {}).sort((a, b) => b[1] - a[1]).map(([k, v]) => `${LANG[k] || k} ${v}`).join(" · ");
  const card = (attrs, side, body) => `<div class="ctx-card" ${attrs}>${side}<div class="grow">${body}</div></div>`;
  $("#ev-detail").innerHTML = `
    <div class="dsec ev-head" style="--c:${c.color}">
      <div class="title-row">${catChip(e.category)}<span class="pill sev-${e.severity}">${e.severity}</span>
        <span class="st st-${e.status}">${EV_STATUS[e.status] || e.status}</span>
        ${e.admiralty ? `<span class="adm big" title="${esc(admiraltyText(e.admiralty, true))}">Admiralty ${e.admiralty}</span>` : ""}
        <span class="tag" title="0-1, from how many independent and credible sources report it">confidence ${Math.round((e.confidence || 0) * 100)}%</span></div>
      <h2 class="dtitle">${esc(e.title)}</h2>
      <div class="ev-facts">${e.place ? `<span>📍 <b>${esc(e.place)}</b></span>` : "<span>📍 place not identified</span>"}
        <span>First report <b>${fmt(e.first_ts)}</b></span><span>Latest <b>${fmt(e.last_ts)}</b></span>
        <span><b>${e.n_sources}</b> sources · <b>${e.n_credible}</b> credible · <b>${e.n_posts}</b> posts</span>${langs ? `<span>${esc(langs)}</span>` : ""}</div>
    </div>
    ${threatHTML(e.threat, e.severity)}
    <div class="dsec"><h3>What we know</h3><div class="verdict" style="border-left-color:${c.color}">${esc(e.summary)}</div>
      ${e.admiralty ? `<div class="muted small">${esc(admiraltyText(e.admiralty, true))}. Reports are attributed to their sources; HybridWatch does not state that a claim is true.</div>` : ""}</div>
    ${e.indicators.length ? `<div class="dsec"><h3>Indicators</h3><ul class="indicators">${e.indicators.map((i) => `<li>${esc(i)}</li>`).join("")}</ul></div>` : ""}
    <div class="dsec"><h3>Timeline</h3>${eventTimeline(e.timeline, c.color)}</div>
    ${e.alerts.length || e.cyber.length || e.related.length ? `<div class="dsec"><h3>Context: linked threats and related events</h3>
      ${e.alerts.map((a) => card(`data-goto="${a.id}"`, `<span class="eic" style="--c:${CAT.disinformation.color}">${CAT.disinformation.icon}</span>`,
        `<b>Disinformation · alert #${a.id}</b> <span class="pill sev-${a.severity}">${a.severity}</span><div>${esc(a.title)}</div><div class="muted small">${esc(a.summary)}</div>`)).join("")}
      ${e.cyber.map((y) => card(`data-cyber="${y.id}"`, `<span class="eic" style="--c:${CAT.cyber.color}">${CAT.cyber.icon}</span>`,
        `<b>Cyber event #${y.id}</b> <span class="cpill st-${y.status}">${CY_STATUS[y.status]}</span> <span class="adm">${y.admiralty}</span><div>${esc(y.title)}</div><div class="muted small">${esc(y.reason)}</div>`)).join("")}
      ${e.related.map((r) => card(`data-event="${r.id}"`, `<span class="eic" style="--c:${CAT[r.category].color}">${CAT[r.category].icon}</span>`,
        `<b>${CAT[r.category].label}</b> <span class="pill sev-${r.severity}">${r.severity}</span> <span class="muted">${esc(r.why)}</span><div>${esc(r.title)}</div>`)).join("")}
    </div>` : ""}
    <div class="dsec"><h3>Key reports</h3>${e.key_posts.map((p) => `<div class="ev">
      <div class="head"><b>${esc(src(p.source_type))} · ${esc(p.author || p.source)}</b><span>${fmt(p.ts)}</span>
        ${p.source_type === "official" ? '<span class="tag blue">official</span>' : p.credibility === "credible" ? '<span class="tag blue">credible outlet</span>' : '<span class="tag">social media</span>'}</div>
      <div class="txt">${esc(p.text)}</div>${p.translation ? `<div class="tr">EN: ${esc(p.translation)}</div>` : ""}
      <div class="head"><a href="${esc(p.url)}" target="_blank" rel="noopener">original</a></div></div>`).join("")}</div>
    <div class="dsec"><h3>Sources (${e.n_sources_total})</h3>
      <table><tr><th>Source</th><th>Type</th><th>Reliability</th><th>First post</th><th>Posts</th><th></th></tr>
      ${e.sources.map((s) => `<tr><td>${esc(s.source)}</td><td>${esc(src(s.type))}</td>
        <td><span class="adm" title="${esc(REL[s.reliability] || "")}">${s.reliability}</span></td><td class="nowrap">${fmt(s.first_ts)}</td>
        <td>${s.posts}</td><td>${s.url ? `<a href="${esc(s.url)}" target="_blank" rel="noopener">link</a>` : ""}</td></tr>`).join("")}</table>
      ${e.n_sources_total > e.sources.length ? `<div class="muted small">Showing the ${e.sources.length} most reliable of ${e.n_sources_total} sources.</div>` : ""}</div>`;
  bindGoto($("#ev-detail"));
}

const EV_MARK = { first: "#8b96a5", credible: "#3ccf8e", official: "#4c9aff", alert: "#f5d547", cyber: "#2dd4bf" };
function eventTimeline(tl, color) {
  if (!tl || !tl.bars.length) return '<div class="muted">No posts.</div>';
  const W = 760, H = 190, L0 = 34, R = 14, TOP = 58, BOT = H - 24;
  const t0 = tl.start, t1 = tl.end;
  const max = Math.max(...tl.bars.map((b) => b.posts));
  const x = (t) => L0 + ((t - t0) / (t1 - t0)) * (W - L0 - R);
  const y = (v) => BOT - (v / max) * (BOT - TOP);
  const bw = Math.max(2, ((W - L0 - R) * tl.bucket) / (t1 - t0) - 1);
  let body = tl.bars.map((b) => `<rect x="${x(b.t)}" y="${y(b.posts)}" width="${bw}" height="${BOT - y(b.posts)}" fill="${color}" opacity=".6"><title>${fmt(b.t)}: ${plural(b.posts, "post")}</title></rect>`).join("");
  let lastX = -1e9, rowi = 0;
  [...tl.markers].sort((a, b) => a.t - b.t).forEach((m) => {
    const mx = x(m.t); rowi = mx - lastX < 130 ? (rowi + 1) % 3 : 0; lastX = mx;
    const ly = 12 + rowi * 14, anchor = mx > W - 110 ? "end" : mx < L0 + 50 ? "start" : "middle";
    body += `<line x1="${mx}" x2="${mx}" y1="${ly + 3}" y2="${BOT}" stroke="${EV_MARK[m.kind]}" stroke-dasharray="4 3"/>
      <text x="${mx}" y="${ly}" fill="${EV_MARK[m.kind]}" font-size="10.5" font-weight="600" text-anchor="${anchor}">${esc(m.label)} · ${fmtTime(m.t)}</text>`;
  });
  const span = (t1 - t0) / 3600, step = [1, 2, 3, 6, 12, 24, 48].find((h) => span / h <= 7) * 3600;
  for (let t = Math.ceil(t0 / step) * step; t <= t1; t += step)
    body += `<text x="${x(t)}" y="${H - 6}" fill="#8b96a5" font-size="10" text-anchor="middle">${new Date(t * 1000).getUTCHours() ? fmtTime(t) : fmt(t).split(",")[0]}</text>`;
  return `<div class="chart"><svg viewBox="0 0 ${W} ${H}"><line x1="${L0}" x2="${W - R}" y1="${BOT}" y2="${BOT}" stroke="#2a323d"/>
    <text x="${L0 - 6}" y="${TOP + 4}" fill="#8b96a5" font-size="10" text-anchor="end">${max}</text>${body}</svg></div>
    <div class="legend"><span><i class="sw" style="background:${color}"></i>posts per ${duration(tl.bucket)}</span>
      ${Object.entries({ first: "first mention", credible: "first credible report", official: "official statement", alert: "alert raised", cyber: "cyber event" })
        .map(([k, n]) => `<span><i class="ln" style="border-color:${EV_MARK[k]}"></i>${n}</span>`).join("")}</div>`;
}

// ------------------------------------------------------------------ start
// deep links: /#alert-12, /#cyber-3, /#event-5
refresh().then(() => {
  const m = location.hash.match(/(alert|cyber|event)-(\d+)/);
  if (!m) return showView("overview");
  ({ alert: openAlert, cyber: openCyber, event: (i) => openEvent(`e${i}`) })[m[1]](+m[2]);
});
let tick = 0;
setInterval(() => {
  tick++;
  refresh();                                            // header, badges, inbox
  if (state.view === "cyber") loadCyber();
  if (state.view === "events" && tick % 3 === 0) loadEvents();
  if (state.view === "situation" && tick % 4 === 0) loadSituation();
}, 3000);
setInterval(() => { if (state.view === "overview" && !document.hidden) loadOverview(); }, 10000);
