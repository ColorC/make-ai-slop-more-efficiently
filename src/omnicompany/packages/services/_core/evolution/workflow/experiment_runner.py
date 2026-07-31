# [OMNI] origin=claude-code domain=services/evolution ts=2026-04-08T03:23:38Z
# [OMNI] material_id="material:core.evolution.workflow.controlled_experiment_runner.py"
"""B.3 受控实验 Runner

把诊断报告第一条 proposed_change 落地为一次受控实验：
  1. 用 LLM 将修改建议转化为具体代码补丁（针对 Router 源文件）
  2. 将补丁写入临时模块，动态加载补丁 Router 类
  3. 用 ReplayRunner 原地重放目标节点及下游 LLM 节点（不产生新 trace）
  4. 返回 ExperimentResult，包含节点级改善判断

自动处理 prompt / logic 类型的变更（修改 Router 文件内容）；
insert_node / split_node 类型需要人工干预，本模块仅生成变更描述。
"""

from __future__ import annotations

import importlib
import importlib.util
import logging
import os
import tempfile
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from omnicompany.bus.sqlite import SQLiteBus
from omnicompany.packages.services._core.evolution.workflow.diagnosis import DiagnosisReport, ProposedChange, _load_node_source
from omnicompany.packages.services._core.evolution.workflow.events import DEFAULT_WORKFLOW_TAGS, publish_workflow_event
from omnicompany.packages.services._core.evolution.workflow.hypothesis import ExperimentRecord, HypothesisBoard
from omnicompany.packages.services._core.evolution.workflow.hypothesis_store import HypothesisBoardStore
from omnicompany.packages.services._core.evolution.workflow.replay_runner import ReplayRunner
from omnicompany.runtime.llm.llm import LLMClient

logger = logging.getLogger(__name__)

# ── 实验结果 ──


@dataclass
class ExperimentResult:
    """B.3 受控实验结果"""

    experiment_id: str
    board_id: str
    hypothesis_id: str
    proposed_change: ProposedChange

    original_trace_id: str
    experiment_trace_id: str | None = None

    # 成功/失败判断
    applied: bool = False
    """是否成功应用了变更"""

    verdict: str = "pending"
    """improved | unchanged | regression | failed_to_apply | requires_human"""

    improvement_score: float = 0.0
    """0.0~1.0 改善程度（相对原始 trace 的节点判定变化）"""

    # 详情
    patch_description: str = ""
    """实际应用的补丁描述"""

    patch_code: str = ""
    """生成的补丁代码片段"""

    side_effects: list[str] = field(default_factory=list)
    """意外回归的节点 ID"""

    notes: str = ""
    """补充说明（失败原因、人工建议等）"""

    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


# ── 代码补丁生成 ──

_SYSTEM_PATCH_PROMPT = """\
你是一个 LLM prompt 优化专家。你会收到一个 Router 类中的 prompt 字符串，以及修改建议。
你的任务是生成改进后的 prompt 文本。

要求：
1. 只返回新的 prompt 文本，用 ===PROMPT_START=== 和 ===PROMPT_END=== 包裹
2. 不要有任何其他内容
3. prompt 文本不要包含 ===PROMPT_START=== 或 ===PROMPT_END=== 这两个标记

格式示例：
===PROMPT_START===
你是一个...（新的 prompt 内容）
===PROMPT_END===
"""


def _extract_class_source(full_source: str, class_name: str) -> str:
    """从文件源码中提取单个类的代码（从类定义到下一个顶级类/函数/EOF）"""
    lines = full_source.splitlines(keepends=True)
    class_start = None
    for i, line in enumerate(lines):
        stripped = line.lstrip()
        if stripped.startswith(f"class {class_name}"):
            class_start = i
            break
    if class_start is None:
        return full_source  # 找不到时返回全文

    # 找到下一个顶级类/函数定义（列0开头的 class/def，不是本类）
    class_end = len(lines)
    for i in range(class_start + 1, len(lines)):
        line = lines[i]
        if line and not line[0].isspace() and (line.startswith("class ") or line.startswith("def ")):
            class_end = i
            break

    return "".join(lines[class_start:class_end])


