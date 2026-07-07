# [OMNI] origin=claude-code domain=omnicompany/guardian ts=2026-04-24T00:00:00Z type=config
# [OMNI] material_id="material:core.guardian.compliance_prevention.circuit_breaker.py"
"""Guardian 规则 — 合规预防 (OMNI-070~074, 2026-04-24).

防止绕过 Worker / Material / 总线体系. 全部 `certainty=needs_judgment`,
由 GuardianAgent LLM 复核 — 因为"是否合法绕过"取决于继承链/语义,
枚举违规形式必然不全, 走 plan §二 "合法入口白名单, 其余皆违规" 范式.

规则速览:
    OMNI-070  Router/Worker 内直调 LLMClient (绕过 LLMCallRouter)
    OMNI-071  继承旧 runtime.agent.AgentNodeLoop (应迁新 packages.services.agent)
    OMNI-072  packages/ 下流程性工作不走 Worker 体系
    OMNI-073  scripts/*.py 跑业务逻辑 (应封装为 packages/ 内 Team)
    OMNI-074  packages/ 下孤儿模块候选 (应有 import 链或 manifest 声明)

合法入口 (LLM 判断时参考):
    - 单次 LLM 调用    → LLMCallRouter (packages.services.agent.routers.llm_call)
    - 多轮 agent 循环  → AgentNodeLoop (packages.services.agent)
    - 业务流程         → Team + Worker + Material 总线

豁免清单 (写规则时硬编码):
    - guardian 自己 3 个 AgentNodeLoop 子类 (judge_agent / llm_judge_agent / routers.HealthReporterRouter):
      DESIGN.md 明示阶段 D runtime 统一后处理
    - packages/services/agent/* : 合法入口本身
    - runtime/llm/* : LLMClient 定义
    - _archive / _graveyard / vendors : 通用豁免

参考: docs/plans/[2026-04-24]COMPLIANCE-PREVENTION/plan.md
"""
from __future__ import annotations

import ast
import re

from ._base import FileContext, GuardianRule, _is_external, _not_graveyard


# ══════════════════════════════════════════════════════════════
# 通用豁免
# ══════════════════════════════════════════════════════════════

# 路径前缀豁免 (合法入口本身 + 已知豁免点)
# 审计 (plan §十二): 每条豁免需过"极端情况法" + 有到期日/架构永久理由
_PATH_EXEMPTIONS: tuple[str, ...] = (
    # 架构永久: runtime/ 是 LLMClient/AgentNodeLoop/Worker 基类定义所在,
    # 基础设施层直调 LLMClient 是合法. 但此豁免意味 runtime/ 下业务代码会逃脱
    # 本规则 — 需另一条规则约束 "runtime/ 纯粹性" (下波).
    "src/omnicompany/runtime/",
    # 架构永久: 本服务是合法 Worker/AgentNodeLoop 入口定义
    "src/omnicompany/packages/services/agent/",
    # LLM-CALL-UNIFICATION T8 (2026-06-13): current core agent service home.
    # AgentNodeLoop, AgentRouter, external worker adapters, and spawn_surface.py
    # are legal infrastructure entry definitions, not orphan workflow modules.
    "src/omnicompany/packages/services/_core/agent/",
    # LLM-CALL-UNIFICATION T13 (2026-06-13): independent evolution workflow
    # orchestration is accepted only because these files now publish lifecycle
    # events through the shared EventBus bridge.
    "src/omnicompany/packages/services/_core/evolution/workflow/orchestrator.py",
    "src/omnicompany/packages/services/_core/evolution/workflow/experiment_runner.py",
    # 架构永久: Guardian 规则插件目录. rules/*.py 被 rules/__init__.py 显式聚合,
    # 文件本身不是业务流程模块, 不应被 OMNI-074 当孤儿候选。
    "src/omnicompany/packages/services/_core/guardian/rules/",
    "src/omnicompany/packages/services/guardian/rules/",
    # 架构永久: omnicompany 材料黑板核心入口. MaterialDispatcher 与
    # publish_material_event 是 2026-06-13 材料统一 T1 的权威设施,
    # 已在 DESIGN.md / MATERIAL-UNIFICATION plan 中声明长期价值。
    "src/omnicompany/packages/services/_core/omnicompany/material_dispatcher.py",
    "src/omnicompany/packages/services/_core/omnicompany/material_events.py",
    "src/omnicompany/packages/services/omnicompany/material_dispatcher.py",
    "src/omnicompany/packages/services/omnicompany/material_events.py",
    # 阶段性豁免 (到期日 2026-06-30 · plan [2026-04-18]AGENT-NODE-LOOP-ROUTERIZATION):
    # guardian 自己 3 个 AgentNodeLoop 子类因本 service 基础设施特殊需求仍继承旧基类,
    # DESIGN.md §已知局限 明示"阶段 D runtime 统一后一并迁". 到期未完成应重审.
    "src/omnicompany/packages/services/guardian/judge_agent.py",      # expires: 2026-06-30
    "src/omnicompany/packages/services/guardian/llm_judge_agent.py",  # expires: 2026-06-30
    "src/omnicompany/packages/services/guardian/routers.py",          # expires: 2026-06-30
    # 架构永久: Guardian 自己的审计留档 (2026-04-24 GuardianAuditStore)
    # 防递归: 扫 Guardian 不应把 Guardian 自己的审计记录当违规候选
    "data/services/guardian/",
)

