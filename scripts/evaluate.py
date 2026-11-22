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
  * cyber verification     - verdict vs ground truth (confusion matrix), time from claim to verdict
  * hybrid links           - was each expected cyber <-> narrative link found
"""
from __future__ import annotations

import sys as _sys
for _s in (_sys.stdout, _sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")   # Windows console (cp1252)
    except Exception:
        pass

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
from hybridwatch.events import EventDetector  # noqa: E402
from hybridwatch.hybrid import Linker  # noqa: E402
from hybridwatch.pipeline import Pipeline  # noqa: E402
from hybridwatch.verify import Verifier, add_event  # noqa: E402

CAMPAIGNS = {"water_poison", "kfor_withdraw", "power_sabotage"}


def prf(tp, fp, fn):
    p = tp / (tp + fp) if tp + fp else 0.0
    r = tp / (tp + fn) if tp + fn else 0.0
    f = 2 * p * r / (p + r) if p + r else 0.0
    return p, r, f


def fmt_t(ts):
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%d %b %H:%M")


def run(cfg: dict, data: Path, step_minutes: int = 5, quiet=False, cyber: Path | None = None) -> dict:
    posts = [json.loads(l) for l in open(data, encoding="utf-8") if l.strip()]
    posts.sort(key=lambda p: p["ts"])
    tmp = Path(tempfile.mkdtemp()) / "eval.db"
    pipe = Pipeline(cfg, db_path=str(tmp), quiet=True)
    pipe.factchecks.refresh_sources([fc for fc in cfg["factcheckers"] if fc.get("type") == "file" and fc.get("enabled")])
    # cyber claims (with ground truth) are released by the same clock, then verified and linked
    events = []
    if cyber and cyber.exists():
        events = sorted(json.load(open(cyber, encoding="utf-8")), key=lambda e: e["first_seen"])
    verifier = Verifier(pipe.conn, cfg, log=lambda *a: None)
    linker = Linker(pipe.conn, cfg, pipe.glossary, pipe.threat)

    t0 = time.perf_counter()
    i, j, clock = 0, 0, posts[0]["ts"]
    while i < len(posts) or j < len(events):
        clock += step_minutes * 60
        batch = []
        while i < len(posts) and posts[i]["ts"] <= clock:
            batch.append(posts[i])
            i += 1
        if batch:
            pipe.process(batch)
        if events:
            while j < len(events) and events[j]["first_seen"] <= clock:
                add_event(pipe.conn, events[j], clock)
                j += 1
            verifier.run(clock)
            linker.run(clock, pipe.alerts)
    EventDetector(pipe.conn, cfg, pipe.threat).run(clock, force=True)
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

    # ---- cyber verification vs ground truth ----
    if events:
        # an alert "contains" a true narrative if it holds at least half of that narrative's posts
        # (a campaign can be merged into the story it distorts, e.g. power_sabotage into power_outage)
        gt_size = Counter(r["gt_narrative"] for r in rows)
        nar_of_alert = {}
        for a in conn.execute("SELECT id, narrative_id FROM alerts"):
            c = Counter(r["gt_narrative"] for r in rows if r["narrative_id"] == a["narrative_id"])
            nar_of_alert[a["id"]] = sorted(g for g, n in c.items()
                                           if g != "noise" and gt_size[g] >= 5 and n >= 0.5 * gt_size[g])
        cy = []
        for e in conn.execute("SELECT * FROM cyber_events ORDER BY claimed_at"):
            raw = jloads(e["raw"], {})
            linked = sorted({g for h in conn.execute("SELECT alert_id FROM hybrid_links WHERE cyber_event_id=?", (e["id"],))
                             for g in nar_of_alert.get(h["alert_id"], [])})
            cy.append({"id": e["id"], "kind": e["kind"], "title": e["title"], "gt": raw.get("gt_status"),
                       "status": e["status"], "admiralty": f"{e['source_reliability']}{e['info_credibility']}",
                       "verdict_min": (e["verdict_at"] - e["claimed_at"]) / 60 if e["verdict_at"] else None,
                       "gt_hybrid": raw.get("gt_hybrid"), "linked_to": linked})
        res["cyber"] = cy
    # ---- events: category and place per true story ----
    gt_of_post = {r["id"]: r["gt_narrative"] for r in rows}
    gt_posts = Counter(gt_of_post.values())
    evs = [dict(e) | {"posts": jloads(e["post_ids"], [])} for e in conn.execute("SELECT * FROM events")]
    ev_res = []
    for g, (cat, place) in EXPECTED_EVENTS.items():
        if not gt_posts.get(g):
            continue
        cover = [e for e in evs if sum(gt_of_post.get(p) == g for p in e["posts"]) >= 0.3 * gt_posts[g]]
        hit = [e for e in cover if e["category"] == cat]
        # a non-threat story is wrong only if an event is mainly made of it (a few posts clustered into a
        # real event do not make it a threat event)
        major = [e for e in cover if Counter(gt_of_post.get(p) for p in e["posts"]).most_common(1)[0][0] == g]
        if not cat:
            cover = major
        ev_res.append({"gt": g, "expected": cat, "place": place, "found": bool(hit) if cat else not cover,
                       "got": sorted({e["category"] for e in cover}),
                       "got_place": hit[0]["place"] if hit else None,
                       "place_ok": (not place) or bool(hit and hit[0]["place"] == place)})
    res["events"] = ev_res
    res["events_total"] = len(evs)
    res["db"] = str(tmp)
    return res


# true story -> (expected event category, expected place); None = should NOT become a threat event
EXPECTED_EVENTS = {
    "power_outage": ("infrastructure", "Pristina"), "snow_roads": ("infrastructure", None),
    "water_poison": ("disinformation", None), "kfor_withdraw": ("disinformation", None),
    "power_sabotage": ("disinformation", None), "euro_freeze": ("disinformation", None),
    "jarinje_protest": ("organized_activity", "Jarinje crossing"), "merdare_smuggling": ("organized_activity", "Merdare crossing"),
    "zvecan_attack": ("security_incident", "Zvečan"), "belgrade_army": ("regional", "Belgrade (Serbia)"),
    "football": (None, None), "energy_prices": (None, None),
}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default=None)
    ap.add_argument("--cyber", default=None, help="cyber events JSON with gt_status (default: data/demo_cyber.json)")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    cfg = load_config()
    data = Path(args.data) if args.data else resolve(cfg, cfg["replay"]["file"])
    cyber = Path(args.cyber) if args.cyber else (None if args.data else resolve(cfg, "data/demo_cyber.json"))
    r = run(cfg, data, cyber=cyber)
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
    if r.get("cyber"):
        print_cyber(r["cyber"])
    if r.get("events"):
        print(f"\nEVENTS (category and place per true story; {r['events_total']} events detected)")
        for e in r["events"]:
            exp = e["expected"] or "no threat event"
            mark = "ok " if e["found"] and e["place_ok"] else "XX "
            where = f"  place {e['got_place']}" + ("" if e["place_ok"] else f" (expected {e['place']})") if e["expected"] else ""
            print(f"  {mark}{e['gt']:18s} expected {exp:21s} got {', '.join(e['got']) or 'nothing':32s}{where}")
        n = len(r["events"])
        print(f"  category correct {sum(e['found'] for e in r['events'])}/{n}   "
              f"place correct {sum(e['found'] and e['place_ok'] for e in r['events'])}/{n}")


def print_cyber(cy: list[dict]) -> None:
    st = ["confirmed", "partly_confirmed", "refuted", "unverified"]
    print("\nCYBER CLAIM VERIFICATION")
    for e in cy:
        ok = "ok " if e["status"] == e["gt"] else "XX "
        t = f"verdict after {e['verdict_min']:.0f} min" if e["verdict_min"] is not None else "no verdict"
        print(f"  {ok}#{e['id']} {e['kind']:17s} truth={e['gt']:10s} got={e['status']:17s} {e['admiralty']}  {t:22s} {e['title']}")
    print("\n  confusion matrix (rows = truth, columns = verdict)")
    print("  " + " " * 17 + "".join(f"{s[:9]:>11s}" for s in st))
    for g in st:
        if any(e["gt"] == g for e in cy):
            print(f"  {g:17s}" + "".join(f"{sum(1 for e in cy if e['gt'] == g and e['status'] == s):>11d}" for s in st))
    acc = sum(e["status"] == e["gt"] for e in cy) / len(cy)
    times = sorted(e["verdict_min"] for e in cy if e["verdict_min"] is not None)
    print(f"  accuracy {acc:.2f}" + (f"   median time claim -> verdict {times[len(times) // 2]:.0f} min" if times else ""))
    print("\nHYBRID LINKS (cyber event -> disinformation narrative)")
    for e in cy:
        if e["gt_hybrid"] or e["linked_to"]:
            found = e["gt_hybrid"] in e["linked_to"]
            print(f"  #{e['id']} {e['title']}: expected link to {e['gt_hybrid']} -> {'FOUND' if found else 'MISSED'}"
                  f"  (linked alert holds: {', '.join(e['linked_to']) or 'nothing'})")
    extra = ", ".join("#" + str(e["id"]) for e in cy if e["linked_to"] and not e["gt_hybrid"])
    print(f"  links on events that should have none: {extra or 'none'}")


if __name__ == "__main__":
    main()
