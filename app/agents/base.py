"""子 agent 运行时：带工具的最小 ReAct 循环。

每个子 agent 持有自己的工具清单，由模型决定「调哪个工具、传什么参数、要不要再调一次」，
工具负责取数与算数。模型不产生数字，只做选择与解释。

协议（模型只输出 JSON）：
    {"thought": "...", "action": "call", "tool": "工具名", "args": {...}}
    {"thought": "...", "action": "finish", "final": {...}}

失败策略：模型不可用、JSON 解析失败、超过步数 —— 一律退回该 agent 的确定性 fallback，
保证演示不会因为模型抽风而中断。
"""

from __future__ import annotations

import inspect
import json
import time
from typing import Any, Callable

from .. import llm

MAX_STEPS = 2
MAX_OBS_CHARS = 700
DECISION_TOKENS = 1200
FORCE_TOKENS = 1500
FORCE_FINISH = ("工具调用已到上限，不能再调工具。现在只能输出："
                '{"thought":"基于已有观测的结论",'
                '"action":"finish","final":{ ... }}')


class Tool:
    def __init__(self, name: str, desc: str, fn: Callable[..., Any], params: dict | None = None):
        self.name = name
        self.desc = desc
        self.fn = fn
        self.params = params or self._infer()

    def _infer(self) -> dict:
        sig = inspect.signature(self.fn)
        out = {}
        for pname, p in sig.parameters.items():
            ann = p.annotation
            if ann is inspect.Parameter.empty:
                typ = "any"
            elif ann in (int, "int"):
                typ = "int"
            elif ann is str:
                typ = "str"
            elif "dict" in str(ann):
                typ = "object"
            elif "list" in str(ann):
                typ = "list"
            else:
                typ = "any"
            out[pname] = {"type": typ, "required": p.default is inspect.Parameter.empty}
        return out

    def schema_line(self) -> str:
        ps = ", ".join(f"{k}:{v['type']}{'' if v['required'] else '?'}" for k, v in self.params.items())
        return f"- {self.name}({ps}) — {self.desc}"

    def call(self, args: dict) -> dict:
        sig = inspect.signature(self.fn)
        accepted = {k: v for k, v in (args or {}).items() if k in sig.parameters}
        missing = [k for k, p in sig.parameters.items()
                   if p.default is inspect.Parameter.empty and k not in accepted]
        if missing:
            return {"error": f"缺少必填参数 {missing}"}
        try:
            return self.fn(**accepted)
        except Exception as exc:
            return {"error": f"{type(exc).__name__}: {exc}"[:300]}


def _truncate(obj: Any, limit: int = MAX_OBS_CHARS) -> str:
    text = json.dumps(obj, ensure_ascii=False, default=str)
    if len(text) <= limit:
        return text
    if isinstance(obj, dict):
        slim = {}
        for k, v in obj.items():
            if isinstance(v, list) and len(v) > 3:
                slim[k] = v[:3]
                slim[f"{k}_count"] = len(v)
            else:
                slim[k] = v
        text = json.dumps(slim, ensure_ascii=False, default=str)
    return text[:limit] + "…" if len(text) > limit else text


SYSTEM_TMPL = """你是「{name}」，{role}

你可以调用这些工具（只输出 JSON，不要输出别的）：
{tools}

输出格式，二选一：
1) 调工具：{{"thought":"为什么要调","action":"call","tool":"工具名","args":{{...}}}}
2) 结束： {{"thought":"结论怎么来的","action":"finish","final":{{...}}}}

规则：
- 最多调用 {max_steps} 次工具，够了就 finish。
- 数字只能来自工具返回，不能自己算、不能编造。
- 工具返回 error 时，换参数或换工具再试一次；仍失败就在 final 里说明缺什么。
- final 里给出你判断所需的关键事实和依据，不要写长篇解释（编排层会写最终答案）。
"""


