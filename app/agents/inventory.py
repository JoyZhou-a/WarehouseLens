"""库存子 agent 的确定性内核。

所有数字在这里算出来，不交给模型。口径来自 ontology：
先用 available_inventory 公式算可用量，再用 promiseable_qty 扣安全库存，
然后逐仓套用规则决定这个仓是否参与本次承诺。

支持 overrides（what-if 用，不改库也不改文件）：
    rule_off          临时关掉的规则 id 列表
    expiry_threshold  临时覆盖临期阈值（默认 30 天）
    wh_down           假设不可用的仓 id 列表
    eta_delta         在途单号 → 天数偏移（正数=晚到）
"""

from __future__ import annotations

from ..ontology import Ontology
from ..tools import mysql_tool

LEAD_TIME = {"WH-HF": 1, "WH-KS": 1, "WH-SZ": 2, "WH-CD": 3, "WH-HK": 3}
PRIORITY = {"WH-SH": 0, "WH-HF": 1, "WH-KS": 2, "WH-CD": 3, "WH-HK": 4, "WH-SZ": 5}


def _norm(overrides: dict | None) -> dict:
    o = dict(overrides or {})
    return {
        "rule_off": set(o.get("rule_off") or []),
        "thresholds": ({"batch_expiry_days": int(o["expiry_threshold"])}
                       if o.get("expiry_threshold") is not None else {}),
        "wh_down": set(o.get("wh_down") or []),
        "eta_delta": {k: int(v) for k, v in (o.get("eta_delta") or {}).items()},
    }


def warehouse_breakdown(onto: Ontology, sku_id: str, order_type: str,
                        overrides: dict | None = None) -> list[dict]:
    o = _norm(overrides)
    rows = mysql_tool.inventory_rows(sku_id)
    out = []
    for row in rows:
        available = onto.calc("available_inventory", row)
        promiseable = onto.calc("promiseable_qty", {**row, "available_inventory": available})
        hits = onto.eval_rules({"warehouse_type": row["warehouse_type"], "order_type": order_type},
                               off=o["rule_off"], thresholds=o["thresholds"])
        blocked = [h for h in hits if h["then"] == "exclude_warehouse"]
        blocked_by = [h["id"] for h in blocked]
        if row["warehouse_id"] in o["wh_down"]:
            blocked_by.append("假设:仓不可用")
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
            "promiseable": 0 if blocked_by else int(promiseable),
            "raw_promiseable": int(promiseable),
            "blocked_by": blocked_by,
            "rules": hits,
        })
    return out


def incoming_supply(onto: Ontology, sku_id: str, order_type: str,
                    overrides: dict | None = None) -> list[dict]:
    o = _norm(overrides)
    wh_type = {w["id"]: w["type"] for w in mysql_tool.warehouses()}
    out = []
    for row in mysql_tool.shipments(sku_id):
        delta = o["eta_delta"].get(row["id"], 0)
        # 晚到意味着到仓时剩余效期更短：效期按到仓时判定（SOP-EXPIRY 第 2 条）
        expiry_at_arrival = row["expiry_days"] - delta if row["expiry_days"] is not None else None
        ctx = {
            "warehouse_type": wh_type.get(row["warehouse_id"]),
            "order_type": order_type,
            "batch_expiry_days": expiry_at_arrival,
            "batch_cleared": row["cleared"],
        }
        hits = onto.eval_rules(ctx, off=o["rule_off"], thresholds=o["thresholds"])
        blocked = [h for h in hits if h["then"] == "exclude_batch"]
        blocked_by = [h["id"] for h in blocked]
        if row["warehouse_id"] in o["wh_down"]:
            blocked_by.append("假设:仓不可用")
        out.append({
            "shipment": row["id"],
            "warehouse_id": row["warehouse_id"],
            "batch": row["batch_id"],
            "qty": row["qty"],
            "eta_days": row["eta_days"] + delta,
            "expiry_days": expiry_at_arrival,
            "countable": not blocked_by,
            "blocked_by": blocked_by,
            "reason": [h["label"] for h in blocked] or (["假设该仓不可用"] if blocked_by else []),
            "assumed_delay": delta,
        })
    return out


def run(onto: Ontology, parsed: dict) -> dict:
    sku_id = parsed["sku_id"]
    order_type = parsed.get("order_type", "new_order")
    demand = int(parsed.get("demand") or 0)
    overrides = parsed.get("overrides") or {}

    warehouses = warehouse_breakdown(onto, sku_id, order_type, overrides)
    supply = incoming_supply(onto, sku_id, order_type, overrides)

    # 分池：直接计入本单可承诺的，和只能作为调拨源的。
    #   - 主供仓（可承诺量最大）：本单从这里发货，直接计入。
    #   - 保税仓：提前承诺＝保税批次直接计入可用，规则允许时也直接计入。
    #   - 其他成品仓：货要先调到主供仓才能发，只在补缺口时作为调拨源出现，不进合计。
    # 同分时按固定优先级，避免并列时排序退化成仓号字典序。
    ranked = sorted(warehouses, key=lambda w: (-w["promiseable"], PRIORITY.get(w["warehouse_id"], 9)))
    primary = next((w for w in ranked if w["promiseable"] > 0), None)
    direct, transfer_sources = [], []
    for w in ranked:
        if w["promiseable"] <= 0:
            continue
        if (primary and w["warehouse_id"] == primary["warehouse_id"]) \
                or w["warehouse_type"] == "bonded":
            direct.append(w)
        else:
            transfer_sources.append(w)

    total = sum(w["promiseable"] for w in direct)
    counted_supply = sum(s["qty"] for s in supply if s["countable"])

    need = demand - total
    transfers = []
    for w in transfer_sources:
        if need <= 0:
            break
        take = min(need, w["promiseable"])
        transfers.append({
            "from": w["warehouse_name"],
            "from_id": w["warehouse_id"],
            "qty": take,
            "lead_time_days": LEAD_TIME.get(w["warehouse_id"], 2),
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
        "after_transfer": total + sum(t["qty"] for t in transfers),
        "direct_ids": [w["warehouse_id"] for w in direct],
        "transfer_ids": [w["warehouse_id"] for w in transfer_sources],
        "primary": primary,
        "transfers": transfers,
        "counted_incoming_qty": counted_supply,
        "overrides": overrides,
        "backend": mysql_tool.backend(),
        "source": "mysql + ontology/" + onto.version,
    }
