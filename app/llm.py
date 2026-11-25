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


def chat(system: str, user: str, temperature: float = 0.2,
         max_tokens: int | None = None) -> str | None:
    """返回模型文本。不可用时返回 None，调用方自己走模板。"""
    if not available():
        return None
    payload = {
        "model": config.LLM_MODEL,
        "temperature": temperature,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    }
    if max_tokens:
        # 这是推理模型：reasoning_content 也计入 max_tokens，
        # 给太小会把正文截断（finish_reason=length），所以默认留足预算。
        payload["max_tokens"] = max_tokens
    try:
        resp = httpx.post(
            f"{config.LLM_BASE_URL.rstrip('/')}/chat/completions",
            headers={"Authorization": f"Bearer {config.LLM_API_KEY}"},
            json=payload,
            timeout=config.LLM_TIMEOUT,
        )
        if resp.status_code != 200:
            # 静默失败会让演示变成「模型没参与」，必须打到日志里
            print(f"[llm] HTTP {resp.status_code}: {resp.text[:200]}", flush=True)
            return None
        data = resp.json()
        choice = data["choices"][0]
        if choice.get("finish_reason") == "length":
            print(f"[llm] 输出被 max_tokens 截断（completion={data.get('usage', {}).get('completion_tokens')}）",
                  flush=True)
        return choice["message"]["content"]
    except Exception as exc:
        print(f"[llm] 调用失败 {type(exc).__name__}: {exc}", flush=True)
        return None


def chat_json(system: str, user: str, max_tokens: int | None = None) -> dict | None:
    text = chat(system, user, max_tokens=max_tokens)
    if not text:
        return None
    match = re.search(r"\{.*\}", text, re.S)
    if not match:
        return None
    try:
        return json.loads(match.group(0))
    except json.JSONDecodeError:
        return None
