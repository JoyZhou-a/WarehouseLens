"""仓储看板的数据聚合。

只做读取与汇总，不引入新的事实来源：数字全部从 MySQL 来，
可用量沿用 ontology 的口径（和问答链路同一套公式），
这样看板和问答不会出现两个口径。
"""

from __future__ import annotations

from ..ontology import get_ontology
from . import mysql_tool

EXPIRY_WARN_DAYS = 30
DUE_SOON_DAYS = 7


def _rows(sql: str, params: tuple = ()) -> list[dict]:
    return mysql_tool.query(sql, params)


def _available(row: dict) -> int:
    """单仓可用量，走 ontology 的公式，避免看板自己另算一套。"""
    try:
        return int(get_ontology().calc("available_inventory", row))
    except Exception:
        return int(row.get("on_hand") or 0)


def overview() -> dict:
    totals = _rows(
        "SELECT COALESCE(SUM(on_hand),0) AS on_hand, COALESCE(SUM(allocated),0) AS allocated,"
        " COALESCE(SUM(reserved),0) AS reserved, COALESCE(SUM(hold_qty),0) AS hold_qty,"
        " COALESCE(SUM(in_transit_arrived),0) AS arrived, COALESCE(SUM(safety_stock),0) AS safety,"
        " COUNT(*) AS cells FROM inventory"
    )[0]
    sku_count = _rows("SELECT COUNT(*) AS c FROM skus")[0]["c"]
    expiring = _rows(
        "SELECT COUNT(*) AS c, COALESCE(SUM(qty),0) AS qty FROM batches WHERE expiry_days < %s",
        (EXPIRY_WARN_DAYS,)
    )[0]
    uncleared = _rows(
        "SELECT COUNT(*) AS c, COALESCE(SUM(b.qty),0) AS qty FROM batches b"
        " JOIN warehouses w ON w.id = b.warehouse_id"
        " WHERE b.cleared = 0 AND w.type = 'bonded'"
    )[0]
    inbound = _rows("SELECT COUNT(*) AS c, COALESCE(SUM(qty),0) AS qty FROM shipments")[0]
    due = _rows(
        "SELECT COUNT(*) AS c, COALESCE(SUM(qty),0) AS qty FROM orders WHERE due_days <= %s",
        (DUE_SOON_DAYS,)
    )[0]

    # 可用量与扣留量要按行算，再汇总
    rows = _rows("SELECT sku_id, warehouse_id, on_hand, allocated, reserved, hold_qty,"
                 " in_transit_arrived, safety_stock FROM inventory")
    available = sum(_available(r) for r in rows)

    on_hand = int(totals["on_hand"])
    hold = int(totals["hold_qty"])
    return {
        "kpi": {
            "在库总量": on_hand,
            "可用量": available,
            "已分配": int(totals["allocated"]),
            "扣留": hold,
            "安全库存": int(totals["safety"]),
            "已到在途": int(totals["arrived"]),
            "物料数": int(sku_count),
            "库存格数": int(totals["cells"]),
        },
        "risk": {
            "临期批次数": int(expiring["c"]),
            "临期数量": int(expiring["qty"]),
            "未清关批次数": int(uncleared["c"]),
            "未清关数量": int(uncleared["qty"]),
            "在途单数": int(inbound["c"]),
            "在途数量": int(inbound["qty"]),
            "7天内订单数": int(due["c"]),
            "7天内订单量": int(due["qty"]),
        },
        "thresholds": {"expiry_warn_days": EXPIRY_WARN_DAYS, "due_soon_days": DUE_SOON_DAYS},
        "backend": mysql_tool.backend(),
    }


