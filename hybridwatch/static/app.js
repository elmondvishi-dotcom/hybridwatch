// HybridWatch analyst console. Plain JS, no build step.
const $ = (s, el = document) => el.querySelector(s);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const api = async (path, opts) => {
  const r = await fetch(path, opts);
  if (!r.ok) throw new Error(`${r.status} ${path}`);
  return r.json();
};
const post = (path, body) => api(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
const fmt = (ts) => ts ? new Date(ts * 1000).toISOString().slice(5, 16).replace("T", " ") : "—";
const TYPE = { coordination: "Coordination suspected", debunk_match: "Matches debunked claim", trending: "Trending" };
const FOLDERS = [["inbox", "Inbox"], ["review", "Needs review"], ["watching", "Watching"], ["confirmed", "Confirmed"], ["dismissed", "Dismissed"]];

const state = { view: "alerts", folder: "inbox", paused: false, snapshot: null, alerts: [], selected: null, detail: null, settings: null, undo: null };

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
document.querySelectorAll(".nav").forEach((b) => b.onclick = () => {
  document.querySelectorAll(".nav").forEach((x) => x.classList.toggle("active", x === b));
  state.view = b.dataset.view;
  document.querySelectorAll(".view").forEach((v) => v.classList.toggle("hidden", v.id !== `view-${state.view}`));
  if (state.view === "activity") loadActivity();
  if (state.view === "settings") loadSettings();
});

// ------------------------------------------------------------------ pause
$("#pause").onclick = () => {
  state.paused = !state.paused;
  state.snapshot = state.paused ? Date.now() / 1000 : null;
  $("#pause").textContent = state.paused ? "▶ Resume" : "⏸ Pause";
  const b = $("#livebadge");
  b.textContent = state.paused ? "❚❚ PAUSED — still monitoring" : "● LIVE";
  b.className = "badge " + (state.paused ? "live-off" : "live-on");
  refresh();
};
$("#pending").onclick = () => { state.snapshot = Date.now() / 1000; refresh(); };

// ------------------------------------------------------------------ list
async function refresh() {
  try {
    const st = await api("/api/status");
    $("#clock").textContent = `data time ${fmt(st.sim_now)} · ${st.posts.toLocaleString()} posts · ${st.sources} sources` +
      (st.worker_alive ? "" : " · ⚠ worker not running");
    renderFolders(st.folders);
    const q = state.paused ? `&snapshot=${state.snapshot}` : "";
    const res = await api(`/api/alerts?folder=${state.folder}${q}`);
    state.alerts = res.alerts;
    const p = $("#pending");
    p.classList.toggle("hidden", !res.pending);
    p.textContent = `${res.pending} new — show`;
    renderList();
  } catch (e) { console.error(e); }
}

function renderFolders(counts) {
  $("#folders").innerHTML = FOLDERS.map(([k, n]) =>
    `<button class="folder ${k === state.folder ? "active" : ""}" data-f="${k}">${n}<span class="n">${counts[k] || ""}</span></button>`).join("");
  document.querySelectorAll(".folder").forEach((b) => b.onclick = () => { state.folder = b.dataset.f; state.selected = null; refresh(); showEmpty(); });
}

function renderList() {
  const el = $("#list");
  if (!state.alerts.length) {
    el.innerHTML = `<div class="empty">${state.folder === "inbox" ? "All caught up. Still monitoring." : "Nothing here."}</div>`;
    return;
  }
  el.innerHTML = state.alerts.map((a) => `
    <div class="row ${a.unread ? "unread" : ""} ${a.id === state.selected ? "sel" : ""}" data-id="${a.id}">
      <div class="bar sev-${a.severity}"></div>
      <div class="main">
        <div class="title">${esc(a.label)}</div>
        <div class="meta">
          <span class="t-sev c-${a.severity}">${a.severity}</span>
          <span>${TYPE[a.type] || a.type}</span>
          <span>${a.posts} posts</span>
          <span>${a.growth >= 3 ? "↑↑" : a.growth >= 1.5 ? "↑" : "→"}</span>
          <span>${fmt(a.bumped_sim)}</span>
          ${a.previously_dismissed ? '<span class="tag orange">re-opened</span>' : ""}
          ${a.new_posts ? `<span class="tag blue">+${a.new_posts} new</span>` : ""}
        </div>
      </div>
    </div>`).join("");
  el.querySelectorAll(".row").forEach((r) => r.onclick = () => select(+r.dataset.id));
}

function showEmpty() { $("#detail").innerHTML = `<div class="empty">Select an alert.</div>`; }

// ------------------------------------------------------------------ detail
async function select(id) {
  state.selected = id;
  renderList();
  const d = await api(`/api/alerts/${id}`);
  state.detail = d;
  renderDetail(d);
}

function renderDetail(d) {
  const conf = d.confidence >= 0.75 ? "High" : d.confidence >= 0.5 ? "Medium" : "Low";
  const langs = Object.entries(d.stats.langs || {}).map(([k, v]) => `${k} ${v}`).join(" · ");
  $("#detail").innerHTML = `
    <div class="actions">
      <button class="btn" id="a-confirm">✓ Confirm</button>
      <button class="btn" id="a-dismiss">✕ Dismiss</button>
      <button class="btn" id="a-review">? Needs review</button>
      <button class="btn" id="a-watch">👁 Watch</button>
      <span class="status badge">${esc(d.status.replace("_", " "))}</span>
    </div>
    ${sinceLastHTML(d.since_last)}
    <div class="dsec">
      <h2 class="dtitle">${esc(d.label)} ${d.label_source === "llm" ? '<span class="tag ai">AI label</span>' : ""}</h2>
      <div class="chips">
        <span class="tag ${d.type === "coordination" ? "red" : d.type === "debunk_match" ? "orange" : "blue"}">${TYPE[d.type] || d.type}</span>
        <span class="tag">Severity: <b class="c-${d.severity}">${d.severity}</b></span>
        <span class="tag">Confidence: ${conf} (${d.confidence})</span>
        <span class="tag">${d.stats.size} posts · ${d.stats.accounts} accounts</span>
        <span class="tag">first seen ${fmt(d.stats.first_ts)}</span>
        <span class="tag">${esc(langs)}</span>
      </div>
    </div>
    <div class="dsec"><h3>Why flagged</h3>
      <ul class="why">${d.reasons.map((r) => `<li class="${r.startsWith("Previously") ? "prev" : ""}">${esc(r)}</li>`).join("")}</ul>
    </div>
    ${relatedHTML(d.related)}
    ${d.factcheck ? `<div class="dsec"><h3>Matched fact-check</h3><div class="fcbox">
      <b>${esc(d.factcheck.verdict)}</b> — ${esc(d.factcheck.source)} (${esc(d.factcheck.date)})<br>
      <span class="muted">${esc(d.factcheck.claim)}</span><br>
      ${d.factcheck.url && d.factcheck.url.startsWith("http") ? `<a href="${esc(d.factcheck.url)}" target="_blank" rel="noopener">Open fact-check →</a>` : ""}
    </div></div>` : ""}
    <div class="dsec"><h3>Timeline (posts per 30 min, copy-paste in red)</h3><div class="chart">${timelineSVG(d.timeline || [])}</div></div>
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
  document.querySelectorAll("#detail [data-goto]").forEach((el) => el.onclick = () => select(+el.dataset.goto));
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
      <div class="grow"><b>#${r.id} ${esc(r.label)}</b>
        <div class="muted">${r.shared} of this alert's ${r.of} coordinated accounts also pushed it
          <span class="tag ${r.status === "confirmed" ? "red" : ""}">${esc(STATUS[r.status] || r.status)}</span></div></div>
      <div class="pct">${Math.round(r.share * 100)}%<div class="muted">shared</div></div>
    </div>`).join("")}
    <p class="muted small">Shared accounts suggest one actor behind several campaigns. Evidence only — attribution is the analyst's call.</p>
  </div>`;
}

function evidenceHTML(p) {
  const src = `${esc(p.source_type)} · ${esc(p.author)}`;
  return `<div class="ev">
    <div class="head">${p.key ? '<span class="tag ai">★ key</span>' : ""}<b>${src}</b><span>${fmt(p.ts)}</span>
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

