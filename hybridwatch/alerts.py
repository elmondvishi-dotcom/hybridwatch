"""Alert lifecycle.

* One alert per narrative. New posts UPDATE the alert instead of creating new ones.
* An alert is only re-surfaced (unread again) when something meaningful changes:
  a new kind of signal, post count doubling, or severity rising.
* Dismissed alerts stay quiet unless a NEW type of signal appears (e.g. organic
  coverage later gets picked up by a coordinated network), or the dismissal expires.
* Every alert carries an AI-prefilled assessment the analyst can accept or change.
"""
from __future__ import annotations

import json
import sqlite3
import time
from collections import Counter

from . import signals as S
from .db import get_setting, jloads
from .hybrid import links_for_alert
from .threat import ThreatModel
from .text import Glossary, normalize

TARGETS = {
    "kfor": "KFOR / international security presence",
    "water": "Public utilities (water supply)",
    "power": "Public utilities (electricity)",
    "bank": "Banking system / savings",
    "euro": "Banking system / savings",
    "election": "Elections",
    "border": "Border crossings",
    "mitrovica": "Mitrovica (local communities)",
}
AIMS = {
    ("water", "poison"): "Cause panic about public safety",
    ("kfor", "withdraw"): "Undermine trust in security guarantees",
    ("euro", "freeze"): "Cause panic / bank run",
    ("bank", "freeze"): "Cause panic / bank run",
    ("power", "sabotage"): "Stoke fear and blame for infrastructure failures",
}


