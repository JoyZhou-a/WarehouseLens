"""文档检索。优先走 Milvus，连不上就降级到本地关键词打分。

降级是为了保证演示不问数据库能不能连上都能出证据。
返回结构里带 backend 字段，前端会显示用的是哪条路径。
"""

from __future__ import annotations

from pathlib import Path

from .. import config
from .embedding import embed

DOC_DIR = config.ROOT / "data" / "docs"
_collection = None


def _milvus():
    global _collection
    if _collection is None:
        from pymilvus import Collection, connections

        connections.connect(alias="default", host=config.MILVUS_HOST, port=config.MILVUS_PORT)
        _collection = Collection(config.MILVUS_COLLECTION)
        _collection.load()
    return _collection


def _milvus_search(query: str, top_k: int) -> list[dict]:
    col = _milvus()
    res = col.search(
        data=[embed(query)],
        anns_field="vector",
        param={"metric_type": "COSINE", "params": {"nprobe": 16}},
        limit=top_k,
        output_fields=["doc_id", "title", "chunk_no", "text"],
    )
    out = []
    for hits in res:
        for h in hits:
            entity = h.entity
            out.append({
                "doc_id": entity.get("doc_id"),
                "title": entity.get("title"),
                "chunk_no": entity.get("chunk_no"),
                "text": entity.get("text"),
                "score": round(float(h.score), 4),
                "backend": "milvus",
            })
    return out


def _fallback_search(query: str, top_k: int) -> list[dict]:
    """Milvus 不可用时，用字符重合度打分，行为对使用方一致。"""
    q = set(_bigrams(query))
    scored = []
    for path in sorted(DOC_DIR.glob("*.md")):
        for i, chunk in enumerate(c.strip() for c in path.read_text(encoding="utf-8").split("\n\n")):
            if len(chunk) < 40:
                continue
            c = set(_bigrams(chunk))
            score = len(q & c) / max(1, len(q))
            scored.append({
                "doc_id": path.stem, "title": path.stem, "chunk_no": i,
                "text": chunk, "score": round(score, 4), "backend": "local",
            })
    scored.sort(key=lambda x: x["score"], reverse=True)
    return scored[:top_k]


def _bigrams(text: str) -> list[str]:
    clean = "".join(text.split())
    return [clean[i:i + 2] for i in range(len(clean) - 1)]


def search(query: str, top_k: int = 4) -> list[dict]:
    try:
        hits = _milvus_search(query, top_k)
        if hits:
            return hits
    except Exception:
        pass
    return _fallback_search(query, top_k)


def read_doc(doc_id: str) -> str | None:
    path = DOC_DIR / f"{doc_id}.md"
    return path.read_text(encoding="utf-8") if path.exists() else None


def backend() -> str:
    try:
        _milvus()
        return "milvus"
    except Exception:
        return "local"
