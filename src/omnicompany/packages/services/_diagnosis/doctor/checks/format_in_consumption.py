# [OMNI] origin=claude-code domain=services/doctor/checks ts=2026-04-19T00:00:00Z
# [OMNI] material_id="material:diagnosis.doctor.format_in_consumption.static_checker.py"
"""F-15/P-13 "声明即消费" 静态检查 (M2 2026-04-19).

铁律（2026-04-19 用户明示）: **Format 禁搭便车**。Router.run() 从 input_data
读取的每个字段必须出现在 FORMAT_IN 对应 Format 的 json_schema 里（含 parent 继承）。
违反即 F-15 (format.md) / P-13 (pipeline.md) MUST 违约, 反模式 FA-08 / PA-11。

本模块提供一个纯 AST 函数, 接收 routers.py 和 formats.py 源码文本, 返回
violation findings。**不 import 被检代码, 不依赖 runner / pipeline**, 既可:
- 被 team_builder 生成代码的本地自检调用 (2026-07-03 批4: 原 LAP 九维检查器
  D9 已显式废止, 本模块的纯 AST 函数仍保留供其他消费方调用)
- 被 doctor Router 包装成诊断节点
- 被脚本全仓扫

算法:
  1. 解析 formats.py 抽每个 Format 的 id / json_schema.properties / parent
  2. resolve_fields(fmt_id) 递归沿 parent 链合并字段
  3. 扫 routers.py 每个 Router/LLMRouter/AgentNodeLoop 子类:
     - 抽 FORMAT_IN (str 或 list[str])
     - 在 run() 方法体抽 input_data.get("X") / input_data["X"] / 嵌套访问链
  4. 单入 vs 多入 fan-in 双分支: 单入查根键, 多入 list[str] 时首键是
     format_id 则查该 Format 子字段, 否则查 flatten 后字段并集
  5. 豁免 reports / _from_<node_id> 基础设施字段
  6. 动态访问降级 warn: **input_data 展开 / items() / 变量别名

Severity 约定:
  - critical: 直接违反 F-15/P-13 MUST, 必须修
  - warn: 动态访问（无法静态判） / 未知外部 Format（无法解析 schema）

历史: M2.α (2026-04-19) 首次实现在 workflow_factory/routers.py, M2.γ 抽到此处。
"""
from __future__ import annotations

import ast
from typing import Any


# 不统计的"框架字段" —— Router 基类/runner 注入, 不属于 Format 语义
_INFRA_FIELDS: frozenset[str] = frozenset({
    # P7.3 reports 容器 (runner 深合并, 非某个 Format 专属)
    "reports",
})
# 单独判断前缀
_INFRA_PREFIXES: tuple[str, ...] = ("_from_",)


def _extract_format_schemas(
    formats_py: str,
) -> tuple[dict[str, dict[str, Any]], list[str]]:
    """解析 formats.py, 返回 (fmt_id → {props, parent}, parse_errors)。

    props 是 json_schema.properties 的 top-level key 集合（set[str]）。
    """
    schemas: dict[str, dict[str, Any]] = {}
    errors: list[str] = []
    try:
        tree = ast.parse(formats_py)
    except SyntaxError as e:
        errors.append(f"formats.py syntax error: {e}")
        return schemas, errors

    def _const_value(node: ast.AST) -> Any:
        """尽力从 AST 节点抽常量值（支持嵌套 dict / list / tuple）。"""
        if isinstance(node, ast.Constant):
            return node.value
        if isinstance(node, ast.Dict):
            d: dict[str, Any] = {}
            for k, v in zip(node.keys, node.values):
                if isinstance(k, ast.Constant):
                    d[k.value] = _const_value(v)
            return d
        if isinstance(node, ast.List):
            return [_const_value(e) for e in node.elts]
        if isinstance(node, ast.Tuple):
            return tuple(_const_value(e) for e in node.elts)
        return None

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = getattr(node.func, "id", "") or getattr(node.func, "attr", "")
        if name != "Format":
            continue
        fmt_id: str | None = None
        parent: str | None = None
        schema: dict[str, Any] | None = None
        components: list[str] = []
        for kw in node.keywords:
            if kw.arg == "id" and isinstance(kw.value, ast.Constant):
                fmt_id = kw.value.value
            elif kw.arg == "parent" and isinstance(kw.value, ast.Constant):
                parent = kw.value.value
            elif kw.arg == "json_schema":
                val = _const_value(kw.value)
                if isinstance(val, dict):
                    schema = val
            elif kw.arg == "components":
                val = _const_value(kw.value)
                if isinstance(val, (list, tuple)):
                    components = [c for c in val if isinstance(c, str)]
        if not fmt_id:
            continue
        props: set[str] = set()
        if isinstance(schema, dict):
            p = schema.get("properties")
            if isinstance(p, dict):
                props = set(p.keys())
        schemas[fmt_id] = {
            "props": props,
            "parent": parent,
            "components": components,  # composite Format 的成员 Format ID 列表
        }
    return schemas, errors


