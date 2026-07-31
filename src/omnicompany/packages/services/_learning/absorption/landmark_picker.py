# [OMNI] origin=claude-code domain=services/absorption/landmark_picker.py ts=2026-04-08T12:00:00Z
# OMNI-024 ALLOW: LandmarkPickerRouter is a session-bound migration wrapper kept with absorption tools.
# [OMNI] migrated 2026-05-03: 旧 omnicompany.runtime.agent.agent_node_loop.AgentNodeLoop 已 deprecate, 现用 packages.services._core.agent.AgentNodeLoop (router 化新基础设施). 7 个原闭包工具 (gh_tree_list/gh_file_read/omni_capabilities/submit_landmark/submit_landscape_sketch/submit_capability_gap + ThinkTool) → 6 个 SingleToolRouter wrapper 委托给原 tools.make_tools_for_session 闭包 (think drop). sess_id 通过 build_tool_context 注入, 工具 wrappers 从 ctx 读.
# [OMNI] material_id="material:learning.absorption.agent_node_loop.landmark_picker.py"
"""absorption.landmark_picker — LandmarkPicker as AgentNodeLoop (Stage 3d L3 升级).

原 Stage 3 版本是单次 LLMRouter, 问题是:
  - LLM 只看 4KB README + 顶层目录, 从没读过实际源码
  - "landmarks" 只是 LLM 基于目录名的猜测
  - "capability gaps" 从未对照 OmniCompany 真实状态
  - "evidence" 字段引用的是语言占比而非代码
  - 全部单次调用, LLM 想进一步探索也没工具

本 Stage 3d 版本解决上述所有问题:
  - 继承 AgentNodeLoop, 走多轮 tool use 循环 (最多 80 turns)
  - 提供 gh_tree_list / gh_file_read 真实抓代码
  - 提供 omni_capabilities 查本仓对照集
  - 提交工具硬强制 file_path 必须是读过的文件 (否则工具返回 Error)
  - 所有提交项带 confidence + confidence_reason (L7 诚实)

输入: state 中必须含 facade_cards + omni_snapshot (来自上游 2 节点)
输出: state + landmarks + landscape_sketches + capability_gaps +
       picker_read_files + picker_listed_paths + picker_finish_summary
"""

from __future__ import annotations

import logging
import threading
from typing import Any, ClassVar

from omnicompany.protocol.anchor import Verdict, VerdictKind
from omnicompany.runtime.agent.agent_loop_config import (
    CompactConfig,
    LoopConfig,
    PermissionConfig,
)

# 2026-05-03 迁: 旧 runtime.agent.agent_node_loop deprecate. 改用 router 化基础设施.
from omnicompany.packages.services._core.agent import AgentNodeLoop
from omnicompany.packages.services._core.agent.routers.prompt_builder import PromptBuilderRouter
from omnicompany.packages.services._core.agent.routers.extract_result import ExtractResultRouter
from omnicompany.packages.services._core.agent.routers.single_tool import (
    SingleToolRouter,
    ToolContext,
    ToolExecutionError,
)

from omnicompany.packages.services._learning.absorption.tools import (
    get_session,
    make_tools_for_session,
    new_session,
    pop_session,
)

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════
# 系统提示 — 硬强制证据与对照
# ═══════════════════════════════════════════════════════════

