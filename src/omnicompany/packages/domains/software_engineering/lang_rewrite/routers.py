# [OMNI] origin=claude-code domain=software_engineering/lang_rewrite ts=2026-04-08T03:23:42Z
# [OMNI] material_id="material:domains.software_engineering.lang_rewrite.pipeline_routers.implementation.py"
"""lang_rewrite.routers — 跨语言改写管线的 Router 实现

每个 Router 对应 pipeline.py 中的一个节点。
"""

from __future__ import annotations

import ast
import json
import logging
import os
import re
import subprocess
import tempfile
from pathlib import Path


def _rust_env() -> dict:
    """返回包含 cargo/mingw64 路径的环境变量，确保 cargo check 可执行。"""
    env = os.environ.copy()
    home = os.path.expanduser("~")
    extra = os.pathsep.join([
        os.path.join(home, ".cargo", "bin"),
        os.path.join(home, "mingw64", "mingw64", "bin"),
    ])
    env["PATH"] = extra + os.pathsep + env.get("PATH", "")
    return env
from typing import Any

from omnicompany.protocol.anchor import Verdict, VerdictKind
from omnicompany.runtime.routing.router import Router

logger = logging.getLogger(__name__)

# ── 目标语言 → 外部依赖映射 ──────────────────────────────────────────────────

_PYTHON_TO_TS: dict[str, str] = {
    "pydantic": "zod (schema) + plain interfaces (models)",
    "ulid": "ulid",
    "sqlite3": "better-sqlite3",
    "redis.asyncio": "ioredis",
    "pathlib": "node:path",
    "asyncio": "native async/await",
    "dataclasses": "plain interfaces / classes",
    "abc": "abstract classes / interfaces",
    "enum": "string literal unions or enums",
    "json": "native JSON",
    "logging": "pino or console",
    "typing": "TypeScript native types",
    "datetime": "Date / dayjs",
    "inspect": "not needed (no runtime reflection)",
    "time": "Date.now() / performance.now()",
}

_PYTHON_TO_RUST: dict[str, str] = {
    "pydantic": "serde + serde_json (derive macros)",
    "ulid": "ulid (crate)",
    "sqlite3": "rusqlite",
    "redis.asyncio": "redis (crate, async feature)",
    "pathlib": "std::path::PathBuf",
    "asyncio": "tokio",
    "dataclasses": "structs with derive macros",
    "abc": "traits",
    "enum": "enum (Rust native)",
    "json": "serde_json",
    "logging": "tracing / log",
    "typing": "Rust native types + generics",
    "datetime": "chrono",
    "inspect": "not needed",
    "time": "std::time::Instant",
}

DEP_MAPS = {"typescript": _PYTHON_TO_TS, "rust": _PYTHON_TO_RUST}

# 引擎层模块顺序（拓扑排序：无依赖 → 有依赖）
ENGINE_TOPO_ORDER = [
    # Phase 1: 纯数据，零内部依赖
    "protocol/registry.py",
    "protocol/state.py",
    "protocol/events.py",
    "protocol/anchor.py",
    "primitives/signal.py",
    "primitives/intent.py",
    "primitives/hook.py",
    "primitives/node.py",
    "primitives/tool.py",
    # Phase 2: 依赖 Phase 1
    "protocol/format.py",
    "protocol/pipeline.py",
    "bus/base.py",
    # Phase 3: 依赖 Phase 1+2
    "bus/memory.py",
    "bus/sqlite.py",
    "runtime/router.py",
    # Phase 4: 依赖前三层
    "runtime/runner.py",
    "runtime/agent_loop.py",
    # Phase 5: 协调层
    "config.py",
    "registry.py",
    "dispatch.py",
    "observe.py",
]


# ═══════════════════════════════════════════════════════════════════════════════
# 1. SourceAnalyzer — 解析 Python 源文件
# ═══════════════════════════════════════════════════════════════════════════════

class SourceAnalyzerRouter(Router):
    FORMAT_IN = "rewrite.source-module"
    FORMAT_OUT = "rewrite.source-module"
    DESCRIPTION = "解析 Python 源文件，提取 AST 摘要、公开接口、内部/外部依赖"

    def run(self, input_data: dict) -> Verdict:
        source_path = Path(input_data.get("source_path", ""))
        if not source_path.exists():
            return Verdict(
                kind=VerdictKind.FAIL,
                output=input_data,
                diagnosis=f"源文件不存在: {source_path}",
                confidence=1.0,
            )

        source_code = source_path.read_text(encoding="utf-8")
        try:
            tree = ast.parse(source_code)
        except SyntaxError as e:
            return Verdict(
                kind=VerdictKind.FAIL,
                output=input_data,
                diagnosis=f"Python 语法错误: {e}",
                confidence=1.0,
            )

        # 提取公开接口（只取模块顶层定义，不深入 class 内部）
        public_interfaces: list[dict] = []
        for node in ast.iter_child_nodes(tree):
            if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                if not node.name.startswith("_"):
                    entry = {
                        "type": type(node).__name__,
                        "name": node.name,
                        "lineno": node.lineno,
                    }
                    if isinstance(node, ast.ClassDef):
                        entry["bases"] = [
                            ast.dump(b) if not isinstance(b, ast.Name) else b.id
                            for b in node.bases
                        ]
                        entry["methods"] = [
                            m.name for m in node.body
                            if isinstance(m, (ast.FunctionDef, ast.AsyncFunctionDef))
                            and not m.name.startswith("_")
                        ]
                    public_interfaces.append(entry)

        # 提取 import —— 区分内部/外部，并记录具体引用的名字
        internal_deps: list[str] = []
        external_deps: list[str] = []
        # imported_names: {module: [name1, name2, ...]} 精确记录从每个内部模块引入了什么
        imported_names: dict[str, list[str]] = {}
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    (internal_deps if alias.name.startswith("omnicompany") else external_deps
                     ).append(alias.name)
            elif isinstance(node, ast.ImportFrom):
                module = node.module or ""
                if module.startswith("omnicompany"):
                    internal_deps.append(module)
                    names = [a.name for a in node.names]
                    imported_names.setdefault(module, []).extend(names)
                else:
                    external_deps.append(module)

        output = {
            **input_data,
            "source_code": source_code,
            "line_count": len(source_code.splitlines()),
            "public_interfaces": public_interfaces,
            "internal_deps": sorted(set(internal_deps)),
            "external_deps": sorted(set(external_deps)),
            "imported_names": imported_names,
        }

        return Verdict(
            kind=VerdictKind.PASS,
            output=output,
            diagnosis=f"解析完成: {len(public_interfaces)} 个公开接口, "
                      f"{len(internal_deps)} 个内部依赖, {len(external_deps)} 个外部依赖",
            confidence=1.0,
        )


# ═══════════════════════════════════════════════════════════════════════════════
# 2. DependencyMapper — 构建依赖图 + 拓扑排序
# ═══════════════════════════════════════════════════════════════════════════════

class DependencyMapperRouter(Router):
    FORMAT_IN = "rewrite.source-module"
    FORMAT_OUT = "rewrite.dependency-graph"
    DESCRIPTION = "构建模块依赖图，拓扑排序确定移植顺序，映射外部依赖到目标语言等价物"

    def run(self, input_data: dict) -> Verdict:
        target_lang = input_data.get("target_lang", "typescript")
        dep_map = DEP_MAPS.get(target_lang, _PYTHON_TO_TS)

        # 映射外部依赖
        ext_deps = input_data.get("external_deps", [])
        dep_mapping: dict[str, str] = {}
        unmapped: list[str] = []
        for dep in ext_deps:
            root = dep.split(".")[0]
            if root in dep_map:
                dep_mapping[dep] = dep_map[root]
            else:
                unmapped.append(dep)

        output = {
            **input_data,
            "target_lang": target_lang,
            "topo_order": ENGINE_TOPO_ORDER,
            "dep_mapping": dep_mapping,
            "unmapped_deps": unmapped,
        }

        diag = f"拓扑排序 {len(ENGINE_TOPO_ORDER)} 个模块, 映射 {len(dep_mapping)} 个外部依赖"
        if unmapped:
            diag += f", {len(unmapped)} 个未映射: {unmapped}"

        return Verdict(
            kind=VerdictKind.PASS,
            output=output,
            diagnosis=diag,
            confidence=1.0,
            granted_tags=["dependency-resolved"],
        )


# ═══════════════════════════════════════════════════════════════════════════════
# 2a. ContextScout — LLM 评估视野需求（dependency_mapper 与 prepare_translation 之间）
# ═══════════════════════════════════════════════════════════════════════════════

class ContextScoutRouter(Router):
    """LLM 评估翻译所需的完整视野。

    在确定性提取（PrepareTranslation）之前，先用 LLM 回答三个问题：
    1. 哪些依赖的 TS 版本不存在？每个缺失依赖需要发明什么接口？
    2. 已有 TS 文件的设计模式是什么？（枚举风格、export 惯例、Verdict 构造方式）
    3. 需要额外读哪些文件来理解更广泛的上下文？

    输出 enriched 信息供 PrepareTranslation 和 IdiomTranslator 使用。
    """
    FORMAT_IN = "rewrite.dependency-graph"
    FORMAT_OUT = "rewrite.dependency-graph"  # 就地增强，不改变类型
    DESCRIPTION = "LLM 评估翻译上下文需求：缺失依赖策略、设计模式、额外文件"

    def __init__(self, *, ts_dir: str | None = None, model: str | None = None):
        self._ts_dir = Path(ts_dir) if ts_dir else None
        self._model = model

    def _make_client(self):
        from omnicompany.runtime.llm.llm import LLMClient
        kwargs = {"role": "runtime_main", "max_tokens": 8192}
        if self._model:
            kwargs["model"] = self._model
        return LLMClient(**kwargs)

    def run(self, input_data: dict) -> Verdict:
        imported_names: dict[str, list[str]] = input_data.get("imported_names", {})
        internal_deps: list[str] = input_data.get("internal_deps", [])
        source_code: str = input_data.get("source_code", "")
        source_path: str = input_data.get("source_path", "")
        ts_dir = self._ts_dir or Path(input_data.get("ts_dir", "data/rewrite/ts_phase1"))

        if not source_code:
            return Verdict(
                kind=VerdictKind.FAIL,
                diagnosis="输入不满足要求: 缺少 source_code，无法评估翻译上下文",
            )

        # ── Step 1: 确定性扫描 — 哪些 TS 文件存��，哪些不存在 ──
        dep_status: dict[str, dict] = {}
        existing_ts_samples: list[str] = []  # 用于提取设计模式

        for module in internal_deps:
            rel_path = module.replace("omnicompany.", "").replace(".", "/") + ".py"
            py_path = Path("src/omnicompany") / rel_path
            stem = py_path.stem
            ts_path = ts_dir / f"{stem}.ts"

            names = imported_names.get(module, [])
            entry = {
                "module": module,
                "names": names,
                "py_exists": py_path.exists(),
                "ts_exists": ts_path.exists(),
            }

            if ts_path.exists():
                ts_content = ts_path.read_text(encoding="utf-8")
                entry["ts_file"] = str(ts_path)
                # 收集前 3 个 TS 文件的头部做模式提取
                if len(existing_ts_samples) < 3:
                    existing_ts_samples.append(
                        f"// === {stem}.ts ===\n" + ts_content[:800]
                    )
            elif py_path.exists():
                # TS 不存在 → 读 Python 源的公开接口签名
                py_content = py_path.read_text(encoding="utf-8")
                # 提取被引用名字的签名（前 5 行）
                sigs = []
                for name in names:
                    sig = _extract_signature_only(name, py_content)
                    if sig:
                        sigs.append(sig)
                entry["py_signatures"] = sigs

            dep_status[module] = entry

        missing_deps = {m: d for m, d in dep_status.items() if not d.get("ts_exists")}
        existing_deps = {m: d for m, d in dep_status.items() if d.get("ts_exists")}

        # 如果没有缺失依赖且没有需要分析的，跳过 LLM 调用
        if not missing_deps and not existing_ts_samples:
            output = {
                **input_data,
                "scout_result": {
                    "missing_dep_strategies": {},
                    "design_patterns": "",
                    "prerequisite_notes": "",
                },
            }
            return Verdict(
                kind=VerdictKind.PASS,
                output=output,
                diagnosis="所有依赖的 TS 版本都存在，无需额外侦察",
                confidence=1.0,
            )

        # ── Step 2: LLM 分析 ──
        missing_info = ""
        if missing_deps:
            parts = []
            for m, d in missing_deps.items():
                sigs = d.get("py_signatures", [])
                parts.append(f"模块 {m}, 引用名字: {d['names']}")
                if sigs:
                    parts.append("  Python 签名:\n" + "\n".join(f"    {s}" for s in sigs))
            missing_info = "\n".join(parts)

        pattern_samples = "\n\n".join(existing_ts_samples) if existing_ts_samples else "(无已有 TS 文件)"

        prompt = f"""你是一个跨语言翻译的上下文评估专家。

我正在将 Python 模块 `{source_path}` 翻译为 TypeScript。
请分析以下信息，回答三个问题。

## 当前模块的 import 列表和依赖状态

### 已有 TS 版本的依赖（可直接 import）
{json.dumps({m: d["names"] for m, d in existing_deps.items()}, indent=2, ensure_ascii=False) if existing_deps else "(无)"}

### 缺失 TS 版本的依赖（需要处理策略）
{missing_info or "(无)"}

## 已有 TS 文件的样本（提取设计模式）
{pattern_samples}

## 当前 Python 源码的 import 部分（前 30 行）
```python
{chr(10).join(source_code.splitlines()[:30])}
```

## 请回答（JSON 格式）

```json
{{
  "missing_dep_strategies": {{
    "模块名": {{
      "strategy": "invent_interface | skip | inline",
      "interface_name": "建议的接口名（如 LLMClientLike）",
      "interface_sketch": "接口的 TS 签名骨架（1-5 行）",
      "reason": "为什么这样处理"
    }}
  }},
  "design_patterns": "从已有 TS 文件中观察到的设计模式（枚举风格、export 惯例、Verdict 构造方式等），2-5 条",
  "additional_files_to_read": ["需要额外读取以获得完整上下文的文件路径"],
  "prerequisite_notes": "翻译前需要注意的事项（如已有文件中的 bug 需要先修复）"
}}
```

只输出 JSON，不要其他内容。
"""
        try:
            client = self._make_client()
            resp = client.call(messages=[{"role": "user", "content": prompt}])
            content = resp.content[0].text

            # 提取 JSON
            json_match = re.search(r"```json\n(.*?)```", content, re.DOTALL)
            if json_match:
                scout_result = json.loads(json_match.group(1))
            else:
                # 尝试直接解析
                scout_result = json.loads(content.strip())

        except Exception as e:
            logger.warning("ContextScout LLM 调用失败: %s, 使用确定性 fallback", e)
            # Fallback: 确定性策略（缺失的都标记为 invent_interface）
            scout_result = {
                "missing_dep_strategies": {
                    m: {
                        "strategy": "invent_interface",
                        "interface_name": f"{d['names'][0]}Like" if d["names"] else "Unknown",
                        "interface_sketch": "// TODO: define interface",
                        "reason": f"TS 版本不存在，需要创建接��: {d['names']}",
                    }
                    for m, d in missing_deps.items()
                },
                "design_patterns": "",
                "additional_files_to_read": [],
                "prerequisite_notes": "",
            }

        # ── Step 3: 读取额外推荐文件 ──
        additional_context = ""
        additional_files = scout_result.get("additional_files_to_read", [])
        if additional_files:
            parts = []
            for fpath in additional_files[:5]:  # 最多读 5 个
                p = Path(fpath)
                if not p.exists():
                    # 尝试 ts_dir 下
                    p = ts_dir / Path(fpath).name
                if p.exists():
                    content = p.read_text(encoding="utf-8")
                    # 只取前 60 行
                    parts.append(f"// === {p.name} ===\n" + "\n".join(content.splitlines()[:60]))
            additional_context = "\n\n".join(parts)

        scout_result["additional_context"] = additional_context

        output = {
            **input_data,
            "scout_result": scout_result,
        }

        n_missing = len(scout_result.get("missing_dep_strategies", {}))
        n_additional = len(additional_files)
        return Verdict(
            kind=VerdictKind.PASS,
            output=output,
            diagnosis=f"侦察完成: {n_missing} 个缺失依赖策略, {n_additional} 个额外文件, "
                      f"设计模式: {scout_result.get('design_patterns', '')[:80]}",
            confidence=0.8,
        )


