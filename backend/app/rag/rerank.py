"""重排序（可插拔）。

默认基于关键词重叠的简单打分；生产可替换为 Cross-Encoder。MVP 无强依赖。
"""
from typing import Any, Dict, List


def _overlap(query: str, text: str) -> float:
    q = set(query)
    t = set(text)
    if not t:
        return 0.0
    return len(q & t) / len(t)


def rerank(query: str, hits: List[Dict[str, Any]], top_k: int = 8) -> List[Dict[str, Any]]:
    for h in hits:
        h["rerank_score"] = h.get("score", 0.0) * 0.7 + _overlap(query, h.get("text", "")) * 0.3
    hits.sort(key=lambda x: x.get("rerank_score", 0.0), reverse=True)
    return hits[:top_k]
