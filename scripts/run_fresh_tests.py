"""Run every FRESH (held-out) test week and add the results up.

Weeks 1-3 are hand-written (make_holdout_data.py, make_holdout2_data.py, make_holdout3_data.py),
weeks 4-8 are drawn from templates (make_holdout_bulk.py). None of them was used to tune HybridWatch.

    py scripts/run_fresh_tests.py                 # all weeks 1-8
    py scripts/run_fresh_tests.py --weeks 3-8     # only some weeks

Writes data/fresh_results.json and prints a summary.
"""
from __future__ import annotations

import sys as _sys
for _s in (_sys.stdout, _sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

import json
import subprocess
import sys
import time
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT))
_argv = sys.argv[:]
sys.argv = [sys.argv[0]]
import evaluate  # noqa: E402
import evaluate_holdout  # noqa: E402  (only for the hand-written sets' ground truth)
sys.argv = _argv

LOAD = evaluate_holdout._load
FOLDER = {1: "holdout", 2: "holdout2", 3: "holdout3"}
MAKER = {1: ["make_holdout_data.py"], 2: ["make_holdout2_data.py"], 3: ["make_holdout3_data.py"]}


def week_setup(w: int):
    folder = FOLDER.get(w, f"holdout{w}")
    d = ROOT / "data" / folder
    if not (d / "posts.jsonl").exists():
        cmd = MAKER.get(w, ["make_holdout_bulk.py", "--week", str(w)])
        subprocess.run([sys.executable, str(HERE / cmd[0])] + cmd[1:], check=True)
    if w in (1, 2, 3):
        _, camps, expected = evaluate_holdout.SETS[str(w)]
    else:
        t = json.load(open(d / "truth.json", encoding="utf-8"))
        camps, expected = set(t["campaigns"]), {k: tuple(v) for k, v in t["events"].items()}
    return d, set(camps), expected


def summarise(r: dict, camps: set, expected: dict) -> dict:
    lead = [round((a["first_credible"] - a["coord_at"]) / 60) for a in r["alerts"]
            if a["gt"] in camps and a["coord_at"] and a["first_credible"] and a["first_credible"] > a["coord_at"]]
    cy = r.get("cyber", [])
    right = sum(e["status"] == e["gt"] for e in cy)
    wrong = sum(e["status"] != e["gt"] and e["status"] in ("confirmed", "refuted") for e in cy)
    hyb_exp = [e for e in cy if e["gt_hybrid"]]
    by_cat = defaultdict(lambda: [0, 0])
    for e in r["events"]:
        cat = e["expected"] or "harmless"
        ok = e["found"] and e["place_ok"]
        by_cat[cat][0] += ok
        by_cat[cat][1] += 1
    threat = [e for e in r["events"] if e["expected"]]
    return {
        "campaigns_caught": len(set(r["campaigns_caught"]) & camps), "campaigns": len(camps),
        "false_coordination_alarms": len(r["false_coordination_alerts"]), "lead_minutes": sorted(lead),
        "hybrid_found": sum(e["gt_hybrid"] in e["linked_to"] for e in hyb_exp), "hybrid_expected": len(hyb_exp),
        "hybrid_false": sum(1 for e in cy if e["linked_to"] and not e["gt_hybrid"]),
        "cyber_right": right, "cyber_wrong": wrong, "cyber_open": len(cy) - right - wrong, "cyber_total": len(cy),
        "events_right": sum(e["found"] and e["place_ok"] for e in threat), "events_total": len(threat),
        "harmless_ignored": by_cat["harmless"][0], "harmless_total": by_cat["harmless"][1],
        "by_category": {k: v for k, v in by_cat.items()},
        "copy_p": round(r["coord_p"], 2), "copy_r": round(r["coord_r"], 2), "debunk_p": round(r["debunk_p"], 2), "debunk_r": round(r["debunk_r"], 2),
        "grouping_f1": round(r["cluster_f1"], 2), "posts": r["posts"], "seconds": round(r["seconds"]),
    }