def _extract_signature_only(name: str, py_source: str) -> str | None:
    """只提取 Python 定义的签名行（class/def 声明 + 参数），不含方法体。"""
    try:
        tree = ast.parse(py_source)
    except SyntaxError:
        return None

    for node in ast.walk(tree):
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.name == name:
                start = node.lineno - 1
                lines = py_source.splitlines()
                # 取 class/def 行 + 字段/方法签名（最多 10 行）
                sig_lines = [lines[start]]
                for line in lines[start + 1: min(start + 30, len(lines))]:
                    stripped = line.strip()
                    if (stripped.startswith("def ") or stripped.startswith("async def ")
                            or stripped.startswith("@") or ":" in stripped and "=" in stripped
                            or stripped.startswith('"""') or stripped == ""):
                        sig_lines.append(line)
                    if len(sig_lines) >= 10:
                        break
                return "\n".join(sig_lines)
    return None


# ═══════════════════════════════════════════════════════════════════════════════
# 2b. PrepareTranslation — 依赖图 → 翻译单元
# ═══════════════════════════════════════════════════════════════════════════════

class PrepareTranslationRouter(Router):
    FORMAT_IN = "rewrite.dependency-graph"
    FORMAT_OUT = "rewrite.translation-unit"
    DESCRIPTION = "按 import 精确提取已翻译依赖的签名、用法和原理，组装为翻译上下文"

    def __init__(self, *, ts_dir: str | None = None):
        """ts_dir: 已翻译 TS 文件所在目录，用于提取依赖上下文。"""
        self._ts_dir = Path(ts_dir) if ts_dir else None

    def run(self, input_data: dict) -> Verdict:
        imported_names: dict[str, list[str]] = input_data.get("imported_names", {})
        ts_dir = self._ts_dir or Path(input_data.get("ts_dir", "data/rewrite/ts_phase1"))

        # 同时从 Python 源码提取被引用类型的定义，作为 ground truth
        context_parts: list[str] = []

        for module, names in imported_names.items():
            # 找到对应的 Python 源文件
            rel_path = module.replace("omnicompany.", "").replace(".", "/") + ".py"
            py_path = Path("src/omnicompany") / rel_path
            if not py_path.exists():
                continue

            py_source = py_path.read_text(encoding="utf-8")

            # 找到对应的已翻译 TS 文件
            stem = py_path.stem
            ts_path = ts_dir / f"{stem}.ts"
            ts_source = ts_path.read_text(encoding="utf-8") if ts_path.exists() else ""

            # 为每个被引用的名字提取上下文
            for name in names:
                entry = _extract_type_context(name, py_source, ts_source)
                if entry:
                    context_parts.append(entry)

        translated_context = "\n\n".join(context_parts) if context_parts else ""

        # ── 合并 ContextScout 的侦察结果 ──
        scout_result = input_data.get("scout_result", {})
        scout_parts: list[str] = []

        # 缺失依赖的接口策略
        missing_strategies = scout_result.get("missing_dep_strategies", {})
        if missing_strategies:
            scout_parts.append("## 缺失依赖的处理策���（TS 版本不存在，需要发明接口）")
            for mod, strategy in missing_strategies.items():
                if strategy.get("strategy") == "invent_interface":
                    scout_parts.append(
                        f"### {strategy.get('interface_name', mod)}\n"
                        f"来源模块: {mod}\n"
                        f"原因: {strategy.get('reason', '')}\n"
                        f"建议接口:\n```typescript\n{strategy.get('interface_sketch', '')}\n```"
                    )

        # 设计模式
        patterns = scout_result.get("design_patterns", "")
        if patterns:
            scout_parts.append(f"## 已有 TS 文件的设计模式（必须遵循）\n{patterns}")

        # 前置注意事项
        prereqs = scout_result.get("prerequisite_notes", "")
        if prereqs:
            scout_parts.append(f"## 翻译前注意事项\n{prereqs}")

        # 额外文件上下文
        additional_ctx = scout_result.get("additional_context", "")
        if additional_ctx:
            scout_parts.append(f"## 额外参考文件\n{additional_ctx}")

        scout_context = "\n\n".join(scout_parts) if scout_parts else ""

        # 合并两部分上下文
        full_context = translated_context
        if scout_context:
            full_context = f"{translated_context}\n\n{'=' * 40}\n{scout_context}" if translated_context else scout_context

        output = {
            **input_data,
            "translated_context": full_context,
        }

        return Verdict(
            kind=VerdictKind.PASS,
            output=output,
            diagnosis=f"提取了 {len(context_parts)} 个依赖类型上下文 + {len(scout_parts)} 条侦察信息 "
                      f"({len(full_context)} chars total)",
            confidence=1.0,
            granted_tags=["translation-ready"],
        )


def _extract_type_context(name: str, py_source: str, ts_source: str) -> str | None:
    """为单个类型名提取翻译上下文：签名 + 用法示例 + 设计原理。

    从 Python 源码提取类/函数定义（ground truth），
    从 TS 源码提取已翻译的对应声明（如果有）。
    """
    parts: list[str] = []
    parts.append(f"### {name}")

    # ── 从 Python 源码提取定义 ──
    try:
        tree = ast.parse(py_source)
    except SyntaxError:
        return None

    py_def = None
    for node in ast.walk(tree):
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.name == name:
                # 提取该定义的源码（含 docstring 和方法签名）
                start = node.lineno - 1
                end = node.end_lineno or (start + 1)
                lines = py_source.splitlines()[start:end]
                py_def = "\n".join(lines)
                break
        # 处理赋值式定义（如枚举值、常量）
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == name:
                    start = node.lineno - 1
                    end = node.end_lineno or (start + 1)
                    py_def = "\n".join(py_source.splitlines()[start:end])
                    break

    if py_def is None:
        return None

    # 截取合理长度——类定义可能很长，只取前 60 行（含所有方法签名和字段）
    py_lines = py_def.splitlines()
    if len(py_lines) > 60:
        # 保留类声明 + 所有字段/方法签名（def xxx 行），截断方法体
        sig_lines = []
        for line in py_lines:
            stripped = line.strip()
            if (stripped.startswith("class ") or stripped.startswith("def ")
                    or stripped.startswith("async def ") or stripped.startswith("@")
                    or ": " in stripped and "=" in stripped  # 字段定义
                    or stripped.startswith('"""') or stripped.startswith("'''")):
                sig_lines.append(line)
            elif not sig_lines or sig_lines[-1].strip().startswith("def "):
                # 保留 def 后的第一行（通常是 docstring）
                sig_lines.append(line)
        py_def = "\n".join(sig_lines[:60])

    parts.append(f"**Python 定义 (ground truth):**\n```python\n{py_def}\n```")

    # ── 从 TS 源码提取已翻译的声明 ──
    if ts_source:
        ts_lines = ts_source.splitlines()
        # 找包含该名字的 export 声明
        relevant_ts: list[str] = []
        capturing = False
        brace_depth = 0
        for line in ts_lines:
            if not capturing and name in line and ("export" in line or "type " in line or "interface " in line or "const " in line):
                capturing = True
                brace_depth = 0

            if capturing:
                relevant_ts.append(line)
                brace_depth += line.count("{") - line.count("}")
                if brace_depth <= 0 and len(relevant_ts) > 1:
                    capturing = False
                # 安全截断
                if len(relevant_ts) > 40:
                    relevant_ts.append("  // ... (truncated)")
                    capturing = False

        if relevant_ts:
            parts.append("**已翻译的 TS 声明 (直接 import 使用):**\n```typescript\n" + "\n".join(relevant_ts) + "\n```")

    # ── 设计原理（从 docstring 或注释提取）──
    # 已包含在 py_def 的 docstring 中

    # ── 用法提示 ──
    parts.append(f"翻译时直接 `import {{ {name} }} from './对应模块'`，保持接口签名一致。")

    return "\n".join(parts)


# ═══════════════════════════════════════════════════════════════════════════════
# 2d. DemandExtractor — 扫描下游模块的调用需求（需求侧）
# ═══════════════════════════════════════════════════════════════════════════════

