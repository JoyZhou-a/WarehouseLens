"""会话记忆：把每轮问答存在 Redis，供前端展示和后续追问使用。

结构：
    chat:{session_id}         list，每条是一个 {role, content, task_id, ts}
    chat:sessions             zset，score=最后更新时间，用于左侧会话列表
"""

from __future__ import annotations

import json
import time
import uuid

from .. import config

TTL = 86400
INDEX_KEY = "chat:sessions"


def _client():
    import redis

    return redis.Redis(decode_responses=True, **config.REDIS)


def new_session() -> str:
    return uuid.uuid4().hex[:10]


def append(session_id: str, role: str, content: str, meta: dict | None = None) -> None:
    if not session_id:
        return
    r = _client()
    msg = {"role": role, "content": content, "ts": time.strftime("%H:%M:%S"),
           "meta": meta or {}}
    key = f"chat:{session_id}"
    r.rpush(key, json.dumps(msg, ensure_ascii=False))
    r.expire(key, TTL)
    r.zadd(INDEX_KEY, {session_id: time.time()})


def messages(session_id: str, limit: int = 200) -> list[dict]:
    if not session_id:
        return []
    r = _client()
    raw = r.lrange(f"chat:{session_id}", 0, limit - 1)
    out = []
    for x in raw:
        try:
            out.append(json.loads(x))
        except json.JSONDecodeError:
            continue
    return out


def history(session_id: str, turns: int = 3) -> list[dict]:
    """最近 N 轮对话（一问一答算一轮），给主 agent 做指代消解用。"""
    msgs = messages(session_id)
    return msgs[-turns * 2:] if msgs else []


def clear(session_id: str) -> None:
    r = _client()
    r.delete(f"chat:{session_id}")
    r.zrem(INDEX_KEY, session_id)


def list_sessions(limit: int = 20) -> list[dict]:
    r = _client()
    ids = r.zrevrange(INDEX_KEY, 0, limit - 1)
    out = []
    for sid in ids:
        msgs = messages(sid, 50)
        title = next((m["content"] for m in msgs if m["role"] == "user"), "（空会话）")
        out.append({"id": sid, "title": title[:40], "count": len(msgs),
                    "updated": r.zscore(INDEX_KEY, sid) or 0})
    return out