# 阶段豁免到期日监控 (plan §十二 要求)
# TODO: 在 sentinel / patrol 周期检查中加一条: 若 today > expires, 升级告警
_STAGED_EXEMPTIONS_EXPIRES: dict[str, str] = {
    "src/omnicompany/packages/services/guardian/judge_agent.py": "2026-06-30",
    "src/omnicompany/packages/services/guardian/llm_judge_agent.py": "2026-06-30",
    "src/omnicompany/packages/services/guardian/routers.py": "2026-06-30",
}


_FLOW_OUTSIDE_WORKER_EXEMPTIONS: tuple[str, ...] = (
    # LLM-CALL-UNIFICATION T4 (2026-06-13): this adapter keeps the multi-turn
    # tool_use_id continuity contract until runtime owns a structured-chat API.
    "src/omnicompany/packages/domains/voxelcraft/content/paths/_llm_helpers.py",
)

_ORPHAN_MODULE_EXEMPTIONS: tuple[str, ...] = (
    # LLM-CALL-UNIFICATION T4 (2026-06-13): active governance/router/worker
    # modules migrated to runtime.llm.structured.call_json. These are not
    # orphan candidates; they are existing entrypoints that now consume the
    # single structured JSON authority.
    "src/omnicompany/packages/services/_governance/plan_steward/steward.py",
    "src/omnicompany/packages/services/_governance/work_history/miner.py",
    "src/omnicompany/packages/domains/voxelcraft/content/paths/_llm_helpers.py",
    "src/omnicompany/packages/domains/voxelcraft/content/paths/item/workers/item_asset_picker.py",
    "src/omnicompany/packages/domains/narrative/routers/beat_generate.py",
    "src/omnicompany/packages/domains/narrative/routers/csl_ingest.py",
    "src/omnicompany/packages/domains/narrative/routers/dialogue_generator.py",
    "src/omnicompany/packages/domains/narrative/routers/goal_achievement_evaluator.py",
    "src/omnicompany/packages/domains/narrative/routers/intent_compiler.py",
    # LLM-CALL-UNIFICATION T6 (2026-06-13): active publish pipeline worker.
    # Imported by workers/__init__.py and run.py, and declared in the local
    # publish_pipeline manifest; not an orphan candidate.
    "src/omnicompany/packages/services/_authoring/publish_pipeline/workers/article_author.py",
    # LLM-CALL-UNIFICATION T9 (2026-06-13): active routerized agent-loop
    # infrastructure after retiring runtime.agent.agent_node_loop and the
    # ToolDefinition registry. These files are imported by tests, run.py,
    # NativeIdeAgent, or LandmarkPicker wrappers and are not orphan candidates.
    "src/omnicompany/packages/services/_core/agent/configurable.py",
    "src/omnicompany/packages/services/_learning/absorption/landmark_picker.py",
    "src/omnicompany/packages/services/_learning/absorption/snapshot.py",
    "src/omnicompany/packages/services/_learning/absorption/tools.py",
    "src/omnicompany/packages/services/_learning/hypothesis/validator.py",
    "src/omnicompany/packages/services/_learning/trace_induction/tools.py",
    # LLM-CALL-UNIFICATION T11 (2026-06-13): active Guardian patrol
    # infrastructure touched while retiring the audit/tow placeholder. These
    # files are imported by guardian/__init__.py, workers/__init__.py, CLI, and
    # tests; they are not orphan candidates.
    "src/omnicompany/packages/services/_core/guardian/_patrol_shim.py",
    "src/omnicompany/packages/services/_core/guardian/workers/fs_scanner_worker.py",
    # LLM-CALL-UNIFICATION T13 (2026-06-13): active EventBus bridge and batch
    # tool entrypoint, not orphan candidates.
    "src/omnicompany/packages/services/_core/evolution/workflow/events.py",
    "src/omnicompany/packages/domains/demogame/ux/seven_tuple/runners/batch_runner.py",
)


