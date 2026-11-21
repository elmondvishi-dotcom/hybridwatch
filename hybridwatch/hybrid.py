"""Hybrid linking: a cyber event and a disinformation narrative that belong to the same operation.

A cyber event is linked to a disinformation alert when ALL of these hold:
  * time   - the narrative is active within the link window after the event (default 24 h)
  * target - the narrative names the same target, OR it is about the same sector
             (only for events that some check already supports: a bare claim needs the same target)
  * topic  - they share at least one glossary concept (e.g. power, network, bank)
and the score (target / sector / shared concepts / time proximity) reaches the threshold.

The reason is stored in `hybrid_links`, e.g.
  "Same sector: energy (power). Outage at 11:30, coordinated 'sabotage' narrative started 12:01 (31 min later)."

When linked, the alert gets the 'hybrid' signal (type "Hybrid threat: cyber + information", severity +1,
a reason in "Why flagged") and the cyber event's severity goes up one level.
Refuted events are never linked; a link is removed if its event is refuted later.
"""
from __future__ import annotations

import json
import re
import sqlite3
import time
from collections import Counter, defaultdict

from .db import jloads
from .text import Glossary, normalize
from .verify import target_terms

SECTOR_CONCEPTS = {"energy": {"power"}, "telecom": {"network"}, "finance": {"bank", "euro"},
                   "government": set(), "health": set(), "other": set()}
KIND_LABEL = {"ddos_claim": "DDoS claim", "ransomware_claim": "Ransomware claim", "defacement": "Defacement",
              "data_breach_claim": "Data breach claim", "outage": "Outage", "vulnerability": "Vulnerability"}


def _hm(ts: float) -> str:
    return time.strftime("%H:%M", time.gmtime(ts))


def _mins(s: float) -> str:
    m = int(round(s / 60))
    return f"{m // 60}h {m % 60:02d}m" if m >= 60 else f"{m} min"


