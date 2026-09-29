"""文本分块：按段落/条款切分，便于检索与引用。"""
from typing import Any, Dict, List


def chunk_text(text: str, *, meta: Any = None, max_chars: int = 400) -> List[Dict[str, Any]]:
    paras = [p.strip() for p in text.split("\n") if p.strip()]
    chunks: List[Dict[str, Any]] = []
    buf = ""
    idx = 0
    for p in paras:
        if len(buf) + len(p) > max_chars and buf:
            chunks.append({"text": buf, "meta": {**(meta or {}), "chunk": idx}})
            idx += 1
            buf = p
        else:
            buf = (buf + "\n" + p).strip()
    if buf:
        chunks.append({"text": buf, "meta": {**(meta or {}), "chunk": idx}})
    return chunks