_NODE_PROMPT = """你是 OmniCompany Repo Absorption 管线的 LandmarkPicker, 运行在 Phase A (Survey) 阶段。

你的任务: 基于给定 GitHub 仓库的门面卡片 + OmniCompany 自身能力快照, 迭代地
**深入阅读** 外部仓库的关键代码, 产出可被人逐条复查的 landmarks / landscape
sketches / capability gaps。

**核心准则: 深度 > 广度, 证据 > 感觉, 事实 > 标签。**

## 你有的工具

探索:
  - gh_tree_list(owner, name, path)            列指定路径的子项 (非递归)
  - gh_file_read(owner, name, path, offset, max_lines)  读文件 (带行号)
  - omni_capabilities(category, filter)        查 OmniCompany 本仓的现有能力

思考:
  - think(thought)   纯思考, 无副作用

提交 (按 schema 严格填写):
  - submit_landmark          tier-1/2/3 候选, 每个必须有深读过的 evidence
  - submit_landscape_sketch  每个 repo 一次, 必须列出 files_relied_on
  - submit_capability_gap    每个真实对照出的 gap, 必须有 ≥3 次 omni_capabilities 查询

终止:
  - finish(message)   调用后循环终止, message 进入 picker_finish_summary

## 绝对规则 (违反即为不合格)

1. **禁止**: 不读文件就猜测 landmark 内容。基于目录名/文件名下结论 = 未完成。
2. **禁止**: 使用置信度标签 (high/medium/low/HIGH/MEDIUM/LOW)、分数、星级、
   "有信心" / "非常确定" 等主观自评。把不确定性**直接写进 prose**:
   - 坏: "confidence: medium"
   - 好: "我读了 manager.rs 前 150 行看到 SandboxManager 的 struct 定义, 但未读
     具体 bwrap 调用的实现细节, 所以只能断言接口形态, 不能断言实现正确性"
3. **禁止**: submit_capability_gap 前 omni_capabilities 调用少于 3 次, 且 3 次
   必须覆盖 packages / routers / builtin_tools 或 core_modules —— 只查 core_modules
   会漏掉 packages/vendors/ 下的已有能力 (这个坑前一跑真实发生过)。
4. **必须**: 每个 submit_landmark 的 evidence.file_path 必须是你实际 gh_file_read
   过的文件, 否则工具层会拒绝。
5. **必须**: 每个 submit_landscape_sketch 的 files_relied_on 至少含 1 个文件。
6. **必须**: 诚实地让 finish summary 反映真实情况, 不要为了好看而夸大发现或
   隐瞒自己没读的部分。

## 深度标准 (硬性)

### 关键文件的深度读取

对于你打算提交为 **tier-1** landmark 的 evidence 文件:

- **如果文件 ≤ 400 行**: 必须**完整读完** (offset=0, max_lines=1200 默认值足够)。
  只读文件的一部分就下 tier-1 结论 = 不合格。
- **如果文件 > 400 行**: 至少读取**该文件的一半** 或 **600 行**, 取较大值。
  可以用多次 gh_file_read 调用配合 offset 参数分段读:
  - 第一次: offset=0, max_lines=1200   (读前 1200 行)
  - 第二次: offset=1200, max_lines=1200 (读 1201-2400)
  以此类推。**禁止**: 只看前 200 行然后写 tier-1。
- **如果文件 > 2000 行且是核心文件**: 至少读开头 1200 行 + 中段 600 行 + 尾部 400 行,
  分 3 次 gh_file_read, 覆盖 struct 定义 + 主流程 + 收尾/错误处理。
- 重复调用同一个文件但不同 offset 的 gh_file_read 会**累加**到 read_files 的覆盖率,
  不会被看作重复 —— 大方使用 offset 深读。

### tier-2 / tier-3 的证据门槛

- **tier-2**: 至少 gh_tree_list 过其所在目录, 并至少深读 1 个代表性文件;
- **tier-3**: 至少知道其位置和大致用途 (能指向目录即可), 可以不深读。

## 广度标准 (硬性)

对中型仓 (500~5000 源文件):
- 至少 **25~40 个文件** 实际读取 (不算 README / LICENSE / 短配置)
- 至少 **列出所有源文件数 > 20 的顶层目录** (gh_tree_list)
- 不允许窄聚焦 (只盯一个目录出所有结论)

对超大仓 (>5000 源文件):
- 至少 **50~80 个文件** 实际读取
- 优先读: 主入口 → 核心 lib/mod → 关键算法/runtime 模块 → 配置/protocol 定义
- 最后写一段 "为什么我认为这些文件是最重要的" 进 finish summary

## 工作流程建议 (不是死规则, 但大体照做)

1. **Plan**: 看完 user message 里的 facade card (已含 README 全文 + tree_recursive +
   顶层目录), 用 think 列出你打算读的文件清单和对照查询计划。

2. **查 OmniCompany 现状 (至少 3 次 omni_capabilities)**, 覆盖不同 category:
   - 查 packages (带 filter 关键词)
   - 查 routers (带 filter 关键词)
   - 查 builtin_tools 或 core_modules
   **先查再读**, 避免后续 gap 错报。

3. **按顶层目录覆盖 tree_list**: 对每个源文件 > 20 的顶层目录至少 gh_tree_list 一次。
   这一步保证广度, 避免漏掉整个子系统。

4. **识别核心文件 + 深读**: 从 tree_list 结果挑 25~40 个文件, 按重要性排序:
   - 主入口 (main.rs / lib.rs / index.ts / __init__.py)
   - 核心抽象 (core/*.rs, runtime/*.py, agent/*, sandbox/*, exec/*)
   - 协议/schema 定义 (protocol.*, types.*)
   - 配置 (Cargo.toml / package.json / pyproject.toml)
   - 关键 README / docs / ARCHITECTURE.md
   **按深度标准读它们**, 不要只读顶部。

5. **边读边 think**: 每读完一个关键文件, 用 think 整理你看到了什么 struct/trait/
   class, 它们的关系, 它们与 OmniCompany 快照中查到的条目有什么结构差异。

6. **出 landmarks**: 每个 submit_landmark 的 `why_interesting` 用散文写清楚你
   读到了什么具体内容, 哪些设计决策值得 OmniCompany 学习, 哪些你不确定。

7. **出 landscape sketch (每 repo 一份)**: positioning 用 2-4 句散文描述项目
   定位和架构取向; core_abstractions 每项含 name + what_it_does (散文) +
   evidence_file; diff_vs_omnicompany 用散文对照命名具体 Omni 条目。

8. **出 gaps**: 每个 gap 的 `omnicompany_current_state` 引用你查到的具体包/类/
   模块名或明确 "no match found for queries: X, Y, Z"。**前面用完了 3 次
   omni_capabilities 不同 category 的查询再来这一步**。

9. **finish**: 当读到的文件数达到深度/广度标准且你自己觉得"再读下去边际收益低了"
   时, 调 finish(message="..."), message 是对本次探索发现的散文总结 + 自述局限
   (没读的重要目录、没深挖的点)。

## 预算

- max turns: **80** (大幅增加, 鼓励深读)
- 70% 用后会收到预算警告
- 深读大文件比读多个小文件更有价值
- think 不算预算消耗, 多用

开始你的深度探索。"""


