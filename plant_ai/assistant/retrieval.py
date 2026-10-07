"""SOP knowledge base and retrieval: BM25 over SOP sections, with optional embeddings fused by reciprocal rank.

Each SOP is a Markdown file with numbered sections (``## 6.4 Immediate checks``). Sections are the retrieval unit;
an SOP's score is its best section score plus a small share of its other matching sections, so a procedure that
matches in several places outranks one that mentions a single keyword.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from ..paths import KB

STOP = {
    "a",
    "an",
    "the",
    "and",
    "or",
    "of",
    "to",
    "in",
    "on",
    "at",
    "for",
    "with",
    "by",
    "from",
    "is",
    "are",
    "be",
    "been",
    "was",
    "were",
    "it",
    "its",
    "this",
    "that",
    "as",
    "into",
    "than",
    "then",
    "when",
    "while",
    "if",
    "not",
    "no",
    "any",
    "all",
    "each",
    "per",
    "can",
    "may",
    "will",
    "should",
    "must",
    "do",
    "does",
    "done",
    "has",
    "have",
    "had",
    "after",
    "before",
    "over",
    "under",
    "up",
    "down",
    "out",
    "so",
    "such",
    "use",
    "used",
    "using",
    "only",
    "also",
    "more",
    "most",
    "less",
    "very",
    "about",
    "which",
    "what",
    "who",
    "how",
}
TAG_RE = re.compile(r"\b[A-Z]{1,3}-\d{3}[A-Z]?(?:_[A-Z]+)?\b")


def stem(w: str) -> str:
    for suf in ("ing", "edly", "ed", "es", "s"):
        if len(w) > 4 and w.endswith(suf):
            return w[: -len(suf)]
    return w


def tokenize(text: str) -> list[str]:
    tokens: list[str] = []
    for tag in TAG_RE.findall(text):  # keep instrument tags whole (AIT-301) as well as their parts
        tokens.append(tag.lower())
    for w in re.findall(r"[a-z0-9]+", text.lower()):
        if w not in STOP and not w.isdigit():
            tokens.append(stem(w))
    return tokens


@dataclass(frozen=True)
class Section:
    sop_id: str  # SOP-06
    sop_title: str
    section: str  # 6.4
    heading: str  # Immediate checks
    text: str

    @property
    def ref(self) -> str:
        return f"{self.sop_id} {self.section}"


def load_kb(path: Path = KB) -> list[Section]:
    sections: list[Section] = []
    for f in sorted(path.glob("SOP-*.md")):
        lines = f.read_text(encoding="utf-8").splitlines()
        sop_id, _, sop_title = lines[0].lstrip("# ").strip().partition(" ")
        current: tuple[str, str] | None = None
        body: list[str] = []
        for ln in [*lines[1:], "## 0.0 end"]:
            m = re.match(r"## (\d+\.\d+) (.+)", ln)
            if m:
                if current:
                    sections.append(Section(sop_id, sop_title, current[0], current[1], "\n".join(body).strip()))
                current, body = (m.group(1), m.group(2)), []
            elif current:
                body.append(ln)
    return sections


def kb_headers(path: Path = KB) -> dict[str, str]:
    """The 'Area: ... Tags: ...' line under each SOP title."""
    out = {}
    for f in sorted(path.glob("SOP-*.md")):
        lines = f.read_text(encoding="utf-8").splitlines()
        out[lines[0].lstrip("# ").split(" ")[0]] = next((ln for ln in lines[1:] if ln.startswith("Area:")), "")
    return out


class BM25:
    def __init__(self, docs: list[list[str]], k1: float = 1.4, b: float = 0.75) -> None:
        self.docs = docs
        self.k1, self.b = k1, b
        self.avgdl = sum(len(d) for d in docs) / max(len(docs), 1)
        df: Counter[str] = Counter()
        for d in docs:
            df.update(set(d))
        n = len(docs)
        self.idf = {t: math.log(1 + (n - c + 0.5) / (c + 0.5)) for t, c in df.items()}
        self.tf = [Counter(d) for d in docs]

    def scores(self, query: list[str]) -> list[float]:
        out = []
        for tf, d in zip(self.tf, self.docs, strict=True):
            s = 0.0
            for t in set(query):
                if t in tf:
                    f = tf[t]
                    s += self.idf[t] * f * (self.k1 + 1) / (f + self.k1 * (1 - self.b + self.b * len(d) / self.avgdl))
            out.append(s)
        return out


@dataclass
class Hit:
    sop_id: str
    sop_title: str
    score: float
    sections: list[tuple[Section, float]]


class Retriever:
    def __init__(self, sections: list[Section] | None = None, embedder=None) -> None:
        self.sections = sections if sections is not None else load_kb()
        headers = kb_headers() if sections is None else {}
        # Index each section with its SOP title, the SOP's tag list and the heading, so that "blower trip" or
        # "AIT-301" finds every section of SOP-06, not only the one that repeats the words.
        self.index_text = [
            f"{s.sop_title} {s.sop_title} {headers.get(s.sop_id, '')} {s.heading} {s.text}" for s in self.sections
        ]
        self.bm25 = BM25([tokenize(t) for t in self.index_text])
        self.embedder = embedder
        self._vectors = None

    def sop_ids(self) -> list[str]:
        return sorted({s.sop_id for s in self.sections})

    def section(self, ref: str) -> Section | None:
        for s in self.sections:
            if s.ref == ref:
                return s
        return None

    def _section_scores(self, query: str) -> list[float]:
        scores = self.bm25.scores(tokenize(query))
        if self.embedder is None:
            return scores
        # Reciprocal rank fusion of the BM25 ranking and the embedding ranking.
        import numpy as np

        if self._vectors is None:
            self._vectors = np.array(self.embedder(self.index_text))
        q = np.array(self.embedder([query])[0])
        sims = self._vectors @ q / (np.linalg.norm(self._vectors, axis=1) * np.linalg.norm(q) + 1e-12)
        bm_rank = {i: r for r, i in enumerate(sorted(range(len(scores)), key=lambda i: -scores[i]))}
        em_rank = {i: r for r, i in enumerate(sorted(range(len(scores)), key=lambda i: -sims[i]))}
        return [1 / (60 + bm_rank[i]) + 1 / (60 + em_rank[i]) for i in range(len(scores))]

    def search(self, query: str, k: int = 3) -> list[Hit]:
        scores = self._section_scores(query)
        by_sop: dict[str, list[tuple[Section, float]]] = {}
        for s, sc in zip(self.sections, scores, strict=True):
            by_sop.setdefault(s.sop_id, []).append((s, sc))
        hits = []
        for sop_id, secs in by_sop.items():
            secs.sort(key=lambda x: -x[1])
            score = secs[0][1] + 0.25 * sum(sc for _, sc in secs[1:3])
            hits.append(Hit(sop_id, secs[0][0].sop_title, score, secs))
        hits.sort(key=lambda h: -h.score)
        return hits[:k]
