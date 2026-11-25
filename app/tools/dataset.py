"""本地数据层。

用途有两个：
1. 没有启动 MySQL / Neo4j 时，工具层退到这里，演示照样能跑。
2. 作为灌库脚本的唯一数据来源，避免两份数据不一致。

数据结构与数据库表的字段保持一致，所以上层代码不需要知道自己走的是哪条路。

关于规模：数据是编的，但必须自洽，而且刻意做成"关系足够多、规则钩子足够密"，
这样 RAG 只靠 70 个文档片段很难拼出答案，而本体+图谱+代码能算准。
其中 NB-X1C-14 在上海/合肥/香港三行的数字是演示脚本的基准，不要改。
"""

from __future__ import annotations

WAREHOUSES = [
    {"id": "WH-SH", "name": "上海成品仓", "type": "finished_goods", "city": "上海"},
    {"id": "WH-HF", "name": "合肥成品仓", "type": "finished_goods", "city": "合肥"},
    {"id": "WH-KS", "name": "昆山成品仓", "type": "finished_goods", "city": "昆山"},
    {"id": "WH-CD", "name": "成都成品仓", "type": "finished_goods", "city": "成都"},
    {"id": "WH-SZ", "name": "深圳备件仓", "type": "service_parts", "city": "深圳"},
    {"id": "WH-HK", "name": "香港保税仓", "type": "bonded", "city": "香港"},
]

SKUS = [
    {"id": "NB-X1C-14", "name": "ThinkPad X1 Carbon 14", "category": "笔记本"},
    {"id": "NB-T14-G5", "name": "ThinkPad T14 Gen5", "category": "笔记本"},
    {"id": "NB-T14-G4", "name": "ThinkPad T14 Gen4", "category": "笔记本"},
    {"id": "NB-X1C-13", "name": "ThinkPad X1 Carbon 13", "category": "笔记本"},
    {"id": "NB-P14S-G2", "name": "ThinkPad P14s Gen2", "category": "笔记本"},
    {"id": "NB-E14-G6", "name": "ThinkPad E14 Gen6", "category": "笔记本"},
    {"id": "SP-MB-14", "name": "14 寸主板备件", "category": "备件"},
    {"id": "SP-KB-14", "name": "14 寸键盘备件", "category": "备件"},
    {"id": "SP-BAT-T14", "name": "T14 电池备件", "category": "备件"},
    {"id": "SP-FAN-14", "name": "14 寸风扇备件", "category": "备件"},
    {"id": "SP-KB-15", "name": "15 寸键盘备件", "category": "备件"},
    {"id": "AC-DOCK-40", "name": "40 系列扩展坞", "category": "配件"},
    {"id": "MN-27-4K", "name": "27 寸 4K 显示器", "category": "配件"},
]

