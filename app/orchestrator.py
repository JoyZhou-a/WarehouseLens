"""编排层。

流程：规划 -> 并行执行检索类 agent -> 组装答案 -> 独立校验 -> 落状态。
state 存 Redis，trace 存 Redis 列表，前端轮询拿来做编排可视化。
"""

from __future__ import annotations

import json
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
from .tools import doc_tool
from .tools import session as session_tool

PARALLEL_AGENTS = {"inventory", "graph", "doc"}


def new_task() -> str:
    return uuid.uuid4().hex[:12]


def ask(question: str, mode: str = "system", session_id: str | None = None,
        task_id: str | None = None) -> dict:
    task_id = task_id or new_task()
    onto = get_ontology()
    history = session_tool.history(session_id) if session_id else []
    st = {
        "task_id": task_id,
        "question": question,
        "mode": mode,
        "session_id": session_id,
        "ontology_version": onto.version,
        "llm": llm.status(),
        "started_at": time.strftime("%H:%M:%S"),
        "history": history,
        "facts": {},
        "evidence": [],
    }
    started = time.time()

    if mode == "baseline":
        _run_baseline(st, question, onto)
    elif mode == "pipeline":
        _run_system(st, question, onto)
    else:
        _run_multi(st, question, onto)

    st["elapsed_ms"] = int((time.time() - started) * 1000)
    st["trace"] = state_tool.events(task_id)
    st["agent_timings"] = {
        name: {"ms": sum(s.get("ms", 0) for s in (info.get("steps") or [])),
               "steps": len(info.get("steps") or []),
               "degraded": info.get("degraded"),
               "tools": [s.get("tool") for s in (info.get("steps") or []) if s.get("action") == "call"]}
        for name, info in (st.get("agents") or {}).items()
    }

    # 先把会话写完，再落状态：前端收到 done 就拉会话，顺序反了会只看到用户这句
    if session_id:
        meta = {"task_id": task_id, "elapsed_ms": st["elapsed_ms"],
                "mode": mode, "timings": st["agent_timings"],
                "verification": st.get("verification")}
        session_tool.append(session_id, "user", question)
        session_tool.append(session_id, "assistant", st.get("answer") or "", meta)

    state_tool.save(task_id, st)
    return st


# ------------------------------------------------------------ 多 agent 编排

PLAN_SYSTEM = """你是仓储问答系统的主 agent。先判断问题类型，再决定派哪些子 agent。

类型只有四种：
- commit：问某物料某数量能不能承诺/够不够
- shortage：问为什么缺货、为什么某批没发
- rule_change：问某条规则生效/放开后会影响什么
- lookup：让列清单（哪些货要发、哪些批次快过期、哪些在途会到仓）

已知物料只有这些，sku 必须从这里选，或写 null：
{skus}

{catalog}

只输出 JSON：
{{"intent":"commit|shortage|rule_change|lookup",
 "slots":{{"sku":"NB-X1C-14 或 null","demand":500 或 null,"window_days":7 或 null}},
 "tasks":[{{"agent":"inventory|graph|doc","goal":"给这个子 agent 的一句话任务"}}]}}

规则：
- 问题里没写物料或数量，但「最近对话」里提到过，就沿用对话里的值（这就是记忆）。
- 口语简称要归一成上面的标准物料号，例如 T14-G5 → NB-T14-G5。
- 只派必要的子 agent，一个能解决就别派两个；lookup 只派 inventory。
- 涉及「这条规则从哪来、谁覆盖」加 graph；涉及「规范怎么写的」加 doc。
- 每个子 agent 最多出现一次。
"""

CLARIFY_TMPL = ("这个问题还缺两个关键信息，我没法直接算：{missing}。\n"
                "请补充后我再给结论（例如「客户 C-2024-017 下周一要 500 台 NB-X1C-14，能承诺吗」）。")


def _clarify(parsed: dict) -> str | None:
    """缺关键参数就反问，不再静默用默认值——默认值会让非专业用户拿到错答案。

    例外：归因类和规则类问题本质是「解释发生了什么」，允许沿用演示物料的默认值，
    但会在 parsed 里标记 assumed，答案里也会写清是哪个物料。
    """
    if parsed["intent"] in ("lookup", "shortage"):
        return None
    missing = []
    if not parsed.get("sku_id"):
        missing.append("物料号")
    if parsed["intent"] == "commit" and not parsed.get("demand"):
        missing.append("数量")
    if not missing:
        return None
    return CLARIFY_TMPL.format(missing="、".join(missing))


