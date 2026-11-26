"""NL2SQL 对照臂：让模型自己写 SQL，对着 MySQL 查完再回答。

这是「不用图、不用语义操作」的最强做法，用来证明：
在多跳/聚合问题上，手写 SQL 的错误率远高于「调用一个固化了查询形状的工具」。
"""

from __future__ import annotations

import re
from decimal import Decimal

import pymysql

from .. import config

FORBIDDEN = re.compile(r"\b(insert|update|delete|drop|alter|truncate|create|grant|replace)\b",
                       re.I)


def _conn():
    return pymysql.connect(
        host=config.MYSQL["host"], port=config.MYSQL["port"], user=config.MYSQL["user"],
        password=config.MYSQL["password"], database=config.MYSQL["database"],
        charset="utf8mb4", connect_timeout=6, read_timeout=20,
    )


def schema_text() -> str:
    """把库结构渲染成模型能读的文本（列名 + 类型 + 主键）。"""
    lines = []
    try:
        conn = _conn()
        with conn.cursor() as cur:
            cur.execute("SHOW TABLES")
            tables = [r[0] for r in cur.fetchall()]
            for t in tables:
                cur.execute(f"SHOW COLUMNS FROM `{t}`")
                cols = [f"{c[0]} {c[1]}" for c in cur.fetchall()]
                cur.execute(f"SELECT COUNT(*) FROM `{t}`")
                n = cur.fetchone()[0]
                lines.append(f"{t}({', '.join(cols)})  -- {n} 行")
        conn.close()
    except Exception as exc:
        lines.append(f"-- 取结构失败：{type(exc).__name__}: {exc}")
    return "\n".join(lines)


def _clean(value):
    """MySQL 会返回 Decimal / date，直接塞进 state 会让 json.dumps 炸掉。"""
    if isinstance(value, Decimal):
        return float(value)
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return value


def execute(sql: str, limit: int = 50) -> dict:
    """只允许单条 SELECT，并强制加 LIMIT，避免演示时把库拖住。"""
    raw = (sql or "").strip().rstrip(";")
    if not raw:
        return {"ok": False, "error": "SQL 为空"}
    if FORBIDDEN.search(raw):
        return {"ok": False, "error": "SQL 里出现写操作，已拒绝"}
    if not re.match(r"(?is)^(select|with)\b", raw):
        return {"ok": False, "error": "只允许 SELECT"}
    if not re.search(r"(?i)\blimit\b", raw):
        raw = f"{raw} LIMIT {limit}"
    try:
        conn = _conn()
        with conn.cursor(pymysql.cursors.DictCursor) as cur:
            cur.execute(raw)
            rows = cur.fetchall()
        conn.close()
        cleaned = [{k: _clean(v) for k, v in row.items()} for row in rows]
        return {"ok": True, "sql": raw, "rows": cleaned[:200], "count": len(cleaned)}
    except Exception as exc:
        return {"ok": False, "sql": raw, "error": f"{type(exc).__name__}: {exc}"}