function timelineSVG(tl) {
  if (!tl.length) return "";
  const W = 700, H = 120, pad = 20;
  const t0 = tl[0].t, t1 = tl[tl.length - 1].t + 1800;
  const max = Math.max(...tl.map((b) => b.posts));
  const bw = Math.max(2, (W - pad * 2) / ((t1 - t0) / 1800) - 1);
  const x = (t) => pad + ((t - t0) / (t1 - t0)) * (W - pad * 2);
  const y = (v) => H - pad - (v / max) * (H - pad * 2);
  let bars = tl.map((b) => `<rect x="${x(b.t)}" y="${y(b.posts)}" width="${bw}" height="${H - pad - y(b.posts)}" fill="#4c9aff" opacity=".55"><title>${fmt(b.t)}: ${b.posts} posts</title></rect>` +
    (b.copy_paste ? `<rect x="${x(b.t)}" y="${y(b.copy_paste)}" width="${bw}" height="${H - pad - y(b.copy_paste)}" fill="#ff5d5d"/>` : "")).join("");
  return `<svg viewBox="0 0 ${W} ${H}"><line x1="${pad}" x2="${W - pad}" y1="${H - pad}" y2="${H - pad}" stroke="#2a323d"/>${bars}
    <text x="${pad}" y="${H - 4}" fill="#8b96a5" font-size="10">${fmt(t0)}</text>
    <text x="${W - pad}" y="${H - 4}" fill="#8b96a5" font-size="10" text-anchor="end">${fmt(t1)}</text>
    <text x="${pad}" y="12" fill="#8b96a5" font-size="10">max ${max}/30min</text></svg>`;
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
    ${aiField("severity", `<select id="f-severity" class="field-ai">${sel(["low", "medium", "high", "critical"], s.severity?.value)}</select>`, s)}
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
  const label = state.detail?.label || `#${id}`;
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
    [a.ai_prefill.confirmed ? `${a.ai_prefill.fields_changed} edits / ${a.ai_prefill.confirmed}` : "—", "analyst edits to AI pre-fill (per confirmed case)"],
  ].map(([v, k]) => `<div class="card"><div class="v">${v}</div><div class="k">${k}</div></div>`).join("");
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
    ${a.alerts.map((al) => `<line x1="${x(al.t)}" x2="${x(al.t)}" y1="${pad}" y2="${H - pad}" stroke="${col[al.type] || "#888"}" stroke-width="2"><title>#${al.id} ${esc(al.label || "")}</title></line>`).join("")}
    <text x="${pad}" y="${H - 8}" fill="#8b96a5" font-size="11">${fmt(t0)}</text>
    <text x="${W - pad}" y="${H - 8}" fill="#8b96a5" font-size="11" text-anchor="end">${fmt(t1)}</text>
    <text x="${pad}" y="16" fill="#8b96a5" font-size="11">max ${max} posts/hour · lines = alerts (red coordination, orange debunk match, blue trending)</text></svg>`;
  $("#act-alerts").innerHTML = `<table><tr><th>#</th><th>Raised</th><th>Type</th><th>Severity</th><th>Narrative</th></tr>${a.alerts.map((al) =>
    `<tr><td>${al.id}</td><td>${fmt(al.t)}</td><td>${TYPE[al.type] || al.type}</td><td class="c-${al.severity}">${al.severity}</td><td>${esc(al.label || "")}</td></tr>`).join("")}</table>`;
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

// deep link: /#alert-12 opens that alert
refresh().then(() => { const m = location.hash.match(/alert-(\d+)/); if (m) select(+m[1]); });
setInterval(() => { if (state.view === "alerts") refresh(); }, 3000);