# sku, warehouse, on_hand, allocated, reserved, hold_qty, in_transit_arrived, safety_stock
INVENTORY = [
    # ---- NB-X1C-14：演示基准，三行数字不要动 ----
    {"sku_id": "NB-X1C-14", "warehouse_id": "WH-SH", "on_hand": 500, "allocated": 120,
     "reserved": 0, "hold_qty": 0, "in_transit_arrived": 40, "safety_stock": 0},
    {"sku_id": "NB-X1C-14", "warehouse_id": "WH-HF", "on_hand": 120, "allocated": 0,
     "reserved": 0, "hold_qty": 30, "in_transit_arrived": 0, "safety_stock": 0},
    {"sku_id": "NB-X1C-14", "warehouse_id": "WH-HK", "on_hand": 60, "allocated": 0,
     "reserved": 0, "hold_qty": 0, "in_transit_arrived": 0, "safety_stock": 0},
    # ---- 其他笔记本 ----
    {"sku_id": "NB-T14-G5", "warehouse_id": "WH-SH", "on_hand": 200, "allocated": 60,
     "reserved": 0, "hold_qty": 10, "in_transit_arrived": 0, "safety_stock": 20},
    {"sku_id": "NB-T14-G5", "warehouse_id": "WH-HF", "on_hand": 40, "allocated": 0,
     "reserved": 0, "hold_qty": 0, "in_transit_arrived": 0, "safety_stock": 0},
    {"sku_id": "NB-T14-G5", "warehouse_id": "WH-KS", "on_hand": 150, "allocated": 30,
     "reserved": 0, "hold_qty": 0, "in_transit_arrived": 0, "safety_stock": 10},
    {"sku_id": "NB-T14-G5", "warehouse_id": "WH-CD", "on_hand": 80, "allocated": 0,
     "reserved": 0, "hold_qty": 20, "in_transit_arrived": 0, "safety_stock": 0},
    {"sku_id": "NB-T14-G4", "warehouse_id": "WH-SH", "on_hand": 90, "allocated": 10,
     "reserved": 0, "hold_qty": 0, "in_transit_arrived": 0, "safety_stock": 0},
    {"sku_id": "NB-T14-G4", "warehouse_id": "WH-HF", "on_hand": 60, "allocated": 0,
     "reserved": 0, "hold_qty": 0, "in_transit_arrived": 0, "safety_stock": 0},
    {"sku_id": "NB-T14-G4", "warehouse_id": "WH-KS", "on_hand": 120, "allocated": 0,
     "reserved": 5, "hold_qty": 0, "in_transit_arrived": 0, "safety_stock": 0},
    {"sku_id": "NB-X1C-13", "warehouse_id": "WH-SH", "on_hand": 140, "allocated": 40,
     "reserved": 0, "hold_qty": 0, "in_transit_arrived": 0, "safety_stock": 10},
    {"sku_id": "NB-X1C-13", "warehouse_id": "WH-KS", "on_hand": 60, "allocated": 0,
     "reserved": 0, "hold_qty": 10, "in_transit_arrived": 0, "safety_stock": 0},
    {"sku_id": "NB-X1C-13", "warehouse_id": "WH-HK", "on_hand": 40, "allocated": 0,
     "reserved": 0, "hold_qty": 0, "in_transit_arrived": 0, "safety_stock": 0},
    {"sku_id": "NB-P14S-G2", "warehouse_id": "WH-SH", "on_hand": 220, "allocated": 50,
     "reserved": 0, "hold_qty": 0, "in_transit_arrived": 30, "safety_stock": 0},
    {"sku_id": "NB-P14S-G2", "warehouse_id": "WH-CD", "on_hand": 60, "allocated": 0,
     "reserved": 0, "hold_qty": 0, "in_transit_arrived": 0, "safety_stock": 0},
    {"sku_id": "NB-E14-G6", "warehouse_id": "WH-SH", "on_hand": 300, "allocated": 100,
     "reserved": 0, "hold_qty": 0, "in_transit_arrived": 0, "safety_stock": 0},
    {"sku_id": "NB-E14-G6", "warehouse_id": "WH-HF", "on_hand": 180, "allocated": 0,
     "reserved": 0, "hold_qty": 0, "in_transit_arrived": 0, "safety_stock": 20},
    {"sku_id": "NB-E14-G6", "warehouse_id": "WH-CD", "on_hand": 90, "allocated": 0,
     "reserved": 0, "hold_qty": 0, "in_transit_arrived": 0, "safety_stock": 0},
    # ---- 备件（备件仓只服务售后，R4）----
    {"sku_id": "SP-MB-14", "warehouse_id": "WH-SZ", "on_hand": 300, "allocated": 80,
     "reserved": 20, "hold_qty": 0, "in_transit_arrived": 0, "safety_stock": 0},
    {"sku_id": "SP-MB-14", "warehouse_id": "WH-SH", "on_hand": 30, "allocated": 0,
     "reserved": 0, "hold_qty": 0, "in_transit_arrived": 0, "safety_stock": 0},
    {"sku_id": "SP-KB-14", "warehouse_id": "WH-SZ", "on_hand": 400, "allocated": 60,
     "reserved": 0, "hold_qty": 0, "in_transit_arrived": 0, "safety_stock": 0},
    {"sku_id": "SP-KB-14", "warehouse_id": "WH-SH", "on_hand": 20, "allocated": 0,
     "reserved": 0, "hold_qty": 0, "in_transit_arrived": 0, "safety_stock": 0},
    {"sku_id": "SP-BAT-T14", "warehouse_id": "WH-SZ", "on_hand": 250, "allocated": 0,
     "reserved": 0, "hold_qty": 15, "in_transit_arrived": 0, "safety_stock": 0},
    {"sku_id": "SP-FAN-14", "warehouse_id": "WH-SZ", "on_hand": 120, "allocated": 20,
     "reserved": 0, "hold_qty": 0, "in_transit_arrived": 0, "safety_stock": 0},
    {"sku_id": "SP-FAN-14", "warehouse_id": "WH-HF", "on_hand": 40, "allocated": 0,
     "reserved": 0, "hold_qty": 0, "in_transit_arrived": 0, "safety_stock": 0},
    {"sku_id": "SP-KB-15", "warehouse_id": "WH-SZ", "on_hand": 80, "allocated": 0,
     "reserved": 0, "hold_qty": 0, "in_transit_arrived": 0, "safety_stock": 0},
    # ---- 配件 ----
    {"sku_id": "AC-DOCK-40", "warehouse_id": "WH-SH", "on_hand": 600, "allocated": 200,
     "reserved": 0, "hold_qty": 0, "in_transit_arrived": 0, "safety_stock": 0},
    {"sku_id": "AC-DOCK-40", "warehouse_id": "WH-KS", "on_hand": 150, "allocated": 0,
     "reserved": 0, "hold_qty": 0, "in_transit_arrived": 0, "safety_stock": 0},
    {"sku_id": "MN-27-4K", "warehouse_id": "WH-SH", "on_hand": 90, "allocated": 10,
     "reserved": 0, "hold_qty": 0, "in_transit_arrived": 0, "safety_stock": 0},
    {"sku_id": "MN-27-4K", "warehouse_id": "WH-CD", "on_hand": 40, "allocated": 0,
     "reserved": 0, "hold_qty": 0, "in_transit_arrived": 0, "safety_stock": 0},
]

