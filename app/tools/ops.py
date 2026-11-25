"""子 agent 用的领域工具集（确定性）。

约定：工具负责取数与算数，模型只负责「选哪个工具、传什么参数」和「解释结果」。
每个工具都返回可 JSON 序列化的 dict，并把 backend 一起返回，方便前端显示走的哪条路。
"""

from __future__ import annotations

from typing import Any

from ..agents import inventory as inv_agent
from ..ontology import get_ontology
from . import dataset, doc_tool, graph_tool, mysql_tool

# ---------------------------------------------------------------- 通用取数


def get_inventory(sku: str) -> dict:
    """某物料的各仓库存明细（在库/已分配/扣留/已到在途/安全库存）。"""
    rows = mysql_tool.inventory_rows(sku)
    return {"sku": sku, "rows": rows, "backend": mysql_tool.backend()}


def get_batches(sku: str, warehouse: str | None = None,
                expiring_within_days: int | None = None) -> dict:
    """某物料的批次，可按仓与剩余效期过滤。"""
    rows = mysql_tool.batches(sku, warehouse)
    if expiring_within_days is not None:
        rows = [b for b in rows if b["expiry_days"] is not None
                and b["expiry_days"] < expiring_within_days]
    return {"sku": sku, "count": len(rows), "rows": rows, "backend": mysql_tool.backend()}


def get_shipments(sku: str) -> dict:
    """某物料的在途单（含所运批次的效期与清关状态）。"""
    rows = mysql_tool.shipments(sku)
    return {"sku": sku, "rows": rows, "backend": mysql_tool.backend()}


# ---------------------------------------------------------------- 计算

ORDER_TYPES = ("new_order", "replenish", "after_sales")


def _norm_order_type(value: str | None) -> str:
    """模型会把 order_type 传成「客户订单」「售后」这种中文，这里归一成枚举，
    否则 R1/R4 这类按 order_type 匹配的规则会失效，算出来的数就是错的。"""
    raw = str(value or "").strip().lower()
    if raw in ORDER_TYPES:
        return raw
    if any(k in raw for k in ("售后", "维修", "after", "service")):
        return "after_sales"
    if any(k in raw for k in ("补货", "replenish", "内部")):
        return "replenish"
    return "new_order"


def compute_promise(sku: str, demand: int, order_type: str = "new_order",
                    overrides: dict | None = None) -> dict:
    """按 ontology 口径算可承诺量与缺口，可带入假设（overrides）。"""
    parsed = {"sku_id": sku, "demand": int(demand), "order_type": _norm_order_type(order_type),
              "overrides": overrides or {}}
    result = inv_agent.run(get_ontology(), parsed)
    result["order_type"] = parsed["order_type"]
    return result


def simulate(sku: str, demand: int, assumptions: dict | None = None) -> dict:
    """假设推演。assumptions 支持：
    rule_off（规则 id 列表）、expiry_threshold（临期天数）、
    wh_down（仓 id 列表）、eta_delta（{在途单号: 天数}）。
    """
    base = compute_promise(sku, demand)
    trial = compute_promise(sku, demand, "new_order", assumptions or {})
    return {
        "assumptions": assumptions or {},
        "before": {"可承诺合计": base["total_promiseable"], "缺口": base["gap"],
                   "调拨后": base["after_transfer"]},
        "after": {"可承诺合计": trial["total_promiseable"], "缺口": trial["gap"],
                  "调拨后": trial["after_transfer"]},
        "delta": trial["total_promiseable"] - base["total_promiseable"],
        "warehouses_after": trial["warehouses"],
        "transfers_after": trial["transfers"],
        "backend": trial["backend"],
    }


# ---------------------------------------------------------------- 列表类

def list_orders(due_within_days: int = 7, sku: str | None = None,
                order_type: str | None = None) -> dict:
    """按到期天数列订单，回答「这周哪些货要发」这类问题。"""
    try:
        sql = "SELECT id, customer, sku_id, qty, due_days, type FROM orders WHERE due_days <= %s"
        params: list[Any] = [int(due_within_days)]
        if sku:
            sql += " AND sku_id = %s"
            params.append(sku)
        if order_type:
            sql += " AND type = %s"
            params.append(order_type)
        sql += " ORDER BY due_days, id"
        rows = mysql_tool.query(sql, params)
        backend = mysql_tool.backend()
    except Exception:
        rows = [o for o in dataset.orders(sku) if o["due_days"] <= int(due_within_days)]
        if order_type:
            rows = [o for o in rows if o["type"] == order_type]
        rows.sort(key=lambda o: (o["due_days"], o["id"]))
        backend = "local"
    total_qty = sum(r["qty"] for r in rows)
    return {"window_days": int(due_within_days), "count": len(rows), "total_qty": total_qty,
            "rows": rows, "backend": backend}


