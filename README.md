# WMS 仓储归因助手

面向仓储场景的多 agent 问答系统，用来回答「这单能不能承诺」「为什么缺货」「这条规则生效后影响什么」。
对比基线是纯 RAG：向量检索命中文档后直接交给模型回答。

## 技术构成

| 组件 | 作用 | 在哪里 |
|---|---|---|
| ontology | 定义类、关系、指标口径、业务规则 | `ontology/wms.yaml` |
| MySQL | 库存、批次、在途等明细 | 容器 wms-mysql |
| Neo4j | 物料、批次、仓库、在途、规则的关系 | 容器 wms-neo4j |
| Milvus | 文档片段的向量 | 容器 wms-milvus |
| Redis | agent 共享状态与 trace | 容器 wms-redis |
| FastAPI | 后端与前端页面 | `app/` |

## 云上实测（117.72.15.179）

四个库已经在云上跑着，本地 `.env` 指过去就能用。实测结果：

| 库 | 版本 | 实测内容 | 十次连接（最小/平均/最大） |
|---|---|---|---|
| MySQL | 8.0.46 | 7 张表，库存 30 行 / 批次 40 / 在途 10 / 订单 20 | 39 / 85 / 136 ms |
| Neo4j | 5.20.0 | 106 节点 / 144 关系 | 37 / 62 / 102 ms |
| Redis | 7.4.12 | PING 通，state 与 trace 落这里 | 37 / 53 / 94 ms |
| Milvus | v2.4.9 | 2 个 collection（bge 1024 维 / hash 512 维），各 49 条向量 | 35 / 61 / 102 ms |

一键复验（只读，不写数据）：

```bash
python scripts/netcheck.py      # 每端口连 10 次 + 四个协议握手
python scripts/check_deps.py    # 四库体检 + 数据条数
python scripts/smoke_demo.py    # 跑三个演示问题，断言数字与数据源（需先起服务）
python scripts/seed.py --check  # 数据自检：引用完整性、数量对得上、文档与规则对得上
```

四个脚本的退出码在全部通过时才为 0，可以直接用在彩排脚本里。`seed.py` 每次写库前都会自动跑一遍自检，数据不自洽就拒绝写入。

### 库掉线时会发生什么

工具层内置本地数据集，真库不可用时退回本地并继续出结果，但**降级不是静默的**：降级时终端会打印原因，轨迹里每步都标 `backend`（`mysql` / `neo4j` / `milvus` / `local`），20 秒冷却后自动重试真库。`smoke_demo.py` 会断言三个数据源必须分别是 `mysql`、`neo4j`、`milvus`，防止演示时其实在读本地常量。

## 本机直接跑

数据库在云上，本机不需要 Docker：

```bash
copy .env.example .env      # 四个 host 改成 117.72.15.179（本仓库已有 .env）
python scripts/seed.py      # 首次或要重置数据时执行，可 --only mysql 单库灌
python run.py               # 打开 http://127.0.0.1:8000
```

### 接大模型

`.env` 里三项，key 建议走系统环境变量或服务器上的 `EnvironmentFile`，不要写进仓库：

```
LLM_MODE=live
LLM_API_KEY=sk-...
LLM_MODEL=deepseek-flash
LLM_REVIEW=off          # 模型复核默认关闭；打开会每题多 7-10 秒
```

`LLM_MODE=replay` 完全不调 API，答案走确定性模板，断网也能演示。live 模式下每题多 1-2 次调用，耗时约 6-8 秒；模型调用失败会自动退回模板，不会让请求失败。

### 向量化：bge-m3 与本地哈希双路

```
EMBED_PROVIDER=bge                       # bge 走租的接口，hash 走本地字符哈希
EMBED_API_KEY=sk-...                     # 硅基流动的 key，BAAI/bge-m3 不计费
EMBED_MODEL=BAAI/bge-m3
EMBED_DIM_BGE=1024
RERANK_ENABLED=off                       # 23 个片段用不上重排，见 docs/agent-design.md
```

