// HybridWatch chart helpers. Plain SVG, one shared tooltip, no library, no build step.
// Charts are drawn at the container's real width (text stays readable) and redrawn on window resize.
// Charts show evidence; they never decide anything.
const HW = (() => {
  // threat levels: the same four colours everywhere (Info grey, Watch blue, Threat orange, Critical red)
  const LV = { info: "#6c7a89", watch: "#4c9aff", threat: "#ff9f43", critical: "#ff5d5d" };
  const LV_NAME = { info: "Info", watch: "Watch", threat: "Threat", critical: "Critical" };
  const INK = { axis: "#3a4554", grid: "#222a34", text: "#8b96a5", strong: "#e3e8ee", surface: "#161b22" };
  const e = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

  // ---------------------------------------------------------------- tooltip (any element with data-tip="<html>")
  let tipEl = null;
  function tipShow(ev, html) {
    if (!tipEl) { tipEl = document.createElement("div"); tipEl.className = "hw-tip"; document.body.appendChild(tipEl); }
    tipEl.innerHTML = html;
    tipEl.style.display = "block";
    const r = tipEl.getBoundingClientRect(), pad = 14;
    let x = ev.clientX + pad, y = ev.clientY + pad;
    if (x + r.width > window.innerWidth - 6) x = ev.clientX - r.width - pad;
    if (y + r.height > window.innerHeight - 6) y = ev.clientY - r.height - pad;
    tipEl.style.left = Math.max(4, x) + "px";
    tipEl.style.top = Math.max(4, y) + "px";
  }
  function tipHide() { if (tipEl) tipEl.style.display = "none"; }
  document.addEventListener("mousemove", (ev) => {
    const t = ev.target.closest && ev.target.closest("[data-tip]");
    if (t) tipShow(ev, t.getAttribute("data-tip")); else tipHide();
  });
  document.addEventListener("scroll", tipHide, true);
  const tip = (html) => `data-tip="${e(html)}"`;   // attribute holding (escaped) tooltip HTML

  // ---------------------------------------------------------------- drawing into containers
  // put(): replace content only when it changed -> refreshing every few seconds never flickers
  function put(el, html) {
    if (!el) return false;
    if (el.__hw === html) return false;
    el.innerHTML = html; el.__hw = html;
    return true;
  }
  // draw(el, fn): fn(width) returns markup; remembered so the chart redraws on resize
  function draw(el, fn) {
    if (!el) return;
    el.__draw = fn; el.setAttribute("data-hw-chart", "");
    put(el, fn(width(el)));
  }
  const width = (el) => Math.max(260, Math.floor(el.getBoundingClientRect().width || el.clientWidth || 600));
  let rt;
  window.addEventListener("resize", () => {
    clearTimeout(rt);
    rt = setTimeout(() => document.querySelectorAll("[data-hw-chart]").forEach((el) => {
      if (el.__draw && el.offsetParent !== null) put(el, el.__draw(width(el)));
    }), 120);
  });
  const empty = (msg) => `<div class="chart-empty">${e(msg || "No data in this period")}</div>`;

  // ---------------------------------------------------------------- scales & axes
  const MON = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
  const p2 = (n) => String(n).padStart(2, "0");
  const dayLabel = (t) => { const d = new Date(t * 1000); return `${d.getUTCDate()} ${MON[d.getUTCMonth()]}`; };
  const hm = (t) => { const d = new Date(t * 1000); return `${p2(d.getUTCHours())}:${p2(d.getUTCMinutes())}`; };
  const when = (t) => t ? `${dayLabel(t)}, ${hm(t)}` : "—";               // "2 Nov, 07:31" (data time, UTC)
  function niceMax(v) {
    if (v <= 0) return 1;
    const p = 10 ** Math.floor(Math.log10(v)), f = v / p;
    return (f <= 1 ? 1 : f <= 2 ? 2 : f <= 2.5 ? 2.5 : f <= 5 ? 5 : 10) * p;
  }
  // whole-number ticks when the maximum allows it (counts are never fractional)
  function yTicks(max, n = 4) {
    if (max >= 2 && !Number.isInteger(max / n)) n = [5, 4, 2].find((k) => Number.isInteger(max / k)) || n;
    const s = max / n; return Array.from({ length: n + 1 }, (_, i) => +(i * s).toFixed(2));
  }
  // time ticks at round steps; midnight shows the date
  function timeTicks(t0, t1, px) {
    const steps = [900, 1800, 3600, 7200, 10800, 21600, 43200, 86400, 172800];
    const step = steps.find((s) => (t1 - t0) / s <= Math.max(2, px / 84)) || 172800;
    const out = [];
    for (let t = Math.ceil(t0 / step) * step; t <= t1; t += step) {
      const mid = new Date(t * 1000).getUTCHours() === 0 && new Date(t * 1000).getUTCMinutes() === 0;
      out.push({ t, label: mid || step >= 86400 ? dayLabel(t) : hm(t), major: mid });
    }
    return out;
  }
  // y axis + grid + x ticks + axis titles for a plot area
  function axes({ W, H, L, R, T, B, yMax, yFmt = (v) => v, x, t0, t1, xTitle, yTitle, ticks }) {
    const y = (v) => H - B - (v / yMax) * (H - B - T);
    let s = "";
    yTicks(yMax).forEach((v) => {
      s += `<line x1="${L}" x2="${W - R}" y1="${y(v)}" y2="${y(v)}" stroke="${v ? INK.grid : INK.axis}"/>
        <text x="${L - 6}" y="${y(v) + 3.5}" fill="${INK.text}" font-size="10.5" text-anchor="end">${e(yFmt(v))}</text>`;
    });
    (ticks || timeTicks(t0, t1, W - L - R)).forEach((k) => {
      s += `<line x1="${x(k.t)}" x2="${x(k.t)}" y1="${H - B}" y2="${H - B + 4}" stroke="${INK.axis}"/>
        <text x="${x(k.t)}" y="${H - B + 16}" fill="${k.major ? INK.strong : INK.text}" font-size="10.5" text-anchor="middle">${e(k.label)}</text>`;
    });
    if (xTitle) s += `<text x="${L + (W - L - R) / 2}" y="${H - 4}" fill="${INK.text}" font-size="10.5" text-anchor="middle">${e(xTitle)}</text>`;
    if (yTitle) s += `<text transform="translate(12 ${T + (H - B - T) / 2}) rotate(-90)" fill="${INK.text}" font-size="10.5" text-anchor="middle">${e(yTitle)}</text>`;
    return { svg: s, y };
  }

  // vertical event markers with staggered labels (no overlaps), full text in the tooltip
  function markers(list, { x, top, bottom, W, L, R, rows = 3 }) {
    let lastX = [], s = "";
    [...list].sort((a, b) => a.t - b.t).forEach((m) => {
      const mx = x(m.t);
      if (mx < L - 1 || mx > W - R + 1) return;
      if (m.glyph) {   // compact: a glyph in the marker's own row, the text lives in the tooltip and the legend
        const gy = top + 11 + (m.row || 0) * 13;
        s += `<g class="hw-hit" ${tip(`<b>${e(m.label)}</b><br>${e(when(m.t))}${m.detail ? "<br>" + e(m.detail) : ""}`)}>
          <line x1="${mx}" x2="${mx}" y1="${gy + 2}" y2="${bottom}" stroke="${m.color}" stroke-width="1.5" stroke-dasharray="3 3" stroke-opacity=".8"/>
          <rect x="${mx - 6}" y="${gy - 11}" width="12" height="${bottom - gy + 11}" fill="transparent"/>
          <text x="${mx}" y="${gy}" fill="${m.color}" font-size="11" text-anchor="middle">${e(m.glyph)}</text></g>`;
        return;
      }
      let row = 0;
      while (row < rows - 1 && lastX[row] !== undefined && mx - lastX[row] < 150) row++;
      lastX[row] = mx;
      const ly = top + 11 + row * 13;
      const anchor = mx > W - R - 90 ? "end" : mx < L + 70 ? "start" : "middle";
      const short = m.label.length > 28 ? m.label.slice(0, 27) + "…" : m.label;
      const t = tip(`<b>${e(m.label)}</b><br>${e(when(m.t))}${m.detail ? "<br>" + e(m.detail) : ""}`);
      s += `<g ${t} class="hw-hit"><line x1="${mx}" x2="${mx}" y1="${ly + 3}" y2="${bottom}" stroke="${m.color}" stroke-width="1.5" stroke-dasharray="4 3"/>
        <rect x="${mx - 5}" y="${ly - 10}" width="10" height="${bottom - ly + 10}" fill="transparent"/>
        <text x="${mx}" y="${ly}" fill="${m.color}" font-size="10.5" font-weight="600" text-anchor="${anchor}">${e(short)}</text></g>`;
    });
    return s;
  }

  // ---------------------------------------------------------------- stacked bars over time
  // o: {t0, t1, bucket, keys, colors, names, rows:[{t, <key>: n, ...}], height, yTitle, xTitle, markers, markerRows,
  //     overlay(row) -> n (hatched part from the baseline), band(row) -> bool (highlight column), rowTip(row) -> html}
  function timeStack(o) {
    return (W) => {
      const rows = o.rows || [];
      const total = (r) => o.keys.reduce((s, k) => s + (r[k] || 0), 0);
      if (!rows.some((r) => total(r) > 0)) return empty(o.emptyText);
      const mrows = o.markers && o.markers.length ? (o.markerRows || 3) : 0;
      const H = o.height || 220, L = 54, R = 12, T = 8 + mrows * 13, B = 36;
      const x = (t) => L + ((t - o.t0) / (o.t1 - o.t0)) * (W - L - R);
      const yMax = niceMax(Math.max(...rows.map(total)));
      const ax = axes({ W, H, L, R, T, B, yMax, x, t0: o.t0, t1: o.t1, xTitle: o.xTitle, yTitle: o.yTitle, ticks: o.ticks });
      const slot = Math.max(1, x(o.t0 + o.bucket) - x(o.t0));
      const gap = slot >= 8 ? 2 : slot >= 4 ? 1 : 0;
      const bw = Math.max(1, slot - gap), inset = o.barInset ? Math.min(o.barInset, slot * 0.2) : 0;
      let bands = "", bars = "", hits = "";
      rows.forEach((r) => {
        const bx = x(r.t) + gap / 2 + inset, w = bw - 2 * inset;
        if (o.band && o.band(r)) bands += `<rect x="${x(r.t)}" y="${T}" width="${slot}" height="${H - B - T}" fill="#e3e8ee" opacity=".07"/>`;
        let acc = 0;
        o.keys.forEach((k) => {
          const v = r[k] || 0;
          if (!v) return;
          const y0 = ax.y(acc), y1 = ax.y(acc + v);
          const h = Math.max(1, y0 - y1 - (y0 - y1 > 3 ? 1 : 0));    // 1px surface gap between segments
          bars += `<rect x="${bx}" y="${y1}" width="${w}" height="${h}" fill="${o.colors[k]}"/>`;
          acc += v;
        });
        const ov = o.overlay ? o.overlay(r) : 0;
        if (ov) bars += `<rect x="${bx}" y="${ax.y(Math.min(ov, acc))}" width="${w}" height="${H - B - ax.y(Math.min(ov, acc))}" fill="url(#hw-hatch)"/>`;
        const t = o.rowTip ? o.rowTip(r) : `<b>${e(when(r.t))}</b><br>` + o.keys.filter((k) => r[k]).map((k) => `${e(o.names[k])}: <b>${r[k]}</b>`).join("<br>");
        hits += `<rect class="hw-hit" x="${x(r.t)}" y="${T}" width="${slot}" height="${H - B - T}" fill="transparent" ${tip(t)}/>`;
      });
      const mk = o.markers ? markers(o.markers, { x, top: 0, bottom: H - B, W, L, R, rows: mrows }) : "";
      return `<svg class="hw-svg" width="${W}" height="${H}" viewBox="0 0 ${W} ${H}">${HATCH}${ax.svg}${bands}${bars}${hits}${mk}</svg>`;
    };
  }
  const HATCH = `<defs><pattern id="hw-hatch" width="5" height="5" patternUnits="userSpaceOnUse" patternTransform="rotate(45)">
    <rect width="5" height="5" fill="rgba(0,0,0,.25)"/><line x1="0" y1="0" x2="0" y2="5" stroke="#f3f4f6" stroke-width="2"/></pattern></defs>`;

  // ---------------------------------------------------------------- horizontal bars (one row per item)
  // items: [{label, value, color, tip}], o: {unit, xTitle}
  function hbars(items, o = {}) {
    return (W) => {
      if (!items.length || !items.some((i) => i.value > 0)) return empty(o.emptyText);
      const labelW = Math.min(150, Math.max(90, Math.max(...items.map((i) => i.label.length)) * 6.6));
      const row = 22, T = 4, B = o.xTitle ? 30 : 6, H = T + items.length * row + B, L = labelW + 8, R = 56;
      const max = Math.max(...items.map((i) => i.value));
      const sum = items.reduce((s, i) => s + i.value, 0);
      const x = (v) => L + (v / max) * (W - L - R);
      let s = "";
      items.forEach((it, k) => {
        const y = T + k * row, pct = Math.round((100 * it.value) / sum);
        const t = it.tip || `<b>${e(it.label)}</b><br>${it.value.toLocaleString()} ${e(o.unit || "")} (${pct}% of ${sum.toLocaleString()})`;
        s += `<g class="hw-hit" ${tip(t)}><rect x="0" y="${y}" width="${W}" height="${row}" fill="transparent"/>
          <text x="${labelW}" y="${y + 15}" fill="${INK.strong}" font-size="12" text-anchor="end">${e(it.label)}</text>
          <rect x="${L}" y="${y + 5}" width="${Math.max(2, x(it.value) - L)}" height="12" rx="2" fill="${it.color}"/>
          <text x="${x(it.value) + 6}" y="${y + 15}" fill="${INK.text}" font-size="11">${it.value.toLocaleString()} · ${pct}%</text></g>`;
      });
      if (o.xTitle) s += `<line x1="${L}" x2="${W - R}" y1="${H - B + 2}" y2="${H - B + 2}" stroke="${INK.axis}"/>
        <text x="${L}" y="${H - B + 14}" fill="${INK.text}" font-size="10">0</text>
        <text x="${W - R}" y="${H - B + 14}" fill="${INK.text}" font-size="10" text-anchor="end">${max.toLocaleString()}</text>
        <text x="${L + (W - L - R) / 2}" y="${H - 3}" fill="${INK.text}" font-size="10.5" text-anchor="middle">${e(o.xTitle)}</text>`;
      return `<svg class="hw-svg" width="${W}" height="${H}" viewBox="0 0 ${W} ${H}">${s}</svg>`;
    };
  }

  // ---------------------------------------------------------------- force-directed layout (computed once per data)
  // Deterministic: same data -> same picture. Returns positions in an abstract space; the caller fits them.
  // Only linked accounts are simulated; accounts that shared no text with anyone are placed on an outer ring
  // (by number of posts), so they never crowd the copy-paste clusters.
  function forceLayout(nodes, edges) {
    const n = nodes.length, idx = new Map(nodes.map((d, i) => [d.id, i]));
    const P = nodes.map(() => [0, 0]);
    const E = edges.map((d) => [idx.get(d.a), idx.get(d.b)]).filter(([a, b]) => a !== undefined && b !== undefined);
    const deg = new Array(n).fill(0); E.forEach(([a, b]) => { deg[a]++; deg[b]++; });
    const linked = [...Array(n).keys()].filter((i) => deg[i]), loose = [...Array(n).keys()].filter((i) => !deg[i]);
    linked.forEach((i, k) => { const a = k * 2.399963, r = 12 * Math.sqrt(k + 1); P[i] = [r * Math.cos(a), r * Math.sin(a)]; });
    const m = linked.length, iters = m > 800 ? 60 : m > 300 ? 140 : 300;
    for (let it = 0; it < iters; it++) {
      const alpha = 1 - it / iters, F = new Map(linked.map((i) => [i, [0, 0]]));
      for (let x = 0; x < m; x++) for (let y = x + 1; y < m; y++) {             // repulsion
        const i = linked[x], j = linked[y];
        const dx = P[i][0] - P[j][0], dy = P[i][1] - P[j][1], d2 = dx * dx + dy * dy + 0.01;
        if (d2 > 160000) continue;
        const f = 600 / d2, fi = F.get(i), fj = F.get(j);
        fi[0] += dx * f; fi[1] += dy * f; fj[0] -= dx * f; fj[1] -= dy * f;
      }
      E.forEach(([a, b]) => {                                                     // springs (rest length 34)
        const dx = P[b][0] - P[a][0], dy = P[b][1] - P[a][1], d = Math.sqrt(dx * dx + dy * dy) + 0.01, f = (d - 34) * 0.05;
        const fa = F.get(a), fb = F.get(b);
        fa[0] += (dx / d) * f; fa[1] += (dy / d) * f; fb[0] -= (dx / d) * f; fb[1] -= (dy / d) * f;
      });
      linked.forEach((i) => {                                                     // weak gravity keeps clusters together
        const f = F.get(i); f[0] -= P[i][0] * 0.008; f[1] -= P[i][1] * 0.008;
        const len = Math.hypot(f[0], f[1]) || 1, step = Math.min(10, len) * alpha;
        P[i][0] += (f[0] / len) * step; P[i][1] += (f[1] / len) * step;
      });
    }
    const R0 = linked.length ? Math.max(...linked.map((i) => Math.hypot(P[i][0], P[i][1]))) + 34 : 0;
    loose.sort((a, b) => (nodes[b].posts - nodes[a].posts) || (a - b));
    let ring = 0, placed = 0;
    while (placed < loose.length) {                                               // concentric rings, 16 px apart
      const r = R0 + ring * 18, cap = Math.max(8, Math.floor((2 * Math.PI * Math.max(r, 20)) / 15));
      const here = loose.slice(placed, placed + cap);
      here.forEach((i, k) => { const a = (2 * Math.PI * k) / here.length + ring * 0.37; P[i] = [Math.max(r, 20) * Math.cos(a), Math.max(r, 20) * Math.sin(a)]; });
      placed += here.length; ring++;
    }
    return P;
  }

  return { LV, LV_NAME, INK, esc: e, tip, put, draw, width, empty, niceMax, yTicks, timeTicks, axes, markers,
    timeStack, hbars, forceLayout, when, hm, dayLabel, HATCH };
})();