def _inject_class_into_source(full_source: str, class_name: str, new_class_code: str) -> str:
    """将修改后的类代码注入回原文件（替换原类）"""
    lines = full_source.splitlines(keepends=True)
    class_start = None
    for i, line in enumerate(lines):
        if line.lstrip().startswith(f"class {class_name}"):
            class_start = i
            break
    if class_start is None:
        return full_source

    class_end = len(lines)
    for i in range(class_start + 1, len(lines)):
        line = lines[i]
        if line and not line[0].isspace() and (line.startswith("class ") or line.startswith("def ")):
            class_end = i
            break

    new_lines = (
        lines[:class_start]
        + [new_class_code if new_class_code.endswith("\n") else new_class_code + "\n"]
        + lines[class_end:]
    )
    return "".join(new_lines)


def _apply_method_patch(class_source: str, method_name: str, new_method_code: str) -> str:
    """将类源码中的指定方法替换为新方法代码"""
    lines = class_source.splitlines(keepends=True)
    method_start = None
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith(f"def {method_name}(") or stripped.startswith(f"async def {method_name}("):
            method_start = i
            break
    if method_start is None:
        return class_source  # 找不到则不修改

    # 找方法结束：下一个同缩进的 def 或类结束
    indent = len(lines[method_start]) - len(lines[method_start].lstrip())
    method_end = len(lines)
    for i in range(method_start + 1, len(lines)):
        line = lines[i]
        if not line.strip():
            continue
        cur_indent = len(line) - len(line.lstrip())
        if cur_indent <= indent and line.strip().startswith(("def ", "async def ")):
            method_end = i
            break

    new_lines = (
        lines[:method_start]
        + [new_method_code if new_method_code.endswith("\n") else new_method_code + "\n"]
        + lines[method_end:]
    )
    return "".join(new_lines)


def _extract_prompt_from_class(class_source: str) -> tuple[str, str, bool]:
    """从 Router 类源码中提取主 prompt 字符串（第一个多行字符串变量）

    Returns:
        (variable_name, prompt_text, is_fstring)
        is_fstring=True 表示原始 prompt 是 f-string（含动态插值）
    """
    import re
    # 匹配 prompt = """...""" 或 prompt = f"""...""" 形式
    pattern = re.compile(
        r'(\w*[Pp]rompt\w*)\s*=\s*(f?)"""(.*?)"""',
        re.DOTALL,
    )
    m = pattern.search(class_source)
    if m:
        is_fstring = m.group(2) == "f"
        return m.group(1), m.group(3), is_fstring
    return "", "", False


def _replace_prompt_in_class(class_source: str, var_name: str, new_prompt: str) -> str:
    """将类源码中的 prompt 字符串替换为新内容"""
    import re
    pattern = re.compile(
        rf'({re.escape(var_name)}\s*=\s*(?:f?)""").*?(""")',
        re.DOTALL,
    )
    replacement = rf'\g<1>{new_prompt}\g<2>'
    result, count = pattern.subn(replacement, class_source, count=1)
    if count == 0:
        logger.warning("[experiment] Could not find prompt variable '%s' to replace", var_name)
        return class_source
    return result


