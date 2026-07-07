"""轻反应性求值引擎:条件(Expr 列表 = AND)与效果。

state 是一个 {var_key: value} 的可变字典(var_key 形如 "namespace.name")。
条件 op: == != > >= < <=    效果 op: set/= += -=
未定义变量按其类型默认(int=0, bool=False, string="")处理,缺类型时按 None→0/"".
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Tuple

from .models import Expr, Project, VarType


def build_initial_state(project: Project) -> Dict[str, Any]:
    """从 project 的 variables + meta_progress 建初始 state。"""
    state: Dict[str, Any] = {}
    allvars = list(project.variables) + list(project.meta_progress.fields)
    for v in allvars:
        key = f"{v.namespace}.{v.name}"
        if v.default is not None:
            state[key] = v.default
        elif v.type == VarType.bool:
            state[key] = False
        elif v.type == VarType.string:
            state[key] = ""
        else:
            state[key] = 0
    return state


def _coerce_num(x: Any) -> float:
    if isinstance(x, bool):
        return 1.0 if x else 0.0
    if isinstance(x, (int, float)):
        return float(x)
    try:
        return float(x)
    except (TypeError, ValueError):
        return 0.0


def eval_condition(cond: Iterable[Expr], state: Dict[str, Any]) -> bool:
    """Expr 列表按 AND 求值。空列表恒真。"""
    for c in cond:
        cur = state.get(c.var)
        if cur is None:  # 缺失变量按其比较值的类型给默认(与 build_initial_state 口径一致)
            cur = False if isinstance(c.value, bool) else ("" if isinstance(c.value, str) else 0)
        v = c.value
        op = c.op
        if op == "==":
            ok = cur == v
        elif op == "!=":
            ok = cur != v
        elif op in (">", ">=", "<", "<="):
            a, b = _coerce_num(cur), _coerce_num(v)
            ok = (a > b if op == ">" else a >= b if op == ">=" else a < b if op == "<" else a <= b)
        else:
            ok = False
        if not ok:
            return False
    return True


def apply_effects(effects: Iterable[Expr], state: Dict[str, Any]) -> None:
    """就地应用效果到 state。"""
    for e in effects:
        op = e.op
        if op in ("set", "="):
            state[e.var] = e.value
        elif op in ("+=", "-="):
            cur = state.get(e.var, 0)
            # 字符串累加器(如 meta.已通关路线集合):+= 做拼接,-= 对字符串无定义(忽略)
            if isinstance(cur, str) or isinstance(e.value, str):
                if op == "+=":
                    base = cur if isinstance(cur, str) else ""
                    state[e.var] = base + str(e.value)
                continue
            delta = _coerce_num(e.value)
            r = _coerce_num(cur) + (delta if op == "+=" else -delta)
            # 两端皆整数时保留 int
            if isinstance(cur, int) and not isinstance(cur, bool) and isinstance(e.value, int) and not isinstance(e.value, bool):
                r = int(r)
            elif r.is_integer():
                r = int(r)
            state[e.var] = r


def referenced_vars(exprs: Iterable[Expr]) -> List[str]:
    return [e.var for e in exprs]


def all_condition_vars(project: Project) -> List[Tuple[str, str]]:
    """返回 (var_key, 来源描述) 列表:所有被条件/触发读取的变量。"""
    out: List[Tuple[str, str]] = []
    for c in project.connections:
        for e in c.condition:
            out.append((e.var, f"connection:{c.id}"))
    for n in project.nodes:
        for e in n.condition:
            out.append((e.var, f"node:{n.id}"))
    for s in project.scenes:
        for e in s.preconditions:
            out.append((e.var, f"scene:{s.id}:precond"))
        for ch in s.choices:
            for e in ch.condition:
                out.append((e.var, f"scene:{s.id}:choice"))
    for en in project.endings:
        for e in en.trigger:
            out.append((e.var, f"ending:{en.node_id}"))
    for r in project.reveal_layers:
        for e in r.trigger:
            out.append((e.var, f"reveal:{r.id}"))
    return out


def all_effect_vars(project: Project) -> List[Tuple[str, str]]:
    """返回 (var_key, 来源) 列表:所有被效果写入的变量。"""
    out: List[Tuple[str, str]] = []
    for c in project.connections:
        for e in c.effects:
            out.append((e.var, f"connection:{c.id}"))
    for s in project.scenes:
        for e in s.effects:
            out.append((e.var, f"scene:{s.id}"))
        for ch in s.choices:
            for e in ch.effects:
                out.append((e.var, f"scene:{s.id}:choice"))
    return out
