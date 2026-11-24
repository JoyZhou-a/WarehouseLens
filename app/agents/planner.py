"""规划 agent。

负责把自然语言问题变成执行计划：识别意图、抽出物料和数量、决定调用哪几个 agent。
有模型时用模型做分类，没模型时用关键词规则，两条路结果结构一致。
"""

from __future__ import annotations

import re

from .. import llm

SKU_RE = re.compile(r"\b([A-Z]{2,3}-[A-Z0-9]{1,6}-\d{1,4})\b")
CUST_RE = re.compile(r"(C-\d{4}-\d{3})")
QTY_RE = re.compile(r"(\d{2,5})\s*(?:台|件|pcs)")

INTENTS = [
    ("rule_change", ["规则", "生效", "合同", "允许", "变更", "影响", "本体"]),
    ("shortage", ["缺货", "为什么", "原因", "归因", "上升", "异常"]),
    ("commit", ["承诺", "交期", "能不能", "能发", "够不够", "交付", "调拨"]),
]

PLANS = {
    "commit": [
        {"agent": "inventory", "goal": "按本体口径算各仓可用量与可承诺量，判断缺口"},
        {"agent": "graph", "goal": "查替代料限制与在途批次关系"},
        {"agent": "doc", "goal": "检索承诺与效期相关规范作为依据"},
    ],
    "shortage": [
        {"agent": "inventory", "goal": "算各仓可用量与被扣留数量，定位缺口"},
        {"agent": "doc", "goal": "检索扣留通知与延误记录作为归因证据"},
        {"agent": "graph", "goal": "查在途批次与效期，判断后续供应"},
    ],
    "rule_change": [
        {"agent": "doc", "goal": "检索合同条款与保税仓规范，确认规则来源"},
        {"agent": "inventory", "goal": "算规则生效前后的可用量差异"},
        {"agent": "graph", "goal": "查规则与文档的治理关系"},
    ],
}


def parse(question: str) -> dict:
    skus = SKU_RE.findall(question)
    customers = CUST_RE.findall(question)
    qtys = QTY_RE.findall(question)
    intent = "commit"
    for name, words in INTENTS:
        if any(w in question for w in words):
            intent = name
            break
    return {
        "intent": intent,
        "sku_id": skus[0] if skus else "NB-X1C-14",
        "customer": customers[0] if customers else None,
        "demand": int(qtys[0]) if qtys else 500,
        "target_rule": "R3" if "保税" in question else None,
        "order_type": "new_order",
    }


def plan(question: str) -> dict:
    parsed = parse(question)
    steps = PLANS[parsed["intent"]]

    # 有模型时让模型复核意图，只能在三个意图里选，避免它自由发挥
    verdict = llm.chat_json(
        "你是意图分类器。只输出 JSON，字段 intent 取值 commit / shortage / rule_change。",
        f"问题：{question}",
    )
    if verdict and verdict.get("intent") in PLANS:
        parsed["intent"] = verdict["intent"]
        steps = PLANS[parsed["intent"]]
        parsed["classified_by"] = "llm"
    else:
        parsed["classified_by"] = "rule"

    return {"parsed": parsed, "steps": steps}
