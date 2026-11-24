# Agent 编排设计

## 为什么分 agent

三类数据源的查询语言不同，MySQL 是 SQL，Neo4j 是 Cypher，Milvus 是向量检索。混在一个 agent 里，工具描述会互相干扰，出错时也定位不到是哪一层。检索可以并行，串行会拖慢。校验必须独立，否则执行者会给自己背书。

## Agent 清单

| id | 名称 | 数据源 | 工具 | 输入 | 输出 |
|---|---|---|---|---|---|
| planner | 规划 | 无 | 无 | 用户问题 | steps 列表，每步含 agent 和子目标 |
| inventory | 库存 | MySQL + ontology | sql_query, available_qty, eval_rules | sku, warehouse, 订单条件 | 可用量、可承诺量、命中规则 |
| graph | 关系 | Neo4j | cypher_paths | sku 或 warehouse | 多跳路径、替代料、在途、影响面 |
| doc | 文档 | Milvus | vector_search, read_doc | 检索语句 | 片段、出处、相似度 |
| forecast | 预测 | 无（预留） | 无 | sku, 时间窗 | 未实现时返回未实现标记 |
| verifier | 校验 | 无 | 读取 state | 全部中间结果 | pass 或 fail，fail 时给出修正要求 |

## 运行状态

状态存在 Redis，key 为 `state:{task_id}`，TTL 一小时。字段：

```
question     原始问题
plan         规划产出的步骤列表
cursor       当前执行到第几步
facts        {指标名: {value, source, rules_applied}}
evidence     [{doc_id, chunk, score, snippet}]
graph_hits   [{path, nodes, rels}]
issues       verifier 提出的问题
answer       最终答案
trace        [{ts, agent, tool, args, ok, ms}]
```

前端轮询 `/api/task/{task_id}` 拿 trace，用来展示编排过程。

## 编排流程

1. 收到问题，创建 task_id，Planner 产出 plan，写入 state。
2. 按 plan 执行。同一批可以并行的步骤用 `asyncio.gather` 并发，inventory、graph、doc 通常可并行。
3. 每个 agent 的工具调用追加到 trace。
4. 全部步骤结束后进 Verifier。
5. Verifier 检查三件事：每个数字是否有来源，规则命中是否被引用，证据之间是否矛盾。
6. fail 则把 issues 回填给对应 agent 重跑，最多两轮。仍 fail 则输出答案并标注未通过项。

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
