"""文档 agent。

走 Milvus 拿到片段和相似度。Milvus 不可用时降级到本地关键词检索，返回结构不变。
"""

from __future__ import annotations

from ..tools import doc_tool

QUERY_HINTS = {
    "commit": "可用库存口径 可承诺量 调拨 效期 承诺规范",
    "shortage": "质检扣留 在途延误 缺货 归因",
    "rule_change": "保税仓 提前承诺 合同条款 规则变更",
}


def run(parsed: dict, question: str) -> dict:
    hint = QUERY_HINTS.get(parsed.get("intent"), "")
    query = f"{question} {hint} {parsed.get('sku_id', '')}".strip()
    hits = doc_tool.search(query, top_k=4)
    return {
        "query": query,
        "evidence": hits,
        "backend": hits[0].get("backend") if hits else doc_tool.backend(),
        "source": "milvus" if hits and hits[0].get("backend") == "milvus" else "local",
    }
