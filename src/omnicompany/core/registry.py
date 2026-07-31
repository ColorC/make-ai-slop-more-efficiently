# [OMNI] origin=human domain=omnicompany/core ts=2026-04-08T03:23:35Z
# [OMNI] material_id="material:omnicompany.core.registry.pipeline_registry.storage.py"
"""omnicompany.core.registry — 可执行 Team 运行时注册表（基础设施）

声明式 Team 注册。CLI 通过名称查表即可调度任何已注册 Team。
不含任何业务逻辑 — 只是一个字典和数据结构定义。

``PipelineEntry`` 仅为旧消费者保留兼容别名；新代码使用 ``TeamEntry``。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Callable

logger = logging.getLogger(__name__)


@dataclass
class CliArg:
    """管线接受的一个 CLI 参数声明。"""
    name: str                   # 参数名（--table, --versions 等）
    help: str = ""              # 帮助说明
    type: type = str            # 类型
    default: Any = None         # 默认值
    required: bool = False      # 是否必填
    is_flag: bool = False       # 是否布尔开关


@dataclass
class TeamEntry:
    """可执行 Team 的运行时注册条目。

    每个业务 Team 在自己的模块中创建一个 TeamEntry 并调用 register()。
    """
    name: str                                   # CLI 名称，如 "agent", "research-run"
    description: str                            # 人类可读描述
    domain: str                                 # 领域标识，用于 DB 路径分隔
    build_team: Callable[..., Any]          # () -> TeamSpec
    build_bindings: Callable[..., dict]         # (args) -> dict[str, Router]
    default_db_dir: str = "data/default"        # 默认 events.db 存放目录
    cli_args: list[CliArg] = field(default_factory=list)
    default_max_steps: int = 50                 # 默认最大步数
    aliases: tuple[str, ...] = ()               # 旧名/别名, 仅作 CLI 解析兼容

    # ── E1 (事件型引擎): 让 dispatch(name) 能按名字跑 MaterialDispatcher 形态的 team ──
    engine: str = "teamrunner"
    """执行引擎。
    - "teamrunner"(默认): build_team() 返回 TeamSpec, 走 TeamRunner 图引擎(原有全部行为不变)。
    - "event":          build_team() 返回 list[Router](worker 清单), 走 MaterialDispatcher 事件驱动。
    沉淀桥逆推出的事件型 team 用 "event", 从而无需先转成 TeamSpec 就能按名复用。"""
    entry_material: str | None = None
    """仅 engine="event" 用: 起跑的初始 material id。
    None 时从 worker 清单自动推导 —— 被某 worker 的 FORMAT_IN 消费、却无任何 worker 以 FORMAT_OUT
    产出的那块 material 即 source(从契约直接推得, 无需额外配置)。推不出唯一时须显式给。"""
    run_context: Callable[[dict], Any] | None = None
    """可选: 运行级上下文工厂 —— 接收 input_dict, 返回 context manager。
    dispatch() 在管线执行前进入、结束后退出(两种引擎都生效)。
    首个消费者 = 业务域多条内容路径 (阶段一 1-5, architecture §3.2 方案a):
    工厂返回 eternal_war_worktree(), 使 worker 的 active_eternal_war_root() 解析到
    隔离副本 —— 没有它, event 引擎裸挂会让 worker 静默回退到活基线直写。"""

    # ── 决策本体元数据 (plan=[2026-07-10]DECISION-ONTOLOGY 阶段二) ──────────────
    # 注册表=机检真源(when 硬匹配/规模数值只写这里);语义手册条目=语义真源(只指不复述)。
    when: dict[str, Any] | None = None
    """机检 when: 什么情境该跑这条管线。约定键:
    - "semantic": str  一句话语义描述(与手册条目 when.trigger.semantic 对齐)
    - "match_keys": list[str]  硬匹配键(路径 glob / 命令名 / 材料类型), 供机器预筛
    - "judge": "rule"|"statistical"|"llm"  判断类型
    None = 未声明(巡检点名补齐)。"""
    scale: dict[str, Any] | None = None
    """规模声明(接入面纪律 DEC-2026-07-10-002: 长管线声明规模+显式确认)。约定键:
    - "tier": "quick"|"short"|"long"  量级
    - "minutes": str  预期耗时区间(如 "5-20")
    - "cost": str  预期开销口径(LLM 调用量级/agent 数)
    条目与文档只指向这里, 不复述数值(合并清单#18)。"""
    confirm: bool = False
    """确认门: True 时 omni run 在启动前打印规模声明并要求显式确认
    (--yes 跳过; 非交互环境无 --yes 直接拒跑)。长管线必须 True。"""
    segments: tuple[dict[str, Any], ...] = ()
    """长管线小段快速入口(合并清单#17: 复用 --only 节点子集, 不造新机制)。
    每段约定键: {"name": str, "only": list[str] 节点子集, "desc": str}。"""
    book_refs: tuple[str, ...] = ()
    """指向语义手册条目锚点(docs/ontology/<文件>#<条目>)。
    双向引用完整性巡检用: 手册条目 projections 列管线名, 管线 book_refs 指回条目。"""


# 兼容旧 import；两者是同一个类对象，不是第二种注册条目。
PipelineEntry = TeamEntry


# ── 全局注册表 ──────────────────────────────────────────────────────────────

_REGISTRY: dict[str, TeamEntry] = {}


def register(entry: TeamEntry) -> None:
    """注册一个可执行 Team。重复注册同名 Team 会覆盖并发出警告。

    entry.aliases 内的旧名同时注册到 _REGISTRY (同一 TeamEntry 对象共享),
    保证 `get("workflow-factory")` 仍返回 team-builder 的 entry.
    """
    if entry.name in _REGISTRY:
        logger.warning("Team '%s' already registered, overwriting", entry.name)
    _REGISTRY[entry.name] = entry
    for alias in entry.aliases:
        if alias in _REGISTRY and _REGISTRY[alias] is not entry:
            logger.warning("Team alias '%s' clashes with existing entry, overwriting", alias)
        _REGISTRY[alias] = entry
    logger.debug("Registered team: %s (domain=%s, aliases=%s)", entry.name, entry.domain, entry.aliases)


def get(name: str) -> TeamEntry | None:
    """按名称查找可执行 Team。"""
    return _REGISTRY.get(name)


def get_or_raise(name: str) -> TeamEntry:
    """按名称查找可执行 Team，未找到则抛异常。"""
    entry = _REGISTRY.get(name)
    if entry is None:
        available = ", ".join(sorted(_REGISTRY.keys())) or "(none)"
        raise KeyError(
            f"Team '{name}' not found. Available: {available}"
        )
    return entry


def list_all(*, include_aliases: bool = False) -> list[TeamEntry]:
    """列出所有已注册可执行 Team（按名称排序）.

    aliases 共享同一 TeamEntry, 默认去重 (同一对象只返回一次).
    include_aliases=True 时按每个 registry key 返回 (可能有重复 entry).
    """
    if include_aliases:
        return sorted(_REGISTRY.values(), key=lambda e: e.name)
    seen: set[int] = set()
    out: list[TeamEntry] = []
    for entry in _REGISTRY.values():
        if id(entry) in seen:
            continue
        seen.add(id(entry))
        out.append(entry)
    return sorted(out, key=lambda e: e.name)


def names() -> list[str]:
    """列出所有已注册 Team 名称。"""
    return sorted(_REGISTRY.keys())


def discover() -> None:
    """自动发现并加载所有已知 Team 注册。

    委托给 omnicompany.core.pipelines.register_all()，
    该模块使用懒加载避免拉入重依赖。
    """
    try:
        from omnicompany.core.pipelines import register_all
        register_all()
    except Exception as e:
        logger.debug("discover failed: %s", e)

