# Agent 编排设计

## 当前架构：主 agent + 三个子 agent（ReAct）

参考 supervisor / orchestrator-workers / Reason-Plan-ReAct 的通行做法：主 agent 负责拆解与派发，子 agent 是带工具的 ReAct 执行器，数字全部由工具算。

```
用户问题
  ↓
主 agent（一次模型调用）
  ├─ 判断类型：承诺 / 归因 / 规则变更 / 列表
  ├─ 抽参数：物料、数量、时间窗；缺关键参数就问回去，不再默认 500
  └─ 决定派谁，并行发出
        ├─ inventory 子 agent（MySQL + ontology，ReAct，2 步）
        │    工具：compute_promise / simulate / get_inventory / get_batches /
        │          get_shipments / list_orders / list_due_shipments /
        │          list_expiring_batches / list_blocked_supply
        ├─ graph 子 agent（Neo4j，ReAct，2 步）
        │    工具：get_substitutes / get_inbound_chain / get_rule_sources / get_rule_governance
        └─ doc 子 agent（Milvus/bge，ReAct，2 步）
             工具：search_docs / read_doc；命中不对题时会改写重查
  ↓
编排层：合并事实 → 规则变更类用确定性内核算 before/after → 模型写答案（失败退模板）
  ↓
verifier：确定性校验（口径重算、规则是否生效、调拨是否超缺口、数字能否溯源）
```

关键设计约束：

- **模型只做选择与表述，不做算术**。子 agent 的工具返回什么，事实就是什么；模型在 final 里转述丢了数据也没关系，编排层按工具优先级取原始返回。
- **步数上限 2，用尽即用已有观测**，不再为了「收尾」多花一次模型调用；模型不可用或跑飞时退回确定性内核（pipeline）并标记 degraded。
- **规则变更类的两组数由编排层算**，不依赖子 agent 恰好调对 simulate。
- **校验独立且确定**，模型复核默认关闭。

## 三种模式（前端可切换）

| 模式 | 谁拆任务 | 用途 |
|---|---|---|
| `system`（默认） | 主 agent（模型） | 演示多 agent 编排 |
| `pipeline` | 代码里写死的三条路 | 对照：固定流水线，快但只能答预置问题 |
| `baseline` | 无（纯 RAG） | 对照：只检索文档、不查库 |

## 旧版（pipeline）说明

下面是改造前的固定流水线设计，保留作为对照实现（`orchestrator._run_system`）：

## 为什么分 agent

三类数据源的查询语言不同，MySQL 是 SQL，Neo4j 是 Cypher，Milvus 是向量检索。混在一个 agent 里，工具描述会互相干扰，出错时也定位不到是哪一层。检索可以并行，串行会拖慢。校验必须独立，否则执行者会给自己背书。

## Agent 清单

| id | 名称 | 数据源 | 工具 | 输入 | 输出 |
|---|---|---|---|---|---|
| planner | 规划 | 无 | 无 | 用户问题 | steps 列表，每步含 agent 和子目标 |
| inventory | 库存 | MySQL + ontology | sql_query, available_qty, eval_rules | sku, warehouse, 订单条件 | 可用量、可承诺量、命中规则 |
| graph | 关系 | Neo4j | cypher_paths | sku 或 warehouse | 多跳路径、替代料、在途、影响面 || doc | 文档 | Milvus | vector_search, read_doc | 检索语句 | 片段、出处、相似度 |
| forecast | 预测 | 无（预留） | 无 | sku, 时间窗 | 未实现时返回未实现标记 |
| verifier | 校验 | 无 | 读取 state | 全部中间结果 | pass 或 fail，fail 时给出修正要求 |

## 运行状态

状态存在 Redis，key 为 `state:{task_id}`，TTL 一小时。实际写入的字段：

```
task_id / question / mode      标识与模式（system 或 baseline）
ontology_version               本次计算用的口径版本
parsed                         规划产物：intent、sku_id、demand、target_rule
plan                           执行步骤列表 [{agent, goal}]
facts                          {agent: 该 agent 的结构化结果}
evidence                       [{doc_id, chunk_no, text, score, backend}]
rule_impact                    规则变更类问题才有：before / after / delta
verification                   {passed, issues[]}
answer / elapsed_ms / trace
trace                          [{ts, agent, tool, args, ok, result, ms}]
```

trace 通过 `/api/ask` 的返回值直接给前端，另外也存 Redis 列表 `trace:{task_id}`，`/api/task/{task_id}` 可重放。

## 模型在哪里参与

算数的地方一个 agent 都没让模型碰，模型只出现在三个位置，且都可关闭：

| 位置 | 作用 | 失败时 | 默认 |
|---|---|---|---|
| planner | 意图分类（commit / shortage / rule_change） | 退回关键词规则 | 开（`LLM_MODE=live`） |
| 编排层 `_compose` | 用事实写答案正文，模板留作兜底 | 退回确定性模板 | 开（`LLM_MODE=live`） |
| verifier | 模型复核，只出参考意见 | 不出现该条 | **关**（`LLM_REVIEW=on` 才启用） |

