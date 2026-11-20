"""Measure how accurate the model is on a labelled dataset.

Replays the dataset through the real pipeline (in 5-minute simulated steps, into a
temporary database) and compares the output with the ground-truth labels.

    py scripts/evaluate.py                       # demo dataset
    py scripts/evaluate.py --data my_labelled.jsonl

Reports:
  * narrative clustering   - pairwise precision / recall / F1, adjusted Rand index
  * copy-paste detection   - post-level precision / recall for coordinated posts
  * debunk matching        - post-level precision / recall (and correct fact-check)
  * alerts                 - which narratives alerted, with which type, false alarms
  * early warning          - alert time vs first credible-outlet report
"""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sklearn.metrics import adjusted_rand_score  # noqa: E402

from hybridwatch.config import load_config, resolve  # noqa: E402
from hybridwatch.db import jloads  # noqa: E402
from hybridwatch.pipeline import Pipeline  # noqa: E402

CAMPAIGNS = {"water_poison", "kfor_withdraw", "power_sabotage"}


def prf(tp, fp, fn):
    p = tp / (tp + fp) if tp + fp else 0.0
    r = tp / (tp + fn) if tp + fn else 0.0
    f = 2 * p * r / (p + r) if p + r else 0.0
    return p, r, f


def fmt_t(ts):
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%d %b %H:%M")


