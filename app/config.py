"""配置读取。所有敏感信息走 .env，不进代码。"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")


def env(key: str, default: str | None = None) -> str:
    value = os.getenv(key, default)
    if value is None:
        raise KeyError(f"缺少环境变量 {key}，检查 .env")
    return value


def env_int(key: str, default: int) -> int:
    try:
        return int(os.getenv(key, str(default)))
    except ValueError:
        return default


ONTOLOGY_PATH = ROOT / env("ONTOLOGY_PATH", "ontology/wms.yaml")

MYSQL = {
    "host": env("MYSQL_HOST", "127.0.0.1"),
    "port": env_int("MYSQL_PORT", 3306),
    "user": env("MYSQL_USER", "root"),
    "password": env("MYSQL_PASSWORD", "root123456"),
    "database": env("MYSQL_DATABASE", "wms"),
}

NEO4J_URI = env("NEO4J_URI", "bolt://127.0.0.1:7687")
NEO4J_AUTH = (env("NEO4J_USER", "neo4j"), env("NEO4J_PASSWORD", "neo4j123456"))

REDIS = {
    "host": env("REDIS_HOST", "127.0.0.1"),
    "port": env_int("REDIS_PORT", 6379),
    "db": env_int("REDIS_DB", 0),
}

MILVUS_HOST = env("MILVUS_HOST", "127.0.0.1")
MILVUS_PORT = env("MILVUS_PORT", "19530")

# 向量化：hash 是本地字符二元组哈希（离线可用），bge 是租的 BAAI/bge-m3（1024 维）。
# 两个 provider 的维度不同，必须各自一个 collection，查询时不能混用。
EMBED_PROVIDER = env("EMBED_PROVIDER", "hash").strip().lower()      # hash | bge
EMBED_API_KEY = env("EMBED_API_KEY", "")
EMBED_BASE_URL = env("EMBED_BASE_URL", "https://api.siliconflow.cn/v1")
EMBED_MODEL = env("EMBED_MODEL", "BAAI/bge-m3")
EMBED_DIM_BGE = env_int("EMBED_DIM_BGE", 1024)

# 配了 key 且显式选了 bge 才走 bge，否则一直用 hash，不会因为少个 key 就起不来
ACTIVE_EMBED = "bge" if (EMBED_PROVIDER == "bge" and EMBED_API_KEY) else "hash"
EMBED_DIM_HASH = env_int("EMBED_DIM", 512)
# 当前激活 provider 的维度；hash 分支自己用 EMBED_DIM_HASH，不要拿这个
EMBED_DIM = EMBED_DIM_BGE if ACTIVE_EMBED == "bge" else EMBED_DIM_HASH

_COLLECTION_BASE = env("MILVUS_COLLECTION_BASE", "wms_docs")
COLLECTION_BY_PROVIDER = {
    "bge": f"{_COLLECTION_BASE}_bge",
    "hash": f"{_COLLECTION_BASE}_hash",
}
MILVUS_COLLECTION = COLLECTION_BY_PROVIDER[ACTIVE_EMBED]

# 重排：召回 top_k 之后用 bge-reranker 重排。默认关，先量收益再决定开不开。
RERANK_ENABLED = env("RERANK_ENABLED", "off").strip().lower() in ("1", "true", "on", "yes")
RERANK_MODEL = env("RERANK_MODEL", "BAAI/bge-reranker-v2-m3")

LLM_MODE = env("LLM_MODE", "replay")
LLM_BASE_URL = env("LLM_BASE_URL", "https://api.deepseek.com/v1")
LLM_API_KEY = env("LLM_API_KEY", "")
LLM_MODEL = env("LLM_MODEL", "deepseek-flash")
LLM_TIMEOUT = env_int("LLM_TIMEOUT", 60)

# 是否让模型参与校验复核。默认关闭：pass/fail 只由确定性检查决定，
# 模型复核既慢（每次多 7-10 秒）又会按自己的口径误判。
LLM_REVIEW = env("LLM_REVIEW", "off").strip().lower() in ("1", "true", "on", "yes")

# ---------------------------------------------------------------- 登录

# 演示用的单管理员账号。密码和签名密钥都走环境变量，代码里只留占位默认值，
# 部署时用 /etc/wms/wms.env 覆盖，仓库里看不到真实密码。
ADMIN_USER = env("ADMIN_USER", "admin")
ADMIN_PASSWORD = env("ADMIN_PASSWORD", "admin12345")
AUTH_SECRET = env("AUTH_SECRET", "dev-only-please-change-me")
AUTH_TTL = env_int("AUTH_TTL", 8 * 3600)
