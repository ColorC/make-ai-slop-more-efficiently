# [OMNI] origin=omnicompany domain=omnicompany/guardian ts=2026-04-10T00:00:00Z
# [OMNI] material_id="material:core.guardian.framework_boundary.enforcer.py"
"""Guardian 规则 — 框架层边界 (OMNI-002/003/004/006/013)。

OMNI-002: 业务代码散落在 runtime/ 框架层根目录
OMNI-003: 直接 import anthropic/openai 绕过 LLMClient
OMNI-004: Router.run() 定义为 async（违反 LAP 同步协议）
OMNI-006: src/ 下出现临时脚本
OMNI-013: 核心代码/Router 代码中出现裸 write，绕过 guarded_write
"""
from __future__ import annotations

import re
from pathlib import Path

from ._base import FileContext, GuardianRule, _is_python, _has_content, _is_external


# ─── OMNI-002: 业务代码散落在 runtime/ 框架层根目录 ─────────────

# 合法的 runtime 文件集（框架核心，不报告）
_LEGAL_RUNTIME_FILES = frozenset({
    # 框架核心
    "runner.py", "router.py", "llm.py", "session.py",
    "agent_loop.py", "agent_node_loop.py", "agent_loop_tools.py",
    "agent_loop_config.py", "agent_loop_compact.py", "agent_loop_permissions.py",
    "agent_intent_router.py", "agent_constants.py",
    "hooks.py", "db_access.py", "tool_executor.py", "domain_loader.py",
    "tools.py", "nodes.py", "signal.py", "stuck.py",
    # 深耦合框架组件（受控技术债，Phase 3 迁移）
    "reward.py", "intent_tracer.py", "graph_builder.py", "pain_system.py",
    "mirror_node.py", "boltzmann_router.py", "route_retriever.py",
    # 辅助框架组件
    "bootstrap.py", "embedding_client.py", "self_types.py",
    "soft_node_executor.py", "tool_pattern_registry.py",
    "reasoning_trace.py", "type_bridge.py", "type_discovery.py",
    # V1.3 框架扩展（Router 子类基类 + 基础设施）
    "knowledge.py", "sub_pipeline.py",
    "compression_summary.py", "experience_search.py",
    "__init__.py",
})

_RUNTIME_ROOT_RE = re.compile(r"^src/omnicompany/runtime/[^/]+\.py$")


def _check_business_in_framework(ctx: FileContext) -> bool:
    if not _is_python(ctx):
        return False
    if not _RUNTIME_ROOT_RE.match(ctx.path):
        return False
    filename = Path(ctx.path).name
    return filename not in _LEGAL_RUNTIME_FILES


# ─── OMNI-003: 直接 import anthropic/openai 绕过 LLMClient ──────

def _check_direct_llm_import(ctx: FileContext) -> bool:
    if not _is_python(ctx) or not _has_content(ctx):
        return False
    if "runtime/llm" in ctx.path or "/tests/" in ctx.path:
        return False
    if _is_external(ctx):
        return False
    # 豁免 guardian 目录（patrol.py / evolve_signal.py / judge_agent.py 含检测字符串作为字面量）
    p = ctx.path.replace("\\", "/")
    if "/packages/services/guardian/" in p or p.startswith("packages/services/guardian/") or p.startswith("src/omnicompany/packages/services/guardian/"):
        return False
    c = ctx.content
    # 只检查实际的 import 语句，跳过注释行和字符串内容
    import_lines = [
        ln for ln in c.splitlines()
        if not ln.strip().startswith("#") and not ln.strip().startswith('"""') and not ln.strip().startswith("'")
    ]
    joined = "\n".join(import_lines)
    return bool(re.search(r"^\s*(?:import|from)\s+(?:anthropic|openai)(?:\b|\.)", joined, re.MULTILINE))


# ─── OMNI-004: Router.run() 定义为 async ──────────────────────────

_ASYNC_RUN_RE = re.compile(r"async\s+def\s+run\s*\(self")
_ROUTER_CLASS_RE = re.compile(r"class\s+\w+\(.*Router.*\)")
# 合法 async 场景: 继承自 SubTeamWorker 的子类 (基类 run 本身就是 async,
# 因为必须 await dispatch() 调子管线). 此场景不该报 OMNI-004.
_SUB_PIPELINE_INHERITANCE_RE = re.compile(
    r"class\s+\w+\([^)]*\b(SubTeamWorker|SubPipelineRouter)\b",
)


def _check_async_router_run(ctx: FileContext) -> bool:
    if not _is_python(ctx) or not _has_content(ctx):
        return False
    if _is_external(ctx):
        return False
    # 豁免框架层自身（runtime/ 里的 Router 基类定义合法有 async）
    if "src/omnicompany/runtime/" in ctx.path:
        return False
    c = ctx.content
    # 只有包含 Router 子类的文件才检查
    if not _ROUTER_CLASS_RE.search(c):
        return False
    # 找到所有 async def run(self...) 的方法
    async_runs = _ASYNC_RUN_RE.findall(c)
    if not async_runs:
        return False
    # SubTeamWorker 子类豁免 (基类必然 async, 子类覆盖也必须 async)
    if _SUB_PIPELINE_INHERITANCE_RE.search(c):
        return False
    # 所有 async def run() 均报告——无论体内是否有 await。
    # async run() 导致 Doctor AST 信号提取失效，违反 LAP 同步协议。
    return True


# ─── OMNI-006: src/ 下出现临时脚本 ──────────────────────────────

_TEMP_NAME_RE = re.compile(
    r"^(test_|scratch_|tmp_|debug_|check_|restore_|update_)\w*\.py$"
)