def _history_text(history: list[dict] | None) -> str:
    if not history:
        return "（无）"
    lines = []
    for m in history[-6:]:
        who = "用户" if m.get("role") == "user" else "系统"
        lines.append(f"{who}：{str(m.get('content'))[:160]}")
    return "\n".join(lines)


def _canon_sku(raw: str | None) -> str | None:
    """把模型/用户给的简称归一成标准物料号：T14-G5 → NB-T14-G5。"""
    if not raw:
        return None
    from .tools import dataset as ds

    want = str(raw).strip().upper().replace(" ", "")
    ids = [s["id"] for s in ds.SKUS]
    if want in ids:
        return want
    for sid in ids:
        if sid.upper().endswith(want) or want in sid.upper():
            return sid
    return None


def _main_plan(question: str, parsed: dict,
               history: list[dict] | None = None) -> tuple[list[dict], str, str | None, dict]:
    """主 agent 决定派谁，同时把记忆里的参数补上。模型不可用时退回关键词计划。"""
    from .agents.workers import CATALOG, WORKERS
    from .tools import dataset as ds

    fallback = [{"agent": s["agent"], "goal": s["goal"]} for s in planner.PLANS[parsed["intent"]]]
    skus = "、".join(f"{s['id']}（{s['name']}）" for s in ds.SKUS)
    verdict = llm.chat_json(PLAN_SYSTEM.format(catalog=CATALOG, skus=skus),
                            f"最近对话：{_history_text(history)}\n"
                            f"问题：{question}\n已抽取参数：{json.dumps(parsed, ensure_ascii=False)}")
    if not verdict:
        return fallback, "rule", None, {}
    tasks, seen = [], set()
    for t in verdict.get("tasks") or []:
        name = (t or {}).get("agent")
        if name in WORKERS and name not in seen:
            seen.add(name)
            tasks.append({"agent": name, "goal": (t.get("goal") or "")[:80]})
    slots = verdict.get("slots") or {}
    if not tasks:
        return fallback, "rule", verdict.get("intent"), slots
    return tasks, "llm", verdict.get("intent"), slots