def _resolve_fields(
    fmt_id: str,
    schemas: dict[str, dict[str, Any]],
    builtin_parents: dict[str, set[str]] | None = None,
    _seen: set[str] | None = None,
) -> set[str]:
    """递归沿 parent 链合并所有字段。

    builtin_parents: 外部 Format 字段表（如 built-in 'doc' 的字段）, 未解析到时
                     用兜底（都当作"未知字段, 不计入"）。
    """
    if _seen is None:
        _seen = set()
    if fmt_id in _seen:
        return set()  # 循环保护
    _seen.add(fmt_id)
    info = schemas.get(fmt_id)
    if info is None:
        if builtin_parents:
            return set(builtin_parents.get(fmt_id, set()))
        return set()
    fields = set(info["props"])
    if info["parent"]:
        fields |= _resolve_fields(info["parent"], schemas, builtin_parents, _seen)
    return fields


def _is_input_data_name(node: ast.AST) -> bool:
    """判断 AST node 是否是 `input_data` 名字节点。"""
    return isinstance(node, ast.Name) and node.id == "input_data"


def _unwrap_access_chain(node: ast.AST) -> list[str] | None:
    """对一个 AST 表达式节点, 尝试抽 input_data 访问链。

    返回:
      - [] : 就是裸的 input_data
      - ["X"] : input_data["X"] 或 input_data.get("X", ...)
      - ["X", "Y"] : input_data["X"]["Y"] 或 input_data.get("X", {}).get("Y")
      - None : 不是从 input_data 起始的访问 / 无法静态解析
    """
    chain: list[str] = []
    cur: ast.AST = node
    while True:
        if _is_input_data_name(cur):
            return list(reversed(chain))
        if isinstance(cur, ast.Subscript):
            slc = cur.slice
            if isinstance(slc, ast.Constant) and isinstance(slc.value, str):
                chain.append(slc.value)
                cur = cur.value
                continue
            return None  # 非字符串索引
        if isinstance(cur, ast.Call):
            func = cur.func
            if isinstance(func, ast.Attribute) and func.attr == "get":
                if (
                    cur.args
                    and isinstance(cur.args[0], ast.Constant)
                    and isinstance(cur.args[0].value, str)
                ):
                    chain.append(cur.args[0].value)
                    cur = func.value
                    continue
            return None
        return None


