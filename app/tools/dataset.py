"""本地数据层（唯一数据源）。

两段式：
1. 手工锚点（ANCHOR_*）：演示脚本依赖的固定事实，数字不能动——
   NB-X1C-14 在上海/合肥/香港三行、TR-88213 的 80 台 25 天效期、替代链等。
2. 批量生成（_generate）：把体量撑起来，让「把数据全塞进 prompt」不可行，
   也让 4-5 跳的图查询有足够数据。

生成用 crc32 做确定性伪随机，同一台机器重复生成结果一致。
"""

from __future__ import annotations

import zlib

WAREHOUSES = [
    {"id": "WH-SH", "name": "上海成品仓", "type": "finished_goods", "city": "上海"},
    {"id": "WH-HF", "name": "合肥成品仓", "type": "finished_goods", "city": "合肥"},
    {"id": "WH-KS", "name": "昆山成品仓", "type": "finished_goods", "city": "昆山"},
    {"id": "WH-CD", "name": "成都成品仓", "type": "finished_goods", "city": "成都"},
    {"id": "WH-SZ", "name": "深圳备件仓", "type": "service_parts", "city": "深圳"},
    {"id": "WH-HK", "name": "香港保税仓", "type": "bonded", "city": "香港"},
]

# ---------------------------------------------------------------- 客户 / 供应商

CUSTOMERS = [
    {"id": "C-2024-017", "name": "华东区某渠道商", "tier": "大客户", "region": "华东"},
    {"id": "C-2024-019", "name": "华南区某集成商", "tier": "普通", "region": "华南"},
    {"id": "C-2024-032", "name": "华北区某分销商", "tier": "大客户", "region": "华北"},
    {"id": "C-2024-045", "name": "西南区某渠道商", "tier": "普通", "region": "西南"},
    {"id": "C-2024-052", "name": "华东区售后服务商", "tier": "普通", "region": "华东"},
    {"id": "C-2024-061", "name": "华中区某大客户", "tier": "大客户", "region": "华中"},
    {"id": "C-2024-070", "name": "东北区某渠道商", "tier": "普通", "region": "东北"},
] + [
    {"id": f"C-2024-{100 + i:03d}", "name": f"区域渠道商 {i}", "tier": "普通" if i % 3 else "大客户",
     "region": ["华东", "华南", "华北", "西南", "华中"][i % 5]}
    for i in range(13)
]

SUPPLIERS = [
    {"id": "S-2201", "name": "仁宝昆山", "region": "华东", "tier": "战略"},
    {"id": "S-2202", "name": "广达上海", "region": "华东", "tier": "战略"},
    {"id": "S-2203", "name": "联宝合肥", "region": "华东", "tier": "核心"},
    {"id": "S-2204", "name": "纬创成都", "region": "西南", "tier": "核心"},
    {"id": "S-2205", "name": "和硕苏州", "region": "华东", "tier": "一般"},
    {"id": "S-2206", "name": "英业达重庆", "region": "西南", "tier": "一般"},
    {"id": "S-2207", "name": "光宝科技", "region": "华南", "tier": "核心"},
    {"id": "S-2208", "name": "群光电子", "region": "华南", "tier": "一般"},
]

# ---------------------------------------------------------------- SKU

ANCHOR_SKUS = [
    ("NB-X1C-14", "ThinkPad X1 Carbon 14", "笔记本"),
    ("NB-T14-G5", "ThinkPad T14 Gen5", "笔记本"),
    ("NB-T14-G4", "ThinkPad T14 Gen4", "笔记本"),
    ("NB-X1C-13", "ThinkPad X1 Carbon 13", "笔记本"),
    ("NB-P14S-G2", "ThinkPad P14s Gen2", "笔记本"),
    ("NB-E14-G6", "ThinkPad E14 Gen6", "笔记本"),
    ("SP-MB-14", "14 寸主板备件", "备件"),
    ("SP-KB-14", "14 寸键盘备件", "备件"),
    ("SP-KB-15", "15 寸键盘备件", "备件"),
    ("SP-BAT-T14", "T14 电池备件", "备件"),
    ("SP-FAN-14", "14 寸风扇备件", "备件"),
    ("AC-DOCK-40", "40 系列扩展坞", "配件"),
    ("MN-27-4K", "27 寸 4K 显示器", "配件"),
]

