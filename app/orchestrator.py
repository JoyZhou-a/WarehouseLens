"""编排层。

流程：规划 -> 并行执行检索类 agent -> 组装答案 -> 独立校验 -> 落状态。
state 存 Redis，trace 存 Redis 列表，前端轮询拿来做编排可视化。
"""

from __future__ import annotations

import time
import uuid
from concurrent.futures import ThreadPoolExecutor

from . import llm
from .agents import doc as doc_agent
from .agents import graph as graph_agent
from .agents import inventory as inv_agent
from .agents import planner, verifier
from .ontology import get_ontology
from .tools import state as state_tool

PARALLEL_AGENTS = {"inventory", "graph", "doc"}


def new_task() -> str:
    return uuid.uuid4().hex[:12]


def ask(question: str, mode: str = "system") -> dict:
    task_id = new_task()
    onto = get_ontology()
    st = {
        "task_id": task_id,
        "question": question,
        "mode": mode,
        "ontology_version": onto.version,
        "llm": llm.status(),
        "started_at": time.strftime("%H:%M:%S"),
        "facts": {},
        "evidence": [],
    }
    started = time.time()

    if mode == "baseline":
        _run_baseline(st, question, onto)
    else:
        _run_system(st, question, onto)

    st["elapsed_ms"] = int((time.time() - started) * 1000)
    st["trace"] = state_tool.events(task_id)
    state_tool.save(task_id, st)
    return st


# ------------------------------------------------------------ 系统路径

def _run_system(st: dict, question: str, onto) -> None:
    task_id = st["task_id"]

    t0 = time.time()
    plan = planner.plan(question)
    state_tool.trace(task_id, "planner", "plan", {"question": question},
                     True, plan, int((time.time() - t0) * 1000))
    st["parsed"] = plan["parsed"]
    st["plan"] = plan["steps"]

    parsed = plan["parsed"]
    facts: dict = {}

    # 检索类 agent 并行执行
    jobs = {}
    with ThreadPoolExecutor(max_workers=3) as pool:
        for step in plan["steps"]:
            name = step["agent"]
            if name == "inventory":
                jobs[name] = pool.submit(inv_agent.run, onto, parsed)
            elif name == "graph":
                jobs[name] = pool.submit(graph_agent.run, parsed)
            elif name == "doc":
                jobs[name] = pool.submit(doc_agent.run, parsed, question)

    for step in plan["steps"]:
        name = step["agent"]
        if name not in jobs:
            state_tool.trace(task_id, name, "skip", {}, True, {"msg": "未实现"}, 0)
            facts[name] = {"implemented": False, "note": "该 agent 预留，当前版本未实现"}
            continue
        t0 = time.time()
        try:
            result = jobs[name].result()
            state_tool.trace(task_id, name, step["goal"], {"sku": parsed.get("sku_id")},
                             True, _summarize(name, result), int((time.time() - t0) * 1000))
        except Exception as exc:
            result = {"error": f"{type(exc).__name__}: {exc}"}
            state_tool.trace(task_id, name, step["goal"], {}, False, result, int((time.time() - t0) * 1000))
        facts[name] = result

    st["facts"] = facts
    st["evidence"] = facts.get("doc", {}).get("evidence", [])

    if parsed["intent"] == "rule_change" and parsed.get("target_rule"):
        st["rule_impact"] = _rule_impact(onto, parsed)

    st["answer"] = _compose(st, onto)

    t0 = time.time()
    verdict = verifier.check(st)
    state_tool.trace(task_id, "verifier", "check", {"items": 4}, verdict["passed"],
                     {"passed": verdict["passed"], "issues": len(verdict["issues"])},
                     int((time.time() - t0) * 1000))
    st["verification"] = verdict


def _summarize(name: str, result: dict) -> dict:
    if name == "inventory":
        return {
            "total_promiseable": result.get("total_promiseable"),
            "demand": result.get("demand"),
            "gap": result.get("gap"),
            "blocked": [f"{s['shipment']}:{s['blocked_by']}" for s in result.get("supply", []) if not s["countable"]],
        }
    if name == "graph":
        return {"substitutes": len(result.get("substitutes", [])),
                "paths": len(result.get("substitute_paths", [])),
                "inbound": len(result.get("inbound", []))}
    if name == "doc":
        return {"hits": len(result.get("evidence", [])), "backend": result.get("backend")}
    return {}