# id, sku, warehouse, qty, expiry_days, cleared
# 规则钩子：B-SH-X1C-03 / B-KS-T14G4-01 / B-X13-HK-01 分别是 R1、R1、R2 的例子
BATCHES = [
    # X1C-14（基准数据的批次，不要动）
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
    # T14-G5
    {"id": "B-SH-T14-01", "sku_id": "NB-T14-G5", "warehouse_id": "WH-SH", "qty": 200,
     "expiry_days": 160, "cleared": 1},
    {"id": "B-HF-T14-01", "sku_id": "NB-T14-G5", "warehouse_id": "WH-HF", "qty": 40,
     "expiry_days": 210, "cleared": 1},
    {"id": "B-KS-T14-01", "sku_id": "NB-T14-G5", "warehouse_id": "WH-KS", "qty": 150,
     "expiry_days": 140, "cleared": 1},
    {"id": "B-CD-T14-01", "sku_id": "NB-T14-G5", "warehouse_id": "WH-CD", "qty": 80,
     "expiry_days": 90, "cleared": 1},
    # T14-G4
    {"id": "B-SH-T14G4-01", "sku_id": "NB-T14-G4", "warehouse_id": "WH-SH", "qty": 90,
     "expiry_days": 120, "cleared": 1},
    {"id": "B-HF-T14G4-01", "sku_id": "NB-T14-G4", "warehouse_id": "WH-HF", "qty": 60,
     "expiry_days": 200, "cleared": 1},
    {"id": "B-KS-T14G4-01", "sku_id": "NB-T14-G4", "warehouse_id": "WH-KS", "qty": 120,
     "expiry_days": 26, "cleared": 1},
    # X1C-13
    {"id": "B-SH-X13-01", "sku_id": "NB-X1C-13", "warehouse_id": "WH-SH", "qty": 140,
     "expiry_days": 170, "cleared": 1},
    {"id": "B-KS-X13-01", "sku_id": "NB-X1C-13", "warehouse_id": "WH-KS", "qty": 60,
     "expiry_days": 240, "cleared": 1},
    {"id": "B-X13-HK-01", "sku_id": "NB-X1C-13", "warehouse_id": "WH-HK", "qty": 40,
     "expiry_days": 260, "cleared": 0},
    # P14s / E14
    {"id": "B-SH-P14S-01", "sku_id": "NB-P14S-G2", "warehouse_id": "WH-SH", "qty": 220,
     "expiry_days": 190, "cleared": 1},
    {"id": "B-CD-P14S-01", "sku_id": "NB-P14S-G2", "warehouse_id": "WH-CD", "qty": 60,
     "expiry_days": 160, "cleared": 1},
    {"id": "B-SH-E14-01", "sku_id": "NB-E14-G6", "warehouse_id": "WH-SH", "qty": 300,
     "expiry_days": 300, "cleared": 1},
    {"id": "B-HF-E14-01", "sku_id": "NB-E14-G6", "warehouse_id": "WH-HF", "qty": 180,
     "expiry_days": 220, "cleared": 1},
    {"id": "B-CD-E14-01", "sku_id": "NB-E14-G6", "warehouse_id": "WH-CD", "qty": 90,
     "expiry_days": 180, "cleared": 1},
    # 备件
    {"id": "B-SZ-MB-01", "sku_id": "SP-MB-14", "warehouse_id": "WH-SZ", "qty": 300,
     "expiry_days": 500, "cleared": 1},
    {"id": "B-SH-MB-01", "sku_id": "SP-MB-14", "warehouse_id": "WH-SH", "qty": 30,
     "expiry_days": 480, "cleared": 1},
    {"id": "B-SZ-KB-01", "sku_id": "SP-KB-14", "warehouse_id": "WH-SZ", "qty": 400,
     "expiry_days": 600, "cleared": 1},
    {"id": "B-SH-KB-01", "sku_id": "SP-KB-14", "warehouse_id": "WH-SH", "qty": 20,
     "expiry_days": 550, "cleared": 1},
    {"id": "B-SZ-BAT-01", "sku_id": "SP-BAT-T14", "warehouse_id": "WH-SZ", "qty": 250,
     "expiry_days": 400, "cleared": 1},
    {"id": "B-SZ-FAN-01", "sku_id": "SP-FAN-14", "warehouse_id": "WH-SZ", "qty": 120,
     "expiry_days": 450, "cleared": 1},
    {"id": "B-HF-FAN-01", "sku_id": "SP-FAN-14", "warehouse_id": "WH-HF", "qty": 40,
     "expiry_days": 380, "cleared": 1},
    {"id": "B-SZ-KB15-01", "sku_id": "SP-KB-15", "warehouse_id": "WH-SZ", "qty": 80,
     "expiry_days": 520, "cleared": 1},
    # 配件
    {"id": "B-SH-DOCK-01", "sku_id": "AC-DOCK-40", "warehouse_id": "WH-SH", "qty": 600,
     "expiry_days": 700, "cleared": 1},
    {"id": "B-KS-DOCK-01", "sku_id": "AC-DOCK-40", "warehouse_id": "WH-KS", "qty": 150,
     "expiry_days": 650, "cleared": 1},
    {"id": "B-SH-MN-01", "sku_id": "MN-27-4K", "warehouse_id": "WH-SH", "qty": 90,
     "expiry_days": 800, "cleared": 1},
    {"id": "B-CD-MN-01", "sku_id": "MN-27-4K", "warehouse_id": "WH-CD", "qty": 40,
     "expiry_days": 760, "cleared": 1},
    # 在途批次（还没到仓，单独建批次方便演示"效期在到仓时判定"）
    {"id": "B-KS-T14G4-EXP", "sku_id": "NB-T14-G4", "warehouse_id": "WH-KS", "qty": 80,
     "expiry_days": 22, "cleared": 1},
    {"id": "B-X13-HK-EXP", "sku_id": "NB-X1C-13", "warehouse_id": "WH-HK", "qty": 40,
     "expiry_days": 260, "cleared": 0},
    {"id": "B-HF-E14-EXP", "sku_id": "NB-E14-G6", "warehouse_id": "WH-HF", "qty": 120,
     "expiry_days": 200, "cleared": 1},
    {"id": "B-SH-P14S-EXP", "sku_id": "NB-P14S-G2", "warehouse_id": "WH-SH", "qty": 40,
     "expiry_days": 180, "cleared": 1},
    {"id": "B-SZ-BAT-EXP", "sku_id": "SP-BAT-T14", "warehouse_id": "WH-SZ", "qty": 100,
     "expiry_days": 400, "cleared": 1},
    {"id": "B-SZ-KB-EXP", "sku_id": "SP-KB-14", "warehouse_id": "WH-SZ", "qty": 150,
     "expiry_days": 500, "cleared": 1},
    {"id": "B-SH-DOCK-EXP", "sku_id": "AC-DOCK-40", "warehouse_id": "WH-SH", "qty": 200,
     "expiry_days": 700, "cleared": 1},
    {"id": "B-CD-E14-EXP", "sku_id": "NB-E14-G6", "warehouse_id": "WH-CD", "qty": 60,
     "expiry_days": 150, "cleared": 1},
]