# ═══════════════════════════════════════════════════════════
# 7 个 LegacyTool wrappers — 委托给 tools.make_tools_for_session 闭包
# ═══════════════════════════════════════════════════════════

# session-id → 已构造的 ToolDefinition 列表 (避免重复 make_tools_for_session 调用)
_LEGACY_TOOL_CACHE: dict[str, dict[str, Any]] = {}
_LEGACY_TOOL_CACHE_LOCK = threading.Lock()


def _get_legacy_tools_map(sess_id: str) -> dict[str, Any]:
    """返回 sess_id 对应的 {tool_name: ToolDefinition} 映射, 缓存避免重建."""
    with _LEGACY_TOOL_CACHE_LOCK:
        cached = _LEGACY_TOOL_CACHE.get(sess_id)
        if cached is not None:
            return cached
        tools = make_tools_for_session(sess_id)
        cached = {t.name: t for t in tools}
        _LEGACY_TOOL_CACHE[sess_id] = cached
        return cached


def _drop_legacy_tools(sess_id: str) -> None:
    with _LEGACY_TOOL_CACHE_LOCK:
        _LEGACY_TOOL_CACHE.pop(sess_id, None)


class _LegacyToolWrapperBase(SingleToolRouter):
    """SingleToolRouter base — delegates to tools.py 闭包工具.

    子类只需声明 TOOL_NAME / DESCRIPTION / INPUT_SCHEMA / IS_*. _execute 通用.
    sess_id 从 ToolContext 读 (build_tool_context 注入).
    """

    IS_CONCURRENCY_SAFE: ClassVar[bool] = True
    IS_READONLY: ClassVar[bool] = True

    def _execute(self, args: dict, ctx: ToolContext) -> str:
        sess_id = getattr(ctx, "absorption_sess_id", None)
        if not sess_id:
            raise ToolExecutionError(
                f"{self.TOOL_NAME}: absorption_sess_id missing from tool context "
                "(LandmarkPickerRouter.build_tool_context must inject it)"
            )
        legacy_map = _get_legacy_tools_map(sess_id)
        tool = legacy_map.get(self.TOOL_NAME)
        if tool is None:
            raise ToolExecutionError(
                f"{self.TOOL_NAME}: legacy tool not found in session {sess_id} "
                f"(available: {list(legacy_map.keys())})"
            )
        # legacy ToolDefinition.call(args, executor, ctx) — 我们 ctx 是新 SimpleNamespace,
        # legacy 闭包只读 sess_id (从外层 closure 已捕获), 不真用 ctx, executor=None 安全.
        return tool.call(args, None, ctx)  # type: ignore[arg-type]


