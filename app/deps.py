"""依赖体检：一次性看四个库到底是"连不上"还是"连上了但没数据"。

为什么不复用 tools/*.py 里的 ping：
那些函数失败后会把模块级的 _degraded 记成 True，之后再也不重试（为了不让演示卡在超时上）。
体检页需要每次都是真实的当前状态，所以这里自己建连接、自己关，不碰那些状态位。

前端页面 app/static/health.html 和命令行 scripts/check_deps.py 都调这里，保证两边结论一致。
"""

from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from typing import Any, Callable

from . import config

TIMEOUT = 3  # 秒。云服务器跨公网，给 3 秒够用，再长页面就转圈了

_MILVUS_LOCK = threading.Lock()  # pymilvus 的 connections 是进程级全局的，别让两个线程同时注册别名


def _check(
    name: str,
    key: str,
    target: str,
    fn: Callable[[], str],
    hint: str = "",
) -> dict[str, Any]:
    start = time.perf_counter()
    try:
        detail = fn()
        return {
            "name": name, "key": key, "target": target, "ok": True,
            "ms": round((time.perf_counter() - start) * 1000, 1),
            "detail": detail, "error": None, "hint": hint,
        }
    except Exception as exc:
        return {
            "name": name, "key": key, "target": target, "ok": False,
            "ms": round((time.perf_counter() - start) * 1000, 1),
            "detail": None, "error": f"{type(exc).__name__}: {exc}"[:300], "hint": hint,
        }


# ---------------------------------------------------------------- 四个库


def _mysql_detail() -> str:
    import pymysql

    conn = pymysql.connect(
        charset="utf8mb4", connect_timeout=TIMEOUT,
        read_timeout=TIMEOUT, write_timeout=TIMEOUT, **config.MYSQL,
    )
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT VERSION()")
            version = cur.fetchone()[0]
            cur.execute("SHOW TABLES")
            tables = [row[0] for row in cur.fetchall()]
            if not tables:
                return f"MySQL {version} 连上了，但库 {config.MYSQL['database']} 里没有表"
            rows = 0
            if "inventory" in tables:
                cur.execute("SELECT COUNT(*) FROM inventory")
                rows = cur.fetchone()[0]
            return f"MySQL {version}，{len(tables)} 张表，inventory {rows} 行"
    finally:
        conn.close()


def _neo4j_detail() -> str:
    from neo4j import GraphDatabase

    driver = GraphDatabase.driver(
        config.NEO4J_URI, auth=config.NEO4J_AUTH, connection_timeout=TIMEOUT,
    )
    try:
        driver.verify_connectivity()
        with driver.session() as session:
            nodes = session.run("MATCH (n) RETURN count(n) AS c").single()["c"]
            rels = session.run("MATCH ()-[r]->() RETURN count(r) AS c").single()["c"]
            try:
                version = session.run(
                    "CALL dbms.components() YIELD versions RETURN versions[0] AS v"
                ).single()["v"]
            except Exception:
                version = "?"
            if nodes == 0:
                return f"Neo4j {version} 连上了，但图里没有节点"
            return f"Neo4j {version}，{nodes} 个节点 / {rels} 条关系"
    finally:
        driver.close()


def _redis_detail() -> str:
    import redis
    from redis.backoff import NoBackoff
    from redis.retry import Retry

    # redis-py 8 默认带重试和退避，端口不通时会磨 26 秒才抛错。
    # 体检要的是"现在通不通"，所以直接关掉重试，2 秒内出结论。
    client = redis.Redis(
        socket_connect_timeout=TIMEOUT, socket_timeout=TIMEOUT,
        retry_on_error=[], retry=Retry(NoBackoff(), 0), **config.REDIS,
    )
    try:
        client.ping()
        info = client.info("server")
        keys = client.dbsize()
        return f"Redis {info.get('redis_version', '?')}，db{config.REDIS['db']} 有 {keys} 个 key"
    finally:
        client.close()


def _milvus_detail() -> str:
    from pymilvus import Collection, connections, utility

    alias = "deps-healthcheck"
    with _MILVUS_LOCK:
        connections.connect(
            alias=alias, host=config.MILVUS_HOST, port=config.MILVUS_PORT, timeout=TIMEOUT,
        )
        try:
            collections = list(utility.list_collections(using=alias))
            head = f"Milvus 连上了，{len(collections)} 个 collection"
            if config.MILVUS_COLLECTION not in collections:
                return f"{head}，还没有 {config.MILVUS_COLLECTION}"
            count = Collection(config.MILVUS_COLLECTION, using=alias).num_entities
            return f"{head}，{config.MILVUS_COLLECTION} 有 {count} 条向量"
        finally:
            connections.disconnect(alias)


# ---------------------------------------------------------------- 汇总


def check_all() -> dict[str, Any]:
    """并发跑四个检查。

    串行的话四个都没起时要等 4 × 超时，页面转半分钟没法看；
    并发之后总耗时等于最慢的那一个。顺序仍按下面列表返回，页面不用再排。
    """
    mysql = config.MYSQL
    jobs: list[tuple[str, str, str, Callable[[], str], str]] = [
        ("MySQL", "mysql", f"{mysql['host']}:{mysql['port']}/{mysql['database']}",
         _mysql_detail, "库在跑但没表？python scripts/check_deps.py --init-schema"),
        ("Neo4j", "neo4j", config.NEO4J_URI,
         _neo4j_detail, "没节点？python scripts/seed.py --only neo4j"),
        ("Redis", "redis", f"{config.REDIS['host']}:{config.REDIS['port']}",
         _redis_detail, "状态库，不需要预置数据"),
        ("Milvus", "milvus", f"{config.MILVUS_HOST}:{config.MILVUS_PORT}",
         _milvus_detail, "milvus 起不来先看它的两个依赖 etcd 和 minio"),
    ]
    with ThreadPoolExecutor(max_workers=len(jobs), thread_name_prefix="deps") as pool:
        checks = list(pool.map(lambda job: _check(*job), jobs))
    ok = all(c["ok"] for c in checks)
    return {
        "ok": ok,
        "passed": sum(1 for c in checks if c["ok"]),
        "total": len(checks),
        "checked_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "checks": checks,
        "targets": {
            "mysql": f"{mysql['host']}:{mysql['port']}/{mysql['database']}",
            "neo4j": config.NEO4J_URI,
            "redis": f"{config.REDIS['host']}:{config.REDIS['port']}/db{config.REDIS['db']}",
            "milvus": f"{config.MILVUS_HOST}:{config.MILVUS_PORT}",
            "collection": config.MILVUS_COLLECTION,
        },
    }