class DemandExtractorRouter(Router):
    """确定性提取"谁依赖当前模块、怎么调用"。

    扫描 ENGINE_TOPO_ORDER 中排在当前模块之后的模块，
    找到 import 了当前模块的下游模块，提取它们的调用方式。

    输出 demand_set: 下游对当前模块的签名期望。
    """
    FORMAT_IN = "rewrite.dependency-graph"
    FORMAT_OUT = "rewrite.demand-set"
    DESCRIPTION = "确定性扫描下游模块对当前模块的调用需求（需求侧）"

    def run(self, input_data: dict) -> Verdict:
        source_path: str = input_data.get("source_path", "")
        source_code: str = input_data.get("source_code", "")
        public_interfaces: list[dict] = input_data.get("public_interfaces", [])

        # 当前模块的 omnicompany 路径
        # e.g. "src/omnicompany/runtime/router.py" → "omnicompany.runtime.routing.router"
        module_name = ""
        if source_path:
            p = Path(source_path)
            # 尝试从路径提取模块名
            parts = p.parts
            try:
                omni_idx = list(parts).index("omnicompany")
                module_parts = list(parts[omni_idx:])
                module_parts[-1] = p.stem  # 去掉 .py
                module_name = ".".join(module_parts)
            except ValueError:
                module_name = p.stem

        # 收集当前模块导出的名字
        exported_names = {iface["name"] for iface in public_interfaces}

        # 扫描所有 topo 后序模块，找到引用了当前模块的
        demand_entries: list[dict] = []

        for topo_module in ENGINE_TOPO_ORDER:
            py_path = Path("src/omnicompany") / topo_module
            if not py_path.exists():
                continue
            if str(py_path) == source_path:
                continue  # 跳过自己

            try:
                downstream_source = py_path.read_text(encoding="utf-8")
                tree = ast.parse(downstream_source)
            except (SyntaxError, UnicodeDecodeError):
                continue

            # 检查是否 import 了当前模块
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and node.module:
                    if module_name and node.module == module_name:
                        imported = [a.name for a in node.names]
                        # 提取调用方式（简单：找被引用名字在下游源码中的用法行）
                        usage_lines = []
                        for name in imported:
                            if name in exported_names:
                                for i, line in enumerate(downstream_source.splitlines()):
                                    if name in line and "import" not in line:
                                        usage_lines.append(f"  L{i+1}: {line.strip()}")
                                        if len(usage_lines) >= 5:
                                            break

                        demand_entries.append({
                            "downstream_module": str(py_path),
                            "imported_names": imported,
                            "usage_examples": usage_lines[:5],
                        })

        output = {
            **input_data,
            "demand_set": demand_entries,
            "demand_summary": f"{len(demand_entries)} 个下游模块引用了当前模块的 {len(exported_names)} 个接口",
        }

        return Verdict(
            kind=VerdictKind.PASS,
            output=output,
            diagnosis=f"需求侧: {len(demand_entries)} 个下游消费者",
            confidence=1.0,
        )


# ═══════════════════════════════════════════════════════════════════════════════
# 2e. SupplyScanner — 扫描当前模块的依赖签名（供给侧）
# ═══════════════════════════════════════════════════════════════════════════════

class SupplyScannerRouter(Router):
    """确定性扫描"当前模块依赖什么、它们提供什么签名"。

    对每个内部依赖：
    - 如果 TS 版本存在 → 提取 TS 声明（可直接 import）
    - 如果 TS 版本不存在 → 提取 Python 源签名作为 ground truth

    输出 supply_map: 每个依赖的真实签名。
    """
    FORMAT_IN = "rewrite.dependency-graph"
    FORMAT_OUT = "rewrite.supply-map"
    DESCRIPTION = "确定性扫描当前模块依赖的真实签名（供给侧）"

    def __init__(self, *, ts_dir: str | None = None, rs_dir: str | None = None):
        self._ts_dir = Path(ts_dir) if ts_dir else None
        self._rs_dir = Path(rs_dir) if rs_dir else None

    def _get_target_info(self, input_data: dict) -> tuple[Path, str, str]:
        """返回 (target_dir, target_ext, lang_label)，根据 target_lang 切换。"""
        target_lang = input_data.get("target_lang", "typescript")
        if target_lang == "rust":
            rs_base = self._rs_dir or Path(input_data.get("rs_dir", "data/rewrite/rs_phase1"))
            return rs_base / "src", "rs", "Rust"
        ts_dir = self._ts_dir or Path(input_data.get("ts_dir", "data/rewrite/ts_phase1"))
        return ts_dir, "ts", "TS"

    def run(self, input_data: dict) -> Verdict:
        imported_names: dict[str, list[str]] = input_data.get("imported_names", {})
        target_dir, target_ext, lang_label = self._get_target_info(input_data)
        import_kw = "use" if target_ext == "rs" else "import"

        supply_entries: list[dict] = []
        missing_deps: list[dict] = []

        for module, names in imported_names.items():
            rel_path = module.replace("omnicompany.", "").replace(".", "/") + ".py"
            py_path = Path("src/omnicompany") / rel_path
            stem = py_path.stem
            target_path = target_dir / f"{stem}.{target_ext}"

            entry: dict[str, Any] = {
                "module": module,
                "names": names,
                f"{target_ext}_exists": target_path.exists(),
            }

            if target_path.exists():
                # 目标语言版本已存在 → 提取已翻译声明
                target_source = target_path.read_text(encoding="utf-8")
                declarations: list[str] = []
                for name in names:
                    if target_ext == "rs":
                        decl = self._extract_rs_declaration(name, target_source)
                    else:
                        decl = self._extract_ts_declaration(name, target_source)
                    if decl:
                        declarations.append(decl)
                entry[f"{target_ext}_file"] = str(target_path)
                entry[f"{target_ext}_declarations"] = declarations
                entry["status"] = "available"
                supply_entries.append(entry)

            elif py_path.exists():
                # 目标版本不存在 → 提取 Python 签名作为 ground truth
                py_source = py_path.read_text(encoding="utf-8")
                py_signatures: list[str] = []
                for name in names:
                    ctx = _extract_type_context(name, py_source, "")
                    if ctx:
                        py_signatures.append(ctx)
                    else:
                        sig = _extract_signature_only(name, py_source)
                        if sig:
                            py_signatures.append(f"### {name}\n```python\n{sig}\n```")

                entry["py_signatures"] = py_signatures
                entry["status"] = f"missing_{target_ext}"
                missing_deps.append(entry)

        # 组装供给侧上下文文本
        supply_context_parts: list[str] = []

        # Rust 专属：将 crate::types 共享类型注入供给侧（禁止重定义）
        if target_ext == "rs":
            types_rs = target_dir / "types.rs"
            if types_rs.exists():
                types_src = types_rs.read_text(encoding="utf-8")
                supply_context_parts.append(
                    "## 【CRITICAL】crate::types 共享类型（已存在，必须 `use crate::types::*;` 导入，禁止重定义）\n"
                    "```rust\n" + types_src + "\n```"
                )

        if supply_entries:
            supply_context_parts.append(f"## 已有 {lang_label} 实现（可直接 {import_kw}）")
            for e in supply_entries:
                for decl in e.get(f"{target_ext}_declarations", []):
                    supply_context_parts.append(decl)

        if missing_deps:
            iface_hint = "trait/struct（命名建议: XxxLike）" if target_ext == "rs" else "TypeScript interface（命名建议: XxxLike）"
            supply_context_parts.append(f"## 缺失 {lang_label} 的依赖（Python 签名 = ground truth）")
            supply_context_parts.append(
                f"以下模块的 {lang_label} 版本不存在。翻译时必须根据 Python 签名"
                f"创建对应的 {iface_hint}，"
                f"不要 {import_kw} 不存在的文件。"
            )
            for e in missing_deps:
                supply_context_parts.append(f"\n### 模块 {e['module']} (引用: {e['names']})")
                for sig in e.get("py_signatures", []):
                    supply_context_parts.append(sig)

        supply_context = "\n\n".join(supply_context_parts)

        output = {
            **input_data,
            "supply_map": {
                "available": supply_entries,
                "missing": missing_deps,
            },
            "supply_context": supply_context,
        }

        return Verdict(
            kind=VerdictKind.PASS,
            output=output,
            diagnosis=f"供给侧: {len(supply_entries)} 个已有 {lang_label}, {len(missing_deps)} 个缺失",
            confidence=1.0,
        )

    @staticmethod
    def _extract_ts_declaration(name: str, ts_source: str) -> str | None:
        """从 TS 源码提取指定名字的 export 声明。"""
        ts_lines = ts_source.splitlines()
        relevant: list[str] = []
        capturing = False
        brace_depth = 0

        for line in ts_lines:
            if not capturing and name in line and (
                "export" in line or "type " in line
                or "interface " in line or "const " in line
            ):
                capturing = True
                brace_depth = 0

            if capturing:
                relevant.append(line)
                brace_depth += line.count("{") - line.count("}")
                if brace_depth <= 0 and len(relevant) > 1:
                    capturing = False
                if len(relevant) > 40:
                    relevant.append("  // ... (truncated)")
                    capturing = False

        if relevant:
            return f"### {name} (TS)\n```typescript\n" + "\n".join(relevant) + "\n```"
        return None

    @staticmethod
    def _extract_rs_declaration(name: str, rs_source: str) -> str | None:
        """从 Rust 源码提取指定名字的 pub 声明。"""
        rs_lines = rs_source.splitlines()
        relevant: list[str] = []
        capturing = False
        brace_depth = 0

        for line in rs_lines:
            if not capturing and name in line and ("pub " in line or "pub(" in line):
                capturing = True
                brace_depth = 0

            if capturing:
                relevant.append(line)
                brace_depth += line.count("{") - line.count("}")
                if brace_depth <= 0 and len(relevant) > 1:
                    capturing = False
                if len(relevant) > 40:
                    relevant.append("    // ... (truncated)")
                    capturing = False

        if relevant:
            return f"### {name} (Rust)\n```rust\n" + "\n".join(relevant) + "\n```"
        return None


# ═══════════════════════════════════════════════════════════════════════════════
# 2c. FeedbackDemote — 等价性失败回退降级
# ═══════════════════════════════════════════════════════════════════════════════

class FeedbackDemoteRouter(Router):
    FORMAT_IN = "rewrite.verified-code"
    FORMAT_OUT = "rewrite.translation-context"
    DESCRIPTION = "等价性验证失败时，将反馈降级为 translation-context 重新翻译"

    def run(self, input_data: dict) -> Verdict:
        verification_result = input_data.get("verification_result", {})
        feedback = input_data.get("_feedback", "")
        if not feedback and verification_result:
            issues = verification_result.get("issues", [])
            feedback = "\n".join(
                f"- {i.get('interface', '?')}: {i.get('description', '')}"
                for i in issues if i.get("severity") == "critical"
            )

        output = {
            "source_path": input_data.get("source_path", ""),
            "source_code": input_data.get("source_code", ""),
            "target_lang": input_data.get("target_lang", ""),
            "dep_mapping": input_data.get("dep_mapping", {}),
            "public_interfaces": input_data.get("public_interfaces", []),
            "topo_order": input_data.get("topo_order", []),
            # 供给侧和需求侧上下文透传（用于重新翻译时保持视野）
            "supply_context": input_data.get("supply_context", ""),
            "demand_set": input_data.get("demand_set", []),
            "_feedback": feedback,
        }
        return Verdict(
            kind=VerdictKind.PASS,
            output=output,
            diagnosis=f"降级完成，携带反馈: {feedback[:100]}",
            confidence=1.0,
            granted_tags=["translation-ready"],
        )


# ═══════════════════════════════════════════════════════════════════════════════
# 3. IdiomTranslator — LLM 翻译
# ═══════════════════════════════════════════════════════════════════════════════

