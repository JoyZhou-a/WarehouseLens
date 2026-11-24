"""本地数据层。

用途有两个：
1. 没有启动 MySQL / Neo4j 时，工具层退到这里，演示照样能跑。
2. 作为灌库脚本的唯一数据来源，避免两份数据不一致。

数据结构与数据库表的字段保持一致，所以上层代码不需要知道自己走的是哪条路。
"""

from __future__ import annotations

WAREHOUSES = [
    {"id": "WH-SH", "name": "上海成品仓", "type": "finished_goods", "city": "上海"},
    {"id": "WH-HF", "name": "合肥成品仓", "type": "finished_goods", "city": "合肥"},
    {"id": "WH-SZ", "name": "深圳备件仓", "type": "service_parts", "city": "深圳"},
    {"id": "WH-HK", "name": "香港保税仓", "type": "bonded", "city": "香港"},
]

SKUS = [
    {"id": "NB-X1C-14", "name": "ThinkPad X1 Carbon 14", "category": "笔记本"},
    {"id": "NB-T14-G5", "name": "ThinkPad T14 Gen5", "category": "笔记本"},
    {"id": "SP-MB-14", "name": "14 寸主板备件", "category": "备件"},
]

INVENTORY = [
    {"sku_id": "NB-X1C-14", "warehouse_id": "WH-SH", "on_hand": 500, "allocated": 120,
     "reserved": 0, "hold_qty": 0, "in_transit_arrived": 40, "safety_stock": 0},
    {"sku_id": "NB-X1C-14", "warehouse_id": "WH-HF", "on_hand": 120, "allocated": 0,
     "reserved": 0, "hold_qty": 30, "in_transit_arrived": 0, "safety_stock": 0},
    {"sku_id": "NB-X1C-14", "warehouse_id": "WH-HK", "on_hand": 60, "allocated": 0,
     "reserved": 0, "hold_qty": 0, "in_transit_arrived": 0, "safety_stock": 0},
    {"sku_id": "NB-T14-G5", "warehouse_id": "WH-SH", "on_hand": 200, "allocated": 60,
     "reserved": 0, "hold_qty": 10, "in_transit_arrived": 0, "safety_stock": 20},
    {"sku_id": "NB-T14-G5", "warehouse_id": "WH-HF", "on_hand": 40, "allocated": 0,
     "reserved": 0, "hold_qty": 0, "in_transit_arrived": 0, "safety_stock": 0},
    {"sku_id": "SP-MB-14", "warehouse_id": "WH-SZ", "on_hand": 300, "allocated": 80,
     "reserved": 20, "hold_qty": 0, "in_transit_arrived": 0, "safety_stock": 0},
    {"sku_id": "SP-MB-14", "warehouse_id": "WH-SH", "on_hand": 30, "allocated": 0,
     "reserved": 0, "hold_qty": 0, "in_transit_arrived": 0, "safety_stock": 0},
]

BATCHES = [
    {"id": "B-SH-X1C-01", "sku_id": "NB-X1C-14", "warehouse_id": "WH-SH", "qty": 300,
     "expiry_days": 200, "cleared": 1},
    {"id": "B-SH-X1C-02", "sku_id": "NB-X1C-14", "warehouse_id": "WH-SH", "qty": 200,
     "expiry_days": 180, "cleared": 1},
    {"id": "B-SH-X1C-03", "sku_id": "NB-X1C-14", "warehouse_id": "WH-SH", "qty": 80,
     "expiry_days": 25, "cleared": 1},
    {"id": "B-HF-X1C-01", "sku_id": "NB-X1C-14", "warehouse_id": "WH-HF", "qty": 120,
     "expiry_days": 150, "cleared": 1},
    {"id": "B-HK-X1C-01", "sku_id": "NB-X1C-14", "warehouse_id": "WH-HK", "qty": 60,
     "expiry_days": 240, "cleared": 0},
    {"id": "B-SH-T14-01", "sku_id": "NB-T14-G5", "warehouse_id": "WH-SH", "qty": 200,
     "expiry_days": 160, "cleared": 1},
    {"id": "B-HF-T14-01", "sku_id": "NB-T14-G5", "warehouse_id": "WH-HF", "qty": 40,
     "expiry_days": 210, "cleared": 1},
    {"id": "B-SZ-MB-01", "sku_id": "SP-MB-14", "warehouse_id": "WH-SZ", "qty": 300,
     "expiry_days": 500, "cleared": 1},
    {"id": "B-SH-MB-01", "sku_id": "SP-MB-14", "warehouse_id": "WH-SH", "qty": 30,
     "expiry_days": 480, "cleared": 1},
]

SHIPMENTS = [
    {"id": "TR-88213", "sku_id": "NB-X1C-14", "warehouse_id": "WH-SH",
     "batch_id": "B-SH-X1C-03", "qty": 80, "eta_days": 2, "carrier": "德邦"},
    {"id": "TR-88220", "sku_id": "NB-T14-G5", "warehouse_id": "WH-SH",
     "batch_id": "B-SH-T14-01", "qty": 60, "eta_days": 3, "carrier": "顺丰"},
]

SUBSTITUTES = [
    {"from_sku": "NB-X1C-14", "to_sku": "NB-T14-G5", "restriction": "仅限非大客户订单"},
]

DOC_RULES = {
    "SOP-EXPIRY": ["R1"],
    "SOP-BONDED": ["R2", "R3"],
    "SOP-ATP-2024": [],
    "contract-C-2024-017": [],
}
OVERRIDES = {"contract-C-2024-017": ["R3"]}

_WH = {w["id"]: w for w in WAREHOUSES}
_SKU = {s["id"]: s for s in SKUS}
_BATCH = {b["id"]: b for b in BATCHES}


def warehouses() -> list[dict]:
    return [dict(w) for w in WAREHOUSES]


def sku(sku_id: str) -> dict | None:
    item = _SKU.get(sku_id)
    return dict(item) if item else None


def inventory_rows(sku_id: str) -> list[dict]:
    rows = []
    for item in INVENTORY:
        if item["sku_id"] != sku_id:
            continue
        wh = _WH.get(item["warehouse_id"], {})
        row = dict(item)
        row["warehouse_name"] = wh.get("name", item["warehouse_id"])
        row["warehouse_type"] = wh.get("type", "finished_goods")
        row["city"] = wh.get("city")
        rows.append(row)
    return sorted(rows, key=lambda r: r["warehouse_id"])


def batches(sku_id: str, warehouse_id: str | None = None) -> list[dict]:
    items = [dict(b) for b in BATCHES if b["sku_id"] == sku_id
             and (warehouse_id is None or b["warehouse_id"] == warehouse_id)]
    return sorted(items, key=lambda b: b["expiry_days"])


def shipments(sku_id: str) -> list[dict]:
    out = []
    for item in SHIPMENTS:
        if item["sku_id"] != sku_id:
            continue
        row = dict(item)
        batch = _BATCH.get(item["batch_id"], {})
        row["expiry_days"] = batch.get("expiry_days")
        row["cleared"] = batch.get("cleared")
        out.append(row)
    return sorted(out, key=lambda r: r["eta_days"])


def substitutes(sku_id: str) -> list[dict]:
    out = []
    for item in SUBSTITUTES:
        if item["from_sku"] != sku_id:
            continue
        row = dict(item)
        target = _SKU.get(item["to_sku"], {})
        row["to_name"] = target.get("name", item["to_sku"])
        row["category"] = target.get("category")
        out.append(row)
    return out