def _run_multi(st: dict, question: str, onto) -> None:
    """主 agent：抽参数 → 澄清或派发 → 子 agent 并行取证 → 组装 → 独立校验。"""
    from .agents.workers import run_worker

    task_id = st["task_id"]
    t0 = time.time()
    # 多 agent 路径只做规则抽取，意图由主 agent 的规划调用一并给出（省一次模型往返）
    parsed = planner.parse(question)
    parsed["classified_by"] = "rule"
    st["parsed"] = parsed

    # 主 agent 的规划调用同时复核意图、并从最近对话里补参数，省掉额外的槽位解析
    t0 = time.time()
    tasks, planned_by, llm_intent, slots = _main_plan(question, parsed, st.get("history"))
    if llm_intent in planner.PLANS:
        parsed["intent"] = llm_intent
        parsed["classified_by"] = "llm"
        if llm_intent == "lookup":
            parsed["lookup_kind"] = planner._lookup_kind(question)
        st["parsed"] = parsed

    # 记忆补槽：只在问题本身没给参数时填，不覆盖用户明说的值
    filled = {}
    if not parsed.get("sku_id") and slots.get("sku"):
        sid = _canon_sku(slots.get("sku"))
        if sid:
            parsed["sku_id"] = sid
            filled["sku"] = sid
    if not parsed.get("demand") and slots.get("demand"):
        try:
            parsed["demand"] = int(slots["demand"])
            filled["demand"] = parsed["demand"]
        except (TypeError, ValueError):
            pass
    if slots.get("window_days") and parsed.get("intent") == "lookup":
        try:
            parsed["window_days"] = int(slots["window_days"])
        except (TypeError, ValueError):
            pass
    if filled:
        parsed["from_memory"] = filled
        state_tool.trace(task_id, "main", "recall", filled, True, {"from": "会话记忆"}, 0)

    if parsed["intent"] == "rule_change":
        if not parsed.get("sku_id"):
            parsed["sku_id"] = "NB-X1C-14"
            parsed["assumed_sku"] = True
        if not parsed.get("demand"):
            parsed["demand"] = 500
            parsed["assumed_demand"] = True
        parsed["target_rule"] = parsed.get("target_rule") or "R3"
    if parsed["intent"] == "shortage" and not parsed.get("sku_id"):
        # 归因类问题经常只说「14 寸机型」或某个仓，沿用演示物料，答案里会写明
        parsed["sku_id"] = "NB-X1C-14"
        parsed["assumed_sku"] = True
    if parsed["intent"] == "shortage" and not parsed.get("demand"):
        parsed["demand"] = 500
        parsed["assumed_demand"] = True
    st["parsed"] = parsed
    state_tool.trace(task_id, "planner", "parse+intent",
                     {"question": question}, True,
                     {"intent": parsed["intent"], "sku": parsed.get("sku_id"),
                      "demand": parsed.get("demand"), "window": parsed.get("window_days"),
                      "by": parsed.get("classified_by")},
                     int((time.time() - t0) * 1000))

    clarify = _clarify(parsed)
    if clarify:
        state_tool.trace(task_id, "main", "ask_back", {}, True, {"missing": clarify[:40]}, 0)
        st["answer"] = clarify
        st["verification"] = {"passed": True, "issues": [
            {"level": "info", "code": "CLARIFY", "msg": "缺少关键参数，先向用户确认"}]}
        return

    # 主 agent 有时会为了省事只派一个子 agent；承诺/归因/规则类问题必须有文档依据，
    # 规则类还必须看图里的治理关系，这里做确定性补齐（不是替代模型决策，是下限）。
    wanted = {t["agent"] for t in tasks}
    for need in ([] if parsed["intent"] == "lookup"
                 else ["doc"] + (["inventory", "graph"] if parsed["intent"] == "rule_change" else [])):
        if need not in wanted:
            tasks.append({"agent": need, "goal": f"补齐：{need} 取证"})
            wanted.add(need)
    st["plan"] = tasks
    state_tool.trace(task_id, "main", "dispatch", {"tasks": [t["agent"] for t in tasks]},
                     True, {"by": planned_by, "intent": parsed["intent"]},
                     int((time.time() - t0) * 1000))

    def trace_cb(agent: str, tool: str, args: dict, ok: bool, result, ms: int) -> None:
        state_tool.trace(task_id, agent, tool, args, ok, result, ms)

    results: dict[str, dict] = {}
    started_at = {t["agent"]: time.time() for t in tasks}
    for t in tasks:
        state_tool.trace(task_id, t["agent"], "start", {"goal": t.get("goal", "")}, True, {}, 0)

    with ThreadPoolExecutor(max_workers=3) as pool:
        jobs = {t["agent"]: pool.submit(run_worker, t["agent"], question, parsed, trace_cb)
                for t in tasks}
        for name, job in jobs.items():
            try:
                results[name] = job.result()
            except Exception as exc:
                results[name] = {"agent": name, "ok": False,
                                 "result": {"error": f"{type(exc).__name__}: {exc}"},
                                 "steps": [], "degraded": True}
            ms = int((time.time() - started_at[name]) * 1000)
            state_tool.trace(task_id, name, "done",
                             {"degraded": bool(results[name].get("degraded"))},
                             bool(results[name].get("ok")),
                             {"steps": len(results[name].get("steps") or []), "ms": ms}, ms)

    st["agents"] = {name: {"degraded": r.get("degraded"), "ok": r.get("ok"),
                           "steps": r.get("steps", [])} for name, r in results.items()}
    for name, res in results.items():
        st["facts"][name] = res.get("result")
    st["evidence"] = (st["facts"].get("doc") or {}).get("hits") or []

    # 规则变更类问题：基线与「关掉规则后」两组数由编排层确定性算，
    # 不依赖子 agent 恰好调对 simulate / compute_promise（它可能只调了其中一个）。
    if parsed["intent"] == "rule_change":
        t0 = time.time()
        baseline = inv_agent.run(onto, parsed)
        st["facts"]["inventory"] = baseline
        if parsed.get("target_rule"):
            st["rule_impact"] = _rule_impact(onto, parsed)
            state_tool.trace(task_id, "main", "compare_rule",
                             {"rule": parsed["target_rule"]}, True, st["rule_impact"],
                             int((time.time() - t0) * 1000))

    if parsed["intent"] == "lookup":
        st["answer"] = _compose_lookup(st, onto)
    else:
        st["answer"] = _compose(st, onto)

    t0 = time.time()
    verdict = verifier.check(st)
    state_tool.trace(task_id, "verifier", "check", {}, verdict["passed"],
                     {"passed": verdict["passed"], "issues": len(verdict["issues"])},
                     int((time.time() - t0) * 1000))
    st["verification"] = verdict


