"""HTTP 接口。前端是单页文件，不打包。"""

from __future__ import annotations

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from . import config, deps, llm
from .ontology import get_ontology
from .orchestrator import ask
from .tools import doc_tool, graph_tool, mysql_tool
from .tools import state as state_tool

app = FastAPI(title="WMS 仓储归因助手", version="0.1")
STATIC = config.ROOT / "app" / "static"

DEMO_QUESTIONS = [
    {"id": "Q1", "text": "客户 C-2024-017 下周一要 500 台 NB-X1C-14，能承诺吗",
     "hint": "可用量口径、临期批次被规则挡掉、跨仓调拨"},
    {"id": "Q2", "text": "上周合肥仓 14 寸机型缺货率为什么上升",
     "hint": "扣留数量与在途延误的归因"},
    {"id": "Q3", "text": "新合同允许保税仓批次提前承诺，这条规则生效会影响什么",
     "hint": "改本体，行为变化"},
]


class AskBody(BaseModel):
    question: str
    mode: str = "system"


class RuleBody(BaseModel):
    enabled: bool


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC / "index.html")


@app.get("/health")
def health_page() -> FileResponse:
    """依赖体检页。库里有没有起、起了有没有数据，一页看完。"""
    return FileResponse(STATIC / "health.html")


@app.get("/api/deps")
def deps_check() -> dict:
    """体检数据。页面和 scripts/check_deps.py 共用这一个来源。"""
    return deps.check_all()


@app.get("/api/health")
def health() -> dict:
    return {
        "mysql": mysql_tool.ping(),
        "neo4j": graph_tool.ping(),
        "milvus": doc_tool.backend(),
        "state": state_tool.backend(),
        "llm": llm.status(),
        "ontology_version": get_ontology().version,
    }


@app.get("/api/demo")
def demo() -> list[dict]:
    return DEMO_QUESTIONS


@app.post("/api/ask")
def ask_api(body: AskBody) -> dict:
    question = body.question.strip()
    if not question:
        raise HTTPException(status_code=400, detail="问题为空")
    if body.mode not in ("system", "baseline"):
        raise HTTPException(status_code=400, detail="mode 只能是 system 或 baseline")
    return ask(question, body.mode)


@app.get("/api/task/{task_id}")
def task(task_id: str) -> dict:
    st = state_tool.load(task_id)
    if not st:
        raise HTTPException(status_code=404, detail="任务不存在或已过期")
    return st


@app.get("/api/ontology")
def ontology() -> dict:
    return get_ontology().summary()


@app.post("/api/ontology/rule/{rule_id}")
def toggle_rule(rule_id: str, body: RuleBody) -> dict:
    try:
        return get_ontology().toggle_rule(rule_id, body.enabled)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get("/api/graph")
def graph_overview() -> dict:
    return graph_tool.overview()


@app.get("/api/rule/{rule_id}/sources")
def rule_sources(rule_id: str) -> list[dict]:
    return graph_tool.rule_sources(rule_id)
