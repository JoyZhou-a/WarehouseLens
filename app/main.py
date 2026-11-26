"""HTTP 接口。前端是单页文件，不打包。"""

from __future__ import annotations

import json
import threading
import time

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel

from . import config, deps, llm
from .ontology import get_ontology
from .orchestrator import ask, new_task
from .tools import doc_tool, graph_tool, mysql_tool
from .tools import session as session_tool
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
    {"id": "Q4", "text": "如果临期线收紧到 20 天，会影响哪些客户订单",
     "hint": "影响面：规则→批次→物料→订单→客户，4 跳（只有图能一趟走完）"},
    {"id": "Q5", "text": "供应商 S-2201 的批次流向了哪些客户",
     "hint": "供应商链路：采购单→批次→物料→订单→客户，5 跳"},
    {"id": "Q6", "text": "这周哪些货要发？",
     "hint": "列表枚举，RAG 完全答不了"},
]


class AskBody(BaseModel):
    question: str
    mode: str = "system"
    session_id: str | None = None


class AskAsyncBody(AskBody):
    pass


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


MODES = ("system", "system_nograph", "pipeline", "baseline", "rag_data", "rag_data_graph",
         "nl2sql")


@app.post("/api/ask")
def ask_api(body: AskBody) -> dict:
    question = body.question.strip()
    if not question:
        raise HTTPException(status_code=400, detail="问题为空")
    if body.mode not in MODES:
        raise HTTPException(status_code=400, detail=f"mode 只能是 {'、'.join(MODES)}")
    return ask(question, body.mode, body.session_id)


@app.post("/api/ask/async")
def ask_async(body: AskAsyncBody) -> dict:
    """后台跑，前端用 /api/events/{task_id} 订阅进度，避免等 15 秒白屏。"""
    question = body.question.strip()
    if not question:
        raise HTTPException(status_code=400, detail="问题为空")
    if body.mode not in MODES:
        raise HTTPException(status_code=400, detail=f"mode 只能是 {'、'.join(MODES)}")
    task_id = new_task()

    def worker() -> None:
        try:
            ask(question, body.mode, body.session_id, task_id=task_id)
        except Exception as exc:  # 后台线程的异常要落到 trace 里，否则前端一直转圈
            state_tool.trace(task_id, "system", "exception", {}, False,
                             {"error": f"{type(exc).__name__}: {exc}"[:200]}, 0)
            state_tool.save(task_id, {"task_id": task_id, "question": question,
                                      "mode": body.mode, "answer": "执行失败，请重试",
                                      "verification": {"passed": False, "issues": []},
                                      "trace": state_tool.events(task_id)})

    threading.Thread(target=worker, daemon=True).start()
    return {"task_id": task_id}


@app.get("/api/events/{task_id}")
def events(task_id: str) -> StreamingResponse:
    """SSE：把 trace 增量吐给前端，最后一条发 state，前端据此收尾。"""

    def gen():
        seen = 0
        deadline = time.time() + 180
        while time.time() < deadline:
            evs = state_tool.events(task_id)
            if len(evs) > seen:
                for e in evs[seen:]:
                    yield f"data: {json.dumps(e, ensure_ascii=False, default=str)}\n\n"
                seen = len(evs)
            st = state_tool.load(task_id)
            if st:
                yield "event: done\ndata: " + json.dumps(
                    {"task_id": task_id, "elapsed_ms": st.get("elapsed_ms"),
                     "agent_timings": st.get("agent_timings")}, ensure_ascii=False) + "\n\n"
                return
            yield ": ping\n\n"
            time.sleep(0.15)
        yield "event: timeout\ndata: {}\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.post("/api/session")
def session_new() -> dict:
    return {"session_id": session_tool.new_session()}


@app.get("/api/sessions")
def session_list() -> list[dict]:
    return session_tool.list_sessions()


@app.get("/api/session/{session_id}")
def session_detail(session_id: str) -> dict:
    return {"session_id": session_id, "messages": session_tool.messages(session_id)}


@app.delete("/api/session/{session_id}")
def session_delete(session_id: str) -> dict:
    session_tool.clear(session_id)
    return {"deleted": session_id}


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