SERIES = [
    ("NB-X1C", "ThinkPad X1 Carbon", "笔记本"),
    ("NB-T14", "ThinkPad T14", "笔记本"),
    ("NB-P14S", "ThinkPad P14s", "笔记本"),
    ("NB-E14", "ThinkPad E14", "笔记本"),
    ("SP-MB", "主板备件", "备件"),
    ("SP-KB", "键盘备件", "备件"),
    ("SP-BAT", "电池备件", "备件"),
    ("SP-FAN", "风扇备件", "备件"),
    ("AC-DOCK", "扩展坞", "配件"),
    ("MN-27", "显示器", "配件"),
]
SUFFIXES = ["11", "12", "13", "21", "22", "G3", "G4", "G6", "G7", "4K", "2K"]


def _h(*parts: str) -> int:
    return zlib.crc32("|".join(parts).encode("utf-8"))


def _skus() -> list[dict]:
    out = [{"id": i, "name": n, "category": c} for i, n, c in ANCHOR_SKUS]
    seen = {s["id"] for s in out}
    for prefix, label, cat in SERIES:
        for suf in SUFFIXES:
            sid = f"{prefix}-{suf}"
            if sid in seen:
                continue
            seen.add(sid)
            out.append({"id": sid, "name": f"{label} {suf}", "category": cat})
    return out


SKUS = _skus()

# ---------------------------------------------------------------- 锚点（不要改）

ANCHOR_INVENTORY = [
    ("NB-X1C-14", "WH-SH", 500, 120, 0, 0, 40, 0),
    ("NB-X1C-14", "WH-HF", 120, 0, 0, 30, 0, 0),
    ("NB-X1C-14", "WH-HK", 60, 0, 0, 0, 0, 0),
]

EXTRA_ANCHOR_INVENTORY = [
    ("NB-T14-G5", "WH-SH", 200, 60, 0, 10, 0, 20),
    ("NB-T14-G5", "WH-HF", 40, 0, 0, 0, 0, 0),
    ("NB-T14-G5", "WH-KS", 150, 30, 0, 0, 0, 10),
    ("NB-T14-G5", "WH-CD", 80, 0, 0, 20, 0, 0),
    ("NB-T14-G4", "WH-SH", 90, 10, 0, 0, 0, 0),
    ("NB-T14-G4", "WH-KS", 120, 0, 5, 0, 0, 0),
    ("NB-X1C-13", "WH-HK", 40, 0, 0, 0, 0, 0),
    ("NB-X1C-13", "WH-SH", 140, 40, 0, 0, 0, 10),
    ("NB-P14S-G2", "WH-SH", 220, 50, 0, 0, 30, 0),
    ("NB-E14-G6", "WH-SH", 300, 100, 0, 0, 0, 0),
    ("NB-E14-G6", "WH-HF", 180, 0, 0, 0, 0, 20),
    ("SP-MB-14", "WH-SZ", 300, 80, 20, 0, 0, 0),
    ("SP-MB-14", "WH-SH", 30, 0, 0, 0, 0, 0),
    ("SP-KB-14", "WH-SZ", 400, 60, 0, 0, 0, 0),
    ("SP-BAT-T14", "WH-SZ", 250, 0, 0, 15, 0, 0),
    ("SP-FAN-14", "WH-SZ", 120, 20, 0, 0, 0, 0),
    ("AC-DOCK-40", "WH-SH", 600, 200, 0, 0, 0, 0),
    ("MN-27-4K", "WH-SH", 90, 10, 0, 0, 0, 0),
]

