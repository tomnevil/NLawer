"""内存 BM25 倒排索引（零依赖，千级条目单查 <50ms）。

中文无空格分词，采用字符 unigram + bigram 组合，兼顾召回与精度。
"""
import math
import re
from typing import Any, Dict, List

_TOKEN_RE = re.compile(r"[一-鿿]|[a-zA-Z0-9]+")


def tokenize(text: str) -> List[str]:
    base = _TOKEN_RE.findall(text.lower())
    bigrams = [f"{base[i]}{base[i + 1]}" for i in range(len(base) - 1)]
    return base + bigrams


class BM25Index:
    def __init__(self, k1: float = 1.5, b: float = 0.75) -> None:
        self.k1 = k1
        self.b = b
        self.docs: List[Dict[str, Any]] = []
        self.df: Dict[str, int] = {}
        self.tf: List[Dict[str, int]] = []
        self.idf: Dict[str, float] = {}
        self.avgdl: float = 0.0

    def add(self, doc_id: Any, text: str, meta: Any = None) -> None:
        tokens = tokenize(text)
        tf: Dict[str, int] = {}
        for t in tokens:
            tf[t] = tf.get(t, 0) + 1
        self.docs.append({"id": doc_id, "text": text, "meta": meta or {}})
        self.tf.append(tf)
        for t in tf:
            self.df[t] = self.df.get(t, 0) + 1
        self._recompute_idf()

    def add_documents(self, docs: List[Dict[str, Any]]) -> None:
        for d in docs:
            self.add(d["id"], d["text"], d.get("meta"))

    def _recompute_idf(self) -> None:
        n = len(self.docs)
        self.avgdl = sum(sum(t.values()) for t in self.tf) / n if n else 0.0
        self.idf = {
            t: math.log((n - df + 0.5) / (df + 0.5) + 1.0) for t, df in self.df.items()
        }

    def search(self, query: str, top_k: int = 8) -> List[Dict[str, Any]]:
        q_tokens = tokenize(query)
        scores = []
        for i, tf in enumerate(self.tf):
            score = 0.0
            dl = sum(tf.values())
            for qt in q_tokens:
                if qt not in tf:
                    continue
                idf = self.idf.get(qt, 0.0)
                f = tf[qt]
                score += idf * (f * (self.k1 + 1)) / (
                    f + self.k1 * (1 - self.b + self.b * dl / (self.avgdl or 1))
                )
            if score > 0:
                scores.append((score, i))
        scores.sort(reverse=True)
        hits = []
        for score, i in scores[:top_k]:
            hits.append({**self.docs[i], "score": score})
        return hits
