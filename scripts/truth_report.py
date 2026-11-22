"""Writes data/truth/report.html from results.json (self-contained, no external files)."""
from __future__ import annotations

import html

SETS = {"t1": "T1 · same events, same wording", "t2": "T2 · new events", "t3": "T3 · new events + new wording"}
COL = {"Keyword rule": "#9AA5B1", "Text model": "#2F6FB3", "Account model": "#3FA79A", "Stacked model": "#B98A2E",
       "Averaged model": "#C2413B"}


def esc(x):
    return html.escape(str(x))


def bars(res):
    models = [m for m in res["sets"]["t1"] if not m.startswith("_")]
    W, H, pad, gw = 1000, 340, 50, 300
    bw = (gw - 40) / len(models)
    out = [f'<svg viewBox="0 0 {W} {H + 70}" width="100%">']
    for g in range(0, 101, 25):
        y = H - g / 100 * (H - pad)
        out.append(f'<line x1="40" x2="{W}" y1="{y}" y2="{y}" stroke="#E2DED4"/><text x="34" y="{y + 5}" text-anchor="end" font-size="13" fill="#6B7684">{g}%</text>')
    for gi, k in enumerate(SETS):
        x0 = 60 + gi * (gw + 20)
        for mi, m in enumerate(models):
            a = res["sets"][k][m]["accuracy"]
            h = a * (H - pad)
            x = x0 + mi * bw
            out.append(f'<rect x="{x:.0f}" y="{H - h:.0f}" width="{bw - 6:.0f}" height="{h:.0f}" fill="{COL[m]}"><title>{esc(m)}: {a:.1%}</title></rect>')
            out.append(f'<text x="{x + (bw - 6) / 2:.0f}" y="{H - h - 6:.0f}" text-anchor="middle" font-size="12" fill="#3B4656">{a * 100:.0f}</text>')
        out.append(f'<text x="{x0 + (gw - 40) / 2:.0f}" y="{H + 24}" text-anchor="middle" font-size="15" fill="#0F1B2D" font-weight="600">{esc(SETS[k])}</text>')
    lx = 60
    for m in models:
        out.append(f'<rect x="{lx}" y="{H + 44}" width="14" height="14" fill="{COL[m]}"/><text x="{lx + 20}" y="{H + 56}" font-size="14" fill="#3B4656">{esc(m)}</text>')
        lx += 22 + len(m) * 8 + 24
    out.append("</svg>")
    return "".join(out)


def curve(res):
    pts = res["curve"]
    W, H, l, b = 1000, 380, 70, 50
    xs = [p["n"] for p in pts]
    import math
    xmin, xmax = math.log(xs[0]), math.log(xs[-1])
    X = lambda n: l + (math.log(n) - xmin) / (xmax - xmin) * (W - l - 20)  # noqa: E731
    Y = lambda a: (H - b) - (a - 0.5) / 0.5 * (H - b - 20)                 # noqa: E731
    out = [f'<svg viewBox="0 0 {W} {H + 30}" width="100%">']
    for g in (0.5, 0.6, 0.7, 0.8, 0.9, 1.0):
        out.append(f'<line x1="{l}" x2="{W - 20}" y1="{Y(g)}" y2="{Y(g)}" stroke="#E2DED4"/><text x="{l - 8}" y="{Y(g) + 5}" text-anchor="end" font-size="17" fill="#6B7684">{g:.0%}</text>')
    for n in xs:
        out.append(f'<text x="{X(n)}" y="{H - 18}" text-anchor="middle" font-size="17" fill="#6B7684">{n}</text>')
    out.append(f'<text x="{(W + l) / 2}" y="{H + 18}" text-anchor="middle" font-size="17" fill="#3B4656">labelled training posts (log scale)</text>')
    cols = {"t1": "#9AA5B1", "t2": "#2F6FB3", "t3": "#C2413B"}
    for k, c in cols.items():
        d = " ".join(f"{'M' if i == 0 else 'L'}{X(p['n']):.1f},{Y(p[k]):.1f}" for i, p in enumerate(pts))
        out.append(f'<path d="{d}" fill="none" stroke="{c}" stroke-width="3"/>')
        for p in pts:
            out.append(f'<circle cx="{X(p["n"]):.1f}" cy="{Y(p[k]):.1f}" r="4" fill="{c}"><title>{k} n={p["n"]}: {p[k]:.1%}</title></circle>')
    out.append("</svg>")
    return "".join(out)


def table(headers, rows):
    h = "".join(f"<th>{esc(x)}</th>" for x in headers)
    b = "".join("<tr>" + "".join(f"<td>{x}</td>" for x in r) + "</tr>" for r in rows)
    return f"<table><tr>{h}</tr>{b}</table>"


