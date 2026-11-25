# 交接说明（给下一个会话）

## 0. 背景

项目名 WarehouseLens，用途是联想供应链二面的现场演示，不是生产系统。核心主张：在 RAG 之上加一层可执行的 ontology 和知识图谱，让系统能回答"这单能不能承诺""为什么缺货""这条规则生效后影响什么"，而 RAG 只能回答"规定是怎么写的"。

用户明确要求：少讲比喻，不要长篇铺垫，先问再做，不要自己补方案。

## 1. 已确定的技术构成

| 组件 | 职责 | 不可替代的理由 |
|---|---|---|
| ontology（`ontology/wms.yaml`） | 类、关系、指标口径、业务规则 | 口径被代码读取执行，改文件行为就变 |
| MySQL | 库存、批次、在途、替代料明细 | 数值计算的数据源 |
| Neo4j | 物料、批次、仓库、在途、规则与文档的溯源 | 多跳遍历，单跳查询用 SQL 更快 |
| Milvus | SOP、合同、邮件等文档片段向量 | 非结构化证据 |
| Redis | agent 共享状态与 trace | 前端靠它展示编排过程 |
| 六个 agent | planner、inventory、graph、doc、forecast（预留）、verifier | 按数据源边界分，可并行，校验独立 |
| RAG 基线 | 只用 doc agent，向量检索后直接回答 | 用来和系统路径并排对比 |

## 2. 已完成的东西（都在 `E:\ResumeProjects\WarehouseLens`）

```
docker-compose.yml      四个库（mysql/neo4j/redis/milvus+etcd+minio）
.env.example            连接配置模板
requirements.txt        Python 依赖
run.py                  本地启动入口
ontology/wms.yaml       本体：6 类、5 关系、2 指标、4 规则、1 口径冲突
db/mysql/schema.sql     建表语句
data/docs/*.md          6 份虚拟文档（SOP、合同、两封邮件）
app/config.py           环境变量
app/ontology.py         公式求值器 + 规则匹配器 + 规则开关写回文件
app/llm.py              live / replay 双模式
app/tools/dataset.py    本地数据层，同时是灌库的唯一数据来源
app/tools/mysql_tool.py 优先查 MySQL，失败退本地层
app/tools/graph_tool.py 优先查 Neo4j，失败退本地层
app/tools/doc_tool.py   优先查 Milvus，失败退本地关键词
app/tools/state.py      优先用 Redis，失败退进程内字典
app/agents/*.py         六个 agent
app/orchestrator.py     编排、答案组装、RAG 基线、规则影响模拟
app/main.py             FastAPI 接口
app/static/index.html   单页前端，含并排对比、编排轨迹、本体面板
scripts/seed.py         灌数据（--only mysql/neo4j/milvus/redis）
docs/agent-design.md    编排设计
docs/ontology-explainer.md 本体入门与面试说法
```

前端在本地已经能跑通，当时四个库都连不上，走的是本地数据层降级。没有做过任何真实数据库连接验证。

## 3. 演示用的三个问题与数字故事

数据设计成自洽的：上海仓可用 420，在途 80 因批次剩余效期 25 天被规则 R1 挡掉，合肥仓 90 台可用（在库 120，扣留 30），所以方案是 420 加合肥调拨 80 等于 500。

| 编号 | 问题 | 考察点 |
|---|---|---|
| Q1 | 客户 C-2024-017 下周一要 500 台 NB-X1C-14，能承诺吗 | 口径计算、规则拦截、跨仓调拨 |
| Q2 | 上周合肥仓 14 寸机型缺货率为什么上升 | 数值归因加文档证据 |
| Q3 | 新合同允许保税仓批次提前承诺，这条规则生效会影响什么 | 改 ontology，行为变化（前后对比 420 到 480） |

四条规则：R1 临期批次不参与新单承诺，R2 保税仓未清关不计可用，R3 保税仓默认不可提前承诺，R4 备件仓只服务售后。

## 4. 环境事实与踩过的坑

- 本机只有 Python 3.14.7（`C:\Python314`），没有其他版本
- `python -m venv` 在 3.14 上 ensurepip 失败，venv 不可用，只能用 `pip install --user`
- pip 在沙箱内解包会被拒写临时目录，需要在完全权限下执行安装
- 依赖已装在用户目录：fastapi 0.165.0、pymilvus 3.0.3、neo4j 6.4.1、redis 8.2.0、pymysql 1.2.4、numpy 2.3.5
- Docker 本机没装，也没有 WSL，所以数据库只能走云上
- `docs/agents.md` 这个文件名会被当成 AGENTS.md 加载，已改名为 `docs/agent-design.md`，不要再改回去
- LLM 目前是 replay 模式，没有配 API key

## 5. 云上资源

- 地址 `117.72.15.179`
- 四个库已经运行，但都是空库，没有建表、没有数据
- **密码未知，Milvus 版本未知**，这两项必须先问用户

## 6. 下一步顺序

1. 连测：3306、7474、7687、6379、19530 五个端口，各连十次，报最小/最大/平均延迟和失败次数
2. 协议验证：MySQL 握手、Redis PING、Neo4j bolt 会话、Milvus 列集合
3. 建表灌数：`python scripts/seed.py`，失败就 `--only` 逐个排查
4. 前端联调：`python run.py`，打开 8000，跑 Q1 看数字是否 420/90/500
5. 录屏兜底
6. 打包上云部署

## 7. 未验证的风险

- pymilvus 3.0.3 与云上 Milvus 服务端版本可能不兼容，2.4.x 服务端大概率连不上，需要降到对应客户端或升级服务端
- neo4j Python 驱动 6.4.1 对 5.x 服务端一般可用，但要实测
- 前端只做过接口冒烟，没在浏览器里看过
- 没有配 LLM key，面试时如果要用真模型需要临时配

## 8. 需要用户回答的问题

1. MySQL、Neo4j、Redis 的密码，以及数据库名（默认 wms）
2. 云上 Milvus 的镜像版本号
3. 二面具体时间，还剩多少小时
4. 现场开网页演示，还是录屏加讲设计
5. 四个库是否必须都出现在演示里，还是允许砍到两三个
