"""Redis 共享状态。

agent 之间不直接互相调用，全部读写这一份 state。
Redis 连不上时退化成进程内字典，保证演示不因为缓存挂掉。
"""

from __future__ import annotations

import json
import time
from typing import Any

from .. import config

TTL = 3600
_memory: dict[str, dict] = {}
_redis = None
_redis_failed = False


def _client():
    global _redis, _redis_failed
    if _redis_failed:
        return None
    if _redis is None:
        import redis

        _redis = redis.Redis(decode_responses=True, **config.REDIS)
        try:
            _redis.ping()
        except Exception:
            _redis_failed = True
            _redis = None
    return _redis


def backend() -> str:
    return "redis" if _client() is not None else "memory"


def save(task_id: str, state: dict) -> None:
    cli = _client()
    if cli is None:
        _memory[task_id] = state
        return
    cli.setex(f"state:{task_id}", TTL, json.dumps(state, ensure_ascii=False))


def load(task_id: str) -> dict | None:
    cli = _client()
    if cli is None:
        return _memory.get(task_id)
    raw = cli.get(f"state:{task_id}")
    return json.loads(raw) if raw else None


def trace(task_id: str, agent: str, tool: str, args: Any, ok: bool,
          result: Any = None, ms: int = 0) -> None:
    """每一步都留痕。前端靠这个画编排过程。"""
    event = {
        "ts": time.strftime("%H:%M:%S"),
        "agent": agent,
        "tool": tool,
        "args": args,
        "ok": ok,
        "result": result,
        "ms": ms,
    }
    cli = _client()
    if cli is None:
        _memory.setdefault(f"trace:{task_id}", {}).setdefault("events", []).append(event)
        return
    cli.rpush(f"trace:{task_id}", json.dumps(event, ensure_ascii=False, default=str))
    cli.expire(f"trace:{task_id}", TTL)


def events(task_id: str) -> list[dict]:
    cli = _client()
    if cli is None:
        return _memory.get(f"trace:{task_id}", {}).get("events", [])
    return [json.loads(x) for x in cli.lrange(f"trace:{task_id}", 0, -1)]