def _generate_patch(
    llm: LLMClient,
    node_source: str,
    change: ProposedChange,
) -> tuple[str, bool]:
    """用 LLM 生成修改后的 Router 类代码（仅支持 prompt 类型变更）

    Returns:
        (patched_class_code, requires_human)
    """
    class_name = "".join(w.capitalize() for w in change.target_node.split("_")) + "Router"
    file_content = node_source.split("\n", 1)[1] if "\n" in node_source else node_source
    class_source = _extract_class_source(file_content, class_name)

    # 提取当前 prompt
    var_name, current_prompt, is_fstring = _extract_prompt_from_class(class_source)
    if not var_name:
        return "找不到 prompt 字符串，无法自动应用修改", True

    # f-string prompt 额外警告：生成内容中的 { } 会破坏 Python 语法
    fstring_warning = ""
    if is_fstring:
        fstring_warning = (
            "\n⚠️ 重要限制：此 prompt 变量是 Python f-string（包含 {变量名} 动态插值）。"
            "在你生成的新 prompt 文本中，所有字面量大括号必须写成双大括号：{{ 和 }}。"
            "例如 TypeScript 代码示例 `export class Foo { method() { } }` 应写成 `export class Foo {{ method() {{ }} }}`。"
            "不要包含单独的 { 或 }，否则 Python 会报 SyntaxError。"
        )

    prompt = f"""## Prompt 优化任务

节点: {change.target_node}
修改描述: {change.change_description}
预期效果: {change.expected_effect}
{fstring_warning}

当前 prompt（变量名: {var_name}）:
---
{current_prompt[:2000]}
---

请生成改进后的 prompt 文本。
"""
    try:
        response = llm.call(
            messages=[{"role": "user", "content": prompt}],
            system=_SYSTEM_PATCH_PROMPT,
        )
        text = ""
        if hasattr(response, "content"):
            for block in response.content:
                if hasattr(block, "text"):
                    text += block.text
        elif isinstance(response, str):
            text = response

        text = text.strip()

        # 提取 prompt 内容
        if "===PROMPT_START===" in text and "===PROMPT_END===" in text:
            new_prompt = text.split("===PROMPT_START===")[1].split("===PROMPT_END===")[0].strip()
        else:
            # fallback：整个响应就是新 prompt
            new_prompt = text.strip()

        if not new_prompt:
            return "LLM 返回空 prompt", True

        # 应用替换
        patched_class = _replace_prompt_in_class(class_source, var_name, "\n" + new_prompt + "\n")

        # 语法验证：确保替换后的类代码可编译
        try:
            compile(patched_class, "<patched_class_validate>", "exec")
        except SyntaxError as se:
            logger.warning(
                "[experiment] Generated patch has syntax error (line %d: %s); returning requires_human",
                se.lineno, se.msg,
            )
            return f"生成的 patch 含语法错误（行{se.lineno}：{se.msg}），需人工修正", True

        return patched_class, False

    except Exception as e:
        logger.error("[experiment] Patch generation failed: %s", e)
        return str(e), True


_SYSTEM_LOGIC_PATCH_PROMPT = """\
你是一个 Python 代码优化专家。你会收到一个 Router 类的源码、目标方法名，以及修改建议。
你的任务是生成改进后的方法实现（完整的方法定义，含 def 行）。

要求：
1. 只返回新的方法代码，用 ===METHOD_START=== 和 ===METHOD_END=== 包裹
2. 方法必须与原方法签名兼容（参数名/类型一致）
3. 保持原方法的 async/sync 属性
4. 不要有任何其他内容

格式示例：
===METHOD_START===
    def run(self, input_data: dict) -> Verdict:
        ...（新的方法实现）
===METHOD_END===
"""


def _generate_logic_patch(
    llm: LLMClient,
    node_source: str,
    change: ProposedChange,
) -> tuple[str, bool]:
    """用 LLM 生成修改后的 Router 类代码（logic 类型变更：替换指定方法）

    Returns:
        (patched_class_code, requires_human)
    """
    class_name = "".join(w.capitalize() for w in change.target_node.split("_")) + "Router"
    file_content = node_source.split("\n", 1)[1] if "\n" in node_source else node_source
    class_source = _extract_class_source(file_content, class_name)

    method_name = change.target_method or "run"

    prompt = f"""## 方法逻辑优化任务

节点: {change.target_node}
Router 类: {class_name}
目标方法: {method_name}
修改描述: {change.change_description}
预期效果: {change.expected_effect}

Router 类完整源码:
---
{class_source[:4000]}
---

请生成改进后的 `{method_name}` 方法实现（完整方法定义，含 def 行和适当缩进）。
"""
    try:
        response = llm.call(
            messages=[{"role": "user", "content": prompt}],
            system=_SYSTEM_LOGIC_PATCH_PROMPT,
        )
        text = ""
        if hasattr(response, "content"):
            for block in response.content:
                if hasattr(block, "text"):
                    text += block.text
        elif isinstance(response, str):
            text = response

        text = text.strip()

        if "===METHOD_START===" in text and "===METHOD_END===" in text:
            new_method = text.split("===METHOD_START===")[1].split("===METHOD_END===")[0]
            # 去掉首尾空行但保留缩进
            new_method = new_method.strip("\n")
        else:
            new_method = text.strip()

        if not new_method:
            return "LLM 返回空方法代码", True

        patched_class = _apply_method_patch(class_source, method_name, new_method)
        return patched_class, False

    except Exception as e:
        logger.error("[experiment] Logic patch generation failed: %s", e)
        return str(e), True


# ── Router 文件定位 ──