class AlertManager:
    def __init__(self, conn: sqlite3.Connection, cfg: dict, glossary: Glossary, threat: ThreatModel | None = None):
        self.conn = conn
        self.th = cfg["thresholds"]
        self.glossary = glossary
        self.threat = threat or ThreatModel(cfg)

    def _assess(self, nid: int, sig: dict, st: dict, links: list, conf: float) -> dict:
        """Threat score for a narrative (threat.py): target, scale, hostility, credibility, escalation."""
        posts = [dict(r) for r in self.conn.execute(
            "SELECT id, text, translation, author, lang, source, source_type, credibility FROM posts "
            "WHERE narrative_id=? ORDER BY ts DESC LIMIT 400", (nid,))]
        focus = (get_setting(self.conn, "focus", {}) or {}).get("keywords", [])
        return self.threat.assess(posts, sig, st, links=links, key=nid, detection_conf=conf, focus=focus)

    def rescore_all(self, now_sim: float) -> int:
        """Recompute score and level of every alert (e.g. at startup, after the model changed).
        Only score / level / explanation change: status, unread, bumps and dismissals stay as they are."""
        n = 0
        for row in self.conn.execute("SELECT * FROM alerts WHERE status!='merged'").fetchall():
            st = S.narrative_stats(self.conn, row["narrative_id"], now_sim, self.th)
            sig = jloads(row["signals"], {}) or {}
            res = self._assess(row["narrative_id"], sig, st, links_for_alert(self.conn, row["id"]), S.confidence(sig))
            self.conn.execute("UPDATE alerts SET severity=?, score=?, threat=? WHERE id=?",
                              (res["level"], res["score"], json.dumps(res), row["id"]))
            n += 1
        # merged alerts are hidden and not rescored: just translate an old severity name to its level
        self.conn.execute("UPDATE alerts SET severity = CASE severity WHEN 'low' THEN 'info' WHEN 'medium' THEN 'watch' "
                          "WHEN 'high' THEN 'threat' ELSE severity END WHERE severity IN ('low','medium','high')")
        self.conn.commit()
        return n

    # ------------------------------------------------------------------
    def update(self, nid: int, now_sim: float) -> str | None:
        """Re-evaluate one narrative. Returns 'created' | 'bumped' | 'updated' | 'reopened' | None."""
        st = S.narrative_stats(self.conn, nid, now_sim, self.th)
        sig_now = S.evaluate_signals(st, self.th)
        row = self.conn.execute("SELECT * FROM alerts WHERE narrative_id=?", (nid,)).fetchone()
        if not sig_now and row is None:
            return None
        # hybrid threat: linked cyber events (see hybrid.py)
        links = links_for_alert(self.conn, row["id"]) if row else []
        if links:
            sig_now["hybrid"] = {"events": [x["event_id"] for x in links], "reason": links[0]["reason"],
                                 "count": len(links), "score": links[0]["score"]}

        # signals accumulate: once a narrative showed coordination, that stays on its record
        sig = dict(jloads(row["signals"], {})) if row else {}
        sig.update(sig_now)
        if "hybrid" in sig and "hybrid" not in sig_now:   # its cyber event was refuted -> link removed
            sig.pop("hybrid")
        if "trending" in sig and "trending" not in sig_now:
            sig["trending"]["active"] = False

        texts = [r["text"] for r in self.conn.execute(
            "SELECT text FROM posts WHERE narrative_id=? ORDER BY followers DESC LIMIT 60", (nid,))]
        concepts = Counter(c for t in texts for c in set(self.glossary.concepts(normalize(t))))

        fc = None
        if "debunk_match" in sig:
            fc = self.conn.execute("SELECT * FROM factchecks WHERE id=?",
                                   (sig["debunk_match"]["factcheck_id"],)).fetchone()
            fc = dict(fc) if fc else None
        atype = S.alert_type(sig)
        conf = S.confidence(sig)
        res = self._assess(nid, sig, st, links, conf)     # level replaces the old severity (hybrid +1 inside)
        sev = res["level"]
        why = S.reasons(sig, st, fc)
        suggestion = self.suggest(nid, sig, st, sev, conf, concepts, fc)
        suggestion["severity"]["reason"] = f"Threat score {res['score']:.0f}/100 ({', '.join(res['why'][:2]) or 'no strong factor'})"
        stats = {k: v for k, v in st.items() if k not in ("coord_post_ids", "coord_authors")}
        wall = time.time()

        if row is None:
            self.conn.execute(
                "INSERT INTO alerts(narrative_id, status, type, severity, confidence, signals, reasons, stats, "
                "created_at, created_sim, bumped_at, bumped_sim, posts_at_bump, unread, suggestion, score, threat, history) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,1,?,?,?,?)",
                (nid, "new", atype, sev, conf, json.dumps(sig), json.dumps(why), json.dumps(stats),
                 wall, now_sim, wall, now_sim, st["size"], json.dumps(suggestion), res["score"], json.dumps(res),
                 json.dumps([{"t": wall, "sim": now_sim, "event": f"Flagged by system ({atype})"}])))
            return "created"

        old_sig = set(jloads(row["signals"], {}).keys())
        new_kinds = set(sig_now) - old_sig
        grew = st["size"] >= (row["posts_at_bump"] or 1) * self.th["bump_growth"]
        old = row["severity"] if row["severity"] in S.SEVERITY else "info"
        sev_up = S.SEVERITY.index(sev) > S.SEVERITY.index(old)
        history = jloads(row["history"], [])
        status, unread, prev_dismissed = row["status"], row["unread"], row["previously_dismissed"]
        bump = False
        result = "updated"

        if status == "dismissed":
            d = jloads(row["dismissed_signals"], {}) or {}
            dismissed_kinds = set(d.get("signals", []))
            fresh = {k for k in sig_now if k not in dismissed_kinds and k != "trending"}
            expired = now_sim - d.get("sim", now_sim) > self.th["dismissal_expiry_hours"] * 3600
            if fresh or (expired and grew and row["dismiss_reason"] != "not_relevant"):
                status, prev_dismissed, bump, result = "new", 1, True, "reopened"
                what = ", ".join(sorted(fresh)) if fresh else "strong growth after dismissal expired"
                why.insert(0, f"Previously dismissed as '{row['dismiss_reason']}', now showing: {what}")
                history.append({"t": wall, "sim": now_sim, "event": f"Re-opened: {what}"})
        elif status in ("new", "needs_review", "watching"):
            if new_kinds or grew or sev_up:
                bump = True
                result = "bumped"
                change = []
                if new_kinds:
                    change.append("new signal: " + ", ".join(sorted(new_kinds)))
                if grew:
                    change.append(f"grew to {st['size']} posts")
                if sev_up:
                    change.append(f"level {old} -> {sev}")
                history.append({"t": wall, "sim": now_sim, "event": "Updated: " + "; ".join(change)})
        # confirmed / merged alerts: stats keep updating silently

        if prev_dismissed and not why[0].startswith("Previously dismissed"):
            d = jloads(row["dismissed_signals"], {}) or {}
            fresh = sorted(k for k in sig if k not in set(d.get("signals", [])) and k != "trending")
            why.insert(0, f"Previously dismissed as '{row['dismiss_reason']}'"
                       + (f", re-opened because of: {', '.join(fresh)}" if fresh else ""))

        self.conn.execute(
            "UPDATE alerts SET status=?, type=?, severity=?, confidence=?, signals=?, reasons=?, stats=?, "
            "suggestion=?, score=?, threat=?, history=?, previously_dismissed=?, unread=?, "
            "bumped_at=?, bumped_sim=?, posts_at_bump=? WHERE id=?",
            (status, atype, sev, conf, json.dumps(sig), json.dumps(why), json.dumps(stats),
             json.dumps(suggestion), res["score"], json.dumps(res), json.dumps(history), prev_dismissed, 1 if bump else unread,
             wall if bump else row["bumped_at"], now_sim if bump else row["bumped_sim"],
             st["size"] if bump else row["posts_at_bump"], row["id"]))
        return result

    # ------------------------------------------------------------------
    def suggest(self, nid: int, sig: dict, st: dict, sev: str, conf: float,
                concepts: Counter, fc: dict | None) -> dict:
        """AI-prefilled assessment. Every field carries the reason behind it; 'Unknown' when unsure."""
        def f(value, reason):
            return {"value": value, "reason": reason, "source": "ai"}

        types = []
        if "debunk_match" in sig:
            types.append("False claim")
        if "coordination" in sig:
            types.append("Coordinated amplification")
        sug = {
            "type": f(types or ["Unknown"], "From detected signals" if types else
                      "Only volume growth detected; could be organic coverage of a real event"),
            "severity": f(sev, f"Reach ~{st.get('reach', 0):,}, alert type, sensitive targets"),
            "analyst_confidence": f(S.confidence_label(conf).lower() if conf >= 0.5 else "Unknown",
                                    f"System confidence {conf:.2f}"),
        }
        # techniques (map to DISARM technique IDs in production)
        tech = []
        if "coordination" in sig:
            c = sig["coordination"]
            tech.append({"tag": "Copy-paste amplification across accounts",
                         "reason": f"{c['accounts']} accounts, near-identical text"})
            if c.get("minutes", 99) <= 15:
                tech.append({"tag": "Synchronised posting burst", "reason": f"within {c['minutes']:g} min"})
        if "new_accounts" in sig:
            tech.append({"tag": "Use of newly created accounts",
                         "reason": f"{int(sig['new_accounts']['share'] * 100)}% created <30 days before posting"})
        if "debunk_match" in sig:
            tech.append({"tag": "Recycling a previously debunked claim",
                         "reason": f"matches {fc['source'] if fc else 'fact-check'}"})
        if "hybrid" in sig:
            tech.append({"tag": "Cyber incident exploited for an information operation",
                         "reason": sig["hybrid"]["reason"]})
        if len(st.get("langs", {})) >= 2:
            tech.append({"tag": "Multilingual targeting", "reason": ", ".join(st["langs"].keys())})
        if len(st.get("source_types", {})) >= 3:
            tech.append({"tag": "Cross-platform spread", "reason": ", ".join(st["source_types"].keys())})
        sug["techniques"] = {"value": tech, "source": "ai"}

        top = [c for c, n in concepts.most_common(6) if n >= 3]
        target = next((TARGETS[c] for c in top if c in TARGETS), None)
        sug["target"] = f(target or "Unknown", f"Key concepts: {', '.join(top[:4])}" if target else "No clear target")
        aim = next((a for (x, y), a in AIMS.items() if x in top and y in top), None)
        sug["aim"] = f(aim or "Unknown", "Pattern of key concepts" if aim else "Not enough evidence")

        # Actor: only suggested with concrete evidence - account overlap with a confirmed case
        actor = f("Unknown", "Attribution needs concrete evidence")
        authors = set(st.get("coord_authors", []))
        if authors:
            for r in self.conn.execute(
                    "SELECT a.id, a.narrative_id FROM alerts a WHERE a.status='confirmed' AND a.narrative_id!=?", (nid,)):
                other = {x["author"] for x in self.conn.execute(
                    "SELECT DISTINCT author FROM posts WHERE narrative_id=?", (r["narrative_id"],))}
                overlap = len(authors & other) / len(authors)
                if overlap >= 0.3:
                    actor = f(f"Same network as confirmed alert #{r['id']}",
                              f"{int(overlap * 100)}% of coordinated accounts also pushed that narrative")
                    break
        sug["actor"] = actor
        sug["reach"] = f(st.get("reach", 0), "Sum of followers of posting accounts")
        sug["recommended_action"] = f(
            "Escalate" if sev in ("threat", "critical") and "coordination" in sig else
            "Monitor" if "trending" in sig and len(sig) == 1 else "Review",
            "Based on severity and signals")

        # key evidence: earliest coordinated posts + most-followed + debunk matches
        ids: list[int] = []
        coord = st.get("coord_post_ids", [])
        if coord:
            ids += [r["id"] for r in self.conn.execute(
                f"SELECT id FROM posts WHERE id IN ({','.join('?' * len(coord))}) ORDER BY ts LIMIT 3", coord)]
        ids += [r["id"] for r in self.conn.execute(
            "SELECT id FROM posts WHERE narrative_id=? AND debunk_id IS NOT NULL ORDER BY ts LIMIT 2", (nid,))]
        ids += [r["id"] for r in self.conn.execute(
            "SELECT id FROM posts WHERE narrative_id=? ORDER BY followers DESC LIMIT 3", (nid,))]
        seen, key = set(), []
        for i in ids:
            if i not in seen:
                seen.add(i)
                key.append(i)
        sug["key_evidence"] = {"value": key[:5], "source": "ai"}
        return sug
