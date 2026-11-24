"""模型调用。

两种模式：
live   真的调 API，用于现场问答。
replay 不调 API，直接用确定性模板。断网、没额度、超时都不会让演示挂掉。

数字永远由工具层算出来再交给模型，模型只负责组织语言和做分类。
"""

from __future__ import annotations

import json
import re

import httpx

from . import config


def mode() -> str:
    return config.LLM_MODE


def available() -> bool:
    return config.LLM_MODE == "live" and bool(config.LLM_API_KEY)


def status() -> dict:
    return {
        "mode": config.LLM_MODE,
        "model": config.LLM_MODEL if available() else "template",
        "live": available(),
    }


def chat(system: str, user: str, temperature: float = 0.2) -> str | None:
    """返回模型文本。不可用时返回 None，调用方自己走模板。"""
    if not available():
        return None
    try:
        resp = httpx.post(
            f"{config.LLM_BASE_URL.rstrip('/')}/chat/completions",
            headers={"Authorization": f"Bearer {config.LLM_API_KEY}"},
            json={
                "model": config.LLM_MODEL,
                "temperature": temperature,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
            },
            timeout=config.LLM_TIMEOUT,
        )
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"]
    except Exception:
        return None


def chat_json(system: str, user: str) -> dict | None:
    text = chat(system, user)
    if not text:
        return None
    match = re.search(r"\{.*\}", text, re.S)
    if not match:
        return None
    try:
        return json.loads(match.group(0))
    except json.JSONDecodeError:
        return None