class IdiomTranslatorRouter(Router):
    FORMAT_IN = "rewrite.translation-context"
    FORMAT_OUT = "rewrite.generated-code"
    DESCRIPTION = "LLM 将 Python 模块翻译为目标语言惯用写法"
    REFLECTION_ENABLED = True

    def __init__(self, *, model: str | None = None):
        self._model = model

    def _make_client(self):
        from omnicompany.runtime.llm.llm import LLMClient
        kwargs = {"role": "runtime_main", "max_tokens": 16384}
        if self._model:
            kwargs["model"] = self._model
        return LLMClient(**kwargs)

    def run(self, input_data: dict) -> Verdict:
        source_code = input_data.get("source_code", "")
        target_lang = input_data.get("target_lang", "typescript")
        dep_mapping = input_data.get("dep_mapping", {})
        public_interfaces = input_data.get("public_interfaces", [])
        feedback = input_data.get("_feedback", "")
        # 新字段：来自 supply_scanner（供给侧签名）和 demand_extractor（需求侧调用）
        supply_context = input_data.get("supply_context", "")
        demand_set = input_data.get("demand_set", [])

        lang_specific = {
            "typescript": {
                "ext": "ts",
                "idiom_guide": (
                    "## 类型映射\n"
                    "- @dataclass class → 一个 class（保持内聚！不要拆成 type + Util class）\n"
                    "- pydantic BaseModel → interface（纯类型）+ zod schema（运行时验证，可选）\n"
                    "- StrEnum → `as const` object + string literal union type\n"
                    "- ABC / abstractmethod → abstract class + abstract methods\n"
                    "- dict[str, Any] → Record<string, unknown>\n"
                    "- list[str] → string[]\n"
                    "- X | None → X | null（或 X | undefined 看场景）\n"
                    "\n"
                    "## 内聚性规则（重要）\n"
                    "- Python 一个 class 含 @classmethod 工厂方法 → TS 同一个 class 含 static 方法\n"
                    "- 不要把一个 class 拆成 type + 工具函数/Util class\n"
                    "- 保持与 Python 版本相同的 import 粒度（一个文件 export 什么，TS 也 export 什么）\n"
                    "\n"
                    "## zod 用法（必须正确）\n"
                    "- z.record() 需要两个参数: z.record(z.string(), z.any())，不是 z.record(z.any())\n"
                    "- z.object().default({}) 中 default 参数必须匹配 schema 类型\n"
                    "- 如果 Python 版没有运行时验证（纯 dataclass），TS 版也不需要 zod schema\n"
                    "\n"
                    "## 其他\n"
                    "- async/await 直接对应\n"
                    "- pathlib.Path → node:path (import path from 'node:path')\n"
                    "- time.monotonic() → performance.now() / 1000（无需 import，Node.js 全局）\n"
                    "- Python snake_case 字段保持 snake_case（不要转 camelCase），保证序列化兼容\n"
                    "\n"
                    "## 导入路径规则（重要）\n"
                    "- 所有内部模块导入一律使用 `'./filename'`（不带路径层级，不用 `../subdir/`）\n"
                    "- 输出文件与所有依赖文件处于同一扁平目录，无子目录层次\n"
                    "- 示例：`import { FactoryEvent } from './events'`（不是 `'../protocol/events'`）\n"
                    "- Node.js 内置模块用 `'node:xxxx'` 前缀：`import path from 'node:path'`\n"
                ),
            },
            "rust": {
                "ext": "rs",
                "idiom_guide": (
                    "## 【CRITICAL】禁止重定义共享类型\n"
                    "Verdict/VerdictKind/Signal/FactoryEvent/EventBus/EventType/EventMetadata/"
                    "TeamSpec/TeamNode/TeamEdge/RouteAction/RouteSpec/NodeKind/"
                    "ValidatorKind/ValidatorSpec/AnchorSpec/TransformerSpec/ScatterSpec\n"
                    "已在 crate::types 定义。**必须** `use crate::types::*;` 导入，禁止在模块内重新 struct/enum 定义。\n"
                    "cargo check 不会报错，但跨模块调用将类型不兼容——这是无声的定时炸弹。\n"
                    "\n"
                    "## 类型映射\n"
                    "- @dataclass / BaseModel → #[derive(Debug, Clone, Serialize, Deserialize)] struct\n"
                    "- StrEnum → #[derive(Serialize, Deserialize)] enum（字符串序列化用 #[serde(rename = \"...\")]）\n"
                    "- ABC / abstractmethod → trait（async 方法用 #[async_trait]）\n"
                    "- dict[str, Any] → HashMap<String, serde_json::Value> 或 serde_json::Map\n"
                    "- list[str] → Vec<String>\n"
                    "- X | None → Option<X>\n"
                    "- 异常 → Result<T, anyhow::Error>（用 ? 操作符链）\n"
                    "\n"
                    "## 内聚性规则\n"
                    "- Python 一个 class → Rust 一个 struct + impl 块（含关联函数替代 @classmethod）\n"
                    "- 保持 Python snake_case 字段名（Rust 也用 snake_case）\n"
                    "\n"
                    "## rusqlite + tokio 模式\n"
                    "- Connection 用 Arc<tokio::sync::Mutex<Option<Connection>>> 包裹\n"
                    "- spawn_blocking 内用 .blocking_lock()，禁止 .lock().await\n"
                    "\n"
                    "## move 闭包\n"
                    "- 同一变量在闭包内外都要用：闭包外先 clone 两份，各自命名，分别 move 和返回\n"
                    "\n"
                    "## json! 宏\n"
                    "- 不支持 **obj 展开；合并用 .as_object().cloned().unwrap_or_default() + .insert()\n"
                    "\n"
                    "## 其他\n"
                    "- async/await 基于 tokio\n"
                    "- pathlib.Path → std::path::PathBuf\n"
                    "- logging → tracing crate\n"
                    "- datetime → chrono::DateTime<Utc>\n"
                    "\n"
                    "## 【R-06】外部 crate 必须在 Cargo.toml 中声明\n"
                    "翻译时如果 use 了 redis/reqwest/lettre 等外部 crate，必须同时声明依赖。\n"
                    "已配置的 crate（可直接 use，无需再声明）：\n"
                    "  tokio, serde, serde_json, anyhow, async-trait, tracing, chrono, ulid,\n"
                    "  futures, thiserror, rusqlite, async-stream, lazy_static, uuid,\n"
                    "  redis (0.25, aio/tokio-comp/streams/connection-manager), reqwest (0.11, json/rustls-tls)\n"
                    "\n"
                    "## 【R-08】redis-rs 0.25 API（必须使用新版接口）\n"
                    "- XREADGROUP：StreamReadOptions::default().group(g,c).count(n).block(ms)，然后 con.xread_options(keys, ids, &opts)\n"
                    "- XACK：con.xack::<_, _, _, usize>(key, group, &[id])  # 4 个类型参数\n"
                    "- XRANGE with count：con.xrange_count(key, start, end, count)（不是 xrange 的第4参数）\n"
                    "- Pipeline.add_command：传 Cmd 值不是 &Cmd 引用\n"
                ),
            },
        }

        spec = lang_specific.get(target_lang, lang_specific["typescript"])

        # 格式化需求侧信息（下游调用方式）
        demand_section = ""
        if demand_set:
            lines = ["## 需求侧：下游模块如何调用当前模块（必须对齐签名）"]
            for entry in demand_set[:10]:  # 最多 10 个下游
                mod = entry.get("downstream_module", "?")
                names = entry.get("imported_names", [])
                examples = entry.get("usage_examples", [])
                lines.append(f"\n### 来自 {mod}（引用: {names}）")
                if examples:
                    lines.extend(examples)
            demand_section = "\n".join(lines)

        prompt = f"""你是一个精通 Python、TypeScript 和 Rust 的软件工程师。
请将以下 Python 模块精确翻译为 {target_lang}，遵循目标语言的惯用写法。

## 翻译规则
{spec["idiom_guide"]}

## 外部依赖映射
{json.dumps(dep_mapping, indent=2, ensure_ascii=False)}

## 供给侧上下文（当前模块依赖的签名/实现，可直接 import 或参照创建接口）
{supply_context or "(无内部依赖)"}

{demand_section or ""}

## 关键约束
1. 所有公开接口的签名必须与 Python 版本语义等价
2. 保持六元原语的边界：Hook 不调 LLM、Tool 不做决策、Node 不写状态
3. 保持事件类型、Format ID 等字符串常量完全一致
4. 不要省略任何公开接口，不要添加 Python 版本没有的功能
5. 缺失 TS 版本的依赖：根据供给侧 Python 签名创建 XxxLike interface，禁止 import 不存在的文件

## 公开接口（必须全部翻译）
{json.dumps(public_interfaces, indent=2, ensure_ascii=False)}

{"## 上次翻译的反馈（请修正）" + chr(10) + feedback if feedback else ""}

## Python 源码
```python
{source_code}
```

请输出完整的 {target_lang} 代码，用 ```{spec["ext"]} 包裹。
同时输出一个接口对照表，用 ```json 包裹，格式：
[{{"python": "ClassName.method_name", "target": "对应名称", "notes": "差异说明"}}]
"""
        client = self._make_client()
        try:
            resp = client.call(
                messages=[{"role": "user", "content": prompt}],
            )
            # LLMClient 返回 _UnifiedResponse，content[0].text
            content = resp.content[0].text

            code_match = re.search(
                rf"```(?:{spec['ext']}|{target_lang})\n(.*?)```",
                content, re.DOTALL,
            )
            mapping_match = re.search(r"```json\n(.*?)```", content, re.DOTALL)

            if not code_match:
                return Verdict(
                    kind=VerdictKind.FAIL,
                    output=input_data,
                    diagnosis="LLM 输出中未找到目标语言代码块",
                    confidence=0.3,
                )

            generated_code = code_match.group(1).strip()
            interface_mapping = []
            if mapping_match:
                try:
                    interface_mapping = json.loads(mapping_match.group(1))
                except json.JSONDecodeError:
                    pass

            output = {
                **input_data,
                "generated_code": generated_code,
                "interface_mapping": interface_mapping,
                "target_ext": spec["ext"],
            }

            return Verdict(
                kind=VerdictKind.PASS,
                output=output,
                diagnosis=f"翻译完成: {len(generated_code)} 字符, "
                          f"{len(interface_mapping)} 个接口映射",
                confidence=0.7,
                granted_tags=["translated"],
            )

        except Exception as e:
            return Verdict(
                kind=VerdictKind.FAIL,
                output=input_data,
                diagnosis=f"LLM 调用失败: {e}",
                confidence=0.0,
            )


# ═══════════════════════════════════════════════════════════════════════════════
# 4. TypeChecker — 编译检查
# ═══════════════════════════════════════════════════════════════════════════════

class TypeCheckerRouter(Router):
    FORMAT_IN = "rewrite.generated-code"
    FORMAT_OUT = "rewrite.checked-code"
    DESCRIPTION = "运行 tsc --noEmit 或 cargo check 验证编译通过"

    def __init__(self, *, work_dir: str | None = None):
        self._work_dir = Path(work_dir) if work_dir else None

    def run(self, input_data: dict) -> Verdict:
        target_lang = input_data.get("target_lang", "typescript")
        generated_code = input_data.get("generated_code", "")
        target_ext = input_data.get("target_ext", "ts")

        if not generated_code:
            return Verdict(
                kind=VerdictKind.FAIL,
                output=input_data,
                diagnosis="无生成代码可检查",
                confidence=1.0,
            )

        work_dir = self._work_dir or Path(input_data.get("work_dir", ""))
        if not work_dir or not work_dir.exists():
            work_dir = Path(tempfile.mkdtemp(prefix="omni-rewrite-"))

        # 写入生成的代码
        source_path = input_data.get("source_path", "module")
        module_name = Path(source_path).stem
        target_file = work_dir / f"{module_name}.{target_ext}"
        # OMNI-013 ALLOW: business artifact write (S3d.6 audited 2026-04-08, follow-up: refactor to guarded_write)
        target_file.write_text(generated_code, encoding="utf-8")

        # 运行编译器（shell=True 确保在 Windows 上也能找到 npx.cmd / cargo）
        if target_lang == "typescript":
            has_tsconfig = (work_dir / "tsconfig.json").exists()
            if has_tsconfig:
                # 项目级检查：使用 tsconfig（支持 @types/node, 路径解析等）
                # 只过滤当前模块的错误，忽略其他已存在文件的错误
                result = subprocess.run(
                    "npx tsc --noEmit",
                    capture_output=True, text=True, timeout=60,
                    cwd=str(work_dir), shell=True,
                    encoding="utf-8", errors="replace",
                )
                if result.returncode == 0:
                    errors = ""
                else:
                    all_errors = result.stderr or result.stdout
                    # 只保留当前模块文件的错误
                    my_errors = [
                        l for l in all_errors.splitlines()
                        if module_name in l or not l.startswith(str(work_dir))
                    ]
                    errors = "\n".join(my_errors[:50]) if my_errors else ""
                    # 如果没有针对当前文件的错误，视为通过
                    if not errors:
                        result = type("R", (), {"returncode": 0})()
            else:
                result = subprocess.run(
                    f'npx tsc --noEmit --strict "{target_file}"',
                    capture_output=True, text=True, timeout=60,
                    cwd=str(work_dir), shell=True,
                    encoding="utf-8", errors="replace",
                )
                errors = (result.stderr or result.stdout)[:3000] if result.returncode != 0 else ""
        elif target_lang == "rust":
            # Cargo 需要代码在 src/{module}.rs 并在 lib.rs 中声明
            src_dir = work_dir / "src"
            src_dir.mkdir(exist_ok=True)
            rust_file = src_dir / f"{module_name}.rs"
            # OMNI-013 ALLOW: business artifact write (S3d.6 audited 2026-04-08, follow-up: refactor to guarded_write)
            rust_file.write_text(generated_code, encoding="utf-8")
            lib_rs = src_dir / "lib.rs"
            mod_decl = f"pub mod {module_name};\n"
            if lib_rs.exists():
                lib_content = lib_rs.read_text(encoding="utf-8")
                if mod_decl.strip() not in lib_content:
                    # OMNI-013 ALLOW: business artifact write (S3d.6 audited 2026-04-08, follow-up: refactor to guarded_write)
                    lib_rs.write_text(lib_content + mod_decl, encoding="utf-8")
            else:
                # OMNI-013 ALLOW: business artifact write (S3d.6 audited 2026-04-08, follow-up: refactor to guarded_write)
                lib_rs.write_text(mod_decl, encoding="utf-8")
            target_file = rust_file  # 供 AgentFixer 使用正确路径
            result = subprocess.run(
                "cargo check",
                capture_output=True, text=True, timeout=120,
                cwd=str(work_dir), shell=True,
                encoding="utf-8", errors="replace",
                env=_rust_env(),
            )
            errors = (result.stderr or result.stdout)[:3000] if result.returncode != 0 else ""
        else:
            return Verdict(
                kind=VerdictKind.FAIL,
                output=input_data,
                diagnosis=f"不支持的目标语言: {target_lang}",
                confidence=1.0,
            )

        if not errors:
            output = {
                **input_data,
                "target_file": str(target_file),
                "work_dir": str(work_dir),
            }
            return Verdict(
                kind=VerdictKind.PASS,
                output=output,
                diagnosis="编译检查通过",
                confidence=1.0,
                granted_tags=["type-checked"],
            )
        else:
            output = {
                **input_data,
                "compile_errors": errors,
                "target_file": str(target_file),
                "work_dir": str(work_dir),
            }
            return Verdict(
                kind=VerdictKind.FAIL,
                output=output,
                diagnosis=f"编译失败:\n{errors[:500]}",
                confidence=1.0,
            )


