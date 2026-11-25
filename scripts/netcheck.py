"""云上四库连通性与协议验证。

    python scripts/netcheck.py            # 每端口 10 次 + 协议验证
    python scripts/netcheck.py -n 3       # 改连接次数

只读：不建表、不写数据。输出直接可以贴进面试材料。
"""

from __future__ import annotations

import argparse
import os
import socket
import statistics
import sys
import time
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")

HOST = os.getenv("MILVUS_HOST") or "127.0.0.1"

PORTS = [
    ("MySQL", int(os.getenv("MYSQL_PORT", "3306"))),
    ("Neo4j-HTTP", 7474),
    ("Neo4j-Bolt", 7687),
    ("Redis", int(os.getenv("REDIS_PORT", "6379"))),
    ("Milvus", int(os.getenv("MILVUS_PORT", "19530"))),
]


def tcp_stats(host: str, port: int, n: int) -> tuple[list[float], list[str]]:
    ok, errs = [], []
    for _ in range(n):
        s = socket.socket()
        s.settimeout(5)
        t0 = time.perf_counter()
        try:
            s.connect((host, port))
            ok.append((time.perf_counter() - t0) * 1000)
        except Exception as exc:
            errs.append(f"{type(exc).__name__}: {exc}")
        finally:
            s.close()
        time.sleep(0.05)
    return ok, errs


def report_latency(host: str, n: int) -> None:
    print(f"\n== TCP 连接 {n} 次  host={host} ==")
    print(f"{'端口':<12}{'服务':<12}{'成功':>4}{'失败':>4}{'最小':>9}{'平均':>9}{'最大':>9}")
    for name, port in PORTS:
        ok, errs = tcp_stats(host, port, n)
        if ok:
            print(f"{port:<12}{name:<12}{len(ok):>4}{len(errs):>4}"
                  f"{min(ok):>8.1f}ms{statistics.mean(ok):>8.1f}ms{max(ok):>8.1f}ms")
        else:
            print(f"{port:<12}{name:<12}{0:>4}{len(errs):>4}  全部失败: {errs[0]}")


def check_mysql() -> tuple[bool, str]:
    import pymysql
    try:
        conn = pymysql.connect(
            host=os.getenv("MYSQL_HOST", "127.0.0.1"),
            port=int(os.getenv("MYSQL_PORT", "3306")),
            user=os.getenv("MYSQL_USER", "root"),
            password=os.getenv("MYSQL_PASSWORD", ""),
            database=os.getenv("MYSQL_DATABASE", "wms"),
            charset="utf8mb4", connect_timeout=8, read_timeout=8,
        )
        with conn.cursor() as cur:
            cur.execute("select version(), current_user(), @@character_set_server")
            ver, user, cs = cur.fetchone()
            cur.execute("show tables")
            tables = [r[0] for r in cur.fetchall()]
        conn.close()
        return True, f"握手+鉴权 OK  {ver}  user={user}  charset={cs}  表={tables or '空'}"
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}"


def check_redis() -> tuple[bool, str]:
    import redis
    try:
        r = redis.Redis(host=os.getenv("REDIS_HOST", "127.0.0.1"),
                        port=int(os.getenv("REDIS_PORT", "6379")),
                        password=os.getenv("REDIS_PASSWORD") or None,
                        socket_connect_timeout=8, socket_timeout=8,
                        decode_responses=True)
        info = r.info("server")
        return True, (f"PING={r.ping()}  redis={info.get('redis_version')}  "
                      f"keys={len(r.keys('wms:*'))}")
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}"


def check_neo4j() -> tuple[bool, str]:
    from neo4j import GraphDatabase
    try:
        drv = GraphDatabase.driver(
            os.getenv("NEO4J_URI", "bolt://127.0.0.1:7687"),
            auth=(os.getenv("NEO4J_USER", "neo4j"), os.getenv("NEO4J_PASSWORD", "")),
            connection_timeout=10,
        )
        drv.verify_connectivity()
        with drv.session() as s:
            comp = s.run("CALL dbms.components() YIELD name, versions RETURN name, versions").data()
            labels = [r["label"] for r in s.run("CALL db.labels()").data()]
            n = s.run("MATCH (n) RETURN count(n) AS c").single()["c"]
        drv.close()
        return True, f"bolt OK  {comp[0]['name']} {comp[0]['versions']}  节点={n}  labels={labels or '空'}"
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}"


def check_milvus() -> tuple[bool, str]:
    try:
        from pymilvus import MilvusClient
    except Exception as exc:
        return False, f"import: {type(exc).__name__}: {exc}"
    host = os.getenv("MILVUS_HOST", "127.0.0.1")
    port = os.getenv("MILVUS_PORT", "19530")
    try:
        c = MilvusClient(uri=f"http://{host}:{port}")
        ver = c.get_server_version()
        cols = list(c.list_collections())
        c.close()
        return True, f"gRPC OK  server={ver}  collections={cols or '空'}"
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}"


def check_milvus_legacy() -> tuple[bool, str]:
    """seed.py 用的是 pymilvus 老 API，单独验一遍，免得灌数时才炸。"""
    try:
        from pymilvus import connections, utility
    except Exception as exc:
        return False, f"import: {type(exc).__name__}: {exc}"
    try:
        connections.connect(alias="default",
                            host=os.getenv("MILVUS_HOST", "127.0.0.1"),
                            port=str(os.getenv("MILVUS_PORT", "19530")), timeout=10)
        cols = utility.list_collections()
        connections.disconnect("default")
        return True, f"旧 API connections.connect OK  collections={cols or '空'}"
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}"


CHECKS = [
    ("MySQL 协议", check_mysql),
    ("Redis 协议", check_redis),
    ("Neo4j Bolt", check_neo4j),
    ("Milvus gRPC", check_milvus),
    ("Milvus 旧API", check_milvus_legacy),
]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("-n", "--times", type=int, default=10)
    ap.add_argument("--host", default=HOST)
    args = ap.parse_args()

    print(f"目标 {args.host}")
    report_latency(args.host, args.times)

    print("\n== 协议验证 ==")
    bad = 0
    for name, fn in CHECKS:
        t0 = time.perf_counter()
        ok, msg = fn()
        ms = (time.perf_counter() - t0) * 1000
        print(f"[{'OK' if ok else 'FAIL'}] {name:<12} {ms:6.1f}ms  {msg}")
        bad += 0 if ok else 1
    print(f"\n{len(CHECKS) - bad}/{len(CHECKS)} 通过")
    return 0 if bad == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