class _GhTreeListRouter(_LegacyToolWrapperBase):
    TOOL_NAME: ClassVar[str] = "gh_tree_list"
    DESCRIPTION: ClassVar[str] = (
        "List files/directories at a specific path inside a GitHub repository (non-recursive). "
        "Returns a JSON array of {name, type, path, size}. Use this to explore a specific "
        "sub-directory you're interested in. For repo root, use path=\"\". For a subdir, "
        "use path=\"codex-rs/core\" etc. The recursive whole-tree listing is already in "
        "your context under facade_card.tree_recursive — only call this tool if you need "
        "fresh or deeper data than the pre-fetched tree."
    )
    INPUT_SCHEMA: ClassVar[dict] = {
        "type": "object",
        "properties": {
            "owner": {"type": "string", "description": "Repo owner, e.g. 'openai'"},
            "name": {"type": "string", "description": "Repo name, e.g. 'codex'"},
            "path": {
                "type": "string",
                "description": "Path within repo, empty string for root",
                "default": "",
            },
        },
        "required": ["owner", "name"],
    }


class _GhFileReadRouter(_LegacyToolWrapperBase):
    TOOL_NAME: ClassVar[str] = "gh_file_read"
    DESCRIPTION: ClassVar[str] = (
        "Read a file from a GitHub repository with optional offset/limit. Returns the file "
        "content with line numbers. Use offset to start at a later line; max_lines caps the "
        "returned chunk (default 1200). Repeated reads of the same file with different offsets "
        "accumulate in the coverage audit — drill freely."
    )
    INPUT_SCHEMA: ClassVar[dict] = {
        "type": "object",
        "properties": {
            "owner": {"type": "string"},
            "name": {"type": "string"},
            "path": {"type": "string", "description": "File path within the repo"},
            "offset": {"type": "integer", "description": "Start line (0-based)", "default": 0},
            "max_lines": {"type": "integer", "description": "Max lines to return", "default": 1200},
        },
        "required": ["owner", "name", "path"],
    }


class _OmniCapabilitiesRouter(_LegacyToolWrapperBase):
    TOOL_NAME: ClassVar[str] = "omni_capabilities"
    DESCRIPTION: ClassVar[str] = (
        "Query OmniCompany's own capability snapshot to verify what we already have. "
        "category ∈ {packages, registered_pipelines, routers, builtin_tools, core_modules}. "
        "filter is a substring match (case-insensitive). MUST be called ≥3 times across "
        "different categories before submit_capability_gap, otherwise gap submission is rejected."
    )
    INPUT_SCHEMA: ClassVar[dict] = {
        "type": "object",
        "properties": {
            "category": {
                "type": "string",
                "enum": ["packages", "registered_pipelines", "routers", "builtin_tools", "core_modules"],
            },
            "filter": {"type": "string", "description": "Substring to filter by (case-insensitive)"},
        },
        "required": ["category"],
    }


class _SubmitLandmarkRouter(_LegacyToolWrapperBase):
    TOOL_NAME: ClassVar[str] = "submit_landmark"
    DESCRIPTION: ClassVar[str] = (
        "Submit a tier-1/2/3 landmark candidate. evidence.file_path MUST be a file you "
        "actually gh_file_read'd, otherwise the tool returns Error. tier-1 requires substantial "
        "depth read of the evidence file (full read for ≤400 lines, half-or-600 for longer)."
    )
    INPUT_SCHEMA: ClassVar[dict] = {
        "type": "object",
        "properties": {
            "tier": {"type": "integer", "enum": [1, 2, 3]},
            "name": {"type": "string"},
            "owner": {"type": "string"},
            "repo_name": {"type": "string"},
            "why_interesting": {"type": "string", "description": "Prose: what specifically you read + why it's worth noting"},
            "evidence": {
                "type": "object",
                "properties": {
                    "file_path": {"type": "string", "description": "Must be a file you've read"},
                    "lines": {"type": "string"},
                    "excerpt": {"type": "string"},
                },
                "required": ["file_path"],
            },
        },
        "required": ["tier", "name", "owner", "repo_name", "why_interesting", "evidence"],
    }
    IS_READONLY: ClassVar[bool] = False  # mutates session state