def _find_router_file(node_id: str) -> Path | None:
    """找到包含 node_id 对应 Router 类的 Python 文件

    Post-2026-04-07 migration: Router implementations live under
    packages/<domain>/routers/ or runtime/nodes/.
    """
    # parents: [0]=workflow [1]=evolution [2]=packages [3]=omnicompany
    _omnicompany = Path(__file__).resolve().parents[4]
    search_roots = [
        _omnicompany / "packages",
        _omnicompany / "runtime" / "nodes",
    ]
    class_name = "".join(w.capitalize() for w in node_id.split("_")) + "Router"
    for root in search_roots:
        if not root.exists():
            continue
        for py_file in root.rglob("*.py"):
            try:
                content = py_file.read_text(encoding="utf-8")
                if class_name in content:
                    return py_file
            except Exception:
                continue
    return None


# ── 临时模块注入 ──


def _load_patched_module(patched_class_code: str, node_id: str, original_source: str) -> Any | None:
    """创建 shim 模块：导入原始模块所有内容，再用补丁类覆盖目标 Router 类"""
    class_name = "".join(w.capitalize() for w in node_id.split("_")) + "Router"

    # 从 original_source 中提取原始文件路径，推断 Python 模块路径
    # original_source 格式: "# 来源: E:\...\routers.py\n{content}"
    original_file: str | None = None
    first_line = original_source.split("\n")[0]
    if first_line.startswith("# 来源:"):
        original_file = first_line[len("# 来源:"):].strip()

    original_module_path: str | None = None
    if original_file:
        # 将文件路径转换为 Python module 路径（从 src/ 下开始）
        try:
            # parents: [0]=workflow [1]=evolution [2]=packages [3]=omnicompany [4]=src
            src_root = Path(__file__).resolve().parents[5]  # src/
            rel = Path(original_file).resolve().relative_to(src_root)
            original_module_path = str(rel).replace("\\", ".").replace("/", ".")[:-3]  # strip .py
        except ValueError:
            pass

    # 构建 shim 文件内容
    if original_module_path:
        shim_lines = [
            "# Experiment shim — imports from original + overrides patched class",
            "from __future__ import annotations",
            f"from {original_module_path} import *",
            "",
            "# === PATCHED CLASS ===",
            patched_class_code,
        ]
    else:
        # fallback: 直接放完整内容（有注入风险，仅作后备）
        file_content = original_source.split("\n", 1)[1] if "\n" in original_source else original_source
        full_patched = _inject_class_into_source(file_content, class_name, patched_class_code)
        shim_lines = [full_patched]

    shim_code = "\n".join(shim_lines)

    try:
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".py", prefix=f"_exp_{node_id}_",
            delete=False, encoding="utf-8",
        ) as f:
            f.write(shim_code)
            tmp_path = f.name

        spec = importlib.util.spec_from_file_location(
            f"_exp_router_{node_id}_{uuid.uuid4().hex[:8]}", tmp_path
        )
        if spec is None or spec.loader is None:
            return None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        router_cls = getattr(module, class_name, None)
        if router_cls is None:
            logger.warning("[experiment] Class %s not found in patched module", class_name)
            return None
        return router_cls

    except Exception as e:
        logger.error("[experiment] Failed to load patched module: %s", e)
        return None
    finally:
        try:
            os.unlink(tmp_path)
        except Exception:
            pass


# ── 管线重跑 ──


async def _run_pipeline_with_patch(
    pipeline_id: str,
    pipeline_input: dict,
    patched_node_id: str,
    patched_router_cls: Any,
    bus_path: str,
) -> str | None:
    """用补丁后的 Router 重跑管线，返回新 trace_id"""
    try:
        # 动态加载管线 build_pipeline 和 build_bindings
        # pipeline_input 可选含 'pipeline_module' 字段覆盖自动推导
        domain_override = pipeline_input.get("pipeline_module")
        if domain_override:
            domain = domain_override.replace("-", "_")
        else:
            # 约定：pipeline_id 格式为 "{domain}-pipeline"
            domain = pipeline_id.replace("-pipeline", "").replace("-", "_")
        # Post-2026-04-07: domain impls live at packages/<domain>/
        module_path = f"omnicompany.packages.{domain}.pipeline"
        run_module_path = f"omnicompany.packages.{domain}.run"

        pipeline_mod = importlib.import_module(module_path)
        run_mod = importlib.import_module(run_module_path)

        pipeline = pipeline_mod.build_pipeline()
        bindings = run_mod.build_bindings(pipeline_input)

        # 注入补丁 Router
        if patched_router_cls is not None:
            try:
                patched_instance = patched_router_cls()
                bindings[patched_node_id] = patched_instance
                logger.info("[experiment] Injected patched %s Router", patched_node_id)
            except Exception as e:
                logger.warning("[experiment] Could not instantiate patched Router: %s", e)

        # 创建隔离的 bus
        from omnicompany.runtime.exec.runner import TeamRunner
        bus = SQLiteBus(bus_path)
        await bus.connect()

        runner = TeamRunner(
            pipeline=pipeline,
            bindings=bindings,
            bus=bus,
        )
        await runner.run(pipeline_input)
        trace_id = runner.last_trace_id
        await bus.close()
        return str(trace_id) if trace_id else None

    except Exception as e:
        logger.error("[experiment] Pipeline re-run failed: %s", e)
        return None