模型复核默认关闭的原因：它不参与 pass/fail，却每次多花 7-10 秒，并且会按自己的口径误判（例如把调拨源算进合计得出「420 应该是 510」）。

给模型写答案时的约束：只传事实（各仓数字、命中规则、调拨方案、文档片段），要求在 150 字内、最多 4 句、不得引入新数字；页脚由代码追加，标明口径和「模型只负责表述」。

## 检索层

文档只有 6 份、切成 23 段，所以检索层刻意保持小：

- **向量化两路**：`wms_docs_bge`（BAAI/bge-m3，1024 维，租接口）与 `wms_docs_hash`（字符二元组哈希，512 维，离线）。维度不同不能混查，bge 失败时整条链切 hash，轨迹里标 `milvus/bge` 或 `milvus/hash`。
- **为什么要 bge**：哈希向量只对用词重合有效。实测同一组问题换说法后，哈希的 top1 分数从 0.46 掉到 0.11、关键段落掉出前三；bge 在改写问法下仍能命中 `SOP-BONDED`「默认不参与提前承诺」和「缺货率 2.1%→6.8%」那段，分数 0.68-0.72。
- **为什么不加重排**：用 top-8 召回跑 `bge-reranker-v2-m3` 对比，Q1 只是换了顺序、Q3 反而把合同第 7 条挤到第 4。召回已经覆盖所需段落，重排只增加一次网络调用和一个故障点。语料上百段时再谈。
- **查询构造**：问题原文 + 意图领域词 + SKU。领域词的作用是稀释客户号、星期几这类槽位词。

## 图库在答案里的两个出口

Neo4j 如果只被查一次、结果不进答案，那它就只是个装饰。现在它有两个必现出口：

1. **替代料与限制**：`SKU -SUBSTITUTE_OF-> SKU` 带 restriction 和可调批次，进事实包，缺口补不上时模型会提。
2. **规则出处**：`Doc -GOVERNS-> Rule`、`Doc -OVERRIDES-> Rule`。规则变更类问题的答案里由代码追加一行「规则来源（Neo4j 多跳）：R3 由 SOP-BONDED 规定；contract-C-2024-017 以 OVERRIDES 覆盖 R3」——这行不让模型生成，因为它必须与图完全一致。

注意这套数据只有 1-2 跳（SKU→SKU、Doc→Rule），没有更深的链路。要说「多跳」得先把链路做深（供应商→采购单→批次→仓→客户），否则名不副实。

## 编排流程

1. 收到问题，创建 task_id，Planner 产出 plan，写入 state。
2. 按 plan 执行。同一批可并行的步骤用 `ThreadPoolExecutor`（3 个 worker）并发，inventory、graph、doc 并行跑，`ms` 从提交那一刻开始计。
3. 每个 agent 的结果追加到 trace。
4. 全部步骤结束后进 Verifier。
5. Verifier 做四类确定性检查：按口径重算可承诺合计是否等于 agent 给的总数、命中规则的仓/批次是否真的被排除、调拨量是否超过缺口、答案里的数字能否在中间结果里找到来源。
6. 校验失败不自动重跑（重跑只会重复同样的计算），而是把 issues 带进结果，前端显示为「校验未通过」，由人决定要不要改口径。

## 可承诺量怎么聚合

这一步决定 Q1 和 Q3 的数字，代码在 `app/agents/inventory.py`：

- **直接计入本单可承诺**：主供仓（可承诺量最大的仓，本演示是上海 420）＋ 保税仓（提前承诺的含义就是保税批次直接计入可用，规则允许时才进）。
- **只作调拨源**：其他成品仓（合肥 90），货要先物理调到主供仓才能发，因此只出现在补缺口方案里，不进合计。
- **不参与**：被 R3 排除的保税仓、被 R4 排除的备件仓。

于是 Q1 是 420 ＋ 调拨 80 ＝ 500；关闭 R3 后保税仓 60 台直接计入，Q3 是 420 → 480。这样「可承诺合计」和「补充方案」不会把同一批货算两遍。


## 确定性约束

数字一律由代码算，LLM 只负责选工具和写解释。同一问题连问两次，答案里的数字必须一致，所以 state 里的 facts 一旦写入不允许被 LLM 改写，只能被工具覆盖。

## 演示用的三个问题

| 编号 | 问题 | 走哪些 agent | 展示点 |
|---|---|---|---|
| Q1 | 客户 C-2024-017 下周一要 500 台 NB-X1C-14，能承诺吗 | planner → inventory + graph + doc → verifier | 可用量口径、在途批次临期被规则挡掉、合肥调拨 |
| Q2 | 上周合肥仓 14 寸缺货率为什么上升 | planner → inventory + doc + graph → verifier | 数值归因加文档证据 |
| Q3 | 新合同允许保税仓批次提前承诺，这条生效后影响什么 | planner → doc + inventory → 规则写回 ontology → 重跑 | ontology 被代码读取，改规则行为就变 |

## 与 RAG 基线的对比

基线只用 doc agent：向量检索命中文档，直接让 LLM 回答。同一个问题，基线答「建议查询可用库存并结合在途判断」，系统答具体数量和调拨方案。前端提供开关切换，两个结果并排显示。