def list_due_shipments(eta_within_days: int = 7, sku: str | None = None) -> dict:
    """按预计到仓天数列在途。"""
    try:
        sql = ("SELECT s.id, s.sku_id, s.warehouse_id, s.batch_id, s.qty, s.eta_days, "
               "s.carrier, b.expiry_days, b.cleared FROM shipments s "
               "LEFT JOIN batches b ON b.id = s.batch_id WHERE s.eta_days <= %s")
        params: list[Any] = [int(eta_within_days)]
        if sku:
            sql += " AND s.sku_id = %s"
            params.append(sku)
        sql += " ORDER BY s.eta_days, s.id"
        rows = mysql_tool.query(sql, params)
        backend = mysql_tool.backend()
    except Exception:
        rows = [r for r in dataset.SHIPMENTS if r["eta_days"] <= int(eta_within_days)
                and (not sku or r["sku_id"] == sku)]
        rows.sort(key=lambda r: (r["eta_days"], r["id"]))
        backend = "local"
    return {"window_days": int(eta_within_days), "count": len(rows), "rows": rows,
            "backend": backend}


def list_expiring_batches(expiring_within_days: int = 30, sku: str | None = None) -> dict:
    """按剩余效期列快过期的批次（不带 SKU 时列出全部）。"""
    try:
        sql = ("SELECT b.id, b.sku_id, b.warehouse_id, b.qty, b.expiry_days, b.cleared "
               "FROM batches b WHERE b.expiry_days < %s")
        params: list[Any] = [int(expiring_within_days)]
        if sku:
            sql += " AND b.sku_id = %s"
            params.append(sku)
        sql += " ORDER BY b.expiry_days, b.id"
        rows = mysql_tool.query(sql, params)
        backend = mysql_tool.backend()
    except Exception:
        rows = [b for b in dataset.BATCHES if b["expiry_days"] < int(expiring_within_days)
                and (not sku or b["sku_id"] == sku)]
        rows.sort(key=lambda b: (b["expiry_days"], b["id"]))
        backend = "local"
    return {"window_days": int(expiring_within_days), "count": len(rows), "rows": rows,
            "backend": backend}


def list_blocked_supply(sku: str) -> dict:
    """某物料里被规则挡掉、不计入可承诺的供应（批次/仓）。"""
    result = compute_promise(sku, 0)
    blocked_wh = [{"仓": w["warehouse_name"], "原始可承诺": w["raw_promiseable"],
                   "命中规则": w["blocked_by"]} for w in result["warehouses"] if w["blocked_by"]]
    blocked_ship = [{"在途单": s["shipment"], "数量": s["qty"], "命中规则": s["blocked_by"],
                     "原因": s["reason"]} for s in result["supply"] if not s["countable"]]
    return {"sku": sku, "blocked_warehouses": blocked_wh, "blocked_supply": blocked_ship,
            "backend": result["backend"]}


# ---------------------------------------------------------------- 图与文档

def get_substitutes(sku: str, max_hops: int = 1) -> dict:
    """替代料链路。max_hops=1 只看一跳，>1 用变长路径。"""
    if int(max_hops) <= 1:
        rows = graph_tool.sub_chain(sku)
        return {"sku": sku, "hops": 1, "rows": rows, "backend": graph_tool.backend()}
    rows = graph_tool.transitive(sku)
    return {"sku": sku, "hops": int(max_hops), "rows": rows, "backend": graph_tool.backend()}


def get_inbound_chain(sku: str) -> dict:
    """在途 → 物料 → 仓 → 批次 的链路（图库侧）。"""
    return {"sku": sku, "rows": graph_tool.inbound_supply(sku), "backend": graph_tool.backend()}


def _resolve_rule(rule: str) -> str:
    """模型经常把规则的中文名当 id 传过来（「保税仓批次提前承诺」），这里做一次归一。"""
    onto = get_ontology()
    ids = {r["id"] for r in onto.rules}
    raw = str(rule or "").strip()
    if raw.upper() in ids:
        return raw.upper()
    for r in onto.rules:
        if r["id"] in raw:
            return r["id"]
        label = r.get("label") or ""
        if label and (raw in label or label in raw):
            return r["id"]
    keywords = [("保税", "R3"), ("提前承诺", "R3"), ("未清关", "R2"), ("清关", "R2"),
                ("临期", "R1"), ("效期", "R1"), ("备件", "R4"), ("售后", "R4")]
    for key, rid in keywords:
        if key in raw:
            return rid
    return raw


def get_rule_sources(rule_id: str) -> dict:
    """某条规则出自哪些文档、被谁覆盖。"""
    rid = _resolve_rule(rule_id)
    return {"rule": rid, "asked_as": rule_id, "rows": graph_tool.rule_sources(rid),
            "backend": graph_tool.backend()}


def get_rule_governance(doc_id: str) -> dict:
    """某份文档治理了哪些规则。"""
    return {"doc": doc_id, "rows": graph_tool.rules_of_doc(doc_id), "backend": graph_tool.backend()}


def search_docs(query: str, top_k: int = 4) -> dict:
    """在 SOP/合同/邮件里做语义检索。"""
    hits = doc_tool.search(query, int(top_k))
    return {"query": query, "count": len(hits), "hits": hits,
            "backend": hits[0]["backend"] if hits else doc_tool.backend()}


def read_doc(doc_id: str) -> dict:
    """读某份文档全文（用于确认片段上下文）。"""
    text = doc_tool.read_doc(doc_id)
    return {"doc_id": doc_id, "found": text is not None,
            "text": (text or "")[:1500], "backend": "local"}
