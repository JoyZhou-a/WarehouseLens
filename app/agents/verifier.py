"""校验 agent。

不产生新内容，只检查执行结果能不能自洽。检查项：
1. 按口径独立重算可承诺合计，和 agent 给的总数比对
2. 规则挡掉的仓/批次有没有真的被排除，且给出了规则编号
3. 调拨量不超过缺口
4. 结论里的数字是否都能在中间结果里找到来源

有模型时再让模型读一遍答案和证据，没有模型时只做确定性检查。
"""

from __future__ import annotations

import json
import re

from .. import config, llm

NUMBER_RE = re.compile(r"\d+(?:\.\d+)?")


def _collect(obj, nums: set[float], texts: list[str]) -> None:
    if isinstance(obj, dict):
        for v in obj.values():
            _collect(v, nums, texts)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            _collect(v, nums, texts)
    elif isinstance(obj, bool):
        return
    elif isinstance(obj, (int, float)):
        nums.add(float(obj))
    elif isinstance(obj, str):
        texts.append(obj)
        for n in NUMBER_RE.findall(obj):
            nums.add(float(n))


def check(state: dict) -> dict:
    issues: list[dict] = []

    intent = (state.get("parsed") or {}).get("intent")
    if intent == "lookup":
        # 列表类问题不做承诺数量校验，只确认真的取到了行
        payload = (state.get("facts") or {}).get("inventory") or {}
        rows = payload.get("rows")
        if rows is None:
            return {"passed": False, "issues": [
                {"level": "error", "code": "NO_ROWS", "msg": "列表类问题没有取到任何数据"}]}
        return {"passed": True, "issues": [
            {"level": "info", "code": "LOOKUP",
             "msg": f"列表类问题：取到 {len(rows)} 条，不做承诺数量校验"}]}

    inv = state.get("facts", {}).get("inventory") or {}

    if not inv:
        issues.append({"level": "error", "code": "NO_INVENTORY",
                       "msg": "没有库存计算结果，无法支撑任何数量结论"})
        return {"passed": False, "issues": issues}

    # 只有「承诺口径计算」的载荷才有分仓与合计；归因类问题子 agent 往往只取原始行。
    # 拿原始行去做合计校验会重算出 0 并报假错，所以先判形状，再决定校验哪几项。
    whs = inv.get("warehouses") or []
    promise_like = bool(whs) and inv.get("total_promiseable") is not None
    if not promise_like:
        if intent in ("commit", "rule_change"):
            issues.append({"level": "error", "code": "NO_PROMISE_CALC",
                           "msg": "承诺/规则类问题没有可承诺口径计算结果（缺分仓或合计）"})
            return {"passed": False, "issues": issues}
        issues.append({"level": "info", "code": "NO_PROMISE_CALC",
                       "msg": "本问没有做承诺口径计算，跳过合计 / 规则 / 调拨三项校验"})
        return {"passed": True, "issues": issues + _content_checks(state, inv)}

    ranked = sorted(whs, key=lambda w: w["promiseable"], reverse=True)
    primary = next((w for w in ranked if w["promiseable"] > 0), None)

    # 独立重算：直接计入本单的 = 主供仓 + 保税仓（其余仓只能走调拨）
    expected = sum(
        w["promiseable"] for w in whs
        if w["promiseable"] > 0 and (
            (primary and w["warehouse_id"] == primary["warehouse_id"])
            or w["warehouse_type"] == "bonded")
    )
    if expected != inv.get("total_promiseable"):
        issues.append({"level": "error", "code": "SUM_MISMATCH",
                       "msg": f"按口径重算的可承诺合计 {expected}（主供仓+保税仓）与总数 "
                              f"{inv.get('total_promiseable')} 不一致"})

    for w in whs:
        if w["blocked_by"] and w["promiseable"] != 0:
            issues.append({"level": "error", "code": "RULE_NOT_APPLIED",
                           "msg": f"{w['warehouse_name']} 命中规则 {w['blocked_by']} 但仍计入可承诺量"})

    for s in inv.get("supply", []):
        if not s["countable"] and not s["blocked_by"]:
            issues.append({"level": "warn", "code": "SUPPLY_NO_REASON",
                           "msg": f"在途 {s['shipment']} 被排除但没有给出原因"})

    gap = max(0, int(inv.get("demand") or 0) - int(inv.get("total_promiseable") or 0))
    moved = sum(int(t["qty"]) for t in inv.get("transfers", []))
    if moved > gap:
        issues.append({"level": "error", "code": "TRANSFER_OVER_GAP",
                       "msg": f"调拨量 {moved} 超过缺口 {gap}，存在重复计算"})

    issues.extend(_content_checks(state, inv))

    llm_note = _llm_review(state)
    if llm_note:
        issues.append({"level": "info", "code": "LLM_REVIEW",
                       "msg": f"模型复核（仅参考，不参与通过判断）：{llm_note}"})

    passed = not any(i["level"] == "error" for i in issues)
    return {"passed": passed, "issues": issues}


