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


LAST_META: dict = {}


def chat(system: str, user: str, temperature: float = 0.2,
         max_tokens: int | None = None) -> str | None:
    """返回模型文本。不可用时返回 None，调用方自己走模板。

    推理模型（deepseek-flash）把 reasoning_content 也计入 max_tokens：
    预算不够时 finish_reason=length 且正文为空。这里按 1x → 3x → 6x 逐级放大重试，
    仍然为空就把诊断写进 LAST_META，让调用方能给出有意义的失败说明。
    """
    if not available():
        return None
    LAST_META.clear()

    def _call(budget: int | None) -> tuple[str | None, str | None]:
        payload = {
            "model": config.LLM_MODEL,
            "temperature": temperature,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }
        if budget:
            payload["max_tokens"] = budget
        resp = httpx.post(
            f"{config.LLM_BASE_URL.rstrip('/')}/chat/completions",
            headers={"Authorization": f"Bearer {config.LLM_API_KEY}"},
            json=payload,
            timeout=config.LLM_TIMEOUT,
        )
        if resp.status_code != 200:
            print(f"[llm] HTTP {resp.status_code}: {resp.text[:200]}", flush=True)
            LAST_META.update({"status": resp.status_code})
            return None, None
        data = resp.json()
        choice = data["choices"][0]
        usage = data.get("usage") or {}
        LAST_META.update({
            "finish": choice.get("finish_reason"),
            "completion_tokens": usage.get("completion_tokens"),
            "prompt_tokens": usage.get("prompt_tokens"),
            "budget": budget,
            "content_len": len(choice["message"].get("content") or ""),
        })
        return choice["message"].get("content") or "", choice.get("finish_reason")

    try:
        budgets = [max_tokens]
        if max_tokens:
            budgets += [min(16000, max(int(max_tokens * 3), 4000)),
                        min(16000, max(int(max_tokens * 6), 8000))]
        text, finish = "", None
        for budget in budgets:
            text, finish = _call(budget)
            if text is None:
                return None
            if (text or "").strip():
                return text
            if finish != "length":
                return text
            print(f"[llm] 正文被推理吃掉（budget={budget}，"
                  f"completion={LAST_META.get('completion_tokens')}），放大重试", flush=True)
        print(f"[llm] 全部预算用尽仍无正文：completion={LAST_META.get('completion_tokens')}", flush=True)
        return text
    except Exception as exc:
        print(f"[llm] 调用失败 {type(exc).__name__}: {exc}", flush=True)
        LAST_META.update({"error": f"{type(exc).__name__}: {exc}"})
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
