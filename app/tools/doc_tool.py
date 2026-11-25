"""文档检索。优先走 Milvus，连不上就降级到本地关键词打分。

两个 provider 各自的 collection：
- wms_docs_bge   1024 维，BAAI/bge-m3（租的接口）
- wms_docs_hash  512 维，本地字符哈希
查询向量必须和索引用同一个 provider，所以切换是整条链一起切，不会混用。

返回结构里带 backend（milvus/bge、milvus/hash、local），前端会显示用的是哪条路径。
"""

from __future__ import annotations

import time
from pathlib import Path

from .. import config
from .embedding import embed

DOC_DIR = config.ROOT / "data" / "docs"
COOLDOWN = 20.0

_collections: dict[str, object] = {}
_failed_at: dict[str, float] = {}


def _collection(provider: str):
    col = _collections.get(provider)
    if col is None:
        from pymilvus import Collection, connections

        connections.connect(alias="default", host=config.MILVUS_HOST, port=config.MILVUS_PORT)
        col = Collection(config.COLLECTION_BY_PROVIDER[provider])
        col.load()
        _collections[provider] = col
    return col


def active_provider() -> str:
    """当前实际用哪个 provider。bge 失败后冷却期内退回 hash。"""
    if config.ACTIVE_EMBED == "hash":
        return "hash"
    failed = _failed_at.get("bge")
    if failed and (time.time() - failed) < COOLDOWN:
        return "hash"
    return "bge"


def _milvus_search(query: str, top_k: int, provider: str) -> list[dict]:
    col = _collection(provider)
    res = col.search(
        data=[embed(query, provider)],
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
                "backend": f"milvus/{provider}",
            })
    return out


def _fallback_search(query: str, top_k: int) -> list[dict]:
    """Milvus 与两个 provider 都不可用时，用字符重合度打分，行为对使用方一致。"""
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
    provider = active_provider()
    try:
        hits = _milvus_search(query, top_k, provider)
        if hits:
            return hits
    except Exception as exc:
        print(f"[doc_tool] {provider} 检索失败，切换：{type(exc).__name__}: {exc}", flush=True)
        _failed_at[provider] = time.time()

    if provider != "hash":
        try:
            hits = _milvus_search(query, top_k, "hash")
            if hits:
                return hits
        except Exception as exc:
            print(f"[doc_tool] hash 检索也失败：{type(exc).__name__}: {exc}", flush=True)

    return _fallback_search(query, top_k)


def read_doc(doc_id: str) -> str | None:
    path = DOC_DIR / f"{doc_id}.md"
    return path.read_text(encoding="utf-8") if path.exists() else None


def backend() -> str:
    provider = active_provider()
    try:
        _collection(provider)
        return f"milvus/{provider}"
    except Exception:
        return "local"