class _SubmitLandscapeSketchRouter(_LegacyToolWrapperBase):
    TOOL_NAME: ClassVar[str] = "submit_landscape_sketch"
    DESCRIPTION: ClassVar[str] = (
        "Submit one landscape sketch per target repo. Must include positioning prose, "
        "core_abstractions list (each with evidence_file), diff_vs_omnicompany, and "
        "files_relied_on (≥1 file). Call once per repo after substantial reading."
    )
    INPUT_SCHEMA: ClassVar[dict] = {
        "type": "object",
        "properties": {
            "owner": {"type": "string"},
            "repo_name": {"type": "string"},
            "positioning": {"type": "string"},
            "core_abstractions": {"type": "array", "items": {"type": "object"}},
            "diff_vs_omnicompany": {"type": "string"},
            "files_relied_on": {"type": "array", "items": {"type": "string"}, "minItems": 1},
        },
        "required": ["owner", "repo_name", "positioning", "files_relied_on"],
    }
    IS_READONLY: ClassVar[bool] = False


class _SubmitCapabilityGapRouter(_LegacyToolWrapperBase):
    TOOL_NAME: ClassVar[str] = "submit_capability_gap"
    DESCRIPTION: ClassVar[str] = (
        "Submit a capability gap (something OmniCompany lacks vs the target repo). "
        "REQUIRES ≥3 prior omni_capabilities queries across different categories before "
        "submission, otherwise rejected. omnicompany_current_state must reference specific "
        "package/class/module names or explicit 'no match found for queries: X, Y, Z'."
    )
    INPUT_SCHEMA: ClassVar[dict] = {
        "type": "object",
        "properties": {
            "title": {"type": "string"},
            "owner": {"type": "string"},
            "repo_name": {"type": "string"},
            "their_implementation": {"type": "string", "description": "What the target repo has + evidence file"},
            "omnicompany_current_state": {"type": "string", "description": "Explicit reference or no-match note"},
            "why_it_matters": {"type": "string"},
        },
        "required": ["title", "owner", "repo_name", "their_implementation", "omnicompany_current_state"],
    }
    IS_READONLY: ClassVar[bool] = False


# ═══════════════════════════════════════════════════════════
# PromptBuilder + ExtractResult subclasses
# ═══════════════════════════════════════════════════════════


# 全局 session 计数器, 用于唯一 sess_id
_SESS_COUNTER_LOCK = threading.Lock()
_SESS_COUNTER = 0


def _next_sess_id(router: Any) -> str:
    global _SESS_COUNTER
    with _SESS_COUNTER_LOCK:
        _SESS_COUNTER += 1
        return f"absorption-picker-{id(router)}-{_SESS_COUNTER}"


