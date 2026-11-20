# HybridWatch — AI-powered OSINT & hybrid threat triage

An always-on tool that watches online sources about the Western Balkans (Albanian, Serbian in Cyrillic and Latin script, English), groups posts into **narratives**, and alerts an analyst when a narrative spreads in a **suspicious way**: coordinated copy-paste bursts by new accounts, or repetition of claims that fact-checkers have already debunked.

**It never decides what is true.** It flags *how* something spreads and links every alert to its evidence. A human analyst makes every decision.

> Built for EDTH Pristina, track 1: AI-Powered OSINT & Hybrid Threat Intelligence.

---

## Quick start (Windows)

```
py -m pip install -r requirements.txt
py run.py --reset
```

This opens http://localhost:8000 and replays a **synthetic 48-hour scenario** at 400× speed (about 7 minutes). Alerts appear in the inbox as the scenario unfolds.

| Command | What it does |
|---|---|
| `py run.py --reset` | fresh database, replay the demo from the start |
| `py run.py` | continue where it stopped (state survives restarts) |
| `py run.py --speed 0` | load the whole replay instantly (backlog mode) |
| `py run.py --speed 2000` | faster replay |
| `py scripts/evaluate.py` | accuracy against the ground-truth labels |
| `py scripts/benchmark.py` | processing speed on your machine |
| `py scripts/make_demo_data.py` | regenerate the synthetic dataset |

The worker and the web interface are separate. Closing the browser **does not stop** collection or analysis. For a server, run them as two processes: `py -m hybridwatch.worker` and `py -m uvicorn hybridwatch.api:app --port 8000`.

---

## Demo script (3 minutes)

1. `py run.py --reset`. The inbox starts empty, and **Activity** shows posts being processed.
2. **Within the first minute:** a *Trending* alert for the power outage in Pristina. Open it: credible outlets reported it first and the wording varies. **Dismiss → "Real event, organic coverage."**
3. **~2 min:** **CRITICAL — Coordination suspected**: "water in Mitrovica poisoned". Show *Why flagged*: 46 accounts posted near-identical text within 11 minutes in 3 languages, 77% of them created in the last 30 days, and it matches a claim already rated *False*. The evidence posts are tagged *copy-paste* and *new account*. **Confirm**: the AI has pre-filled type, severity, techniques, target and aim. Change one field and the system records that you corrected it.
4. Point out the timing: **flagged ~3 hours before the first credible outlet reported on it.**
5. **~5 min:** *KFOR withdrawing*: another coordinated campaign. Open *Add details* on Confirm: the AI suggests the actor is the **same network as the confirmed water campaign** (71% of accounts overlap).
6. **~6 min:** the **dismissed power-outage alert re-opens by itself**: *"Previously dismissed as 'organic', re-opened because of: coordination"*. A network is now claiming the outage was sabotage. The timeline shows the organic spike, then a red copy-paste burst 34 hours later.
7. Close with **Activity** (posts processed, alert markers, speed) and `scripts/evaluate.py` (accuracy).

---

## How it works

```
 sources ──► collectors ──► FAST PATH (every post, vectorised) ──────────────────► SQLite ◄── API ◄── browser
 (replay,      (poll)         normalise (Cyrillic→Latin, diacritics)                   ▲
  RSS, …)                     language guess                                           │
                              embedding (char n-grams + regional glossary)             │
                              MinHash/LSH near-duplicates  ──► copy-paste groups       │
                              online clustering            ──► narratives              │
                              similarity search            ──► debunked-claim matches  │
                            SIGNALS per narrative ──► ALERTS (create / update / re-open)
                            SLOW PATH (optional LLM, a few calls per cycle):
                              narrative labels · translations · confirm debunk matches
```

### Signals (how a narrative spreads, not whether it is true)
| Signal | Meaning |
|---|---|
| **coordination** | ≥5 distinct accounts posted near-identical text (MinHash similarity ≥ 0.6) within 30 min. Language versions of one template are combined. |
| **new_accounts** | ≥50% of the copy-paste accounts were created <30 days before posting |
| **no_credible_first** | no credible outlet carried the story before the burst |
| **debunk_match** | ≥3 posts repeat a claim from the fact-check library (posts that *refute* the claim are excluded) |
| **trending** | volume ≥3× its recent baseline. Awareness only, no judgment. |

### Alert behaviour
* **One alert per narrative.** New posts update it instead of creating new alerts.
* Alerts come back to the top (unread) only on **meaningful change**: a new kind of signal, the post count doubling, or severity rising.
* **Narratives merge** on hard evidence: the same copy-pasted text, the same debunked claim, or the same network posting at the same time.
* **Dismissed ≠ safe.** A dismissed alert stays quiet unless a *new kind* of signal appears (organic → coordinated), or the dismissal expires (72 h).
* **Pause** freezes the analyst's view, not the system. New alerts collect behind a "N new" counter. Critical ones always show.
* **Confirm** turns the narrative into a known claim (feedback loop) and records which AI pre-fill fields the analyst changed, which measures pre-fill accuracy.
* The **AI pre-fill** shows its reason for every field and says "Unknown" when unsure. The actor is suggested only with concrete evidence (account overlap with a confirmed case).

