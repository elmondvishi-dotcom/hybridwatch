"""Every test in the pitch, in one command, with the models the app actually uses.

    py scripts/run_tests.py              # everything (XLM-RoBERTa if installed, LLM comparison if a key is set)
    py scripts/run_tests.py --no-llm     # skip the LLM comparison (no internet needed)

Models tested
  * Keyword rule ................. hand-written baseline (what a person would write)
  * Text model A ................. TF-IDF character + word n-grams -> logistic regression, isotonic calibration
  * Text model B ................. XLM-RoBERTa sentence embeddings (paraphrase-multilingual-mpnet-base-v2)
                                   -> logistic regression, isotonic calibration
  * Account model ................ gradient-boosted trees (depth 3) on account behaviour, isotonic calibration
  * Text + account ............... average of a text model and the account model
  * LLM (zero-shot) .............. the configured LLM answers without any training, as a comparison
  * Full system .................. XLM-RoBERTa story grouping, MinHash/LSH copy-paste, fact-check matching,
                                   threat score (threat.py), events (events.py), cyber verification (verify.py),
                                   hybrid links (hybrid.py)

Tests
  1. True / false posts: train on 2,000 labelled posts, test on T1 (same events), T2 (new events),
     T3 (new events + new wording). Accuracy, F1, calibration (ECE, Brier), "when >= 90% sure".
  2. Full system on the 48-hour synthetic scenario (scripts/evaluate.py): story grouping, copy-paste
     coordination, debunked claims, alerts and threat levels, cyber verification, hybrid links, events.
  3. Speed on this computer.

Writes data/test_results.json and data/test_report.md. LLM answers are cached in data/llm_test_cache.jsonl,
so a second run costs nothing. All data is synthetic.
"""
from __future__ import annotations