def by_warehouse() -> list[dict]:
    whs = {w["id"]: w for w in _rows("SELECT id, name, type, city FROM warehouses")}
    rows = _rows("SELECT sku_id, warehouse_id, on_hand, allocated, reserved, hold_qty,"
                 " in_transit_arrived, safety_stock FROM inventory")
    agg: dict[str, dict] = {}
    for r in rows:
        wid = r["warehouse_id"]
        item = agg.setdefault(wid, {"warehouse_id": wid, "on_hand": 0, "available": 0,
                                    "allocated": 0, "hold": 0, "safety": 0, "cells": 0})
        item["on_hand"] += int(r["on_hand"] or 0)
        item["available"] += _available(r)
        item["allocated"] += int(r["allocated"] or 0)
        item["hold"] += int(r["hold_qty"] or 0)
        item["safety"] += int(r["safety_stock"] or 0)
        item["cells"] += 1

    batch = {b["warehouse_id"]: b for b in _rows(
        "SELECT warehouse_id, COUNT(*) AS c FROM batches WHERE expiry_days < %s"
        " GROUP BY warehouse_id", (EXPIRY_WARN_DAYS,))}
    for wid, item in agg.items():
        meta = whs.get(wid, {})
        item["name"] = meta.get("name", wid)
        item["type"] = meta.get("type", "-")
        item["city"] = meta.get("city", "")
        item["expiring"] = int((batch.get(wid) or {}).get("c", 0))
        item["occupancy"] = round(100 * item["allocated"] / item["on_hand"], 1) if item["on_hand"] else 0
    return sorted(agg.values(), key=lambda x: x["on_hand"], reverse=True)


def top_skus(limit: int = 12) -> list[dict]:
    rows = _rows(
        "SELECT i.sku_id, s.name, s.category, SUM(i.on_hand) AS on_hand,"
        " SUM(i.allocated) AS allocated, SUM(i.hold_qty) AS hold_qty"
        " FROM inventory i JOIN skus s ON s.id = i.sku_id"
        " GROUP BY i.sku_id, s.name, s.category ORDER BY on_hand DESC LIMIT %s",
        (limit,)
    )
    out = []
    for r in rows:
        detail = _rows("SELECT on_hand, allocated, reserved, hold_qty, in_transit_arrived,"
                       " safety_stock FROM inventory WHERE sku_id = %s", (r["sku_id"],))
        available = sum(_available(d) for d in detail)
        out.append({
            "sku": r["sku_id"], "name": r["name"], "category": r["category"],
            "on_hand": int(r["on_hand"]), "available": available,
            "allocated": int(r["allocated"]), "hold": int(r["hold_qty"]),
            "gap": max(0, int(r["allocated"]) + int(r["hold_qty"]) - available),
        })
    return out


def expiring_batches(limit: int = 10) -> list[dict]:
    return _rows(
        "SELECT b.id, b.sku_id, s.name, b.warehouse_id, w.name AS warehouse_name,"
        " b.qty, b.expiry_days, b.cleared"
        " FROM batches b JOIN skus s ON s.id = b.sku_id"
        " JOIN warehouses w ON w.id = b.warehouse_id"
        " WHERE b.expiry_days < %s ORDER BY b.expiry_days LIMIT %s",
        (EXPIRY_WARN_DAYS, limit)
    )


def inbound_shipments(limit: int = 10) -> list[dict]:
    return _rows(
        "SELECT sh.id, sh.sku_id, s.name, sh.warehouse_id, w.name AS warehouse_name,"
        " sh.qty, sh.eta_days, sh.carrier"
        " FROM shipments sh JOIN skus s ON s.id = sh.sku_id"
        " LEFT JOIN warehouses w ON w.id = sh.warehouse_id"
        " ORDER BY sh.eta_days LIMIT %s", (limit,)
    )


def due_orders(limit: int = 10) -> list[dict]:
    return _rows(
        "SELECT o.id, o.customer, o.sku_id, s.name, o.qty, o.due_days, o.type"
        " FROM orders o JOIN skus s ON s.id = o.sku_id"
        " WHERE o.due_days <= %s ORDER BY o.due_days LIMIT %s", (DUE_SOON_DAYS, limit)
    )


def snapshot() -> dict:
    return {
        "overview": overview(),
        "warehouses": by_warehouse(),
        "top_skus": top_skus(),
        "expiring": expiring_batches(),
        "inbound": inbound_shipments(),
        "due_orders": due_orders(),
    }
