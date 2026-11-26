"""三个子 agent：各自持工具，由模型做 ReAct 决策，工具负责算数。

共同约定：
- 输入是「任务描述 + 已知参数」，不是整份 state，避免上下文互相污染；
- 输出统一为 {agent, ok, result, steps, degraded}，result 是该 agent 的结构化事实；
- 模型不可用或跑飞时退回各自的确定性实现（inventory/graph/doc 内核还在）。
"""

from __future__ import annotations

import json

from ..ontology import get_ontology
from ..tools import doc_tool, ops
from . import doc as doc_core
from . import graph as graph_core
from . import inventory as inv_core
from .base import ReActAgent, Tool

INVENTORY_TOOLS = [
    Tool("compute_promise", "按 ontology 口径算可承诺量、缺口、调拨方案。参数 sku, demand, overrides", ops.compute_promise),
    Tool("simulate", "假设推演：rule_off / expiry_threshold / wh_down / eta_delta", ops.simulate),
    Tool("get_inventory", "某物料各仓库存明细（在库/已分配/扣留/在途/安全库存）", ops.get_inventory),
    Tool("get_batches", "某物料批次，可按仓与剩余效期过滤", ops.get_batches),
    Tool("get_shipments", "某物料在途单", ops.get_shipments),
    Tool("list_blocked_supply", "被规则挡掉、不计入可承诺的供应", ops.list_blocked_supply),
    Tool("list_orders", "按到期天数列订单（回答这周哪些货要发）", ops.list_orders),
    Tool("list_due_shipments", "按预计到仓天数列在途", ops.list_due_shipments),
    Tool("list_expiring_batches", "列快过期的批次", ops.list_expiring_batches),
]

GRAPH_TOOLS = [
    Tool("get_substitutes", "替代料链路，max_hops>1 走变长路径", ops.get_substitutes),
    Tool("get_inbound_chain", "在途→物料→仓→批次的链路", ops.get_inbound_chain),
    Tool("get_rule_sources", "某条规则出自哪些文档、被谁覆盖", ops.get_rule_sources),
    Tool("get_rule_governance", "某份文档治理了哪些规则", ops.get_rule_governance),
    Tool("impact_of_rule", "某条规则生效会影响哪些订单和客户（规则→批次→物料→订单→客户）",
         ops.impact_of_rule),
    Tool("supplier_trace", "某供应商的采购单、批次、客户（5 跳）", ops.supplier_trace),
    Tool("substitute_coverage", "替代链能覆盖多少未交付需求（替代链 × 可用量 × 订单）",
         ops.substitute_coverage),
]

DOC_TOOLS = [
    Tool("search_docs", "语义检索 SOP/合同/邮件", ops.search_docs),
    Tool("read_doc", "读某份文档全文，用于确认上下文", ops.read_doc),
]


def _inv_fallback(task: str, ctx: dict) -> dict:
    return inv_core.run(get_ontology(), ctx.get("parsed") or {})


def _graph_fallback(task: str, ctx: dict) -> dict:
    return graph_core.run(ctx.get("parsed") or {})


def _doc_fallback(task: str, ctx: dict) -> dict:
    return doc_core.run(ctx.get("parsed") or {}, ctx.get("question", ""))


inventory_agent = ReActAgent(
    name="inventory",
    role=("库存与承诺子 agent。负责：算某物料各仓的可用量/可承诺量、判断缺口、给调拨方案、"
          "按规则判断哪些批次或仓不能计入、按时间窗列订单/在途/临期批次、做假设推演。"
          "任何数字都必须来自工具，不能自己估算。"),
    tools=INVENTORY_TOOLS,
    fallback=_inv_fallback,
    max_steps=2,
)

graph_agent = ReActAgent(
    name="graph",
    role=("知识图谱子 agent。负责：替代料链路（含跳数与限制）、在途链路、"
          "规则与文档之间的治理关系（哪条规则出自哪份文件、被谁覆盖）。"
          "只回答结构关系，不负责算数。"),
    tools=GRAPH_TOOLS,
    fallback=_graph_fallback,
    max_steps=2,
)

doc_agent = ReActAgent(
    name="doc",
    role=("文档子 agent。负责：在 SOP、合同、邮件里检索依据。"
          "检索到片段后要先判断「这些片段是否真的对得上问题」（看是否提到相关物料/仓库/主题），"
          "对不上就换关键词、加物料号或缩到具体文档重查，最多两轮。只提供条文依据，不提供数字。"),
    tools=DOC_TOOLS,
    fallback=_doc_fallback,
    max_steps=2,
)