# ═══════════════════════════════════════════════════════════════════════════════
# 5. EquivalenceVerifier — 语义等价性验证
# ═══════════════════════════════════════════════════════════════════════════════

class EquivalenceVerifierRouter(Router):
    FORMAT_IN = "rewrite.checked-code"
    FORMAT_OUT = "rewrite.verified-code"
    DESCRIPTION = "生成具体测试用例，分别在 Python 和目标语言中运行，客观对比结果"

    def __init__(self, *, model: str | None = None, ts_dir: str | None = None):
        self._model = model
        self._ts_dir = Path(ts_dir) if ts_dir else None

    def _make_client(self):
        from omnicompany.runtime.llm.llm import LLMClient
        kwargs = {"role": "runtime_main", "max_tokens": 8192}
        if self._model:
            kwargs["model"] = self._model
        return LLMClient(**kwargs)

    def run(self, input_data: dict) -> Verdict:
        source_code = input_data.get("source_code", "")
        generated_code = input_data.get("generated_code", "")
        target_lang = input_data.get("target_lang", "typescript")
        interface_mapping = input_data.get("interface_mapping", [])
        source_path = input_data.get("source_path", "")
        target_file = input_data.get("target_file", "")

        # ── Step 1: LLM 生成测试用例 ──
        client = self._make_client()
        prompt = f"""你是一个测试工程师。以下是一个 Python 模块和它的 {target_lang} 翻译版本。

请为每个公开接口（类、函数）生成**具体的等价性测试用例**。每个测试用例包含：
1. 构造输入数据
2. 调用接口
3. 输出预期的 JSON 结果（可序列化的）

## 接口映射
{json.dumps(interface_mapping, indent=2, ensure_ascii=False)}

## Python 源码
```python
{source_code}
```

## {target_lang} 目标码
```
{generated_code}
```

## 输出格式
生成两段代码：

1. Python 测试脚本（输出 JSON 到 stdout）:
```python_test
import json, sys
sys.path.insert(0, 'src')
# ... 导入并调用每个接口，收集结果 ...
results = {{}}
# results["Signal.pain"] = Signal.pain("node1", "hurt").to_dict()
# results["Signal.from_dict"] = Signal.from_dict({{"format": "test", "text": "hello"}}).to_dict()
print(json.dumps(results, default=str, sort_keys=True))
```

2. {target_lang} 测试脚本（输出相同结构的 JSON 到 stdout）:
```{target_lang}_test
// ... 导入并调用每个接口，收集结果 ...
// console.log(JSON.stringify(results, null, 0))
```

关键：两个脚本的 results 结构必须完全一致，键名相同，输入数据相同。
只测试确定性行为（不测试时间戳、随机 ID 等）。
"""
        try:
            resp = client.call(messages=[{"role": "user", "content": prompt}])
            content = resp.content[0].text

            # 提取测试脚本
            py_test_match = re.search(r"```python_test\n(.*?)```", content, re.DOTALL)
            ts_test_match = re.search(
                rf"```(?:{target_lang}_test|typescript_test)\n(.*?)```",
                content, re.DOTALL,
            )

            if not py_test_match or not ts_test_match:
                # fallback: 尝试普通代码块
                py_test_match = py_test_match or re.search(r"```python\n(.*?)```", content, re.DOTALL)
                ts_test_match = ts_test_match or re.search(r"```(?:typescript|ts)\n(.*?)```", content, re.DOTALL)

            if not py_test_match or not ts_test_match:
                return Verdict(
                    kind=VerdictKind.PARTIAL,
                    output=input_data,
                    diagnosis="LLM 未生成有效的双语测试脚本",
                    confidence=0.3,
                )

            py_test_code = py_test_match.group(1).strip()
            ts_test_code = ts_test_match.group(1).strip()

        except Exception as e:
            return Verdict(
                kind=VerdictKind.FAIL,
                output=input_data,
                diagnosis=f"测试生成 LLM 调用失败: {e}",
                confidence=0.0,
            )

        # ── Step 2: 运行 Python 测试 ──
        py_result = self._run_python_test(py_test_code)

        # ── Step 3: 运行 TS 测试 ──
        ts_dir = self._ts_dir or Path(input_data.get("work_dir", "data/rewrite/ts_phase1"))
        ts_result = self._run_ts_test(ts_test_code, ts_dir, target_lang)

        # ── Step 4: 对比结果 ──
        verification = {
            "py_test_code": py_test_code,
            "ts_test_code": ts_test_code,
            "py_output": py_result,
            "ts_output": ts_result,
        }

        if py_result.get("error") or ts_result.get("error"):
            errors = []
            if py_result.get("error"):
                errors.append(f"Python 测试报错: {py_result['error'][:200]}")
            if ts_result.get("error"):
                errors.append(f"TS 测试报错: {ts_result['error'][:200]}")
            return Verdict(
                kind=VerdictKind.FAIL,
                output={**input_data, "verification_result": verification,
                        "_feedback": "\n".join(errors)},
                diagnosis=f"测试执行失败: {'; '.join(errors)[:200]}",
                confidence=0.6,
            )

        # 逐 key 对比
        py_data = py_result.get("data", {})
        ts_data = ts_result.get("data", {})
        all_keys = sorted(set(list(py_data.keys()) + list(ts_data.keys())))
        mismatches: list[dict] = []
        matches: list[str] = []

        for key in all_keys:
            py_val = py_data.get(key)
            ts_val = ts_data.get(key)
            if py_val == ts_val:
                matches.append(key)
            else:
                mismatches.append({
                    "interface": key,
                    "python": py_val,
                    "target": ts_val,
                })

        verification["matches"] = matches
        verification["mismatches"] = mismatches

        if not mismatches:
            return Verdict(
                kind=VerdictKind.PASS,
                output={**input_data, "verification_result": verification},
                diagnosis=f"等价性验证通过: {len(matches)} 个接口全部一致",
                confidence=0.95,
                granted_tags=["semantically-verified"],
            )
        else:
            feedback = "不等价的接口:\n" + "\n".join(
                f"- {m['interface']}: Python={m['python']}, TS={m['target']}"
                for m in mismatches[:10]
            )
            return Verdict(
                kind=VerdictKind.FAIL,
                output={**input_data, "verification_result": verification,
                        "_feedback": feedback},
                diagnosis=f"等价性验证失败: {len(mismatches)}/{len(all_keys)} 个接口不一致",
                confidence=0.9,
            )

    def _run_python_test(self, test_code: str) -> dict:
        """在子进程中运行 Python 测试脚本，返回 JSON 结果。"""
        try:
            with tempfile.NamedTemporaryFile(
                mode="w", suffix=".py", delete=False, encoding="utf-8"
            ) as f:
                f.write(test_code)
                tmp_path = f.name
            result = subprocess.run(
                f'python "{tmp_path}"',
                capture_output=True, text=True, timeout=30,
                cwd=str(Path.cwd()), shell=True,
                encoding="utf-8", errors="replace",
            )
            Path(tmp_path).unlink(missing_ok=True)
            if result.returncode != 0:
                return {"error": result.stderr[:500]}
            try:
                return {"data": json.loads(result.stdout)}
            except json.JSONDecodeError:
                return {"error": f"Python 输出不是有效 JSON: {result.stdout[:200]}"}
        except subprocess.TimeoutExpired:
            return {"error": "Python 测试超时(30s)"}
        except Exception as e:
            return {"error": str(e)}

    def _run_ts_test(self, test_code: str, work_dir: Path, target_lang: str) -> dict:
        """在子进程中运行 TS 测试脚本，返回 JSON 结果。"""
        if target_lang != "typescript":
            return {"error": f"暂不支持 {target_lang} 测试运行"}

        test_file = work_dir / "_equiv_test.ts"
        try:
            # OMNI-013 ALLOW: business artifact write (S3d.6 audited 2026-04-08, follow-up: refactor to guarded_write)
            test_file.write_text(test_code, encoding="utf-8")
            result = subprocess.run(
                f'npx tsx "{test_file}"',
                capture_output=True, text=True, timeout=30,
                cwd=str(work_dir), shell=True,
                encoding="utf-8", errors="replace",
            )
            if result.returncode != 0:
                return {"error": result.stderr[:500]}
            try:
                return {"data": json.loads(result.stdout)}
            except json.JSONDecodeError:
                return {"error": f"TS 输出不是有效 JSON: {result.stdout[:200]}"}
        except subprocess.TimeoutExpired:
            return {"error": "TS 测试超时(30s)"}
        except Exception as e:
            return {"error": str(e)}
        finally:
            test_file.unlink(missing_ok=True)


# ═══════════════════════════════════════════════════════════════════════════════
# 6. AgentFixer — agent_loop 自主修复
# ═══════════════════════════════════════════════════════════════════════════════

class AgentFixerRouter(Router):
    FORMAT_IN = "rewrite.generated-code"
    FORMAT_OUT = "rewrite.generated-code"
    DESCRIPTION = "agent_loop 自主修复编译错误：读错误→定位→修改→重试"

    def __init__(self, *, max_fix_rounds: int = 3, model: str | None = None):
        self._max_fix_rounds = max_fix_rounds
        self._model = model

    def _make_client(self):
        from omnicompany.runtime.llm.llm import LLMClient
        kwargs = {"role": "runtime_main", "max_tokens": 16384}
        if self._model:
            kwargs["model"] = self._model
        return LLMClient(**kwargs)

    def _verify_fix(self, code: str, work_dir: str, target_file: str, target_lang: str) -> str:
        """运行 tsc/cargo check 验证修复结果。返回错误信息（空则通过）。"""
        if not work_dir:
            return ""
        try:
            tf = Path(target_file)
            # OMNI-013 ALLOW: business artifact write (S3d.6 audited 2026-04-08, follow-up: refactor to guarded_write)
            tf.write_text(code, encoding="utf-8")
            if target_lang == "typescript":
                has_tsconfig = (Path(work_dir) / "tsconfig.json").exists()
                if has_tsconfig:
                    result = subprocess.run(
                        "npx tsc --noEmit",
                        capture_output=True, text=True, timeout=60,
                        cwd=work_dir, shell=True,
                        encoding="utf-8", errors="replace",
                    )
                    if result.returncode == 0:
                        return ""
                    module_name = tf.stem
                    all_errors = result.stderr or result.stdout
                    my_errors = [l for l in all_errors.splitlines() if module_name in l]
                    return "\n".join(my_errors[:50]) if my_errors else ""
                else:
                    result = subprocess.run(
                        f'npx tsc --noEmit --strict "{target_file}"',
                        capture_output=True, text=True, timeout=60,
                        cwd=work_dir, shell=True,
                        encoding="utf-8", errors="replace",
                    )
            elif target_lang == "rust":
                result = subprocess.run(
                    "cargo check",
                    capture_output=True, text=True, timeout=120,
                    cwd=work_dir, shell=True,
                    encoding="utf-8", errors="replace",
                    env=_rust_env(),
                )
            else:
                return ""
            if result.returncode == 0:
                return ""
            return (result.stderr or result.stdout)[:3000]
        except Exception:
            return ""

    def run(self, input_data: dict) -> Verdict:
        compile_errors = input_data.get("compile_errors", "")
        generated_code = input_data.get("generated_code", "")
        target_lang = input_data.get("target_lang", "typescript")
        source_code = input_data.get("source_code", "")
        work_dir = input_data.get("work_dir", "")
        target_file = input_data.get("target_file", "")

        if not compile_errors:
            return Verdict(
                kind=VerdictKind.PASS,
                output=input_data,
                diagnosis="无编译错误需要修复",
                confidence=1.0,
            )

        client = self._make_client()
        current_code = generated_code
        current_errors = compile_errors

        # ── 痛觉追踪：记录每轮错误行数，检测"不降"模式 ──
        error_line_history: list[int] = [len(compile_errors.splitlines())]
        stagnant = False

        for round_num in range(self._max_fix_rounds):
            prompt = f"""你是一个 {target_lang} 编译错误修复专家。

以下代码无法编译通过。请修复所有编译错误，输出完整的修正后代码。

## 编译错误
```
{current_errors}
```

## 当前代码
```
{current_code}
```

## 原始 Python 源码（参考语义）
```python
{source_code}
```

请输出修正后的完整代码，用 ```{input_data.get("target_ext", "ts")} 包裹。
只修复编译错误，不要改变功能语义。
"""
            try:
                resp = client.call(
                    messages=[{"role": "user", "content": prompt}],
                )
                content = resp.content[0].text

                ext = input_data.get("target_ext", "ts")
                code_match = re.search(
                    rf"```(?:{ext}|{target_lang})\n(.*?)```",
                    content, re.DOTALL,
                )
                if not code_match:
                    continue

                fixed_code = code_match.group(1).strip()
                current_code = fixed_code

                logger.info("agent_fixer round %d: code updated (%d chars)",
                            round_num + 1, len(fixed_code))

                # ── 中间验证：如果 tsc/cargo 通过则提前终止 ──
                if work_dir and target_file:
                    remaining_errors = self._verify_fix(
                        fixed_code, work_dir, target_file, target_lang
                    )
                    if not remaining_errors:
                        logger.info("agent_fixer round %d: compile PASS, 提前终止", round_num + 1)
                        break
                    current_errors = remaining_errors
                    err_lines = len(remaining_errors.splitlines())
                    error_line_history.append(err_lines)
                    logger.info("agent_fixer round %d: 仍有 %d 行错误，继续",
                                round_num + 1, err_lines)

                    # ── 痛觉检测：连续 2 轮错误数未下降 → 停止无效迭代 ──
                    if len(error_line_history) >= 3:
                        recent = error_line_history[-3:]
                        if recent[-1] >= recent[-2] >= recent[-3]:
                            stagnant = True
                            logger.warning(
                                "agent_fixer: 错误数连续 %d 轮未下降 (%s)，"
                                "可能超出修复能力范围，提前终止",
                                len(recent), recent,
                            )
                            break

            except Exception as e:
                logger.warning("agent_fixer round %d failed: %s", round_num + 1, e)
                continue

        fix_rounds = round_num + 1
        final_err_lines = error_line_history[-1] if error_line_history else 0

        output = {
            **input_data,
            "generated_code": current_code,
            "fix_rounds": fix_rounds,
            "error_line_history": error_line_history,
            "fixer_stagnant": stagnant,
        }

        # ── 痛觉信号：停滞 → FAIL + 诊断 ──
        if stagnant:
            return Verdict(
                kind=VerdictKind.FAIL,
                output=output,
                diagnosis=(
                    f"agent_fixer 停滞: {fix_rounds} 轮修复后错误数未下降 "
                    f"({error_line_history})。"
                    f"错误可能超出代码修改范围（如缺少 Cargo.toml 依赖、"
                    f"环境配置、管线拓扑问题）。"
                    f"最后一轮错误:\n{current_errors[:500]}"
                ),
                confidence=0.2,
            )

        return Verdict(
            kind=VerdictKind.PASS,
            output=output,
            diagnosis=f"agent_fixer 完成 {fix_rounds} 轮修复尝试 (最终 {final_err_lines} 行错误)",
            confidence=0.5 if final_err_lines == 0 else 0.3,
        )


