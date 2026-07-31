# [OMNI] origin=ai-ide domain=services/_core/agent ts=2026-05-01T00:00:00Z type=router status=active agent=ai-ide-current
# OMNI-024 ALLOW: ConfigurableAgent is a framework base class, not a package router entrypoint.
# [OMNI] summary="ConfigurableAgent + AgentSpec, 配置驱动外包装, prompt 走外部 .md 文件"
# [OMNI] why="项目内 5+ 份 agent 实施 prompt 都硬编码在 Python 字符串里, 不是人类可读 material 不可配置. 这层外包装把 prompt / 工具 / 上下文触发器集中到 AgentSpec, 为后续 hook / context-trigger 集中管理留接口"
# [OMNI] tags=agent,configurable,framework,prompt-as-material
# [OMNI] material_id="material:core.agent.configurable_framework.spec_and_registry.py"
"""ConfigurableAgent — agent 配置驱动外包装.

继承 AgentNodeLoop. 默认走 SPEC 配置, 业务子类如需细粒度行为仍可继承 + override
(`build_prompt_builder` / `build_extract_result` / `build_tool_context` 等钩子).

核心机制:

1. **AgentSpec dataclass** — 集中所有可配置项, 含用户原始需求 6.2.1 列的 13 项
   全部字段 (注册信息 / LLM / IO materials / prompt / 工具 / 工作区 / 门禁 /
   上下文触发器 / 红绿测试).

2. **prompt 从外部 .md 文件加载** — `SPEC.prompt_path` 指向独立 markdown 文件.
   人类可读 material, 也是可配置 material. 启动时载入, 跑模板替换 (Python str.format).

3. **工具按字符串名查 TOOL_REGISTRY** — 不再 import 类 hardcode, 走注册表.
   后续新工具注册到 TOOL_REGISTRY 即可被任何 agent 用配置引用.

4. **__init_subclass__ 派生 NODE_PROMPT / TOOL_ROUTERS** — 子类设 SPEC 即可,
   基类自动从 SPEC 派生父类 AgentNodeLoop 需要的两个类属性.

继承约定:
- 默认: 业务 agent 子类 = `class XxxAgent(ConfigurableAgent): SPEC = AgentSpec(...)` 一行
- 高级: 业务 agent 子类同时 override `build_prompt_builder` 等 (走配置 + 局部代码并存)
- 禁: 跳过 SPEC 直接硬编码 NODE_PROMPT (回退到 AgentNodeLoop 旧用法)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, ClassVar, Mapping, Sequence

from omnicompany.packages.services._core.agent.loop import AgentNodeLoop
from omnicompany.packages.services._core.agent.routers.single_tool import (
    SingleToolRouter,
    GlobRouter,
    GrepRouter,
    ReadFileRouter,
    EditRouter,
    ListDirRouter,
)
from omnicompany.packages.services._core.agent.routers.web_fetch import WebFetchRouter
from omnicompany.packages.services._core.agent.routers.write_file import WriteFileRouter


# ──────────────────────────────────────────────────────────────────────
# 工具注册表 — 字符串名 → SingleToolRouter 子类
# ──────────────────────────────────────────────────────────────────────

TOOL_REGISTRY: dict[str, type[SingleToolRouter]] = {}


def register_tool(name: str, router_cls: type[SingleToolRouter]) -> None:
    """注册一个工具到 TOOL_REGISTRY.

    重复注册同一名字 + 同一类是 no-op; 注册到不同类报错.
    """
    existing = TOOL_REGISTRY.get(name)
    if existing is not None and existing is not router_cls:
        raise ValueError(
            f"tool {name!r} already registered with {existing.__name__}, "
            f"refused to override with {router_cls.__name__}"
        )
    TOOL_REGISTRY[name] = router_cls


def _register_builtin_tools() -> None:
    """登记 framework 自带 6 个标准工具."""
    register_tool("glob", GlobRouter)
    register_tool("grep", GrepRouter)
    register_tool("read_file", ReadFileRouter)
    register_tool("edit", EditRouter)
    register_tool("list_dir", ListDirRouter)
    register_tool("write_file", WriteFileRouter)
    register_tool("web_fetch", WebFetchRouter)


_register_builtin_tools()


def auto_register_singletool_subclasses() -> int:
    """扫所有已 import 的 SingleToolRouter 子类自动 register_tool.

    业务侧 (例 demogame/team_business/workers/feishu_tools 等) import 自己 router 后
    调一次本函数, 把所有有 TOOL_NAME 的子类都登记到 TOOL_REGISTRY.

    返回新登记的 tool 数. 已存在的不重复.
    """
    def _all_subclasses(cls):
        s = set()
        for sub in cls.__subclasses__():
            s.add(sub)
            s |= _all_subclasses(sub)
        return s

    added = 0
    for cls in _all_subclasses(SingleToolRouter):
        tool_name = cls.__dict__.get("TOOL_NAME")
        if not tool_name:
            continue
        if tool_name in TOOL_REGISTRY:
            continue
        try:
            register_tool(tool_name, cls)
            added += 1
        except (ValueError, TypeError):
            pass
    return added


# ──────────────────────────────────────────────────────────────────────
# AgentSpec — 配置 dataclass
# ──────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class AgentSpec:
    """agent 配置 spec, 用户原始需求 6.2.1 列了 13 项配置全包含.

    频段约定:
    - 必填字段无默认值 (id / name)
    - 选填字段显式默认值 (空数组 / 空字典 / 空字符串). 用户原始需求 6.2.1.13:
      "所有模板的所有配置不允许不填, 只能显式使用默认值"
    """

    # ── 1. omnicompany 注册信息 ──
    id: str                                              # 唯一 ID (例 "demogame.business_researcher")
    name: str                                            # 类名风格 (例 "IgameBusinessResearcher")
    domain: str = ""                                     # 业务域 (例 "demogame")
    parent_worker_kind: str = "agent"                    # 跟工人规范对齐 (R-19 智能体子类型)
    registry_namespace: str = "services.agent.instances"  # 注册中心命名空间

    # ── 2. LLM 配置 ──
    llm_model: str = "qwen-3.6-plus"                     # omnicompany 铁律 1: 唯一模型
    llm_temperature: float = 0.3                         # 调研类任务低温度减幻觉
    llm_max_tokens: int = 16000                          # 单轮 max_tokens (16k 起步, 2026-05-03 跟 LLMClient + Claude Code escalated 64k 同向). LLMClient _continue_if_truncated_* 自动续写撞 length 接着生, 4000 / 12000 都是反模式
    llm_max_turns: int | None = None                     # 活动驱动；仅明确有界任务设整数上限
    llm_timeout_seconds: int | None = None               # None 使用统一传输层无返回保护
    llm_extra_body: Mapping[str, Any] = field(default_factory=dict)  # 原生模型参数，如 thinking_budget

    # ── 3. 产出 material 列表 ──
    output_materials: tuple[str, ...] = ()               # agent 可能产出的 material 全清单
    primary_output: str = ""                             # 主产出 (触发下游)

    # ── 4. 触发性 material ──
    trigger_materials: tuple[str, ...] = ()              # 哪些 material 激活本 agent
    trigger_mode: str = "any"                            # any (任一触发) / all (全部触发)

    # ── 5. 响应范围 (material 来源) ──
    accepted_input_materials: tuple[str, ...] = ()       # agent 内部能消费的 material
    forbidden_input_materials: tuple[str, ...] = ()      # 禁订阅 (例如自家产出 / GT 答案)

    # ── 6. 用户输入 ──
    user_input_template: str = ""                        # 执行期 LLM 看到的用户消息模板
    user_input_required_fields: tuple[str, ...] = ()     # 必填字段名清单

    # ── 7. 系统 prompt (核心: 外部 .md 文件) ──
    prompt_path: str = ""                                # 相对项目根 (例 "docs/agent_prompts/xxx.md") 或绝对路径
    prompt_substitutions: Mapping[str, Any] = field(default_factory=dict)
    # str.format 替换用. prompt 文件内 {key} 占位符跟此 dict 配对.

    # ── 8. 工具列表 (字符串名走 TOOL_REGISTRY) ──
    tools: tuple[str, ...] = ()
    # 例: ("glob", "grep", "read_file", "list_dir") — 三位一体推荐 ≤ 10

    # ── 9. 工作区 ──
    workspace: Mapping[str, Any] = field(default_factory=dict)
    # {name, write_prefixes, read_prefixes, bash_cwd_prefixes}

    # ── 10. 门禁列表 (敏感操作需要审批) ──
    gates: Sequence[Mapping[str, Any]] = ()
    # 每条 dict 含: kind (human_blocking / auto_block) / trigger / block_message

    # ── 11. 上下文触发器列表 (执行期插入额外 context 的钩子) ──
    context_triggers: Sequence[Mapping[str, Any]] = ()
    # 每条 dict 含: on (触发条件) / inject_context (注入哪份 context)

    # ── 12. 自定义代码开关 ──
    allow_custom_code: bool = False
    # 配置式 agent 默认禁自定义代码. 业务子类 override build_xxx 钩子时此 flag = True.

    # ── 13. 红绿测试基线 ──
    test_baseline: Mapping[str, Any] = field(default_factory=dict)
    # {green_samples: [...], red_samples: [...], gradient_samples: [...]}


# ──────────────────────────────────────────────────────────────────────
# ConfigurableAgent — 配置驱动外包装基类
# ──────────────────────────────────────────────────────────────────────


class ConfigurableAgent(AgentNodeLoop):
    """配置驱动 agent. 子类只需声明 `SPEC = AgentSpec(...)` 即可跑.

    示例最小子类 (业务侧)::

        class IgameBusinessResearcher(ConfigurableAgent):
            SPEC = AgentSpec(
                id="demogame.business_researcher",
                name="IgameBusinessResearcher",
                domain="demogame",
                prompt_path="docs/agent_prompts/demogame_business_researcher.md",
                tools=("glob", "grep", "read_file", "list_dir"),
                output_materials=("demogame.business-understanding-doc",),
                primary_output="demogame.business-understanding-doc",
                trigger_materials=("demogame.business-research-request",),
            )

    框架自动:
    1. 从 `SPEC.prompt_path` 载入 prompt 文件作 NODE_PROMPT
    2. 从 `SPEC.tools` (字符串名) 经 TOOL_REGISTRY 解析为 TOOL_ROUTERS (类列表)
    3. 跑 prompt_substitutions 替换占位符

    业务侧需要更细粒度行为时 (例如自定义 PromptBuilder 注入 input_data 字段),
    继承本类同时 override `build_prompt_builder` 等钩子. 此时 `SPEC.allow_custom_code`
    应当显式置 True 表明本子类含自定义代码.
    """

    SPEC: ClassVar[AgentSpec | None] = None

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        spec = self._resolve_spec()
        kwargs.setdefault("model", spec.llm_model)
        kwargs.setdefault("llm_timeout_seconds", spec.llm_timeout_seconds)
        kwargs.setdefault("llm_max_tokens", spec.llm_max_tokens)
        kwargs.setdefault("llm_extra_body", dict(spec.llm_extra_body))
        super().__init__(*args, **kwargs)

    @classmethod
    def _resolve_spec(cls) -> AgentSpec:
        if cls.SPEC is None:
            raise RuntimeError(
                f"{cls.__name__}.SPEC is None. ConfigurableAgent 子类必须设 "
                f"`SPEC = AgentSpec(...)` 类属性."
            )
        return cls.SPEC

    @classmethod
    def _load_prompt(cls) -> str:
        """从 SPEC.prompt_path 载入 prompt 文件.

        相对路径相对项目根 (omnicompany 包根的上两级).
        绝对路径直接用.
        """
        spec = cls._resolve_spec()
        if not spec.prompt_path:
            return ""
        path = Path(spec.prompt_path)
        if not path.is_absolute():
            import omnicompany
            project_root = Path(omnicompany.__file__).resolve().parents[2]
            path = project_root / spec.prompt_path
        if not path.exists():
            raise FileNotFoundError(
                f"{cls.__name__}: prompt 文件不存在 — {path} "
                f"(配置 SPEC.prompt_path={spec.prompt_path!r})"
            )
        text = path.read_text(encoding="utf-8")
        if spec.prompt_substitutions:
            try:
                text = text.format(**dict(spec.prompt_substitutions))
            except KeyError as e:
                raise RuntimeError(
                    f"{cls.__name__}: prompt 模板替换失败, 占位符 {e!s} 在 "
                    f"prompt_substitutions 找不到. 文件: {path}"
                )
        return text

    @classmethod
    def _resolve_tool_routers(cls) -> list[type[SingleToolRouter]]:
        """从 SPEC.tools (字符串名) 经 TOOL_REGISTRY 解析为 Router 类列表."""
        spec = cls._resolve_spec()
        resolved: list[type[SingleToolRouter]] = []
        for name in spec.tools:
            router_cls = TOOL_REGISTRY.get(name)
            if router_cls is None:
                raise ValueError(
                    f"{cls.__name__}: 工具 {name!r} 不在 TOOL_REGISTRY. "
                    f"已注册: {sorted(TOOL_REGISTRY.keys())}. "
                    f"业务工具需先 register_tool({name!r}, YourRouter)."
                )
            resolved.append(router_cls)
        return resolved

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        # SPEC 未设的 (如本类自身或中间抽象子类) 跳过派生
        if cls.SPEC is None:
            return
        # 派生 NODE_PROMPT (子类未显式覆盖时)
        # SPEC.prompt_path 显式给出但文件找不到 → 立即报错 (不静默 fallback 让用户改 SPEC)
        if not cls.NODE_PROMPT and cls.SPEC.prompt_path:
            cls.NODE_PROMPT = cls._load_prompt()
        # 派生 TOOL_ROUTERS (子类未显式覆盖时)
        if not cls.TOOL_ROUTERS:
            cls.TOOL_ROUTERS = cls._resolve_tool_routers()
        # 派生 FORMAT_IN / FORMAT_OUT / FORMAT_IN_MODE (子类未显式覆盖时)
        # 让 MaterialDispatcher 看见订阅图直接激活 ConfigurableAgent 子类 (2026-05-05 加)
        if not getattr(cls, "FORMAT_IN", None):
            triggers = cls.SPEC.trigger_materials
            if len(triggers) == 1:
                cls.FORMAT_IN = triggers[0]
            elif len(triggers) > 1:
                cls.FORMAT_IN = list(triggers)
                # SPEC.trigger_mode "any" → "or", "all" → "and"
                mode = (cls.SPEC.trigger_mode or "any").lower()
                cls.FORMAT_IN_MODE = "or" if mode == "any" else "and"
        if not getattr(cls, "FORMAT_OUT", None):
            cls.FORMAT_OUT = cls.SPEC.primary_output or ""


__all__ = [
    "AgentSpec",
    "ConfigurableAgent",
    "TOOL_REGISTRY",
    "register_tool",
    "auto_register_singletool_subclasses",
]
