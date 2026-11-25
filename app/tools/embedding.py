"""向量化。

两个 provider：
- hash：字符二元组哈希，512 维，不依赖外部服务，断网可用；
- bge：租的 BAAI/bge-m3，1024 维，真正按语义编码。

两者维度不同、语义空间不同，各自写进自己的 collection，查询时必须与索引用同一个 provider，
不能一边用 bge 建库、一边用 hash 查库——那样余弦相似度没有意义。
"""

from __future__ import annotations

import zlib

import httpx
import numpy as np

from .. import config


def embed_hash(text: str, dim: int = 512) -> list[float]:
    vec = np.zeros(dim, dtype=np.float32)
    clean = "".join(text.split())
    for i in range(len(clean) - 1):
        vec[zlib.crc32(clean[i:i + 2].encode("utf-8")) % dim] += 1.0
    norm = float(np.linalg.norm(vec))
    return (vec / norm).tolist() if norm else vec.tolist()


def embed_bge(text: str) -> list[float]:
    """调硅基流动的 bge-m3。失败时抛异常，由调用方决定是切换还是降级。"""
    resp = httpx.post(
        f"{config.EMBED_BASE_URL.rstrip('/')}/embeddings",
        headers={"Authorization": f"Bearer {config.EMBED_API_KEY}"},
        json={"model": config.EMBED_MODEL, "input": [text]},
        timeout=20,
    )
    resp.raise_for_status()
    data = resp.json()["data"]
    if not data:
        raise RuntimeError("embedding 接口返回空结果")
    return data[0]["embedding"]


def embed(text: str, provider: str | None = None) -> list[float]:
    p = provider or config.ACTIVE_EMBED
    if p == "bge":
        return embed_bge(text)
    # hash 的维度永远固定，不能跟着激活 provider 变，否则和 512 维的索引对不上
    return embed_hash(text, config.EMBED_DIM_HASH)


def dim_of(provider: str) -> int:
    return config.EMBED_DIM_BGE if provider == "bge" else 512
