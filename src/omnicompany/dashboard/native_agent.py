# [OMNI] origin=ai-ide domain=dashboard ts=2026-05-02T09:30:00Z type=agent status=active agent=ai-ide-current
# [OMNI] summary="Native dashboard IDE agent — ConfigurableAgent + AgentSpec, prompt 走 .md material"
# [OMNI] why="2026-04-18 立的 Agent Node Loop Router 化铁律, 旧 IDEAgentLoop 继承的旧 AgentNodeLoop 已被 deprecate. 新写一个跟新规范对齐的 IDE agent, dogfood ConfigurableAgent 抽象."
# [OMNI] tags=agent,configurable,dashboard,ide,native
# [OMNI] material_id="material:dashboard.native_ide_agent.configurable_agent.py"
"""NativeIdeAgent — dashboard 内置交互式 agent.

继承 ConfigurableAgent, prompt 从 native_agent_prompt.md 加载. 工具集走 TOOL_REGISTRY
字符串引用 (glob/grep/read_file/list_dir/write_file/bash/finish).

跟旧 runtime.agent.ide_agent_loop.IDEAgentLoop 关系:
- 同样跑 ide_agent role (ModelRegistry override → qwen3.6-max-preview)
- 同样的 system prompt 内容 (从旧 SYSTEM_PROMPT_STATIC 拷, 拆 .md 文件 + 占位符化)
- 旧的多了 todo_write/goal_create/plan_register 等 IDE 业务工具, 本 round 1 没接 (留 round 2)
- 旧的有 assistant_db 动态注入 + on_compact 写 history, round 1 也没接 (留 round 2)
- round 1 目标: dogfood ConfigurableAgent 抽象, 验端到端跑通
"""

from __future__ import annotations

import os
import platform as _platform
import subprocess
from pathlib import Path
from typing import Any

from omnicompany.packages.services._core.agent import (
    AgentNodeLoop as _NewAgentNodeLoop,  # noqa: F401  (确保 import 触发 __init__ 拉子 router)
    AgentSpec,
    ConfigurableAgent,
    DevBashRouter,
    auto_register_singletool_subclasses,
)
# import dashboard 私有工具触发 SingleToolRouter 子类登记 (TodoWriteRouter 等)
from omnicompany.dashboard import native_agent_tools  # noqa: F401
from omnicompany.dashboard.native_agent_context import build_context_section
from omnicompany.dashboard.session_workdir import default_session_cwd, resolve_session_cwd
from omnicompany.runtime.agent.agent_loop_config import (
    CompactConfig,
    LoopConfig,
    PermissionConfig,
    RetryConfig,
)


# ── 自动登记非内建工具 (DevBashRouter / TodoWriteRouter 等) 到 TOOL_REGISTRY ──
# 内建 6 个 (glob/grep/read_file/list_dir/write_file/web_fetch) 已在 configurable.py 注册
# 其他 SingleToolRouter 子类只要被 import 过, auto_register 会扫到自动登记
auto_register_singletool_subclasses()

# DevBashRouter / BashRouter 的 TOOL_NAME 都是 "bash" 名字撞. NativeIdeAgent dashboard
# 工作流要 DevBashRouter (cwd whitelist, 不依赖 bash_bus 注入). 强制绑定 — 否则
# auto_register 拿到的是哪个取决于 __subclasses__() 顺序, BashRouter 实例化会因
# 缺 bash_bus= 参数报错 (BashRouter 走 BashBus 基础设施, NativeIdeAgent 没注入).
from omnicompany.packages.services._core.agent.configurable import (
    TOOL_REGISTRY,
    register_tool,
)
TOOL_REGISTRY.pop("bash", None)  # 清旧注册, 防 register_tool 重复注册报错
register_tool("bash", DevBashRouter)


# ── 环境探测 (用于 prompt 占位符替换) ─────────────────────────────────


def _detect_shell() -> str:
    if _platform.system() == "Windows":
        return "bash (Git Bash / WSL)"
    return os.environ.get("SHELL", "/bin/bash").split("/")[-1]


def _is_git_repo(cwd: str) -> bool:
    try:
        r = subprocess.run(
            ["git", "rev-parse", "--is-inside-work-tree"],
            cwd=cwd, capture_output=True, text=True, timeout=5,
        )
        return r.returncode == 0 and r.stdout.strip() == "true"
    except Exception:
        return False


def _build_substitutions(cwd: str, model_id: str, active_plan: str | None = None) -> dict[str, str]:
    return {
        "cwd": cwd,
        "is_git_repo": str(_is_git_repo(cwd)),
        "platform": _platform.system().lower(),
        "shell": _detect_shell(),
        "os_version": f"{_platform.system()} {_platform.release()}",
        "model_id": model_id,
        "knowledge_cutoff": "May 2025",
        # turn=0 一次性注入: PROGRESS.md 头 + active project.md + active plan.md
        # 跟 cc_session 共享同一份真信息源 (frontmatter / project.md), 无私有 store
        "assistant_context": build_context_section(cwd, active_plan=active_plan),
    }


# ── LoopConfig ──────────────────────────────────────────────────────


_NATIVE_LOOP_CONFIG = LoopConfig(
    max_turns=100,
    context_window=200_000,
    retry=RetryConfig(
        max_retries=10,
        base_delay_ms=500,
        max_delay_ms=32_000,
        jitter_factor=0.25,
        retry_on_overload=True,
        fallback_model=None,
        fallback_after_attempts=5,
    ),
    compact=CompactConfig(
        aging_threshold=5,
        max_tool_output=20_000,
        truncation_strategy="head_tail",
        max_messages=120,
        auto_compact_enabled=True,
        auto_compact_threshold=0.85,
        auto_compact_max_failures=3,
        compact_preserve_turns=3,
        enable_compression_summary=True,
    ),
    permission=PermissionConfig(
        mode="default",
        always_allow=["finish", "read_file", "glob", "grep", "list_dir"],
        always_deny=[],
        ai_classifier_enabled=False,
    ),
    enable_tool_concurrency=True,
    max_concurrent_tools=10,
    budget_warning_threshold=0.9,
)