ANCHOR_BATCHES = [
    ("B-SH-X1C-01", "NB-X1C-14", "WH-SH", 300, 200, 1, "PO-2025-0001"),
    ("B-SH-X1C-02", "NB-X1C-14", "WH-SH", 200, 180, 1, "PO-2025-0001"),
    ("B-SH-X1C-03", "NB-X1C-14", "WH-SH", 80, 25, 1, "PO-2025-0002"),
    ("B-HF-X1C-01", "NB-X1C-14", "WH-HF", 120, 150, 1, "PO-2025-0003"),
    ("B-HK-X1C-01", "NB-X1C-14", "WH-HK", 60, 240, 0, "PO-2025-0004"),
    ("B-SH-T14-01", "NB-T14-G5", "WH-SH", 200, 160, 1, "PO-2025-0005"),
    ("B-HF-T14-01", "NB-T14-G5", "WH-HF", 40, 210, 1, "PO-2025-0006"),
    ("B-SH-T14G4-01", "NB-T14-G4", "WH-SH", 90, 120, 1, "PO-2025-0007"),
    ("B-X13-HK-EXP", "NB-X1C-13", "WH-HK", 40, 260, 0, "PO-2025-0008"),
    ("B-SZ-MB-01", "SP-MB-14", "WH-SZ", 300, 500, 1, "PO-2025-0009"),
    ("B-SH-MB-01", "SP-MB-14", "WH-SH", 30, 480, 1, "PO-2025-0010"),
]


