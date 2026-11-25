"""文档 agent。

走 Milvus 拿到片段和相似度。Milvus 不可用时降级到本地关键词检索，返回结构不变。
"""

from __future__ import annotations

from ..tools import doc_tool

# 领域词不是为了让检索"能跑"，而是把问题里的槽位词（客户号、星期几、物料号）稀释掉，
# 让与文档用词更接近的部分占更大权重。换 bge 之后原句本身也有语义信号，所以是两者相加。
QUERY_HINTS = {
    "commit": "可承诺量 缺口 补充方案 承诺必须给出 可用库存量 计算过程 命中规则编号",
    "shortage": "质检扣留 在途延误 缺货 归因",
    "rule_change": "合同条款 适用条件 清关 提前承诺",
}


def _query(parsed: dict, question: str) -> str:
    hint = QUERY_HINTS.get(parsed.get("intent"), "")
    return f"{question} {hint} {parsed.get('sku_id', '')}".strip()


def run(parsed: dict, question: str) -> dict:
    query = _query(parsed, question)
    hits = doc_tool.search(query, top_k=4)
    return {
        "query": query,
        "evidence": hits,
        "backend": hits[0].get("backend") if hits else doc_tool.backend(),
        "source": "milvus" if hits and str(hits[0].get("backend", "")).startswith("milvus") else "local",
    }
