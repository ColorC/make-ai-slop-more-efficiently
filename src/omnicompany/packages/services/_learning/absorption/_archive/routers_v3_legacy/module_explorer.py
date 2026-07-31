# [OMNI] origin=claude-code domain=services/absorption ts=2026-04-18T00:00:00Z type=router
# [OMNI] material_id="material:learning.absorption.v3_legacy.module_explorer.agent_loop.py"
#
# ⚠ DEPRECATED (2026-04-18) — 继承旧 runtime.agent.agent_node_loop.AgentNodeLoop。阶段 C 会迁到 packages.services.agent.AgentNodeLoop
# 违规：LLMClient/ToolDefinition.call 直调 + 内存 list[dict] 传参（非 Format+bus）。
# 重构计划：omnifactory/docs/plans/[2026-04-18]AGENT-NODE-LOOP-ROUTERIZATION/plan.md
# 禁止基于本类新增实现；Guardian 会监控违规。
"""module_explorer — V3 ModuleExplorerRouter（AgentNodeLoop）

合并原 ModulePicker + ModuleReader 的职责。

设计依据（对照 format.md 原则 5 / F-14 / F-15 / P-13）：
  「判断节点的 input Format 必须包含该节点做出判断所需的全部信息」
  - 判断「哪个模块值得深读」需要看实际代码内容
  - 原 ModulePicker 仅凭 symbol 名做判断，违反 F-14
  - 修法：选择和读取合并为同一 AgentNodeLoop，选择发生在读之后

**2026-04-18 升级**：FORMAT_IN 从 absorption.repomap 升为 composite
`absorption.module_exploration.context`（4 路 fan-in）。消费 wiki 三路
（capability_inventory / gap_registry / reception_intents）做四元判断：
已有可改进 / 已知缺口 / 愿接收新主题 / 架构冲突。硬编码 self_ portrait 字段已全部移除。

工具集：
  local_list    — 列目录（了解结构，浏览感兴趣的区域）
  local_grep    — 全 repo 搜索关键词/模式（主动发现正交模块）
  local_read    — 读具体文件（读完再决定是否 submit）
  submit_module — 提交一个已读文件为「值得学习的模块」
  think / finish

FORMAT_IN:  absorption.module_exploration.context (composite)
FORMAT_OUT: absorption.module.code
"""
from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any, ClassVar

from omnicompany.protocol.anchor import Verdict, VerdictKind
from omnicompany.runtime.routing.router import Router

# ── 会话状态 ─────────────────────────────────────────────────────────────────

_EXPLORER_SESSION_STATE: dict[str, dict] = {}
_EXPLORER_SESS_LOCK = threading.Lock()
_EXPLORER_SESS_COUNTER = 0


def _next_explorer_sess_id(router: Any) -> str:
    global _EXPLORER_SESS_COUNTER
    with _EXPLORER_SESS_LOCK:
        _EXPLORER_SESS_COUNTER += 1
        return f"explorer-v3-{id(router)}-{_EXPLORER_SESS_COUNTER}"


def _new_explorer_session(
    sess_id: str,
    repo_local_path: str,
    repo_name: str,
    detail_views: dict,
    upstream_input: dict,
) -> dict:
    state: dict = {
        "repo_local_path": repo_local_path,
        "repo_name": repo_name,
        "detail_views": detail_views,
        "upstream_input": upstream_input,
        "read_files": [],        # 已读文件路径列表
        "read_cache": {},        # path → content（已读内容缓存）
        "submitted_modules": [], # 已提交的模块列表
    }
    _EXPLORER_SESSION_STATE[sess_id] = state
    return state


# ── 工具工厂 ─────────────────────────────────────────────────────────────────