def _check_temp_in_src(ctx: FileContext) -> bool:
    if not _is_python(ctx):
        return False
    if not ctx.path.startswith("src/"):
        return False
    if "/tests/" in ctx.path or _is_external(ctx):
        return False
    filename = Path(ctx.path).name
    return bool(_TEMP_NAME_RE.match(filename))


# ─── OMNI-013: packages 业务代码禁止裸 write ─────────────────────

_GUARDED_WRITE_ENFORCE_ROOTS = (
    "src/omnicompany/packages/",  # 只检查 packages 业务层
)

# 这些文件是底层实现或自洽入口，不走 guarded_write
_GUARDED_WRITE_EXEMPT_FILES = (
    "src/omnicompany/core/guarded_write.py",   # 唯一裸写入口
    "src/omnicompany/core/omnimark.py",        # stamp_file 会递归调自己
    "src/omnicompany/bus/sqlite_bus.py",        # SQLite 底层
    "src/omnicompany/bus/memory_bus.py",        # 内存 bus
)

# 匹配典型的裸文件写入 / 删除 / 改名调用。不求完备，抓常见大头。
_RAW_WRITE_RE = re.compile(
    r"""(
        \.write_text\(
        | \.write_bytes\(
        | open\([^)]*['"]w[b]?['"]
        | shutil\.(copy|copy2|copyfile|copytree|move)\(
        | os\.(write|replace|rename|remove|unlink|rmdir|removedirs)\(
    )""",
    re.VERBOSE,
)


def _check_direct_raw_write(ctx: FileContext) -> bool:
    """OMNI-013: 业务入口 + LLM agent 工具入口禁止裸 write，必须走 guarded_write。"""
    if not _is_python(ctx):
        return False
    if ctx.content is None:
        return False
    p = ctx.path.replace("\\", "/")

    # 两大 in-scope 区域
    in_packages_entry = (
        p.startswith("src/omnicompany/packages/")
        and (p.endswith("/routers.py") or p.endswith("/pipeline.py") or p.endswith("/run.py"))
    )
    in_runtime_exec = p.startswith("src/omnicompany/runtime/exec/")
    if not (in_packages_entry or in_runtime_exec):
        return False
    if p in _GUARDED_WRITE_EXEMPT_FILES:
        return False
    if _is_external(ctx):
        return False
    if "/tests/" in p or "/_graveyard/" in p:
        return False

    # 找所有 raw write 行
    lines = ctx.content.splitlines()
    unallowed_count = 0
    for idx, line in enumerate(lines):
        if not _RAW_WRITE_RE.search(line):
            continue
        # 检查同行是否有 ALLOW marker
        if "OMNI-013 ALLOW" in line:
            continue
        # 检查前一行是否有 ALLOW marker
        if idx > 0 and "OMNI-013 ALLOW" in lines[idx - 1]:
            continue
        unallowed_count += 1

    return unallowed_count > 0


RULES: list[GuardianRule] = [
    GuardianRule(
        id="OMNI-002",
        name="business-in-framework",
        severity="CRITICAL",
        description="业务代码散落在 runtime/ 框架层根目录",
        check=_check_business_in_framework,
        disposition=["warn", "evolve-signal"],
        message_template="{path} 看起来是业务代码但出现在 runtime/ 框架层。请移至 src/omnicompany/packages/<namespace>/<domain>/。",
        certainty="needs_judgment",
    ),
    GuardianRule(
        id="OMNI-003",
        name="bypass-llm-client",
        severity="CRITICAL",
        description="直接 import anthropic/openai，绕过 LLMClient 统一接口",
        check=_check_direct_llm_import,
        disposition=["warn", "evolve-signal"],
        message_template="{path} 直接使用 LLM SDK。所有 LLM 调用必须通过 omnicompany.runtime.llm.llm.LLMClient。",
        certainty="needs_judgment",
    ),
    GuardianRule(
        id="OMNI-004",
        name="async-router-run",
        severity="HIGH",
        description=(
            "Router.run() 定义为 async — 疑似违反 LAP 同步协议. 但合法例外: "
            "继承自 async 基类 (AgentNodeLoop / LLMCallRouter / SubTeamWorker 等) 的子类 "
            "必须覆盖为 async. GuardianAgent 读代码判是否合法继承链."
        ),
        check=_check_async_router_run,
        disposition=["warn"],
        message_template=(
            "{path} 中 Router.run() 定义为 async. 若非继承 async 基类, "
            "Router.run() 必须同步 (TeamRunner 通过 asyncio.to_thread 包装). "
            "async run() 同时导致 Doctor AST 信号提取失效."
        ),
        certainty="needs_judgment",  # 2026-04-24 降级: 实扫 30 命中, 大部分是合法 AgentNodeLoop/LLMCallRouter 子类
    ),
    GuardianRule(
        id="OMNI-006",
        name="temp-script-in-src",
        severity="MEDIUM",
        description="临时脚本（test_* / scratch_* / tmp_* / debug_*）出现在 src/ 目录",
        check=_check_temp_in_src,
        disposition=["warn", "quarantine"],
        message_template="{path} 看起来是临时脚本但在 src/ 下。请移至 scripts/ 或 tests/ 目录。",
        certainty="needs_judgment",
    ),
    GuardianRule(
        id="OMNI-013",
        name="direct-raw-write",
        severity="HIGH",
        description="核心代码 / Router 代码中出现裸 Path.write_text / open(..,'w')，绕过 guarded_write",
        check=_check_direct_raw_write,
        disposition=["warn", "evolve-signal"],
        message_template="{path} 出现裸文件写入。请改用 `from omnicompany.core.guarded_write import write_file`，它自动挂 OmniShield 审计 + OmniMark 贴头。",
        certainty="needs_judgment",
    ),
]
