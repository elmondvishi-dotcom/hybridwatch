// HybridWatch visual layer: the Overview page, score breakdown, alert charts (spread, splits, network),
// monitored sites and the cyber "claim vs reality" chart. Uses charts.js (HW) and app.js helpers at call time.
// Charts show evidence, they decide nothing: wording is "suspected", "claimed", "confirmed by".

// ---------------------------------------------------------------- shared constants
const OV_HOURS = 24;
const LANG_COLOR = { sq: "#d55181", sr_latn: "#0ca30c", sr_cyrl: "#9085e9", en: "#c98500", other: "#56606c" };  // validated (adjacent CVD ΔE ≥ 8.4)
const PLATFORM_COLOR = "#94a3b8";                       // one series -> one neutral colour
const AGE = [[30, "#ff5d5d", "under 30 days (new)"], [365, "#f5d547", "30 days – 1 year"], [Infinity, "#7d8ea3", "over 1 year"]];
const AGE_UNKNOWN = "#3a4554";
const RING = ["#c084fc", "#2dd4bf", "#f5d547", "#f472b6"];
const MARK = { flagged: "#f5d547", coordination: "#e3e8ee", credible: "#3ccf8e", cyber: "#2dd4bf", analyst: "#f0abfc",
  critical: "#ff5d5d", hybrid: "#c084fc", claimed: "#f5d547", first_seen: "#8b96a5", verdict: "#e3e8ee" };
const SITE = { up: ["#3ccf8e", "●", "UP"], slow: ["#f5d547", "◐", "SLOW"], down: ["#ff5d5d", "✕", "DOWN"], unknown: ["#6c7a89", "○", "NO DATA"] };
const TREND_ARROW = { up2: "⇈", up: "↑", flat: "→", down: "↓" };
const lvPill = (lv) => `<span class="pill sev-${lv}">${HW.LV_NAME[lv] || lv}</span>`;
const lvSwatch = (lv) => `<i class="sw" style="background:${HW.LV[lv]}"></i>`;

// ---------------------------------------------------------------- list filters opened from the Overview
Object.assign(state, { filter: null, cyHours: null, ovData: null, geo: null });
const FILTER_WORD = { level: (v) => HW.LV_NAME[v], category: (v) => CAT[v].label, place: (v, f) => v === "none" ? "Not located" : (f.placeName || v),
  hours: (v) => `active in the last ${v} h` };
function openAlertsFiltered(f) {
  state.filter = f; state.folder = "active"; state.selected = null; state.detail = null;
  if (state.view !== "alerts") showView("alerts"); else { refresh(); }
}
function clearFilter() { state.filter = null; state.folder = "inbox"; refresh(); showEmpty(); }
function filterQuery() {
  const f = state.filter;
  if (!f) return "";
  return ["level", "category", "place", "hours"].filter((k) => f[k]).map((k) => `&${k}=${encodeURIComponent(f[k])}`).join("");
}
function renderFilterBar() {
  const el = $("#filterbar"), f = state.filter;
  el.classList.toggle("hidden", !f);
  if (!f) return;
  const parts = ["level", "category", "place", "hours"].filter((k) => f[k]).map((k) => `<span class="tag">${esc(FILTER_WORD[k](f[k], f))}</span>`);
  el.innerHTML = `<span class="muted">Filtered from Overview:</span> ${parts.join(" ")}
    <span class="muted small-n">All open, watched and confirmed alerts at Watch or above.</span>
    <button class="btn small-btn" id="filter-clear">✕ Clear</button>`;
  $("#filter-clear").onclick = clearFilter;
}
function openCyberFiltered(folder, hours) {
  state.cyFolder = folder; state.cyHours = hours; state.cySel = null;
  if (state.view !== "cyber") showView("cyber"); else loadCyber();
}

// ---------------------------------------------------------------- 8a score breakdown (replaces the dot rows)
function scoreBreakdownHTML(t, level) {
  const th = t.thresholds || { watch: 25, threat: 50, critical: 75 };
  const score = Math.round(t.score);
  const band = score >= th.critical ? "critical" : score >= th.threat ? "threat" : score >= th.watch ? "watch" : "info";
  const rows = Object.keys(FACTOR).map((k) => {
    const f = t.factors[k] || { value: 1, reason: "" };
    const w = f.weight != null ? `×${f.weight.toFixed(2)}` : "";
    const pts = f.points != null ? `+${f.points % 1 ? f.points.toFixed(1) : f.points} pts` : "";
    const tipHtml = `<b>${FACTOR[k]}: ${f.value}/5</b>${f.weight != null ? ` · weight ${f.weight} · ${pts}` : ""}<br>${esc(f.reason || "—")}`;
    return `<div class="fb" ${HW.tip(tipHtml)}>
      <span class="fb-k">${FACTOR[k]} <span class="muted">${w}</span></span>
      <span class="fb-track"><span class="fb-fill" style="width:${(f.value / 5) * 100}%;background:${HW.LV[level] || HW.LV.info}"></span>
        ${[1, 2, 3, 4].map((i) => `<i style="left:${i * 20}%"></i>`).join("")}</span>
      <span class="fb-v"><b>${f.value}</b>/5 <span class="muted">${pts}</span></span>
      <span class="fb-r">${esc(f.reason || "")}</span></div>`;
  }).join("");
  const seg = (a, b, lv) => `<span style="left:${a}%;width:${b - a}%;background:${HW.LV[lv]}" ${HW.tip(`${HW.LV_NAME[lv]}: ${a}–${b === 100 ? 100 : b - 1}`)}></span>`;
  const capped = band !== level ? `<div class="fb-cap">Score band is ${HW.LV_NAME[band]}; level is ${HW.LV_NAME[level]}${t.why_not && t.why_not[0] ? `: ${esc(t.why_not[0])}` : ""}.</div>` : "";
  return `<div class="fb-head">
      <div class="fb-total"><span class="muted">Total score</span> <b>${score}</b><span class="muted">/100</span> → ${lvPill(level)}</div>
      <div class="fb-scale" aria-label="score scale">${seg(0, th.watch, "info")}${seg(th.watch, th.threat, "watch")}${seg(th.threat, th.critical, "threat")}${seg(th.critical, 100, "critical")}
        <b class="fb-mark" style="left:${score}%"></b></div>
      <div class="fb-ticks"><span style="left:0">0</span><span style="left:${th.watch}%">${th.watch}</span><span style="left:${th.threat}%">${th.threat}</span><span style="left:${th.critical}%">${th.critical}</span><span style="left:100%">100</span></div>
    </div>${capped}
    <div class="ch-s">Five factors, each 1–5. Bar = factor value; points = its share of the score (weight × value). Hover a bar for its reason.</div>
    <div class="fbs">${rows}</div>`;
}

