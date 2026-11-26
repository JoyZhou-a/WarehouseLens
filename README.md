# WarehouseLens

**把仓储口径做成可执行的本体，用主 agent + 子 agent 编排取出可审计的答案。**

这个项目要回答的是仓库日常真的会问、但 RAG 答不好的四类问题：

| 问题 | 靠什么回答 |
|---|---|
| 客户 C-2024-017 下周一要 500 台 NB-X1C-14，能承诺吗 | 本体公式算可用量，规则判临期/保税，代码算缺口与调拨 |
| 如果临期线收紧到 20 天，会影响哪些客户订单 | 规则→批次→物料→订单→客户，4 跳图查询 |
| 供应商 S-2201 的批次流向了哪些客户 | 供应商→采购单→批次→物料→订单→客户，5 跳 |
| 这周哪些货要发？ | 结构化查询 + 逐条枚举（向量检索做不到） |

不是「给文档加个向量库」，而是三件事的组合：**本体（口径）→ 编排（谁来算）→ 校验（算得对不对）**。

---

## 60 秒看懂

```bash
python run.py                 # http://127.0.0.1:8000 ，账号见 .env
```

界面分三栏：

- **左**：会话列表（追问能接上文）
- **中**：对话 + 每个回答内联「执行过程」——谁在干活、调了什么工具、耗时多少
- **右**：本次运行的编排图（主 agent 派发到哪个子 agent，谁在发光）、各角色耗时、校验结论

顶部还有「仓储看板」：库存总量与可用量、分仓占用率、临期批次、在途与待发订单——可用量走的是同一套本体公式，和问答不会出现两个口径。

打开「对比模式」可以把同一道题并排跑两种路径：左边多 agent 编排 **11 秒**给出 420 台，右边强基线 **50 秒**算出 365（把 R1 用在了在库而不是在途批次上）。

架构图见 [`shots/architecture.png`](shots/architecture.png)。

---

## 一、本体层：口径不是提示词，是代码执行的文件

`ontology/wms.yaml` 定义 6 个类、5 种关系、2 个指标公式、4 条规则。指标是**可求值的表达式**，不是自然语言：

```yaml
metrics:
  - name: available_inventory
    formula: on_hand - allocated - reserved - hold_qty + in_transit_arrived
  - name: promiseable_qty
    formula: max(0, available_inventory - safety_stock)
rules:
  - id: R1
    label: 临期批次不参与新单承诺
    when: {order_type: new_order, expiry_days_lt: 30}
    then: exclude_batch
  - id: R3
    label: 保税仓批次默认不可提前承诺
    when: {warehouse_type: bonded}
    then: exclude_warehouse
```

执行链：`app/ontology.py` 用 AST 安全求值公式 → `app/agents/inventory.py` 逐仓套公式、逐条判规则 → 命中 `exclude_batch` 的批次不进可用量，命中 `exclude_warehouse` 的仓可承诺记 0。

**这一点决定了项目的上限**：

- 数字只有一个来源，同一问题问十次都是 420，因为算的是公式；
- 改规则不用改代码——界面上关掉 R3，文件被改写，再问就变成 480；
- 答案脚注打印口径原文，评审可以核。

> R1 的作用对象是**在途批次**（TR-88213 的 80 台），不是上海仓的在库 500 台。这个语义写在代码里；只拿到「数据 + 规则文本」的模型区分不出来，这正是消融里强基线算成 365 的原因。

---

## 二、编排层：LLM 只在两个位置决策

```
用户提问
  → planner：规则抽 SKU/数量/时间窗，LLM 复判意图（commit / shortage / rule_change / lookup / supplier）
  → 记忆与澄清：Redis 会话槽位补全，缺参数就反问
  → 主 agent（LLM）：决定派谁、给每个子 agent 一句目标
  → 并行子 agent ×3（ReAct，最多 3 步，每次自己选工具）
  → 编排层确定性动作：补齐证据、规则前后对比、影响面按解析阈值重算
  → 组装：模板给结构，LLM 只做表述
  → verifier：确定性四项检查
  → Redis 落状态 + SSE 逐条推给前端
```

| 角色 | 负责 | 为什么不能交换 |
|---|---|---|
| 主 agent（LLM） | 派哪几个子 agent、目标是什么 | 不同问题需要不同取证组合，写死就退化成流水线 |
| 子 agent（LLM） | 在自己的工具清单里选哪个、传什么参数 | 同样是查库存，「算承诺」和「查扣留」用的工具不同 |
| 编排层（代码） | 补下限、算影响面、组装、校验 | 这些必须可复现，不能交给模型 |

**子 agent 的工具集**（共 22 个领域工具）：