def run(cfg: dict, data: Path, step_minutes: int = 5, quiet=False) -> dict:
    posts = [json.loads(l) for l in open(data, encoding="utf-8") if l.strip()]
    posts.sort(key=lambda p: p["ts"])
    tmp = Path(tempfile.mkdtemp()) / "eval.db"
    pipe = Pipeline(cfg, db_path=str(tmp), quiet=True)
    pipe.factchecks.refresh_sources([fc for fc in cfg["factcheckers"] if fc.get("type") == "file" and fc.get("enabled")])

    t0 = time.perf_counter()
    i, clock = 0, posts[0]["ts"]
    while i < len(posts):
        clock += step_minutes * 60
        batch = []
        while i < len(posts) and posts[i]["ts"] <= clock:
            batch.append(posts[i])
            i += 1
        if batch:
            pipe.process(batch)
    elapsed = time.perf_counter() - t0
    conn = pipe.conn

    rows = conn.execute("SELECT id, narrative_id, dup_group, author, debunk_id, gt_narrative, gt_coordinated, "
                        "gt_debunk, ts, credibility FROM posts").fetchall()
    fc_ext = {r["id"]: r["ext_id"] for r in conn.execute("SELECT id, ext_id FROM factchecks")}
    res: dict = {"posts": len(rows), "seconds": elapsed, "posts_per_sec": len(rows) / elapsed}

    # ---- clustering (non-noise posts) ----
    sig = [r for r in rows if r["gt_narrative"] != "noise"]
    res["ari"] = adjusted_rand_score([r["gt_narrative"] for r in sig], [r["narrative_id"] for r in sig])
    tp = fp = fn = 0
    by_pred = defaultdict(Counter)
    for r in sig:
        by_pred[r["narrative_id"]][r["gt_narrative"]] += 1
    gt_sizes = Counter(r["gt_narrative"] for r in sig)
    for c in by_pred.values():
        n = sum(c.values())
        same = sum(v * (v - 1) / 2 for v in c.values())
        tp += same
        fp += n * (n - 1) / 2 - same
    total_true = sum(v * (v - 1) / 2 for v in gt_sizes.values())
    fn = total_true - tp
    res["cluster_p"], res["cluster_r"], res["cluster_f1"] = prf(tp, fp, fn)
    # how is each true narrative split?
    res["narrative_split"] = {}
    for g in gt_sizes:
        c = Counter(r["narrative_id"] for r in sig if r["gt_narrative"] == g)
        top_n = c.most_common(1)[0][1]
        res["narrative_split"][g] = {"size": gt_sizes[g], "largest_cluster_share": round(top_n / gt_sizes[g], 2),
                                     "clusters": len(c)}

    # ---- coordination (copy-paste groups with >= k accounts) ----
    k = cfg["thresholds"]["coord_min_accounts"]
    group_accts = defaultdict(set)
    for r in rows:
        group_accts[r["dup_group"]].add(r["author"])
    tp = fp = fn = 0
    for r in rows:
        pred = len(group_accts[r["dup_group"]]) >= k
        gt = bool(r["gt_coordinated"])
        tp += pred and gt
        fp += pred and not gt
        fn += gt and not pred
    res["coord_p"], res["coord_r"], res["coord_f1"] = prf(tp, fp, fn)

    # ---- debunk matching ----
    tp = fp = fn = wrong = 0
    for r in rows:
        pred = fc_ext.get(r["debunk_id"]) if r["debunk_id"] else None
        gt = r["gt_debunk"]
        if pred and gt:
            tp += 1
            wrong += pred != gt
        elif pred:
            fp += 1
        elif gt:
            fn += 1
    res["debunk_p"], res["debunk_r"], res["debunk_f1"] = prf(tp, fp, fn)
    res["debunk_wrong_claim"] = wrong

    # ---- alerts ----
    first_cred = defaultdict(lambda: None)
    for r in sorted(rows, key=lambda r: r["ts"]):
        if r["credibility"] == "credible" and first_cred[r["gt_narrative"]] is None:
            first_cred[r["gt_narrative"]] = r["ts"]
    alerts = []
    for a in conn.execute("SELECT a.*, n.label FROM alerts a LEFT JOIN narratives n ON n.id=a.narrative_id "
                          "WHERE a.status!='merged'"):
        c = Counter(r["gt_narrative"] for r in rows if r["narrative_id"] == a["narrative_id"])
        gt, n = c.most_common(1)[0] if c else ("?", 0)
        coord_hist = [h["sim"] for h in jloads(a["history"], []) if "coordination" in h["event"]]
        coord_at = a["created_sim"] if a["type"] == "coordination" and not coord_hist else (coord_hist[0] if coord_hist else None)
        alerts.append({"id": a["id"], "gt": gt, "purity": round(n / max(sum(c.values()), 1), 2), "type": a["type"],
                       "severity": a["severity"], "confidence": a["confidence"], "created_sim": a["created_sim"],
                       "signals": sorted(jloads(a["signals"], {}).keys()), "label": a["label"], "coord_at": coord_at,
                       "first_credible": first_cred[gt]})
    res["alerts"] = alerts
    coord_narr = {a["narrative_id"] for a in conn.execute("SELECT narrative_id, signals FROM alerts WHERE status!='merged'")
                  if "coordination" in jloads(a["signals"], {})}
    caught = []
    for g in CAMPAIGNS:
        cp = [r for r in rows if r["gt_narrative"] == g and r["gt_coordinated"]]
        hit = [r for r in cp if r["narrative_id"] in coord_narr and len(group_accts[r["dup_group"]]) >= k]
        if cp and len(hit) >= 0.5 * len(cp):
            caught.append(g)
    res["campaigns_caught"] = sorted(caught)
    res["campaigns_missed"] = sorted(CAMPAIGNS - set(caught))
    # false alarm: a coordination alert whose copy-paste groups contain no truly coordinated posts
    false = []
    for nid in coord_narr:
        grp = [r for r in rows if r["narrative_id"] == nid and len(group_accts[r["dup_group"]]) >= k]
        if grp and not any(r["gt_coordinated"] for r in grp):
            false.append(Counter(r["gt_narrative"] for r in grp).most_common(1)[0][0])
    res["false_coordination_alerts"] = sorted(false)
    res["db"] = str(tmp)
    return res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default=None)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    cfg = load_config()
    data = Path(args.data) if args.data else resolve(cfg, cfg["replay"]["file"])
    r = run(cfg, data)
    if args.json:
        print(json.dumps(r, indent=1, default=str))
        return
    print(f"\nReplayed {r['posts']} posts in 5-minute steps ({r['seconds']:.1f}s). For raw speed run scripts/benchmark.py\n")
    print("NARRATIVE CLUSTERING (non-noise posts)")
    print(f"  pairwise precision {r['cluster_p']:.2f}  recall {r['cluster_r']:.2f}  F1 {r['cluster_f1']:.2f}  ARI {r['ari']:.2f}")
    for g, s in sorted(r["narrative_split"].items()):
        print(f"    {g:15s} {s['size']:4d} posts -> largest cluster holds {int(s['largest_cluster_share']*100)}% "
              f"({s['clusters']} clusters)")
    print("\nCOPY-PASTE COORDINATION (post level)")
    print(f"  precision {r['coord_p']:.2f}  recall {r['coord_r']:.2f}  F1 {r['coord_f1']:.2f}")
    print("\nDEBUNKED-CLAIM MATCHING (post level)")
    print(f"  precision {r['debunk_p']:.2f}  recall {r['debunk_r']:.2f}  F1 {r['debunk_f1']:.2f}  "
          f"(matched to wrong claim: {r['debunk_wrong_claim']})")
    print("\nALERTS")
    for a in sorted(r["alerts"], key=lambda a: a["created_sim"]):
        early = ""
        if "coordination" in a["signals"] and a["coord_at"]:
            fc = a["first_credible"]
            early = f"  coordination flagged {fmt_t(a['coord_at'])}"
            if a["gt"] in CAMPAIGNS and fc and fc > a["coord_at"]:
                early += f", {(fc - a['coord_at'])/60:.0f} min before first credible report"
        print(f"  #{a['id']:<3} {fmt_t(a['created_sim'])}  {a['type']:13s} {a['severity']:8s} conf {a['confidence']:.2f}  "
              f"truth={a['gt']:14s} purity {a['purity']:.2f}{early}")
    print(f"\n  campaigns caught: {r['campaigns_caught']}   missed: {r['campaigns_missed']}")
    print(f"  coordination alerts on organic narratives (false alarms): {r['false_coordination_alerts'] or 'none'}")


if __name__ == "__main__":
    main()