import sys as _sys
for _s in (_sys.stdout, _sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")   # Windows console (cp1252) cannot print ✓ ≥ σ
    except Exception:
        pass

import argparse
import hashlib
import json
import platform
import random
import re
import subprocess
import sys
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from hybridwatch.config import load_config, resolve  # noqa: E402
from hybridwatch.embed import HashingEmbedder, make_embedder  # noqa: E402
from hybridwatch.llm import LLM  # noqa: E402
from hybridwatch.text import Glossary  # noqa: E402

DATA = ROOT / "data"
TRUTH = DATA / "truth"


def say(*a):
    print(*a, flush=True)


class CachedLLM:
    """LLM calls with a cache file, a gap between calls (free-tier rate limits) and retries."""

    def __init__(self, llm: LLM, gap: float):
        self.llm, self.gap, self.last, self.calls = llm, gap, 0.0, 0
        self.path = DATA / "llm_test_cache.jsonl"
        self.cache = {}
        if self.path.exists():
            for line in self.path.read_text(encoding="utf-8").splitlines():
                try:
                    r = json.loads(line)
                    self.cache[r["k"]] = r["v"]
                except Exception:
                    pass

    def ask(self, prompt: str, max_tokens: int) -> str | None:
        k = hashlib.sha1((str(self.llm.model) + prompt).encode()).hexdigest()
        if k in self.cache:
            return self.cache[k]
        for attempt in range(4):
            wait = self.gap - (time.time() - self.last)
            if wait > 0:
                time.sleep(wait)
            self.last = time.time()
            ans = self.llm.ask(prompt, max_tokens, temperature=0, timeout=120)
            self.calls += 1
            if ans:
                self.cache[k] = ans
                with open(self.path, "a", encoding="utf-8") as f:
                    f.write(json.dumps({"k": k, "v": ans}, ensure_ascii=False) + "\n")
                return ans
            time.sleep(15 * (attempt + 1))
        return None


def parse_list(text):
    if not text:
        return []
    m = re.search(r"\[.*\]", text, re.S)
    try:
        return json.loads(m.group(0)) if m else []
    except Exception:
        return []


JUDGE = """You are a fact-checking analyst. For each social media post about a security incident in Kosovo,
decide whether it is a TRUE, accurate report or a FALSE / fabricated claim (disinformation).
Reply with ONLY a JSON array, one object per post, in order: {{"i": <index>, "label": "true"|"false", "confidence": 0.5-1.0}}

{posts}"""


# ---------------------------------------------------------------- 1. true / false
def truth_test(emb, cllm, judge_n: int) -> dict:
    import truth_experiment as te
    from sklearn.calibration import CalibratedClassifierCV
    from sklearn.linear_model import LogisticRegression
    if not (TRUTH / "train.jsonl").exists():
        say("  generating the labelled true/false data ...")
        subprocess.run([sys.executable, str(ROOT / "scripts" / "truth_data.py")], check=True)
    train = te.load("train")
    tests = {k: te.load(k) for k in te.SETS}
    say(f"  training on {len(train)} labelled posts ...")
    m = te.train_all(train)
    xlmr = None
    if not isinstance(emb, HashingEmbedder):
        say(f"  embedding {len(train) + sum(len(v) for v in tests.values())} posts with {emb.name} ...")
        t0 = time.time()
        Etr = emb.embed([r["text"] for r in train])
        xlmr = CalibratedClassifierCV(LogisticRegression(C=1.0, max_iter=4000), cv=5, method="isotonic").fit(Etr, te.y_of(train))
        Ete = {k: emb.embed([r["text"] for r in rows]) for k, rows in tests.items()}
        say(f"    done in {time.time() - t0:.0f} s")
    out = {"sizes": {"train": len(train), **{k: len(v) for k, v in tests.items()}}, "sets": {}}
    preds_all = {}
    for k, rows in tests.items():
        y = te.y_of(rows)
        P = te.predict_all(m, rows)
        preds = {"Keyword rule": P["Keyword rule"], "TF-IDF text model": P["Text model"],
                 "Account model": P["Account model"], "TF-IDF + account": (P["Text model"] + P["Account model"]) / 2}
        if xlmr is not None:
            px = xlmr.predict_proba(Ete[k])[:, 1]
            preds["XLM-RoBERTa text model"] = px
            preds["XLM-RoBERTa + account"] = (px + P["Account model"]) / 2
        preds_all[k] = preds
        out["sets"][k] = {name: te.metrics(y, p) for name, p in preds.items()}
        say(f"  {k}: " + "  ".join(f"{n} {v['accuracy']:.1%}" for n, v in out["sets"][k].items()))
    if cllm and judge_n:
        rows = tests["t3"]
        idx = random.Random(7).sample(range(len(rows)), min(judge_n, len(rows)))
        pj = np.full(len(idx), np.nan)
        say(f"  the LLM judges {len(idx)} T3 posts zero-shot ...")
        for b in range(0, len(idx), 20):
            chunk = idx[b:b + 20]
            posts = "\n".join(f"{j}: {rows[i]['text']}" for j, i in enumerate(chunk))
            for o in parse_list(cllm.ask(JUDGE.format(posts=posts), 60 * len(chunk) + 100)):
                try:
                    j = int(o["i"])
                    c = min(max(float(o.get("confidence", 0.75)), 0.5), 1.0)
                    pj[b + j] = c if str(o["label"]).lower().startswith("f") else 1 - c
                except Exception:
                    pass
        ok = ~np.isnan(pj)
        if ok.sum():
            y = te.y_of([rows[i] for i in idx])[ok]
            sub = {"LLM zero-shot": te.metrics(y, pj[ok])}
            for name, p in preds_all["t3"].items():
                if name != "Keyword rule":
                    sub[name] = te.metrics(y, np.asarray(p)[np.array(idx)][ok])
            out["llm_judge_t3"] = {"answered": int(ok.sum()), "asked": len(idx), "model": cllm.llm.model, "models": sub}
            say(f"    LLM {sub['LLM zero-shot']['accuracy']:.1%} on {int(ok.sum())} posts")
    return out


# ---------------------------------------------------------------- 2. full system
def system_test(cfg) -> dict:
    import evaluate
    say("  replaying the 48-hour scenario (posts + cyber claims) through the real pipeline ...")
    data = resolve(cfg, cfg["replay"]["file"])
    cyber = resolve(cfg, "data/demo_cyber.json")
    r = evaluate.run(cfg, data, cyber=cyber if cyber.exists() else None, quiet=True)
    out = {}
    for k, v in r.items():
        if k in ("db", "narrative_split"):
            continue
        out[k] = round(v, 3) if isinstance(v, float) else v
    cy = r.get("cyber") or []
    if cy:
        out["cyber_accuracy"] = round(sum(1 for c in cy if c["gt"] == c["status"]) / len(cy), 3)
        mins = sorted(c["verdict_min"] for c in cy if c["verdict_min"] is not None)
        out["cyber_median_verdict_min"] = round(mins[len(mins) // 2]) if mins else None
        exp = [c for c in cy if c.get("gt_hybrid")]
        out["hybrid_links_found"] = sum(1 for c in exp if c["gt_hybrid"] in c["linked_to"])
        out["hybrid_links_expected"] = len(exp)
        out["hybrid_false_links"] = sum(1 for c in cy if not c.get("gt_hybrid") and c["linked_to"])
    ev = r.get("events") or []
    if ev:
        out["events_category_ok"] = sum(1 for e in ev if e["found"])
        out["events_place_ok"] = sum(1 for e in ev if e["place_ok"])
        out["events_n"] = len(ev)
    say(f"    coordination F1 {r.get('coord_f1', 0):.2f} · debunk P/R {r.get('debunk_p', 0):.2f}/{r.get('debunk_r', 0):.2f} · "
        f"campaigns {len(r.get('campaigns_caught', []))}/{len(r.get('campaigns_caught', [])) + len(r.get('campaigns_missed', []))} · "
        f"cyber {out.get('cyber_accuracy')} · events {out.get('events_category_ok')}/{out.get('events_n')} · "
        f"{r.get('posts_per_sec', 0):.0f} posts/s")
    return out


# ---------------------------------------------------------------- 3. speed
def speed_test(cfg, emb) -> dict:
    from hybridwatch.threat import ThreatModel
    posts = [json.loads(l)["text"] for l in open(resolve(cfg, cfg["replay"]["file"]), encoding="utf-8") if l.strip()][:2000]
    tm = ThreatModel(cfg, None, log=lambda *a: None)
    t0 = time.perf_counter()
    for t in posts:
        tm.post(None, t)
    tp = len(posts) / (time.perf_counter() - t0)
    t0 = time.perf_counter()
    emb.embed(posts[:500])
    e = 500 / (time.perf_counter() - t0)
    return {"threat_post_analysis_per_s": round(tp), "embedding_per_s": round(e, 1)}


# ---------------------------------------------------------------- report
def pct(x):
    return "—" if x is None else f"{x * 100:.1f}%"


def write_report(R: dict) -> str:
    L = ["# HybridWatch test results", "", f"Run {R['env']['when']} on {R['env']['machine']} · Python {R['env']['python']}", "",
         "## Models", "", "| Part | Model |", "|---|---|"]
    L += [f"| {k} | {v} |" for k, v in R["env"]["models"].items()]
    t = R.get("truth")
    if t:
        L += ["", "## 1. True / false posts (accuracy on posts never seen in training)", "",
              "| Model | T1 same events | T2 new events | T3 new events + new wording | T3 ECE | T3 when ≥90% sure |",
              "|---|---|---|---|---|---|"]
        for name in t["sets"]["t3"]:
            s = {k: t["sets"][k][name] for k in ("t1", "t2", "t3")}
            L.append(f"| {name} | {pct(s['t1']['accuracy'])} | {pct(s['t2']['accuracy'])} | **{pct(s['t3']['accuracy'])}** | "
                     f"{s['t3']['ece']:.3f} | {pct(s['t3']['sure_acc'])} on {pct(s['t3']['sure_share'])} |")
        j = t.get("llm_judge_t3")
        if j:
            L += ["", f"**LLM ({j['model']}) zero-shot vs trained models on the same {j['answered']} T3 posts:**", "",
                  "| Model | Accuracy | F1 |", "|---|---|---|"]
            L += [f"| {n} | {pct(v['accuracy'])} | {v['f1']:.2f} |" for n, v in j["models"].items()]
    s = R.get("system")
    if s:
        L += ["", "## 2. Full system on the 48-hour synthetic scenario", "",
              f"Replayed {s.get('posts')} posts at {s.get('posts_per_sec', 0):.0f} posts/s (embedder: {R['env']['models']['Story grouping & fact-check matching']})", "",
              "| Check | Result |", "|---|---|",
              f"| Story grouping (pairwise F1 / ARI) | {s.get('cluster_f1')} / {s.get('ari')} |",
              f"| Copy-paste coordination P / R / F1 | {s.get('coord_p')} / {s.get('coord_r')} / {s.get('coord_f1')} |",
              f"| Debunked-claim matching P / R / F1 | {s.get('debunk_p')} / {s.get('debunk_r')} / {s.get('debunk_f1')} |",
              f"| Campaigns caught | {len(s.get('campaigns_caught', []))} of {len(s.get('campaigns_caught', [])) + len(s.get('campaigns_missed', []))} |",
              f"| Coordination alerts on organic stories | {len(s.get('false_coordination_alerts', []))} |",
              f"| Cyber claims verified correctly | {pct(s.get('cyber_accuracy'))} · median {s.get('cyber_median_verdict_min')} min to verdict |",
              f"| Hybrid links found / expected / false | {s.get('hybrid_links_found')} / {s.get('hybrid_links_expected')} / {s.get('hybrid_false_links')} |",
              f"| Events: category right / place right | {s.get('events_category_ok')}/{s.get('events_n')} · {s.get('events_place_ok')}/{s.get('events_n')} |"]
        if s.get("alerts"):
            L += ["", "| Alert | Type | Level | Confidence | True story | Purity |", "|---|---|---|---|---|---|"]
            for a in s["alerts"]:
                L.append(f"| #{a['id']} | {a['type']} | {a['severity']} | {a['confidence']} | {a['gt']} | {a['purity']} |")
    sp = R.get("speed")
    if sp:
        L += ["", "## 3. Speed on this computer", "",
              f"Threat analysis {sp['threat_post_analysis_per_s']:,} posts/s · embedding {sp['embedding_per_s']} posts/s"]
    L += ["", "*All data is synthetic: it shows the method works as designed, not field accuracy.*"]
    return "\n".join(L)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-llm", action="store_true", help="skip the LLM comparison")
    ap.add_argument("--judge", type=int, default=200, help="T3 posts the LLM judges zero-shot")
    ap.add_argument("--gap", type=float, default=2.5, help="seconds between LLM calls (free-tier rate limit)")
    a = ap.parse_args()
    cfg = load_config()
    emb = make_embedder(cfg, Glossary(cfg.get("glossary", {})))
    llm = LLM(cfg)
    cllm = None
    if llm.enabled and not a.no_llm:
        if llm.ask("Reply with the single word OK.", 5) is None:
            say("! the LLM did not answer - LLM comparison skipped (check the key named in config.yaml llm.api_key_env)")
        else:
            cllm = CachedLLM(llm, a.gap)
    neural = not isinstance(emb, HashingEmbedder)
    models = {
        "Story grouping & fact-check matching": getattr(emb, "name", "hashing embedder (XLM-RoBERTa not installed)"),
        "Copy-paste detection": "MinHash (64 values) + LSH (16 bands x 4 rows)",
        "True/false text model A": "TF-IDF char 2-5 + word 1-2 grams -> logistic regression (isotonic calibration)",
        "True/false text model B": (f"{emb.name} embeddings -> logistic regression (isotonic calibration)"
                                    if neural else "not run (XLM-RoBERTa not installed)"),
        "Account model": "gradient-boosted trees, depth 3 (isotonic calibration)",
        "Threat score": "threat.py: 5 factors (target, scale, hostility, credibility, escalation), 1-5 each -> 0-100",
        "Events": "events.py: category + place (38 municipalities)",
        "Cyber verification": "verify.py: technical + independent checks -> Admiralty code",
        "Hybrid links": "hybrid.py: cyber event <-> disinformation narrative (time, target, topic)",
        "LLM": f"{llm.model if llm.enabled else 'none'} ({'used' if cllm else 'not used'}) - labels, translations, zero-shot comparison",
    }
    R = {"env": {"when": time.strftime("%Y-%m-%d %H:%M"), "machine": platform.platform(),
                 "python": platform.python_version(), "models": models}}
    say("\n1/3 True / false posts")
    R["truth"] = truth_test(emb, cllm, a.judge)
    say("\n2/3 Full system")
    R["system"] = system_test(cfg)
    say("\n3/3 Speed")
    R["speed"] = speed_test(cfg, emb)
    say(f"    {R['speed']}")
    if cllm:
        R["env"]["llm_calls"] = cllm.calls
    (DATA / "test_results.json").write_text(json.dumps(R, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    (DATA / "test_report.md").write_text(write_report(R), encoding="utf-8")
    say(f"\nDone. Results: {DATA / 'test_report.md'}  (and test_results.json)")


if __name__ == "__main__":
    main()