- 库存子 agent：`compute_promise`、`simulate`、`get_inventory`、`get_batches`、`get_shipments`、`list_*`
- 图谱子 agent：`impact_of_rule`（4 跳）、`supplier_trace`（5 跳）、`substitute_coverage`（变长）、`get_rule_sources`
- 文档子 agent：`search_docs`（Milvus 语义检索）、`read_doc`

**ReAct 协议**：每步输出 `{"thought","action":"call|finish","tool","args"}`；步数用尽时先用已有观测作答，再不行退回确定性实现；每次决策、每次工具调用都写进 trace，前端据此画图与展示过程。

**幻觉的三道闸**：工具返回数字而不是文本；答案模板结构固定；verifier 独立重算并做数字溯源。

---

## 三、校验层：pass/fail 只由确定性检查决定

`app/agents/verifier.py` 做四件事：

1. 按口径独立重算可承诺合计（主供仓 + 保税仓），与 agent 给的总数比对；
2. 规则挡掉的仓/批次是否真的被排除；
3. 调拨量是否超过缺口（防重复计算）；
4. 答案里的数字能否在中间结果里找到来源。

校验器先判断载荷形状：承诺类问题没有分仓明细直接判 fail；归因类问题只做「证据 + 数字溯源」。模型复核（`LLM_REVIEW`）默认关闭——它慢、且会按自己的口径误判，只作为参考信息。

---

## 四、知识图谱：什么时候它才不可替代

Neo4j 里 **1298 个节点 / 3196 条关系**：

```
Supplier -PLACED_WITH<- PurchaseOrder -FROM_PO<- Batch -STORED_IN-> Warehouse
PurchaseOrder -CONTAINS-> SKU <-ORDERED_FOR- Order -TO_CUSTOMER-> Customer
SKU -SUBSTITUTE_OF{restriction}-> SKU
Doc -GOVERNS-> Rule <-OVERRIDES- Doc
```

**诚实说明**：1–2 跳的查询用 MySQL 外键就够了，图没有优势（我们测过差异为 0）。图的独立价值只出现在「跳数不固定 + 实体异构」的问题上：

- 规则收紧会影响哪些客户订单（规则→批次→物料→订单→客户）
- 供应商的批次流向了哪些客户（5 跳；SQL 要 5 表 join，且容易扇出重复）
- 替代链 1–3 跳能覆盖多少未交付需求

图的另一层用处是**溯源**：每条规则出自哪份文件、被哪份合同覆盖——这类关系在关系库里没有对应表。

同时要承认：Rule 节点是 `wms.yaml` 的 seed 快照，规则开关不会自动同步到图。图在这里是**派生视图**，口径的唯一来源始终是 YAML。

---

## 五、和「强化 RAG」的对照实验

`scripts/ablation.py` 跑五条臂，题库四道（承诺 / 规则影响 / 列表 / 影响面）：

| 臂 | Q1 承诺 | Q3 规则影响 | Q5 列表 | Q7 影响面 | 总正确率 | 平均耗时 |
|---|---|---|---|---|---|---|
| ① 强基线：规则+数据全给模型 | 1/2 | 0/2 | 0/2 | 0/2 | 12% | 34s |
| ② 强基线 + 图谱关系文本 | 1/2 | 0/2 | 0/2 | 0/2 | 12% | 34s |
| ③ 多 agent（本体执行，无图谱） | 2/2 | 2/2 | 2/2 | 2/2 | 100% | 12s |
| ④ 完整系统 | 2/2 | 2/2 | 2/2 | 2/2 | 100% | 15s |
| ⑤ NL2SQL（模型自己写 SQL） | 0/2 | 0/2 | 0/2 | 0/2 | 0% | 29s |

三条结论：

1. **信息相同、只差「谁算」，就是 12% 对 100%，而且慢 2–3 倍。** 强基线把 R1 作用在在库上算成 365；系统按在途批次算得到 420。
2. **无 SKU 的全局问题，强基线连数据都组不出来**——取数本身需要一个查询计划。
3. **NL2SQL 全错不是因为不会写 SQL，是口径不在库里**：公式和阈值在 YAML 里，数据库只有原始列，模型只能硬编码或漏掉。

细节与测量缺陷见 [`docs/ablation.md`](docs/ablation.md)。

---

## 六、数据与存储分工

| 存储 | 存什么 | 规模 |
|---|---|---|
| MySQL 8.0 | 事实：库存、批次、在途、订单、客户、供应商、采购单 | 389 库存格 / 761 批次 / 200 订单 / 11 张表 |
| Neo4j 5.20 | 关系与治理 | 1298 节点 / 3196 关系 |
| Milvus 2.4.9 | 文档向量（12 份 SOP/合同/邮件，49 段） | bge-m3 1024 维 |
| Redis 7.4 | 会话、任务快照、编排轨迹 | 会话 24h / 任务 1h |
| `ontology/wms.yaml` | 口径与规则 | 唯一来源 |