def _matches_path(ctx: FileContext, paths: tuple[str, ...]) -> bool:
    p = ctx.path.replace("\\", "/")
    return any(p.startswith(ex) or p == ex.rstrip("/") for ex in paths)


def _is_path_exempt(ctx: FileContext) -> bool:
    return _matches_path(ctx, _PATH_EXEMPTIONS)


def _common_skip(ctx: FileContext) -> bool:
    """通用跳过条件: 外部代码 / 归档 / 路径豁免 / 非 .py."""
    if _is_external(ctx) or not _not_graveyard(ctx):
        return True
    if not ctx.path.endswith(".py"):
        return True
    if _is_path_exempt(ctx):
        return True
    if not ctx.content:
        return True
    return False


# ══════════════════════════════════════════════════════════════
# OMNI-070 · Router/Worker 内直调 LLMClient
# ══════════════════════════════════════════════════════════════

_LLMCLIENT_CALL_RE = re.compile(r"\bLLMClient\s*\(")


def _check_direct_llmclient_in_class(ctx: FileContext) -> bool:
    """OMNI-070 粗筛: 文件含 class 定义 + 类体内出现 LLMClient(...) 构造.

    粗筛保守 (宁多报候选, 让 LLM 筛). 不区分基类类型 — LLM 复核时判断
    是否合法继承链 (LLMCallRouter / AgentNodeLoop 内部用 OK).
    """
    if _common_skip(ctx):
        return False
    p = ctx.path.replace("\\", "/")
    # 只扫 packages/ 下文件 (其他路径可能有合法工具脚本)
    if "/packages/" not in p:
        return False
    content = ctx.content or ""
    if "LLMClient" not in content:
        return False
    try:
        tree = ast.parse(content)
    except SyntaxError:
        return False
    # 找到任何 class, 类体内含 LLMClient(...) 构造
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue
        # 把类体序列化回源码 segment 再扫 LLMClient(
        try:
            class_src = ast.get_source_segment(content, node) or ""
        except Exception:
            class_src = ""
        if _LLMCLIENT_CALL_RE.search(class_src):
            return True
    return False


# ══════════════════════════════════════════════════════════════
# OMNI-071 · 继承旧 runtime.agent.AgentNodeLoop
# ══════════════════════════════════════════════════════════════

_OLD_AGENT_LOOP_IMPORT_RE = re.compile(
    r"from\s+omnicompany\.runtime\.agent(?:\.[a-zA-Z_]+)?\s+import\s+[^;\n]*AgentNodeLoop",
)


def _check_old_agent_node_loop_inherit(ctx: FileContext) -> bool:
    """OMNI-071 粗筛: 文件 import 旧 AgentNodeLoop 且有 class 继承它.

    准确判定: 用 AST 查 ImportFrom + ClassDef 的 base.
    """
    if _common_skip(ctx):
        return False
    content = ctx.content or ""
    # 快速过滤
    if "AgentNodeLoop" not in content or "runtime.agent" not in content:
        return False
    try:
        tree = ast.parse(content)
    except SyntaxError:
        return False
    # 1. 检查 import 是否来自旧路径
    has_old_import = False
    imported_aliases: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.ImportFrom):
            continue
        if not node.module:
            continue
        if node.module.startswith("omnicompany.runtime.agent"):
            for alias in node.names:
                if alias.name == "AgentNodeLoop":
                    has_old_import = True
                    imported_aliases.add(alias.asname or alias.name)
    if not has_old_import:
        return False
    # 2. 检查是否有 class 继承 AgentNodeLoop (或别名)
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue
        for base in node.bases:
            base_name = ast.unparse(base) if hasattr(ast, "unparse") else ""
            for alias in imported_aliases:
                if alias in base_name:
                    return True
    return False


# ══════════════════════════════════════════════════════════════
# OMNI-072 · packages/ 下流程性工作不走 Worker 体系
# ══════════════════════════════════════════════════════════════