# ── 主入口 ──


class ExperimentRunner:
    """B.3 受控实验 Runner

    用法：
        runner = ExperimentRunner(store)  # Move 8: unified path is the default
        result = await runner.run(board, report)
    """

    def __init__(
        self,
        store: HypothesisBoardStore,
        bus_path: str | None = None,  # Move 8: None → unified data/events.db
        experiment_bus_path: str | None = None,
        llm: LLMClient | None = None,
        event_bus: Any | None = None,
    ):
        self.store = store
        self.bus_path = bus_path
        # 实验 trace 写入单独的 db，避免污染原始 events
        self.experiment_bus_path = experiment_bus_path or (
            str(bus_path).replace(".db", "_exp.db") if bus_path else None
        )
        self._llm = llm or LLMClient()
        self._event_bus = event_bus

    async def _publish_event(
        self,
        event_type: str,
        *,
        trace_id: str,
        payload: dict[str, Any],
    ) -> None:
        await publish_workflow_event(
            self._event_bus,
            trace_id=trace_id,
            event_type=event_type,
            source="evolution.workflow.experiment_runner",
            payload=payload,
            tags=[*DEFAULT_WORKFLOW_TAGS, "experiment"],
            bus_path=self.bus_path,
        )

    async def _publish_result(
        self,
        board: HypothesisBoard,
        result: ExperimentResult,
    ) -> ExperimentResult:
        await self._publish_event(
            "evolution.workflow.experiment_completed",
            trace_id=board.trace_id,
            payload={
                "board_id": board.board_id,
                "experiment_id": result.experiment_id,
                "hypothesis_id": result.hypothesis_id,
                "target_node": result.proposed_change.target_node,
                "change_type": result.proposed_change.change_type,
                "verdict": result.verdict,
                "applied": result.applied,
                "improvement_score": result.improvement_score,
            },
        )
        return result

    async def run(
        self,
        board: HypothesisBoard,
        report: DiagnosisReport,
        change_index: int = 0,
        focus_hypothesis_id: str | None = None,
    ) -> ExperimentResult:
        """执行受控实验

        Args:
            board: 当前假设黑板
            report: 诊断报告（含 proposed_changes）
            change_index: 使用第几条 proposed_change（默认第 0 条）
            focus_hypothesis_id: 锁定前记录的 focus 假设 ID（防止 lock() 后 focus_candidate() 返回 None）
        """
        experiment_id = str(uuid.uuid4())
        # 优先用调用方传入的 ID（lock 之前捕获）；否则退化到 focus_candidate()
        if focus_hypothesis_id:
            hypothesis_id = focus_hypothesis_id
        else:
            focus = board.focus_candidate()
            hypothesis_id = focus.id if focus else ""

        if not report.proposed_changes:
            result = ExperimentResult(
                experiment_id=experiment_id,
                board_id=board.board_id,
                hypothesis_id=hypothesis_id,
                proposed_change=ProposedChange(
                    target_node="", change_type="", change_description="",
                    expected_effect="",
                ),
                original_trace_id=board.trace_id,
                verdict="failed_to_apply",
                notes="诊断报告无 proposed_changes",
            )
            return await self._publish_result(board, result)

        change = report.proposed_changes[change_index]

        logger.info(
            "[experiment] Starting experiment: node=%s type=%s",
            change.target_node, change.change_type,
        )

        result = ExperimentResult(
            experiment_id=experiment_id,
            board_id=board.board_id,
            hypothesis_id=hypothesis_id,
            proposed_change=change,
            original_trace_id=board.trace_id,
        )
        await self._publish_event(
            "evolution.workflow.experiment_started",
            trace_id=board.trace_id,
            payload={
                "board_id": board.board_id,
                "experiment_id": experiment_id,
                "hypothesis_id": hypothesis_id,
                "target_node": change.target_node,
                "change_type": change.change_type,
            },
        )

        # 只有 prompt / logic 类型支持自动实验；其他类型需要人工
        if change.change_type not in ("prompt", "logic"):
            result.verdict = "requires_human"
            result.notes = (
                f"change_type={change.change_type} 需要人工修改。\n"
                f"建议操作：{change.change_description}"
            )
            self._record_experiment(board, result)
            self.store.save(board)
            return await self._publish_result(board, result)

        # 加载 Router 源码
        node_source = _load_node_source(change.target_node, board.pipeline_id)
        if node_source.startswith("# 未找到"):
            result.verdict = "requires_human"
            result.notes = f"找不到 {change.target_node} 对应的 Router 源文件"
            self._record_experiment(board, result)
            self.store.save(board)
            return await self._publish_result(board, result)

        # 生成补丁（按变更类型路由）
        logger.info("[experiment] Generating %s patch via LLM...", change.change_type)
        if change.change_type == "logic":
            patched_code, requires_human = _generate_logic_patch(self._llm, node_source, change)
        else:
            patched_code, requires_human = _generate_patch(self._llm, node_source, change)

        if requires_human:
            result.verdict = "requires_human"
            result.patch_description = patched_code  # 人工参考描述
            result.notes = f"LLM 判定此变更需要人工干预: {patched_code[:200]}"
            self._record_experiment(board, result)
            self.store.save(board)
            return await self._publish_result(board, result)

        result.patch_code = patched_code
        result.patch_description = change.change_description

        # 先做语法检查（只检查补丁类代码本身，不检查全文件）
        try:
            compile(patched_code, "<patch_class>", "exec")
        except SyntaxError as se:
            logger.error("[experiment] Patch class syntax error: %s", se)
            result.verdict = "failed_to_apply"
            result.notes = f"补丁类语法错误（行 {se.lineno}）：{se.msg}"
            self._record_experiment(board, result)
            self.store.save(board)
            return await self._publish_result(board, result)

        patched_router_cls = _load_patched_module(patched_code, change.target_node, node_source)
        if patched_router_cls is None:
            result.verdict = "failed_to_apply"
            result.notes = "补丁模块加载失败（导入错误）"
            self._record_experiment(board, result)
            self.store.save(board)
            return await self._publish_result(board, result)

        result.applied = True

        # 原地重放：用 ReplayRunner 重跑目标节点及下游 LLM 节点，不产生新 trace
        logger.info(
            "[experiment] Replaying node %s (patch_type=%s)...",
            change.target_node, change.change_type,
        )
        replay_runner = ReplayRunner(bus_path=self.bus_path, llm=self._llm)
        replay_result = await replay_runner.run(
            board=board,
            patched_node_id=change.target_node,
            patched_router_cls=patched_router_cls,
            patch_type=change.change_type,
        )

        result.experiment_trace_id = None  # 原地重放不产生新 trace
        result.improvement_score = replay_result.improvement_score
        result.verdict = replay_result.verdict
        result.notes = replay_result.notes or ""

        # 记录回归节点
        result.side_effects = [
            nr.node_id
            for nr in replay_result.node_results
            if nr.direction == "regressed"
        ]

        logger.info(
            "[experiment] Replay done: verdict=%s improved=%.2f side_effects=%s",
            result.verdict, result.improvement_score, result.side_effects,
        )

        self._record_experiment(board, result)
        self.store.save(board)
        return await self._publish_result(board, result)

    def _record_experiment(
        self,
        board: HypothesisBoard,
        result: ExperimentResult,
    ) -> None:
        """将实验记录写入黑板 experiment_log"""
        focus = board.get_hypothesis(result.hypothesis_id)

        record = ExperimentRecord(
            id=result.experiment_id,
            hypothesis_id=result.hypothesis_id,
            locked_node=result.proposed_change.target_node,
            change_description=result.proposed_change.change_description,
            change_type=result.proposed_change.change_type,
            failing_traces_tested=[result.original_trace_id],
            outcome=result.verdict,
        )

        if result.experiment_trace_id:
            record.newly_passing_traces = [result.experiment_trace_id]

        board.experiment_log.append(record)
        board.active_experiment_id = result.experiment_id

        if focus:
            focus.experiment_count += 1
            focus.last_experiment_outcome = result.verdict