def _collect_input_data_reads(
    run_func: ast.FunctionDef,
) -> tuple[list[list[str]], list[str]]:
    """扫 run() 方法体, 返回 (access_chains, dynamic_warns)。

    access_chains 是所有成功解析的访问链; dynamic_warns 是静态无法判的情况
    (变量别名 / ** 展开 / items() 遍历 等)。
    """
    chains: list[list[str]] = []
    warns: list[str] = []

    for sub in ast.walk(run_func):
        # ** input_data 作为 kwargs 传递给函数
        if isinstance(sub, ast.keyword) and sub.arg is None:
            if _is_input_data_name(sub.value):
                warns.append("** 展开 input_data 传给函数, 静态无法判消费字段")
        # 变量别名: data = input_data
        if isinstance(sub, ast.Assign):
            for tgt in sub.targets:
                if isinstance(tgt, ast.Name) and _is_input_data_name(sub.value):
                    warns.append(
                        f"变量别名 {tgt.id} = input_data, 后续 {tgt.id}.xxx 访问无法追踪"
                    )
        # for k, v in input_data.items() / for k in input_data
        if isinstance(sub, ast.For):
            it = sub.iter
            if _is_input_data_name(it):
                warns.append("for ... in input_data 遍历, 静态无法判消费字段")
            elif (
                isinstance(it, ast.Call)
                and isinstance(it.func, ast.Attribute)
                and it.func.attr in ("items", "keys", "values")
                and _is_input_data_name(it.func.value)
            ):
                warns.append(f"for ... in input_data.{it.func.attr}() 遍历, 静态无法判")

    # 所有 Subscript / Call-with-get 起点是 input_data 的
    for sub in ast.walk(run_func):
        chain: list[str] | None = None
        if isinstance(sub, ast.Subscript):
            chain = _unwrap_access_chain(sub)
        elif isinstance(sub, ast.Call):
            func = sub.func
            if isinstance(func, ast.Attribute) and func.attr == "get":
                chain = _unwrap_access_chain(sub)
        if chain is not None and chain:
            chains.append(chain)

    # 去重, 保留访问深度
    seen: set[tuple[str, ...]] = set()
    unique_chains: list[list[str]] = []
    for c in chains:
        t = tuple(c)
        if t not in seen:
            seen.add(t)
            unique_chains.append(c)

    return unique_chains, warns


def _router_format_in(cls_node: ast.ClassDef) -> str | list[str] | None:
    """抽 Router 类的 FORMAT_IN 值 (str / list[str] / None)。"""
    for item in cls_node.body:
        if not isinstance(item, ast.Assign):
            continue
        for tgt in item.targets:
            if not (isinstance(tgt, ast.Name) and tgt.id == "FORMAT_IN"):
                continue
            val = item.value
            if isinstance(val, ast.Constant) and isinstance(val.value, str):
                return val.value
            if isinstance(val, ast.List):
                out: list[str] = []
                for e in val.elts:
                    if isinstance(e, ast.Constant) and isinstance(e.value, str):
                        out.append(e.value)
                return out if out else None
    return None


def _find_run_method(cls_node: ast.ClassDef) -> ast.FunctionDef | None:
    """找 Router.run 方法节点（不含基类的 run）。"""
    for item in cls_node.body:
        if isinstance(item, ast.FunctionDef) and item.name == "run":
            return item
    return None