class _LandmarkPickerPromptBuilder(PromptBuilderRouter):
    """LandmarkPicker 自定义首条 user 消息 — 创建 session, 拼 facade summaries."""

    def __init__(self, agent_ref: "LandmarkPickerRouter", *, template: str, bus: Any | None = None):
        super().__init__(template=template, bus=bus)
        self._agent = agent_ref

    def build_initial_messages(self, input_data: dict) -> list[dict]:
        facade_cards = input_data.get("facade_cards") or []
        omni_snapshot = input_data.get("omni_snapshot") or {}

        if not facade_cards:
            raise ValueError("LandmarkPicker: input 缺少 facade_cards")
        if not omni_snapshot:
            raise ValueError("LandmarkPicker: input 缺少 omni_snapshot")

        # 新建会话状态 (保存 input_data 以便 extract_result 向下游透传 upstream keys)
        self._agent._sess_id = _next_sess_id(self._agent)
        new_session(self._agent._sess_id, facade_cards, omni_snapshot, upstream_input=dict(input_data))

        # 构造 user message: 精简 facade_cards 再塞给 LLM (避免 tree_recursive 膨胀)
        facade_summaries = []
        for c in facade_cards:
            tree = c.get("tree_recursive") or []
            top_buckets: dict[str, list[str]] = {"<root>": []}
            for t in tree:
                if t.get("type") != "blob":
                    continue
                path = t.get("path", "")
                if "/" not in path:
                    top_buckets["<root>"].append(path)
                else:
                    top = path.split("/", 1)[0]
                    top_buckets.setdefault(top, []).append(path)
            bucket_strs = []
            for k in sorted(top_buckets.keys()):
                files = top_buckets[k]
                bucket_strs.append(
                    f"  {k}/ ({len(files)} files): {files[:20]}"
                    + (f" ... +{len(files) - 20} more" if len(files) > 20 else "")
                )

            facade_summaries.append(
                f"""### {c['owner']}/{c['name']}

- URL: {c['url']}
- Description: {c.get('description', '(none)')}
- Stars: {c.get('stars', 0)} · Forks: {c.get('forks', 0)} · Open issues: {c.get('open_issues', 0)}
- License: {c.get('license')}
- Default branch: {c.get('default_branch')}
- Primary language: {c.get('primary_language')}
- Language stats: {c.get('language_stats')}
- Commit frequency: {c.get('commit_frequency')}
- File count: {c.get('file_count', 0)} blobs / {c.get('dir_count', 0)} trees
- Top contributors: {[cr.get('login') for cr in c.get('contributors', [])[:5]]}
- Recent releases: {[r.get('tag') for r in c.get('releases', [])[:3]]}

### README (full, {c.get('readme_size', 0)} bytes)

{c.get('readme_full') or ''}

### tree_recursive (grouped by top-level, up to 20 files shown per group)

{chr(10).join(bucket_strs)}
"""
            )

        profile = input_data.get("profile", "framework_absorption")

        snap_brief = (
            f"OmniCompany current capabilities snapshot:\n"
            f"- {len(omni_snapshot.get('packages', {}))} packages\n"
            f"- {len(omni_snapshot.get('registered_pipelines', []))} registered pipelines\n"
            f"- {len(omni_snapshot.get('routers', []))} Router classes\n"
            f"- {len(omni_snapshot.get('builtin_tools', []))} built-in tools\n"
            f"- {len(omni_snapshot.get('core_modules', []))} core/runtime modules\n"
            f"Use `omni_capabilities` tool to query specific categories for details."
        )

        user_content = f"""# Absorption Run

## Profile
**{profile}**

## Target repositories
{chr(10).join(facade_summaries)}

---

## OmniCompany self-snapshot (brief)
{snap_brief}

---

## Your task

Iteratively **deep-read** the repo(s) above using gh_tree_list and gh_file_read.
Query omni_capabilities (≥3 different categories) to verify OmniCompany's
current state before submitting any capability gap.

### Reading depth target per repo

- **Breadth**: list every top-level directory that has more than 20 source
  files, so you don't miss whole subsystems.
- **Depth**: read 25-40 source files for medium repos, 50-80 for huge repos
  (>5000 files). For every tier-1 landmark you plan to submit, read the
  evidence file substantially — full read for files ≤400 lines, at least
  half the file (or 600 lines, whichever is larger) for longer files.
- Use the `offset` parameter on gh_file_read to read later segments of large
  files. Repeated reads with different offsets accumulate in the coverage
  audit — do not hesitate to drill.

### No cargo-culted confidence

Do not use labels like "high/medium/low confidence", stars, scores, or
"I'm confident that...". Instead write honest prose — if something is
uncertain, describe the uncertainty concretely ("I only read 200 of 4801
lines of protocol.rs so my claim about message routing is based on the
top-level enum definition, not the handler implementations").

Submit landmarks/sketches/gaps via the dedicated tools, then call finish
when done. Quality and depth of evidence matter more than count."""

        return [{"role": "user", "content": user_content}]