def main():
    weeks = list(range(1, 9))
    if "--weeks" in sys.argv:
        a, b = sys.argv[sys.argv.index("--weeks") + 1].split("-")
        weeks = list(range(int(a), int(b) + 1))
    out_path = ROOT / "data" / "fresh_results.json"
    results = json.load(open(out_path, encoding="utf-8")) if out_path.exists() else {}
    results.setdefault("weeks", {})
    for w in weeks:
        d, camps, expected = week_setup(w)
        evaluate.CAMPAIGNS, evaluate.EXPECTED_EVENTS = camps, expected
        cfg = LOAD()
        cfg["factcheckers"] = [{"name": "Held-out fact-checks", "type": "file", "url": str(d / "factchecks.json"), "enabled": True}]
        t0 = time.time()
        print(f"week {w}: running ({d.name}) ...", flush=True)
        r = evaluate.run(cfg, d / "posts.jsonl", cyber=d / "cyber.json")
        s = summarise(r, camps, expected)
        results["weeks"][str(w)] = s
        print(f"week {w}: campaigns {s['campaigns_caught']}/{s['campaigns']} (false alarms {s['false_coordination_alarms']}) · "
              f"hybrid {s['hybrid_found']}/{s['hybrid_expected']} · cyber right {s['cyber_right']}/{s['cyber_total']} wrong {s['cyber_wrong']} · "
              f"events {s['events_right']}/{s['events_total']} · harmless {s['harmless_ignored']}/{s['harmless_total']} · {time.time() - t0:.0f}s", flush=True)
        json.dump(results, open(out_path, "w", encoding="utf-8"), indent=1)

    W = results["weeks"].values()
    tot = {k: sum(x[k] for x in W) for k in ("campaigns_caught", "campaigns", "false_coordination_alarms", "hybrid_found", "hybrid_expected", "hybrid_false",
                                             "cyber_right", "cyber_wrong", "cyber_open", "cyber_total", "events_right", "events_total",
                                             "harmless_ignored", "harmless_total", "posts")}
    lead = sorted(m for x in W for m in x["lead_minutes"])
    cats = defaultdict(lambda: [0, 0])
    for x in W:
        for c, (a, b) in x["by_category"].items():
            cats[c][0] += a
            cats[c][1] += b
    tot["lead_minutes_min"], tot["lead_minutes_median"], tot["lead_minutes_max"] = (lead[0], lead[len(lead) // 2], lead[-1]) if lead else (None, None, None)
    tot["by_category"] = dict(cats)
    tot["weeks"] = sorted(int(k) for k in results["weeks"])
    results["total"] = tot
    json.dump(results, open(out_path, "w", encoding="utf-8"), indent=1)
    print("\nTOTAL over weeks", tot["weeks"])
    print(f"  posts replayed            {tot['posts']:,}")
    print(f"  campaigns caught          {tot['campaigns_caught']}/{tot['campaigns']}   false coordination alarms {tot['false_coordination_alarms']}")
    print(f"  early warning (minutes)   min {tot['lead_minutes_min']}  median {tot['lead_minutes_median']}  max {tot['lead_minutes_max']}")
    print(f"  hybrid links              {tot['hybrid_found']}/{tot['hybrid_expected']}   false links {tot['hybrid_false']}")
    print(f"  hacker claims             right {tot['cyber_right']}  left open {tot['cyber_open']}  wrong {tot['cyber_wrong']}  (of {tot['cyber_total']})")
    print(f"  events right type+place   {tot['events_right']}/{tot['events_total']}")
    print(f"  harmless stories ignored  {tot['harmless_ignored']}/{tot['harmless_total']}")
    for c, (a, b) in sorted(cats.items()):
        print(f"    {c:20s} {a}/{b}")
    print(f"\nSaved {out_path}")


if __name__ == "__main__":
    main()
