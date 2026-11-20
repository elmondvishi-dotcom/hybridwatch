"""Embedders: turn posts into unit-length vectors.

* HashingEmbedder (default) - fully offline, no model download, no training,
  thousands of posts per second on a laptop CPU. Character n-grams handle
  Albanian/Serbian inflection; glossary concept tokens bridge languages.
* SentenceTransformerEmbedder (optional) - a multilingual neural model
  (e.g. paraphrase-multilingual-MiniLM-L12-v2). Better cross-language matching,
  slower, needs `pip install sentence-transformers` and a one-time model download.
"""
from __future__ import annotations

import numpy as np
from sklearn.feature_extraction.text import HashingVectorizer

from .text import Glossary, normalize


class HashingEmbedder:
    def __init__(self, glossary: Glossary, char_features: int = 2**13, word_features: int = 2**12,
                 char_weight: float = 1.0, concept_weight: float = 1.0):
        self.glossary = glossary
        self.char = HashingVectorizer(analyzer="char_wb", ngram_range=(3, 5), n_features=char_features,
                                      alternate_sign=False, norm="l2", lowercase=False)
        self.word = HashingVectorizer(analyzer="word", token_pattern=r"\S+", n_features=word_features,
                                      alternate_sign=False, norm="l2", lowercase=False)
        self.char_weight = char_weight
        self.concept_weight = concept_weight
        self.dim = char_features + word_features

    def _concept_doc(self, norm: str) -> str:
        concepts = self.glossary.concepts(norm)
        words = [w for w in norm.split() if len(w) > 3]
        # concept tokens count double: they carry the cross-language signal
        return " ".join(words + [f"c_{c}" for c in concepts] * 2)

    def embed(self, texts: list[str], normalized: list[str] | None = None) -> np.ndarray:
        norms = normalized if normalized is not None else [normalize(t) for t in texts]
        c = self.char.transform(norms).toarray().astype(np.float32) * self.char_weight
        w = self.word.transform([self._concept_doc(n) for n in norms]).toarray().astype(np.float32)
        w *= self.concept_weight
        v = np.hstack([c, w])
        n = np.linalg.norm(v, axis=1, keepdims=True)
        n[n == 0] = 1.0
        return v / n


class SentenceTransformerEmbedder:
    def __init__(self, model_name: str = "paraphrase-multilingual-MiniLM-L12-v2"):
        from sentence_transformers import SentenceTransformer  # optional dependency
        self.model = SentenceTransformer(model_name)
        self.dim = self.model.get_sentence_embedding_dimension()

    def embed(self, texts: list[str], normalized: list[str] | None = None) -> np.ndarray:
        v = self.model.encode(texts, batch_size=64, convert_to_numpy=True, normalize_embeddings=True)
        return v.astype(np.float32)


def make_embedder(cfg: dict, glossary: Glossary):
    kind = cfg.get("embedder", "hashing")
    if kind == "sentence-transformers":
        try:
            return SentenceTransformerEmbedder(cfg.get("embedder_model", "paraphrase-multilingual-MiniLM-L12-v2"))
        except Exception as e:  # missing package or no internet for the download
            print(f"[embed] sentence-transformers unavailable ({e}); falling back to hashing embedder")
    return HashingEmbedder(glossary)
