"""Web API + static interface. Only READS what the worker produced (plus analyst decisions),
so closing the interface never stops the analysis.

    py -m uvicorn hybridwatch.api:app --port 8000
"""
from __future__ import annotations

import json
import re
import threading
import time
from collections import defaultdict
from pathlib import Path

import numpy as np
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .config import load_config, resolve
from .db import connect, get_setting, jloads, set_setting
from .text import detect_language, has_cyrillic

CFG = load_config()
STATIC = Path(__file__).parent / "static"
SEV_RANK = {"critical": 3, "high": 2, "medium": 1, "low": 0}
FOLDERS = {"inbox": ("new",), "review": ("needs_review",), "confirmed": ("confirmed",),
           "dismissed": ("dismissed",), "watching": ("watching",)}

app = FastAPI(title="HybridWatch")
app.mount("/static", StaticFiles(directory=STATIC), name="static")


_local = threading.local()


def db():
    # one connection per server thread (SQLite connections should not be shared across threads)
    if not hasattr(_local, "conn"):
        _local.conn = connect(resolve(CFG, CFG["db_path"]))
    return _local.conn


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


# ---------------------------------------------------------------- status
@app.get("/api/status")
def status():
    c = db()
    counts = {f: c.execute(f"SELECT COUNT(*) FROM alerts WHERE status IN ({','.join('?' * len(s))})", s).fetchone()[0]
              for f, s in FOLDERS.items()}
    hb = get_setting(c, "heartbeat", 0) or 0
    return {
        "sim_now": get_setting(c, "sim_now", 0),
        "posts": c.execute("SELECT COUNT(*) FROM posts").fetchone()[0],
        "narratives": c.execute("SELECT COUNT(*) FROM narratives").fetchone()[0],
        "sources": len([s for s in (get_setting(c, "sources", []) or []) if s.get("enabled")]),
        "factchecks": c.execute("SELECT COUNT(*) FROM factchecks").fetchone()[0],
        "folders": counts,
        "worker_alive": time.time() - hb < 30,
        "llm": CFG.get("llm", {}).get("provider", "none"),
    }


# ---------------------------------------------------------------- alerts
ALERT_SQL = ("SELECT a.*, n.label, n.label_source, n.tags, n.rep_post_id, n.en_post_id FROM alerts a "
             "LEFT JOIN narratives n ON n.id=a.narrative_id ")
OPEN = ("new", "needs_review", "watching")
_SENTENCES = re.compile(r"(?<=[.!?])\s+")


def _english_part(text: str) -> str | None:
    """Leading English sentences of a multilingual fact-check claim ("Claim that X. Uji ... . Voda ...")."""
    out = []
    for s in _SENTENCES.split((text or "").strip()):
        if has_cyrillic(s) or detect_language(s) != "en":
            break
        out.append(s)
    return " ".join(out).rstrip(". ") or None


def _shorten(text: str, n: int = 140) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= n else text[:n].rsplit(" ", 1)[0] + "…"


def _factcheck(c, sig: dict):
    if "debunk_match" not in sig:
        return None
    fc = c.execute("SELECT source, claim, verdict, url, date, kind FROM factchecks WHERE id=?",
                   (sig["debunk_match"]["factcheck_id"],)).fetchone()
    return dict(fc) if fc else None


def _title(c, r, sig: dict) -> tuple[str, str]:
    """Plain-English title: LLM label > fact-check claim (English part) > central English post > heuristic."""
    if r["label_source"] == "llm" and r["label"]:
        return r["label"], "llm"
    fc = _factcheck(c, sig)
    if fc and fc["kind"] != "analyst":
        en = _english_part(fc["claim"])
        if en:
            return en, "factcheck"
    if r["en_post_id"]:
        p = c.execute("SELECT text FROM posts WHERE id=?", (r["en_post_id"],)).fetchone()
        if p:
            return _shorten(p["text"]), "post"
    return r["label"] or "(labelling…)", "heuristic"


