"""2×2 消融实验：ontology（代码执行 / 文本交给模型）× 图谱（有 / 无）。

    python scripts/ablation.py                        # 默认打本机 8000
    python scripts/ablation.py --base http://1.2.3.4:8000 --repeats 3

四个格子：
    rag_data          无 ontology（规则以文本给出，模型自己算）+ 无图谱
    rag_data_graph    无 ontology + 图谱关系以文本注入
    system_nograph    有 ontology（代码执行）+ 无图谱
    system            有 ontology + 有图谱（完整系统）

打分只看关键数字是否正确、是否出现错口径数字，以及耗时。
"""

from __future__ import annotations

import argparse
import json
import re
import statistics
import time
import urllib.error
import urllib.request

CASES = [
    {
        "name": "Q1 承诺",
        "q": "客户 C-2024-017 下周一要 500 台 NB-X1C-14，能承诺吗",
        "must": ["420", "80", "500"],
        "forbid": ["510", "680"],
    },
    {
        "name": "Q3 规则影响",
        "q": "新合同允许保税仓批次提前承诺，这条规则生效会影响什么",
        "must": ["480"],
        "forbid": ["570", "510"],
    },
    {
        "name": "Q5 列表",
        "q": "这周哪些货要发？",
        "must": ["13211"],
        "forbid": ["510"],
    },
    {
        "name": "Q7 影响面（多跳）",
        "q": "如果临期线收紧到 20 天，会影响哪些客户订单",
        "must": ["2798"],
        "forbid": ["编造"],
    },
]

CELLS = [
    ("① 强基线（无ont·无图）", "rag_data"),
    ("② 强基线（无ont·有图）", "rag_data_graph"),
    ("③ 多agent（有ont·无图）", "system_nograph"),
    ("④ 系统（有ont·有图）", "system"),
    ("⑤ NL2SQL（手写 SQL）", "nl2sql"),
]


def login(base: str) -> str:
    """服务要登录；返回 Cookie 头。账号从环境变量读。"""
    payload = json.dumps({"username": os.getenv("ADMIN_USER", "admin"),
                          "password": os.getenv("ADMIN_PASSWORD", "admin12345")}).encode()
    req = urllib.request.Request(base.rstrip("/") + "/api/login", data=payload,
                                 headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=30) as r:
        raw = r.headers.get("Set-Cookie") or ""
    pairs = [p.split(";")[0].strip() for p in raw.split(",") if "=" in p]
    return "; ".join(p for p in pairs
                     if not p.lower().startswith(("path", "httponly", "samesite", "max-age")))


def post(base: str, payload: dict, cookie: str = "") -> dict:
    headers = {"Content-Type": "application/json"}
    if cookie:
        headers["Cookie"] = cookie
    req = urllib.request.Request(base.rstrip("/") + "/api/ask",
                                 data=json.dumps(payload).encode(),
                                 headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=600) as r:
        return json.loads(r.read().decode())


def score(case: dict, answer: str) -> dict:
    text = answer or ""
    nums = set(re.findall(r"\d{2,5}", text))
    missing = [m for m in case["must"] if m not in nums]
    forbidden = [f for f in case["forbid"] if f in nums]
    return {"ok": not missing and not forbidden, "missing": missing,
            "forbidden": forbidden, "nums": sorted(nums), "len": len(text)}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8000")
    ap.add_argument("--repeats", type=int, default=2)
    ap.add_argument("--out", default="ablation-result.json")
    args = ap.parse_args()
    cookie = login(args.base)

    rows = []
    for label, mode in CELLS:
        print(f"\n=== {label} ({mode}) ===")
        for case in CASES:
            runs = []
            for i in range(args.repeats):
                t0 = time.time()
                try:
                    st = post(args.base, {"question": case["q"], "mode": mode}, cookie)
                except urllib.error.HTTPError as exc:
                    print(f"  {case['name']} #{i+1}: HTTP {exc.code}")
                    runs.append({"ok": False, "missing": ["HTTP"], "forbidden": [],
                                 "nums": [], "len": 0, "ms": 0})
                    continue
                ms = st.get("elapsed_ms") or int((time.time() - t0) * 1000)
                s = score(case, st.get("answer", ""))
                s["ms"] = ms
                runs.append(s)
                flag = "✓" if s["ok"] else "✗"
                print(f"  {case['name']} #{i+1}: {flag} {ms}ms len={s['len']} "
                      f"nums={s['nums']}"
                      + (f" 缺={s['missing']}" if s["missing"] else "")
                      + (f" 禁词={s['forbidden']}" if s["forbidden"] else ""))
            rows.append({
                "cell": label, "mode": mode, "case": case["name"],
                "ok_rate": sum(r["ok"] for r in runs) / len(runs),
                "ms": int(statistics.mean(r["ms"] for r in runs)) if runs else 0,
            })

    print("\n===== 结果 =====")
    print(f"{'格子':<26}{'题目':<12}{'正确率':>7}{'平均耗时':>10}")
    for r in rows:
        print(f"{r['cell']:<26}{r['case']:<12}{r['ok_rate'] * 100:>6.0f}%{r['ms']:>9}ms")

    by_cell: dict[str, list[dict]] = {}
    for r in rows:
        by_cell.setdefault(r["cell"], []).append(r)
    print("\n===== 按格子汇总 =====")
    print(f"{'格子':<26}{'总正确率':>9}{'平均耗时':>10}")
    for cell, items in by_cell.items():
        acc = statistics.mean(i["ok_rate"] for i in items)
        ms = statistics.mean(i["ms"] for i in items)
        print(f"{cell:<26}{acc * 100:>8.0f}%{ms:>9.0f}ms")

    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(rows, f, ensure_ascii=False, indent=1)
    print(f"\n明细已写入 {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
