"""单管理员登录。

演示用的最小实现：账号密码来自环境变量，登录成功后下发一个 HMAC 签名的
无状态 token（放 httpOnly cookie），服务端不存 session 表。够用且不引入依赖。

设计上的取舍写在 README：真实系统应该用 SSO / OAuth，密码要哈希存储。
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time

from . import config

COOKIE = "wms_auth"


def _sign(payload: bytes) -> str:
    digest = hmac.new(config.AUTH_SECRET.encode("utf-8"), payload, hashlib.sha256).digest()
    return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")


def issue(user: str) -> str:
    """生成 token：base64(json) + '.' + 签名。"""
    body = json.dumps({"u": user, "exp": int(time.time()) + config.AUTH_TTL},
                      separators=(",", ":")).encode("utf-8")
    token = base64.urlsafe_b64encode(body).decode("ascii").rstrip("=")
    return f"{token}.{_sign(body)}"


def verify(token: str | None) -> dict | None:
    if not token or "." not in token:
        return None
    body_b64, signature = token.rsplit(".", 1)
    padding = "=" * (-len(body_b64) % 4)
    try:
        body = base64.urlsafe_b64decode(body_b64 + padding)
    except Exception:
        return None
    if not hmac.compare_digest(signature, _sign(body)):
        return None
    try:
        data = json.loads(body)
    except Exception:
        return None
    if int(data.get("exp", 0)) < time.time():
        return None
    return data


def check_password(user: str, password: str) -> bool:
    """常数时间比较，避免用字符串比较泄露长度/前缀信息。"""
    user_ok = hmac.compare_digest(user.strip(), config.ADMIN_USER)
    pass_ok = hmac.compare_digest(password, config.ADMIN_PASSWORD)
    return user_ok and pass_ok
