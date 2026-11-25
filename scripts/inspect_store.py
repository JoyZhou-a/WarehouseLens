"""盘点上这个系统里到底存了什么。只读，不改任何数据。

    python scripts/inspect_store.py
    python scripts/inspect_store.py --samples 5
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")

from app import config  # noqa: E402


def head(title: str) -> None:
    print(f"\n{'=' * 78}\n{title}\n{'=' * 78}")


def mysql_dump(samples: int) -> None:
    import pymysql

    head("MySQL · 明细数据")
    conn = pymysql.connect(cursorclass=pymysql.cursors.DictCursor, charset="utf8mb4",
                           connect_timeout=10, **config.MYSQL)
    try:
        with conn.cursor() as cur:
            cur.execute("SHOW TABLES")
            tables = [list(r.values())[0] for r in cur.fetchall()]
            total = 0
            for t in tables:
                cur.execute(f"SELECT COUNT(*) AS c FROM `{t}`")
                c = cur.fetchone()["c"]
                total += c
                print(f"\n  [{t}]  {c} 行")
                if c:
                    cur.execute(f"SELECT * FROM `{t}` LIMIT {samples}")
                    for row in cur.fetchall():
                        text = ", ".join(f"{k}={v}" for k, v in row.items())
                        print(f"      {text[:150]}")
            print(f"\n  合计 {len(tables)} 张表 / {total} 行")
    finally:
        conn.close()


def neo4j_dump(samples: int) -> None:
    from neo4j import GraphDatabase

    head("Neo4j · 关系图谱")
    drv = GraphDatabase.driver(config.NEO4J_URI, auth=config.NEO4J_AUTH, connection_timeout=10)
    try:
        with drv.session() as s:
            print("\n  节点：")
            for row in s.run("MATCH (n) RETURN labels(n)[0] AS label, count(*) AS c ORDER BY c DESC"):
                print(f"      {row['label']:<12} {row['c']}")
            print("\n  关系：")
            for row in s.run("MATCH ()-[r]->() RETURN type(r) AS t, count(*) AS c ORDER BY c DESC"):
                print(f"      {row['t']:<18} {row['c']}")
            print("\n  一条多跳路径（物料 → 批次 → 仓库）：")
            q = """
            MATCH (k:SKU {id:'NB-X1C-14'})-[:HAS_BATCH]->(b:Batch)-[:STORED_IN]->(w:Warehouse)
            RETURN k.id AS sku, b.id AS batch, b.expiry_days AS expiry, w.name AS wh
            ORDER BY b.expiry_days LIMIT $n
            """
            for row in s.run(q, n=samples):
                print(f"      {row['sku']} → {row['batch']} (效期 {row['expiry']} 天) → {row['wh']}")
            print("\n  规则与文档的治理关系：")
            q2 = """
            MATCH (d:Doc)-[x:GOVERNS|OVERRIDES]->(r:Rule)
            RETURN d.id AS doc, type(x) AS rel, r.id AS rule, r.label AS label
            """
            for row in s.run(q2):
                print(f"      {row['doc']:<22} {row['rel']:<10} {row['rule']} {row['label']}")
    finally:
        drv.close()


def milvus_dump(samples: int) -> None:
    from pymilvus import Collection, connections, utility

    head("Milvus · 文档向量")
    connections.connect(alias="inspect", host=config.MILVUS_HOST, port=config.MILVUS_PORT)
    try:
        for name in utility.list_collections(using="inspect"):
            col = Collection(name, using="inspect")
            dim = next((f.params.get("dim") for f in col.schema.fields if f.name == "vector"), "?")
            print(f"\n  [{name}]  {col.num_entities} 条向量，维度 {dim}")
            print(f"      字段：{', '.join(f.name for f in col.schema.fields)}")
            col.load()
            rows = col.query(expr="chunk_no >= 0", output_fields=["doc_id", "chunk_no", "text"],
                             limit=samples)
            for r in rows:
                print(f"      {r['doc_id']:<22} chunk{r['chunk_no']}  {r['text'][:44]!r}")
    finally:
        connections.disconnect("inspect")


def redis_dump() -> None:
    import redis

    head("Redis · 运行状态与轨迹")
    r = redis.Redis(**config.REDIS, decode_responses=True)
    scheme = {"wms:": "启动标记", "state:": "任务快照", "trace:": "编排轨迹"}
    keys = sorted(r.keys("*"))
    print(f"\n  共 {len(keys)} 个 key")
    for k in keys[:12]:
        kind = next((v for p, v in scheme.items() if k.startswith(p)), "?")
        ttl = r.ttl(k)
        t = r.type(k)
        size = r.llen(k) if t == "list" else len(r.get(k) or "")
        print(f"      {k:<28} {kind:<8} type={t:<6} 长度={size:<6} ttl={ttl}s")
    if len(keys) > 12:
        print(f"      … 其余 {len(keys) - 12} 个同类型")


def ontology_dump() -> None:
    from app.ontology import get_ontology

    head("ontology/wms.yaml · 口径与规则")
    onto = get_ontology()
    print(f"\n  版本 {onto.version}，路径 {onto.path.name}")
    print(f"\n  类（{len(onto.classes)}）：")
    for name, spec in onto.classes.items():
        print(f"      {name:<12} {spec.get('label', '')}  属性: {', '.join(spec.get('props', []))}")
    print(f"\n  关系（{len(onto.relations)}）：")
    for rel in onto.relations:
        print(f"      {rel['from']} -{rel['name']}-> {rel['to']}   {rel.get('label', '')}")
    print(f"\n  指标（{len(onto.metrics)}）：")
    for name, m in onto.metrics.items():
        print(f"      {name:<22} {m['formula']}")
    print(f"\n  规则（{len(onto.rules)}）：")
    for rule in onto.rules:
        flag = "启用" if rule.get("enabled", True) else "停用"
        print(f"      {rule['id']} [{flag}] {rule.get('label', '')}  when={json.dumps(rule.get('when'), ensure_ascii=False)} -> {rule['then']}")
    if onto.conflicts:
        print(f"\n  口径冲突（{len(onto.conflicts)}）：")
        for c in onto.conflicts:
            print(f"      {c.get('id')} on {c.get('metric', '?')}: {c.get('resolution', '')}")


def docs_dump() -> None:
    head("data/docs · 原始文档")
    for path in sorted((ROOT / "data" / "docs").glob("*.md")):
        text = path.read_text(encoding="utf-8")
        chunks = [c for c in text.split("\n\n") if len(c.strip()) >= 40]
        print(f"  {path.name:<26} {len(text):>5} 字符  →  {len(chunks)} 段")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--samples", type=int, default=3)
    args = ap.parse_args()

    print(f"目标：MySQL {config.MYSQL['host']} · Neo4j {config.NEO4J_URI} · "
          f"Milvus {config.MILVUS_HOST} · Redis {config.REDIS['host']}")
    docs_dump()
    ontology_dump()
    mysql_dump(args.samples)
    neo4j_dump(args.samples)
    milvus_dump(args.samples)
    redis_dump()
    return 0


if __name__ == "__main__":
    sys.exit(main())