def _row_summary(r, c) -> dict:
    stats = jloads(r["stats"], {})
    seen = jloads(r["seen_snapshot"])
    new_posts = max(0, stats.get("size", 0) - seen["posts"]) if seen and r["unread"] else 0
    title, title_source = _title(c, r, jloads(r["signals"], {}))
    return {"id": r["id"], "status": r["status"], "type": r["type"], "severity": r["severity"],
            "confidence": r["confidence"], "title": title, "title_source": title_source,
            "tags": jloads(r["tags"], []) or [], "label": r["label"] or "(labelling…)", "posts": stats.get("size", 0),
            "growth": stats.get("growth", 0), "accounts": stats.get("accounts", 0),
            "langs": stats.get("langs", {}), "unread": bool(r["unread"]), "bumped_at": r["bumped_at"],
            "bumped_sim": r["bumped_sim"], "created_sim": r["created_sim"],
            "previously_dismissed": bool(r["previously_dismissed"]), "new_posts": new_posts}


def _summary(sig: dict, fc: dict | None) -> str:
    """One plain-language sentence built only from detected signals. Never says the claim is true or
    false: a rating is always attributed to the fact-checker."""
    rated = None
    if "debunk_match" in sig:
        if fc and fc.get("kind") == "analyst":
            rated = "your team already confirmed this claim as disinformation"
        else:
            rated = f"fact-checkers already rated this claim {fc['verdict'] if fc else 'False'}"
    if "coordination" in sig:
        co = sig["coordination"]
        out = (f"Likely coordinated campaign. {co['accounts']} accounts posted the same message "
               f"within {max(1, round(co.get('minutes', 0)))} minutes.")
        extra = []
        if "new_accounts" in sig:
            extra.append(f"{int(sig['new_accounts']['share'] * 100)}% of the accounts are newly created")
        if rated:
            extra.append(rated)
        if extra:
            s = ", and ".join(extra)
            out += " " + s[0].upper() + s[1:] + "."
        return out
    if "debunk_match" in sig:
        if fc and fc.get("kind") == "analyst":
            out = "Repeats a claim your team already confirmed as disinformation."
        else:
            out = f"Repeats a claim fact-checkers rated {fc['verdict'] if fc else 'False'}."
        out += f" {sig['debunk_match']['posts']} posts repeat it."
        if "trending" in sig:
            out += f" Volume is {sig['trending']['growth']:g}x its normal rate."
        return out
    if "trending" in sig:
        t = sig["trending"]
        return (f"Fast-growing story. No sign of coordination. {t['recent']} posts within an hour, "
                f"{t['growth']:g}x the normal rate.")
    return ""


