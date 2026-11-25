"""本体加载与执行。

这个模块是整个系统的口径来源。指标公式和业务规则都从这里读，
代码里不写死任何计算公式。改 ontology/wms.yaml，这里的行为跟着变。
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import yaml

from . import config


class Ontology:
    def __init__(self, path: Path | None = None) -> None:
        self.path = Path(path) if path else config.ONTOLOGY_PATH
        self.reload()

    def reload(self) -> None:
        self.data = yaml.safe_load(self.path.read_text(encoding="utf-8"))
        self.version = str(self.data.get("version", "0"))
        self.metrics = self.data.get("metrics", {})
        self.rules = self.data.get("rules", [])
        self.classes = self.data.get("classes", {})
        self.relations = self.data.get("relations", [])
        self.constraints = self.data.get("constraints", [])
        self.conflicts = self.data.get("conflicts", [])

    # ---------------------------------------------------------- 指标

    def calc(self, name: str, values: dict, _depth: int = 0) -> float:
        """按公式求值。变量优先取传入值，其次是其他指标。"""
        if _depth > 8:
            raise RecursionError(f"指标递归过深: {name}")
        if name not in self.metrics:
            raise KeyError(f"未定义的指标: {name}")
        formula = self.metrics[name]["formula"]
        tree = ast.parse(formula, mode="eval")

        def lookup(var: str):
            if var in values and values[var] is not None:
                return float(values[var])
            if var in self.metrics:
                return self.calc(var, values, _depth + 1)
            raise KeyError(f"公式 {name} 里的变量 {var} 没有取值")

        def ev(node):
            if isinstance(node, ast.Expression):
                return ev(node.body)
            if isinstance(node, ast.BinOp):
                left, right = ev(node.left), ev(node.right)
                if isinstance(node.op, ast.Add):
                    return left + right
                if isinstance(node.op, ast.Sub):
                    return left - right
                if isinstance(node.op, ast.Mult):
                    return left * right
                if isinstance(node.op, ast.Div):
                    return left / right
                raise ValueError(f"不支持的运算符: {ast.dump(node.op)}")
            if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
                return -ev(node.operand)
            if isinstance(node, ast.Name):
                return lookup(node.id)
            if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
                return float(node.value)
            raise ValueError(f"公式里有不支持的语法: {formula}")

        return ev(tree)

    def formula_text(self, name: str) -> str:
        return self.metrics.get(name, {}).get("formula", "")

    # ---------------------------------------------------------- 规则

    @staticmethod
    def _match(when: dict, ctx: dict, thresholds: dict | None = None) -> bool:
        for key, expected in when.items():
            if key.endswith("_lt"):
                base = key[:-3]
                # 允许外部覆盖阈值，用于 what-if：例如把临期线从 30 天改成 20 天
                limit = (thresholds or {}).get(base, expected)
                actual = ctx.get(base)
                if actual is None or not (actual < limit):
                    return False
            else:
                if ctx.get(key) != expected:
                    return False
        return True

    def eval_rules(self, ctx: dict, off: set[str] | None = None,
                   thresholds: dict | None = None) -> list[dict]:
        """按条件匹配规则。

        off        临时关掉的规则 id（what-if 用，不改文件）
        thresholds 临时覆盖阈值（what-if 用）
        """
        hits = []
        for rule in self.rules:
            if not rule.get("enabled", True):
                continue
            if off and rule["id"] in off:
                continue
            if self._match(rule.get("when", {}), ctx, thresholds):
                hits.append({
                    "id": rule["id"],
                    "label": rule.get("label", ""),
                    "then": rule.get("then"),
                    "when": rule.get("when", {}),
                })
        return hits

    def toggle_rule(self, rule_id: str, enabled: bool) -> dict:
        """改文件而不是改内存。文件是唯一事实来源，改完立刻重载。"""
        text = self.path.read_text(encoding="utf-8")
        pattern = re.compile(
            r"(  - id: " + re.escape(rule_id) + r"\n(?:.*\n)*?    enabled: )(true|false)"
        )
        new_text, count = pattern.subn(
            lambda m: m.group(1) + ("true" if enabled else "false"), text, count=1
        )
        if count == 0:
            raise KeyError(f"规则 {rule_id} 没有 enabled 字段")
        # 固定写 LF：Windows 上默认会改成 CRLF，整个文件在 git 里变成全量改动
        self.path.write_text(new_text, encoding="utf-8", newline="\n")
        self.reload()
        rule = next((r for r in self.rules if r["id"] == rule_id), None)
        return {"rule": rule_id, "enabled": bool(rule and rule.get("enabled")),
                "version": self.version}

    # ---------------------------------------------------------- 展示

    def summary(self) -> dict:
        return {
            "version": self.version,
            "path": str(self.path.relative_to(config.ROOT)),
            "classes": [{"name": k, **v} for k, v in self.classes.items()],
            "relations": self.relations,
            "metrics": [{"name": k, **v} for k, v in self.metrics.items()],
            "rules": self.rules,
            "constraints": self.constraints,
            "conflicts": self.conflicts,
        }


_onto: Ontology | None = None


def get_ontology() -> Ontology:
    global _onto
    if _onto is None:
        _onto = Ontology()
    return _onto
