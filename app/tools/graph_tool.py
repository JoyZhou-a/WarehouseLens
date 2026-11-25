"""Neo4j 访问。

优先走图数据库。连不上就用本地数据层现算同样的路径，返回结构一致。
"""

from __future__ import annotations

import time
from typing import Any

from neo4j import GraphDatabase

from .. import config
from . import dataset

# 降级不是永久的：云上偶发一次超时不该让整场演示都退回本地数据。
# 失败后冷却 20 秒，期间快速返回本地结果，到点自动再试真库。
COOLDOWN = 20.0
_degraded_at: float | None = None
_last_error: str | None = None
_driver = None


def _degraded() -> bool:
    if _degraded_at is None:
        return False
    return (time.time() - _degraded_at) < COOLDOWN


def _mark_failed(exc: Exception | None = None) -> None:
    global _degraded_at, _last_error
    _degraded_at = time.time()
    if exc is not None:
        _last_error = f"{type(exc).__name__}: {exc}"
        # 降级不能是静默的：打到服务日志，现场也能在终端看见
        print(f"[graph_tool] 退回本地数据：{_last_error[:300]}", flush=True)


def _mark_ok() -> None:
    global _degraded_at
    _degraded_at = None


def last_error() -> str | None:
    return _last_error


def backend() -> str:
    return "local" if _degraded() else "neo4j"


def driver():
    global _driver
    if _driver is None:
        _driver = GraphDatabase.driver(
            config.NEO4J_URI, auth=config.NEO4J_AUTH,
            # 云上 Bolt 首连要 200ms~1s，并发时更久，2 秒会误判成连不上
            connection_timeout=8, max_connection_lifetime=300,
        )
    return _driver


def run(cypher: str, **params) -> list[dict[str, Any]]:
    with driver().session() as session:
        return [dict(r) for r in session.run(cypher, **params)]


def _try(cypher: str, params: dict, fallback):
    if _degraded():
        return fallback()
    try:
        result = run(cypher, **params)
        _mark_ok()
        return result
    except Exception as exc:
        _mark_failed(exc)
        return fallback()


def ping() -> bool:
    if _degraded():
        return False
    try:
        run("RETURN 1 AS ok")
        _mark_ok()
        return True
    except Exception as exc:
        _mark_failed(exc)
        return False


def sub_chain(sku_id: str) -> list[dict]:
    def local():
        out = []
        for s in dataset.substitutes(sku_id):
            stock = [
                {"batch": b["id"], "warehouse": b["warehouse_id"],
                 "qty": b["qty"], "expiry_days": b["expiry_days"]}
                for b in dataset.batches(s["to_sku"])
            ][:5]
            out.append({"from_sku": s["from_sku"], "to_sku": s["to_sku"],
                        "to_name": s["to_name"], "restriction": s["restriction"],
                        "stock": stock})
        return out

    return _try(
        """
        MATCH (a:SKU {id:$sku})-[:SUBSTITUTE_OF]->(b:SKU)
        OPTIONAL MATCH (b)-[:HAS_BATCH]->(bt:Batch)-[:STORED_IN]->(w:Warehouse)
        RETURN a.id AS from_sku, b.id AS to_sku, b.name AS to_name,
               [r IN [(a)-[x:SUBSTITUTE_OF]->(b) | x.restriction]][0] AS restriction,
               collect(DISTINCT {batch: bt.id, warehouse: w.name,
                                 qty: bt.qty, expiry_days: bt.expiry_days})[..5] AS stock
        """,
        {"sku": sku_id},
        local,
    )


