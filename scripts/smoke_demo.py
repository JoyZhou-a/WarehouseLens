"""演示前一键自检。需要服务已经起来（python run.py）。

    python scripts/smoke_demo.py

检查三件事：
1. 四个库都连得上，且走的是真库不是本地降级
2. 三个演示问题的关键数字对不对（420 / 90 / 500 / 480）
3. 每一步校验是否通过

退出码 0 表示可以上台。任何一项不对都会打印具体差在哪。
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request

BASE = os.getenv("WMS_BASE", "http://127.0.0.1:8000")
COOKIE: dict[str, str] = {}

Q1 = "客户 C-2024-017 下周一要 500 台 NB-X1C-14，能承诺吗"
Q2 = "上周合肥仓 14 寸机型缺货率为什么上升"
Q3 = "新合同允许保税仓批次提前承诺，这条规则生效会影响什么"

failures: list[str] = []


def login() -> None:
    """服务加了登录后，脚本要先拿 cookie；账号从环境变量读，默认同 .env 占位值。"""
    payload = {"username": os.getenv("ADMIN_USER", "admin"),
               "password": os.getenv("ADMIN_PASSWORD", "admin12345")}
    req = urllib.request.Request(
        BASE + "/api/login",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"}, method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        raw = r.headers.get("Set-Cookie") or ""
    for part in raw.split(";"):
        if "=" in part and not part.strip().lower().startswith(("path", "httponly", "samesite", "max-age")):
            k, v = part.strip().split("=", 1)
            COOKIE[k] = v
    if not COOKIE:
        raise SystemExit("登录失败：没有拿到 cookie，检查 ADMIN_USER / ADMIN_PASSWORD")


def api(path: str, payload: dict | None = None) -> dict | list:
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    headers = {"Content-Type": "application/json"} if data else {}
    if COOKIE:
        headers["Cookie"] = "; ".join(f"{k}={v}" for k, v in COOKIE.items())
    req = urllib.request.Request(
        BASE + path, data=data, headers=headers,
        method="POST" if data else "GET",
    )
    with urllib.request.urlopen(req, timeout=180) as r:
        return json.loads(r.read().decode("utf-8"))


def check(label: str, ok: bool, detail: str = "") -> None:
    print(f"[{'OK  ' if ok else 'FAIL'}] {label}{('  ' + detail) if detail else ''}")
    if not ok:
        failures.append(label)


def main() -> int:
    try:
        health = api("/api/health")
    except urllib.error.URLError as exc:
        print(f"服务没起来：{exc}. 先执行 python run.py")
        return 2
    try:
        login()
    except Exception as exc:  # 账号不对就没法跑后面的断言，直接退出
        print(f"登录失败：{exc}")
        return 2

    print("== 依赖 ==")
    check("MySQL 真库", health.get("mysql") is True, f"mysql={health.get('mysql')}")
    check("Neo4j 真库", health.get("neo4j") is True, f"neo4j={health.get('neo4j')}")
    check("Milvus 真库", str(health.get("milvus", "")).startswith("milvus"),
          f"milvus={health.get('milvus')}")
    check("Redis 真库", health.get("state") == "redis", f"state={health.get('state')}")
    check("ontology 已加载", bool(health.get("ontology_version")),
          f"v{health.get('ontology_version')}")

    print("\n== Q1 承诺 ==")
    st = api("/api/ask", {"question": Q1, "mode": "system"})
    inv = st["facts"]["inventory"]
    by_id = {w["warehouse_id"]: w for w in inv["warehouses"]}
    check("上海仓可承诺 420", by_id["WH-SH"]["promiseable"] == 420,
          f"实际 {by_id['WH-SH']['promiseable']}")
    check("合肥仓可承诺 90", by_id["WH-HF"]["promiseable"] == 90,
          f"实际 {by_id['WH-HF']['promiseable']}")
    check("保税仓被 R3 挡掉", by_id["WH-HK"]["promiseable"] == 0
          and "R3" in by_id["WH-HK"]["blocked_by"], f"{by_id['WH-HK']['blocked_by']}")
    check("在途 80 台被 R1 挡掉", all(s["countable"] for s in inv["supply"]) is False,
          str([f"{s['shipment']}:{s['blocked_by']}" for s in inv["supply"]]))
    check("合计 420 + 调拨 80 = 500",
          inv["total_promiseable"] == 420
          and inv["after_transfer"] == 500
          and sum(t["qty"] for t in inv["transfers"]) == 80,
          f"合计 {inv['total_promiseable']}，调拨 {[t['qty'] for t in inv['transfers']]}，"
          f"方案后 {inv['after_transfer']}")
    check("Q1 校验通过", st["verification"]["passed"], str(st["verification"]["issues"]))
    check("库存查询走真 MySQL", inv.get("backend") == "mysql", f"backend={inv.get('backend')}")
    graph_facts = st["facts"].get("graph") or {}
    if graph_facts:
        check("图查询走真 Neo4j", graph_facts.get("backend") == "neo4j",
              f"backend={graph_facts.get('backend')}")
    doc_backend = (st["facts"].get("doc") or {}).get("backend")
    check("文档检索走真 Milvus", str(doc_backend).startswith("milvus"), f"backend={doc_backend}")
    agents_used = list((st.get("agents") or {}).keys())
    check("主 agent 确实派了子 agent", len(agents_used) >= 2, f"派发={agents_used}")
    check("子 agent 都自主收敛（无退回确定性）",
          all(not info.get("degraded") for info in (st.get("agents") or {}).values()),
          str({k: v.get("degraded") for k, v in (st.get("agents") or {}).items()}))

    print("\n== Q2 归因 ==")
    st2 = api("/api/ask", {"question": Q2, "mode": "system"})
    check("Q2 校验通过", st2["verification"]["passed"], str(st2["verification"]["issues"]))
    check("Q2 引用了扣留邮件", any(e["doc_id"] == "mail-qa-hold-0307" for e in st2["evidence"]))

    print("\n== Q3 规则影响 ==")
    st3 = api("/api/ask", {"question": Q3, "mode": "system"})
    impact = st3.get("rule_impact") or {}
    check("命中规则 R3", impact.get("rule") == "R3", str(impact.get("rule")))
    check("420 变 480（+60）",
          impact.get("before") == 420 and impact.get("after") == 480,
          f"{impact.get('before')} -> {impact.get('after')}")
    check("Q3 校验通过", st3["verification"]["passed"], str(st3["verification"]["issues"]))
    check("Q3 答案引用了 Neo4j 的规则来源", "Neo4j 多跳" in st3["answer"],
          st3["answer"][:60].replace("\n", " "))
    graph_backend3 = (st3["facts"].get("graph") or {}).get("backend")
    check("Q3 图查询走真 Neo4j", graph_backend3 == "neo4j", f"backend={graph_backend3}")

    print("\n== 列表类问题（RAG 答不了的那类） ==")
    lst = api("/api/ask", {"question": "这周哪些货要发？", "mode": "system"})
    rows = ((lst.get("facts") or {}).get("inventory") or {}).get("rows") or []
    check("列表问题落到了 lookup 意图", (lst.get("parsed") or {}).get("intent") == "lookup",
          str((lst.get("parsed") or {}).get("intent")))
    check("从库里枚举到订单", len(rows) > 0, f"{len(rows)} 条")
    check("答案里逐条列出", "｜" in (lst.get("answer") or ""), (lst.get("answer") or "")[:50])

    print("\n== 缺参数时应反问 ==")
    ask_back = api("/api/ask", {"question": "客户要一批货，能发吗", "mode": "system"})
    check("缺物料/数量时反问而不是默认 500",
          "补充" in (ask_back.get("answer") or "") and not (ask_back.get("facts") or {}),
          (ask_back.get("answer") or "")[:40])

    print("\n== RAG 基线对照 ==")
    base = api("/api/ask", {"question": Q1, "mode": "baseline"})
    base_answer = base["answer"]
    # 基线拿不到库存数据，不该出现 420 这种只有查库才知道的数字
    check("基线没有编出可用量 420", "420" not in base_answer,
          base_answer[:80].replace("\n", " "))
    check("基线明确无法给出承诺结论",
          ("无法" in base_answer or "不能" in base_answer),
          base_answer[:60].replace("\n", " "))

    print("\n== 耗时 ==")
    for tag, s in (("Q1", st), ("Q2", st2), ("Q3", st3)):
        print(f"  {tag}: {s['elapsed_ms']} ms")

    if failures:
        print(f"\n{len(failures)} 项未通过：{failures}")
        return 1
    print("\n全部通过，可以上台")
    return 0


if __name__ == "__main__":
    sys.exit(main())