def _make_explorer_tools(sess_id: str) -> list:
    from omnicompany.runtime.agent.agent_loop_tools import (
        FinishTool, ThinkTool, ToolDefinition,
    )

    _MAX_READ = 25  # 最多读 25 个不同文件

    def _state() -> dict:
        return _EXPLORER_SESSION_STATE[sess_id]

    # ── local_list ────────────────────────────────
    LocalListTool = ToolDefinition(
        name="local_list",
        description=(
            "List files and subdirectories at a path. "
            "Use path='' for repo root. Non-recursive."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "path": {"type": "string", "default": ""},
            },
            "required": [],
        },
        is_concurrency_safe=True, is_readonly=True,
    )

    def _local_list_call(args: dict, executor: Any, ctx: Any) -> str:
        repo_root = Path(_state()["repo_local_path"])
        rel = (args.get("path") or "").strip("/\\").strip()
        target = repo_root / rel if rel else repo_root
        if not target.exists():
            return f"Error: '{rel or '.'}' does not exist"
        if not target.is_dir():
            return f"Error: '{rel or '.'}' is not a directory"
        items = []
        try:
            for e in sorted(target.iterdir()):
                rp = str(e.relative_to(repo_root)).replace("\\", "/")
                items.append({"name": e.name, "type": "dir" if e.is_dir() else "file",
                               "path": rp, "size": e.stat().st_size if e.is_file() else 0})
        except PermissionError as ex:
            return f"Error: permission denied: {ex}"
        return json.dumps({"path": rel or ".", "items": items}, ensure_ascii=False)

    LocalListTool.call = _local_list_call  # type: ignore[assignment]

    # ── local_grep ────────────────────────────────
    LocalGrepTool = ToolDefinition(
        name="local_grep",
        description=(
            "Search file contents across the repo using a regex. "
            "Returns file:line:content matches. "
            "Use this to find modules you don't know by filename — "
            "e.g. local_grep 'def retry|jitter|backoff' to find retry utilities. "
            "glob_pattern filters files (e.g. '*.py'). max_results default 80."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "pattern": {"type": "string"},
                "glob_pattern": {"type": "string", "default": ""},
                "max_results": {"type": "integer", "default": 80, "minimum": 1, "maximum": 200},
            },
            "required": ["pattern"],
        },
        is_concurrency_safe=True, is_readonly=True,
    )

    def _local_grep_call(args: dict, executor: Any, ctx: Any) -> str:
        import subprocess as _sp
        repo_root = Path(_state()["repo_local_path"])
        pattern = args.get("pattern", "")
        if not pattern:
            return "Error: pattern required"
        glob = (args.get("glob_pattern") or "").strip()
        max_r = int(args.get("max_results") or 80)
        cmd = ["rg", "--line-number", "--no-heading", "--color=never", "--max-count=3", pattern]
        if glob:
            cmd += ["--glob", glob]
        cmd.append(".")
        try:
            res = _sp.run(cmd, cwd=str(repo_root), capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=30)
            lines = res.stdout.splitlines()[:max_r]
        except FileNotFoundError:
            import re as _re
            import fnmatch as _fn
            lines = []
            try:
                rx = _re.compile(pattern)
            except _re.error as e:
                return f"Error: invalid regex: {e}"
            for fp in sorted(repo_root.rglob("*")):
                if not fp.is_file():
                    continue
                if glob and not _fn.fnmatch(fp.name, glob.lstrip("**/").lstrip("**\\")):
                    continue
                try:
                    for i, ln in enumerate(fp.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
                        if rx.search(ln):
                            lines.append(f"{str(fp.relative_to(repo_root)).replace(chr(92),'/')}:{i}:{ln}")
                            if len(lines) >= max_r:
                                break
                except Exception:
                    continue
                if len(lines) >= max_r:
                    break
        except Exception as e:
            return f"Error: {e}"
        if not lines:
            return f"No matches for '{pattern}'"
        return f"=== grep '{pattern}' — {len(lines)} matches ===\n" + "\n".join(lines)

    LocalGrepTool.call = _local_grep_call  # type: ignore[assignment]

    # ── local_read ────────────────────────────────
    LocalReadTool = ToolDefinition(
        name="local_read",
        description=(
            "Read a file from the repo. Returns line-numbered content. "
            f"Budget: at most {_MAX_READ} distinct files per session. "
            "Read files you are seriously considering submitting — "
            "reading is the prerequisite for submitting. "
            "**Default limit is 20000 lines — reads the whole file for nearly all source files. "
            "Header reports total lines; if total > end you MUST call again with offset=end to see the rest. "
            "Never submit_module without reading the FULL file first.**"
        ),
        input_schema={
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "offset": {"type": "integer", "default": 0, "minimum": 0},
                "limit": {"type": "integer", "default": 20000, "minimum": 10},
            },
            "required": ["path"],
        },
        is_concurrency_safe=True, is_readonly=True,
    )

    def _local_read_call(args: dict, executor: Any, ctx: Any) -> str:
        state = _state()
        repo_root = Path(state["repo_local_path"])
        rel = (args.get("path") or "").strip("/\\")
        read_log = state["read_files"]
        cache = state["read_cache"]
        is_new = rel not in read_log
        if is_new and len(read_log) >= _MAX_READ:
            pending = [m["path"] for m in state["submitted_modules"]]
            return (
                f"ERROR: read budget exhausted ({_MAX_READ}/{_MAX_READ} files). "
                f"Submitted so far: {pending}. "
                f"Call submit_module for any remaining candidates you already read, then finish."
            )
        target = repo_root / rel
        if not target.exists():
            return f"Error: '{rel}' not found"
        if not target.is_file():
            return f"Error: '{rel}' is a directory"
        if target.stat().st_size > 1024 * 1024:
            return f"Error: '{rel}' too large (>1MB)"
        try:
            content = target.read_text(encoding="utf-8", errors="replace")
        except Exception as e:
            return f"Error reading {rel}: {e}"
        lines = content.splitlines()
        total = len(lines)
        offset = int(args.get("offset") or 0)
        limit = int(args.get("limit") or 20000)
        start = min(offset, total)
        end = min(start + limit, total)
        segment = lines[start:end]
        numbered = "\n".join(f"{i+1:5d}\t{ln}" for i, ln in enumerate(segment, start=start))
        if is_new:
            read_log.append(rel)
        # 累积缓存（多次读取合并）
        if rel not in cache:
            cache[rel] = []
        cache[rel].append((start, end, "\n".join(segment)))
        remaining_hint = (
            f"\n\n⚠️ 文件未读完：total={total} 行，本次只读到第 {end} 行。"
            f"还有 {total - end} 行未读。调 local_read(path='{rel}', offset={end}) 继续读。"
            f"**submit_module 之前必须读完全文**。"
            if end < total else ""
        )
        header = f"=== {rel} ({total} lines, showing {start+1}-{end}) [READ: {len(read_log)}/{_MAX_READ}] ===\n"
        return header + numbered + remaining_hint

    LocalReadTool.call = _local_read_call  # type: ignore[assignment]

    # ── submit_module ─────────────────────────────
    SubmitModuleTool = ToolDefinition(
        name="submit_module",
        description=(
            "Submit a file as a module worth learning from. "
            "IMPORTANT: You MUST have called local_read on this file first. "
            "Submitting without reading = rejected. "
            "\n"
            "Fill these fields based on the wiki context provided in the initial user message:\n"
            "- judgement: one of {already_exists, known_gap, welcome_theme, unforeseen, conflict}\n"
            "  * already_exists — OmniCompany 某 capability_inventory 模块已有类似实现，可改进\n"
            "  * known_gap — 对应 gap_registry 的某个 gap_id（填 wiki_ref）\n"
            "  * welcome_theme — 对应 reception_intents 某模块的 welcome_themes（填 wiki_ref=<module_path>）\n"
            "  * unforeseen — 与已有能力、已知缺口、接收意愿都不直接匹配但看起来重要（未预知，交下游评估）\n"
            "  * conflict — 违反某个 hard_constraint（填 reason 说明冲突，**标注但不跳过**）\n"
            "- wiki_ref: 对应 wiki 实体的标识：gap_id (G1-G7) / module_path (runtime/llm 等) / 留空（unforeseen）\n"
            "- priority: P0 (OmniCompany 完全缺 / 已知高价值缺口) / P1 (已有较弱 / 愿接收) / "
            "P2 (参考 / 架构冲突但记录)\n"
            "- reason: 具体这个文件实现了什么（引用你读到的代码片段）"
        ),
        input_schema={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "File path (must have been read)"},
                "judgement": {
                    "type": "string",
                    "enum": ["already_exists", "known_gap", "welcome_theme", "unforeseen", "conflict"],
                },
                "wiki_ref": {
                    "type": "string",
                    "description": "gap_id / module_path / 空字符串",
                },
                "priority": {"type": "string", "enum": ["P0", "P1", "P2"]},
                "reason": {"type": "string", "description": "What specifically makes this worth learning (cite what you read)"},
            },
            "required": ["path", "judgement", "priority", "reason"],
        },
        is_concurrency_safe=False, is_readonly=True,
    )

    def _submit_module_call(args: dict, executor: Any, ctx: Any) -> str:
        state = _state()
        path = (args.get("path") or "").strip("/\\")
        if path not in state["read_files"]:
            return (
                f"Error: '{path}' has not been read in this session. "
                f"Call local_read(path='{path}') first, then submit_module."
            )
        # 收集已读内容片段
        cache = state["read_cache"]
        content_parts = cache.get(path, [])
        if content_parts:
            # 用最大范围的那次读取
            best = max(content_parts, key=lambda x: x[1] - x[0])
            content = f"=== {path} (read lines {best[0]+1}-{best[1]}) ===\n{best[2]}"
        else:
            content = f"[content from read session for {path}]"

        # 也尝试从 detail_views 补充符号树
        detail = state["detail_views"].get(path, "")

        judgement = args.get("judgement", "unforeseen")
        wiki_ref = (args.get("wiki_ref") or "").strip()
        # 向后兼容：若 wiki_ref 留空，judgement=known_gap 时猜个 "?"
        if judgement == "known_gap" and not wiki_ref:
            wiki_ref = "?"
        state["submitted_modules"].append({
            "path": path,
            "judgement": judgement,
            "wiki_ref": wiki_ref,
            # gap_id 字段向后兼容（下游 learning_extractor / report_writer 可能读），
            # known_gap 时复用 wiki_ref；其它 judgement 时留空表达式以便下游识别"非缺口"
            "gap_id": wiki_ref if judgement == "known_gap" else "",
            "priority": args.get("priority", "P2"),
            "reason": args.get("reason", ""),
            "content": content,
            "detail_view": detail,
        })
        n = len(state["submitted_modules"])
        return f"Module '{path}' submitted as {judgement} ({n} total so far)."

    SubmitModuleTool.call = _submit_module_call  # type: ignore[assignment]

    return [LocalListTool, LocalGrepTool, LocalReadTool, SubmitModuleTool, ThinkTool, FinishTool]