def _rule_impact(onto, parsed: dict) -> dict:
    """把目标规则临时关掉再算一遍，给出前后差异。内存里翻转，不改文件。"""
    rule_id = parsed["target_rule"]
    before = inv_agent.run(onto, parsed)

    original = next((r for r in onto.rules if r["id"] == rule_id), None)
    if not original:
        return {"rule": rule_id, "found": False}
    original["enabled"] = False
    try:
        after = inv_agent.run(onto, parsed)
    finally:
        original["enabled"] = True

    delta = after["total_promiseable"] - before["total_promiseable"]
    return {
        "rule": rule_id,
        "label": original.get("label"),
        "found": True,
        "before": before["total_promiseable"],
        "after": after["total_promiseable"],
        "delta": delta,
        "changed_warehouses": [
            {"warehouse": w["warehouse_name"], "promiseable": w["promiseable"]}
            for w in after["warehouses"]
            if w["promiseable"] != next(
                (x["promiseable"] for x in before["warehouses"] if x["warehouse_id"] == w["warehouse_id"]), 0)
        ],
    }


# ------------------------------------------------------------ 答案组装

def _compose(st: dict, onto) -> str:
    parsed = st["parsed"]
    inv = st.get("facts", {}).get("inventory", {})
    intent = parsed["intent"]

    if intent == "baseline" or st["mode"] == "baseline":
        return st.get("answer", "")

    if intent == "commit":
        return _compose_commit(st, inv, onto)
    if intent == "shortage":
        return _compose_shortage(st, inv)
    if intent == "rule_change":
        return _compose_rule(st, inv)
    return "已执行检索，未匹配到可用的回答模板。"


def _lines_inv(inv: dict) -> str:
    parts = []
    for w in inv.get("warehouses", []):
        head = f"{w['warehouse_name']} 可承诺 {w['promiseable']} 台"
        if w["blocked_by"]:
            parts.append(f"{head}（被 {'、'.join(w['blocked_by'])} 排除，原始可承诺 {w['raw_promiseable']} 台）")
        else:
            parts.append(f"{head}（在库 {w['on_hand']}，已分配 {w['allocated']}，"
                         f"扣留 {w['hold_qty']}，已到在途 {w['in_transit_arrived']}）")
    return "；".join(parts)


def _blocked_supply(inv: dict) -> str:
    items = [s for s in inv.get("supply", []) if not s["countable"]]
    if not items:
        return ""
    out = []
    for s in items:
        out.append(f"在途 {s['shipment']} 的 {s['qty']} 台不计入（批次 {s['batch']} 剩余效期 "
                   f"{s['expiry_days']} 天，命中 {'、'.join(s['blocked_by'])}：{s['reason'][0] if s['reason'] else ''}）")
    return "；".join(out)


def _compose_commit(st: dict, inv: dict, onto) -> str:
    if not inv:
        return "库存计算失败，无法给出承诺结论。"
    transfers = inv.get("transfers", [])
    if inv["total_promiseable"] >= inv["demand"]:
        verdict = "可以承诺"
    elif inv["total_promiseable"] + sum(t["qty"] for t in transfers) >= inv["demand"]:
        verdict = "可以通过跨仓调拨承诺"
    else:
        verdict = "无法全额承诺"

    lines = [
        f"结论：{verdict}。{inv['sku_name']}（{inv['sku_id']}）可承诺合计 {inv['total_promiseable']} 台，"
        f"本次需求 {inv['demand']} 台，缺口 {inv['gap']} 台。",
        f"分仓口径：{_lines_inv(inv)}。",
    ]
    blocked = _blocked_supply(inv)
    if blocked:
        lines.append(f"被规则排除的供应：{blocked}。")

    if transfers:
        plan = "，".join(f"{t['from']} 调拨 {t['qty']} 台（{t['lead_time_days']} 天）" for t in transfers)
        lines.append(f"补充方案：{plan}。")
        total_with = inv["total_promiseable"] + sum(t["qty"] for t in transfers)
        lines.append(f"方案后可用于本单数量 {total_with} 台，覆盖需求。")

    sources = [f"{e['doc_id']} 第 {e['chunk_no'] + 1} 段（相似度 {e['score']}）" for e in st.get("evidence", [])[:3]]
    if sources:
        lines.append(f"依据：{'；'.join(sources)}。")
    lines.append(f"口径版本：ontology {st['ontology_version']}，可用量公式 {onto.formula_text('available_inventory')}。")
    return "\n".join(lines)


