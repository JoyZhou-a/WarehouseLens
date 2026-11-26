"""生成虚拟数据并写入 MySQL / Neo4j / Milvus / Redis。

用法：
    python scripts/seed.py                  # 全部
    python scripts/seed.py --only mysql     # 只灌一个
    python scripts/seed.py --only neo4j,milvus

数据是编造的，只用于演示。数字之间保持自洽，Q1 的 420 + 80 就是从这里算出来的。
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import yaml
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")
sys.path.insert(0, str(ROOT))

from app import config  # noqa: E402
from app.tools.embedding import embed as _embed  # noqa: E402

DOC_DIR = ROOT / "data" / "docs"
ONTOLOGY_PATH = ROOT / os.getenv("ONTOLOGY_PATH", "ontology/wms.yaml")


def config_embed_dim(provider: str) -> int:
    return config.EMBED_DIM_BGE if provider == "bge" else config.EMBED_DIM_HASH

# ---------------------------------------------------------------- 虚拟数据

WAREHOUSES = [
    ("WH-SH", "上海成品仓", "finished_goods", "上海"),
    ("WH-HF", "合肥成品仓", "finished_goods", "合肥"),
    ("WH-SZ", "深圳备件仓", "service_parts", "深圳"),
    ("WH-HK", "香港保税仓", "bonded", "香港"),
]

SKUS = [
    ("NB-X1C-14", "ThinkPad X1 Carbon 14", "笔记本"),
    ("NB-T14-G5", "ThinkPad T14 Gen5", "笔记本"),
    ("SP-MB-14", "14 寸主板备件", "备件"),
]

# sku, warehouse, on_hand, allocated, reserved, hold_qty, in_transit_arrived, safety_stock
INVENTORY = [
    ("NB-X1C-14", "WH-SH", 500, 120, 0, 0, 40, 0),    # 可用 420
    ("NB-X1C-14", "WH-HF", 120, 0, 0, 30, 0, 0),      # 可用 90，30 台质检扣留
    ("NB-X1C-14", "WH-HK", 60, 0, 0, 0, 0, 0),        # 可用 60，保税默认不参与承诺
    ("NB-T14-G5", "WH-SH", 200, 60, 0, 10, 0, 20),    # 可用 130，可承诺 110
    ("NB-T14-G5", "WH-HF", 40, 0, 0, 0, 0, 0),        # 可用 40
    ("SP-MB-14", "WH-SZ", 300, 80, 20, 0, 0, 0),      # 可用 200
    ("SP-MB-14", "WH-SH", 30, 0, 0, 0, 0, 0),         # 可用 30
]

# id, sku, warehouse, qty, expiry_days, cleared
BATCHES = [
    ("B-SH-X1C-01", "NB-X1C-14", "WH-SH", 300, 200, 1),
    ("B-SH-X1C-02", "NB-X1C-14", "WH-SH", 200, 180, 1),
    ("B-SH-X1C-03", "NB-X1C-14", "WH-SH", 80, 25, 1),   # 在途批次，到仓即临期
    ("B-HF-X1C-01", "NB-X1C-14", "WH-HF", 120, 150, 1),
    ("B-HK-X1C-01", "NB-X1C-14", "WH-HK", 60, 240, 0),  # 未清关
    ("B-SH-T14-01", "NB-T14-G5", "WH-SH", 200, 160, 1),
    ("B-HF-T14-01", "NB-T14-G5", "WH-HF", 40, 210, 1),
    ("B-SZ-MB-01", "SP-MB-14", "WH-SZ", 300, 500, 1),
    ("B-SH-MB-01", "SP-MB-14", "WH-SH", 30, 480, 1),
]

# id, sku, warehouse, batch, qty, eta_days, carrier
SHIPMENTS = [
    ("TR-88213", "NB-X1C-14", "WH-SH", "B-SH-X1C-03", 80, 2, "德邦"),
    ("TR-88220", "NB-T14-G5", "WH-SH", "B-SH-T14-01", 60, 3, "顺丰"),
]

# from_sku, to_sku, restriction
SUBSTITUTES = [
    ("NB-X1C-14", "NB-T14-G5", "仅限非大客户订单"),
]


# 数据唯一来源是 app/tools/dataset.py，本地降级模式读的就是它。
# 这里覆盖上面的字面量，保证灌库和本地数据永远一致。
sys.path.insert(0, str(ROOT))
from app.tools import dataset as _ds  # noqa: E402

WAREHOUSES = [(w["id"], w["name"], w["type"], w["city"]) for w in _ds.WAREHOUSES]
SKUS = [(s["id"], s["name"], s["category"]) for s in _ds.SKUS]
INVENTORY = [(i["sku_id"], i["warehouse_id"], i["on_hand"], i["allocated"], i["reserved"],
              i["hold_qty"], i["in_transit_arrived"], i["safety_stock"]) for i in _ds.INVENTORY]
BATCHES = [(b["id"], b["sku_id"], b["warehouse_id"], b["qty"], b["expiry_days"], b["cleared"])
           for b in _ds.BATCHES]
SHIPMENTS = [(s["id"], s["sku_id"], s["warehouse_id"], s["batch_id"], s["qty"],
              s["eta_days"], s["carrier"]) for s in _ds.SHIPMENTS]
SUBSTITUTES = [(s["from_sku"], s["to_sku"], s["restriction"]) for s in _ds.SUBSTITUTES]
ORDERS = [(o["id"], o["customer"], o["sku_id"], o["qty"], o["due_days"], o["type"])
          for o in _ds.ORDERS]
CUSTOMERS = [(c["id"], c["name"], c["tier"], c["region"]) for c in _ds.CUSTOMERS]
SUPPLIERS = [(s["id"], s["name"], s["region"], s["tier"]) for s in _ds.SUPPLIERS]
PURCHASE_ORDERS = [(p["id"], p["supplier_id"], p["sku_id"], p["qty"], p["eta_days"], p["status"])
                   for p in _ds.PURCHASE_ORDERS]
BATCH_PO = [(b["id"], b.get("po_id") or "PO-2025-0001") for b in _ds.BATCHES]


def ontology() -> dict:
    with open(ONTOLOGY_PATH, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


# ---------------------------------------------------------------- MySQL

def seed_mysql(onto: dict) -> None:
    import pymysql

    conn = pymysql.connect(
        host=os.getenv("MYSQL_HOST", "127.0.0.1"),
        port=int(os.getenv("MYSQL_PORT", "3306")),
        user=os.getenv("MYSQL_USER", "root"),
        password=os.getenv("MYSQL_PASSWORD", "root123456"),
        database=os.getenv("MYSQL_DATABASE", "wms"),
        charset="utf8mb4",
        autocommit=True,
    )
    with conn.cursor() as cur:
        cur.executemany(
            "REPLACE INTO warehouses(id,name,type,city) VALUES(%s,%s,%s,%s)", WAREHOUSES
        )
        cur.executemany("REPLACE INTO skus(id,name,category) VALUES(%s,%s,%s)", SKUS)
        cur.executemany(
            "REPLACE INTO inventory(sku_id,warehouse_id,on_hand,allocated,reserved,"
            "hold_qty,in_transit_arrived,safety_stock) VALUES(%s,%s,%s,%s,%s,%s,%s,%s)",
            INVENTORY,
        )
        cur.executemany(
            "REPLACE INTO batches(id,sku_id,warehouse_id,qty,expiry_days,cleared) "
            "VALUES(%s,%s,%s,%s,%s,%s)",
            BATCHES,
        )
        cur.executemany(
            "REPLACE INTO shipments(id,sku_id,warehouse_id,batch_id,qty,eta_days,carrier) "
            "VALUES(%s,%s,%s,%s,%s,%s,%s)",
            SHIPMENTS,
        )
        cur.executemany(
            "REPLACE INTO substitutes(from_sku,to_sku,restriction) VALUES(%s,%s,%s)",
            SUBSTITUTES,
        )
        cur.executemany(
            "REPLACE INTO orders(id,customer,sku_id,qty,due_days,type) "
            "VALUES(%s,%s,%s,%s,%s,%s)",
            ORDERS,
        )
        cur.executemany(
            "REPLACE INTO customers(id,name,tier,region) VALUES(%s,%s,%s,%s)", CUSTOMERS
        )
        cur.executemany(
            "REPLACE INTO suppliers(id,name,region,tier) VALUES(%s,%s,%s,%s)", SUPPLIERS
        )
        cur.executemany(
            "REPLACE INTO purchase_orders(id,supplier_id,sku_id,qty,eta_days,status) "
            "VALUES(%s,%s,%s,%s,%s,%s)",
            PURCHASE_ORDERS,
        )
        cur.executemany(
            "REPLACE INTO batch_po(batch_id,po_id) VALUES(%s,%s)", BATCH_PO
        )
    conn.close()
    print(f"[mysql] warehouses={len(WAREHOUSES)} skus={len(SKUS)} "
          f"inventory={len(INVENTORY)} batches={len(BATCHES)} shipments={len(SHIPMENTS)} "
          f"orders={len(ORDERS)} customers={len(CUSTOMERS)} suppliers={len(SUPPLIERS)} "
          f"po={len(PURCHASE_ORDERS)}")


# ---------------------------------------------------------------- Neo4j

def seed_neo4j(onto: dict) -> None:
    from neo4j import GraphDatabase

    driver = GraphDatabase.driver(
        os.getenv("NEO4J_URI", "bolt://127.0.0.1:7687"),
        auth=(os.getenv("NEO4J_USER", "neo4j"), os.getenv("NEO4J_PASSWORD", "neo4j123456")),
    )
    with driver.session() as s:
        s.run("MATCH (n) WHERE n:Warehouse OR n:SKU OR n:Batch OR n:Shipment "
              "OR n:Doc OR n:Rule OR n:Ontology OR n:Order OR n:Customer "
              "OR n:Supplier OR n:PurchaseOrder DETACH DELETE n")

        s.run("CREATE (:Ontology {version: $v, path: $p})",
              v=str(onto.get("version")), p=str(ONTOLOGY_PATH.name))

        for wid, name, wtype, city in WAREHOUSES:
            s.run("CREATE (:Warehouse {id:$id, name:$name, type:$type, city:$city})",
                  id=wid, name=name, type=wtype, city=city)

        for sid, name, cat in SKUS:
            s.run("CREATE (:SKU {id:$id, name:$name, category:$cat})", id=sid, name=name, cat=cat)

        for cid, cname, tier, region in CUSTOMERS:
            s.run("CREATE (:Customer {id:$id, name:$name, tier:$tier, region:$region})",
                  id=cid, name=cname, tier=tier, region=region)

        for sid, sname, region, tier in SUPPLIERS:
            s.run("CREATE (:Supplier {id:$id, name:$name, region:$region, tier:$tier})",
                  id=sid, name=sname, region=region, tier=tier)

        for pid, sup, sku, qty, eta, status in PURCHASE_ORDERS:
            s.run(
                "MATCH (sp:Supplier {id:$sup}), (k:SKU {id:$sku}) "
                "CREATE (po:PurchaseOrder {id:$id, qty:$qty, eta_days:$eta, status:$st}) "
                "MERGE (po)-[:PLACED_WITH]->(sp) MERGE (po)-[:CONTAINS]->(k)",
                id=pid, sup=sup, sku=sku, qty=qty, eta=eta, st=status,
            )
            s.run("MATCH (sp:Supplier {id:$sup}), (k:SKU {id:$sku}) "
                  "MERGE (sp)-[:SUPPLIES]->(k)", sup=sup, sku=sku)

        for bid, sku, wid, qty, exp, cleared in BATCHES:
            s.run(
                "MATCH (wh:Warehouse {id:$wid}) "
                "CREATE (b:Batch {id:$id, sku_id:$sku, qty:$qty, "
                "expiry_days:$exp, cleared:$cleared})-[:STORED_IN]->(wh)",
                id=bid, sku=sku, wid=wid, qty=qty, exp=exp, cleared=cleared,
            )
            s.run("MATCH (b:Batch {id:$b}), (k:SKU {id:$s}) MERGE (k)-[:HAS_BATCH]->(b)",
                  b=bid, s=sku)

        # 批次 → 采购单：供应商链路的关键一跳
        for bid, po in BATCH_PO:
            s.run("MATCH (b:Batch {id:$b}), (po:PurchaseOrder {id:$p}) "
                  "MERGE (b)-[:FROM_PO]->(po)", b=bid, p=po)

        for tid, sku, wid, batch, qty, eta, carrier in SHIPMENTS:
            s.run(
                "MATCH (k:SKU {id:$sku}), (wh:Warehouse {id:$wid}), (b:Batch {id:$batch}) "
                "CREATE (sh:Shipment {id:$id, qty:$qty, eta_days:$eta, carrier:$c}) "
                "MERGE (sh)-[:CARRIES]->(k) MERGE (sh)-[:DELIVERS_TO]->(wh) "
                "MERGE (sh)-[:HAS_BATCH]->(b)",
                id=tid, sku=sku, wid=wid, batch=batch, qty=qty, eta=eta, c=carrier,
            )

        for f, t, restriction in SUBSTITUTES:
            s.run(
                "MATCH (a:SKU {id:$f}), (b:SKU {id:$t}) "
                "CREATE (a)-[:SUBSTITUTE_OF {restriction:$r}]->(b)",
                f=f, t=t, r=restriction,
            )

        for rule in onto.get("rules", []):
            s.run("CREATE (:Rule {id:$id, label:$label, then:$then, enabled:$enabled})",
                  id=rule["id"], label=rule.get("label", ""),
                  then=rule.get("then", ""), enabled=bool(rule.get("enabled", True)))
            s.run("MATCH (o:Ontology), (r:Rule {id:$id}) MERGE (o)-[:HAS_RULE]->(r)", id=rule["id"])

        # 订单 → 物料 → 客户，让 Order / Customer 在库里都能被多跳问到
        for oid, customer, sku, qty, due, otype in ORDERS:
            s.run(
                "MATCH (k:SKU {id:$sku}), (c:Customer {id:$c}) "
                "CREATE (o:Order {id:$id, customer:$c, qty:$qty, due_days:$due, type:$t}) "
                "MERGE (o)-[:ORDERED_FOR]->(k) MERGE (o)-[:TO_CUSTOMER]->(c)",
                id=oid, c=customer, sku=sku, qty=qty, due=due, t=otype,
            )

        # 文档与规则的治理关系，用来演示「哪条规则出自哪份文件」
        # 名单来自 dataset.DOC_RULES，避免文档清单和代码各写一份
        for doc_id, rule_ids in _ds.DOC_RULES.items():
            s.run("CREATE (:Doc {id:$id, title:$t})", id=doc_id, t=doc_id)
            for rid in rule_ids:
                s.run("MATCH (d:Doc {id:$d}), (r:Rule {id:$r}) MERGE (d)-[:GOVERNS]->(r)", d=doc_id, r=rid)
        for doc_id, rule_ids in _ds.OVERRIDES.items():
            for rid in rule_ids:
                s.run("MATCH (d:Doc {id:$d}), (r:Rule {id:$r}) MERGE (d)-[:OVERRIDES]->(r)",
                      d=doc_id, r=rid)

        counts = {}
        for label in ["Warehouse", "SKU", "Batch", "Shipment", "Rule", "Doc", "Order",
                      "Customer", "Supplier", "PurchaseOrder"]:
            counts[label] = s.run(f"MATCH (n:{label}) RETURN count(n) AS c").single()["c"]
    driver.close()
    print(f"[neo4j] {counts}")


# ---------------------------------------------------------------- Milvus

def embed(text: str) -> list[float]:
    """保留旧名字，内部转调 embedding 模块（默认 hash provider）。"""
    return _embed(text, "hash")


def embed_with(provider: str, text: str) -> list[float]:
    return _embed(text, provider)


def load_docs(provider: str = "hash") -> list[dict]:
    rows = []
    for path in sorted(DOC_DIR.glob("*.md")):
        text = path.read_text(encoding="utf-8")
        title = path.stem
        chunks = [c.strip() for c in text.split("\n\n") if len(c.strip()) >= 40]
        for i, chunk in enumerate(chunks):
            rows.append({"doc_id": title, "title": title, "chunk_no": i,
                         "text": chunk, "vector": embed_with(provider, chunk)})
    return rows


def _create_collection(name: str, dim: int):
    from pymilvus import Collection, CollectionSchema, DataType, FieldSchema

    fields = [
        FieldSchema("id", DataType.INT64, is_primary=True, auto_id=True),
        FieldSchema("doc_id", DataType.VARCHAR, max_length=128),
        FieldSchema("title", DataType.VARCHAR, max_length=256),
        FieldSchema("chunk_no", DataType.INT64),
        FieldSchema("text", DataType.VARCHAR, max_length=4096),
        FieldSchema("vector", DataType.FLOAT_VECTOR, dim=dim),
    ]
    return Collection(name, CollectionSchema(fields, description="仓储文档片段"))


def seed_milvus() -> None:
    """两个 provider 各灌一个 collection：hash 打底，bge 语义。

    collection 名字里的后缀必须和维度、编码模型严格对应，否则查询时余弦相似度没有意义。
    """
    from pymilvus import connections, utility

    connections.connect(
        alias="default",
        host=os.getenv("MILVUS_HOST", "127.0.0.1"),
        port=str(os.getenv("MILVUS_PORT", "19530")),
    )

    providers = ["hash"]
    if config.EMBED_API_KEY:
        providers.append("bge")
    else:
        print("[milvus] 没有 EMBED_API_KEY，只灌 hash collection")

    for provider in providers:
        name = config.COLLECTION_BY_PROVIDER[provider]
        dim = config_embed_dim(provider)
        if utility.has_collection(name):
            utility.drop_collection(name)

        col = _create_collection(name, dim)
        rows = load_docs(provider)
        col.insert([{"doc_id": r["doc_id"], "title": r["title"], "chunk_no": r["chunk_no"],
                     "text": r["text"], "vector": r["vector"]} for r in rows])
        col.create_index("vector", {"index_type": "IVF_FLAT", "metric_type": "COSINE",
                                    "params": {"nlist": 64}})
        col.load()
        print(f"[milvus] collection={name} provider={provider} chunks={len(rows)} dim={dim}")

    # 早期版本只灌过 wms_docs（512 维），留着会让人误以为在用
    if utility.has_collection("wms_docs"):
        utility.drop_collection("wms_docs")
        print("[milvus] 已删除旧的 wms_docs（改用 wms_docs_hash / wms_docs_bge）")


# ---------------------------------------------------------------- Redis

def seed_redis() -> None:
    import redis

    r = redis.Redis(
        host=os.getenv("REDIS_HOST", "127.0.0.1"),
        port=int(os.getenv("REDIS_PORT", "6379")),
        db=int(os.getenv("REDIS_DB", "0")),
        decode_responses=True,
    )
    r.set("wms:ontology:version", str(ontology().get("version")))
    r.set("wms:boot", "ok")
    print(f"[redis] ping={r.ping()} keys={r.keys('wms:*')}")


# ---------------------------------------------------------------- 数据自检

def validate(onto: dict) -> list[str]:
    """灌数前先校验数据自洽，避免手改 dataset 之后写出脏数据。

    返回错误列表，空表示通过。检查的是「引用完整性」和「数量对得上」，
    不检查业务合理性（那个靠规则和演示脚本保证）。
    """
    errors: list[str] = []

    wh_ids = {w["id"] for w in _ds.WAREHOUSES}
    sku_ids = {s["id"] for s in _ds.SKUS}
    batch_ids = {b["id"] for b in _ds.BATCHES}
    shipment_batches = {s["batch_id"] for s in _ds.SHIPMENTS}
    rule_ids = {r["id"] for r in onto.get("rules", [])}

    def dup(items, key, label):
        seen, bad = set(), []
        for it in items:
            v = it[key]
            if v in seen:
                bad.append(v)
            seen.add(v)
        if bad:
            errors.append(f"{label} 主键重复：{sorted(set(bad))}")

    dup(_ds.WAREHOUSES, "id", "warehouses")
    dup(_ds.SKUS, "id", "skus")
    dup(_ds.BATCHES, "id", "batches")
    dup(_ds.SHIPMENTS, "id", "shipments")
    dup(_ds.ORDERS, "id", "orders")

    for w in _ds.WAREHOUSES:
        if w["type"] not in ("finished_goods", "service_parts", "bonded"):
            errors.append(f"仓库 {w['id']} 类型非法：{w['type']}")

    seen_inv = set()
    for i in _ds.INVENTORY:
        key = (i["sku_id"], i["warehouse_id"])
        if key in seen_inv:
            errors.append(f"inventory 重复：{key}")
        seen_inv.add(key)
        if i["sku_id"] not in sku_ids:
            errors.append(f"inventory 引用了不存在的物料：{i['sku_id']}")
        if i["warehouse_id"] not in wh_ids:
            errors.append(f"inventory 引用了不存在的仓库：{i['warehouse_id']}")
        for f in ("on_hand", "allocated", "reserved", "hold_qty", "in_transit_arrived", "safety_stock"):
            if i[f] < 0:
                errors.append(f"inventory {key} 字段 {f} 为负：{i[f]}")

    # 批次：必须挂在真实存在的仓上，且数量能解释清在库量
    by_key: dict[tuple, list[dict]] = {}
    for b in _ds.BATCHES:
        if b["sku_id"] not in sku_ids:
            errors.append(f"批次 {b['id']} 引用了不存在的物料：{b['sku_id']}")
        if b["warehouse_id"] not in wh_ids:
            errors.append(f"批次 {b['id']} 引用了不存在的仓库：{b['warehouse_id']}")
        if b["qty"] <= 0:
            errors.append(f"批次 {b['id']} 数量非法：{b['qty']}")
        if b["cleared"] not in (0, 1, True, False):
            errors.append(f"批次 {b['id']} 的 cleared 不是 0/1：{b['cleared']}")
        by_key.setdefault((b["sku_id"], b["warehouse_id"]), []).append(b)

    for i in _ds.INVENTORY:
        key = (i["sku_id"], i["warehouse_id"])
        batches = by_key.get(key, [])
        if i["on_hand"] > 0 and not batches:
            errors.append(f"{key} 有在库 {i['on_hand']} 但没有任何批次")
        total = sum(b["qty"] for b in batches)
        not_in_transit = sum(b["qty"] for b in batches if b["id"] not in shipment_batches)
        if total < i["on_hand"]:
            errors.append(f"{key} 批次合计 {total} 少于在库 {i['on_hand']}")
        if not_in_transit > i["on_hand"]:
            errors.append(f"{key} 非在途批次合计 {not_in_transit} 超过在库 {i['on_hand']}")

    # 在途：批次必须存在，且物料/仓要与批次一致
    batch_by_id = {b["id"]: b for b in _ds.BATCHES}
    for s in _ds.SHIPMENTS:
        b = batch_by_id.get(s["batch_id"])
        if not b:
            errors.append(f"在途 {s['id']} 引用了不存在的批次：{s['batch_id']}")
            continue
        if b["sku_id"] != s["sku_id"] or b["warehouse_id"] != s["warehouse_id"]:
            errors.append(f"在途 {s['id']} 与批次 {b['id']} 的物料/仓库不一致")
        if s["qty"] > b["qty"]:
            errors.append(f"在途 {s['id']} 数量 {s['qty']} 超过批次 {b['id']} 的 {b['qty']}")
        if s["eta_days"] <= 0:
            errors.append(f"在途 {s['id']} 的 eta_days 非法：{s['eta_days']}")

    # 替代料：两端都要存在，不能自替代，不能重复
    seen_sub = set()
    for s in _ds.SUBSTITUTES:
        for end in (s["from_sku"], s["to_sku"]):
            if end not in sku_ids:
                errors.append(f"替代料引用了不存在的物料：{end}")
        if s["from_sku"] == s["to_sku"]:
            errors.append(f"替代料自替代：{s['from_sku']}")
        pair = (s["from_sku"], s["to_sku"])
        if pair in seen_sub:
            errors.append(f"替代料重复：{pair}")
        seen_sub.add(pair)
        if not s.get("restriction"):
            errors.append(f"替代料 {pair} 没有写限制条件")

    # 订单
    for o in _ds.ORDERS:
        if o["sku_id"] not in sku_ids:
            errors.append(f"订单 {o['id']} 引用了不存在的物料：{o['sku_id']}")
        if o["qty"] <= 0:
            errors.append(f"订单 {o['id']} 数量非法：{o['qty']}")
        if o["due_days"] <= 0:
            errors.append(f"订单 {o['id']} due_days 非法：{o['due_days']}")
        if o["type"] not in ("new_order", "replenish", "after_sales"):
            errors.append(f"订单 {o['id']} 类型非法：{o['type']}")
        if not o.get("customer"):
            errors.append(f"订单 {o['id']} 没有客户")

    # 客户 / 供应商 / 采购单：图上的多跳链要靠这些引用成立
    cust_ids = {c["id"] for c in _ds.CUSTOMERS}
    sup_ids = {s["id"] for s in _ds.SUPPLIERS}
    po_ids = {p["id"] for p in _ds.PURCHASE_ORDERS}
    dup(_ds.CUSTOMERS, "id", "customers")
    dup(_ds.SUPPLIERS, "id", "suppliers")
    dup(_ds.PURCHASE_ORDERS, "id", "purchase_orders")

    for o in _ds.ORDERS:
        if o["customer"] not in cust_ids:
            errors.append(f"订单 {o['id']} 的客户不在客户表里：{o['customer']}")

    for p in _ds.PURCHASE_ORDERS:
        if p["supplier_id"] not in sup_ids:
            errors.append(f"采购单 {p['id']} 的供应商不存在：{p['supplier_id']}")
        if p["sku_id"] not in sku_ids:
            errors.append(f"采购单 {p['id']} 的物料不存在：{p['sku_id']}")
        if p["qty"] <= 0:
            errors.append(f"采购单 {p['id']} 数量非法：{p['qty']}")

    for b in _ds.BATCHES:
        po = b.get("po_id")
        if not po:
            errors.append(f"批次 {b['id']} 没有关联采购单")
        elif po not in po_ids:
            errors.append(f"批次 {b['id']} 关联的采购单不存在：{po}")

    # 文档：DOC_RULES 的键必须和目录里的 md 一一对应，引用的规则必须存在
    doc_files = {p.stem for p in (ROOT / "data" / "docs").glob("*.md")}
    for doc_id, rules in _ds.DOC_RULES.items():
        if doc_id not in doc_files:
            errors.append(f"DOC_RULES 里的 {doc_id} 没有对应的 md 文件")
        for rid in rules:
            if rid not in rule_ids:
                errors.append(f"DOC_RULES 里 {doc_id} 引用了不存在的规则：{rid}")
    for doc_id in doc_files - set(_ds.DOC_RULES):
        errors.append(f"文档 {doc_id}.md 没有登记到 DOC_RULES，图里不会有它的治理关系")
    for doc_id, rules in _ds.OVERRIDES.items():
        if doc_id not in doc_files:
            errors.append(f"OVERRIDES 里的 {doc_id} 没有对应的 md 文件")
        for rid in rules:
            if rid not in rule_ids:
                errors.append(f"OVERRIDES 里 {doc_id} 引用了不存在的规则：{rid}")

    # 文档切片：太短的段落不会进 Milvus，会导致「有文档但检索不到」
    for path in sorted((ROOT / "data" / "docs").glob("*.md")):
        chunks = [c for c in path.read_text(encoding="utf-8").split("\n\n") if len(c.strip()) >= 40]
        if not chunks:
            errors.append(f"文档 {path.stem}.md 切不出任何片段（全部短于 40 字）")

    return errors


# ---------------------------------------------------------------- main

STORES = {"mysql": seed_mysql, "neo4j": seed_neo4j,
          "milvus": lambda _o: seed_milvus(), "redis": lambda _o: seed_redis()}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default=",".join(STORES))
    ap.add_argument("--check", action="store_true",
                    help="只做数据自检，不写任何库")
    args = ap.parse_args()

    onto = ontology()
    print(f"[ontology] version={onto.get('version')} rules={len(onto.get('rules', []))}")

    errors = validate(onto)
    if errors:
        print(f"[check] 发现 {len(errors)} 个数据问题：")
        for e in errors:
            print(f"  - {e}")
        if args.check:
            return 1
        print("[check] 有问题的数据不写库，先修 dataset.py")
        return 1
    print(f"[check] 数据自检通过：{len(_ds.WAREHOUSES)} 仓 / {len(_ds.SKUS)} 物料 / "
          f"{len(_ds.INVENTORY)} 库存行 / {len(_ds.BATCHES)} 批次 / "
          f"{len(_ds.SHIPMENTS)} 在途 / {len(_ds.SUBSTITUTES)} 替代 / "
          f"{len(_ds.ORDERS)} 订单 / {len(_ds.DOC_RULES)} 文档")
    if args.check:
        return 0

    failed = []
    for name in [s.strip() for s in args.only.split(",") if s.strip()]:
        if name not in STORES:
            print(f"[skip] unknown store: {name}")
            continue
        try:
            STORES[name](onto)
        except Exception as exc:  # 单个库连不上不影响其他库
            failed.append(name)
            print(f"[fail] {name}: {type(exc).__name__}: {exc}")

    if failed:
        print(f"未完成: {failed}")
        return 1
    print("全部完成")
    return 0


if __name__ == "__main__":
    sys.exit(main())