def _compose_lookup(st: dict, onto) -> str:
    """列表类问题：条目由代码从库里取出并逐条列出，模型最多补一句总结。"""
    parsed = st["parsed"]
    payload = st["facts"].get("inventory") or {}
    rows = payload.get("rows") or []
    kind = parsed.get("lookup_kind") or "orders"
    window = parsed.get("window_days") or 7
    head = {
        "orders": f"未来 {window} 天要发的订单",
        "shipments": f"未来 {window} 天会到仓的在途",
        "batches": f"剩余效期不足 {window} 天的批次",
    }[kind]

    if not rows:
        return (f"{head}：没有符合条件的记录。\n\n"
                f"（来源：{payload.get('backend', 'mysql')}；口径 ontology {st['ontology_version']}）")

    lines = [f"{head}：共 {len(rows)} 条"
             + (f"，合计 {payload.get('total_qty')} 台" if kind == "orders" else "") + "。"]
    for r in rows[:15]:
        if kind == "orders":
            lines.append(f"- {r['id']}｜{r['customer']}｜{r['sku_id']}｜{r['qty']} 台｜"
                         f"{r['due_days']} 天内到期｜{r['type']}")
        elif kind == "shipments":
            lines.append(f"- {r['id']}｜{r['sku_id']}｜{r['qty']} 台 → {r['warehouse_id']}｜"
                         f"{r['eta_days']} 天后到仓｜批次效期 {r['expiry_days']} 天")
        else:
            lines.append(f"- {r['id']}｜{r['sku_id']}｜{r['warehouse_id']}｜{r['qty']} 台｜"
                         f"剩余效期 {r['expiry_days']} 天｜清关 {'是' if r['cleared'] else '否'}")
    if len(rows) > 15:
        lines.append(f"- … 其余 {len(rows) - 15} 条未列出")

    ev = st.get("evidence") or []
    if ev:
        lines.append("依据：" + "；".join(f"{e['doc_id']} 第 {e['chunk_no'] + 1} 段" for e in ev[:2]) + "。")
    lines.append(f"（来源：{payload.get('backend', 'mysql')}；口径 ontology {st['ontology_version']}）")
    return "\n".join(lines)


# ------------------------------------------------------------ 系统路径

def _run_system(st: dict, question: str, onto) -> None:
    task_id = st["task_id"]

    t0 = time.time()
    plan = planner.plan(question)
    state_tool.trace(task_id, "planner", "plan", {"question": question},
                     True, plan, int((time.time() - t0) * 1000))
    st["parsed"] = plan["parsed"]
    st["plan"] = plan["steps"]

    parsed = dict(plan["parsed"])
    # 老流水线只支持带参数的承诺/归因问题，缺参数时沿用历史默认值（新路径会反问）
    parsed["sku_id"] = parsed.get("sku_id") or "NB-X1C-14"
    parsed["demand"] = parsed.get("demand") or 500
    facts: dict = {}

    # 检索类 agent 并行执行
    jobs = {}
    submitted: dict[str, float] = {}
    with ThreadPoolExecutor(max_workers=3) as pool:
        for step in plan["steps"]:
            name = step["agent"]
            if name == "inventory":
                jobs[name] = pool.submit(inv_agent.run, onto, parsed)
            elif name == "graph":
                jobs[name] = pool.submit(graph_agent.run, parsed)
            elif name == "doc":
                jobs[name] = pool.submit(doc_agent.run, parsed, question)
            if name in jobs:
                submitted[name] = time.time()

    for step in plan["steps"]:
        name = step["agent"]
        if name not in jobs:
            state_tool.trace(task_id, name, "skip", {}, True, {"msg": "未实现"}, 0)
            facts[name] = {"implemented": False, "note": "该 agent 预留，当前版本未实现"}
            continue
        try:
            result = jobs[name].result()
            # 耗时从提交那一刻算，否则并行任务会全部显示 0ms
            elapsed = int((time.time() - submitted[name]) * 1000)
            state_tool.trace(task_id, name, step["goal"], {"sku": parsed.get("sku_id")},
                             True, _summarize(name, result), elapsed)
        except Exception as exc:
            elapsed = int((time.time() - submitted[name]) * 1000)
            result = {"error": f"{type(exc).__name__}: {exc}"}
            state_tool.trace(task_id, name, step["goal"], {}, False, result, elapsed)
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
            "backend": result.get("backend"),
            "blocked": [f"{s['shipment']}:{s['blocked_by']}" for s in result.get("supply", []) if not s["countable"]],
        }
    if name == "graph":
        return {"substitutes": len(result.get("substitutes", [])),
                "paths": len(result.get("substitute_paths", [])),
                "inbound": len(result.get("inbound", [])),
                "backend": result.get("backend")}
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

