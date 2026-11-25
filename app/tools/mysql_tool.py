"""MySQL 访问。

优先查 MySQL。连不上就退到本地数据层，返回结构完全一致。
第一次失败后记住状态，避免每次请求都等连接超时。
"""

from __future__ import annotations

import threading
import time
from typing import Any

import pymysql

from .. import config
from . import dataset

# 同 graph_tool：降级带冷却，掉一次不代表整场都不能用真库
COOLDOWN = 20.0
_degraded_at: float | None = None
_last_error: str | None = None
_local = threading.local()


def _degraded() -> bool:
    if _degraded_at is None:
        return False
    return (time.time() - _degraded_at) < COOLDOWN


def _mark_failed(exc: Exception | None = None) -> None:
    global _degraded_at, _last_error
    _degraded_at = time.time()
    if exc is not None:
        _last_error = f"{type(exc).__name__}: {exc}"
        print(f"[mysql_tool] 退回本地数据：{_last_error[:300]}", flush=True)


def _mark_ok() -> None:
    global _degraded_at
    _degraded_at = None


def last_error() -> str | None:
    return _last_error


def backend() -> str:
    return "local" if _degraded() else "mysql"


def _connect():
    return pymysql.connect(charset="utf8mb4", cursorclass=pymysql.cursors.DictCursor,
                           autocommit=True, connect_timeout=6, read_timeout=10,
                           write_timeout=10, **config.MYSQL)


def _conn():
    """按线程复用连接。

    云上 MySQL 一次握手加鉴权要 300ms 左右，每次查询都新建连接会让一次问答多等好几秒。
    每个线程持有一条连接，失效了（服务端超时断链）就用 ping 重连。
    """
    conn = getattr(_local, "conn", None)
    if conn is None:
        conn = _connect()
        _local.conn = conn
        return conn
    try:
        conn.ping(reconnect=True)
        return conn
    except Exception:
        _local.conn = None
        conn = _connect()
        _local.conn = conn
        return conn


def _execute(sql: str, params: tuple | list) -> list[dict[str, Any]]:
    with _conn().cursor() as cur:
        cur.execute(sql, params)
        return list(cur.fetchall())


def query(sql: str, params: tuple | list = ()) -> list[dict[str, Any]]:
    try:
        return _execute(sql, params)
    except (pymysql.err.OperationalError, pymysql.err.InterfaceError):
        # 连接被服务端关掉：丢掉重连一次，还失败才交给上层降级
        _local.conn = None
        return _execute(sql, params)


def _try(sql: str, params: tuple | list, fallback):
    if _degraded():
        return fallback()
    try:
        result = query(sql, params)
        _mark_ok()
        return result
    except Exception as exc:
        _mark_failed(exc)
        return fallback()


def ping() -> bool:
    if _degraded():
        return False
    try:
        query("SELECT 1 AS ok")
        _mark_ok()
        return True
    except Exception as exc:
        _mark_failed(exc)
        return False


def inventory_rows(sku_id: str) -> list[dict]:
    return _try(
        """
        SELECT i.sku_id, i.warehouse_id, w.name AS warehouse_name, w.type AS warehouse_type,
               w.city, i.on_hand, i.allocated, i.reserved, i.hold_qty,
               i.in_transit_arrived, i.safety_stock
        FROM inventory i JOIN warehouses w ON w.id = i.warehouse_id
        WHERE i.sku_id = %s ORDER BY i.warehouse_id
        """,
        (sku_id,),
        lambda: dataset.inventory_rows(sku_id),
    )


def batches(sku_id: str, warehouse_id: str | None = None) -> list[dict]:
    sql = ("SELECT id, sku_id, warehouse_id, qty, expiry_days, cleared "
           "FROM batches WHERE sku_id = %s")
    params: list = [sku_id]
    if warehouse_id:
        sql += " AND warehouse_id = %s"
        params.append(warehouse_id)
    sql += " ORDER BY expiry_days ASC"
    return _try(sql, params, lambda: dataset.batches(sku_id, warehouse_id))


def shipments(sku_id: str) -> list[dict]:
    return _try(
        """
        SELECT s.id, s.sku_id, s.warehouse_id, s.batch_id, s.qty, s.eta_days, s.carrier,
               b.expiry_days, b.cleared
        FROM shipments s LEFT JOIN batches b ON b.id = s.batch_id
        WHERE s.sku_id = %s ORDER BY s.eta_days ASC
        """,
        (sku_id,),
        lambda: dataset.shipments(sku_id),
    )


def substitutes(sku_id: str) -> list[dict]:
    return _try(
        """
        SELECT s.from_sku, s.to_sku, s.restriction, k.name AS to_name, k.category
        FROM substitutes s JOIN skus k ON k.id = s.to_sku
        WHERE s.from_sku = %s
        """,
        (sku_id,),
        lambda: dataset.substitutes(sku_id),
    )


def sku(sku_id: str) -> dict | None:
    def fallback():
        return [dataset.sku(sku_id)] if dataset.sku(sku_id) else []

    rows = _try("SELECT id, name, category FROM skus WHERE id = %s", (sku_id,), fallback)
    return rows[0] if rows else None


def warehouses() -> list[dict]:
    return _try("SELECT id, name, type, city FROM warehouses ORDER BY id", (),
                dataset.warehouses)
