"""依赖体检，命令行版。和网页 /health 用的是同一份检查逻辑（app/deps.py）。

用法：
    python scripts/check_deps.py                      # 查 .env 里配的地址
    python scripts/check_deps.py --host 47.1.2.3      # 临时查云端，不改 .env
    python scripts/check_deps.py --init-schema        # 顺便把 db/mysql/schema.sql 建成表

--host 只影响这一次运行，端口沿用 .env 里的配置。库在云上、代码在本地时用这个，
不用改 .env 也不用在云服务器上装 Python。

退出码：0 全部通过，1 有依赖没通过。所以可以直接：
    python scripts/check_deps.py --host <云IP> && python scripts/seed.py
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

SCHEMA = ROOT / "db" / "mysql" / "schema.sql"


def point_at(host: str) -> None:
    """把四个库的 host 临时指到别的机器，端口保持原样。"""
    from app import config

    parsed = urlparse(config.NEO4J_URI.replace("bolt://", "http://"))
    neo4j_port = parsed.port or 7687

    config.MYSQL["host"] = host
    config.REDIS["host"] = host
    config.MILVUS_HOST = host
    config.NEO4J_URI = f"bolt://{host}:{neo4j_port}"


def init_schema() -> None:
    import pymysql

    from app import config

    if not SCHEMA.exists():
        raise SystemExit(f"找不到 {SCHEMA}")
    conn = pymysql.connect(
        charset="utf8mb4", autocommit=True, connect_timeout=5, **config.MYSQL
    )
    try:
        # schema.sql 用的是 CREATE TABLE IF NOT EXISTS，重复执行安全。
        # 按分号切，跳过空语句；文件里的 -- 注释由服务端解析，不用先剥。
        sql = SCHEMA.read_text(encoding="utf-8")
        statements = [s.strip() for s in sql.split(";") if s.strip()]
        with conn.cursor() as cur:
            for stmt in statements:
                cur.execute(stmt)
            cur.execute("SHOW TABLES")
            tables = [r[0] for r in cur.fetchall()]
    finally:
        conn.close()
    print(f"[mysql] 建表完成，当前 {len(tables)} 张表：{', '.join(tables)}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", help="临时把四个库的 host 指向这台机器，比如云服务器公网 IP")
    ap.add_argument("--init-schema", action="store_true", help="建表后再体检")
    args = ap.parse_args()

    from app import config, deps

    if args.host:
        point_at(args.host)
        print(f"host 已临时覆盖为 {args.host}（端口沿用 .env，不改文件）")

    print(f"配置来源：{ROOT / '.env'}（不存在则用默认值）")
    print(f"  MySQL  {config.MYSQL['host']}:{config.MYSQL['port']}/{config.MYSQL['database']}")
    print(f"  Neo4j  {config.NEO4J_URI}  user={config.NEO4J_AUTH[0]}")
    print(f"  Redis  {config.REDIS['host']}:{config.REDIS['port']} db={config.REDIS['db']}")
    print(f"  Milvus {config.MILVUS_HOST}:{config.MILVUS_PORT} "
          f"collection={config.MILVUS_COLLECTION}")
    print()

    if args.init_schema:
        init_schema()
        print()

    result = deps.check_all()
    for c in result["checks"]:
        mark = "OK  " if c["ok"] else "FAIL"
        line = f"[{mark}] {c['name']:<7} {c['target']:<28} {c['ms']:>7.1f} ms"
        if c["ok"]:
            print(f"{line}  {c['detail']}")
        else:
            print(f"{line}  {c['error']}")
            if c["hint"]:
                print(f"{'':>8}→ {c['hint']}")

    print()
    if result["ok"]:
        print(f"全部通过（{result['passed']}/{result['total']}），"
              f"网页版：http://127.0.0.1:8000/health")
        return 0
    print(f"没通过：{result['total'] - result['passed']}/{result['total']} 个依赖不可用。"
          f"演示时应用会自动降级到本地数据，但 source 字段会显示 local。")
    return 1


if __name__ == "__main__":
    sys.exit(main())