def _content_checks(state: dict, inv: dict) -> list[dict]:
    """与口径无关的两项：有没有文档依据、答案里的数字能不能追到中间结果。"""
    issues: list[dict] = []
    intent = (state.get("parsed") or {}).get("intent")
    if not state.get("evidence") and intent in ("commit", "rule_change"):
        issues.append({"level": "warn", "code": "NO_EVIDENCE", "msg": "结论没有文档依据"})

    answer = state.get("answer") or ""
    if answer:
        known: set[float] = set()
        texts: list[str] = []
        _collect(inv, known, texts)
        _collect(state.get("rule_impact") or {}, known, texts)
        _collect(state.get("evidence") or [], known, texts)
        _collect((state.get("facts") or {}).get("graph") or {}, known, texts)
        mentioned = {float(n) for n in NUMBER_RE.findall(answer)}
        stray = {n for n in mentioned
                 if n > 100 and not any(abs(n - k) < 1e-6 for k in known)}
        if stray:
            issues.append({"level": "warn", "code": "NUMBER_NOT_TRACED",
                           "msg": f"答案里这些数字在中间结果里找不到来源：{sorted(stray)}"})
    return issues


def _llm_review(state: dict) -> str | None:
    """模型复核只作参考，不参与通过与否的判断。

    要给出聚合口径，否则它会按「各仓之和」重算，把调拨源当成可直接承诺的仓，
    得出「420 应该是 510」这类我们自己口径下的假问题。
    """
    if not config.LLM_REVIEW:
        return None
    if not llm.available():
        return None
    inv = state.get("facts", {}).get("inventory") or {}
    digest = {
        "可承诺合计": inv.get("total_promiseable"),
        "需求": inv.get("demand"),
        "缺口": inv.get("gap"),
        "调拨方案后可用": inv.get("after_transfer"),
        "调拨": [f"{t['from']} {t['qty']} 台" for t in inv.get("transfers", [])],
        "各仓": [{"仓": w["warehouse_name"], "可承诺": w["promiseable"],
                  "被规则排除前的可承诺": w["raw_promiseable"],
                  "命中规则": w["blocked_by"]} for w in inv.get("warehouses", [])],
        "在途": [{"单号": s["shipment"], "数量": s["qty"], "计入": s["countable"],
                  "命中规则": s["blocked_by"]} for s in inv.get("supply", [])],
    }
    if state.get("rule_impact"):
        digest["规则影响"] = state["rule_impact"]

    system = (
        "你是供应链数据校验员。判断「答案」是否与「数据」矛盾，只回答一句话。\n"
        "口径（必须按这个理解，不要自己重算合计）：\n"
        "- 可承诺合计 = 主供仓（可承诺量最大的仓）+ 保税仓（未被规则排除时）；\n"
        "- 其他成品仓只作为调拨源，不计入可承诺合计，出现在「调拨」里；\n"
        "- 被规则排除的仓可承诺为 0；\n"
        "- 命中规则的批次不计入可用量。\n"
        "只标记确实矛盾的数字或归属；不要推测答案没说过的话，不要按各仓之和重算。\n"
        "没有矛盾就只回答 OK。"
    )
    user = (f"数据：{json.dumps(digest, ensure_ascii=False)}\n\n"
            f"答案：{state.get('answer', '')}")
    verdict = llm.chat(system, user, temperature=0.0)
    if not verdict or verdict.strip().upper().startswith("OK"):
        return None
    return verdict.strip()[:300]
