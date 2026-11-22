"""Optional LLM client (slow path only).

Called for a handful of items, never per post:
  * one-line narrative labels
  * translations of evidence posts
  * confirming a likely debunk match ("does this post repeat claim X?")

Providers: none (offline heuristics), anthropic, openai_compatible (e.g. a local
Mistral served by Ollama/vLLM so no data leaves your servers).
"""
from __future__ import annotations

import os

import httpx


class LLM:
    def __init__(self, cfg: dict):
        c = cfg.get("llm", {}) or {}
        self.provider = c.get("provider", "none")
        self.model = c.get("model", "")
        self.base_url = c.get("base_url", "").rstrip("/")
        self.api_key = os.environ.get(c.get("api_key_env", "ANTHROPIC_API_KEY"), "")
        if self.provider == "anthropic" and not self.api_key:
            print("[llm] no API key found; LLM features disabled")
            self.provider = "none"
        remote = self.provider == "openai_compatible" and not any(h in self.base_url for h in ("localhost", "127.0.0.1"))
        if remote and not self.api_key:
            print(f"[llm] {c.get('api_key_env')} is not set; LLM features disabled "
                  f"(Windows: setx {c.get('api_key_env')} \"your-key\", then open a new terminal)")
            self.provider = "none"

    def _resolve_model(self) -> None:
        """model: auto -> ask the provider which models exist and take the newest Flash-type chat model
        (Google renames Gemini models often, so a fixed name goes stale)."""
        if self.model and self.model != "auto":
            return
        import time as _t
        if _t.time() - getattr(self, "_resolve_failed", 0) < 300:     # no network: retry at most every 5 minutes
            return
        try:
            headers = {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}
            r = httpx.get(f"{self.base_url}/models", headers=headers, timeout=20)
            r.raise_for_status()
            ids = [m.get("id", "") for m in r.json().get("data", [])]
            short = lambda i: i.split("/")[-1]
            skip = ("image", "tts", "audio", "live", "embed", "vision", "thinking", "exp", "whisper", "guard",
                    "safeguard", "moderation", "transcribe", "speech", "omni")
            chat = [i for i in ids if not any(k in short(i).lower() for k in skip)]

            def version(i):
                import re
                m = re.search(r"(\d+(?:\.\d+)?)", short(i))
                return float(m.group(1)) if m else 0.0
            # preference: Gemini Flash (Google), then strong open models (Groq / OpenRouter / Ollama)
            prefs = ("flash", "gpt-oss-120b", "llama-4", "llama-3.3-70b", "qwen3", "llama", "gpt-oss", "mistral", "gemma")
            order = []
            for k in prefs:
                group = [i for i in chat if k in short(i).lower() and "lite" not in short(i).lower() and i not in order]
                order += sorted(group, key=lambda i: ("preview" not in i, version(i)), reverse=True)
            order += [i for i in chat if i not in order]
            for cand in order[:8]:                 # first model that actually answers (free tier differs by model)
                self.model = cand
                if self._raw_ask("Reply with OK.", 5) is not None:
                    print(f"[llm] model: auto -> {cand}")
                    return
                print(f"[llm] {cand} refused, trying the next model ...")
            self.model = order[0] if order else (ids[0] if ids else "auto")
            print(f"[llm] no model answered; last error above")
        except Exception as e:
            self._resolve_failed = _t.time()
            print(f"[llm] could not list models ({e}); set an exact model name in config.yaml")

    @property
    def enabled(self) -> bool:
        return self.provider != "none"

    def ask(self, prompt: str, max_tokens: int = 200, temperature: float | None = None, timeout: float | None = None) -> str | None:
        if self.provider == "openai_compatible" and (not self.model or self.model == "auto"):
            self._resolve_model()
            if self.model == "auto":
                return None
        return self._raw_ask(prompt, max_tokens, temperature, timeout)

    def _raw_ask(self, prompt: str, max_tokens: int = 200, temperature: float | None = None,
                 timeout: float | None = None) -> str | None:
        try:
            if self.provider == "anthropic":
                body = {"model": self.model, "max_tokens": max_tokens, "messages": [{"role": "user", "content": prompt}]}
                if temperature is not None:
                    body["temperature"] = temperature
                r = httpx.post("https://api.anthropic.com/v1/messages", timeout=timeout or 30, headers={
                    "x-api-key": self.api_key, "anthropic-version": "2023-06-01", "content-type": "application/json"},
                    json=body)
                r.raise_for_status()
                return "".join(b.get("text", "") for b in r.json()["content"]).strip()
            if self.provider == "openai_compatible":
                headers = {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}
                body = {"model": self.model, "max_tokens": max_tokens, "messages": [{"role": "user", "content": prompt}]}
                if temperature is not None:
                    body["temperature"] = temperature
                r = httpx.post(f"{self.base_url}/chat/completions", timeout=timeout or 60, headers=headers,
                               json=body)
                r.raise_for_status()
                return r.json()["choices"][0]["message"]["content"].strip()
        except httpx.HTTPStatusError as e:
            detail = e.response.text[:400].replace("\n", " ")
            print(f"[llm] call failed: HTTP {e.response.status_code} from {self.model}: {detail}")
        except Exception as e:
            print(f"[llm] call failed: {e}")
        return None

    # ---- tasks ----
    def label(self, samples: list[str]) -> str | None:
        joined = "\n".join(f"- {s[:280]}" for s in samples[:8])
        return self.ask(
            "These social media posts (Albanian, Serbian or English) belong to one narrative. "
            "Write ONE neutral English sentence (max 15 words) stating the claim they push, "
            "starting with 'Claims that' or 'Reports that'. Do not judge whether it is true.\n\n" + joined,
            max_tokens=60)

    def translate(self, text: str) -> str | None:
        return self.ask("Translate this post into English. Reply with the translation only.\n\n" + text, 300)

    def repeats_claim(self, post: str, claim: str) -> bool | None:
        ans = self.ask(
            f"Claim: {claim}\nPost: {post}\n\nDoes the post REPEAT or SUPPORT the claim "
            "(not refute or mock it)? Answer only YES or NO.", max_tokens=3)
        if ans is None:
            return None
        return ans.strip().upper().startswith("Y")