def _timeline(c, r, nid: int, sig: dict, stats: dict, history: list) -> dict:
    """Posts over the narrative's lifetime (min. 12 h), with 'Flagged' / 'First credible report' markers
    and the early-warning lead time between them."""
    lo, hi = c.execute("SELECT MIN(ts), MAX(ts) FROM posts WHERE narrative_id=?", (nid,)).fetchone()
    if lo is None:
        return {}
    flagged = r["created_sim"]
    coord_hist = [h["sim"] for h in history if "coordination" in h.get("event", "") and h.get("by") != "analyst"]
    coord_at = coord_hist[0] if coord_hist else (flagged if "coordination" in sig else None)
    cred = stats.get("first_credible_ts")
    now = get_setting(c, "sim_now", 0) or hi
    marks = [m for m in (flagged, coord_at, cred) if m]
    start, end = min([lo, *marks]), max([hi, *marks])
    span_min = 12 * 3600
    if end - start < span_min:
        end = max(end, min(start + span_min, now))
        start = min(start, end - span_min)
    bucket = max(1800, int((end - start) / 60 // 1800 + 1) * 1800)   # at most ~60 bars
    start = start // bucket * bucket
    bars = defaultdict(lambda: [0, 0])
    groups = _coord_groups(sig)
    for p in c.execute("SELECT ts, dup_group FROM posts WHERE narrative_id=?", (nid,)):
        b = int(p["ts"] // bucket * bucket)
        bars[b][0] += 1
        bars[b][1] += p["dup_group"] in groups
    markers = [{"kind": "flagged", "label": "Flagged", "t": flagged}]
    if coord_at and abs(coord_at - flagged) > bucket:
        markers.append({"kind": "coordination", "label": "Coordination detected", "t": coord_at})
    if cred:
        markers.append({"kind": "credible", "label": "First credible report", "t": cred})
    alert_at = coord_at or flagged
    lead = None
    if cred and alert_at and alert_at < cred:
        lead = {"seconds": cred - alert_at, "from": alert_at, "to": cred,
                "what": "Coordination detected" if coord_at and coord_at != flagged else "Flagged"}
    return {"start": start, "end": end, "bucket": bucket, "markers": markers, "lead": lead,
            "bars": [{"t": t, "posts": v[0], "copy_paste": v[1]} for t, v in sorted(bars.items())]}


def _coord_groups(sig: dict) -> set:
    co = sig.get("coordination", {})
    return set(co.get("groups") or [co.get("group")]) - {None}


def _network_accounts(c, nid: int, sig: dict) -> set:
    """Accounts that took part in this narrative's copy-paste bursts (not organic posters)."""
    groups = list(_coord_groups(sig))
    if not groups:
        return set()
    return {r[0] for r in c.execute(
        f"SELECT DISTINCT author FROM posts WHERE narrative_id=? AND dup_group IN ({','.join('?' * len(groups))})",
        (nid, *groups))}


def _linked(a: set, b: set) -> bool:
    """Two coordinated account sets belong to the same network."""
    shared = len(a & b)
    return shared >= 3 and shared / min(len(a), len(b)) >= 0.2


def _related(c, aid: int, nid: int, sig: dict) -> tuple[list, dict]:
    """Other alerts pushed by the same coordinated accounts.
    Returns (related alerts, {author: [alert ids]})."""
    mine = _network_accounts(c, nid, sig)
    if len(mine) < 3:
        return [], {}
    related, seen_in = [], defaultdict(list)
    for r in c.execute(ALERT_SQL + "WHERE a.id!=? AND a.status!='merged' AND a.signals LIKE '%coordination%'",
                       (aid,)).fetchall():
        sig_r = jloads(r["signals"], {})
        theirs = _network_accounts(c, r["narrative_id"], sig_r)
        shared = mine & theirs
        if _linked(mine, theirs):
            related.append({"id": r["id"], "title": _title(c, r, sig_r)[0], "status": r["status"],
                            "severity": r["severity"], "shared": len(shared), "of": len(mine),
                            "share": round(len(shared) / len(mine), 2)})
            for a in shared:
                seen_in[a].append(r["id"])
    related.sort(key=lambda x: -x["shared"])
    return related, dict(seen_in)


def _changes_since(prev: dict | None, now: dict, history: list) -> dict | None:
    """What changed between the analyst's last look and now (None = nothing / first look)."""
    if not prev:
        return None
    sev_up = SEV_RANK.get(now["severity"], 0) > SEV_RANK.get(prev.get("severity"), 0)
    out = {
        "since_sim": prev.get("sim"),
        "posts": now["posts"] - prev.get("posts", 0),
        "accounts": now["accounts"] - prev.get("accounts", 0),
        "new_signals": [s for s in now["signals"] if s not in prev.get("signals", [])],
        "severity": [prev.get("severity"), now["severity"]] if sev_up else None,
        "events": [h["event"] for h in history if h.get("t", 0) > prev.get("at", 0) and h.get("by") != "analyst"],
    }
    if out["posts"] <= 0 and not out["new_signals"] and not out["severity"] and not out["events"]:
        return None
    return out


@app.get("/api/alerts")
def list_alerts(folder: str = "inbox", snapshot: float | None = None):
    """snapshot = wall time the analyst paused at. Alerts updated after it are counted, not shown
    (except critical ones, which always break through)."""
    c = db()
    st = FOLDERS.get(folder, ("new",))
    rows = c.execute(ALERT_SQL + f"WHERE a.status IN ({','.join('?' * len(st))})", st).fetchall()
    shown, pending = [], 0
    for r in rows:
        if snapshot and r["bumped_at"] > snapshot and r["severity"] != "critical":
            pending += 1
            continue
        shown.append(_row_summary(r, c))
    shown.sort(key=lambda a: (-SEV_RANK.get(a["severity"], 0), -a["bumped_at"]))
    return {"alerts": shown, "pending": pending}


@app.get("/api/overview")
def overview():
    """What the right pane shows when no alert is selected."""
    c = db()
    rows = c.execute(ALERT_SQL + f"WHERE a.status IN ({','.join('?' * len(OPEN))})", OPEN).fetchall()
    sigs = {r["id"]: jloads(r["signals"], {}) for r in rows}
    coord = [r for r in rows if "coordination" in sigs[r["id"]]]
    # linked networks: open coordinated alerts that share accounts, grouped into connected components
    nets = {r["id"]: _network_accounts(c, r["narrative_id"], sigs[r["id"]]) for r in coord}
    parent = {i: i for i in nets}

    def root(i):
        while parent[i] != i:
            i = parent[i]
        return i
    ids = list(nets)
    for i, a in enumerate(ids):
        for b in ids[i + 1:]:
            if _linked(nets[a], nets[b]):
                parent[root(a)] = root(b)
    groups = defaultdict(list)
    for i in ids:
        groups[root(i)].append(i)
    networks = [sorted(g) for g in groups.values() if len(g) >= 2]

    def rank(r):
        return (r["status"] == "new", SEV_RANK.get(r["severity"], 0), r["bumped_at"] or 0)
    top = max(rows, key=rank) if rows else None
    latest = max(rows, key=lambda r: r["created_sim"] or 0) if rows else None
    brief = lambda r: {"id": r["id"], "title": _title(c, r, sigs[r["id"]])[0], "severity": r["severity"],
                       "t": r["created_sim"]}
    return {
        "open": len(rows),
        "critical": sum(r["severity"] == "critical" for r in rows),
        "high": sum(r["severity"] == "high" for r in rows),
        "coordinated": len(coord),
        "linked_alerts": sum(len(g) for g in networks),
        "networks": networks,
        "latest": brief(latest) if latest else None,
        "top": brief(top) if top else None,
    }


@app.get("/api/alerts/{aid}")
def alert_detail(aid: int):
    c = db()
    r = c.execute(ALERT_SQL + "WHERE a.id=?", (aid,)).fetchone()
    if not r:
        raise HTTPException(404)
    sig = jloads(r["signals"], {})
    sug = jloads(r["suggestion"], {})
    fc = _factcheck(c, sig)
    out = _row_summary(r, c)
    out.update({"reasons": jloads(r["reasons"], []), "signals": sig, "stats": jloads(r["stats"], {}),
                "suggestion": sug, "assessment": jloads(r["assessment"]), "history": jloads(r["history"], []),
                "dismiss_reason": r["dismiss_reason"], "label_source": r["label_source"],
                "summary": _summary(sig, fc), "factcheck": fc})
    # one representative original post, shown as a quote under the title
    if r["rep_post_id"]:
        q = c.execute("SELECT text, translation, lang, source_type, author FROM posts WHERE id=?",
                      (r["rep_post_id"],)).fetchone()
        if q and _shorten(q["text"]) != out["title"]:
            out["quote"] = dict(q)
    # what changed since the analyst last opened this alert; then remember what they see now
    stats = out["stats"]
    snap = {"posts": stats.get("size", 0), "accounts": stats.get("accounts", 0), "severity": r["severity"],
            "signals": sorted(sig), "at": time.time(), "sim": get_setting(c, "sim_now", 0)}
    out["since_last"] = _changes_since(jloads(r["seen_snapshot"]), snap, out["history"])
    c.execute("UPDATE alerts SET seen_snapshot=? WHERE id=?", (json.dumps(snap), aid))
    c.commit()

    nid = r["narrative_id"]
    if nid is None:
        return out

    # same network: other alerts pushed by the same coordinated accounts
    out["related"], seen_in = _related(c, aid, nid, sig)

    out["timeline"] = _timeline(c, r, nid, sig, stats, out["history"])
    coord_groups = _coord_groups(sig)

    # evidence: AI-picked key posts first, then most-followed
    key = sug.get("key_evidence", {}).get("value", [])
    cols = "id, source, source_type, credibility, author, author_created, followers, ts, text, translation, lang, url, dup_group, debunk_id"
    posts = []
    if key:
        posts = c.execute(f"SELECT {cols} FROM posts WHERE id IN ({','.join('?' * len(key))})", key).fetchall()
        posts = sorted(posts, key=lambda p: key.index(p["id"]))
    more = c.execute(f"SELECT {cols} FROM posts WHERE narrative_id=? ORDER BY followers DESC LIMIT 12", (nid,)).fetchall()
    seen = {p["id"] for p in posts}
    posts += [p for p in more if p["id"] not in seen]
    out["evidence"] = [dict(p) | {"key": p["id"] in key, "copy_paste": p["dup_group"] in coord_groups,
                                  "new_account": bool(p["author_created"] and p["ts"] - p["author_created"] < 30 * 86400)}
                       for p in posts[:12]]

    # network: top accounts
    out["accounts"] = [dict(a) | {"also_in": seen_in.get(a["author"], [])} for a in c.execute(
        "SELECT author, COUNT(*) AS posts, MAX(followers) AS followers, MIN(author_created) AS created, MIN(ts) AS first "
        "FROM posts WHERE narrative_id=? GROUP BY author ORDER BY first LIMIT 15", (nid,))]
    if r["unread"]:
        c.execute("UPDATE alerts SET unread=0 WHERE id=?", (aid,))
        c.commit()
    return out


UNDO_COLS = ("status", "assessment", "dismiss_reason", "dismissed_signals", "dismissed_posts", "unread")


class Decision(BaseModel):
    action: str                    # confirm | dismiss | review | watch | reopen
    reason: str | None = None      # dismiss reason, or review assignee
    fields: dict | None = None     # confirm: assessment fields (type, severity, + optional details)
    note: str | None = None
    duplicate_of: int | None = None


@app.post("/api/alerts/{aid}/decision")
def decide(aid: int, d: Decision):
    c = db()
    r = c.execute("SELECT * FROM alerts WHERE id=?", (aid,)).fetchone()
    if not r:
        raise HTTPException(404)
    now = time.time()
    sim = get_setting(c, "sim_now", 0)
    history = jloads(r["history"], [])
    sug = jloads(r["suggestion"], {})
    changed = []
    prev = {k: r[k] for k in UNDO_COLS}  # state before this decision, for undo

    if d.action == "confirm":
        fields = d.fields or {}
        # which AI suggestions did the analyst change? (measures how good the pre-fill is)
        for k, v in fields.items():
            if k in sug and "value" in sug[k] and v not in (None, "", []) and v != sug[k]["value"]:
                changed.append(k)
        c.execute("UPDATE alerts SET status='confirmed', assessment=?, unread=0 WHERE id=?",
                  (json.dumps({"fields": fields, "note": d.note, "at": now, "changed": changed}), aid))
        # feedback loop: the confirmed narrative becomes a known claim
        n = c.execute("SELECT label, centroid FROM narratives WHERE id=?", (r["narrative_id"],)).fetchone()
        if n and n["centroid"]:
            c.execute("INSERT OR IGNORE INTO factchecks(ext_id, source, claim, verdict, url, date, kind, embedding) "
                      "VALUES(?,?,?,?,?,?,?,?)",
                      (f"analyst-{aid}", "Analyst-confirmed case", n["label"] or "", "confirmed disinformation",
                       f"#alert-{aid}", time.strftime("%Y-%m-%d"), "analyst",
                       np.frombuffer(n["centroid"], dtype=np.float32).tobytes()))
        event = f"Confirmed ({', '.join(fields.get('type') or []) or 'unspecified'}, {fields.get('severity', '?')})"
    elif d.action == "dismiss":
        if d.reason == "duplicate" and d.duplicate_of:
            c.execute("UPDATE alerts SET status='merged', dismiss_reason='duplicate', unread=0 WHERE id=?", (aid,))
            event = f"Dismissed as duplicate of #{d.duplicate_of}"
        else:
            c.execute("UPDATE alerts SET status='dismissed', dismiss_reason=?, dismissed_signals=?, "
                      "dismissed_posts=?, unread=0 WHERE id=?",
                      (d.reason or "unspecified",
                       json.dumps({"signals": list(jloads(r["signals"], {}).keys()), "sim": sim}),
                       jloads(r["stats"], {}).get("size", 0), aid))
            event = f"Dismissed: {d.reason}"
    elif d.action == "review":
        c.execute("UPDATE alerts SET status='needs_review', unread=0 WHERE id=?", (aid,))
        event = "Marked for review" + (f" (assigned to {d.reason})" if d.reason else "")
    elif d.action == "watch":
        c.execute("UPDATE alerts SET status='watching', unread=0 WHERE id=?", (aid,))
        event = "Watching"
    elif d.action == "reopen":
        c.execute("UPDATE alerts SET status='new' WHERE id=?", (aid,))
        event = "Re-opened by analyst"
    else:
        raise HTTPException(400, "unknown action")

    if d.note:
        event += f" — note: {d.note}"
    history.append({"t": now, "sim": sim, "event": event, "by": "analyst"})
    c.execute("UPDATE alerts SET history=? WHERE id=?", (json.dumps(history), aid))
    cur = c.execute("INSERT INTO decisions(alert_id, action, reason, fields, changed_fields, note, ts, prev) "
                    "VALUES(?,?,?,?,?,?,?,?)",
                    (aid, d.action, d.reason, json.dumps(d.fields), json.dumps(changed), d.note, now, json.dumps(prev)))
    c.commit()
    return {"ok": True, "changed_fields": changed, "decision_id": cur.lastrowid, "event": event}


@app.post("/api/alerts/{aid}/undo")
def undo(aid: int):
    """Revert the analyst's latest decision on this alert. The undo itself stays in the history (audit trail)."""
    c = db()
    d = c.execute("SELECT * FROM decisions WHERE alert_id=? AND undone=0 ORDER BY id DESC LIMIT 1", (aid,)).fetchone()
    r = c.execute("SELECT history FROM alerts WHERE id=?", (aid,)).fetchone()
    if not d or not r or not d["prev"]:
        raise HTTPException(400, "nothing to undo")
    prev = jloads(d["prev"], {})
    c.execute(f"UPDATE alerts SET {', '.join(f'{k}=?' for k in UNDO_COLS)} WHERE id=?",
              (*[prev.get(k) for k in UNDO_COLS], aid))
    if d["action"] == "confirm":  # take the confirmed case back out of the known-claims library
        c.execute("DELETE FROM factchecks WHERE ext_id=? AND kind='analyst'", (f"analyst-{aid}",))
    history = jloads(r["history"], [])
    undone = next((h["event"] for h in reversed(history) if h.get("by") == "analyst" and h.get("t") == d["ts"]),
                  d["action"])
    history.append({"t": time.time(), "sim": get_setting(c, "sim_now", 0), "event": f"Undone: {undone}",
                    "by": "analyst"})
    c.execute("UPDATE alerts SET history=? WHERE id=?", (json.dumps(history), aid))
    c.execute("UPDATE decisions SET undone=1 WHERE id=?", (d["id"],))
    c.commit()
    return {"ok": True, "status": prev.get("status")}


# ---------------------------------------------------------------- settings
@app.get("/api/settings")
def get_settings():
    c = db()
    return {k: get_setting(c, k, CFG.get(k)) for k in ("sources", "focus", "factcheckers")}


class SettingsIn(BaseModel):
    sources: list | None = None
    focus: dict | None = None
    factcheckers: list | None = None


@app.put("/api/settings")
def put_settings(s: SettingsIn):
    c = db()
    for k, v in s.model_dump().items():
        if v is not None:
            set_setting(c, k, v)
    return {"ok": True}


# ---------------------------------------------------------------- activity
@app.get("/api/activity")
def activity():
    c = db()
    buckets = [dict(r) for r in c.execute(
        "SELECT CAST(ts/3600 AS INTEGER)*3600 AS t, COUNT(*) AS posts FROM posts GROUP BY t ORDER BY t")]
    alerts = [{"id": r["id"], "t": r["created_sim"], "type": r["type"], "severity": r["severity"],
               "title": _title(c, r, jloads(r["signals"], {}))[0]}
              for r in c.execute(ALERT_SQL + "WHERE a.status!='merged' ORDER BY a.created_sim")]
    m = c.execute("SELECT SUM(n_posts) AS n, SUM(seconds) AS s, MAX(n_posts*1.0/seconds) AS peak FROM metrics "
                  "WHERE seconds>0").fetchone()
    dec = c.execute("SELECT changed_fields FROM decisions WHERE action='confirm' AND undone=0").fetchall()
    changed = [f for d in dec for f in jloads(d["changed_fields"], [])]
    return {"buckets": buckets, "alerts": alerts,
            "throughput": {"posts": m["n"] or 0, "avg_per_sec": (m["n"] / m["s"]) if m["s"] else 0,
                           "peak_per_sec": m["peak"] or 0},
            "ai_prefill": {"confirmed": len(dec), "fields_changed": len(changed)}}