def _compose_shortage(st: dict, inv: dict) -> str:
    whs = [w for w in inv.get("warehouses", []) if w["hold_qty"] > 0 or w["blocked_by"]]
    lines = ["结论：缺货由两类原因叠加，一部分是被扣留的库存，一部分是在途未按时到货。"]
    for w in inv.get("warehouses", []):
        if w["warehouse_name"].startswith("合肥"):
            lines.append(
                f"合肥仓：在库 {w['on_hand']} 台，其中 {w['hold_qty']} 台被质量扣留不计入可用量，"
                f"实际可承诺 {w['promiseable']} 台。")
    for s in inv.get("supply", []):
        if s["warehouse_id"] in ("WH-SH", "WH-HF"):
            status = "计入" if s["countable"] else f"不计入，命中 {'、'.join(s['blocked_by'])}"
            lines.append(f"在途 {s['shipment']}：{s['qty']} 台，预计 {s['eta_days']} 天后到仓，"
                         f"批次剩余效期 {s['expiry_days']} 天，{status}。")
    ev = st.get("evidence", [])[:3]
    if ev:
        lines.append("文档证据：")
        for e in ev:
            snippet = e["text"].replace("\n", " ")[:120]
            lines.append(f"- {e['doc_id']}（{e['score']}）：{snippet}...")
    lines.append("建议：对合肥仓恢复的产能优先补 14 寸，同时把在途延误的批次改走现货调拨。")
    return "\n".join(lines)


def _compose_rule(st: dict, inv: dict) -> str:
    impact = st.get("rule_impact") or {}
    ev = st.get("evidence", [])[:2]
    lines = []
    if impact.get("found"):
        lines.append(f"结论：规则 {impact['rule']}（{impact['label']}）是当前保税仓不计入承诺的唯一原因。")
        lines.append(f"生效前总可承诺 {impact['before']} 台，关闭该规则后 {impact['after']} 台，"
                     f"变化 {impact['delta']:+d} 台。")
        for c in impact.get("changed_warehouses", []):
            lines.append(f"差异仓：{c['warehouse']} 变为可承诺 {c['promiseable']} 台。")
    else:
        lines.append("结论：没有找到对应规则。")
    if ev:
        lines.append("合同与规范依据：")
        for e in ev:
            lines.append(f"- {e['doc_id']}（{e['score']}）：{e['text'].replace(chr(10), ' ')[:120]}...")
    lines.append("操作：确认后点击「应用规则变更」，文件 ontology/wms.yaml 会被修改，"
                 "后续所有提问按新版本计算。")
    return "\n".join(lines)


# ------------------------------------------------------------ RAG 基线

def _run_baseline(st: dict, question: str, onto) -> None:
    """纯 RAG：只做向量检索，把片段交回给模型，不做任何计算。"""
    task_id = st["task_id"]
    t0 = time.time()
    hits = doc_agent.run({"intent": "commit", "sku_id": "NB-X1C-14"}, question)
    state_tool.trace(task_id, "doc", "vector_search", {"query": question}, True,
                     {"hits": len(hits["evidence"])}, int((time.time() - t0) * 1000))
    st["evidence"] = hits["evidence"]
    st["facts"] = {"doc": hits}

    snippets = "\n".join(f"- {e['doc_id']}：{e['text'][:100]}..." for e in hits["evidence"][:3])
    answer = (
        "仅基于文档检索，无法给出数量结论。\n"
        f"检索到 {len(hits['evidence'])} 个相关片段：\n{snippets}\n"
        "结合文档内容，建议先查询实时可用库存与在途情况，再确认是否可承诺。"
    )
    if llm.available():
        rewritten = llm.chat(
            "你只能使用给出的文档片段回答问题，不能编造数量。",
            f"问题：{question}\n文档片段：{snippets}",
        )
        if rewritten:
            answer = rewritten
    st["answer"] = answer
    st["verification"] = {"passed": True, "issues": [
        {"level": "info", "code": "BASELINE", "msg": "基线模式不做数量校验，它拿不到数量"}]}