// ---------------------------------------------------------------- 7 OVERVIEW
async function loadOverview(force) {
  const paused = state.paused && state.ovData && !force;
  $("#ov-paused").classList.toggle("hidden", !state.paused);
  if (state.paused) $("#ov-paused").textContent = `Paused: figures frozen since ${fmt(state.ovData?.now)} (data time). Monitoring continues. Press Resume to update.`;
  if (paused) return;
  let d;
  try { d = await api("/api/dashboard"); } catch (err) { console.error(err); return; }
  if (!state.geo) { try { state.geo = await api("/static/geo/kosovo-municipalities.geojson"); } catch (err) { state.geo = { features: [] }; } }
  if (state.paused && state.ovData && !force) return;
  state.ovData = d;
  renderOverviewPage(d);
}

function renderOverviewPage(d) {
  $("#ov-sub").textContent = `Data to ${fmt(d.now)} (UTC) · counters, map, matrix and Top 5: alerts active in the last ${OV_HOURS} h (posts since ${fmt(d.since)}) · refreshes every 10 s`;
  // (a) banner
  const b = d.banner;
  HW.put($("#ov-banner"), `<div class="ovb lvb-${b.level}">
    <span class="ovb-lv">${(HW.LV_NAME[b.level] || b.level).toUpperCase()}</span>
    <span class="ovb-t">${esc(b.text)}</span>
    ${b.alert_id ? `<button class="btn" data-goto="${b.alert_id}">Open alert #${b.alert_id} →</button>` : ""}</div>`);
  // (b) counters
  const c = d.counters;
  const ctr = (key, n, label, color, icon, hint) => `<button class="ctr" data-ctr="${key}" style="--c:${color}" ${HW.tip(`<b>${n} ${esc(label)}</b><br>${esc(hint)}<br>Click to open the list.`)}>
    <span class="ctr-ic">${icon}</span><span class="ctr-v">${n}</span><span class="ctr-k">${esc(label)}</span></button>`;
  HW.put($("#ov-counters"), [
    ctr("critical", c.critical, "Critical alerts", HW.LV.critical, "▲", "Alerts at Critical level with posts in the last 24 h"),
    ctr("threat", c.threat, "Threat alerts", HW.LV.threat, "■", "Alerts at Threat level with posts in the last 24 h"),
    ctr("watch", c.watch, "Watch alerts", HW.LV.watch, "●", "Alerts at Watch level with posts in the last 24 h"),
    ctr("cyber_confirmed", c.cyber_confirmed, "confirmed cyber incidents", CAT.cyber.color, "⌁", "Cyber claims made in the last 24 h that technical and independent checks support"),
    ctr("cyber_refuted", c.cyber_refuted, "refuted claims", "#3ccf8e", "✗", "Cyber claims made in the last 24 h that the checks contradict"),
  ].join(""));
  // (c) map, (f) top 5, (d) matrix, (e) week
  HW.draw($("#ov-map"), (W) => mapSVG(d, W));
  HW.put($("#ov-map-legend"), `${["watch", "threat", "critical"].map((l) => `<span>${lvSwatch(l)}${HW.LV_NAME[l]}</span>`).join("")}
    <span><i class="sw" style="background:#1c232c;border:1px solid #2a323d"></i>no active alert</span>
    <span>fill = highest level in the municipality · dot size = number of active alerts (<svg width="34" height="14"><circle cx="6" cy="7" r="4" fill="#8b96a5"/><circle cx="24" cy="7" r="6.5" fill="#8b96a5"/></svg> 1, 3) · click to filter</span>`);
  HW.put($("#ov-unloc"), unlocatedHTML(d.map));
  HW.put($("#ov-top"), topHTML(d.top));
  HW.put($("#ov-matrix"), matrixHTML(d.matrix));
  const wk = d.timeline, names = Object.fromEntries(Object.keys(CAT).map((k) => [k, CAT[k].label]));
  HW.draw($("#ov-week"), HW.timeStack({
    t0: wk.start, t1: wk.end, bucket: 86400, keys: wk.categories, colors: Object.fromEntries(Object.keys(CAT).map((k) => [k, CAT[k].color])),
    names, rows: wk.days, height: 236, yTitle: "Posts", xTitle: "Day (UTC, data time)", barInset: 6,
    ticks: wk.days.map((r) => ({ t: r.t + 43200, label: HW.dayLabel(r.t), major: false })),
    emptyText: "No data in this period: no posts in detected events in the last 7 days.",
    rowTip: (r) => { const tot = wk.categories.reduce((s, k) => s + r[k], 0);
      return `<b>${HW.dayLabel(r.t)}</b> · ${tot.toLocaleString()} posts<br>` + wk.categories.filter((k) => r[k]).map((k) => `${CAT[k].icon} ${esc(CAT[k].label)}: <b>${r[k].toLocaleString()}</b>`).join("<br>"); },
    markers: wk.markers.map((m) => ({ t: m.t, color: MARK[m.kind], glyph: m.kind === "critical" ? "▼" : "◆", row: m.kind === "critical" ? 0 : 1,
      label: m.kind === "critical" ? `Alert #${m.alert_id} reached Critical` : `Hybrid link: cyber #${m.cyber_id} + alert #${m.alert_id}`, detail: m.label })),
    markerRows: 2,
  }));
  HW.put($("#ov-week-legend"), wk.categories.map((k) => `<span><i class="sw" style="background:${CAT[k].color}"></i>${CAT[k].icon} ${CAT[k].label}</span>`).join("") +
    `<span><i class="ln" style="border-color:${MARK.critical}"></i>▼ alert reached Critical</span><span><i class="ln" style="border-color:${MARK.hybrid}"></i>◆ hybrid link (cyber event + narrative)</span>`);
  bindOverview();
}

function bindOverview() {
  const root = $("#view-overview");
  bindGoto(root);
  root.querySelectorAll("[data-ctr]").forEach((b) => b.onclick = () => {
    const k = b.dataset.ctr;
    if (k === "cyber_confirmed") return openCyberFiltered("confirmed", OV_HOURS);
    if (k === "cyber_refuted") return openCyberFiltered("refuted", OV_HOURS);
    openAlertsFiltered({ level: k, hours: OV_HOURS });
  });
  root.querySelectorAll("[data-cell]").forEach((b) => b.onclick = () => {
    const [category, level] = b.dataset.cell.split("|");
    openAlertsFiltered({ category, level, hours: OV_HOURS });
  });
  root.querySelectorAll("[data-place]").forEach((b) => b.onclick = () => {
    openAlertsFiltered({ place: b.dataset.place, placeName: b.dataset.name, hours: OV_HOURS });
  });
}

// (c) map of Kosovo by municipality (bundled GeoJSON, plain SVG)
function mapSVG(d, W) {
  const feats = (state.geo && state.geo.features) || [];
  if (!feats.length) return HW.empty("Map data not found (static/geo/kosovo-municipalities.geojson).");
  if (!mapSVG.box) {
    let x0 = 99, x1 = -99, y0 = 99, y1 = -99;
    const walk = (c) => typeof c[0] === "number" ? (x0 = Math.min(x0, c[0]), x1 = Math.max(x1, c[0]), y0 = Math.min(y0, c[1]), y1 = Math.max(y1, c[1])) : c.forEach(walk);
    feats.forEach((f) => walk(f.geometry.coordinates));
    mapSVG.box = [x0, x1, y0, y1];
  }
  const [x0, x1, y0, y1] = mapSVG.box, kx = Math.cos((((y0 + y1) / 2) * Math.PI) / 180), pad = 10;
  const Hmax = 460, s = Math.min((W - 2 * pad) / ((x1 - x0) * kx), (Hmax - 2 * pad) / (y1 - y0));
  const w = (x1 - x0) * kx * s, H = Math.ceil((y1 - y0) * s + 2 * pad), ox = (W - w) / 2;
  const px = (lon, lat) => [ox + (lon - x0) * kx * s, pad + (y1 - lat) * s];
  const path = (g) => (g.type === "Polygon" ? [g.coordinates] : g.coordinates).map((poly) => poly.map((ring) =>
    "M" + ring.map(([lon, lat]) => px(lon, lat).map((v) => v.toFixed(1)).join(",")).join("L") + "Z").join("")).join("");
  const byId = Object.fromEntries(d.map.places.map((p) => [p.id, p]));
  const tipFor = (f, p) => `<b>${esc(f.properties.name)}</b> · ${esc(f.properties.name_sr)} · ${esc(f.properties.name_sr_cyrl)}<br>` +
    (p ? `${plural(p.count, "active alert")} · highest: <b>${HW.LV_NAME[p.level]}</b> · named in ${plural(p.mentions, "post")}<br>` +
      p.alerts.slice(0, 4).map((a) => `#${a.id} ${HW.LV_NAME[a.level]}: ${esc(a.title.slice(0, 70))}`).join("<br>") + "<br><i>Click to open these alerts</i>"
      : "No active alert names this municipality");
  let shapes = "", dots = "", labels = "";
  feats.forEach((f) => {
    const p = byId[f.id];
    const fill = p ? HW.LV[p.level] : "#1c232c";
    shapes += `<path class="hw-hit mun${p ? " on" : ""}" d="${path(f.geometry)}" fill="${fill}" fill-opacity="${p ? 0.42 : 1}" stroke="#0f1216" stroke-width="1"
      ${p ? `data-place="${f.id}" data-name="${esc(f.properties.name)}"` : ""} ${HW.tip(tipFor(f, p))}/>`;
    if (p) {
      const [cx, cy] = px(...f.properties.label), r = 5 + 3.5 * Math.sqrt(p.count);
      dots += `<circle class="hw-hit" cx="${cx}" cy="${cy}" r="${r}" fill="${HW.LV[p.level]}" stroke="${HW.INK.surface}" stroke-width="2"
        data-place="${f.id}" data-name="${esc(f.properties.name)}" ${HW.tip(tipFor(f, p))}/>`;
      labels += `<text x="${cx + r + 4}" y="${cy + 4}" fill="#e3e8ee" font-size="11.5" font-weight="600" paint-order="stroke" stroke="#0f1216" stroke-width="3">${esc(f.properties.name)} (${p.count})</text>`;
    }
  });
  return `<svg class="hw-svg" width="${W}" height="${H}" viewBox="0 0 ${W} ${H}" role="img" aria-label="Map of Kosovo municipalities coloured by highest alert level">${shapes}${dots}${labels}</svg>`;
}

function unlocatedHTML(m) {
  const u = m.unlocated;
  const head = `<div class="unloc-h" ${u.length ? `data-place="none" data-name="Not located" role="button"` : ""}>Not located <b>${u.length}</b></div>
    <div class="muted small-n">Active alerts whose posts name no municipality${u.length ? " · click to list them" : ""}.</div>`;
  if (!m.total) return head + `<div class="chart-empty sm">No data in this period</div>`;
  if (!u.length) return head + `<div class="chart-empty sm">Every active alert names a place.</div>`;
  return head + u.slice(0, 6).map((a) => `<div class="unloc-i" data-goto="${a.id}" ${HW.tip(`#${a.id} · ${HW.LV_NAME[a.level]} · score ${Math.round(a.score)}<br>${esc(a.title)}`)}>
    <i class="dot" style="background:${HW.LV[a.level]}"></i><span>#${a.id} ${esc(a.title)}</span></div>`).join("") +
    (u.length > 6 ? `<div class="unloc-i more" data-place="none" data-name="Not located">+ ${u.length - 6} more</div>` : "");
}

function topHTML(top) {
  if (!top.length) return `<div class="chart-empty">No data in this period: no alert at Watch or above was active in the last ${OV_HOURS} h.</div>`;
  return `<div class="top5">${top.map((a, i) => `<div class="t5" data-goto="${a.id}">
    <span class="t5-n">${i + 1}</span>
    <div class="t5-main"><div class="t5-title" title="${esc(a.title)}">${esc(a.title)}</div>
      <div class="t5-meta">${lvPill(a.level)}
        <span class="t5-cats">${a.categories.map((k) => `<span class="cat-ic" style="--c:${CAT[k].color}" ${HW.tip(esc(CAT[k].label))}>${CAT[k].icon}</span>`).join("")}</span>
        <span class="t5-trend tr-${a.trend_dir}" ${HW.tip(`Posts in the last hour vs the normal rate: ${a.growth}x`)}>${TREND_ARROW[a.trend_dir]} ${a.trend}</span>
        ${a.hybrid ? '<span class="tag hyb">hybrid</span>' : ""}
        <span class="t5-when">updated ${fmt(a.updated)}</span></div></div>
    <span class="t5-score" ${HW.tip("Threat score 0–100")}>${Math.round(a.score)}</span></div>`).join("")}</div>`;
}

function matrixHTML(mx) {
  const lvls = ["watch", "threat", "critical"];
  const max = Math.max(1, ...Object.values(mx).flatMap((r) => lvls.map((l) => r[l])));
  const total = Object.values(mx).reduce((s, r) => s + lvls.reduce((t, l) => t + r[l], 0), 0);
  const cell = (k, l) => {
    const n = mx[k][l], a = n ? 0.18 + 0.72 * (n / max) : 0;
    const bg = n ? `background:color-mix(in srgb, ${HW.LV[l]} ${Math.round(a * 100)}%, transparent)` : "";
    return `<td><button class="mx-c ${n ? "" : "zero"}" ${n ? `data-cell="${k}|${l}"` : "disabled"} style="${bg};color:${a > 0.6 ? "#0f1216" : "#e3e8ee"}"
      ${HW.tip(`${esc(CAT[k].label)} × ${HW.LV_NAME[l]}: <b>${n}</b> ${n === 1 ? "alert" : "alerts"} (last ${OV_HOURS} h)${n ? "<br>Click to open them" : ""}`)}>${n}</button></td>`;
  };
  return `<table class="mx"><thead><tr><th class="mx-corner">Category ↓ · Level →</th>${lvls.map((l) => `<th>${lvSwatch(l)}${HW.LV_NAME[l]}</th>`).join("")}</tr></thead>
    <tbody>${Object.keys(CAT).map((k) => `<tr><th class="mx-row"><span class="cat-ic" style="--c:${CAT[k].color}">${CAT[k].icon}</span>${esc(CAT[k].label)}</th>${lvls.map((l) => cell(k, l)).join("")}</tr>`).join("")}</tbody></table>
    ${total ? "" : `<div class="chart-empty sm">No data in this period</div>`}`;
}

// ---------------------------------------------------------------- 8b-d alert detail charts
function alertChartsHTML(d) {
  const sp = d.spread || {};
  const lead = d.timeline && d.timeline.lead ? `<div class="lead">⏱ <b>${esc(d.timeline.lead.what)} ${duration(d.timeline.lead.seconds)} before the first credible media report</b>
    <span class="muted">(${fmt(d.timeline.lead.from)} vs ${fmt(d.timeline.lead.to)})</span></div>` : "";
  const langs = (sp.langs_order || []).map((k) => `<span><i class="sw" style="background:${LANG_COLOR[k]}"></i>${esc(sp.lang_names[k])}</span>`).join("");
  const mk = [...new Set((sp.markers || []).map((m) => m.kind))];
  const MK_NAME = { flagged: "flagged by the system", coordination: "coordination detected", credible: "first credible report", cyber: "linked cyber event (claimed time)", analyst: "analyst decision" };
  return `
    <div class="dsec"><h3>Spread timeline</h3>${lead}
      <div class="ch-t">Posts per 30 minutes, by language</div>
      <div class="ch-s">Hatched = copy-paste posts (near-identical text from many accounts). Shaded column = copy-paste burst (≥ ${sp.burst_min || 3} in 30 min).</div>
      <div id="d-spread" class="chart-box"></div>
      <div class="legend">${langs}<span><i class="sw hatch"></i>copy-paste posts</span><span><i class="sw band"></i>copy-paste burst</span>
        ${mk.map((k) => `<span><i class="ln" style="border-color:${MARK[k]}"></i>${MK_NAME[k]}</span>`).join("")}</div></div>
    <div class="dsec"><h3>Language &amp; platform split</h3>
      <div class="split2"><div><div class="ch-t">Posts by language</div><div id="d-langs" class="chart-box"></div></div>
        <div><div class="ch-t">Posts by platform</div><div id="d-plat" class="chart-box"></div></div></div></div>
    <div class="dsec"><h3>Who is pushing this</h3>
      <div class="ch-t">Accounts linked by near-identical text</div>
      <div class="ch-s">Each dot is an account (size = its posts in this story, colour = account age at its first post). A line joins accounts that posted near-identical text. Links show shared text, not proof of common control.</div>
      <div id="d-net" class="chart-box"><div class="chart-empty">Loading accounts…</div></div></div>`;
}

function drawAlertCharts(d) {
  const sp = d.spread || {};
  const names = sp.lang_names || {};
  HW.draw($("#d-spread"), HW.timeStack({
    t0: sp.start, t1: sp.end, bucket: sp.bucket || 1800, keys: sp.langs_order || [], colors: LANG_COLOR, names, rows: sp.bars || [],
    height: 250, yTitle: "Posts per 30 min", xTitle: "Time (UTC, data time)", emptyText: "No data in this period: this story has no posts yet.",
    overlay: (r) => r.copy, band: (r) => r.burst,
    rowTip: (r) => `<b>${HW.when(r.t)} – ${HW.hm(r.t + 1800)}</b><br>` + (sp.langs_order || []).filter((k) => r[k]).map((k) => `${esc(names[k])}: <b>${r[k]}</b>`).join("<br>") +
      (r.copy ? `<br>Copy-paste: <b>${r.copy}</b>${r.burst ? " (burst)" : ""}` : ""),
    markers: (sp.markers || []).map((m) => ({ t: m.t, label: m.label, color: MARK[m.kind] || "#e3e8ee", detail: m.detail })),
  }));
  const split = sp.split || { languages: [], platforms: [] };
  HW.draw($("#d-langs"), HW.hbars(split.languages.map((x) => ({ label: x.name, value: x.n, color: LANG_COLOR[x.key] || LANG_COLOR.other })), { unit: "posts", xTitle: "Posts" }));
  HW.draw($("#d-plat"), HW.hbars(split.platforms.map((x) => ({ label: x.name, value: x.n, color: PLATFORM_COLOR })), { unit: "posts", xTitle: "Posts" }));
  loadNetwork(d.id, false);
}

async function loadNetwork(id, all) {
  let n;
  try { n = await api(`/api/alerts/${id}/network${all ? "?all=1" : ""}`); } catch (err) { HW.put($("#d-net"), HW.empty("Could not load the accounts.")); return; }
  if (state.selected !== id || !$("#d-net")) return;
  const el = $("#d-net");
  if (!n.nodes.length) { HW.put(el, HW.empty("No data in this period: no accounts in this story.")); return; }
  const P = HW.forceLayout(n.nodes, n.edges);
  const ringOf = Object.fromEntries(n.other_alerts.map((o, i) => [o.id, RING[i] || "#8b96a5"]));
  const head = `<div class="net-head"><span><b>${n.total.toLocaleString()}</b> accounts · <b>${n.groups}</b> copy-paste groups${n.capped ? ` · showing the <b>${n.shown}</b> most involved` : ""}</span>
    ${n.capped ? `<button class="btn small-btn" id="net-all">Show all ${n.total.toLocaleString()}</button>` : all ? `<button class="btn small-btn" id="net-less">Show top 150</button>` : ""}</div>`;
  const legend = `<div class="legend">${AGE.map(([, c, l]) => `<span><i class="sw round" style="background:${c}"></i>${l}</span>`).join("")}
    <span><i class="sw round" style="background:${AGE_UNKNOWN}"></i>age unknown</span><span>line = near-identical text</span>
    ${n.other_alerts.map((o) => `<span class="link-plain inline" data-goto="${o.id}" ${HW.tip(`${o.shared} of these accounts are in alert #${o.id}'s copy-paste bursts<br>${esc(o.title)}`)}>
      <i class="sw ring" style="border-color:${ringOf[o.id]}"></i>also in #${o.id} (${o.shared})</span>`).join("")}</div>`;
  const draw = (W) => {
    const H = Math.round(Math.min(480, Math.max(300, W * 0.55))), pad = 26;
    const xs = P.map((p) => p[0]), ys = P.map((p) => p[1]);
    const bx0 = Math.min(...xs), bx1 = Math.max(...xs), by0 = Math.min(...ys), by1 = Math.max(...ys);
    const k = Math.min((W - 2 * pad) / (bx1 - bx0 || 1), (H - 2 * pad) / (by1 - by0 || 1));
    const X = (i) => pad + (P[i][0] - bx0) * k + ((W - 2 * pad) - (bx1 - bx0) * k) / 2;
    const Y = (i) => pad + (P[i][1] - by0) * k + ((H - 2 * pad) - (by1 - by0) * k) / 2;
    const idx = new Map(n.nodes.map((d, i) => [d.id, i]));
    const lines = n.edges.map((e) => { const a = idx.get(e.a), b = idx.get(e.b);
      return `<line x1="${X(a).toFixed(1)}" y1="${Y(a).toFixed(1)}" x2="${X(b).toFixed(1)}" y2="${Y(b).toFixed(1)}" stroke="#4a5666" stroke-opacity=".55" stroke-width="${Math.min(3, e.w)}"/>`; }).join("");
    const dots = n.nodes.map((d, i) => {
      const age = d.age_days, col = age == null ? AGE_UNKNOWN : AGE.find(([lim]) => age < lim)[1];
      const r = 3 + 2.2 * Math.sqrt(d.posts), ring = d.also_in.length ? ringOf[d.also_in[0]] || "#8b96a5" : null;
      const t = `<b>${esc(d.id)}</b><br>${plural(d.posts, "post")} in this story · ${(d.followers || 0).toLocaleString()} followers<br>` +
        `Account age at first post: <b>${age == null ? "unknown" : plural(age, "day")}</b><br>First post: ${HW.when(d.first)}` +
        (d.copy ? `<br>In ${plural(d.copy, "copy-paste group")}` : "<br>No copy-paste with other accounts") +
        (d.coordinated ? "<br>Part of the suspected coordinated burst" : "") +
        (d.also_in.length ? `<br><b>Also in the bursts of alert ${d.also_in.map((x) => "#" + x).join(", ")}</b>` : "");
      return `<g class="hw-hit" ${HW.tip(t)}>${ring ? `<circle cx="${X(i).toFixed(1)}" cy="${Y(i).toFixed(1)}" r="${r + 3}" fill="none" stroke="${ring}" stroke-width="2"/>` : ""}
        <circle cx="${X(i).toFixed(1)}" cy="${Y(i).toFixed(1)}" r="${r.toFixed(1)}" fill="${col}" stroke="${HW.INK.surface}" stroke-width="1.5"/>
        ${ring ? `<text x="${(X(i) + r + 4).toFixed(1)}" y="${(Y(i) + 3).toFixed(1)}" fill="${ring}" font-size="9.5" font-weight="600">#${d.also_in.join(",#")}</text>` : ""}</g>`;
    }).join("");
    return `<svg class="hw-svg net" width="${W}" height="${H}" viewBox="0 0 ${W} ${H}" role="img" aria-label="Network of accounts">${lines}${dots}</svg>`;
  };
  el.innerHTML = head + `<div id="d-net-svg"></div>` + legend; el.__hw = null;
  HW.draw($("#d-net-svg"), draw);
  bindGoto(el);
  const more = $("#net-all"), less = $("#net-less");
  if (more) more.onclick = () => { HW.put(el, '<div class="chart-empty">Laying out all accounts…</div>'); setTimeout(() => loadNetwork(id, true), 20); };
  if (less) less.onclick = () => loadNetwork(id, false);
}

// ---------------------------------------------------------------- 9a monitored sites grid
async function renderSites() {
  let s;
  try { s = await api("/api/cyber/sites"); } catch (err) { return; }
  if (state.cySel) return;   // an event was opened meanwhile
  const el = $("#cy-detail");
  const tiles = s.sites.map((x, i) => {
    const [col, ic, word] = SITE[x.state] || SITE.unknown;
    const last = x.last ? `Last check ${fmt(x.last.t)}: ${esc(x.last.detail)}` : "Never checked";
    return `<div class="site ${x.stale ? "stale" : ""}" style="--c:${col}">
      <div class="site-h"><span class="site-st" ${HW.tip(esc(last))}>${ic} ${word}</span>
        <span class="site-up" ${HW.tip(x.uptime_24h == null ? "No checks in the last 24 h" : `${x.uptime_24h}% of ${x.checks_24h} checks in the last 24 h answered`)}>${x.uptime_24h == null ? "—" : x.uptime_24h + "%"} <span class="muted">24 h uptime</span></span></div>
      <div class="site-host">${esc(x.host)}</div><div class="muted small-n">${esc(x.target || "")}</div>
      <div class="site-spark" id="spark-${i}"></div>
      <div class="muted small-n">${x.stale ? `No checks in the last 24 h · ${x.last ? "last " + fmt(x.last.t) : "never checked"}` : `${plural(x.checks_24h, "check")} in 24 h`}
        · events ${x.events.map((id) => `<a class="link" data-cyber="${id}">#${id}</a>`).join(" ")}</div></div>`;
  }).join("");
  HW.put(el, `<div class="page-in"><h2 class="sites-t">Monitored sites</h2>
    <div class="ch-s">Every public homepage the verifier has checked: one GET at a time, never a scan. Tile colour = latest check (up / slow ≥ ${s.slow_seconds} s / down).
      Sparkline = response time over the last 24 h (data time). ${s.live_checks ? "" : "Live checks are off (config <code>cyber.live_checks</code>): the demo shows the recorded checks."}</div>
    ${s.sites.length ? `<div class="sites">${tiles}</div>` : HW.empty("No data in this period: no site has been checked yet.")}
    <div class="legend">${["up", "slow", "down"].map((k) => `<span><i class="sw" style="background:${SITE[k][0]}"></i>${SITE[k][1]} ${SITE[k][2].toLowerCase()}</span>`).join("")}<span>faded tile = no check in the last 24 h</span></div>
    <p class="muted small">Select a cyber event on the left to see how it was verified.</p></div>`);
  s.sites.forEach((x, i) => HW.draw($(`#spark-${i}`), (W) => sparkSVG(x, s, W)));
  bindGoto(el);
}

function sparkSVG(x, s, W) {
  const H = 54, L = 4, R = 4, T = 6, B = 14;
  if (!x.points.length) return `<svg class="hw-svg" width="${W}" height="${H}"><rect x="0" y="0" width="${W}" height="${H - B + 4}" fill="#12171d" rx="4"/>
    <text x="${W / 2}" y="${(H - B) / 2 + 6}" fill="#8b96a5" font-size="11" text-anchor="middle">No data in this period</text>
    <text x="${L}" y="${H - 2}" fill="#8b96a5" font-size="9.5">${HW.when(s.since)}</text><text x="${W - R}" y="${H - 2}" fill="#8b96a5" font-size="9.5" text-anchor="end">now</text></svg>`;
  const ymax = Math.max(s.slow_seconds * 2, ...x.points.map((p) => p.secs || 0));
  const X = (t) => L + ((t - s.since) / (s.now - s.since)) * (W - L - R), Y = (v) => H - B - (v / ymax) * (H - B - T);
  const up = x.points.filter((p) => p.secs != null);
  const line = up.length > 1 ? `<polyline points="${up.map((p) => `${X(p.t).toFixed(1)},${Y(p.secs).toFixed(1)}`).join(" ")}" fill="none" stroke="#8b96a5" stroke-width="1.5"/>` : "";
  const pts = x.points.map((p) => { const t = HW.tip(`<b>${HW.when(p.t)}</b> · ${SITE[p.state][2]}<br>${esc(p.detail)}`);
    return p.state === "down" ? `<text class="hw-hit" ${t} x="${X(p.t)}" y="${T + 6}" fill="${SITE.down[0]}" font-size="11" text-anchor="middle">✕</text>`
      : `<circle class="hw-hit" ${t} cx="${X(p.t)}" cy="${Y(p.secs ?? 0)}" r="3.5" fill="${SITE[p.state][0]}" stroke="${HW.INK.surface}" stroke-width="1.5"/>`; }).join("");
  return `<svg class="hw-svg" width="${W}" height="${H}" viewBox="0 0 ${W} ${H}"><line x1="${L}" x2="${W - R}" y1="${H - B}" y2="${H - B}" stroke="${HW.INK.axis}"/>
    <line x1="${L}" x2="${W - R}" y1="${Y(s.slow_seconds)}" y2="${Y(s.slow_seconds)}" stroke="${SITE.slow[0]}" stroke-opacity=".35" stroke-dasharray="3 3"/>
    ${line}${pts}<text x="${L}" y="${H - 2}" fill="#8b96a5" font-size="9.5">${HW.when(s.since)}</text><text x="${W - R}" y="${H - 2}" fill="#8b96a5" font-size="9.5" text-anchor="end">${HW.when(s.now)}</text></svg>`;
}

// ---------------------------------------------------------------- 9b claim vs reality (one per cyber event)
function realityHTML(e) {
  const r = e.reality;
  return `<div class="dsec"><h3>Claim vs reality</h3>
    <div class="ch-t">What the measurements show around the claimed attack time</div>
    <div class="ch-s">Top: homepage response time of ${r && r.host ? `<b>${esc(r.host)}</b>` : "the target"} (✕ = no answer). Bottom: IODA internet signal for the area, as % of normal. Two panels, two separate scales.
      The chart shows evidence; the status comes from the checks listed under Verification.</div>
    <div id="cy-reality" class="chart-box"></div>
    <div class="legend"><span><i class="sw round" style="background:${SITE.up[0]}"></i>answered</span><span><i class="sw round" style="background:${SITE.slow[0]}"></i>slow (≥ ${r ? r.slow_seconds : 2} s)</span>
      <span style="color:${SITE.down[0]}">✕</span><span>no answer</span><span><i class="sw round" style="background:${CAT.cyber.color}"></i>IODA signal</span>
      <span><i class="ln" style="border-color:${MARK.claimed}"></i>claimed attack time</span><span><i class="ln" style="border-color:${MARK.first_seen}"></i>first seen by us</span>
      <span><i class="ln" style="border-color:${MARK.verdict}"></i>verdict</span></div></div>`;
}

function drawReality(e) {
  const r = e.reality;
  HW.draw($("#cy-reality"), (W) => {
    if (!r || (!r.site.length && !r.ioda.length))
      return HW.empty("No data in this period: no homepage or network measurements for this claim (no website to check, or live checks are off).");
    const L = 50, R = 14, top = 58, ph = 96, gap = 34, B = 36, H = top + ph * 2 + gap + B;
    const x = (t) => L + ((t - r.start) / (r.end - r.start)) * (W - L - R);
    const a0 = top, a1 = top + ph, b0 = a1 + gap, b1 = b0 + ph;
    let s = "";
    // panel A: response time
    const smax = HW.niceMax(Math.max(r.slow_seconds * 2, ...r.site.map((p) => p.secs || 0)));
    const ya = (v) => a1 - (v / smax) * (a1 - a0 - 12);
    [0, r.slow_seconds, smax].forEach((v) => { s += `<line x1="${L}" x2="${W - R}" y1="${ya(v)}" y2="${ya(v)}" stroke="${v ? HW.INK.grid : HW.INK.axis}"/>
      <text x="${L - 6}" y="${ya(v) + 3.5}" fill="#8b96a5" font-size="10.5" text-anchor="end">${v} s</text>`; });
    s += `<text x="${L}" y="${a0 - 6}" fill="#e3e8ee" font-size="11" font-weight="600" paint-order="stroke" stroke="#0f1216" stroke-width="4">Homepage response time${r.host ? ` · ${esc(r.host)}` : ""}</text>`;
    if (!r.site.length) s += `<text x="${(L + W - R) / 2}" y="${(a0 + a1) / 2}" fill="#8b96a5" font-size="11.5" text-anchor="middle">No data in this period: no homepage checks</text>`;
    const ok = r.site.filter((p) => p.secs != null);
    if (ok.length > 1) s += `<polyline points="${ok.map((p) => `${x(p.t).toFixed(1)},${ya(p.secs).toFixed(1)}`).join(" ")}" fill="none" stroke="#8b96a5" stroke-width="2"/>`;
    r.site.forEach((p) => { const t = HW.tip(`<b>${HW.when(p.t)}</b> · ${SITE[p.state][2]}${p.http ? ` · HTTP ${p.http}` : ""}${p.secs != null ? ` · ${p.secs} s` : ""}<br>${esc(p.detail)}`);
      s += p.state === "down" ? `<text class="hw-hit" ${t} x="${x(p.t)}" y="${a0 + 10}" fill="${SITE.down[0]}" font-size="14" font-weight="700" text-anchor="middle">✕</text>`
        : `<circle class="hw-hit" ${t} cx="${x(p.t)}" cy="${ya(p.secs)}" r="5" fill="${SITE[p.state][0]}" stroke="${HW.INK.surface}" stroke-width="2"/>`; });
    // panel B: IODA
    const yb = (v) => b1 - (v / 100) * (b1 - b0 - 10);
    [0, 50, 100].forEach((v) => { s += `<line x1="${L}" x2="${W - R}" y1="${yb(v)}" y2="${yb(v)}" stroke="${v ? HW.INK.grid : HW.INK.axis}"/>
      <text x="${L - 6}" y="${yb(v) + 3.5}" fill="#8b96a5" font-size="10.5" text-anchor="end">${v}%</text>`; });
    s += `<text x="${L}" y="${b0 - 6}" fill="#e3e8ee" font-size="11" font-weight="600" paint-order="stroke" stroke="#0f1216" stroke-width="4">IODA internet signal (% of normal)</text>`;
    const iv = r.ioda.filter((p) => p.value != null);
    if (!r.ioda.length) s += `<text x="${(L + W - R) / 2}" y="${(b0 + b1) / 2}" fill="#8b96a5" font-size="11.5" text-anchor="middle">No data in this period: no IODA check for this claim</text>`;
    if (iv.length > 1) s += `<polyline points="${iv.map((p) => `${x(p.t).toFixed(1)},${yb(p.value).toFixed(1)}`).join(" ")}" fill="none" stroke="${CAT.cyber.color}" stroke-width="2"/>`;
    r.ioda.forEach((p) => { const t = HW.tip(`<b>${HW.when(p.t)}</b> · IODA${p.value != null ? ` · ${p.value}% of normal` : ""}<br>${esc(p.detail)}`);
      s += p.value != null ? `<circle class="hw-hit" ${t} cx="${x(p.t)}" cy="${yb(p.value)}" r="5" fill="${CAT.cyber.color}" stroke="${HW.INK.surface}" stroke-width="2"/>`
        : `<text class="hw-hit" ${t} x="${x(p.t)}" y="${(b0 + b1) / 2}" fill="#8b96a5" font-size="12" text-anchor="middle">?</text>`; });
    // x axis + markers across both panels
    HW.timeTicks(r.start, r.end, W - L - R).forEach((k) => { s += `<line x1="${x(k.t)}" x2="${x(k.t)}" y1="${b1}" y2="${b1 + 4}" stroke="${HW.INK.axis}"/>
      <text x="${x(k.t)}" y="${b1 + 16}" fill="${k.major ? "#e3e8ee" : "#8b96a5"}" font-size="10.5" text-anchor="middle">${k.label}</text>`; });
    s += `<text x="${L + (W - L - R) / 2}" y="${H - 4}" fill="#8b96a5" font-size="10.5" text-anchor="middle">Time (UTC, data time)</text>`;
    const mk = [{ t: r.claimed_at, label: "Claimed attack", color: MARK.claimed, detail: "Time the claim says the attack happened" },
      { t: r.first_seen, label: "First seen", color: MARK.first_seen, detail: "When HybridWatch first saw the claim" }];
    if (r.verdict_at) mk.push({ t: r.verdict_at, label: CY_STATUS[r.status] || r.status, color: MARK.verdict, detail: "Status set by the verification checks" });
    const panelTitles = s.match(/<text x="[^"]+" y="[^"]+" fill="#e3e8ee" font-size="11" font-weight="600" paint-order[^>]*>[^<]*<\/text>/g) || [];
    s += HW.markers(mk.filter((m) => m.t), { x, top: 0, bottom: b1, W, L, R, rows: 3 }) + panelTitles.join("");   // titles drawn over the marker lines
    return `<svg class="hw-svg" width="${W}" height="${H}" viewBox="0 0 ${W} ${H}">${s}</svg>`;
  });
}