def _generate():
    inventory: list[dict] = []
    batches: list[dict] = []

    def add_cell(sku, wh, on_hand, alloc, res, hold, arrived, safety, expiries, cleared, po):
        inventory.append({"sku_id": sku, "warehouse_id": wh, "on_hand": on_hand,
                          "allocated": alloc, "reserved": res, "hold_qty": hold,
                          "in_transit_arrived": arrived, "safety_stock": safety})
        n = max(1, len(expiries))
        base = on_hand // n
        for i in range(n):
            qty = base if i < n - 1 else on_hand - base * (n - 1)
            if qty <= 0:
                continue
            batches.append({"id": f"B-{wh[3:]}-{sku}-{i + 1:02d}", "sku_id": sku,
                            "warehouse_id": wh, "qty": qty,
                            "expiry_days": expiries[i] if i < len(expiries) else 300,
                            "cleared": cleared, "po_id": po})

    for row in ANCHOR_INVENTORY + EXTRA_ANCHOR_INVENTORY:
        sku, wh, oh, alloc, res, hold, arr, safe = row
        inventory.append({"sku_id": sku, "warehouse_id": wh, "on_hand": oh,
                          "allocated": alloc, "reserved": res, "hold_qty": hold,
                          "in_transit_arrived": arr, "safety_stock": safe})
    for bid, sku, wh, qty, exp, cleared, po in ANCHOR_BATCHES:
        batches.append({"id": bid, "sku_id": sku, "warehouse_id": wh, "qty": qty,
                        "expiry_days": exp, "cleared": cleared, "po_id": po})

    anchored = {(i["sku_id"], i["warehouse_id"]) for i in inventory}
    batched_cells = {(b["sku_id"], b["warehouse_id"]) for b in batches}

    # 锚点里没有批次的格子，按在库量补批次（否则自检会报「有在库没批次」）
    for inv in list(inventory):
        key = (inv["sku_id"], inv["warehouse_id"])
        if key in batched_cells or inv["on_hand"] <= 0:
            continue
        h = _h(*key)
        n = 1 + h % 2
        expiries = sorted(5 + (h >> 3) % 380 for _ in range(n))
        add_cell(inv["sku_id"], inv["warehouse_id"], inv["on_hand"], inv["allocated"],
                 inv["reserved"], inv["hold_qty"], inv["in_transit_arrived"],
                 inv["safety_stock"], expiries,
                 0 if inv["warehouse_id"] == "WH-HK" and h % 3 == 0 else 1,
                 f"PO-2025-{h % 80 + 1:04d}")
        inventory.pop()  # add_cell 会重复追加库存行，这里只需要它生成批次

    # 其余 SKU 生成 3-4 个仓
    for sku in SKUS:
        sid = sku["id"]
        if sid.startswith("SP-"):
            pool = ["WH-SZ", "WH-SH", "WH-HF"]
        elif sid.startswith("AC-") or sid.startswith("MN-"):
            pool = ["WH-SH", "WH-KS", "WH-CD"]
        else:
            pool = ["WH-SH", "WH-HF", "WH-KS", "WH-CD", "WH-HK"]
        pick = [w for w in pool if (sid, w) not in anchored][: 3 + _h(sid, "n") % 2]
        for wh in pick:
            h = _h(sid, wh)
            on_hand = 30 + h % 470
            add_cell(sid, wh, on_hand, int(on_hand * (h % 30) / 100),
                     int(on_hand * (h % 7) / 100), int(on_hand * (h % 5) / 100),
                     (h % 60) if h % 4 == 0 else 0, int(on_hand * (h % 12) / 100),
                     sorted(5 + (h >> 3) % 400 for _ in range(1 + h % 3)),
                     0 if (wh == "WH-HK" and h % 3 == 0) else 1,
                     f"PO-2025-{h % 80 + 1:04d}")

    # 采购单
    pos: list[dict] = []
    for i in range(80):
        h = _h("PO", str(i))
        pos.append({"id": f"PO-2025-{i + 1:04d}",
                    "supplier_id": SUPPLIERS[h % len(SUPPLIERS)]["id"],
                    "sku_id": SKUS[h % len(SKUS)]["id"], "qty": 100 + h % 900,
                    "eta_days": 1 + h % 10,
                    "status": "closed" if h % 5 == 0 else "open"})

    # 在途
    shipments: list[dict] = [
        {"id": "TR-88213", "sku_id": "NB-X1C-14", "warehouse_id": "WH-SH",
         "batch_id": "B-SH-X1C-03", "qty": 80, "eta_days": 2, "carrier": "德邦"},
        {"id": "TR-88220", "sku_id": "NB-T14-G5", "warehouse_id": "WH-SH",
         "batch_id": "B-SH-T14-01", "qty": 40, "eta_days": 3, "carrier": "顺丰"},
    ]
    for i, b in enumerate(batches):
        if i % 9:
            continue
        if b["id"] in ("B-SH-X1C-03", "B-SH-T14-01"):
            continue
        h = _h("TR", b["id"])
        shipments.append({"id": f"TR-{90000 + i}", "sku_id": b["sku_id"],
                          "warehouse_id": b["warehouse_id"], "batch_id": b["id"],
                          "qty": max(1, b["qty"] // 4), "eta_days": 1 + h % 7,
                          "carrier": ["德邦", "顺丰", "中通", "中港运输"][h % 4]})

    # 订单
    orders: list[dict] = [
        {"id": "ORD-2024-0117", "customer": "C-2024-017", "sku_id": "NB-X1C-14",
         "qty": 500, "due_days": 7, "type": "new_order"},
        {"id": "ORD-2024-0118", "customer": "C-2024-032", "sku_id": "NB-T14-G5",
         "qty": 300, "due_days": 10, "type": "new_order"},
        {"id": "ORD-2024-0121", "customer": "C-2024-017", "sku_id": "NB-T14-G5",
         "qty": 120, "due_days": 14, "type": "new_order"},
        {"id": "ORD-2024-0125", "customer": "C-2024-045", "sku_id": "NB-X1C-13",
         "qty": 80, "due_days": 12, "type": "new_order"},
        {"id": "ORD-2024-0130", "customer": "C-2024-052", "sku_id": "SP-MB-14",
         "qty": 40, "due_days": 5, "type": "after_sales"},
        {"id": "ORD-2024-0131", "customer": "C-2024-052", "sku_id": "SP-KB-14",
         "qty": 60, "due_days": 6, "type": "after_sales"},
        {"id": "ORD-2024-0136", "customer": "C-2024-061", "sku_id": "NB-E14-G6",
         "qty": 400, "due_days": 20, "type": "new_order"},
        {"id": "ORD-2024-0140", "customer": "C-2024-019", "sku_id": "NB-P14S-G2",
         "qty": 150, "due_days": 9, "type": "new_order"},
        {"id": "ORD-2024-0142", "customer": "C-2024-032", "sku_id": "AC-DOCK-40",
         "qty": 500, "due_days": 15, "type": "new_order"},
        {"id": "ORD-2024-0145", "customer": "C-2024-070", "sku_id": "NB-T14-G4",
         "qty": 200, "due_days": 8, "type": "new_order"},
        {"id": "ORD-2024-0150", "customer": "C-2024-045", "sku_id": "MN-27-4K",
         "qty": 60, "due_days": 11, "type": "new_order"},
        {"id": "ORD-2024-0160", "customer": "C-2024-052", "sku_id": "SP-FAN-14",
         "qty": 50, "due_days": 4, "type": "after_sales"},
        {"id": "ORD-2024-0163", "customer": "C-2024-061", "sku_id": "NB-X1C-13",
         "qty": 120, "due_days": 16, "type": "new_order"},
        {"id": "ORD-2024-0167", "customer": "C-2024-019", "sku_id": "NB-T14-G5",
         "qty": 90, "due_days": 13, "type": "new_order"},
        {"id": "ORD-2024-0170", "customer": "C-2024-070", "sku_id": "SP-BAT-T14",
         "qty": 80, "due_days": 7, "type": "after_sales"},
        {"id": "ORD-2024-0173", "customer": "C-2024-032", "sku_id": "NB-P14S-G2",
         "qty": 60, "due_days": 21, "type": "new_order"},
        {"id": "ORD-2024-0176", "customer": "C-2024-045", "sku_id": "AC-DOCK-40",
         "qty": 300, "due_days": 9, "type": "new_order"},
        {"id": "ORD-2024-0180", "customer": "C-2024-061", "sku_id": "MN-27-4K",
         "qty": 40, "due_days": 19, "type": "new_order"},
        {"id": "ORD-2024-0152", "customer": "C-2024-032", "sku_id": "NB-X1C-14",
         "qty": 200, "due_days": 18, "type": "replenish"},
        {"id": "ORD-2024-0155", "customer": "C-2024-061", "sku_id": "NB-E14-G6",
         "qty": 300, "due_days": 25, "type": "replenish"},
    ]
    cust_ids = [c["id"] for c in CUSTOMERS]
    for i in range(180):
        h = _h("ORD", str(i))
        typ = "after_sales" if h % 7 == 0 else ("replenish" if h % 9 == 0 else "new_order")
        orders.append({"id": f"ORD-2025-{i + 1:04d}",
                       "customer": cust_ids[h % len(cust_ids)],
                       "sku_id": SKUS[(h >> 4) % len(SKUS)]["id"],
                       "qty": 20 + (h >> 8) % 480,
                       "due_days": 1 + (h >> 3) % 30, "type": typ})
    return inventory, batches, shipments, orders, pos


INVENTORY, BATCHES, SHIPMENTS, ORDERS, PURCHASE_ORDERS = _generate()

SUBSTITUTES = [
    {"from_sku": "NB-X1C-14", "to_sku": "NB-T14-G5", "restriction": "仅限非大客户订单"},
    {"from_sku": "NB-T14-G5", "to_sku": "NB-T14-G4", "restriction": "仅限非大客户订单"},
    {"from_sku": "NB-T14-G4", "to_sku": "NB-P14S-G2", "restriction": "仅限同代机型"},
    {"from_sku": "NB-P14S-G2", "to_sku": "NB-E14-G6", "restriction": "仅限售后替换"},
    {"from_sku": "SP-KB-14", "to_sku": "SP-KB-15", "restriction": "仅限售后替换"},
]

DOC_RULES = {
    "SOP-EXPIRY": ["R1"],
    "SOP-BONDED": ["R2", "R3"],
    "SOP-SERVICE": ["R4"],
    "SOP-ATP-2024": [],
    "SOP-TRANSFER": [],
    "contract-C-2024-017": [],
    "contract-C-2024-031": [],
    "contract-C-2024-052": [],
    "mail-qa-hold-0307": [],
    "mail-qa-hold-0211": [],
    "mail-supplier-delay": [],
    "mail-supplier-delay-0918": [],
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


def orders(sku_id: str | None = None) -> list[dict]:
    return [dict(o) for o in ORDERS if sku_id is None or o["sku_id"] == sku_id]