COMPOSE_SYSTEM = """你是联想供应链的计划经理，正在回答销售或计划同事的追问。

必须遵守：
1. 只能使用「事实」里出现的数字和信息，不得引入任何新数字、新批次、新仓库、新客户、新日期。
2. 事实里没有的东西，直接说「这项数据没有」，不要推测。
3. 控制在 150 字以内、最多 4 句话：先给结论，再讲是哪条规则挡住、或为什么不够，最后一句给建议或风险提示。
4. 不要表格，不要逐仓罗列全部字段，不要复述原始 JSON，不要分点排版。
5. 提到规则时用编号加中文名，例如「R1 临期批次不参与新单承诺」。
6. 不要写「根据提供的事实」这类套话，直接说结论。"""


def _compose(st: dict, onto) -> str:
    """先用模型写，模型不可用就退回确定性模板。数字永远来自事实，不来自模型。"""
    parsed = st["parsed"]
    inv = st.get("facts", {}).get("inventory") or {}
    intent = parsed["intent"]

    if st["mode"] == "baseline":
        return st.get("answer", "")

    template = _compose_template(st, onto, intent, inv)
    if not llm.available():
        return template

    written = _compose_with_llm(st, onto, intent)
    return written or template


def _compose_template(st: dict, onto, intent: str, inv: dict) -> str:
    if intent == "commit":
        return _compose_commit(st, inv, onto)
    if intent == "shortage":
        return _compose_shortage(st, inv)
    if intent == "rule_change":
        return _compose_rule(st, inv)
    return "已执行检索，未匹配到可用的回答模板。"


def _facts_for_llm(st: dict) -> dict:
    """交给模型的只有事实，没有模板文字。字段名用中文，减少模型误读。"""
    inv = st.get("facts", {}).get("inventory") or {}
    facts = {
        "物料": {"id": inv.get("sku_id"), "名称": inv.get("sku_name")},
        "需求": inv.get("demand"),
        "可承诺合计": inv.get("total_promiseable"),
        "缺口": inv.get("gap"),
        "调拨方案后可用": inv.get("after_transfer"),
        "各仓": [
            {"仓": w["warehouse_name"], "类型": w["warehouse_type"],
             "在库": w["on_hand"], "已分配": w["allocated"], "预留": w["reserved"],
             "扣留": w["hold_qty"], "已到在途": w["in_transit_arrived"],
             "可承诺": w["promiseable"], "命中规则": w["blocked_by"]}
            for w in inv.get("warehouses", [])
        ],
        "在途": [
            {"单号": s["shipment"], "到仓": s["warehouse_id"], "数量": s["qty"],
             "预计天数": s["eta_days"], "批次效期": s["expiry_days"],
             "是否计入": s["countable"], "命中规则": s["blocked_by"]}
            for s in inv.get("supply", [])
        ],
        "调拨": inv.get("transfers", []),
        "文档依据": [
            {"文件": e["doc_id"], "相似度": e["score"], "片段": e["text"][:150]}
            for e in st.get("evidence", [])[:3]
        ],
    }
    impact = st.get("rule_impact")
    if impact:
        facts["规则影响"] = {
            "规则": impact.get("rule"), "名称": impact.get("label"),
            "生效前": impact.get("before"), "生效后": impact.get("after"),
            "变化": impact.get("delta"),
            "受影响仓库": impact.get("changed_warehouses"),
        }

    # 图库的结果也要进事实，否则 Neo4j 只是被查了一下，对答案毫无影响
    graph = (st.get("facts") or {}).get("graph") or {}
    subs = graph.get("substitutes") or []
    if subs:
        facts["替代料"] = [
            {"替代物料": s.get("to_name"), "限制": s.get("restriction"),
             "可用批次": [f"{b['warehouse']} {b['qty']} 台（效期 {b['expiry_days']} 天）"
                        for b in (s.get("stock") or [])]}
            for s in subs
        ]
    governance = graph.get("rule_governance") or []
    if governance:
        facts["规则来源"] = [
            {"文件": g["doc"], "关系": g["rel"], "规则": g["rule"], "规则名": g.get("label")}
            for g in governance
        ]
    return facts