# id, sku, warehouse, batch, qty, eta_days, carrier
SHIPMENTS = [
    {"id": "TR-88213", "sku_id": "NB-X1C-14", "warehouse_id": "WH-SH",
     "batch_id": "B-SH-X1C-03", "qty": 80, "eta_days": 2, "carrier": "德邦"},
    {"id": "TR-88220", "sku_id": "NB-T14-G5", "warehouse_id": "WH-SH",
     "batch_id": "B-SH-T14-01", "qty": 60, "eta_days": 3, "carrier": "顺丰"},
    {"id": "TR-88231", "sku_id": "NB-X1C-13", "warehouse_id": "WH-HK",
     "batch_id": "B-X13-HK-EXP", "qty": 40, "eta_days": 4, "carrier": "中港运输"},
    {"id": "TR-88245", "sku_id": "NB-T14-G4", "warehouse_id": "WH-KS",
     "batch_id": "B-KS-T14G4-EXP", "qty": 80, "eta_days": 2, "carrier": "德邦"},
    {"id": "TR-88251", "sku_id": "NB-E14-G6", "warehouse_id": "WH-HF",
     "batch_id": "B-HF-E14-EXP", "qty": 120, "eta_days": 5, "carrier": "顺丰"},
    {"id": "TR-88258", "sku_id": "NB-P14S-G2", "warehouse_id": "WH-SH",
     "batch_id": "B-SH-P14S-EXP", "qty": 40, "eta_days": 1, "carrier": "德邦"},
    {"id": "TR-88263", "sku_id": "SP-BAT-T14", "warehouse_id": "WH-SZ",
     "batch_id": "B-SZ-BAT-EXP", "qty": 100, "eta_days": 3, "carrier": "顺丰"},
    {"id": "TR-88270", "sku_id": "SP-KB-14", "warehouse_id": "WH-SZ",
     "batch_id": "B-SZ-KB-EXP", "qty": 150, "eta_days": 6, "carrier": "德邦"},
    {"id": "TR-88275", "sku_id": "AC-DOCK-40", "warehouse_id": "WH-SH",
     "batch_id": "B-SH-DOCK-EXP", "qty": 200, "eta_days": 2, "carrier": "顺丰"},
    {"id": "TR-88280", "sku_id": "NB-E14-G6", "warehouse_id": "WH-CD",
     "batch_id": "B-CD-E14-EXP", "qty": 60, "eta_days": 4, "carrier": "德邦"},
]

# from_sku, to_sku, restriction
# X1C-14 → T14-G5 → T14-G4 → P14S-G2 是一条三跳链，用来看图库的变长路径
SUBSTITUTES = [
    {"from_sku": "NB-X1C-14", "to_sku": "NB-T14-G5", "restriction": "仅限非大客户订单"},
    {"from_sku": "NB-T14-G5", "to_sku": "NB-T14-G4", "restriction": "仅限非大客户订单"},
    {"from_sku": "NB-T14-G4", "to_sku": "NB-P14S-G2", "restriction": "仅限同代机型"},
    {"from_sku": "NB-P14S-G2", "to_sku": "NB-E14-G6", "restriction": "仅限售后替换"},
    {"from_sku": "SP-KB-14", "to_sku": "SP-KB-15", "restriction": "仅限售后替换"},
]

# id, customer, sku, qty, due_days, type
ORDERS = [
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
    {"id": "ORD-2024-0152", "customer": "内部分货", "sku_id": "NB-X1C-14",
     "qty": 200, "due_days": 18, "type": "replenish"},
    {"id": "ORD-2024-0155", "customer": "内部分货", "sku_id": "NB-E14-G6",
     "qty": 300, "due_days": 25, "type": "replenish"},
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