class _LandmarkPickerExtractResult(ExtractResultRouter):
    """LandmarkPicker 自定义产物提取 — 从 session state 取 landmarks/sketches/gaps."""

    def __init__(self, agent_ref: "LandmarkPickerRouter", *, bus: Any | None = None):
        super().__init__(bus=bus)
        self._agent = agent_ref

    def extract(
        self,
        *,
        final_text: str,
        messages: list[dict],
        turn_count: int,
        stop_reason: str,
    ) -> Verdict:
        sess_id = self._agent._sess_id
        if not sess_id:
            return Verdict(
                kind=VerdictKind.FAIL,
                output={},
                diagnosis="LandmarkPicker: no session id (init failed)",
            )

        state = get_session(sess_id)
        if state is None:
            return Verdict(
                kind=VerdictKind.FAIL,
                output={},
                diagnosis="LandmarkPicker: session state lost",
            )

        landmarks = state.get("landmarks", [])
        sketches = state.get("sketches", [])
        gaps = state.get("gaps", [])
        read_files = state.get("read_files", [])
        listed_paths = state.get("listed_paths", [])

        # 从 session state 取回原始 input_data, 透传所有 upstream 字段
        upstream = state.get("upstream_input") or {}
        output = dict(upstream)
        output["landmarks"] = landmarks
        output["landscape_sketches"] = sketches
        output["capability_gaps"] = gaps
        output["picker_read_files"] = read_files
        output["picker_listed_paths"] = listed_paths
        output["picker_finish_summary"] = final_text.strip() if final_text else None
        output["picker_loop_stats"] = {
            "landmarks_count": len(landmarks),
            "sketches_count": len(sketches),
            "gaps_count": len(gaps),
            "files_read": len(read_files),
            "dirs_listed": len(listed_paths),
        }

        # Clean up session + tool cache before returning
        pop_session(sess_id)
        _drop_legacy_tools(sess_id)
        self._agent._sess_id = None

        if not landmarks and not gaps and not sketches:
            return Verdict(
                kind=VerdictKind.PARTIAL,
                output=output,
                diagnosis=(
                    "Picker 结束但未提交任何 landmark/sketch/gap。可能是 LLM 失去方向或"
                    "认为该仓无价值。查看 picker_finish_summary 获取 LLM 自述。"
                ),
                confidence=0.3,
            )

        return Verdict(
            kind=VerdictKind.PASS,
            output=output,
            confidence=0.85,
            diagnosis=(
                f"picker finished: {len(landmarks)} landmarks "
                f"({sum(1 for l in landmarks if l.get('tier') == 1)} t1), "
                f"{len(sketches)} sketches, {len(gaps)} gaps; "
                f"read {len(read_files)} files, listed {len(listed_paths)} dirs"
            ),
            granted_tags=["domain.absorption", "stage.judged", "stage.evidence_backed"],
        )


# ═══════════════════════════════════════════════════════════
# LandmarkPickerRouter (AgentNodeLoop) — router 化, 2026-05-03 迁
# ═══════════════════════════════════════════════════════════


class LandmarkPickerRouter(AgentNodeLoop):
    """AgentNodeLoop 版 LandmarkPicker, 迭代读代码 + 对照 OmniCompany (router 化, 2026-05-03 迁).

    7 个原闭包工具中 6 个重写为 SingleToolRouter wrappers (think drop, FinishTool 由 base 自动加),
    通过 ToolContext.absorption_sess_id 找到对应 session 的 legacy ToolDefinition 并委托执行.
    Session state 沿用原 tools.py 的模块级 _SESSION_STATE dict.
    """

    DESCRIPTION = (
        "AgentNodeLoop (max 80 turns, readonly): 迭代探索外部 repo 源码 + 查 "
        "OmniCompany 能力, 产出 evidence-backed landmarks + sketches + gaps。"
    )
    FORMAT_IN = "absorption.omnicompany_snapshot"
    FORMAT_OUT = "absorption.landmark_list"

    NODE_PROMPT: ClassVar[str] = _NODE_PROMPT
    TOOL_ROUTERS: ClassVar[list] = [
        _GhTreeListRouter,
        _GhFileReadRouter,
        _OmniCapabilitiesRouter,
        _SubmitLandmarkRouter,
        _SubmitLandscapeSketchRouter,
        _SubmitCapabilityGapRouter,
    ]
    LOOP_CONFIG: ClassVar[LoopConfig] = LoopConfig(
        max_turns=80,
        compact=CompactConfig(
            auto_compact_enabled=True,
            auto_compact_threshold=0.85,
        ),
        permission=PermissionConfig(mode="readonly"),
    )

    def __init__(
        self,
        *,
        model: str | None = None,
        bus: Any | None = None,
        config: LoopConfig | None = None,
        role: str = "runtime_main",
    ):
        self._sess_id: str | None = None
        self._role = role
        super().__init__(model=model, bus=bus, config=config or self.LOOP_CONFIG)

    def build_prompt_builder(self, *, bus: Any) -> PromptBuilderRouter:
        return _LandmarkPickerPromptBuilder(self, template=self.NODE_PROMPT, bus=bus)

    def build_extract_result(self, *, bus: Any) -> ExtractResultRouter:
        return _LandmarkPickerExtractResult(self, bus=bus)

    def build_tool_context(self, *, input_data: dict, turn: int, trace_id: str) -> dict:
        # SingleTool wrappers 从 ctx.absorption_sess_id 找 session 的 legacy 工具
        ctx = super().build_tool_context(input_data=input_data, turn=turn, trace_id=trace_id)
        ctx.update({
            "trace_id": trace_id,
            "turn_number": turn,
            "absorption_sess_id": self._sess_id,
        })
        return ctx
