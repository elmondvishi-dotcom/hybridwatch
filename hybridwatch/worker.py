"""The always-on worker. Runs independently of the web interface:
closing the browser does not stop collection or analysis.

    py -m hybridwatch.worker            # run forever
    py -m hybridwatch.worker --reset    # wipe the database and start the replay from the beginning
"""
from __future__ import annotations

import argparse
import os
import threading
import time

from .config import load_config, resolve
from .db import get_setting, set_setting
from .collectors import make_collector
from .pipeline import Pipeline
from .cyber_collectors import make_cyber_collector
from .events import EventDetector
from .hybrid import Linker
from .verify import Verifier, add_event


def seed_settings(pipe: Pipeline) -> None:
    for key in ("sources", "focus", "factcheckers"):
        if get_setting(pipe.conn, key) is None:
            set_setting(pipe.conn, key, pipe.cfg.get(key))
    if get_setting(pipe.conn, "cyber_sources") is None:
        set_setting(pipe.conn, "cyber_sources", (pipe.cfg.get("cyber") or {}).get("sources", []))


class Worker:
    def __init__(self, cfg: dict, stop: threading.Event | None = None):
        self.cfg = cfg
        self.stop = stop or threading.Event()
        self.pipe = Pipeline(cfg)
        self.verifier = Verifier(self.pipe.conn, cfg)
        self.linker = Linker(self.pipe.conn, cfg, self.pipe.glossary, self.pipe.threat)
        self.events = EventDetector(self.pipe.conn, cfg, self.pipe.threat)
        seed_settings(self.pipe)
        # recompute threat scores of existing alerts and cyber events (the model or its config may have changed);
        # the alert lifecycle (status, dismissals, bumps) is not touched
        n = self.pipe.alerts.rescore_all(self.pipe.now)
        self.linker.rescore()
        self.pipe.conn.commit()
        if n:
            print(f"[worker] threat scores recomputed for {n} alerts")
        self.collectors: dict[str, object] = {}
        self.cyber_collectors: dict[str, object] = {}
        self.last_fc_refresh = 0.0

    def _sync_collectors(self) -> None:
        sources = get_setting(self.pipe.conn, "sources", []) or []
        states = get_setting(self.pipe.conn, "collector_state", {}) or {}
        wanted = {s["name"]: s for s in sources if s.get("enabled")}
        for name in list(self.collectors):
            if name not in wanted:
                del self.collectors[name]
        for name, src in wanted.items():
            if name not in self.collectors:
                try:
                    c = make_collector(self.cfg, src, states.setdefault(name, {}))
                    if c:
                        self.collectors[name] = c
                        print(f"[worker] source enabled: {name}")
                except Exception as e:
                    print(f"[worker] cannot start source {name}: {e}")

    def _sync_cyber(self) -> None:
        sources = get_setting(self.pipe.conn, "cyber_sources", []) or []
        states = get_setting(self.pipe.conn, "cyber_collector_state", {}) or {}
        wanted = {s["name"]: s for s in sources if s.get("enabled")}
        for name in list(self.cyber_collectors):
            if name not in wanted:
                del self.cyber_collectors[name]
        for name, src in wanted.items():
            if name not in self.cyber_collectors:
                try:
                    c = make_cyber_collector(self.cfg, src, states.setdefault(name, {}))
                    if c:
                        self.cyber_collectors[name] = c
                        print(f"[worker] cyber source enabled: {name}")
                except Exception as e:
                    print(f"[worker] cannot start cyber source {name}: {e}")

    def _cyber_step(self) -> dict:
        """Collect cyber claims, verify them, link them to narratives (data clock = latest post time)."""
        self._sync_cyber()
        # replay-only: the data clock; with live sources the claims carry real time, so use the wall clock
        live = any(c.kind != "replay" for c in self.cyber_collectors.values())
        now = max(self.pipe.now, time.time()) if live else self.pipe.now
        new = 0
        for c in self.cyber_collectors.values():
            if not c.due():
                continue
            try:
                for ev in c.poll(now):
                    new += add_event(self.pipe.conn, ev, now) is not None
            except Exception as e:   # graceful failure: one broken source never stops the worker
                print(f"[worker] cyber poll failed for {c.source.get('name')}: {e}")
        set_setting(self.pipe.conn, "cyber_collector_state", {n: c.state for n, c in self.cyber_collectors.items()})
        ver = self.verifier.run(now)
        lk = self.linker.run(now, self.pipe.alerts)
        out = {"cyber_new": new, "cyber_changed": ver["changed"], "hybrid_links": lk["created"]}
        return {k: n for k, n in out.items() if n}

    def _save_state(self) -> None:
        states = {name: c.state for name, c in self.collectors.items()}
        old = get_setting(self.pipe.conn, "collector_state", {}) or {}
        old.update(states)
        set_setting(self.pipe.conn, "collector_state", old)

    def step(self) -> dict:
        if time.time() - self.last_fc_refresh > 1800:
            self.pipe.factchecks.refresh_sources(get_setting(self.pipe.conn, "factcheckers", []) or [])
            self.last_fc_refresh = time.time()
        self.pipe.factchecks.reload()  # picks up analyst-confirmed cases added via the UI
        self._sync_collectors()
        posts = []
        for c in self.collectors.values():
            try:
                posts += c.poll()
            except Exception as e:
                print(f"[worker] poll failed for {c.source.get('name')}: {e}")
        res = self.pipe.process(posts) if posts else {"posts": 0}
        res.update(self._cyber_step())
        ev = self.events.run(self.pipe.now)          # at most every events.recompute_seconds
        if ev.get("events") is not None and res.get("posts"):
            res["events"] = ev["events"]
        self._save_state()
        set_setting(self.pipe.conn, "heartbeat", time.time())
        return res

    def run(self) -> None:
        print("[worker] running - Ctrl+C to stop")
        poll = self.cfg["worker"].get("poll_seconds", 2)
        while not self.stop.is_set():
            try:
                res = self.step()
                if res.get("posts"):
                    extra = {k: v for k, v in res.items() if k in ("created", "bumped", "reopened", "cyber_new",
                                                                    "cyber_changed", "hybrid_links", "events")}
                    print(f"[worker] {res['posts']} posts in {res['seconds']*1000:.0f} ms "
                          f"({res['posts']/max(res['seconds'],1e-6):,.0f}/s) {extra or ''}")
            except Exception as e:
                import traceback
                traceback.print_exc()
                print(f"[worker] cycle error: {e}")
            # replay backlog: go straight on with the next slice; otherwise wait for new data
            busy = any(getattr(c, "backlog", False) for c in self.collectors.values())
            self.stop.wait(0.05 if busy else poll)


def reset_db(cfg: dict) -> None:
    p = resolve(cfg, cfg["db_path"])
    for suffix in ("", "-wal", "-shm"):
        if os.path.exists(str(p) + suffix):
            os.remove(str(p) + suffix)
    print(f"[worker] database reset: {p}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default=None)
    ap.add_argument("--reset", action="store_true", help="delete the database first")
    args = ap.parse_args()
    cfg = load_config(args.config)
    if args.reset:
        reset_db(cfg)
    try:
        Worker(cfg).run()
    except KeyboardInterrupt:
        print("\n[worker] stopped")


if __name__ == "__main__":
    main()
