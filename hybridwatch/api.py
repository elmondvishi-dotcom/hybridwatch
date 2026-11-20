"""Web API + static interface. Only READS what the worker produced (plus analyst decisions),
so closing the interface never stops the analysis.

    py -m uvicorn hybridwatch.api:app --port 8000
"""
from __future__ import annotations

import json
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
def _row_summary(r) -> dict:
    stats = jloads(r["stats"], {})
    seen = jloads(r["seen_snapshot"])
    new_posts = max(0, stats.get("size", 0) - seen["posts"]) if seen and r["unread"] else 0
    return {"id": r["id"], "status": r["status"], "type": r["type"], "severity": r["severity"],
            "confidence": r["confidence"], "label": r["label"] or "(labelling…)", "posts": stats.get("size", 0),
            "growth": stats.get("growth", 0), "accounts": stats.get("accounts", 0),
            "langs": stats.get("langs", {}), "unread": bool(r["unread"]), "bumped_at": r["bumped_at"],
            "bumped_sim": r["bumped_sim"], "previously_dismissed": bool(r["previously_dismissed"]),
            "new_posts": new_posts}


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


def _related(c, aid: int, nid: int, sig: dict) -> tuple[list, dict]:
    """Other alerts pushed by the same coordinated accounts.
    Returns (related alerts, {author: [alert ids]})."""
    mine = _network_accounts(c, nid, sig)
    if len(mine) < 3:
        return [], {}
    related, seen_in = [], defaultdict(list)
    for r in c.execute("SELECT a.id, a.narrative_id, a.signals, a.status, a.severity, n.label FROM alerts a "
                       "LEFT JOIN narratives n ON n.id=a.narrative_id "
                       "WHERE a.id!=? AND a.status!='merged' AND a.signals LIKE '%coordination%'", (aid,)).fetchall():
        theirs = _network_accounts(c, r["narrative_id"], jloads(r["signals"], {}))
        shared = mine & theirs
        if len(shared) >= 3 and len(shared) / min(len(mine), len(theirs)) >= 0.2:
            related.append({"id": r["id"], "label": r["label"] or "(labelling…)", "status": r["status"],
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
    rows = c.execute(
        f"SELECT a.*, n.label FROM alerts a LEFT JOIN narratives n ON n.id=a.narrative_id "
        f"WHERE a.status IN ({','.join('?' * len(st))})", st).fetchall()
    shown, pending = [], 0
    for r in rows:
        if snapshot and r["bumped_at"] > snapshot and r["severity"] != "critical":
            pending += 1
            continue
        shown.append(_row_summary(r))
    shown.sort(key=lambda a: (-SEV_RANK.get(a["severity"], 0), -a["bumped_at"]))
    return {"alerts": shown, "pending": pending}


@app.get("/api/alerts/{aid}")
def alert_detail(aid: int):
    c = db()
    r = c.execute("SELECT a.*, n.label, n.label_source FROM alerts a LEFT JOIN narratives n ON n.id=a.narrative_id "
                  "WHERE a.id=?", (aid,)).fetchone()
    if not r:
        raise HTTPException(404)
    sig = jloads(r["signals"], {})
    sug = jloads(r["suggestion"], {})
    out = _row_summary(r)
    out.update({"reasons": jloads(r["reasons"], []), "signals": sig, "stats": jloads(r["stats"], {}),
                "suggestion": sug, "assessment": jloads(r["assessment"]), "history": jloads(r["history"], []),
                "dismiss_reason": r["dismiss_reason"], "label_source": r["label_source"]})
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

    # timeline: posts per 30 min (simulated time)
    tl = defaultdict(lambda: [0, 0])
    coord_groups = _coord_groups(sig)
    for p in c.execute("SELECT ts, dup_group FROM posts WHERE narrative_id=?", (nid,)):
        b = int(p["ts"] // 1800 * 1800)
        tl[b][0] += 1
        if p["dup_group"] in coord_groups:
            tl[b][1] += 1
    out["timeline"] = [{"t": t, "posts": v[0], "copy_paste": v[1]} for t, v in sorted(tl.items())]

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
    if "debunk_match" in sig:
        fc = c.execute("SELECT source, claim, verdict, url, date, kind FROM factchecks WHERE id=?",
                       (sig["debunk_match"]["factcheck_id"],)).fetchone()
        out["factcheck"] = dict(fc) if fc else None
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
    alerts = [dict(r) for r in c.execute(
        "SELECT a.id, a.created_sim AS t, a.type, a.severity, n.label FROM alerts a "
        "LEFT JOIN narratives n ON n.id=a.narrative_id WHERE a.status!='merged' ORDER BY a.created_sim")]
    m = c.execute("SELECT SUM(n_posts) AS n, SUM(seconds) AS s, MAX(n_posts*1.0/seconds) AS peak FROM metrics "
                  "WHERE seconds>0").fetchone()
    dec = c.execute("SELECT changed_fields FROM decisions WHERE action='confirm' AND undone=0").fetchall()
    changed = [f for d in dec for f in jloads(d["changed_fields"], [])]
    return {"buckets": buckets, "alerts": alerts,
            "throughput": {"posts": m["n"] or 0, "avg_per_sec": (m["n"] / m["s"]) if m["s"] else 0,
                           "peak_per_sec": m["peak"] or 0},
            "ai_prefill": {"confirmed": len(dec), "fields_changed": len(changed)}}