# ── 系统提示 ─────────────────────────────────────────────────────────────────

_EXPLORER_SYSTEM_PROMPT = """你是 OmniCompany 的知识探索者。你在读一个外部代码仓库，
目标是找出所有对 OmniCompany 有学习价值的模块，并把它们的内容收集进来。

## 核心洞察：基础设施进步无法预知

OmniCompany 的 capability_inventory / gap_registry 只能描述**已经意识到的**维度。
真正高价值的吸纳常常在"意识到之前"——没见过 smart_model_routing 就不知道它是一个概念。
因此：
- wiki **不穷举**探索方向；reception_intents 的 welcome_themes 只给锚点
- 外部 repo 里**不匹配任何 wiki 条目但看起来重要**的文件，要**主动标为 unforeseen 交下游评估**
- 不要因为"wiki 没提"就跳过明显的精密实现

## 步骤 0：先看项目自述特色（project_thesis）

**在任何探索之前**，先读 user 消息里的「外部项目自述特色」节。这是项目自己宣称的核心价值和主要设计。

- 宣称特色是**必须覆盖清单**——不管 wiki 是否提及，每一条宣称特色都必须在探索过程中至少检查一次
- 如果一个宣称特色有对应的代码文件，submit_module 的 reason 里注明"**确认宣称特色**：{宣称内容}"
- 如果找不到对应实现，用 `local_grep` 主动搜，仍找不到时 submit 一条 unforeseen 并在 reason 里写"**宣称但代码层未见实证**：{宣称内容}"
- **不评判宣称特色是否"神奇"**——只如实记录对齐情况；report_writer 会综合判断

## 四元判断原则（读完每个文件后落到这一档）

对每个你认真读过的文件，用 wiki 做四元对比，决定 submit_module 的 judgement 字段：

1. **already_exists** — 对应 OmniCompany capability_inventory 里某个模块，"已有但可改进"
   → 填 wiki_ref = 模块路径（如 `runtime/llm`）
2. **known_gap** — 对应 gap_registry 里某个 gap
   → 填 wiki_ref = 缺口 ID（如 `G1`）
3. **welcome_theme** — 对应 reception_intents 里某基础设施模块的 welcome_themes
   → 填 wiki_ref = 模块路径（如 `runtime/llm`），reason 里引用对应 welcome_theme
4. **unforeseen** — **不匹配** 以上三档，但你判断这个文件重要
   → 填 wiki_ref = "", reason 里说明为什么重要
5. **conflict** — 违反某个 reception_intents.hard_constraints
   → 填 wiki_ref = 违反的模块路径，reason 说明冲突点。**依然提交**（标注"不吸纳"交下游），
      priority 通常 P2

## 工作原则

**读完再提交，不读不提交。** submit_module 要求你必须先 local_read 这个文件。
判断「这个文件值不值得深读」需要看实际代码——仅凭文件名和符号名不够。

不要因为 wiki 没直接提到某类能力就跳过。你的主动发现优先级高于机械匹配。

## 工作流程

0. **先看项目自述**（步骤 0，见上方）：列出宣称特色清单，作为必须覆盖的检查列表

1. **再看 wiki（在 user 消息里）**：
   - capability_inventory：OmniCompany 有哪些模块（判断 already_exists）
   - gap_registry：OmniCompany 已识别哪些缺口（判断 known_gap）
   - reception_intents：各基础设施模块 welcome_themes + hard_constraints
     （判断 welcome_theme / conflict）

2. **看外部地图（coarse_view）**：所有文件 `path[行数]:symbol1·symbol2`
   - 排名靠前的不一定最有价值；小文件可能是精密工具
   - 主动找：error·retry·classifier·manager·provider·registry·approval·
     compress·plugin·mcp·ensemble·router·checkpoint·delegate·mixture·audit·skill

3. **搜索**：用 local_grep 主动发现感兴趣的模式
   - `local_grep "def retry|backoff|jitter"` → 重试工具
   - `local_grep "DANGEROUS|approval|dangerous_command"` → 安全拦截
   - `local_grep "class.*Provider|ABC"` → 接口层
   - `local_grep "mixture|ensemble|delegate|sub.?agent"` → 多模型/子 agent
   - `local_grep "checkpoint|snapshot"` → 状态快照
   - 不限于 wiki 提过的主题，任何值得学的都搜

4. **读取**：对有意思的文件 local_read 看实际代码
   - 读完 think 一下：这个文件真正做了什么？对照 wiki 四元判断落哪一档？
   - 即便落 unforeseen 也值得提交（OmniCompany 没预知但可能该吸纳）

5. **提交**：submit_module 带 judgement + wiki_ref + priority + reason
   - 无数量限制；有多少值得提交的就提交多少
   - conflict 也提交（记录已评估不吸纳的理由，下游不会重复踩坑）

6. **继续**：不断循环，直到确信没有明显遗漏
7. **结束**：调 finish

## 记住

- 工作文件预算是 25 个不同文件（local_read 计数）
- local_grep 不消耗预算，可以随时用来定位
- 预算用完时优先提交已读的，再 grep 定位剩余重要文件
- **unforeseen 比硬凑 known_gap 更诚实**：对不上 wiki 就标 unforeseen，不要强行套 G1-G7
"""


