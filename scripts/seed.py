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
import zlib
from pathlib import Path

import numpy as np
import yaml
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")

EMBED_DIM = int(os.getenv("EMBED_DIM", "512"))
DOC_DIR = ROOT / "data" / "docs"
ONTOLOGY_PATH = ROOT / os.getenv("ONTOLOGY_PATH", "ontology/wms.yaml")

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
    conn.close()
    print(f"[mysql] warehouses={len(WAREHOUSES)} skus={len(SKUS)} "
          f"inventory={len(INVENTORY)} batches={len(BATCHES)} shipments={len(SHIPMENTS)}")


# ---------------------------------------------------------------- Neo4j

def seed_neo4j(onto: dict) -> None:
    from neo4j import GraphDatabase

    driver = GraphDatabase.driver(
        os.getenv("NEO4J_URI", "bolt://127.0.0.1:7687"),
        auth=(os.getenv("NEO4J_USER", "neo4j"), os.getenv("NEO4J_PASSWORD", "neo4j123456")),
    )
    with driver.session() as s:
        s.run("MATCH (n) WHERE n:Warehouse OR n:SKU OR n:Batch OR n:Shipment "
              "OR n:Doc OR n:Rule OR n:Ontology DETACH DELETE n")

        s.run("CREATE (:Ontology {version: $v, path: $p})",
              v=str(onto.get("version")), p=str(ONTOLOGY_PATH.name))

        for wid, name, wtype, city in WAREHOUSES:
            s.run("CREATE (:Warehouse {id:$id, name:$name, type:$type, city:$city})",
                  id=wid, name=name, type=wtype, city=city)

        for sid, name, cat in SKUS:
            s.run("CREATE (:SKU {id:$id, name:$name, category:$cat})", id=sid, name=name, cat=cat)

        for bid, sku, wid, qty, exp, cleared in BATCHES:
            s.run(
                "MATCH (wh:Warehouse {id:$wid}) "
                "CREATE (b:Batch {id:$id, sku_id:$sku, qty:$qty, "
                "expiry_days:$exp, cleared:$cleared})-[:STORED_IN]->(wh)",
                id=bid, sku=sku, wid=wid, qty=qty, exp=exp, cleared=cleared,
            )
            s.run("MATCH (b:Batch {id:$b}), (k:SKU {id:$s}) MERGE (k)-[:HAS_BATCH]->(b)",
                  b=bid, s=sku)

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

        # 文档与规则的治理关系，用来演示「哪条规则出自哪份文件」
        governs = {
            "SOP-EXPIRY": ["R1"],
            "SOP-BONDED": ["R2", "R3"],
            "SOP-ATP-2024": [],
            "contract-C-2024-017": [],
        }
        for doc_id, rule_ids in governs.items():
            s.run("CREATE (:Doc {id:$id, title:$t})", id=doc_id, t=doc_id)
            for rid in rule_ids:
                s.run("MATCH (d:Doc {id:$d}), (r:Rule {id:$r}) MERGE (d)-[:GOVERNS]->(r)", d=doc_id, r=rid)

        s.run(
            "MATCH (d:Doc {id:'contract-C-2024-017'}), (r:Rule {id:'R3'}) "
            "MERGE (d)-[:OVERRIDES]->(r)"
        )

        counts = {}
        for label in ["Warehouse", "SKU", "Batch", "Shipment", "Rule", "Doc"]:
            counts[label] = s.run(f"MATCH (n:{label}) RETURN count(n) AS c").single()["c"]
    driver.close()
    print(f"[neo4j] {counts}")


# ---------------------------------------------------------------- Milvus

def embed(text: str) -> list[float]:
    """字符二元组哈希向量，不依赖外部模型，离线可跑。演示够用，换 BGE 也只改这个函数。"""
    v = np.zeros(EMBED_DIM, dtype=np.float32)
    t = "".join(text.split())
    for i in range(len(t) - 1):
        h = zlib.crc32(t[i:i + 2].encode("utf-8")) % EMBED_DIM
        v[h] += 1.0
    n = float(np.linalg.norm(v))
    return (v / n).tolist() if n else v.tolist()


def load_docs() -> list[dict]:
    rows = []
    for path in sorted(DOC_DIR.glob("*.md")):
        text = path.read_text(encoding="utf-8")
        title = path.stem
        chunks = [c.strip() for c in text.split("\n\n") if len(c.strip()) >= 40]
        for i, chunk in enumerate(chunks):
            rows.append({"doc_id": title, "title": title, "chunk_no": i,
                         "text": chunk, "vector": embed(chunk)})
    return rows


def seed_milvus() -> None:
    from pymilvus import (Collection, CollectionSchema, DataType, FieldSchema,
                          connections, utility)

    connections.connect(
        alias="default",
        host=os.getenv("MILVUS_HOST", "127.0.0.1"),
        port=str(os.getenv("MILVUS_PORT", "19530")),
    )
    name = os.getenv("MILVUS_COLLECTION", "wms_docs")
    if utility.has_collection(name):
        utility.drop_collection(name)

    fields = [
        FieldSchema("id", DataType.INT64, is_primary=True, auto_id=True),
        FieldSchema("doc_id", DataType.VARCHAR, max_length=128),
        FieldSchema("title", DataType.VARCHAR, max_length=256),
        FieldSchema("chunk_no", DataType.INT64),
        FieldSchema("text", DataType.VARCHAR, max_length=4096),
        FieldSchema("vector", DataType.FLOAT_VECTOR, dim=EMBED_DIM),
    ]
    col = Collection(name, CollectionSchema(fields, description="仓储文档片段"))
    rows = load_docs()
    col.insert([{"doc_id": r["doc_id"], "title": r["title"], "chunk_no": r["chunk_no"],
                 "text": r["text"], "vector": r["vector"]} for r in rows])
    col.create_index("vector", {"index_type": "IVF_FLAT", "metric_type": "COSINE",
                                "params": {"nlist": 64}})
    col.load()
    print(f"[milvus] collection={name} chunks={len(rows)} dim={EMBED_DIM}")


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


# ---------------------------------------------------------------- main

STORES = {"mysql": seed_mysql, "neo4j": seed_neo4j,
          "milvus": lambda _o: seed_milvus(), "redis": lambda _o: seed_redis()}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default=",".join(STORES))
    args = ap.parse_args()

    onto = ontology()
    print(f"[ontology] version={onto.get('version')} rules={len(onto.get('rules', []))}")

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
