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
MILVUS_COLLECTION = env("MILVUS_COLLECTION", "wms_docs")
EMBED_DIM = env_int("EMBED_DIM", 512)

LLM_MODE = env("LLM_MODE", "replay")
LLM_BASE_URL = env("LLM_BASE_URL", "https://api.deepseek.com/v1")
LLM_API_KEY = env("LLM_API_KEY", "")
LLM_MODEL = env("LLM_MODEL", "deepseek-chat")
LLM_TIMEOUT = env_int("LLM_TIMEOUT", 60)