# ── Router ───────────────────────────────────────────────────────────────────

class ModuleExplorerRouter(Router):
    """V3 模块探索节点（AgentNodeLoop）。

    合并了原 ModulePicker + ModuleReader 的职责。
    选择发生在读取之后，不是之前——符合 format.md 原则 5 / F-14。

    工具集：local_list / local_grep / local_read / submit_module / think / finish
    格式链：absorption.repomap → absorption.module.code
    """

    DESCRIPTION = (
        "V3 模块探索：AgentNodeLoop，读完再选，"
        "local_grep 主动发现 + local_read 确认内容 + submit_module 提交，"
        "符合 F-14 判断信息充分原则。"
        "FORMAT_IN 是 composite (absorption.module_exploration.context)，"
        "4 路 fan-in: repomap + capability_inventory + gap_registry + reception_intents，"
        "Explorer 基于四元判断（已有/缺口/愿接收/架构冲突 + unforeseen 兜底）提交模块。"
    )
    FORMAT_IN = "absorption.module_exploration.context"
    FORMAT_OUT = "absorption.module.code"

    def __init__(self, **kwargs: Any) -> None:
        self._sess_id: str | None = None
        self._role = kwargs.get("role", "runtime_main")

    def _build_agentloop(self) -> Any:
        from omnicompany.runtime.agent.agent_node_loop import AgentNodeLoop
        from omnicompany.runtime.agent.agent_loop_config import (
            CompactConfig, LoopConfig, PermissionConfig,
        )

        class _ExplorerLoop(AgentNodeLoop):
            DESCRIPTION = ModuleExplorerRouter.DESCRIPTION
            FORMAT_IN = "absorption.module_exploration.context"
            FORMAT_OUT = "absorption.module.code"
            # Bus 由 Runner 通过 router._bus 注入（见 runtime/exec/runner.py
            # 通用 bus 注入段），_build_agentloop() 下面把 self._bus 透传给 loop
            # 构造器。agent 内部 tool.call / tool.result / llm.request /
            # llm.response 事件因此可全量落盘，满足 2026-04-18 的硬校验。
            SYSTEM_PROMPT: ClassVar[str] = _EXPLORER_SYSTEM_PROMPT
            LOOP_CONFIG: ClassVar[LoopConfig] = LoopConfig(
                max_turns=80,
                compact=CompactConfig(
                    auto_compact_enabled=True,
                    auto_compact_threshold=0.80,
                ),
                permission=PermissionConfig(mode="readonly"),
            )
            TOOLS: ClassVar[list] = []

            def __init__(self_inner, outer: "ModuleExplorerRouter", **kw: Any) -> None:
                kw.setdefault("role", outer._role)
                super().__init__(**kw)
                self_inner._outer = outer

            def build_initial_messages(self_inner, input_data: dict) -> list[dict]:
                from omnicompany.runtime.llm.llm import LLMClient
                from omnicompany.packages.services._learning.absorption.wiki_loader import (
                    load_capability_inventory, load_gap_registry, load_reception_intents,
                    render_capability_inventory_for_prompt,
                    render_gap_registry_for_prompt,
                    render_reception_intents_for_prompt,
                )

                # ── 读 composite 的 4 路组件 ──
                # 主路径（正常 dispatch）：runner 按 composite Format 的 components 做 fan-in 合并，
                #   input_data 以各 format_id 为 key 提供 4 路。
                # 兜底路径（FeedbackRouter JUMP supplement 模式）：input_data 可能只带
                #   repomap 字段 + supplement_guidance。此时三路 wiki 从 wiki_loader 进程级缓存兜底。
                repomap_obj = input_data.get("absorption.repomap")
                capability_obj = input_data.get("omni.self.capability_inventory")
                gap_obj = input_data.get("omni.self.gap_registry")
                reception_obj = input_data.get("omni.self.reception_intents")

                fallback_diagnoses: list[str] = []

                # repomap 必须存在 —— 没有外部仓库地图没法工作
                if repomap_obj is None:
                    # 向后兼容：旧输入把 repomap 字段平铺在 input_data 里（supplement/JUMP 场景）
                    if input_data.get("repo_local_path") or input_data.get("coarse_view"):
                        repomap_obj = dict(input_data)
                        fallback_diagnoses.append(
                            "repomap 直接从 input_data 平铺字段重建（非 composite 走法）"
                        )

                if repomap_obj is None:
                    raise ValueError(
                        "ModuleExplorer: 缺少 absorption.repomap 组件（composite FORMAT_IN 未满足）"
                    )

                repo_name = repomap_obj.get("repo_name", "unknown")
                repo_local_path = repomap_obj.get("repo_local_path", "")
                coarse_view = repomap_obj.get("coarse_view", "")
                detail_views: dict = repomap_obj.get("detail_views") or {}

                if not repo_local_path:
                    raise ValueError("ModuleExplorer: 缺少 repo_local_path")
                if not coarse_view:
                    raise ValueError("ModuleExplorer: 缺少 coarse_view（RepoMapper 未运行？）")

                # wiki 三路：composite 模式直接用；supplement/jump 模式从 wiki_loader 缓存兜底
                if capability_obj is None:
                    try:
                        capability_obj = load_capability_inventory()
                        fallback_diagnoses.append("capability_inventory 走 wiki_loader 缓存兜底")
                    except Exception:
                        capability_obj = {"modules": [], "module_count": 0, "readme_capability_map": ""}
                if gap_obj is None:
                    try:
                        gap_obj = load_gap_registry()
                        fallback_diagnoses.append("gap_registry 走 wiki_loader 缓存兜底")
                    except Exception:
                        gap_obj = {"gaps": [], "gap_count": 0, "index_summary": ""}
                if reception_obj is None:
                    try:
                        reception_obj = load_reception_intents()
                        fallback_diagnoses.append("reception_intents 走 wiki_loader 缓存兜底")
                    except Exception:
                        reception_obj = {"intents": [], "module_count": 0}

                sess_id = _next_explorer_sess_id(self_inner)
                self_inner._outer._sess_id = sess_id
                # upstream_input 保留 repomap 组件（下游 learning_extractor 要 repo_name 等）
                _new_explorer_session(
                    sess_id,
                    repo_local_path=repo_local_path,
                    repo_name=repo_name,
                    detail_views=detail_views,
                    upstream_input=dict(repomap_obj),
                )
                if fallback_diagnoses:
                    _EXPLORER_SESSION_STATE[sess_id]["fallback_notes"] = fallback_diagnoses

                from omnicompany.runtime.agent.agent_loop_tools import FinishTool
                bound_tools = _make_explorer_tools(sess_id)
                if not any(t.name == "finish" for t in bound_tools):
                    bound_tools.append(FinishTool)
                self_inner._tools = bound_tools
                self_inner._tool_map = {t.name: t for t in self_inner._tools}
                tools_spec = [t.to_api_spec() for t in self_inner._tools]
                role = self_inner._outer._role
                self_inner._llm = LLMClient(role=role, tools=tools_spec)
                self_inner._llm_no_tools = LLMClient(role=role, tools=[])

                # 检查是否是补充探索（FeedbackRouter JUMP 过来）
                # supplement_guidance 可能挂在 composite 外层，也可能挂在 repomap 内部（兼容 JUMP）
                supplement_guidance: str = (
                    input_data.get("supplement_guidance")
                    or repomap_obj.get("supplement_guidance", "")
                    or ""
                )
                previous_files_read: list[str] = list(
                    input_data.get("previous_files_read")
                    or repomap_obj.get("previous_files_read")
                    or []
                )
                found_titles: list[str] = list(
                    input_data.get("found_titles")
                    or repomap_obj.get("found_titles")
                    or []
                )
                iteration: int = int(
                    input_data.get("iteration")
                    or repomap_obj.get("iteration")
                    or 1
                )

                supplement_section = ""
                if supplement_guidance:
                    prev_files_text = (
                        "\n".join(f"  - {f}" for f in previous_files_read[:20])
                        if previous_files_read else "（无）"
                    )
                    prev_findings_text = (
                        "\n".join(f"  - {t}" for t in found_titles[:10])
                        if found_titles else "（无）"
                    )
                    supplement_section = f"""
---

## ⚠️ 补充探索指示（Iteration {iteration}）

{supplement_guidance}

**上一轮已读文件（可复读，但优先探索新方向）**:
{prev_files_text}

**上一轮已发现（不要重复提交）**:
{prev_findings_text}

请优先按照补充探索方向行动，而不是重复上一轮的路径。"""

                task_prefix = (
                    f"## ⚠️ 补充探索 Iteration {iteration}\n\n"
                    if supplement_guidance else ""
                )

                # 组装 wiki 三档到 user prompt
                capability_md = render_capability_inventory_for_prompt(capability_obj)
                gap_md = render_gap_registry_for_prompt(gap_obj)
                reception_md = render_reception_intents_for_prompt(reception_obj)
                project_thesis = repomap_obj.get("project_thesis", "")
                project_thesis_section = (
                    f"""---

## § 项目自述特色（project_thesis · 步骤 0 必读）

以下是外部项目自己宣称的核心特色与设计哲学（来自 README 安装节之前的部分）。
**探索时必须对每条宣称特色至少检查一次**；submit_module 的 reason 里标注
"确认宣称特色：{'{宣称内容}'}" 或 "宣称了但代码层未见实证：{'{宣称内容}'}"。
不评判宣称特色是否重要——只如实记录对齐情况。

{project_thesis}

---"""
                    if project_thesis
                    else ""
                )

                user_msg = f"""# 模块探索任务

{task_prefix}**Repo**: {repo_name}
**路径**: {repo_local_path}
{supplement_section}

---

# 第一部分：OmniCompany 自知识（wiki 动态加载）

对照四元判断原则（见 system prompt）：判外部文件属 already_exists / known_gap / welcome_theme / unforeseen / conflict 哪一档。

## § wiki-1 · 能力清单（capability_inventory）

{capability_md}

---

## § wiki-2 · 已识别缺口（gap_registry）

{gap_md}

---

## § wiki-3 · 接收意愿（reception_intents）

{reception_md}

{project_thesis_section}

# 第二部分：外部仓库全量符号地图（coarse_view）

格式：`path[行数]:symbol1·symbol2·symbol3`，按重要性排序

{coarse_view}

---

开始探索。**先从步骤 0 列出宣称特色清单**，再 think 地图里哪些文件与宣称特色或 wiki 三档相关；
也要主动 grep 基础设施进步的典型关键词（ensemble/mixture/router/checkpoint/delegate/skill...），
可能落 unforeseen 档的实现往往被 wiki 漏掉但最值得吸纳。

然后 local_read 确认实际内容，最后 submit_module（带 judgement + wiki_ref + priority + reason）。
**读完再提交，不读不提交。**"""

                return [{"role": "user", "content": user_msg}]

            def extract_result(self_inner, final_text: str, messages: list[dict]) -> Verdict:
                sess_id = self_inner._outer._sess_id
                if not sess_id:
                    return Verdict(kind=VerdictKind.FAIL, output={},
                                   diagnosis="ModuleExplorer: no session id")
                state = _EXPLORER_SESSION_STATE.pop(sess_id, None)
                self_inner._outer._sess_id = None
                if state is None:
                    return Verdict(kind=VerdictKind.FAIL, output={},
                                   diagnosis="ModuleExplorer: session state lost")

                submitted = state.get("submitted_modules", [])
                read_files = state.get("read_files", [])
                upstream = state.get("upstream_input", {})
                repo_name = state.get("repo_name", "unknown")

                if not submitted:
                    return Verdict(
                        kind=VerdictKind.PARTIAL,
                        output={**upstream, "repo_name": repo_name,
                                "module_readings": [], "files_read": read_files},
                        confidence=0.0,
                        diagnosis="ModuleExplorer: 探索结束但未 submit 任何模块",
                    )

                # 构建 module_readings（absorption.module.code 格式）
                module_readings = []
                for m in submitted:
                    content = m.get("content", "")
                    detail = m.get("detail_view", "")
                    if content and detail:
                        full = f"## 符号树\n{detail}\n\n## 已读代码\n{content}"
                        method = "detail_view+local_read"
                    elif content:
                        full = content
                        method = "local_read"
                    else:
                        full = f"## 符号树\n{detail}" if detail else "[no content]"
                        method = "detail_view"
                    module_readings.append({
                        "path": m["path"],
                        "judgement": m.get("judgement", "unforeseen"),
                        "wiki_ref": m.get("wiki_ref", ""),
                        "gap_id": m.get("gap_id", ""),  # 兼容下游
                        "priority": m["priority"],
                        "reason": m.get("reason", ""),
                        "content": full,
                        "line_count": len(full.splitlines()),
                        "read_method": method,
                    })

                p0 = sum(1 for m in module_readings if m["priority"] == "P0")
                judgements = sorted({m.get("judgement", "unforeseen") for m in module_readings})
                fallback_notes = state.get("fallback_notes", [])

                diag = (
                    f"ModuleExplorer: {len(module_readings)} 模块提交 ({p0} P0), "
                    f"judgements={judgements}, 读取 {len(read_files)} 文件"
                )
                if fallback_notes:
                    diag += f" | 兜底: {'; '.join(fallback_notes)}"

                return Verdict(
                    kind=VerdictKind.PASS,
                    output={
                        **upstream,
                        "repo_name": repo_name,
                        "module_readings": module_readings,
                        "files_read": read_files,
                    },
                    confidence=round(min(p0 / 5, 1.0), 2),
                    diagnosis=diag,
                    granted_tags=["domain.absorption", "stage.v3.explorer"],
                )

        # 把 Runner 注入的 bus 透传给内层 loop（见 runner.py 通用 bus 注入段）。
        # 若因调用路径没经 Runner（如直接单元测试）拿不到 _bus，则退化为 MemoryBus
        # 保底落盘到内存（不阻塞运行，也不隐藏事件）。
        injected_bus = getattr(self, "_bus", None)
        if injected_bus is None:
            from omnicompany.bus.memory import MemoryBus
            injected_bus = MemoryBus()
        return _ExplorerLoop(self, bus=injected_bus)

    async def run(self, input_data: Any) -> Verdict:  # type: ignore[override]
        loop = self._build_agentloop()
        # 保留 loop 引用, 供 crystallize 在 run 返回后提炼 trace
        self._last_agent_loop = loop
        # 把 outer router 类名传给 loop, crystallize 的 SpecPatch 才能指向真 Router 而非 inner class
        loop._outer_router_class = type(self).__name__
        return await loop.run(input_data)
