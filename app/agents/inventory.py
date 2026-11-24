"""库存 agent。

所有数字在这里算出来，不交给模型。
口径来自 ontology：先用 available_inventory 公式算可用量，再用 promiseable_qty 扣安全库存，
然后逐仓套用规则决定这个仓是否参与本次承诺。
"""

from __future__ import annotations

from ..ontology import Ontology
from ..tools import mysql_tool


def _rule_ctx(row: dict, order_type: str, **extra) -> dict:
    ctx = {
        "warehouse_type": row.get("warehouse_type"),
        "order_type": order_type,
    }
    ctx.update(extra)
    return ctx


def warehouse_breakdown(onto: Ontology, sku_id: str, order_type: str) -> list[dict]:
    rows = mysql_tool.inventory_rows(sku_id)
    out = []
    for row in rows:
        available = onto.calc("available_inventory", row)
        promiseable = onto.calc("promiseable_qty", {**row, "available_inventory": available})
        hits = onto.eval_rules(_rule_ctx(row, order_type))
        blocked = [h for h in hits if h["then"] == "exclude_warehouse"]
        out.append({
            "warehouse_id": row["warehouse_id"],
            "warehouse_name": row["warehouse_name"],
            "warehouse_type": row["warehouse_type"],
            "on_hand": row["on_hand"],
            "allocated": row["allocated"],
            "reserved": row["reserved"],
            "hold_qty": row["hold_qty"],
            "in_transit_arrived": row["in_transit_arrived"],
            "safety_stock": row["safety_stock"],
            "available": int(available),
            "promiseable": 0 if blocked else int(promiseable),
            "raw_promiseable": int(promiseable),
            "blocked_by": [h["id"] for h in blocked],
            "rules": hits,
        })
    return out


def incoming_supply(onto: Ontology, sku_id: str, order_type: str) -> list[dict]:
    wh_type = {w["id"]: w["type"] for w in mysql_tool.warehouses()}
    out = []
    for row in mysql_tool.shipments(sku_id):
        ctx = {
            "warehouse_type": wh_type.get(row["warehouse_id"]),
            "order_type": order_type,
            "batch_expiry_days": row["expiry_days"],
            "batch_cleared": row["cleared"],
        }
        hits = onto.eval_rules(ctx)
        blocked = [h for h in hits if h["then"] == "exclude_batch"]
        out.append({
            "shipment": row["id"],
            "warehouse_id": row["warehouse_id"],
            "batch": row["batch_id"],
            "qty": row["qty"],
            "eta_days": row["eta_days"],
            "expiry_days": row["expiry_days"],
            "countable": not blocked,
            "blocked_by": [h["id"] for h in blocked],
            "reason": [h["label"] for h in blocked],
        })
    return out


def run(onto: Ontology, parsed: dict) -> dict:
    sku_id = parsed["sku_id"]
    order_type = parsed.get("order_type", "new_order")
    demand = int(parsed.get("demand") or 0)

    warehouses = warehouse_breakdown(onto, sku_id, order_type)
    supply = incoming_supply(onto, sku_id, order_type)

    total = sum(w["promiseable"] for w in warehouses)
    counted_supply = sum(s["qty"] for s in supply if s["countable"])

    ranked = sorted(warehouses, key=lambda w: w["promiseable"], reverse=True)
    primary = ranked[0] if ranked else None
    transfers = []
    if primary and demand > primary["promiseable"]:
        need = demand - primary["promiseable"]
        for w in ranked[1:]:
            if need <= 0:
                break
            if w["promiseable"] <= 0:
                continue
            take = min(need, w["promiseable"])
            transfers.append({
                "from": w["warehouse_name"],
                "from_id": w["warehouse_id"],
                "qty": take,
                "lead_time_days": 1 if w["warehouse_id"] == "WH-HF" else 2,
            })
            need -= take

    return {
        "sku_id": sku_id,
        "sku_name": (mysql_tool.sku(sku_id) or {}).get("name", sku_id),
        "order_type": order_type,
        "demand": demand,
        "warehouses": warehouses,
        "supply": supply,
        "total_promiseable": total,
        "counted_incoming": counted_supply,
        "gap": max(0, demand - total),
        "primary": primary,
        "transfers": transfers,
        "source": "mysql + ontology/" + onto.version,
    }
