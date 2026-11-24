"""校验 agent。

不产生新内容，只检查执行结果能不能自洽。检查项：
1. 每个关键数字有没有来源
2. 被规则挡掉的批次有没有给出规则编号
3. 各仓可承诺量加起来是否等于总数
4. 结论里的数字是否都出现在中间结果里

有模型时再让模型读一遍答案和证据，没有模型时只做确定性检查。
"""

from __future__ import annotations

import re

from .. import llm

NUMBER_RE = re.compile(r"\b(\d{2,5})\b")


def check(state: dict) -> dict:
    issues: list[dict] = []
    inv = state.get("facts", {}).get("inventory") or {}

    if not inv:
        issues.append({"level": "error", "code": "NO_INVENTORY",
                       "msg": "没有库存计算结果，无法支撑任何数量结论"})
        return {"passed": False, "issues": issues}

    whs = inv.get("warehouses", [])
    total_independent = sum(w["promiseable"] for w in whs)
    if total_independent != inv.get("total_promiseable"):
        issues.append({"level": "error", "code": "SUM_MISMATCH",
                       "msg": f"各仓可承诺量之和 {total_independent} 与总数 {inv.get('total_promiseable')} 不一致"})

    for w in whs:
        if w["blocked_by"] and w["promiseable"] != 0:
            issues.append({"level": "error", "code": "RULE_NOT_APPLIED",
                           "msg": f"{w['warehouse_name']} 命中规则 {w['blocked_by']} 但仍计入可承诺量"})

    for s in inv.get("supply", []):
        if not s["countable"] and not s["blocked_by"]:
            issues.append({"level": "warn", "code": "SUPPLY_NO_REASON",
                           "msg": f"在途 {s['shipment']} 被排除但没有给出原因"})

    if not state.get("evidence") and state.get("parsed", {}).get("intent") in ("commit", "rule_change"):
        issues.append({"level": "warn", "code": "NO_EVIDENCE",
                       "msg": "结论没有文档依据"})

    answer = state.get("answer") or ""
    if answer:
        mentioned = {int(n) for n in NUMBER_RE.findall(answer)}
        known = {int(inv.get("total_promiseable", 0)), int(inv.get("demand", 0))}
        known |= {int(w["promiseable"]) for w in whs}
        known |= {int(t["qty"]) for t in inv.get("transfers", [])}
        stray = {n for n in mentioned if n > 100 and n not in known}
        if stray:
            issues.append({"level": "warn", "code": "NUMBER_NOT_TRACED",
                           "msg": f"答案里这些数字在中间结果里找不到来源：{sorted(stray)}"})

    llm_note = _llm_review(state)
    if llm_note:
        issues.append({"level": "info", "code": "LLM_REVIEW", "msg": llm_note})

    passed = not any(i["level"] == "error" for i in issues)
    return {"passed": passed, "issues": issues}


def _llm_review(state: dict) -> str | None:
    if not llm.available():
        return None
    fact_text = str(state.get("facts", {}))[:2000]
    answer = state.get("answer", "")
    verdict = llm.chat(
        "你是供应链数据校验员。只回答一句话，指出答案里与数据不一致的地方；没有就回答 OK。",
        f"数据：{fact_text}\n答案：{answer}",
    )
    if not verdict or verdict.strip().upper().startswith("OK"):
        return None
    return verdict.strip()[:300]