---

## 七、技术栈

| 层 | 技术 |
|---|---|
| 交互 | 原生 JavaScript（无框架）、EventSource / SSE、CSS 动画 |
| 接口 | FastAPI 0.165、uvicorn；同步 `/api/ask`、异步 `/api/ask/async`、`/api/events/{id}` |
| 编排 | 自研 orchestrator（约 1200 行）+ ReAct 运行时 + 线程池并行派发 |
| 工具 | 22 个领域工具，统一 JSON 返回，全部带 backend 标记 |
| 存储 | MySQL 8.0 / Neo4j 5.20 / Milvus 2.4.9 / Redis 7.4 |
| 模型 | DeepSeek `deepseek-flash`（推理模型）、`BAAI/bge-m3`（1024 维，硅基流动） |
| 登录 | 单管理员，HMAC 签名 cookie，密码放 `/etc/wms/wms.env` |
| 部署 | systemd + uvicorn，云服务器 `117.72.15.179:8000` |

---

## 八、目录

```
app/
  orchestrator.py        编排：规划 → 派发 → 合并 → 组装 → 校验
  ontology.py            本体：AST 公式求值、规则执行、开关改写
  auth.py                单管理员登录（HMAC cookie）
  agents/
    planner.py           意图与槽位抽取
    base.py              ReAct 运行时（步数上限、退化、trace）
    workers.py           三个子 agent 的工具清单与结果归一
    inventory.py         确定性库存计算（本体口径）
    verifier.py          确定性校验
  tools/
    ops.py               领域工具集
    graph_tool.py        Neo4j 查询（含降级）
    mysql_tool.py        MySQL 查询（含降级）
    doc_tool.py          Milvus 检索（bge / 本地 hash 双 provider）
    dashboard.py         仓储看板聚合
    session.py           会话记忆（Redis）
    state.py             任务快照与轨迹（Redis）
  static/                index.html / login.html（单文件前端，不打包）
ontology/wms.yaml        口径与规则（唯一来源）
data/docs/*.md           12 份 SOP / 合同 / 邮件
db/mysql/schema.sql      11 张表
scripts/
  seed.py                灌库 + 数据自检（--check）
  smoke_demo.py          上台前自检（含登录）
  ablation.py            五臂消融实验
  check_deps.py          依赖体检（--init-schema）
docs/
  agent-design.md        编排与图谱设计细节
  ablation.md            消融结果与测量缺陷
  DEMO-DATA.md           数据字典
```

---

## 九、常见追问

**Q：为什么要四个数据库？**
各解决不同问题：MySQL 存事实，图存路径与溯源，Milvus 存非结构化文本，Redis 存运行态。每个都能一句话说清为什么不用别的替代。

**Q：为什么不用 LangGraph？**
这个项目的重点是编排判断本身，框架会把它藏起来；而且有几处必须由代码决定（影响面按解析阈值重算、数字可溯源、缺证据补齐），写进图里更绕。手写约 1200 行，延迟与可观测性都可控。如果需求变成「可中断、任意节点恢复、多轮反思」，会换成 LangGraph——那是它的强项。

**Q：主 agent 和子 agent 的区别？**
主 agent 决定**分工**（派谁、目标），子 agent 决定**取证方式**（选哪个工具、传什么参数）。两者都是 LLM 决策，但决策对象不同；编排层只做保底与计算，不替代它们。

**Q：怎么保证 LLM 不编数字？**
三层：工具返回结构化数字、答案模板固定结构、verifier 独立重算并做数字溯源。模型只负责组织语言。

**Q：知识图谱是不是多余的？**
在 1–2 跳的问题上确实多余，我们测过差异为 0。它只在 4–5 跳、异构实体、需要溯源时才有独立价值——所以代码里把它做成「查询能力」，而不是必经路径。

---

## 十、已知边界

- 数据是合成的；规模为演示设计（389 库存格 / 761 批次），不是生产量级。
- 没有出入库流水表，所以「为什么库存从 500 变成 420」只能解释当前状态，追不到具体事件；预测也缺少历史出库序列。
- 断点恢复只做到「工具副作用前落盘」，整轮不能从中间续跑。
- 登录是最小实现：单账号、明文比对环境变量里的密码；真实系统应接 SSO 并哈希存储。
- 消融每格 2 次，0/50/100 粒度较粗；强基线的部分失败来自推理模型的 token 预算（已加重试），仍属模型相关结论。