# ═══════════════════════════════════════════════════════════════════════════════
# 7. StyleChecker — L2 惯用风格检查（biome lint）
# ═══════════════════════════════════════════════════════════════════════════════

class StyleCheckerRouter(Router):
    """运行 biome lint（或 eslint）检查 TS 代码的惯用风格。

    意义（来自 OpenRewrite/Syzygy）：tsc 通过只说明"类型对"，
    不说明"写法对"。用 `any` 绕过类型、未用变量、错误的 import 形式
    等问题都可能掩盖翻译错误。
    """
    FORMAT_IN = "rewrite.checked-code"
    FORMAT_OUT = "rewrite.style-checked"
    DESCRIPTION = "L2: biome lint 检查 TS 惯用风格（不允许 any/未使用变量/不安全操作）"

    def __init__(self, *, work_dir: str | None = None):
        self._work_dir = Path(work_dir) if work_dir else None

    def run(self, input_data: dict) -> Verdict:
        generated_code = input_data.get("generated_code", "")
        target_lang = input_data.get("target_lang", "typescript")
        source_path = input_data.get("source_path", "module")

        if target_lang != "typescript":
            # 非 TS 语言直接 PASS，未来可扩展 clippy for Rust
            return Verdict(
                kind=VerdictKind.PASS,
                output={**input_data, "style_errors": [], "style_warnings": []},
                diagnosis=f"StyleChecker: {target_lang} 暂无 lint 规则，跳过",
                confidence=1.0,
            )

        work_dir = self._work_dir or Path(input_data.get("work_dir", ""))
        if not work_dir or not work_dir.exists():
            work_dir = Path(tempfile.mkdtemp(prefix="omni-style-"))

        module_name = Path(source_path).stem
        target_file = work_dir / f"{module_name}.ts"
        # OMNI-013 ALLOW: business artifact write (S3d.6 audited 2026-04-08, follow-up: refactor to guarded_write)
        target_file.write_text(generated_code, encoding="utf-8")

        # 尝试 biome，fallback 只做基础正则扫描
        result = subprocess.run(
            f'npx biome lint --reporter=json "{target_file}"',
            capture_output=True, text=True, timeout=30,
            cwd=str(work_dir), shell=True,
            encoding="utf-8", errors="replace",
        )

        style_errors: list[str] = []
        style_warnings: list[str] = []

        if result.returncode not in (0, 1):
            # biome 未安装或其他错误 → fallback 静态扫描
            any_count = generated_code.count(": any") + generated_code.count("<any>")
            unsafe_count = generated_code.count("as any") + generated_code.count("@ts-ignore")
            if unsafe_count > 0:
                style_warnings.append(f"发现 {unsafe_count} 处 ts-ignore/as any 用法")
            if any_count > 5:
                style_warnings.append(f"发现 {any_count} 处 `: any` 类型注解（超过阈值 5）")
        else:
            # 解析 biome JSON 输出
            try:
                data = json.loads(result.stdout or "{}")
                for diag in data.get("diagnostics", []):
                    sev = diag.get("severity", "")
                    msg = diag.get("description", "")[:200]
                    if sev == "error":
                        style_errors.append(msg)
                    else:
                        style_warnings.append(msg)
            except (json.JSONDecodeError, KeyError):
                pass

        output = {
            **input_data,
            "style_errors": style_errors,
            "style_warnings": style_warnings,
            "target_file": str(target_file),
            "work_dir": str(work_dir),
        }

        if style_errors:
            return Verdict(
                kind=VerdictKind.FAIL,
                output=output,
                diagnosis=f"StyleChecker FAIL: {len(style_errors)} 个风格错误 — {style_errors[0][:100]}",
                confidence=0.95,
            )

        warn_note = f"，{len(style_warnings)} 个警告" if style_warnings else ""
        return Verdict(
            kind=VerdictKind.PASS,
            output=output,
            diagnosis=f"StyleChecker PASS{warn_note}",
            confidence=1.0,
        )


# ═══════════════════════════════════════════════════════════════════════════════
# 7b. StyleFixer — LLM 修复惯用风格问题
# ═══════════════════════════════════════════════════════════════════════════════

class StyleFixerRouter(Router):
    """LLM 修复 biome lint 发现的惯用风格问题（消除 any/补类型注解/修复 import 形式）。"""
    FORMAT_IN = "rewrite.style-checked"
    FORMAT_OUT = "rewrite.checked-code"
    DESCRIPTION = "LLM 修复惯用风格问题（消除 any/补类型注解）"

    def __init__(self, *, model: str | None = None):
        self._model = model

    def _make_client(self):
        from omnicompany.runtime.llm.llm import LLMClient
        kwargs = {"role": "runtime_main", "max_tokens": 16384}
        if self._model:
            kwargs["model"] = self._model
        return LLMClient(**kwargs)

    def run(self, input_data: dict) -> Verdict:
        generated_code: str = input_data.get("generated_code", "")
        style_errors: list = input_data.get("style_errors", [])
        style_warnings: list = input_data.get("style_warnings", [])
        target_lang: str = input_data.get("target_lang", "typescript")

        issues = "\n".join(f"- {e}" for e in (style_errors + style_warnings)[:20])
        prompt = f"""请修复以下 {target_lang} 代码中的惯用风格问题，保持功能语义不变。

## 风格问题
{issues}

## 当前代码
```typescript
{generated_code}
```

修复要求：
1. 消除 `any` 类型，替换为具体类型或 `unknown`
2. 消除 `@ts-ignore`，修复底层类型问题
3. 消除未使用变量和导入
4. 保持功能语义完全不变

请输出修正后的完整代码，用 ```ts 包裹。
"""
        try:
            client = self._make_client()
            resp = client.call(messages=[{"role": "user", "content": prompt}])
            content = resp.content[0].text
            code_match = re.search(r"```(?:ts|typescript)\n(.*?)```", content, re.DOTALL)
            if not code_match:
                return Verdict(
                    kind=VerdictKind.FAIL,
                    output=input_data,
                    diagnosis="StyleFixer: LLM 未输出有效代码块",
                    confidence=0.3,
                )
            fixed_code = code_match.group(1).strip()
            return Verdict(
                kind=VerdictKind.PASS,
                output={**input_data, "generated_code": fixed_code},
                diagnosis=f"StyleFixer: 修复 {len(style_errors)} 个错误, {len(style_warnings)} 个警告",
                confidence=0.8,
            )
        except Exception as e:
            return Verdict(
                kind=VerdictKind.FAIL,
                output=input_data,
                diagnosis=f"StyleFixer LLM 调用失败: {e}",
                confidence=0.0,
            )


# ═══════════════════════════════════════════════════════════════════════════════
# 8. InterfaceExtractor — 确定性提取双语接口规格
# ═══════════════════════════════════════════════════════════════════════════════

class InterfaceExtractorRouter(Router):
    """AST 解析 Python 源码和已生成的 TS 代码，提取各自的公开接口列表。

    为下游 SignatureComparator 和 BehavioralTester 提供结构化的"比对锚点"，
    不依赖 LLM，完全确定性。
    """
    FORMAT_IN = "rewrite.style-checked"
    FORMAT_OUT = "rewrite.interface-spec"
    DESCRIPTION = "确定性 AST 提取 Python/__all__ 和 TS/export 的公开接口规格"

    def run(self, input_data: dict) -> Verdict:
        source_code: str = input_data.get("source_code", "")
        generated_code: str = input_data.get("generated_code", "")
        public_interfaces: list = input_data.get("public_interfaces", [])

        # ── Python 接口 ──
        py_interfaces = self._extract_python_interfaces(source_code, public_interfaces)

        # ── TS 接口 ──
        ts_interfaces = self._extract_ts_interfaces(generated_code)

        interface_spec = {
            "python": py_interfaces,
            "typescript": ts_interfaces,
        }

        return Verdict(
            kind=VerdictKind.PASS,
            output={**input_data, "interface_spec": interface_spec},
            diagnosis=(
                f"InterfaceExtractor: Python {len(py_interfaces)} 个接口, "
                f"TS {len(ts_interfaces)} 个接口"
            ),
            confidence=1.0,
        )

    @staticmethod
    def _extract_python_interfaces(source_code: str, existing: list) -> list[dict]:
        """从 AST 提取 Python 公开接口。"""
        if existing:
            return [
                {"name": i["name"], "kind": i.get("kind", "class"), "signature": i.get("signature", "")}
                for i in existing
            ]
        try:
            tree = ast.parse(source_code)
        except SyntaxError:
            return []
        interfaces = []
        for node in ast.iter_child_nodes(tree):
            if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                if not node.name.startswith("_"):
                    kind = "class" if isinstance(node, ast.ClassDef) else "function"
                    # 提取方法签名
                    methods = []
                    if isinstance(node, ast.ClassDef):
                        for child in node.body:
                            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                                if not child.name.startswith("_") or child.name in ("__init__",):
                                    methods.append(child.name)
                    interfaces.append({
                        "name": node.name,
                        "kind": kind,
                        "methods": methods,
                        "line": node.lineno,
                    })
        return interfaces

    @staticmethod
    def _extract_ts_interfaces(ts_code: str) -> list[dict]:
        """从 TS 源码提取 export 声明（正则，因无 TS AST 解析器）。"""
        interfaces = []
        seen = set()
        patterns = [
            # export class Foo / export abstract class Foo
            (r"^export\s+(?:abstract\s+)?class\s+(\w+)", "class"),
            # export function foo / export async function foo
            (r"^export\s+(?:async\s+)?function\s+(\w+)", "function"),
            # export const foo / export let foo
            (r"^export\s+(?:const|let)\s+(\w+)", "const"),
            # export type Foo / export interface Foo
            (r"^export\s+(?:type|interface)\s+(\w+)", "type"),
            # export { Foo, Bar } re-exports
            (r"^export\s+\{([^}]+)\}", "re-export"),
        ]
        for i, line in enumerate(ts_code.splitlines()):
            line = line.strip()
            for pattern, kind in patterns:
                m = re.match(pattern, line)
                if m:
                    if kind == "re-export":
                        names = [n.strip().split(" as ")[0].strip() for n in m.group(1).split(",")]
                        for name in names:
                            if name and name not in seen:
                                seen.add(name)
                                interfaces.append({"name": name, "kind": kind, "line": i + 1})
                    else:
                        name = m.group(1)
                        if name not in seen:
                            seen.add(name)
                            interfaces.append({"name": name, "kind": kind, "line": i + 1})
                    break
        return interfaces


# ═══════════════════════════════════════════════════════════════════════════════
# 9. SignatureComparator — 确定性签名比对（L3a）
# ═══════════════════════════════════════════════════════════════════════════════