两个 provider 各占一个 collection（`wms_docs_bge` 1024 维、`wms_docs_hash` 512 维），因为维度不同不能混查。bge 不可用时整条链自动切到 hash，页面 `backend` 会显示 `milvus/bge` 或 `milvus/hash`。换 provider 后要重灌向量：`python scripts/seed.py --only milvus`。

演示入口支持深链，现场不用手打问题：

- `/?q=Q1` 直接跑 Q1，`&cmp=1` 加上 RAG 并排对比
- `/?q=Q3&cmp=1` 跑规则变更
- `/?q=Q2&onto=1` 顺带展开本体面板

## 本地启动（自己起数据库时用）

```bash
# 1. 环境
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt

# 2. 配置
copy .env.example .env

# 3. 起数据库（首次拉镜像会慢）
docker compose up -d mysql neo4j redis etcd minio milvus

# 4. 等健康检查通过
docker compose ps

# 5. 灌数据
python scripts/seed.py

# 6. 只灌某一个库
python scripts/seed.py --only mysql
```

## 端口

| 服务 | 端口 |
|---|---|
| MySQL | 3306 |
| Neo4j Browser | 7474 |
| Neo4j Bolt | 7687 |
| Redis | 6379 |
| Milvus | 19530 |
| 应用 | 8000 |

## 依赖体检

四个库起来没有、起来后有没有数据，一个页面看完。这两个入口用的是同一份检查逻辑（`app/deps.py`）：

```bash
python scripts/check_deps.py          # 命令行版，全部通过时退出码才为 0
python run.py                         # 然后打开 http://127.0.0.1:8000/health
```

页面每 5 秒自动刷新一次，除了红绿灯，还显示每个库的实际地址、耗时和具体报错，方便判断是「连不上」还是「连上了但没建表／没灌数据」。检查是并发跑的，四个全挂也是 3 秒内出结果。

常见两种黄灯：

- MySQL 显示「连上了，但库里没有表」→ `python scripts/check_deps.py --init-schema`
- Neo4j 显示「图里没有节点」→ `python scripts/seed.py --only neo4j`

## 上云

数据库跑在云上，本地通过公网 IP 调用。

1. 云服务器装 Docker：`curl -fsSL https://get.docker.com | sh -s -- --mirror Aliyun`，再 `systemctl enable --now docker`。
2. 把仓库 clone 到云上，`cp .env.example .env`，改掉默认密码。
3. `docker compose up -d`，等 `docker compose ps` 全部 healthy。
4. 安全组放开 3306、7474、7687、6379、19530。
5. 本地 `.env` 把四个 host 改成云服务器公网 IP，然后 `python scripts/seed.py` 灌数据（数据写进云上的库）。
6. 打开 `http://<公网IP>:8000/health`，四个都绿就通了。

注意：这些端口直接暴露公网等于裸奔，演示期用完就关掉安全组。正式做法是只开 8000，让应用在云内网访问数据库。另外 `.env` 被 git 忽略了，云上那份要单独建。

## 目录

```
app/            后端与前端
  agents/       六个 agent 的实现
  tools/        工具层，访问四个数据源
  orchestrator  编排与校验
ontology/       本体定义
db/mysql/       建表语句
data/docs/      虚拟文档，供 Milvus 检索
scripts/seed.py 数据生成
docs/           设计说明
```

## 演示问题

| 编号 | 问题 | 看什么 |
|---|---|---|
| Q1 | 客户 C-2024-017 下周一要 500 台 NB-X1C-14，能承诺吗 | 可用量口径、临期批次被规则挡掉、跨仓调拨 |
| Q2 | 上周合肥仓 14 寸机型缺货率为什么上升 | 数值归因加文档证据 |
| Q3 | 新合同允许保税仓提前承诺，这条生效后影响什么 | 改 ontology，行为变化 |