_WORKER_BASE_NAMES = (
    "Worker", "Router", "AgentNodeLoop", "LLMCallRouter",
    "SubTeamWorker", "SubPipelineRouter",
)


def _check_packages_flow_outside_worker(ctx: FileContext) -> bool:
    """OMNI-072 粗筛: packages/ 下 .py 含 LLMClient import 或 LLM 调用,
    但**没有**任何 class 继承标准 Worker 体系基类.

    LLM 复核要点: 是 helper / 工具 / 实际业务流程脚本?
    """
    if _common_skip(ctx):
        return False
    p = ctx.path.replace("\\", "/")
    if "/packages/" not in p:
        return False
    # __init__.py / formats.py / team.py 是结构文件, 跳过
    fname = p.rsplit("/", 1)[-1]
    if fname in ("__init__.py", "formats.py", "team.py", "materials.py"):
        return False
    if _matches_path(ctx, _FLOW_OUTSIDE_WORKER_EXEMPTIONS):
        return False
    content = ctx.content or ""
    # 必须含 LLM 调用迹象
    if "LLMClient" not in content:
        return False
    try:
        tree = ast.parse(content)
    except SyntaxError:
        return False
    # 判定: 文件里是否有 class 继承标准基类
    has_standard_class = False
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue
        for base in node.bases:
            base_src = ast.unparse(base) if hasattr(ast, "unparse") else ""
            for w in _WORKER_BASE_NAMES:
                if w in base_src:
                    has_standard_class = True
                    break
            if has_standard_class:
                break
        if has_standard_class:
            break
    if has_standard_class:
        return False  # 文件里有标准 class, OK (LLMClient 用法属类内部 → 由 OMNI-070 管)
    # 无标准 class 但用了 LLMClient → 候选
    return True


# ══════════════════════════════════════════════════════════════
# OMNI-073 · scripts/*.py 跑业务逻辑
# ══════════════════════════════════════════════════════════════


def _check_scripts_business_logic(ctx: FileContext) -> bool:
    """OMNI-073 粗筛: scripts/ 下任何 .py 都是候选 (除明显 shim / CLI 入口).

    2026-04-24 收紧 (plan §十二): scripts/ 下**默认都送 LLM 复核**,
    让 LLM 按"合法三条件" 判是否合法持续存在.
    """
    p = ctx.path.replace("\\", "/")
    if not p.startswith("scripts/"):
        return False
    if not p.endswith(".py"):
        return False
    if not ctx.content:
        return False
    # 明显 CLI entry / install 类文件自己申报 (文件头含标记) 可略过
    first_20_lines = "\n".join(ctx.content.splitlines()[:20])
    if "OMNI-PERSISTENT-SCRIPT" in first_20_lines:  # 显式声明长期存在
        return False
    return True


# ══════════════════════════════════════════════════════════════
# OMNI-074 · 死代码 / 孤儿模块
# ══════════════════════════════════════════════════════════════

def _check_orphan_module(ctx: FileContext) -> bool:
    """OMNI-074 粗筛: packages/ 下 .py 非协议文件, 粗筛出所有候选交 LLM.

    2026-04-24 plan §十二 '合法三条件' 落地:
    - 标准协议文件 (__init__.py / formats.py / team.py / materials.py / DESIGN.md) → 跳过
    - 已豁免位置 (vendors/_archive/_graveyard) → 跳过
    - 其余送 LLM 判: 有活跃依赖链 (被 import) 或 manifest 声明长期价值? 否则孤儿.

    粗筛只判"是否值得送 LLM"; LLM 做真正的 import 图追踪 + manifest 查询.
    """
    if _common_skip(ctx):
        return False
    p = ctx.path.replace("\\", "/")
    if "/packages/" not in p:
        return False
    fname = p.rsplit("/", 1)[-1]
    # 协议文件不是孤儿, 是架构槽位
    if fname in ("__init__.py", "formats.py", "team.py", "materials.py",
                 "pipeline.py", "run.py", "routers.py"):
        return False
    if _matches_path(ctx, _ORPHAN_MODULE_EXEMPTIONS):
        return False
    # 其余 .py 都送 LLM 复核
    return True


# ══════════════════════════════════════════════════════════════
# RULES
# ══════════════════════════════════════════════════════════════