def transitive(sku_id: str) -> list[dict]:
    def local():
        paths = []
        frontier = [(sku_id, [sku_id])]
        for _ in range(3):
            nxt = []
            for node, path in frontier:
                for s in dataset.SUBSTITUTES:
                    if s["from_sku"] == node and s["to_sku"] not in path:
                        new_path = path + [s["to_sku"]]
                        paths.append({"path": new_path, "hops": len(new_path) - 1})
                        nxt.append((s["to_sku"], new_path))
            frontier = nxt
        return paths

    return _try(
        """
        MATCH p=(a:SKU {id:$sku})-[:SUBSTITUTE_OF*1..3]->(b:SKU)
        RETURN [n IN nodes(p) | n.id] AS path, length(p) AS hops
        """,
        {"sku": sku_id},
        local,
    )


def inbound_supply(sku_id: str) -> list[dict]:
    names = {w["id"]: w["name"] for w in dataset.WAREHOUSES}

    def local():
        return [{"shipment": s["id"], "warehouse": names.get(s["warehouse_id"]),
                 "qty": s["qty"], "eta_days": s["eta_days"], "carrier": s["carrier"],
                 "batch": s["batch_id"], "expiry_days": s.get("expiry_days")}
                for s in dataset.shipments(sku_id)]

    return _try(
        """
        MATCH (sh:Shipment)-[:CARRIES]->(k:SKU {id:$sku})
        OPTIONAL MATCH (sh)-[:DELIVERS_TO]->(w:Warehouse)
        OPTIONAL MATCH (sh)-[:HAS_BATCH]->(b:Batch)
        RETURN sh.id AS shipment, w.name AS warehouse, sh.qty AS qty,
               sh.eta_days AS eta_days, sh.carrier AS carrier,
               b.id AS batch, b.expiry_days AS expiry_days
        ORDER BY sh.eta_days
        """,
        {"sku": sku_id},
        local,
    )


def rules_of_doc(doc_id: str) -> list[dict]:
    def local():
        out = [{"doc": doc_id, "rel": "GOVERNS", "rule": rid, "label": None, "enabled": None}
               for rid in dataset.DOC_RULES.get(doc_id, [])]
        out += [{"doc": doc_id, "rel": "OVERRIDES", "rule": rid, "label": None, "enabled": None}
                for rid in dataset.OVERRIDES.get(doc_id, [])]
        return out

    return _try(
        """
        MATCH (d:Doc {id:$doc})-[x:GOVERNS|OVERRIDES]->(r:Rule)
        RETURN d.id AS doc, type(x) AS rel, r.id AS rule, r.label AS label,
               r.enabled AS enabled
        """,
        {"doc": doc_id},
        local,
    )


def rule_sources(rule_id: str) -> list[dict]:
    def local():
        out = []
        for doc, rules in dataset.DOC_RULES.items():
            if rule_id in rules:
                out.append({"doc": doc, "rel": "GOVERNS", "rule": rule_id})
        for doc, rules in dataset.OVERRIDES.items():
            if rule_id in rules:
                out.append({"doc": doc, "rel": "OVERRIDES", "rule": rule_id})
        return out

    return _try(
        """
        MATCH (d:Doc)-[x:GOVERNS|OVERRIDES]->(r:Rule {id:$rule})
        RETURN d.id AS doc, type(x) AS rel, r.id AS rule
        """,
        {"rule": rule_id},
        local,
    )


def overview() -> dict:
    from ..ontology import get_ontology

    onto = get_ontology()

    def local():
        return {
            "version": onto.version,
            "rule_count": len(onto.rules),
            "warehouses": len(dataset.WAREHOUSES),
            "skus": len(dataset.SKUS),
            "backend": backend(),
        }

    return _try(
        """
        MATCH (o:Ontology)-[:HAS_RULE]->(r:Rule)
        WITH o, count(r) AS rule_count
        OPTIONAL MATCH (w:Warehouse) WITH o, rule_count, count(DISTINCT w) AS wh
        OPTIONAL MATCH (k:SKU) WITH o, rule_count, wh, count(DISTINCT k) AS sku
        RETURN o.version AS version, rule_count, wh AS warehouses, sku AS skus
        """,
        {},
        local,
    )
