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

    @property
    def enabled(self) -> bool:
        return self.provider != "none"

    def ask(self, prompt: str, max_tokens: int = 200) -> str | None:
        try:
            if self.provider == "anthropic":
                r = httpx.post("https://api.anthropic.com/v1/messages", timeout=30, headers={
                    "x-api-key": self.api_key, "anthropic-version": "2023-06-01", "content-type": "application/json"},
                    json={"model": self.model, "max_tokens": max_tokens,
                          "messages": [{"role": "user", "content": prompt}]})
                r.raise_for_status()
                return "".join(b.get("text", "") for b in r.json()["content"]).strip()
            if self.provider == "openai_compatible":
                headers = {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}
                r = httpx.post(f"{self.base_url}/chat/completions", timeout=60, headers=headers,
                               json={"model": self.model, "max_tokens": max_tokens,
                                     "messages": [{"role": "user", "content": prompt}]})
                r.raise_for_status()
                return r.json()["choices"][0]["message"]["content"].strip()
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