---

## Results on the synthetic scenario

`py scripts/evaluate.py` (2,615 posts, 9 ground-truth narratives, 3 campaigns):

| Metric | Result |
|---|---|
| Narrative clustering | pairwise F1 **0.90**, ARI **0.88** |
| Copy-paste detection | precision **0.95**, recall **1.00** |
| Debunked-claim matching | precision **0.99**, recall **0.82**, never matched to the wrong claim |
| Campaigns caught | **3 / 3**, with **0** false coordination alerts on organic stories |
| Early warning | water campaign **175 min**, KFOR campaign **235 min** before the first credible report |
| Speed (2-core laptop CPU) | embeddings ~8,000 posts/s, full pipeline incl. database ~2,000–3,000 posts/s |

⚠️ The data is synthetic, so these numbers show that the method works, not how it performs in the real world. **For the pitch, record a real past event and hand-label ~150–200 posts** (same JSONL format, with `gt_*` fields), then run `py scripts/evaluate.py --data your_file.jsonl`.

---

## Using real data

**Sources:** Settings → *Add source*. RSS works out of the box for news sites. Set each source's credibility (`credible` / `unknown` / `low`), since it drives the *no credible outlet first* signal. Telegram, X and Facebook need platform API credentials. Add a collector in `hybridwatch/collectors.py` with a `poll()` that returns post dicts.

**Recorded datasets:** a JSONL file, one post per line:
```json
{"ext_id": "...", "source": "...", "source_type": "telegram", "credibility": "unknown", "author": "...",
 "author_created": 1790000000, "followers": 120, "ts": 1793500000, "text": "...", "url": "..."}
```

**Fact-checkers:** the regional fact-checkers are listed in Settings, but **disabled until you add their RSS URL** (check each site). Google Fact Check Tools works with a free API key in the env var `GOOGLE_FACTCHECK_KEY`. The demo uses a synthetic library (`data/factchecks_demo.json`).

**LLM (optional)** in `config.yaml`:
```yaml
llm:
  provider: anthropic            # set ANTHROPIC_API_KEY
  # or, to keep all data on your own machine (data sovereignty):
  provider: openai_compatible
  base_url: http://localhost:11434/v1   # e.g. Ollama
  model: mistral
```
Without an LLM, labels are taken from the most representative post and translations come from the dataset.

**Better cross-language matching:** `py -m pip install sentence-transformers` and set `embedder: sentence-transformers`. This downloads a multilingual model once and runs slower. The default hashing embedder plus the regional **glossary** in `config.yaml` (word stems → shared concepts, e.g. `uji / voda / water`) works offline.

---

## Project layout
```
run.py                     start worker + web UI
config.yaml                thresholds, LLM, glossary, default sources and fact-checkers
hybridwatch/
  text.py                  normalisation, transliteration, language guess, glossary
  embed.py                 hashing embedder (default) / sentence-transformers
  minhash.py               MinHash + LSH near-duplicate index
  clustering.py            online narrative clustering + merging
  factcheck.py             debunked-claims library, loaders, matching, refutation filter
  signals.py               per-narrative stats and signals, severity, confidence, reasons
  alerts.py                alert lifecycle, re-open logic, AI-prefilled assessment
  pipeline.py              fast path + slow path, evidence-based merges
  collectors.py            replay + RSS collectors
  worker.py                the always-on loop
  api.py                   FastAPI endpoints
  llm.py                   optional LLM client (Anthropic or local OpenAI-compatible)
  static/                  inbox UI (plain HTML/CSS/JS)
scripts/
  make_demo_data.py        synthetic labelled scenario
  evaluate.py              accuracy report
  benchmark.py             speed report
```

## Suggested team split
* **Data:** real collectors (Telegram/RSS), record and hand-label a real event
* **Model:** tune thresholds on real data, try the multilingual embedder, map techniques to DISARM IDs
* **Signals/alerts:** account-network graph, more coordination features (posting cadence, shared links)
* **UI + pitch:** demo flow, evaluation slide, legal/ethics slide

## Known limitations / next steps
* Synthetic evaluation only. Validate on a labelled real event.
* The hashing embedder relies on the glossary for cross-language grouping. The neural embedder removes most of that dependency.
* No authentication or multi-user support. SQLite is fine for a demo; use PostgreSQL in production.
* Only public sources. The tool tracks narratives and coordination patterns, not individuals. Keep data only for a limited time (GDPR).