class Linker:
    def __init__(self, conn: sqlite3.Connection, cfg: dict, glossary: Glossary, threat=None):
        from .threat import ThreatModel
        self.conn = conn
        self.threat = threat or ThreatModel(cfg)
        c = cfg.get("cyber", {}) or {}
        self.window = c.get("link_window_hours", 24) * 3600
        self.min_score = c.get("link_min_score", 0.5)
        self.min_posts = c.get("link_min_posts", 5)
        self.glossary = glossary

    # ------------------------------------------------------------------
    def run(self, now: float, alert_mgr=None) -> dict:
        """Create links for new evidence, drop links of refuted events, re-evaluate affected alerts."""
        touched_alerts: set[int] = set()
        # drop links whose event got refuted
        for r in self.conn.execute("SELECT h.id, h.alert_id FROM hybrid_links h JOIN cyber_events e ON e.id=h.cyber_event_id "
                                   "WHERE e.status='refuted'").fetchall():
            self.conn.execute("DELETE FROM hybrid_links WHERE id=?", (r["id"],))
            touched_alerts.add(r["alert_id"])
        events = self.conn.execute("SELECT * FROM cyber_events WHERE status!='refuted' AND first_seen<=? AND claimed_at>=?",
                                   (now, now - 2 * self.window)).fetchall()
        alerts = self.conn.execute("SELECT id, narrative_id, signals FROM alerts WHERE status!='merged' "
                                   "AND narrative_id IS NOT NULL").fetchall()
        have = {(r[0], r[1]) for r in self.conn.execute("SELECT cyber_event_id, alert_id FROM hybrid_links")}
        created = 0
        for ev in events:
            for a in alerts:
                if (ev["id"], a["id"]) in have:
                    continue
                link = self.match(ev, a, now)
                if link:
                    self.conn.execute("INSERT OR IGNORE INTO hybrid_links(cyber_event_id, alert_id, reason, score) "
                                      "VALUES(?,?,?,?)", (ev["id"], a["id"], link["reason"], link["score"]))
                    touched_alerts.add(a["id"])
                    created += 1
        self.rescore()
        self.conn.commit()
        if alert_mgr:
            for aid in touched_alerts:
                r = self.conn.execute("SELECT narrative_id FROM alerts WHERE id=?", (aid,)).fetchone()
                if r and r["narrative_id"] is not None:
                    alert_mgr.update(r["narrative_id"], now)
            self.conn.commit()
        return {"created": created, "alerts": len(touched_alerts)}

    def rescore(self) -> None:
        """Threat level of every cyber event (threat.py): kind, target, verification status, hybrid links."""
        linked = defaultdict(list)
        for r in self.conn.execute("SELECT cyber_event_id, alert_id FROM hybrid_links"):
            linked[r[0]].append(r[1])
        watched = {r[0] for r in self.conn.execute("SELECT event_id FROM verification_checks "
                                                    "WHERE check_name='watch_products' AND result='supports'")}
        for ev in self.conn.execute("SELECT * FROM cyber_events").fetchall():
            raw = dict(jloads(ev["raw"], {}) or {}, _watched=ev["id"] in watched)
            res = self.threat.assess_cyber(dict(ev), raw, linked.get(ev["id"]))
            if res["level"] != ev["severity"] or res["score"] != ev["score"]:
                self.conn.execute("UPDATE cyber_events SET severity=?, score=?, threat=? WHERE id=?",
                                  (res["level"], res["score"], json.dumps(res), ev["id"]))

    # ------------------------------------------------------------------
    def match(self, ev: sqlite3.Row, alert: sqlite3.Row, now: float) -> dict | None:
        t0 = ev["claimed_at"] or ev["first_seen"]
        lo, hi = t0 - 2 * 3600, min(now, t0 + self.window)
        posts = self.conn.execute("SELECT ts, text, translation FROM posts WHERE narrative_id=? AND ts BETWEEN ? AND ? "
                                  "ORDER BY ts", (alert["narrative_id"], lo, hi)).fetchall()
        if len(posts) < self.min_posts:
            return None
        raw = jloads(ev["raw"], {}) or {}
        norms = [normalize(p["text"] + " " + (p["translation"] or "")) for p in posts]
        concepts = Counter(c for n in norms for c in set(self.glossary.concepts(n)))
        narr = {c for c, k in concepts.items() if k >= 3}
        ev_text = normalize(" ".join(str(x) for x in (ev["title"], ev["target"], " ".join(raw.get("keywords", [])),
                                                       raw.get("description", ""))))
        ev_concepts = set(self.glossary.concepts(ev_text)) | SECTOR_CONCEPTS.get(ev["sector"], set())
        shared = narr & ev_concepts
        terms = target_terms(ev["target"], raw)
        target_hits = sum(1 for n in norms if any(t in n for t in terms)) if terms else 0
        target = target_hits >= 2
        sector = bool(SECTOR_CONCEPTS.get(ev["sector"], set()) & narr)
        if not shared or not (target or sector):
            return None
        # a bare claim (nothing supports it yet) is only linked when the narrative names the same target
        if ev["status"] == "unverified" and not target:
            return None

        sig = jloads(alert["signals"], {}) or {}
        co = sig.get("coordination", {})
        coord_start = co.get("start") if co.get("start") and lo <= co["start"] <= hi else None
        start = coord_start or posts[0]["ts"]
        dt = start - t0
        score = min(1.0, 0.4 * target + 0.3 * sector + min(0.3, 0.1 * len(shared))
                    + 0.2 * max(0.0, 1 - abs(dt) / self.window))
        if score < self.min_score:
            return None

        name = re.sub(r"\s*\([^)]*\)", "", ev["target"] or "").strip() or ev["target"]
        what = (f"Same target: {name}" if target else
                f"Same sector: {ev['sector']} ({', '.join(sorted(SECTOR_CONCEPTS[ev['sector']] & narr))})")
        topic = [c for c, _ in concepts.most_common() if c not in ev_concepts][:1] or sorted(shared)[:1]
        when = f"{_mins(abs(dt))} {'later' if dt >= 0 else 'earlier'}"
        reason = (f"{what}. {KIND_LABEL.get(ev['kind'], ev['kind'])} at {_hm(t0)}, "
                  f"{'coordinated ' if coord_start else ''}'{topic[0]}' narrative started {_hm(start)} ({when}).")
        return {"score": round(score, 2), "reason": reason}


def links_for_alert(conn: sqlite3.Connection, alert_id: int) -> list[dict]:
    return [dict(r) for r in conn.execute(
        "SELECT h.reason, h.score, e.id AS event_id, e.kind, e.title, e.target, e.status, e.claimed_at, e.severity, "
        "e.source_reliability || e.info_credibility AS admiralty "
        "FROM hybrid_links h JOIN cyber_events e ON e.id=h.cyber_event_id WHERE h.alert_id=? AND e.status!='refuted' "
        "ORDER BY h.score DESC", (alert_id,))]