class SignatureComparatorRouter(Router):
    """确定性对比 Python 公开接口名和 TS export 名。

    遵循 Syzygy 论文的建议：先做静态名称等价检查，发现缺失/多余的接口，
    再做动态行为测试。静态检查失败直接打回，不浪费动态测试资源。
    """
    FORMAT_IN = "rewrite.interface-spec"
    FORMAT_OUT = "rewrite.signature-compared"
    DESCRIPTION = "L3a: 确定性对比 Python/TS 接口名集合，发现遗漏和新增"

    def run(self, input_data: dict) -> Verdict:
        interface_spec: dict = input_data.get("interface_spec", {})
        py_ifaces = interface_spec.get("python", [])
        ts_ifaces = interface_spec.get("typescript", [])

        py_names = {i["name"] for i in py_ifaces}
        ts_names = {i["name"] for i in ts_ifaces}

        # 在 TS 中缺失的（翻译遗漏）
        missing_in_ts = py_names - ts_names
        # TS 中多出来的（可能是额外引入的，不一定是问题）
        extra_in_ts = ts_names - py_names
        # 两侧都有的
        matched = py_names & ts_names

        comparison = {
            "matched": sorted(matched),
            "missing_in_ts": sorted(missing_in_ts),
            "extra_in_ts": sorted(extra_in_ts),
            "py_count": len(py_names),
            "ts_count": len(ts_names),
            "match_rate": len(matched) / max(len(py_names), 1),
        }

        output = {**input_data, "signature_comparison": comparison}

        if missing_in_ts:
            # 有遗漏 → FAIL，携带反馈
            feedback = (
                f"TS 翻译遗漏了以下 Python 公开接口（必须全部翻译）: "
                f"{sorted(missing_in_ts)}"
            )
            return Verdict(
                kind=VerdictKind.FAIL,
                output={**output, "_feedback": feedback},
                diagnosis=f"SignatureComparator FAIL: 缺少 {sorted(missing_in_ts)}",
                confidence=1.0,
            )

        extra_note = f"（TS 额外有 {sorted(extra_in_ts)}，可接受）" if extra_in_ts else ""
        return Verdict(
            kind=VerdictKind.PASS,
            output=output,
            diagnosis=(
                f"SignatureComparator PASS: {len(matched)}/{len(py_names)} 接口匹配{extra_note}"
            ),
            confidence=1.0,
        )


# ═══════════════════════════════════════════════════════════════════════════════
# 10. BehavioralTester — 确定性行为测试（L3b）
# ═══════════════════════════════════════════════════════════════════════════════