class ReActAgent:
    def __init__(self, name: str, role: str, tools: list[Tool],
                 fallback: Callable[[str, dict], dict] | None = None,
                 max_steps: int = MAX_STEPS):
        self.name = name
        self.role = role
        self.tools = {t.name: t for t in tools}
        self.fallback = fallback
        self.max_steps = max_steps

    def run(self, task: str, ctx: dict | None = None) -> dict:
        ctx = ctx or {}
        trace = ctx.get("trace")
        started = time.time()
        steps: list[dict] = []

        if not llm.available():
            return self._fallback(task, ctx, steps, "模型不可用", started)

        system = SYSTEM_TMPL.format(
            name=self.name, role=self.role,
            tools="\n".join(t.desc_line() if hasattr(t, "desc_line") else t.schema_line()
                            for t in self.tools.values()),
            max_steps=self.max_steps,
        )
        transcript = [f"任务：{task}"]
        observations: dict[str, dict] = {}
        last_obs: dict = {}

        for step_no in range(1, self.max_steps + 1):
            t0 = time.time()
            decision = llm.chat_json(system, "\n".join(transcript), max_tokens=DECISION_TOKENS)
            if not decision:
                # 模型偶尔会吐散文，提醒一次再试，别急着退回确定性路径
                transcript.append("上一次输出不是合法 JSON。只输出 JSON 对象，不要任何解释文字。")
                decision = llm.chat_json(system, "\n".join(transcript), max_tokens=DECISION_TOKENS)
            if not decision:
                steps.append({"step": step_no, "thought": "（模型返回无法解析）", "action": "error"})
                return self._fallback(task, ctx, steps, "模型输出无法解析", started,
                                      observations, last_obs)

            action = str(decision.get("action", "")).lower()
            thought = str(decision.get("thought", ""))[:300]

            if action == "finish":
                final = decision.get("final") or {}
                steps.append({"step": step_no, "thought": thought, "action": "finish",
                              "ms": int((time.time() - t0) * 1000)})
                if trace:
                    trace(self.name, "finish", {"thought": thought}, True,
                          {"final_keys": list(final)[:6]}, int((time.time() - t0) * 1000))
                return {"agent": self.name, "ok": True, "result": final, "steps": steps,
                        "observations": observations, "last_observation": last_obs,
                        "degraded": False, "elapsed_ms": int((time.time() - started) * 1000)}

            tool_name = decision.get("tool") or ""
            args = decision.get("args") or {}
            tool = self.tools.get(tool_name)
            if not tool:
                obs = {"error": f"没有名为 {tool_name} 的工具，可用：{list(self.tools)}"}
            else:
                obs = tool.call(args)

            if isinstance(obs, dict) and "error" not in obs:
                observations[tool_name] = obs
                last_obs = obs

            steps.append({"step": step_no, "thought": thought, "action": "call",
                          "tool": tool_name, "args": args,
                          "ok": "error" not in obs,
                          "ms": int((time.time() - t0) * 1000)})
            if trace:
                trace(self.name, tool_name, args, "error" not in obs,
                      _truncate(obs, 400), int((time.time() - t0) * 1000))
            transcript.append(json.dumps({"thought": thought, "action": "call",
                                          "tool": tool_name, "args": args}, ensure_ascii=False))
            transcript.append("observation: " + _truncate(obs))

        # 步数用尽：如果已经拿到工具观测，就直接用它（编排层会写最终答案），
        # 不再多花一次模型调用去让模型复述——把延迟留给真正需要的环节。
        if last_obs:
            steps.append({"step": len(steps) + 1, "action": "use_observations",
                          "thought": "步数用尽，已有工具观测，跳过收尾"})
            if trace:
                trace(self.name, "use_observations", {}, True,
                      {"keys": list(last_obs)[:6]}, 0)
            return {"agent": self.name, "ok": True, "result": last_obs, "steps": steps,
                    "observations": observations, "last_observation": last_obs,
                    "degraded": False, "elapsed_ms": int((time.time() - started) * 1000)}

        # 一次工具都没调成：再给模型一次收尾机会，否则退回确定性路径
        decision = llm.chat_json(system, "\n".join(transcript + [FORCE_FINISH]),
                                 max_tokens=FORCE_TOKENS)
        if decision and str(decision.get("action", "")).lower() == "finish":
            final = decision.get("final") or {}
            steps.append({"step": len(steps) + 1,
                          "thought": str(decision.get("thought", ""))[:300],
                          "action": "finish_forced"})
            if trace:
                trace(self.name, "finish_forced", {}, True, {"final_keys": list(final)[:6]}, 0)
            return {"agent": self.name, "ok": True, "result": final, "steps": steps,
                    "observations": observations, "last_observation": last_obs,
                    "degraded": False, "forced_finish": True,
                    "elapsed_ms": int((time.time() - started) * 1000)}

        return self._fallback(task, ctx, steps, f"超过 {self.max_steps} 步仍未收敛", started,
                              observations, last_obs)

    def _fallback(self, task: str, ctx: dict, steps: list[dict], reason: str,
                  started: float, observations: dict | None = None,
                  last_obs: dict | None = None) -> dict:
        if not self.fallback:
            return {"agent": self.name, "ok": False, "result": {"error": reason},
                    "steps": steps, "observations": observations or {},
                    "last_observation": last_obs or {}, "degraded": True,
                    "elapsed_ms": int((time.time() - started) * 1000)}
        try:
            result = self.fallback(task, ctx)
        except Exception as exc:
            result = {"error": f"{type(exc).__name__}: {exc}"[:200]}
        steps.append({"step": len(steps) + 1, "thought": f"退回确定性路径：{reason}",
                      "action": "fallback"})
        trace = ctx.get("trace")
        if trace:
            trace(self.name, "fallback", {"reason": reason}, True,
                  {"keys": list(result)[:6]}, int((time.time() - started) * 1000))
        return {"agent": self.name, "ok": "error" not in result, "result": result,
                "steps": steps, "observations": observations or {},
                "last_observation": last_obs or {}, "degraded": True,
                "elapsed_ms": int((time.time() - started) * 1000)}
