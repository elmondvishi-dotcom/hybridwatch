"""How right is it? Train supervised models on labelled TRUE/FALSE posts, test on posts they never saw.

    py scripts/truth_data.py          # make the dataset (or use your own labelled JSONL, same fields)
    py scripts/truth_experiment.py    # train, test, write data/truth/report.html

Models (all supervised, all trained ONLY on train.jsonl):
  0. Keyword rule      - "false if it shouts or says urgent/share/sources say" (what a person would hand-write)
  1. Text model        - TF-IDF character + word n-grams -> logistic regression (calibrated)
  2. Account model     - gradient-boosted trees on account age, followers, activity, copy-paste group size
  3. Stacked model     - a second model learns how to combine text probability + account features
  4. Averaged model    - the average of the text and account models' probabilities (more robust)

Test sets (labels hidden from the model, used only to score it):
  t1 same events, same wording · t2 NEW events · t3 NEW events + NEW wording style
Reports accuracy, precision/recall/F1 (for catching FALSE posts), confusion matrix, calibration
(does 90% certain mean right 9/10?), accuracy when it is very sure, hard cases, per language,
a learning curve (how many labels are needed) and the words that drive its decisions.
"""
from __future__ import annotations

import sys as _sys
for _s in (_sys.stdout, _sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")   # Windows console (cp1252) cannot print ✓ ≥ σ
    except Exception:
        pass

import json
import math
import re
import sys
from pathlib import Path

import numpy as np
from scipy.sparse import hstack
from sklearn.calibration import CalibratedClassifierCV
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.model_selection import cross_val_predict

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from hybridwatch.text import normalize  # noqa: E402

DATA = Path(__file__).resolve().parent.parent / "data" / "truth"
SETS = {"t1": "Same events, same wording", "t2": "New events", "t3": "New events + new wording"}


def load(name):
    return [json.loads(l) for l in open(DATA / f"{name}.jsonl", encoding="utf-8") if l.strip()]


def y_of(rows):
    return np.array([1 if r["label"] == "false" else 0 for r in rows])   # 1 = FALSE post (what we want to catch)


# ---------------------------------------------------------------- features
class TextFeatures:
    def __init__(self):
        self.char = TfidfVectorizer(analyzer="char_wb", ngram_range=(2, 5), min_df=2, lowercase=False, sublinear_tf=True)
        self.word = TfidfVectorizer(analyzer="word", ngram_range=(1, 2), min_df=2, token_pattern=r"\S+", sublinear_tf=True)

    def fit(self, texts):
        self.char.fit(texts)
        self.word.fit([normalize(t) for t in texts])
        return self

    def transform(self, texts):
        return hstack([self.char.transform(texts), self.word.transform([normalize(t) for t in texts])]).tocsr()


def account_matrix(rows):
    return np.array([[math.log1p(r["account_age_days"]), math.log1p(r["followers"]), r["posts_per_day"],
                      math.log1p(r["copy_group_size"]), float(r["is_outlet"])] for r in rows])


KEYWORDS = ["urgjent", "hitno", "urgent", "shperndaj", "podelite", "share", "fshij", "obrisu", "delete", "heshtin",
            "cute", "silent", "fshehin", "kriju", "hiding", "burime", "izvori", "sources", "shok", "drug", "friend", "insajder", "insider"]


def keyword_rule(rows):
    out = []
    for r in rows:
        t = r["text"]
        n = normalize(t)
        letters = [c for c in t if c.isalpha()]
        shouting = letters and sum(c.isupper() for c in letters) / len(letters) > 0.5
        out.append(1 if shouting or "!!" in t or any(k in n for k in KEYWORDS) else 0)
    return np.array(out)


# ---------------------------------------------------------------- models
def train_all(train, seed=0):
    texts = [r["text"] for r in train]
    y = y_of(train)
    tf = TextFeatures().fit(texts)
    Xt = tf.transform(texts)
    text_model = CalibratedClassifierCV(LogisticRegression(C=4.0, max_iter=2000), cv=5, method="isotonic").fit(Xt, y)
    Xa = account_matrix(train)
    acc_model = CalibratedClassifierCV(HistGradientBoostingClassifier(max_depth=3, random_state=seed), cv=5,
                                       method="isotonic").fit(Xa, y)
    # stacking: out-of-fold text probabilities so the combiner never sees a score made on its own training row
    oof = cross_val_predict(LogisticRegression(C=4.0, max_iter=2000), Xt, y, cv=5, method="predict_proba")[:, 1]
    comb = CalibratedClassifierCV(HistGradientBoostingClassifier(max_depth=3, random_state=seed), cv=5,
                                  method="isotonic").fit(np.column_stack([oof, Xa]), y)
    return {"tf": tf, "text": text_model, "acc": acc_model, "comb": comb}


def predict_all(m, rows):
    Xt = m["tf"].transform([r["text"] for r in rows])
    Xa = account_matrix(rows)
    p_text = m["text"].predict_proba(Xt)[:, 1]
    p_acc = m["acc"].predict_proba(Xa)[:, 1]
    p_comb = m["comb"].predict_proba(np.column_stack([p_text, Xa]))[:, 1]
    return {"Keyword rule": keyword_rule(rows).astype(float), "Text model": p_text,
            "Account model": p_acc, "Stacked model": p_comb, "Averaged model": (p_text + p_acc) / 2}


# ---------------------------------------------------------------- metrics
def metrics(y, p):
    pred = (p >= 0.5).astype(int)
    tp = int(((pred == 1) & (y == 1)).sum()); tn = int(((pred == 0) & (y == 0)).sum())
    fp = int(((pred == 1) & (y == 0)).sum()); fn = int(((pred == 0) & (y == 1)).sum())
    prec = tp / (tp + fp) if tp + fp else 0.0
    rec = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * prec * rec / (prec + rec) if prec + rec else 0.0
    conf = np.maximum(p, 1 - p)                       # how sure it is of the class it picked
    bins = []
    ece = 0.0
    for lo in np.arange(0.5, 1.0, 0.1):
        hi = lo + 0.1 + (1e-9 if lo >= 0.9 else 0)
        mask = (conf >= lo) & (conf < hi)
        if mask.sum():
            acc_b = float((pred[mask] == y[mask]).mean())
            lo_pct = int(round(lo * 100))
            bins.append({"range": f"{lo_pct}–{lo_pct + 10}%", "n": int(mask.sum()),
                         "said": float(conf[mask].mean()), "right": acc_b})
            ece += mask.mean() * abs(conf[mask].mean() - acc_b)
    sure = conf >= 0.9
    return {"accuracy": (tp + tn) / len(y), "precision": prec, "recall": rec, "f1": f1,
            "cm": {"tp": tp, "tn": tn, "fp": fp, "fn": fn}, "ece": float(ece),
            "brier": float(np.mean((p - y) ** 2)), "bins": bins,
            "sure_share": float(sure.mean()), "sure_acc": float((pred[sure] == y[sure]).mean()) if sure.any() else None}


def main():
    train = load("train")
    tests = {k: load(k) for k in SETS}
    m = train_all(train)
    res = {"sizes": {"train": len(train), **{k: len(v) for k, v in tests.items()}}, "sets": {}}
    for k, rows in tests.items():
        y = y_of(rows)
        preds = predict_all(m, rows)
        hard = np.array([r["hard"] for r in rows])
        langs = np.array([r["lang"] for r in rows])
        entry = {}
        for name, p in preds.items():
            mm = metrics(y, p)
            pred = (p >= 0.5).astype(int)
            mm["hard_acc"] = float((pred[hard] == y[hard]).mean())
            mm["easy_acc"] = float((pred[~hard] == y[~hard]).mean())
            mm["by_lang"] = {l: float((pred[langs == l] == y[langs == l]).mean()) for l in ("sq", "sr", "en")}
            entry[name] = mm
        # most confident mistakes of the combined model
        p = preds["Averaged model"]
        wrong = [(abs(p[i] - 0.5), i) for i in range(len(rows)) if (p[i] >= 0.5) != (y[i] == 1)]
        entry["_errors"] = [{"text": rows[i]["text"], "truth": rows[i]["label"], "said_false": round(float(p[i]), 2),
                             "account_age_days": rows[i]["account_age_days"], "copy_group_size": rows[i]["copy_group_size"]}
                            for _, i in sorted(wrong, reverse=True)[:6]]
        res["sets"][k] = entry

    # learning curve: how many labelled posts does it need?
    curve = []
    for n in (50, 100, 200, 400, 800, 1200, 2000):
        accs = {k: [] for k in SETS}
        for seed in range(3):
            r = np.random.default_rng(seed)
            tr = [train[i] for i in r.permutation(len(train))[:n]]
            if len({x["label"] for x in tr}) < 2:
                continue
            mm = train_all(tr, seed) if n >= 100 else None
            for k, rows in tests.items():
                if mm is None:   # too few rows for 5-fold calibration: plain text model
                    tf = TextFeatures().fit([x["text"] for x in tr])
                    lr = LogisticRegression(C=4.0, max_iter=2000).fit(tf.transform([x["text"] for x in tr]), y_of(tr))
                    p = lr.predict_proba(tf.transform([x["text"] for x in rows]))[:, 1]
                else:
                    p = predict_all(mm, rows)["Averaged model"]
                accs[k].append(float(((p >= 0.5).astype(int) == y_of(rows)).mean()))
        curve.append({"n": n, **{k: float(np.mean(v)) for k, v in accs.items()}})
        print(f"  learning curve n={n:5d}: " + "  ".join(f"{k} {np.mean(v):.3f}" for k, v in accs.items()))
    res["curve"] = curve

    # diversity: same amount of data, two writing styles instead of one
    md = train_all(load("train_diverse"))
    res["diversity"] = {}
    for k, rows in tests.items():
        y = y_of(rows)
        res["diversity"][k] = {"one_style": float((((predict_all(m, rows)["Averaged model"]) >= 0.5).astype(int) == y).mean()),
                               "two_styles": float((((predict_all(md, rows)["Averaged model"]) >= 0.5).astype(int) == y).mean()),
                               "two_styles_text": float((((predict_all(md, rows)["Text model"]) >= 0.5).astype(int) == y).mean())}
    print("  diversity:", res["diversity"])

    # what the text model relies on (interpretable word-level model)
    wv = TfidfVectorizer(analyzer="word", ngram_range=(1, 2), min_df=3, token_pattern=r"\S+")
    Xw = wv.fit_transform([normalize(r["text"]) for r in train])
    lr = LogisticRegression(C=2.0, max_iter=2000).fit(Xw, y_of(train))
    names = np.array(wv.get_feature_names_out())
    order = np.argsort(lr.coef_[0])
    res["words_false"] = [(names[i], round(float(lr.coef_[0][i]), 2)) for i in order[::-1][:12]]
    res["words_true"] = [(names[i], round(float(lr.coef_[0][i]), 2)) for i in order[:12]]

    (DATA / "results.json").write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    print()
    for k, label in SETS.items():
        print(f"{k} · {label}")
        for name, mm in res["sets"][k].items():
            if name.startswith("_"):
                continue
            print(f"   {name:15s} accuracy {mm['accuracy']:.3f}  F1 {mm['f1']:.3f}  ECE {mm['ece']:.3f}  "
                  f"hard {mm['hard_acc']:.2f}  when ≥90% sure: {mm['sure_acc'] if mm['sure_acc'] is None else round(mm['sure_acc'], 3)} "
                  f"on {mm['sure_share']:.0%} of posts")
    from truth_report import write_report  # noqa: E402
    out = write_report(res, DATA / "report.html")
    print(f"\nreport: {out}")


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    main()
