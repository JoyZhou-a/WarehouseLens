"""关系 agent。

走 Neo4j，回答需要多跳的问题：这个物料能不能用别的物料替代、替代有什么限制、
在途批次挂在哪个仓、某条规则出自哪份文件。
"""

from __future__ import annotations

from ..tools import graph_tool


def run(parsed: dict) -> dict:
    sku_id = parsed["sku_id"]

    subs = graph_tool.sub_chain(sku_id)
    paths = graph_tool.transitive(sku_id)
    supply = graph_tool.inbound_supply(sku_id)

    for item in subs:
        if item.get("restriction"):
            item["note"] = f"替代受限：{item['restriction']}"

    rules_from_docs = []
    if parsed.get("intent") == "rule_change":
        for doc in ("contract-C-2024-017", "SOP-BONDED", "SOP-EXPIRY"):
            rules_from_docs.extend(graph_tool.rules_of_doc(doc))

    return {
        "sku_id": sku_id,
        "substitutes": subs,
        "substitute_paths": paths,
        "inbound": supply,
        "rule_governance": rules_from_docs,
        "source": "neo4j",
    }
