"""规划 agent。

把自然语言问题变成执行计划：
1. 结构抽取：物料、数量、客户、时间窗；
2. 意图判定：承诺 / 缺货归因 / 规则变更 / 列表查询；
3. 有模型时用模型复核意图（标签集合已扩到 4 个），没有就用关键词。

注意：这里的「计划」只是策略提示，真正执行多 agent 编排时由主 agent 决定派发。
"""

from __future__ import annotations

import re

from .. import llm

# 物料号形态：NB-X1C-14、NB-T14-G5、SP-MB-14 都要能认出来。
# 最后一段不能限定纯数字——T14 的后缀是 G5，限定 \d 会静默回退到默认物料，
# 拿错物料的数据去回答，比报错更糟。
SKU_RE = re.compile(r"\b([A-Z]{2,3}-[A-Z0-9]{1,6}-[A-Z0-9]{1,6})\b")
CUST_RE = re.compile(r"(C-\d{4}-\d{3})")
SUPPLIER_RE = re.compile(r"\b(S-\d{4})\b")
QTY_RE = re.compile(r"(\d{2,5})\s*(?:台|件|pcs)")
WINDOW_RE = re.compile(r"(?:未来|接下来|这两天|这周|本周|下周|今天|明天)?\s*(\d{1,2})\s*(?:个)?(?:天|日)")
# 「收紧到 20 天」「放宽到 45 天」这类阈值必须解析出来，
# 否则阈值由模型自己填，同一问题的答案每次都不一样。
EXPIRY_RE = re.compile(r"(?:收紧|放宽|调整|改)[^0-9]{0,6}(\d{1,3})\s*天")
EXPIRY_RE2 = re.compile(r"(?:效期|临期|线|阈值)[^0-9]{0,6}(\d{1,3})\s*天")

INTENTS = [
    ("rule_change", ["规则", "生效", "合同", "允许", "变更", "影响", "本体", "放开", "开启"]),
    ("supplier", ["供应商", "采购单", "供货商"]),
    ("shortage", ["缺货", "为什么", "原因", "归因", "上升", "异常", "延误", "扣留"]),
    ("lookup", ["哪些", "有哪些", "清单", "列表", "要发", "到仓", "快过期", "临期", "多少单"]),
    ("commit", ["承诺", "交期", "能不能", "能发", "够不够", "交付", "调拨"]),
]

PLANS = {
    "supplier": [
        {"agent": "graph", "goal": "沿供应商→采购单→批次→物料→订单→客户查链路"},
    ],
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
    "lookup": [
        {"agent": "inventory", "goal": "按时间窗列出订单/在途/临期批次"},
        {"agent": "doc", "goal": "检索相关作业规范作为口径依据"},
    ],
}


def _window_days(question: str) -> int:
    m = WINDOW_RE.search(question)
    if m:
        return max(1, min(30, int(m.group(1))))
    if "今天" in question:
        return 1
    if any(k in question for k in ("这周", "本周", "下周")):
        return 7
    return 7


def _lookup_kind(question: str) -> str:
    if any(k in question for k in ("过期", "临期", "效期")):
        return "batches"
    if any(k in question for k in ("到仓", "在途", "到货")):
        return "shipments"
    return "orders"


def _target_rule(question: str) -> str | None:
    if "保税" in question:
        return "R3"
    if any(k in question for k in ("临期", "效期", "过期")):
        return "R1"
    if any(k in question for k in ("备件", "售后")):
        return "R4"
    if "清关" in question:
        return "R2"
    return None


def _expiry_threshold(question: str) -> int | None:
    for pattern in (EXPIRY_RE, EXPIRY_RE2):
        m = pattern.search(question)
        if m:
            value = int(m.group(1))
            if 1 <= value <= 365:
                return value
    return None


def parse(question: str) -> dict:
    skus = SKU_RE.findall(question)
    customers = CUST_RE.findall(question)
    suppliers = SUPPLIER_RE.findall(question)
    qtys = QTY_RE.findall(question)
    intent = "commit"
    for name, words in INTENTS:
        if any(w in question for w in words):
            intent = name
            break
    return {
        "intent": intent,
        "sku_id": skus[0] if skus else None,
        "customer": customers[0] if customers else None,
        "supplier_id": suppliers[0] if suppliers else None,
        "demand": int(qtys[0]) if qtys else None,
        "target_rule": _target_rule(question),
        "expiry_threshold": _expiry_threshold(question),
        "order_type": "new_order",
        "window_days": _window_days(question),
        "lookup_kind": _lookup_kind(question) if intent == "lookup" else None,
    }


def plan(question: str) -> dict:
    parsed = parse(question)
    steps = PLANS[parsed["intent"]]

    # 有模型时让模型复核意图，只能在四个意图里选，避免它自由发挥
    verdict = llm.chat_json(
        "你是意图分类器。只输出 JSON，字段 intent 取值 "
        "commit / shortage / rule_change / lookup。"
        "lookup 表示「列清单」类问题（哪些货要发、哪些批次快过期、哪些在途会到）。",
        f"问题：{question}",
    )
    if verdict and verdict.get("intent") in PLANS:
        parsed["intent"] = verdict["intent"]
        if parsed["intent"] == "lookup":
            parsed["lookup_kind"] = _lookup_kind(question)
        steps = PLANS[parsed["intent"]]
        parsed["classified_by"] = "llm"
    else:
        parsed["classified_by"] = "rule"

    return {"parsed": parsed, "steps": steps}
