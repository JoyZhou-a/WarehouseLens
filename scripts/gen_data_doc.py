"""把库里现存数据导出成一份人看的数据字典（markdown）。只读，只在本地生成文件。"""
import os, sys, time
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT)); os.chdir(ROOT)
from dotenv import load_dotenv; load_dotenv(ROOT / ".env")

from app import config
from app.ontology import get_ontology
from app.tools import mysql_tool
import pymysql

out = []
w = out.append

w("# 演示数据字典")
w("")
w(f"由 `scripts/gen_data_doc.py` 从运行中的库直接导出，{time.strftime('%Y-%m-%d %H:%M')}。")
w(f"数据源：MySQL `{config.MYSQL['host']}` / Neo4j `{config.NEO4J_URI}` / Milvus `{config.MILVUS_HOST}` / ontology `{config.ONTOLOGY_PATH.name}`。")
w("")

onto = get_ontology()

w("## 1. 仓库")
w("")
w("| id | 名称 | 类型 | 城市 | 说明 |")
w("|---|---|---|---|---|")
notes = {"finished_goods": "成品仓，可参与新单", "bonded": "保税仓，R3 默认不计入提前承诺",
         "service_parts": "备件仓，R4 只服务售后"}
for r in mysql_tool.warehouses():
    w(f"| {r['id']} | {r['name']} | {r['type']} | {r['city']} | {notes.get(r['type'], '')} |")
w("")

w("## 2. 物料")
w("")
w("| id | 名称 | 类别 |")
w("|---|---|---|")
conn = pymysql.connect(cursorclass=pymysql.cursors.DictCursor, charset="utf8mb4", **config.MYSQL)
cur = conn.cursor()
cur.execute("SELECT * FROM skus ORDER BY id")
for r in cur.fetchall():
    w(f"| {r['id']} | {r['name']} | {r['category']} |")
w("")

w("## 3. 库存（可用量与可承诺量按 ontology 公式现算）")
w("")
w("| SKU | 仓库 | 在库 | 已分配 | 预留 | 扣留 | 已到在途 | 安全库存 | 可用 | 可承诺 |")
w("|---|---|---|---|---|---|---|---|---|---|")
cur.execute("SELECT i.*, w.name AS wh FROM inventory i JOIN warehouses w ON w.id=i.warehouse_id ORDER BY i.sku_id, i.warehouse_id")
for r in cur.fetchall():
    av = onto.calc("available_inventory", r)
    pr = onto.calc("promiseable_qty", {**r, "available_inventory": av})
    w(f"| {r['sku_id']} | {r['wh']} | {r['on_hand']} | {r['allocated']} | {r['reserved']} | "
      f"{r['hold_qty']} | {r['in_transit_arrived']} | {r['safety_stock']} | {int(av)} | {int(pr)} |")
w("")

w("## 4. 批次")
w("")
w("| 批次 | SKU | 仓库 | 数量 | 剩余效期(天) | 已清关 |")
w("|---|---|---|---|---|---|")
cur.execute("SELECT * FROM batches ORDER BY sku_id, expiry_days")
for r in cur.fetchall():
    w(f"| {r['id']} | {r['sku_id']} | {r['warehouse_id']} | {r['qty']} | {r['expiry_days']} | "
      f"{'是' if r['cleared'] else '否'} |")
w("")

w("## 5. 在途")
w("")
w("| 单号 | SKU | 到仓 | 批次 | 数量 | ETA(天) | 承运商 | 批次效期(天) |")
w("|---|---|---|---|---|---|---|---|")
cur.execute("SELECT s.*, b.expiry_days FROM shipments s LEFT JOIN batches b ON b.id=s.batch_id ORDER BY s.id")
for r in cur.fetchall():
    w(f"| {r['id']} | {r['sku_id']} | {r['warehouse_id']} | {r['batch_id']} | {r['qty']} | "
      f"{r['eta_days']} | {r['carrier']} | {r['expiry_days']} |")
w("")

w("## 6. 替代料")
w("")
cur.execute("SELECT * FROM substitutes")
for r in cur.fetchall():
    w(f"- {r['from_sku']} 可替代为 {r['to_sku']}，限制：{r['restriction']}")
w("")

w("## 7. 订单")
w("")
cur.execute("SELECT COUNT(*) AS c FROM orders")
n = cur.fetchone()["c"]
w(f"当前 **{n} 行**。" + ("客户订单没有落库，Q1 的 C-2024-017 只存在于问题文本里，涉及订单明细的问题问不出来。" if n == 0 else ""))
w("")
conn.close()

w("## 8. 规则（ontology 驱动，可开关）")
w("")
w("| 规则 | 说明 | 触发条件 | 效果 | 状态 |")
w("|---|---|---|---|---|")
for r in onto.rules:
    w(f"| {r['id']} | {r.get('label')} | `{r.get('when')}` | {r['then']} | "
      f"{'启用' if r.get('enabled', True) else '停用'} |")
w("")

w("## 9. 文档（23 段，进 Milvus 的是这些）")
w("")
for path in sorted((ROOT / "data" / "docs").glob("*.md")):
    text = path.read_text(encoding="utf-8").strip()
    chunks = [c for c in text.split("\n\n") if len(c.strip()) >= 40]
    first = text.splitlines()[0].lstrip("# ").strip()
    w(f"### {path.stem}")
    w(f"{first}（{len(chunks)} 段）")
    w("")
    for c in chunks:
        w(f"- {c.strip().replace(chr(10), ' ')[:150]}")
    w("")

TARGET = ROOT / "DEMO-DATA.md"
TARGET.write_text("\n".join(out) + "\n", encoding="utf-8")
print(f"已写入 {TARGET}（{len(out)} 行）")
