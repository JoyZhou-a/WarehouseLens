"""字符二元组哈希向量。

不依赖外部模型，离线可跑，维度固定。演示够用。
换真实向量模型只需要改这个文件，Milvus 和检索层不动。
"""

from __future__ import annotations

import zlib

import numpy as np

from .. import config


def embed(text: str, dim: int | None = None) -> list[float]:
    d = dim or config.EMBED_DIM
    vec = np.zeros(d, dtype=np.float32)
    clean = "".join(text.split())
    for i in range(len(clean) - 1):
        vec[zlib.crc32(clean[i:i + 2].encode("utf-8")) % d] += 1.0
    norm = float(np.linalg.norm(vec))
    return (vec / norm).tolist() if norm else vec.tolist()