# ── NativeIdeAgent ─────────────────────────────────────────────────


_PROMPT_PATH = "src/omnicompany/dashboard/native_agent_prompt.md"
_DEFAULT_MODEL_ID = "qwen3.6-max-preview"
_DEFAULT_CWD = default_session_cwd()


class NativeIdeAgent(ConfigurableAgent):
    """Dashboard 内置 IDE agent (ConfigurableAgent 子类, allow_custom_code=True).

    构造函数走 cwd → prompt_substitutions, 所以同一类不同实例可指不同 cwd.
    """

    SPEC = AgentSpec(
        id="dashboard.native_ide_agent",
        name="NativeIdeAgent",
        domain="dashboard",
        parent_worker_kind="agent",
        registry_namespace="dashboard.agent.instances",
        # LLM: 走 ide_agent role (ModelRegistry override → qwen3.6-max-preview)
        # llm_model 字段未参与新架构 LLMCallRouter (它走 role/model 显式 __init__ 参数)
        llm_model="qwen3.6-max-preview",
        llm_temperature=0.3,
        llm_max_tokens=4000,
        llm_max_turns=100,
        llm_timeout_seconds=600,
        prompt_path=_PROMPT_PATH,
        prompt_substitutions=_build_substitutions(_DEFAULT_CWD, _DEFAULT_MODEL_ID),
        tools=("glob", "grep", "read_file", "edit", "list_dir", "write_file", "bash", "todo_write", "think"),
        allow_custom_code=True,  # 我们 override LOOP_CONFIG + 走自定义 cwd 注入
    )

    LOOP_CONFIG = _NATIVE_LOOP_CONFIG

    def __init__(
        self,
        *,
        cwd: str | None = None,
        active_plan: str | None = None,
        bus: Any | None = None,
        config: LoopConfig | None = None,
    ):
        # 实例级覆写 prompt_substitutions cwd + active_plan
        self._cwd = resolve_session_cwd(cwd)
        self._active_plan = active_plan
        # 重跑替换拼出 instance prompt (含 PROGRESS.md / project.md / plan.md 真信息源注入)
        self._instance_prompt = self._reload_with_cwd(self._cwd, active_plan=active_plan)
        super().__init__(
            role="ide_agent",  # ModelRegistry override → qwen3.6-max-preview
            bus=bus,
            config=config or _NATIVE_LOOP_CONFIG,
        )
        from omnicompany.packages.services._core.agent.routers.prompt_builder import PromptBuilderRouter
        self._prompt_builder = PromptBuilderRouter(template=self._instance_prompt, bus=bus)

    def build_tool_context(self, *, input_data: dict, turn: int, trace_id: str) -> dict:
        """Override 注入 dashboard IDE 沙盒上下文.

        - cwd / project_root: 实例 cwd (默认项目根)
        - allowed_bash_roots: 允许 bash 在 cwd 下跑 (DevBashRouter 安全 guard 要求)
        - origin / agent_name: OmniGuardian 审计溯源
        - read_files: AgentNodeLoop 父类默认含 (Wave 5+7 Read→Edit 状态机), 这里 override 时
          要继承下来 — 否则 FileEdit 因 ctx 没 read_files 属性走兼容跳过路径, 协议失效
        - subagent_registry: 让主 agent 真能 spawn sub-agent (Wave 3 真上线)
        """
        # 懒构 subagent_registry (bus 在 super().__init__ 后才有)
        if not hasattr(self, "_subagent_registry_cache"):
            from omnicompany.packages.services._core.agent.routers.agent_spawn_factory import (
                build_default_subagent_registry,
            )
            try:
                self._subagent_registry_cache = build_default_subagent_registry(bus=self._bus)
            except Exception:
                # bus 缺 / 构造异常 → 留空 registry, AgentRouter 会报清晰错误指引
                self._subagent_registry_cache = {}

        ctx = super().build_tool_context(input_data=input_data, turn=turn, trace_id=trace_id)
        ctx.update({
            "cwd": self._cwd,
            "project_root": self._cwd,
            "trace_id": trace_id,
            "turn_number": turn,
            "permission_mode": "default",
            "origin": "ai-ide",
            "agent_name": "NativeIdeAgent",
            "domain": "dashboard",
            "allowed_bash_roots": (self._cwd,),
            # WriteFileRouter 沙盒: 允许写 cwd 树下任意路径 (跟 bash 同范围)
            "allowed_write_roots": (self._cwd,),
            # Wave 5+7 (2026-05-04 Read→Edit 状态机): 跨工具调用共享同一 set 实例
            "read_files": self._read_files,
            # Wave 3 (2026-05-04 真 spawn): Agent 工具按名查 factory
            "subagent_registry": self._subagent_registry_cache,
        })
        return ctx

    @classmethod
    def _reload_with_cwd(cls, cwd: str, active_plan: str | None = None) -> str:
        """跟 _load_prompt 同样从文件读, 但用实例 cwd + active_plan 重跑替换."""
        path = Path(_PROMPT_PATH)
        if not path.is_absolute():
            import omnicompany
            project_root = Path(omnicompany.__file__).resolve().parents[2]
            path = project_root / _PROMPT_PATH
        text = path.read_text(encoding="utf-8")
        subs = _build_substitutions(cwd, _DEFAULT_MODEL_ID, active_plan=active_plan)
        return text.format(**subs)


__all__ = ["NativeIdeAgent"]