RULES: list[GuardianRule] = [
    GuardianRule(
        id="OMNI-070",
        name="direct-llmclient-in-class",
        severity="MEDIUM",
        description=(
            "packages/ 下 class 内构造 LLMClient — 疑似绕过 LLMCallRouter / AgentNodeLoop 标准入口. "
            "LLM 复核: 该 class 是否继承自合法 async 基类 (AgentNodeLoop / LLMCallRouter / 子类)? "
            "若是则 dismissed (基类内部用), 否则 confirmed (业务 Worker 应引用 LLMCallRouter 作管线节点)."
        ),
        check=_check_direct_llmclient_in_class,
        disposition=["warn"],
        message_template=(
            "{path}: class 内构造 LLMClient. 若非 AgentNodeLoop / LLMCallRouter 子类基础设施, "
            "请改用 LLMCallRouter 作 Worker 节点挂管线 (单次调用) 或继承 AgentNodeLoop (多轮)."
        ),
        certainty="needs_judgment",
    ),
    GuardianRule(
        id="OMNI-071",
        name="legacy-agent-node-loop-inherit",
        severity="HIGH",
        description=(
            "继承旧 runtime.agent.AgentNodeLoop. 应迁到新 packages.services.agent.AgentNodeLoop "
            "(详见 docs/plans/[2026-04-18]AGENT-NODE-LOOP-ROUTERIZATION/plan.md)."
        ),
        check=_check_old_agent_node_loop_inherit,
        disposition=["warn"],
        message_template=(
            "{path}: 继承旧 runtime.agent.AgentNodeLoop. 迁到 packages.services.agent.AgentNodeLoop "
            "(已知豁免: guardian/judge_agent · llm_judge_agent · routers.HealthReporterRouter)."
        ),
        certainty="needs_judgment",
    ),
    GuardianRule(
        id="OMNI-072",
        name="packages-flow-outside-worker",
        severity="MEDIUM",
        description=(
            "packages/ 下 .py 文件用 LLMClient 但无 class 继承 Worker / Router / AgentNodeLoop 体系. "
            "LLM 复核: 是合法 helper / 工具函数, 还是绕体系跑业务流程?"
        ),
        check=_check_packages_flow_outside_worker,
        disposition=["warn"],
        message_template=(
            "{path}: 模块用 LLMClient 但不继承标准 Worker 体系. "
            "若是工具函数 (无状态/单一职责) → dismissed. 若跑流程 → 应封装为 Worker 挂 Team."
        ),
        certainty="needs_judgment",
    ),
    GuardianRule(
        id="OMNI-073",
        name="scripts-persistent-legitimacy",
        severity="MEDIUM",
        description=(
            "scripts/ 下 .py 持续存在必须满足合法三条件之一: 活跃依赖链 / "
            "声明长期价值 / 标准协议槽位. 一次性工具/probe/实验脚本堆积 = 违规. "
            "LLM 按 plan §十二 判定."
        ),
        check=_check_scripts_business_logic,
        disposition=["warn"],
        message_template=(
            "{path}: scripts/ 下 .py 存在合法性待判. 若自陈一次性/probe/实验 → "
            "归档到 _archive/scripts/. 若长期有用 → 在脚本头加 `# OMNI-PERSISTENT-SCRIPT` 标记 + owner/purpose."
        ),
        certainty="needs_judgment",
    ),
    GuardianRule(
        id="OMNI-074",
        name="orphan-module",
        severity="LOW",
        description=(
            "packages/ 下 .py (非标准协议文件) 若无活跃依赖链 + 无 manifest 声明 → 孤儿/死代码. "
            "LLM 读 grep 结果判: 被谁 import / 是否在 manifest 声明长期价值."
        ),
        check=_check_orphan_module,
        disposition=["warn"],
        message_template=(
            "{path}: packages/ 下模块合法性待判. 若无 import 引用且无 manifest 声明 → "
            "归档到 _archive/ 或迁到合适位置, 或在 .omni/manifest.yaml 声明长期价值."
        ),
        certainty="needs_judgment",
    ),
]


__all__ = [
    "RULES",
    "_PATH_EXEMPTIONS",
    "_FLOW_OUTSIDE_WORKER_EXEMPTIONS",
    "_ORPHAN_MODULE_EXEMPTIONS",
    "_WORKER_BASE_NAMES",
    "_STAGED_EXEMPTIONS_EXPIRES",
    "_check_direct_llmclient_in_class",
    "_check_old_agent_node_loop_inherit",
    "_check_packages_flow_outside_worker",
    "_check_scripts_business_logic",
    "_check_orphan_module",
]