def pct(x):
    return "—" if x is None else f"{x * 100:.1f}%"


def write_report(res, path):
    t3 = res["sets"]["t3"]
    best = t3["Averaged model"]
    div = res["diversity"]
    models = [m for m in t3 if not m.startswith("_")]
    rows = []
    for m in models:
        r = [esc(m)] + [pct(res["sets"][k][m]["accuracy"]) for k in SETS] + [
            f"{t3[m]['f1']:.2f}", pct(t3[m]["hard_acc"]),
            "—" if m == "Keyword rule" else f"{pct(t3[m]['sure_acc'])} on {t3[m]['sure_share']:.0%} of posts"]
        rows.append(r)
    cm = best["cm"]
    calib = table(["Model said", "Posts", "Average certainty", "Actually right"],
                  [[b["range"], b["n"], pct(b["said"]), pct(b["right"])] for b in best["bins"]])
    lang = table(["Language"] + [SETS[k].split(" · ")[0] for k in SETS],
                 [[{"sq": "Albanian", "sr": "Serbian", "en": "English"}[l]] + [pct(res["sets"][k]["Averaged model"]["by_lang"][l]) for k in SETS]
                  for l in ("sq", "sr", "en")])
    errs = "".join(f"<li><b>Truth: {esc(e['truth'].upper())}</b>, model said {e['said_false'] * 100:.0f}% false "
                   f"<span class='muted'>(account {e['account_age_days']:.0f} days old, copy-paste group {e['copy_group_size']})</span><br>"
                   f"<span class='quote'>{esc(e['text'])}</span></li>" for e in t3["_errors"])
    wf = ", ".join(f"<code>{esc(w)}</code>" for w, _ in res["words_false"])
    wt = ", ".join(f"<code>{esc(w)}</code>" for w, _ in res["words_true"])
    page = f"""<!DOCTYPE html><html lang="en"><head><meta charset="utf-8"><title>How right is it? HybridWatch true/false experiment</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
body{{margin:0;background:#F4F2EC;color:#3B4656;font:17px/1.55 Inter,system-ui,-apple-system,"Segoe UI",sans-serif}}
main{{max-width:1080px;margin:0 auto;padding:48px 28px 80px}}
h1{{font:600 40px/1.15 Rubik,system-ui,sans-serif;color:#0F1B2D;margin:0 0 10px}} h2{{font:600 26px/1.2 Rubik,system-ui,sans-serif;color:#0F1B2D;margin:44px 0 12px}}
.eyebrow{{font:600 13px/1 "JetBrains Mono",monospace;letter-spacing:2px;color:#8A4E08}}
.cards{{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:14px;margin:22px 0}}
.card{{background:#FBFAF6;border:1px solid #D9D4C7;border-radius:12px;padding:16px 18px}} .card .v{{font:600 34px/1.1 Rubik,system-ui;color:#0F1B2D}} .card .k{{font-size:14px;color:#6B7684}}
.card.hot{{border-color:#C2413B}} table{{border-collapse:collapse;width:100%;background:#FBFAF6;font-size:15px}} th,td{{text-align:left;padding:8px 10px;border-bottom:1px solid #E2DED4}} th{{color:#0F1B2D}}
.panel{{background:#FBFAF6;border:1px solid #D9D4C7;border-radius:12px;padding:18px}} .muted{{color:#6B7684}} .quote{{font-style:italic}}
.warn{{background:#FFF4E0;border:1px solid #E8A33D;border-radius:12px;padding:14px 18px}} code{{background:#ECE8DD;padding:1px 6px;border-radius:5px;font-size:14px}}
li{{margin-bottom:12px}} .grid2{{display:grid;grid-template-columns:1fr 1fr;gap:18px}} @media(max-width:800px){{.grid2{{grid-template-columns:1fr}}}}
</style></head><body><main>
<p class="eyebrow">HYBRIDWATCH · SUPERVISED LEARNING EXPERIMENT · SYNTHETIC DATA</p>
<h1>How right is it?</h1>
<p>Models were trained on <b>{res['sizes']['train']:,} labelled posts</b> (half true, half false) and then scored on posts they had never seen, with labels hidden.
The three test sets get harder: T1 is new posts about the same events, T2 is new events, and T3 is new events written in a writing style the model never saw.</p>

<div class="cards">
<div class="card"><div class="v">{pct(res['sets']['t1']['Text model']['accuracy'])}</div><div class="k">T1 · text model (same events and wording)</div></div>
<div class="card"><div class="v">{pct(res['sets']['t2']['Text model']['accuracy'])}</div><div class="k">T2 · text model (new events)</div></div>
<div class="card hot"><div class="v">{pct(t3['Text model']['accuracy'])}</div><div class="k">T3 · text model (new events + new wording)</div></div>
<div class="card"><div class="v">{pct(best['accuracy'])}</div><div class="k">T3 · averaged text + account model</div></div>
<div class="card"><div class="v">{pct(div['t3']['two_styles_text'])}</div><div class="k">T3 · text model trained on two writing styles (same 2,000 posts)</div></div>
</div>

<div class="warn"><b>What this means.</b> Near-perfect scores on T1 and T2 do not show that the model is good. They show that it learned how the generator writes.
The honest number is T3, where the wording is new. Real posts will look even less like the training data, so <b>the real accuracy can only be measured on labelled real data</b> (for example the Banjska set).</div>

<h2>1. Accuracy on each test set</h2>
<div class="panel">{bars(res)}</div>
{table(["Model", "T1", "T2", "T3", "F1 on T3", "Hard cases T3", "When ≥90% sure (T3)"], rows)}
<p class="muted">"Hard cases" are true posts written like panic (caps, no source) and false posts written calmly with a fake “police confirmed”. F1 is for catching false posts.</p>

<h2>2. Variety beats volume</h2>
<div class="panel"><b>Learning curve</b> (averaged model): accuracy as the number of labelled training posts grows
<p style="margin:8px 0 0"><span style="color:#9AA5B1">■</span> T1 same events · {res['curve'][-1]['t1']:.0%} &nbsp; <span style="color:#2F6FB3">■</span> T2 new events · {res['curve'][-1]['t2']:.0%} &nbsp; <span style="color:#C2413B">■</span> T3 new events + new wording · {res['curve'][-1]['t3']:.0%}</p>{curve(res)}
<p class="muted">On T3 accuracy levels off after about 400 labelled posts: more posts in the <i>same</i> style stop helping.</p></div>
<div class="panel" style="margin-top:18px"><b>Same 2,000 posts, more variety</b>
{table(["Trained on", "T3 text model", "T3 averaged"], [["One writing style", pct(t3['Text model']['accuracy']), pct(div['t3']['one_style'])], ["Two writing styles", pct(div['t3']['two_styles_text']), pct(div['t3']['two_styles'])]])}
<p>Swapping half the training posts for a second writing style lifts T3 from {pct(t3['Text model']['accuracy'])} to <b>{pct(div['t3']['two_styles_text'])}</b>.
For real data: label posts from <b>many different channels, outlets and writers</b>, not many posts from a few.</p></div>

<h2>3. How much to trust the certainty %</h2>
<div class="grid2">
<div class="panel"><b>Averaged model on T3:</b> when it says X%, how often is it right?{calib}
<p class="muted">Expected calibration error {best['ece']:.3f} (0 is perfect). When it is at least 90% sure it was right {pct(best['sure_acc'])} of the time, on {best['sure_share']:.0%} of posts:
those can be trusted, the rest go to an analyst.</p></div>
<div class="panel"><b>Confusion matrix</b> (averaged model, T3)
{table(["", "Model: true", "Model: false"], [["Really true", cm['tn'], cm['fp']], ["Really false", cm['fn'], cm['tp']]])}
<p class="muted">{cm['fp']} true posts flagged as false, {cm['fn']} false posts missed.</p>
<b>By language</b> (averaged model){lang}</div>
</div>

<h2>4. Why it decides what it decides</h2>
<div class="grid2">
<div class="panel"><b>Words that push towards FALSE</b><br>{wf}</div>
<div class="panel"><b>Words that push towards TRUE</b><br>{wt}</div>
</div>
<p class="muted">It mostly learned style and sourcing (urgency, "sources say", attribution to the police), not facts. That is why new wording hurts, and why account behaviour and cross-checking with credible sources (the event scorer) are needed on top.</p>

<h2>5. Its most confident mistakes (T3)</h2>
<ul>{errs}</ul>

<h2>Method</h2>
<p>Text model: TF-IDF character n-grams (2–5) and word n-grams (1–2) feeding a logistic regression, calibrated with isotonic regression (5-fold).
Account model: gradient-boosted trees on account age, followers, posts per day, copy-paste group size and whether the account is a news outlet, also calibrated.
Stacked model: a second model trained to combine the two. Averaged model: the mean of the two probabilities.
Keyword rule: "false if it shouts, uses '!!', or says urgent, share, sources say".
All synthetic: {res['sizes']['train']:,} training posts in Albanian, Serbian and English; test sets of {res['sizes']['t1']}, {res['sizes']['t2']} and {res['sizes']['t3']} posts.
Reproduce with <code>py scripts/truth_data.py</code> then <code>py scripts/truth_experiment.py</code>.</p>
</main></body></html>"""
    path.write_text(page, encoding="utf-8")
    return path
