"""Background LLM thread (Mistral on Ollama, or any configured LLM).

A local model on a laptop can take 10-60 s per answer. The fast path (embeddings, copy-paste
detection, clustering, alerts) must never wait for that, so this thread does the slow work on its
own database connection, one item at a time, most recent alerts first:
  * a one-line English label for each alerted narrative (again when it doubles in size)
  * English translations of posts in alerted narratives
If the LLM is not running it waits and retries, and the rest of the app works as usual.
"""
from __future__ import annotations

import threading
import time

from .config import resolve
from .db import connect
from .llm import LLM


class LLMBackground(threading.Thread):
    def __init__(self, cfg: dict, stop: threading.Event):
        super().__init__(daemon=True, name="llm-background")
        self.cfg = cfg
        self.stop = stop
        self.llm = LLM(cfg)
        self.timeout = float((cfg.get("llm", {}) or {}).get("timeout_seconds", 180))
        self.max_translations = int((cfg.get("llm", {}) or {}).get("translate_per_alert", 3))

    # -- one unit of work; returns True if it did something
    def _label_one(self, c) -> bool:
        r = c.execute(
            "SELECT n.id, n.size FROM narratives n JOIN alerts a ON a.narrative_id=n.id "
            "WHERE a.status!='merged' AND (n.label_source IS NULL OR n.label_source!='llm' "
            "OR n.size >= 2*COALESCE(n.labeled_at_size,1)) ORDER BY a.bumped_at DESC LIMIT 1").fetchone()
        if not r:
            return False
        sample = [x["text"] for x in c.execute(
            "SELECT text FROM posts WHERE narrative_id=? ORDER BY (dup_group IS NOT NULL) DESC, ts DESC LIMIT 8",
            (r["id"],))]
        if not sample:
            return False
        label = self.llm.label(sample)
        if label is None:
            raise ConnectionError("no answer")
        c.execute("UPDATE narratives SET label=?, label_source='llm', labeled_at_size=? WHERE id=?",
                  (label.strip().strip('"'), r["size"], r["id"]))
        c.commit()
        print(f"[llm] label for narrative {r['id']}: {label[:90]}")
        return True

    def _translate_one(self, c) -> bool:
        r = c.execute(
            "SELECT p.id, p.text FROM posts p JOIN alerts a ON a.narrative_id=p.narrative_id "
            "WHERE p.translation IS NULL AND p.lang!='en' AND a.status!='merged' "
            "AND (SELECT COUNT(*) FROM posts q WHERE q.narrative_id=p.narrative_id AND q.translation IS NOT NULL) < ? "
            "ORDER BY a.bumped_at DESC, p.followers DESC LIMIT 1", (self.max_translations,)).fetchone()
        if not r:
            return False
        tr = self.llm.translate(r["text"])
        if tr is None:
            raise ConnectionError("no answer")
        c.execute("UPDATE posts SET translation=? WHERE id=?", (tr, r["id"]))
        c.commit()
        return True

    def run(self) -> None:
        c = connect(resolve(self.cfg, self.cfg["db_path"]))
        print(f"[llm] background thread started ({self.llm.provider}: {self.llm.model})")
        # longer timeout for slow local models
        ask = self.llm.ask
        self.llm.ask = lambda prompt, max_tokens=200, **kw: ask(prompt, max_tokens, timeout=self.timeout)
        warned = False
        while not self.stop.is_set():
            try:
                did = self._label_one(c) or self._translate_one(c)
                warned = False
                self.stop.wait(0.5 if did else 3)
            except ConnectionError:
                if not warned:
                    hint = ("is Ollama running?" if "localhost" in self.llm.base_url or "127.0.0.1" in self.llm.base_url
                            else f"is the API key set ({(self.cfg.get('llm', {}) or {}).get('api_key_env')}) and is there internet?")
                    print(f"[llm] no answer from the LLM - {hint} Retrying every 30 s; everything else keeps working.")
                    warned = True
                self.stop.wait(30)
            except Exception as e:
                print(f"[llm] error: {e}")
                self.stop.wait(10)
