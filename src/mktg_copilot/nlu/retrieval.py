"""TF-IDF retrieval over the metric definitions and glossary (the retrieval-augmented part of the copilot).

Used to answer definition questions, to suggest metrics when a question is vague, and to give an optional
LLM planner grounded context. Deterministic, local, and fast: no embeddings service required.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

import numpy as np
import yaml
from sklearn.feature_extraction.text import TfidfVectorizer

from ..plan import Retrieved
from ..semantic import YAML_PATH, Catalog


_TOKEN = re.compile(r"[a-z][a-z0-9]+")
_STOP = {"the", "a", "an", "of", "to", "is", "are", "does", "do", "how", "what", "and", "or", "in", "on", "for", "it", "this", "that", "with", "by", "as", "be", "at"}


def stem(word: str) -> str:
    """A deliberately small stemmer: enough that anomaly/anomalies and forecast/forecasts/forecasting match."""
    for suffix, repl in (("ies", "y"), ("ing", ""), ("ed", ""), ("es", ""), ("s", "")):
        if word.endswith(suffix) and len(word) - len(suffix) >= 3:
            return word[: -len(suffix)] + repl
    return word


def analyzer(text: str) -> list[str]:
    toks = [stem(t) for t in _TOKEN.findall(text.lower()) if t not in _STOP]
    return toks + [a + "_" + b for a, b in zip(toks, toks[1:])]


@dataclass
class Doc:
    id: str
    title: str
    kind: str          # metric | glossary
    text: str


class Retriever:
    def __init__(self, cat: Catalog):
        raw = yaml.safe_load(YAML_PATH.read_text())
        defs = raw.get("definitions", {})
        self.docs: list[Doc] = []
        for m in cat.metrics.values():
            d = defs.get(m.key, {})
            formula = f"{m.numerator} divided by {m.denominator}" if m.kind == "ratio" else f"total {m.key}"
            body = " ".join([m.label, m.label, *m.synonyms, d.get("meaning", ""), d.get("read_as", ""), d.get("watch_out", ""), formula])
            self.docs.append(Doc(m.key, m.label, "metric", body))
        for g in cat.glossary:
            self.docs.append(Doc(g["id"], g["title"], "glossary", f"{g['title']} {g['title']} {g.get('keywords', '')} {g['text']}"))
        self.vec = TfidfVectorizer(analyzer=analyzer, sublinear_tf=True)
        self.matrix = self.vec.fit_transform([d.text for d in self.docs])

    def search(self, query: str, k: int = 3, kind: str | None = None) -> list[Retrieved]:
        q = self.vec.transform([query])
        scores = (self.matrix @ q.T).toarray().ravel()
        order = np.argsort(-scores)
        out = []
        for i in order:
            if scores[i] <= 0:
                break
            if kind and self.docs[i].kind != kind:
                continue
            out.append(Retrieved(id=self.docs[i].id, title=self.docs[i].title, score=round(float(scores[i]), 3)))
            if len(out) == k:
                break
        return out

    def get(self, doc_id: str) -> Doc | None:
        return next((d for d in self.docs if d.id == doc_id), None)