def check_format_in_consumption(
    routers_py: str,
    formats_py: str,
    *,
    builtin_parent_fields: dict[str, set[str]] | None = None,
) -> list[dict[str, Any]]:
    """F-15/P-13 声明即消费 checker 主入口.

    Args:
        routers_py: routers.py 源码
        formats_py: formats.py 源码
        builtin_parent_fields: 外部 Format 字段表（built-in Format 的字段）,
            若提供, Format 继承自 built-in 时用此表替代 set()。

    Returns:
        findings list, 每条 dict:
          {severity, router_class, format_in, read_path, declared_fields, message}
          severity ∈ {"critical", "warn"}
    """
    findings: list[dict[str, Any]] = []

    schemas, parse_errs = _extract_format_schemas(formats_py)
    for e in parse_errs:
        findings.append({
            "severity": "warn",
            "router_class": None,
            "format_in": None,
            "read_path": None,
            "declared_fields": None,
            "message": e,
        })

    try:
        router_tree = ast.parse(routers_py)
    except SyntaxError as e:
        findings.append({
            "severity": "warn",
            "router_class": None,
            "format_in": None,
            "read_path": None,
            "declared_fields": None,
            "message": f"routers.py syntax error: {e}",
        })
        return findings

    for node in ast.walk(router_tree):
        if not isinstance(node, ast.ClassDef):
            continue
        base_names = [
            getattr(b, "id", "") or getattr(b, "attr", "")
            for b in node.bases
        ]
        if not any(b in ("Router", "LLMRouter", "AgentNodeLoop") for b in base_names):
            continue

        fmt_in = _router_format_in(node)
        run_fn = _find_run_method(node)
        if not fmt_in or run_fn is None:
            # 没 FORMAT_IN / 没 run(): 其他 check (D2 / R-01 / R-02) 会报, 此处跳过
            continue

        chains, dyn_warns = _collect_input_data_reads(run_fn)
        for w in dyn_warns:
            findings.append({
                "severity": "warn",
                "router_class": node.name,
                "format_in": fmt_in,
                "read_path": None,
                "declared_fields": None,
                "message": f"{node.name}: 动态 input_data 访问 — {w}",
            })

        # 多入判断: 显式 list 或单字符串但指向 composite Format
        # OMNI-026 偏好 composite (单字符串 + components), 但 list[str] 也支持
        is_explicit_list = isinstance(fmt_in, list)
        composite_components: list[str] = []
        if isinstance(fmt_in, str):
            info = schemas.get(fmt_in) or {}
            composite_components = list(info.get("components") or [])

        if is_explicit_list:
            fmt_list = list(fmt_in)
        elif composite_components:
            # composite: checker 展开成 components 列表处理,
            # Router.run() 会用 input_data["<component_id>"] 形式访问
            fmt_list = composite_components
        else:
            fmt_list = [fmt_in] if isinstance(fmt_in, str) else []

        declared_by_format: dict[str, set[str]] = {}
        unknown_formats: list[str] = []
        for fid in fmt_list:
            fields = _resolve_fields(fid, schemas, builtin_parent_fields)
            declared_by_format[fid] = fields
            if fid not in schemas:
                unknown_formats.append(fid)
        declared_union: set[str] = set()
        for fs in declared_by_format.values():
            declared_union |= fs

        # 多入 = 显式 list 或 composite (runner 侧都会给 format_id 命名空间)
        is_multi_input = is_explicit_list or bool(composite_components)

        for chain in chains:
            if not chain:
                continue
            first = chain[0]

            if first in _INFRA_FIELDS or any(first.startswith(p) for p in _INFRA_PREFIXES):
                continue

            # 多入 fan-in: first 是 FORMAT_IN 列表里的 format_id
            if is_multi_input and first in fmt_list:
                if len(chain) >= 2:
                    inner = chain[1]
                    inner_fields = declared_by_format[first]
                    if inner and inner not in inner_fields and first in schemas:
                        findings.append({
                            "severity": "critical",
                            "router_class": node.name,
                            "format_in": fmt_in,
                            "read_path": chain,
                            "declared_fields": sorted(inner_fields),
                            "message": (
                                f"{node.name}.run() 读 input_data[{first!r}][{inner!r}] "
                                f"但 Format {first!r} schema 未声明字段 {inner!r} "
                                f"(F-15/P-13 MUST, 反模式 PA-11 透传黑盒)"
                            ),
                        })
                continue

            if first in declared_union:
                continue

            # 读了但没声明 → critical
            # 但若 FORMAT_IN 包含外部/built-in Format 无法解析 schema, 降为 warn
            if unknown_formats:
                findings.append({
                    "severity": "warn",
                    "router_class": node.name,
                    "format_in": fmt_in,
                    "read_path": chain,
                    "declared_fields": sorted(declared_union),
                    "message": (
                        f"{node.name}.run() 读 input_data[{first!r}] "
                        f"(路径 {chain!r}), 但 FORMAT_IN 中 {unknown_formats!r} "
                        f"是外部/built-in Format 未能解析 schema, 无法判定"
                    ),
                })
            else:
                findings.append({
                    "severity": "critical",
                    "router_class": node.name,
                    "format_in": fmt_in,
                    "read_path": chain,
                    "declared_fields": sorted(declared_union),
                    "message": (
                        f"{node.name}.run() 读 input_data[{first!r}] 但 FORMAT_IN={fmt_in!r} "
                        f"对应 Format schema 未声明字段 {first!r}; 已声明字段: "
                        f"{sorted(declared_union)} (F-15/P-13 MUST, 反模式 FA-08 透传消费)"
                    ),
                })

    return findings


__all__ = ["check_format_in_consumption"]
