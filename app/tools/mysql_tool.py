"""MySQL 访问。

优先查 MySQL。连不上就退到本地数据层，返回结构完全一致。
第一次失败后记住状态，避免每次请求都等连接超时。
"""

from __future__ import annotations

from typing import Any

import pymysql

from .. import config
from . import dataset

_degraded = False


def backend() -> str:
    return "local" if _degraded else "mysql"


def _conn():
    return pymysql.connect(charset="utf8mb4", cursorclass=pymysql.cursors.DictCursor,
                           autocommit=True, connect_timeout=2, read_timeout=5,
                           write_timeout=5, **config.MYSQL)


def query(sql: str, params: tuple | list = ()) -> list[dict[str, Any]]:
    with _conn() as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        return list(cur.fetchall())


def _try(sql: str, params: tuple | list, fallback):
    global _degraded
    if _degraded:
        return fallback()
    try:
        return query(sql, params)
    except Exception:
        _degraded = True
        return fallback()


def ping() -> bool:
    global _degraded
    if _degraded:
        return False
    try:
        query("SELECT 1 AS ok")
        return True
    except Exception:
        _degraded = True
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
