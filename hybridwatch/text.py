"""Text normalisation for Albanian, Serbian (Cyrillic + Latin) and English.

Everything downstream (embeddings, near-duplicate hashing, glossary matching)
works on the normalised form, so a Serbian post in Cyrillic and the same post in
Latin script end up identical.
"""
from __future__ import annotations

import re
import unicodedata

# Serbian Cyrillic -> Latin (Gaj's alphabet)
_CYR = {
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "ђ": "dj", "е": "e", "ж": "z",
    "з": "z", "и": "i", "ј": "j", "к": "k", "л": "l", "љ": "lj", "м": "m", "н": "n",
    "њ": "nj", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t", "ћ": "c", "у": "u",
    "ф": "f", "х": "h", "ц": "c", "ч": "c", "џ": "dz", "ш": "s",
}
_CYR.update({k.upper(): v for k, v in _CYR.items()})

_URL = re.compile(r"https?://\S+|www\.\S+")
_MENTION = re.compile(r"@\w+")
_NON_WORD = re.compile(r"[^a-z0-9#\s]")
_SPACES = re.compile(r"\s+")

_SQ_HINTS = {"dhe", "nje", "është", "eshte", "nuk", "per", "në", "ne", "te", "të", "qe", "që", "jane", "janë", "ujë", "ka"}
_SR_HINTS = {"je", "i", "da", "u", "na", "se", "su", "za", "nije", "od", "sa", "ne", "kao", "ali", "što", "sto"}
_EN_HINTS = {"the", "and", "is", "are", "of", "to", "in", "not", "for", "will", "this", "that", "be"}


def has_cyrillic(text: str) -> bool:
    return any("Ѐ" <= ch <= "ӿ" for ch in text)


def transliterate(text: str) -> str:
    return "".join(_CYR.get(ch, ch) for ch in text)


def strip_diacritics(text: str) -> str:
    # ë->e, ç->c, š->s, č/ć->c, ž->z, đ->dj
    text = text.replace("đ", "dj").replace("Đ", "Dj")
    nfkd = unicodedata.normalize("NFKD", text)
    return "".join(ch for ch in nfkd if not unicodedata.combining(ch))


def normalize(text: str) -> str:
    """Lowercase, Cyrillic->Latin, no diacritics, no URLs/mentions/punctuation."""
    t = transliterate(text)
    t = _URL.sub(" ", t)
    t = _MENTION.sub(" ", t)
    t = strip_diacritics(t.lower())
    t = _NON_WORD.sub(" ", t)
    return _SPACES.sub(" ", t).strip()


def detect_language(text: str) -> str:
    """Cheap language guess: 'sr', 'sq', 'en' or 'unk'. Good enough for routing/stats."""
    if has_cyrillic(text):
        return "sr"
    lower = text.lower()
    if "ë" in lower or "ç" in lower:
        return "sq"
    words = set(re.findall(r"\w+", lower))
    scores = {
        "sq": len(words & _SQ_HINTS),
        "sr": len(words & _SR_HINTS) + (2 if re.search(r"[čćšžđ]", lower) else 0),
        "en": len(words & _EN_HINTS),
    }
    best = max(scores, key=scores.get)
    return best if scores[best] > 0 else "unk"


class Glossary:
    """Maps regional word stems to a shared canonical concept token.

    This is the 'local knowledge' layer: it lets the cheap offline embedder see
    that 'uji', 'voda' and 'water' are the same concept, so Albanian, Serbian and
    English posts about one story land in the same narrative. With a multilingual
    neural embedder this matters less, but it still helps for place names.
    """

    def __init__(self, mapping: dict[str, list[str]]):
        self.stems: list[tuple[str, str]] = []
        for concept, stems in mapping.items():
            for s in stems:
                self.stems.append((normalize(s), concept))
        # longest stems first so 'struja' wins over 'str'
        self.stems.sort(key=lambda x: -len(x[0]))
        self._cache: dict[str, str | None] = {}

    def concept(self, token: str) -> str | None:
        if token in self._cache:
            return self._cache[token]
        found = None
        for stem, concept in self.stems:
            if token.startswith(stem):
                found = concept
                break
        self._cache[token] = found
        return found

    def concepts(self, normalized_text: str) -> list[str]:
        out = []
        for tok in normalized_text.split():
            if len(tok) < 3:
                continue
            c = self.concept(tok)
            if c:
                out.append(c)
        return out