def _rule_provenance_line(st: dict) -> str | None:
    """规则出自哪份文件、被谁覆盖——这段只能来自 Neo4j，不能让模型自己说。"""
    governance = ((st.get("facts") or {}).get("graph") or {}).get("rule_governance") or []
    if not governance:
        return None
    governs, overrides = [], []
    for g in governance:
        if g.get("rel") == "GOVERNS":
            governs.append(f"{g['rule']} 由 {g['doc']} 规定")
        elif g.get("rel") == "OVERRIDES":
            overrides.append(f"{g['doc']} 以 OVERRIDES 覆盖 {g['rule']}")
    parts = []
    if governs:
        parts.append("、".join(dict.fromkeys(governs)))
    if overrides:
        parts.append("、".join(dict.fromkeys(overrides)))
    return "规则来源（Neo4j 多跳）：" + "；".join(parts) + "。" if parts else None


def _compose_with_llm(st: dict, onto, intent: str) -> str | None:
    facts = _facts_for_llm(st)
    question = st["question"]
    hist = _history_text(st.get("history"))
    answer = llm.chat(COMPOSE_SYSTEM,
                      f"最近对话：{hist}\n\n问题：{question}\n\n事实：\n{json.dumps(facts, ensure_ascii=False)}",
                      temperature=0.3, max_tokens=600)
    if not answer:
        return None

    lines = [answer.strip()]
    if intent == "rule_change":
        prov = _rule_provenance_line(st)
        if prov:
            lines.append(prov)
        lines.append("操作：确认后打开「本体定义」面板，把 "
                     f"{(st.get('rule_impact') or {}).get('rule', '目标规则')} 的开关关掉，"
                     "文件 ontology/wms.yaml 会被改写，再问一次即可看到新数字。")
    lines.append(f"（数字由代码从 MySQL / Neo4j 算出，模型只负责表述；口径 ontology "
                 f"{st['ontology_version']}，可用量 = {onto.formula_text('available_inventory')}）")
    return "\n\n".join(lines)


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

BASELINE_SYSTEM = """你是一个只依据检索片段回答问题的助手。

必须遵守：
1. 只能使用给出的片段内容作答，片段里没有的数字、库存、批次、客户信息一律不得编造。
2. 缺数据就直接说缺什么、无法判断，不要用常识补。
3. 不要假装自己能查库或能计算可用量。"""


def _run_baseline(st: dict, question: str, onto) -> None:
    """纯 RAG 基线：只用问题本身做向量检索，把命中片段原样交给模型。

    刻意不加任何领域提示词、不做 query 改写、不做计算——这是要对比的那条路径，
    给它加料就没有对比意义了。
    """
    task_id = st["task_id"]
    t0 = time.time()
    hits = doc_tool.search(question, top_k=4)
    backend = hits[0]["backend"] if hits else doc_tool.backend()
    state_tool.trace(task_id, "doc", "vector_search",
                     {"query": question, "top_k": 4}, True,
                     {"hits": len(hits), "backend": backend},
                     int((time.time() - t0) * 1000))

    st["evidence"] = hits
    st["facts"] = {"doc": {"query": question, "evidence": hits, "backend": backend}}

    # 片段按原文给，不截断，避免基线因为被截断而答得更差
    snippets = "\n\n".join(f"【{e['doc_id']} 第 {e['chunk_no'] + 1} 段】{e['text']}" for e in hits)
    answer = (
        "仅基于文档检索，无法给出数量结论。\n"
        f"检索到 {len(hits)} 个相关片段：\n"
        + "\n".join(f"- {e['doc_id']}：{e['text'][:100]}..." for e in hits[:3])
        + "\n结合文档内容，建议先查询实时可用库存与在途情况，再确认是否可承诺。"
    )
    if llm.available():
        rewritten = llm.chat(BASELINE_SYSTEM,
                             f"问题：{question}\n\n检索到的片段：\n{snippets}",
                             temperature=0.2, max_tokens=900)
        if rewritten:
            answer = rewritten
    st["answer"] = answer
    st["verification"] = {"passed": True, "issues": [
        {"level": "info", "code": "BASELINE", "msg": "基线模式不做数量校验，它拿不到数量"}]}