WORKERS = {"inventory": inventory_agent, "graph": graph_agent, "doc": doc_agent}

# 给主 agent 的能力说明（用于规划）
CATALOG = """可用子 agent：
- inventory：库存与承诺。能算可承诺量/缺口/调拨（compute_promise、simulate 假设推演），
  能查库存/批次/在途，能列订单、在途、临期批次（回答「这周哪些货要发」这类问题用它）。
- graph：知识图谱。查替代料链路（含跳数）、在途链路、规则出自哪份文档、文档治理哪些规则。
- doc：文档检索。在 SOP/合同/邮件里找依据，会自动检查命中是否对题并重查。"""


def task_text(agent: str, question: str, parsed: dict) -> str:
    sku = parsed.get("sku_id")
    demand = parsed.get("demand")
    window = parsed.get("window_days")
    lines = [f"用户问题：{question}",
             f"已知参数：SKU={sku}，需求={demand}，时间窗={window}天"]
    if parsed.get("intent") == "lookup":
        kind = parsed.get("lookup_kind") or "orders"
        tool = {"orders": "list_orders", "shipments": "list_due_shipments",
                "batches": "list_expiring_batches"}[kind]
        lines.append(f"这是列表类问题，用 {tool}(window={window}) 取数据，必要时再补一次过滤。")
    elif parsed.get("intent") == "rule_change":
        lines.append("这是规则变更/影响类问题：需要算「规则生效前」和「关掉目标规则后」两组数，"
                     "用 simulate(sku, demand, {rule_off:[规则]}) 对比；"
                     "如果问影响面（哪些订单/客户），用 impact_of_rule(rule_id, within_days)。")
    if parsed.get("supplier_id"):
        lines.append(f"问题涉及供应商 {parsed['supplier_id']}，用 supplier_trace 查它的采购单、批次和客户。")
    lines.append("请用工具取证并给出结论所需的事实。")
    return "\n".join(lines)


PAYLOAD_PRIORITY = {
    "inventory": ["compute_promise", "simulate", "list_orders", "list_due_shipments",
                  "list_expiring_batches", "list_blocked_supply", "get_inventory",
                  "get_batches", "get_shipments"],
    "graph": ["get_substitutes", "get_rule_sources", "get_rule_governance",
              "get_inbound_chain"],
    "doc": ["search_docs", "read_doc"],
}


def run_worker(name: str, question: str, parsed: dict, trace=None) -> dict:
    worker = WORKERS[name]
    ctx = {"parsed": parsed, "question": question, "trace": trace}
    out = worker.run(task_text(name, question, parsed), ctx)

    # 模型常在 final 里转述而不是原样带回数据，这里按优先级从工具观测里取真实返回，
    # 保证「数字来自工具」这条不被模型的复述破坏。
    obs = out.get("observations") or {}
    picked = None
    for tool in PAYLOAD_PRIORITY.get(name, []):
        if tool in obs:
            picked = obs[tool]
            break

    if name == "graph":
        # 图工具返回的是 {rows: [...]}，编排层和答案组装用的是语义键，这里归一
        norm: dict = {"backend": (picked or {}).get("backend")}
        if "get_substitutes" in obs:
            norm["substitutes"] = obs["get_substitutes"].get("rows") or []
        if "get_rule_sources" in obs:
            norm["rule_governance"] = obs["get_rule_sources"].get("rows") or []
        elif "get_rule_governance" in obs:
            norm["rule_governance"] = obs["get_rule_governance"].get("rows") or []
        if "get_inbound_chain" in obs:
            norm["inbound"] = obs["get_inbound_chain"].get("rows") or []
        for key in ("impact_of_rule", "supplier_trace", "substitute_coverage"):
            if key in obs:
                norm[key] = obs[key]
        if out.get("result"):
            norm["_agent_note"] = str(out["result"])[:300]
        out["result"] = norm
        out["agent"] = name
        return out

    if name == "doc" and "search_docs" in obs:
        hits = obs["search_docs"].get("hits") or []
        doc_norm = {"hits": hits, "backend": obs["search_docs"].get("backend")}
        if out.get("result"):
            doc_norm["_agent_note"] = str(out["result"])[:300]
        out["result"] = doc_norm
        out["agent"] = name
        return out

    if picked is not None:
        merged = dict(picked)
        if out.get("result"):
            merged["_agent_note"] = str(out["result"])[:300]
        out["result"] = merged
    out["agent"] = name
    return out


def summarize_steps(out: dict) -> list[dict]:
    return out.get("steps") or []


def dump(out: dict) -> str:
    return json.dumps(out.get("result") or {}, ensure_ascii=False)[:400]