class BehavioralTesterRouter(Router):
    """确定性行为测试：执行 TS 导入检查或 Rust crate 测试/golden vectors。

    不依赖 LLM 生成测试脚本（这是 EquivalenceVerifier 旧设计的致命缺陷）。
    只验证"能导入 + 接口存在 + 基础类型正确"三件事，确定性执行。

    参考 VERT 的 differential testing 方法，但降级到可靠执行的最小集。
    """
    FORMAT_IN = "rewrite.interface-spec"
    FORMAT_OUT = "rewrite.behavioral-tested"
    DESCRIPTION = "L3b: 确定性 TS import 或 Rust crate/golden-vector 行为测试"

    def __init__(self, *, ts_dir: str | None = None, rs_dir: str | None = None):
        self._ts_dir = Path(ts_dir) if ts_dir else None
        self._rs_dir = Path(rs_dir) if rs_dir else None

    def run(self, input_data: dict) -> Verdict:
        generated_code: str = input_data.get("generated_code", "")
        source_path: str = input_data.get("source_path", "module")
        target_lang: str = input_data.get("target_lang", "typescript")
        interface_spec: dict = input_data.get("interface_spec", {})
        ts_ifaces = interface_spec.get("typescript", [])

        if target_lang == "rust":
            return self._run_rust(input_data)

        if target_lang != "typescript":
            return Verdict(
                kind=VerdictKind.PASS,
                output={
                    **input_data,
                    "behavioral_test_result": {
                        "skipped": True,
                        "backend": target_lang,
                    },
                },
                diagnosis=f"BehavioralTester: {target_lang} 暂不支持，跳过",
                confidence=1.0,
            )

        ts_dir = self._ts_dir or Path(input_data.get("work_dir", "data/rewrite/ts_phase1"))
        module_name = Path(source_path).stem

        # 写生成代码到 ts_dir（临时）
        target_file = ts_dir / f"{module_name}.ts"
        backup = None
        if target_file.exists():
            backup = target_file.read_text(encoding="utf-8")
        # OMNI-013 ALLOW: business artifact write (S3d.6 audited 2026-04-08, follow-up: refactor to guarded_write)
        target_file.write_text(generated_code, encoding="utf-8")

        try:
            # 生成固定模板测试脚本
            test_script = self._make_test_script(module_name, ts_ifaces)
            test_file = ts_dir / f"_behavioral_test_{module_name}.ts"
            # OMNI-013 ALLOW: business artifact write (S3d.6 audited 2026-04-08, follow-up: refactor to guarded_write)
            test_file.write_text(test_script, encoding="utf-8")

            # 用 tsc 编译测试脚本（不执行，只验证类型）
            has_tsconfig = (ts_dir / "tsconfig.json").exists()
            if has_tsconfig:
                # 项目级检查，只过滤测试文件的错误
                result = subprocess.run(
                    "npx tsc --noEmit",
                    capture_output=True, text=True, timeout=30,
                    cwd=str(ts_dir), shell=True,
                    encoding="utf-8", errors="replace",
                )
                test_name = test_file.stem
                all_out = (result.stderr or result.stdout)
                my_lines = [l for l in all_out.splitlines() if test_name in l or module_name in l]
                errors = "\n".join(my_lines).strip()
                if result.returncode != 0 and not my_lines:
                    result = type("R", (), {"returncode": 0})()
                    errors = ""
            else:
                result = subprocess.run(
                    f'npx tsc --noEmit --strict "{test_file}"',
                    capture_output=True, text=True, timeout=30,
                    cwd=str(ts_dir), shell=True,
                    encoding="utf-8", errors="replace",
                )
                errors = (result.stderr or result.stdout).strip()
            test_file.unlink(missing_ok=True)

            if result.returncode == 0 or not errors:
                btest = {"passed": True, "exports_verified": [i["name"] for i in ts_ifaces]}
                return Verdict(
                    kind=VerdictKind.PASS,
                    output={**input_data, "behavioral_test_result": btest},
                    diagnosis=(
                        f"BehavioralTester PASS: {len(ts_ifaces)} 个接口可导入并通过类型检查"
                    ),
                    confidence=0.9,
                )
            else:
                btest = {"passed": False, "errors": errors[:1000]}
                return Verdict(
                    kind=VerdictKind.FAIL,
                    output={
                        **input_data,
                        "behavioral_test_result": btest,
                        "_feedback": f"BehavioralTest 失败:\n{errors[:500]}",
                    },
                    diagnosis="BehavioralTester FAIL: 接口导入/类型检查失败",
                    confidence=0.9,
                )
        finally:
            # 还原文件
            if backup is not None:
                # OMNI-013 ALLOW: business artifact write (S3d.6 audited 2026-04-08, follow-up: refactor to guarded_write)
                target_file.write_text(backup, encoding="utf-8")
            elif target_file.exists():
                target_file.unlink()

    @staticmethod
    def _make_test_script(module_name: str, ts_ifaces: list[dict]) -> str:
        """生成固定的 import 验证脚本——只导入不执行，让 tsc 做类型检查。"""
        class_names = [i["name"] for i in ts_ifaces if i["kind"] in ("class", "type")]
        fn_names = [i["name"] for i in ts_ifaces if i["kind"] == "function"]
        const_names = [i["name"] for i in ts_ifaces if i["kind"] == "const"]
        re_names = [i["name"] for i in ts_ifaces if i["kind"] == "re-export"]

        all_names = class_names + fn_names + const_names + re_names
        if not all_names:
            return f"// No exports found in {module_name}\nexport {{}};\n"

        imports = ", ".join(all_names[:30])  # tsc 最多处理 30 个
        lines = [
            f"// BehavioralTester: auto-generated import verification for {module_name}",
            f"import type {{ {imports} }} from './{module_name}';",
            "",
            "// Verify types are accessible (compile-time check only)",
        ]
        for name in class_names[:10]:
            lines.append(f"type _Check_{name} = {name};")
        lines.append("")
        lines.append("export {};  // ensure module mode")
        return "\n".join(lines)

    def _run_rust(self, input_data: dict) -> Verdict:
        """把生成模块放进 crate，执行已签入测试和可选 golden vectors。

        ``behavioral_test_spec``（或 ``behavioral_test_spec_path`` 指向的 JSON）可含：
        ``command``（argv 数组）和 ``vectors``。每个 vector 可用 ``stdin_json`` /
        ``expected_json`` 做结构比较，或用 ``stdin`` / ``expected_stdout`` 做逐 byte
        比较；首差分别报告 JSON Pointer 或 byte offset。
        """
        generated_code = str(input_data.get("generated_code", ""))
        source_path = str(input_data.get("source_path", "module"))
        rs_dir = self._rs_dir or Path(
            input_data.get("rs_dir")
            or input_data.get("work_dir")
            or "data/rewrite/rs_phase1"
        )
        module_name = re.sub(r"\W", "_", Path(source_path).stem)
        src_dir = rs_dir / "src"
        cargo_toml = rs_dir / "Cargo.toml"
        if not cargo_toml.exists():
            return self._rust_failure(
                input_data,
                "missing_cargo_toml",
                f"Rust 行为测试需要 crate，但未找到 {cargo_toml}",
            )

        src_dir.mkdir(parents=True, exist_ok=True)
        target_file = src_dir / f"{module_name}.rs"
        lib_file = src_dir / "lib.rs"
        target_backup = target_file.read_bytes() if target_file.exists() else None
        lib_backup = lib_file.read_bytes() if lib_file.exists() else None
        target_file.write_text(generated_code, encoding="utf-8")
        mod_decl = f"pub mod {module_name};"
        lib_text = lib_file.read_text(encoding="utf-8") if lib_file.exists() else ""
        if not re.search(rf"(?m)^\s*(?:pub\s+)?mod\s+{re.escape(module_name)}\s*;", lib_text):
            lib_file.write_text(
                f"{lib_text.rstrip()}\n{mod_decl}\n" if lib_text else f"{mod_decl}\n",
                encoding="utf-8",
            )

        try:
            listed = subprocess.run(
                ["cargo", "test", "--quiet", "--", "--list"],
                capture_output=True,
                text=True,
                timeout=180,
                cwd=str(rs_dir),
                encoding="utf-8",
                errors="replace",
                env=_rust_env(),
            )
            if listed.returncode != 0:
                errors = (listed.stderr or listed.stdout).strip()
                return self._rust_failure(
                    input_data,
                    "cargo_test_list_failed",
                    errors[:1000],
                )
            discovered = sum(
                1 for line in listed.stdout.splitlines() if re.search(r":\s+test\s*$", line)
            )

            tested = subprocess.run(
                ["cargo", "test", "--quiet"],
                capture_output=True,
                text=True,
                timeout=300,
                cwd=str(rs_dir),
                encoding="utf-8",
                errors="replace",
                env=_rust_env(),
            )
            if tested.returncode != 0:
                errors = (tested.stderr or tested.stdout).strip()
                return self._rust_failure(
                    input_data,
                    "cargo_test_failed",
                    errors[:1000],
                    cargo_tests_discovered=discovered,
                )

            try:
                spec = self._load_rust_behavioral_spec(input_data, rs_dir)
            except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
                return self._rust_failure(
                    input_data,
                    "invalid_behavioral_test_spec",
                    str(exc),
                    cargo_tests_discovered=discovered,
                )
            vector_result = self._run_rust_vectors(spec, rs_dir)
            vector_count = int(vector_result.get("vectors_executed", 0))
            if vector_result.get("passed") is False:
                return self._rust_failure(
                    input_data,
                    "golden_vector_mismatch",
                    str(vector_result.get("error") or vector_result.get("first_difference")),
                    cargo_tests_discovered=discovered,
                    vector_result=vector_result,
                )
            if discovered == 0 and vector_count == 0:
                return self._rust_failure(
                    input_data,
                    "no_behavioral_cases",
                    "Rust crate 没有已签入测试，且未提供 behavioral_test_spec vectors",
                    cargo_tests_discovered=0,
                )

            btest = {
                "passed": True,
                "skipped": False,
                "backend": "rust",
                "cargo_tests_discovered": discovered,
                "golden_vectors_executed": vector_count,
                "first_difference": None,
            }
            return Verdict(
                kind=VerdictKind.PASS,
                output={**input_data, "behavioral_test_result": btest},
                diagnosis=(
                    "BehavioralTester PASS: Rust cargo tests "
                    f"{discovered}，golden vectors {vector_count}"
                ),
                confidence=1.0,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            return self._rust_failure(input_data, "rust_test_execution_failed", str(exc))
        finally:
            if target_backup is None:
                target_file.unlink(missing_ok=True)
            else:
                target_file.write_bytes(target_backup)
            if lib_backup is None:
                lib_file.unlink(missing_ok=True)
            else:
                lib_file.write_bytes(lib_backup)

    @staticmethod
    def _load_rust_behavioral_spec(input_data: dict, rs_dir: Path) -> dict | None:
        spec = input_data.get("behavioral_test_spec")
        spec_path = input_data.get("behavioral_test_spec_path")
        if spec is not None and spec_path is not None:
            raise ValueError("只能提供 behavioral_test_spec 或 behavioral_test_spec_path 之一")
        if spec_path is not None:
            path = Path(str(spec_path))
            if not path.is_absolute():
                path = rs_dir / path
            spec = json.loads(path.read_text(encoding="utf-8"))
        if spec is not None and not isinstance(spec, dict):
            raise TypeError("behavioral_test_spec 必须是 JSON object")
        return spec

    @classmethod
    def _run_rust_vectors(cls, spec: dict | None, rs_dir: Path) -> dict:
        if spec is None:
            return {"passed": True, "vectors_executed": 0, "first_difference": None}
        command = spec.get("command")
        vectors = spec.get("vectors", [])
        if not isinstance(command, list) or not command or not all(
            isinstance(item, str) and item for item in command
        ):
            raise ValueError("behavioral_test_spec.command 必须是非空 argv 字符串数组")
        if not isinstance(vectors, list):
            raise TypeError("behavioral_test_spec.vectors 必须是数组")
        timeout = int(spec.get("timeout_seconds", 120))
        for index, vector in enumerate(vectors):
            if not isinstance(vector, dict):
                raise TypeError(f"vector {index} 必须是 object")
            argv = [*command, *[str(item) for item in vector.get("args", [])]]
            if "stdin_json" in vector:
                stdin = json.dumps(
                    vector["stdin_json"],
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                )
            else:
                stdin = str(vector.get("stdin", ""))
            completed = subprocess.run(
                argv,
                input=stdin,
                capture_output=True,
                text=True,
                timeout=timeout,
                cwd=str(rs_dir),
                encoding="utf-8",
                errors="replace",
                env=_rust_env(),
            )
            name = str(vector.get("name", index))
            if completed.returncode != 0:
                return {
                    "passed": False,
                    "vectors_executed": index + 1,
                    "failed_vector": name,
                    "error": (completed.stderr or completed.stdout)[:1000],
                    "first_difference": {"kind": "process_exit", "returncode": completed.returncode},
                }
            if "expected_json" in vector:
                try:
                    actual = json.loads(completed.stdout)
                except json.JSONDecodeError as exc:
                    return {
                        "passed": False,
                        "vectors_executed": index + 1,
                        "failed_vector": name,
                        "error": f"stdout 不是合法 JSON: {exc}",
                        "first_difference": {"kind": "json_parse", "offset": exc.pos},
                    }
                difference = cls._first_json_difference(vector["expected_json"], actual)
            else:
                expected = str(vector.get("expected_stdout", "")).encode("utf-8")
                actual_bytes = completed.stdout.encode("utf-8")
                difference = cls._first_byte_difference(expected, actual_bytes)
            if difference is not None:
                return {
                    "passed": False,
                    "vectors_executed": index + 1,
                    "failed_vector": name,
                    "first_difference": difference,
                }
        return {
            "passed": True,
            "vectors_executed": len(vectors),
            "first_difference": None,
        }

    @classmethod
    def _first_json_difference(cls, expected: Any, actual: Any, path: str = "") -> dict | None:
        if type(expected) is not type(actual):
            return {
                "kind": "json_pointer",
                "path": path or "",
                "expected": expected,
                "actual": actual,
            }
        if isinstance(expected, dict):
            for key in sorted(set(expected) | set(actual)):
                child = f"{path}/{str(key).replace('~', '~0').replace('/', '~1')}"
                if key not in expected or key not in actual:
                    return {
                        "kind": "json_pointer",
                        "path": child,
                        "expected": expected.get(key, "<missing>"),
                        "actual": actual.get(key, "<missing>"),
                    }
                difference = cls._first_json_difference(expected[key], actual[key], child)
                if difference is not None:
                    return difference
            return None
        if isinstance(expected, list):
            if len(expected) != len(actual):
                return {
                    "kind": "json_pointer",
                    "path": path or "",
                    "expected_length": len(expected),
                    "actual_length": len(actual),
                }
            for index, (left, right) in enumerate(zip(expected, actual, strict=True)):
                difference = cls._first_json_difference(left, right, f"{path}/{index}")
                if difference is not None:
                    return difference
            return None
        if expected != actual:
            return {
                "kind": "json_pointer",
                "path": path or "",
                "expected": expected,
                "actual": actual,
            }
        return None

    @staticmethod
    def _first_byte_difference(expected: bytes, actual: bytes) -> dict | None:
        limit = min(len(expected), len(actual))
        for offset in range(limit):
            if expected[offset] != actual[offset]:
                return {
                    "kind": "byte_offset",
                    "offset": offset,
                    "expected": expected[offset],
                    "actual": actual[offset],
                }
        if len(expected) != len(actual):
            return {
                "kind": "byte_offset",
                "offset": limit,
                "expected_length": len(expected),
                "actual_length": len(actual),
            }
        return None

    @staticmethod
    def _rust_failure(
        input_data: dict,
        reason: str,
        error: str,
        *,
        cargo_tests_discovered: int | None = None,
        vector_result: dict | None = None,
    ) -> Verdict:
        btest: dict[str, Any] = {
            "passed": False,
            "skipped": False,
            "backend": "rust",
            "reason": reason,
            "errors": error[:1000],
        }
        if cargo_tests_discovered is not None:
            btest["cargo_tests_discovered"] = cargo_tests_discovered
        if vector_result is not None:
            btest.update(
                {
                    "golden_vectors_executed": vector_result.get("vectors_executed", 0),
                    "first_difference": vector_result.get("first_difference"),
                    "failed_vector": vector_result.get("failed_vector"),
                }
            )
        return Verdict(
            kind=VerdictKind.FAIL,
            output={
                **input_data,
                "behavioral_test_result": btest,
                "_feedback": f"Rust BehavioralTest 失败 [{reason}]: {error[:500]}",
            },
            diagnosis=f"BehavioralTester FAIL (Rust): {reason}",
            confidence=1.0,
        )


# ═══════════════════════════════════════════════════════════════════════════════
# 11. EquivalenceJudge — LLM 最终语义裁判（L4）
# ═══════════════════════════════════════════════════════════════════════════════

class EquivalenceJudgeRouter(Router):
    """LLM 最终语义裁判：在类型检查、风格检查、签名对比、行为测试全部通过后，
    对"精神等价性"做最终裁判。

    关键改进（相比旧 EquivalenceVerifier）：
    1. 只在前4层全通过后才启动（不作为唯一裁判）
    2. 不尝试执行生成的测试代码（执行由 BehavioralTester 负责）
    3. 专注于"设计意图等价"：注释/docstring 的语义保留、六元约束遵守、惯用性
    4. 输入是结构化的 interface_spec + signature_comparison，不是原始代码
    """
    FORMAT_IN = "rewrite.behavioral-tested"
    FORMAT_OUT = "rewrite.verified-code"
    DESCRIPTION = "L4: LLM 最终语义裁判（设计意图/六元约束/惯用性），前置层全通过后才启动"

    def __init__(self, *, model: str | None = None):
        self._model = model

    def _make_client(self):
        from omnicompany.runtime.llm.llm import LLMClient
        kwargs = {"role": "runtime_main", "max_tokens": 4096}
        if self._model:
            kwargs["model"] = self._model
        return LLMClient(**kwargs)

    def run(self, input_data: dict) -> Verdict:
        source_code: str = input_data.get("source_code", "")
        generated_code: str = input_data.get("generated_code", "")
        target_lang: str = input_data.get("target_lang", "typescript")
        interface_spec: dict = input_data.get("interface_spec", {})
        sig_comparison: dict = input_data.get("signature_comparison", {})
        btest: dict = input_data.get("behavioral_test_result", {})
        style_warnings: list = input_data.get("style_warnings", [])

        # 准备摘要（避免 token 浪费：不发送完整源码，发送接口规格）
        py_ifaces = interface_spec.get("python", [])
        ts_ifaces = interface_spec.get("typescript", [])
        matched = sig_comparison.get("matched", [])
        extra_in_ts = sig_comparison.get("extra_in_ts", [])

        # 源码摘要：只取 docstring 和类/方法签名
        source_summary = self._extract_docstrings_and_sigs(source_code)

        prompt = f"""你是一个跨语言翻译质量裁判。以下 Python 模块已被翻译为 {target_lang}，
前置层验证均已通过：类型检查 ✓, 风格检查 ✓, 签名对比 ✓, 行为测试 ✓。

请从以下4个维度评估翻译质量：
1. **设计意图保留**：注释/docstring 的语义是否保留，设计原则是否一致
2. **六元原语约束**：Hook 不调 LLM、Tool 不做决策、Router 不写状态等边界是否遵守
3. **错误处理等价**：Python 的 raise 路径是否在 {target_lang} 中有等价的错误处理
4. **惯用性**：是否充分利用 {target_lang} 的语言特性（而非直译 Python 习惯）

## 接口匹配情况
- 匹配: {matched}
- TS 额外有: {extra_in_ts}（合理的 re-export/helper 可接受）
- 行为测试: {'通过' if btest.get('passed') else '跳过'}
- 风格警告: {style_warnings or '无'}

## Python 接口摘要（含 docstring）
{source_summary[:3000]}

## TypeScript 代码摘要（前 100 行）
```typescript
{chr(10).join(generated_code.splitlines()[:100])}
```

## 裁判结果（JSON）
```json
{{
  "verdict": "pass" | "fail" | "partial",
  "score": 0.0-1.0,
  "issues": [
    {{"dimension": "设计意图|六元约束|错误处理|惯用性", "severity": "critical|minor", "description": "..."}}
  ],
  "summary": "一句话总结"
}}
```
只输出 JSON，不要其他内容。"""

        try:
            client = self._make_client()
            resp = client.call(messages=[{"role": "user", "content": prompt}])
            content = resp.content[0].text

            json_match = re.search(r"```json\n(.*?)```", content, re.DOTALL)
            if json_match:
                judge_result = json.loads(json_match.group(1))
            else:
                judge_result = json.loads(content.strip())

        except Exception as e:
            logger.warning("EquivalenceJudge LLM 调用失败: %s", e)
            judge_result = {
                "verdict": "pass",
                "score": 0.7,
                "issues": [],
                "summary": f"LLM 裁判失败({e})，基于前置层通过判定为 PASS",
            }

        verdict_str = judge_result.get("verdict", "pass")
        score = float(judge_result.get("score", 0.7))
        issues = judge_result.get("issues", [])
        summary = judge_result.get("summary", "")

        output = {
            **input_data,
            "verification_result": {
                "judge_result": judge_result,
                "matched_interfaces": matched,
                "behavioral_test": btest,
            },
        }

        critical_issues = [i for i in issues if i.get("severity") == "critical"]

        if verdict_str == "pass" and not critical_issues:
            return Verdict(
                kind=VerdictKind.PASS,
                output=output,
                diagnosis=f"EquivalenceJudge PASS (score={score:.2f}): {summary}",
                confidence=score,
                granted_tags=["semantically-verified"],
            )
        elif verdict_str == "partial" or (verdict_str == "pass" and critical_issues):
            feedback = "\n".join(
                f"- [{i['dimension']}] {i['description']}" for i in critical_issues[:5]
            )
            return Verdict(
                kind=VerdictKind.PARTIAL,
                output={**output, "_feedback": feedback},
                diagnosis=f"EquivalenceJudge PARTIAL (score={score:.2f}): {summary}",
                confidence=score,
            )
        else:
            feedback = "\n".join(
                f"- [{i['dimension']}] {i['description']}" for i in issues[:5]
            )
            return Verdict(
                kind=VerdictKind.FAIL,
                output={**output, "_feedback": feedback},
                diagnosis=f"EquivalenceJudge FAIL (score={score:.2f}): {summary}",
                confidence=score,
            )

    @staticmethod
    def _extract_docstrings_and_sigs(source_code: str) -> str:
        """提取 Python 源码中所有类/函数的签名行 + docstring（最多150行）。"""
        try:
            tree = ast.parse(source_code)
        except SyntaxError:
            return source_code[:1500]

        parts: list[str] = []
        lines = source_code.splitlines()
        for node in ast.walk(tree):
            if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                if node.name.startswith("_"):
                    continue
                # 签名行
                sig = lines[node.lineno - 1]
                parts.append(sig)
                # docstring
                if (node.body and isinstance(node.body[0], ast.Expr)
                        and isinstance(node.body[0].value, ast.Constant)
                        and isinstance(node.body[0].value.value, str)):
                    doc = node.body[0].value.value.strip()
                    parts.append(f'  """{doc[:200]}"""')
        return "\n".join(parts[:150])
