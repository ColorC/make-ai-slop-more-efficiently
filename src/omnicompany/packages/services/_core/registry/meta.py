# [OMNI] origin=claude-code domain=services/registry ts=2026-04-11T00:00:00Z
# [OMNI] material_id="material:core.registry.meta_type_system.type_registry.py"
"""
Registry 元类型系统 — MetaTypeRegistry

注册体系的核心哲学：
  "代码即注册"——写 class X(Router) / Format(id=...) 本身就是注册行为，
  scanner 是唯一的、权威的读取方，不存在旁路注册。

EntityTypeDef 定义了一种实体类型的所有元信息：
  - 如何在代码中识别（canonical_form）
  - 如何扫描出实例（scanner 由外部注入）
  - 未来质量字段的 schema（quality_fields，供 HealthArchive Phase 2 使用）

MetaTypeRegistry 是开放可扩展的：
  任何满足"稳定身份 + 独立质量面 + 演变主体 + 依赖表面"四条判据的
  新类型（如 knowledge），都可以通过 register_type() 动态加入，
  不需要修改注册核心代码。
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class EntityTypeDef:
    """描述一种可注册实体类型的元数据。"""

    name: str
    """类型名称（全局唯一，用于 entity_id 前缀，如 'router'）。"""

    display_name: str
    """人可读名称（如 'Router'）。"""

    canonical_form: str
    """人可读的"如何在代码中声明这种实体"说明。
    这是注册的唯一正典形式——不在此形式中声明的实体不在注册体系中。
    示例：'class X(Router) 继承自 Router/LLMRouter/AgentNodeLoop'
    """

    data_dir: str
    """在 data/registry/ 下的子目录名称（如 'router'）。"""

    quality_fields: dict[str, str] = field(default_factory=dict)
    """质量字段 schema（Phase 2 使用）：字段名 → 描述。
    空 dict 表示该类型的质量 schema 尚未定义。
    """

    registration_criteria: str = ""
    """为什么这种类型值得注册（第一性说明，用于文档和 self-check）。"""


class MetaTypeRegistry:
    """注册体系的元层：管理哪些实体类型可以被注册。

    设计为单例（通过模块级 `meta_registry` 对象使用）。
    """

    def __init__(self) -> None:
        self._types: dict[str, EntityTypeDef] = {}

    def register_type(self, typedef: EntityTypeDef) -> None:
        """注册一个新的实体类型。可在运行时动态调用（用于扩展）。"""
        if typedef.name in self._types:
            raise ValueError(
                f"Entity type '{typedef.name}' already registered. "
                f"Use update_type() to modify."
            )
        self._types[typedef.name] = typedef

    def update_type(self, typedef: EntityTypeDef) -> None:
        """更新已注册的类型定义（用于演变）。"""
        self._types[typedef.name] = typedef

    def get_type(self, name: str) -> EntityTypeDef:
        if name not in self._types:
            raise KeyError(f"Entity type '{name}' not registered.")
        return self._types[name]

    def list_types(self) -> list[str]:
        return list(self._types.keys())

    def has_type(self, name: str) -> bool:
        return name in self._types

    def all_types(self) -> list[EntityTypeDef]:
        return list(self._types.values())


# ── 模块级单例 ──────────────────────────────────────────────────────────────
meta_registry = MetaTypeRegistry()


# ── 内置五元类型注册 ─────────────────────────────────────────────────────────
# 注册顺序不影响功能，按依赖层次从底向上排列（Format 最底层，Pipeline 最顶层）

meta_registry.register_type(EntityTypeDef(
    name="format",
    display_name="Format",
    canonical_form=(
        "在任意 formats.py 文件中声明 Format(id='...', name='...', ...) 实例。"
        "id 全局唯一。不在 formats.py 中声明的不视为已注册 Format。"
    ),
    data_dir="format",
    quality_fields={
        "has_description": "description 字段是否存在且长度 ≥ 50 字符",
        "has_examples": "examples 字段是否有内容",
        "has_tags": "tags 字段是否声明了域标签",
        "parent_valid": "parent 字段引用的 Format 是否存在",
        "components_valid": "components 字段中的所有 Format 是否存在（composite 检查）",
    },
    registration_criteria=(
        "Format 是数据契约，是 Pipeline 节点间通信的'类型系统'。"
        "每个 Format 有独立的质量面（五要素），随版本演变，被多个 Router/Pipeline 依赖。"
    ),
))

meta_registry.register_type(EntityTypeDef(
    name="router",
    display_name="Router",
    canonical_form=(
        "在任意 .py 文件中定义继承自 Router / LLMRouter / AgentNodeLoop 的类。"
        "类名在 package 内唯一。私有类（_开头）不注册。"
    ),
    data_dir="router",
    quality_fields={
        "has_description": "DESCRIPTION 字段是否存在且长度 ≥ 50 字符",
        "has_fail_path": "run() 中是否有 return Verdict(kind=VerdictKind.FAIL, ...)",
        "format_in_literal": "FORMAT_IN 是否为字符串字面量（非 f-string/list）",
        "format_out_literal": "FORMAT_OUT 是否为字符串字面量",
        "run_is_async": "run() 是否为 async def（违反同步协议）",
        "grade": "Doctor 综合等级 A/B/C/D/F",
    },
    registration_criteria=(
        "Router 是计算单元，是 Pipeline 的基本构成块。"
        "每个 Router 有独立质量面（R-01~R-10），频繁修改，被 Pipeline 引用。"
    ),
))

meta_registry.register_type(EntityTypeDef(
    name="pipeline",
    display_name="Pipeline",
    canonical_form=(
        "在任意 pipeline*.py 文件中定义返回 TeamSpec 的函数（通常命名为 build_*_pipeline()）。"
        "Pipeline 名称 = 函数名去掉 build_ 前缀和 _pipeline 后缀。"
    ),
    data_dir="pipeline",
    quality_fields={
        "node_count": "Pipeline 中节点数",
        "maturity_distribution": "各 NodeMaturity 等级的节点数量分布",
        "has_isolated_nodes": "是否存在孤立节点（无出边且无入边）",
        "format_chain_valid": "Format 链是否完整（无断裂）",
    },
    registration_criteria=(
        "Pipeline 是确定性组合形式，将 Router 和 Format 编排为有向无环图。"
        "拓扑可静态分析，有独立的质量面（拓扑健康/节点成熟度），随节点增删而演变。"
    ),
))

meta_registry.register_type(EntityTypeDef(
    name="agent_loop",
    display_name="AgentLoop",
    canonical_form=(
        "在任意 .py 文件中定义继承自 AgentNodeLoop 的类（agent_loop 是 router 的特化，"
        "但质量面完全不同：运行时成功率/轮次 vs 静态拓扑健康，因此独立注册）。"
    ),
    data_dir="agent_loop",
    quality_fields={
        "max_turns": "max_turns 上限值",
        "tool_count": "tool_repertoire 中工具数量",
        "success_rate": "历史运行成功率（0.0~1.0，Phase 5 填入）",
        "avg_turns": "历史平均轮次（Phase 5 填入）",
    },
    registration_criteria=(
        "AgentLoop 是自主组合形式，拓扑动态决策，失败模式（max_turns 耗尽/工具幻觉）"
        "与 Pipeline 完全不同，质量标准是运行时统计而非静态拓扑，因此独立注册。"
    ),
))

meta_registry.register_type(EntityTypeDef(
    name="tool",
    display_name="Tool",
    canonical_form=(
        "在任意 .py 文件中用 @register_tool 装饰器注册，或在 tools.py 中显式声明 ToolDef(...)。"
        "（当前工具系统尚未标准化，Phase 1 先扫描 guarded_write 等已知模式。）"
    ),
    data_dir="tool",
    quality_fields={
        "has_description": "工具是否有用途描述",
        "has_error_handling": "是否有明确的错误处理",
    },
    registration_criteria=(
        "Tool 是外部能力适配器（文件写入、P4、LLM API 等），被 Router/AgentLoop 调用。"
        "有独立的可靠性/延迟/授权范围质量面，随外部接口变化而演变。"
    ),
))

meta_registry.register_type(EntityTypeDef(
    name="hook",
    display_name="Hook",
    canonical_form=(
        "在任意 .py 文件中以生命周期事件名称（pre_run / post_run / on_fail 等）"
        "注册的回调函数，或在 hooks.py 中显式声明。"
    ),
    data_dir="hook",
    quality_fields={
        "lifecycle_point": "附着的生命周期事件（pre_run/post_run/on_fail 等）",
        "has_error_handling": "是否有明确的错误处理（钩子异常不应阻断主流程）",
    },
    registration_criteria=(
        "Hook 是生命周期拦截器，附着在 Pipeline/Runner 的执行事件上。"
        "有独立的执行成功率质量面，随拦截逻辑变化而演变。"
    ),
))


# ── omnicompany 八种基础概念扩展 (2026-05-02 补 data + plan) ────────────────
# 用户原始需求 6.1 列 8 种基础概念: team/worker/material/plan,project/data/hook/agent/tool.
# 上面已注册 6 种 (format=material / router=worker / pipeline=team / agent_loop=agent / tool / hook),
# 这里补 data + plan 凑齐. 八种全部走同一 InstanceRegistry / RegistryQuery 接口.

meta_registry.register_type(EntityTypeDef(
    name="data",
    display_name="Data",
    canonical_form=(
        "data/<domain>/ 下面的内容性 .md / .yaml / .json 文件 (项目知识库 / 业务事实 / "
        "调研结果). 显式 `omni register data --content=<file>` 才视为已注册."
    ),
    data_dir="data",
    quality_fields={
        "has_omnimark_header": "OmniMark 头是否齐全 (5 字段: origin/ts/type/summary/why+tags)",
        "has_kind_tag": "tags 是否含 kind.* (data.fact / data.research / data.knowledge_base 等)",
        "size_bytes": "文件字节数 (size 监控)",
        "trace_id": "首次注册 session 的 trace_id (跟身份模块联动)",
    },
    registration_criteria=(
        "Data 是项目内的内容性资料 (区别于 Material 这种'流转中的数据契约'). "
        "稳定身份 = 文件路径; 独立质量面 = OmniMark 头 + 体积 + 引用数; "
        "演变主体 = 业务事实变更; 依赖表面 = 引用了哪些其他 data / material."
    ),
))

meta_registry.register_type(EntityTypeDef(
    name="template",
    display_name="Template",
    canonical_form=(
        "templates/<kind>/ 下面 6 件套 (向导.md / 注册件.yaml / 范本.py / 范本_prompt.md / "
        "骨架.py / 等) - 元模板层 (omnicompany 三层分类的层三). 每个模板对应一种 kind, "
        "给 omni new <kind> 复制为草稿用. OmniMark type=template."
    ),
    data_dir="template",
    quality_fields={
        "has_omnimark_header": "模板自身的 OmniMark 头 (origin / ts / type=template / summary / why / tags)",
        "kind_for": "本模板服务的 kind (agent / worker / tool / material / team / hook / data / plan)",
        "shape": "模板形态 (file 单文件 / folder 整目录)",
        "trace_id": "首次注册 session 的 trace_id",
    },
    registration_criteria=(
        "Template 是元模板, 跟 8 种基础概念正交 (它是'造概念实例的模具'). "
        "稳定身份 = 模板路径 + kind_for; 独立质量面 = 占位符齐 + 向导.md 跟实际范本对齐; "
        "演变主体 = 规范变了模板跟着变; 依赖表面 = templates/<kind>/ 内 6 件套互相引用."
    ),
))

meta_registry.register_type(EntityTypeDef(
    name="plan",
    display_name="Plan",
    canonical_form=(
        "docs/plans/<topic>/[YYYY-MM-DD]<NAME>/plan.md 主文件 + 同目录附属材料. "
        "目录形式注册, plan.md 必有, OmniMark 头 + 七节硬结构 (R-21 design_md_template)."
    ),
    data_dir="plan",
    quality_fields={
        "has_omnimark_header": "plan.md 的 OmniMark 头齐全",
        "has_seven_sections": "plan.md 七节硬结构 (状态/核心目的/核心接口/架构决策/数据流/已知局限/参考资料)",
        "binding_block_complete": "plan 规范 v1 的 binding 块齐 (workspace/packages/targets/applicable_standards/expected_completion/ttl_days)",
        "is_archived": "是否已归档到 _archive/",
        "trace_id": "首次注册 session 的 trace_id",
    },
    registration_criteria=(
        "Plan 是过程记录性产物 (区别于 Data 这种内容性 + DESIGN.md 这种就近性). "
        "稳定身份 = 目录路径 + plan_id; 独立质量面 = 七节齐 + binding 完整; "
        "演变主体 = plan 进度跟决策; 依赖表面 = binding 块的 packages/targets/applicable_standards."
    ),
))


# ── 元 IO 类型 (2026-05-02 加, 用户原始需求 6.6 — tool 操作绑定状态) ──────────────
# 用户原话: "所有的 I (输入观察) 和 O (输出操作) 都要统一再进行注册, 变为元 IO
# (语义原子化, 尺寸上可以再分但是语义上不再分的 IO) 再一次进行记录."
#
# 实施层骨架: registry type 加好, services/_core/meta_io/ 实施留下一阶段.
# 规范设计: docs/standards/cli/meta_io.md.

meta_registry.register_type(EntityTypeDef(
    name="external_pointer",
    display_name="External Pointer",
    canonical_form=(
        "二进制文件 / 不可改外部项目文件的中央指针注册. 物理形态 = sidecar JSON 文件位于 "
        "data/services/registry/external_pointers/<encoded_target_path>.json. "
        "sidecar JSON 内含 target_path + omnimark 等字段, 不动目标本身一字节."
    ),
    data_dir="external_pointer",
    quality_fields={
        "target_path": "实际指向的物理文件路径 (绝对或相对项目根)",
        "target_existence_check": "是否检查 target 真存在 (file_exists / dir_exists / skip_check)",
        "kind_inner": "目标内容的真实 omnicompany kind (worker / tool / data / 等), 跟外层 type=external_pointer 区分",
        "is_binary": "目标是否二进制 (true 不可读 / false 可读但是外部项目不能改)",
        "is_external_project": "是否在 omnicompany 项目根之外 (例 D:\\P4\\... / 其他 git repo)",
        "trace_id": "首次注册 session 的 trace_id",
    },
    registration_criteria=(
        "External Pointer 是不能写头文件类内容的指针注册. 适用: (1) 二进制 (.png / .pyc / .so / .xlsx), "
        "(2) 外部项目文件 (D:\\P4\\... 不能侵入修改), (3) 第三方依赖 (vendors/). "
        "稳定身份 = sidecar 路径 + target_path; 独立质量面 = sidecar JSON 完整 + target 仍存在; "
        "演变主体 = target 路径变 / target 内容变 (sidecar 不跟着改, 但要重新指); "
        "依赖表面 = sidecar 内 omnimark.tags 跟 deps 列出该 target 关联的其他 material."
    ),
))

meta_registry.register_type(EntityTypeDef(
    name="testmap",
    display_name="功能点-测试台账",
    canonical_form=(
        "各业务仓根 testmap.yaml(契约: services/_governance/testmap.py)。"
        "由 `omni testmap sync` 跑 discover_testmaps + verify_testmap 自动发现并写入,"
        "不走 omni new / omni register 手工通道。"
    ),
    data_dir="testmap",
    quality_fields={
        "covered": "verify 实测 status=covered 的功能点数",
        "gap": "verify 实测 status=gap 的功能点数(有 should 无测试锚)",
        "stale": "verify 实测 status=stale 的功能点数(锚文件/用例锚失效)",
        "gates_status": "各 gate 最近一次真跑的 green/red(attrs.gates)",
        "review_findings_count": "理论覆盖再评最近一次的 finding 数(attrs.review)",
    },
    registration_criteria=(
        "testmap 是功能点与测试锚的对照契约, 稳定身份 = app 名; 独立质量面 = "
        "covered/gap/stale 三态 + 门禁真跑结果 + 理论覆盖再评; 演变主体 = 功能点增删/测试锚变化; "
        "依赖表面 = testmap_path 指向的真实测试文件。registration_criteria: 由 "
        "omni testmap sync 自动发现注册, 不走 omni new/omni register 手工通道。"
    ),
))

meta_registry.register_type(EntityTypeDef(
    name="meta_io",
    display_name="Meta IO",
    canonical_form=(
        "在 services/_core/meta_io/ 下面 (或就近 services/<service>/meta_io/) 用 "
        "MetaIO(id='...', kind='read|write', target='...') 声明语义原子化的 I/O 单元. "
        "每个 tool 必须声明它消费 / 产出哪些 meta_io. 不在 meta_io 注册的 I/O 视为非法操作 (G4 锁的范围扩展)."
    ),
    data_dir="meta_io",
    quality_fields={
        "kind": "I/O 性质: 'read' (输入观察, 不改外部状态) / 'write' (输出操作, 改外部状态) / 'mutate' (读改一体)",
        "target_type": "目标资源类型 (file / api / db / process / network)",
        "is_atomic_semantic": "语义是否原子化 — 这条 meta_io 不能再按语义拆分 (例如 'read_csv_row' 是一个; 'read_csv_file_then_pick_row' 不是)",
        "side_effect_scope": "副作用范围 (local_file / git_remote / external_service / db_row 等)",
        "trace_id": "首次注册 session 的 trace_id",
    },
    registration_criteria=(
        "Meta IO 是 tool 层 IO 操作的原子单位. 稳定身份 = id + kind; "
        "独立质量面 = 副作用范围 + 状态可检查性; "
        "演变主体 = tool 实施细节变 (更高抽象包装) 但语义原子不变; "
        "依赖表面 = tool 声明的 consumed_meta_io / produced_meta_io 列表 + 状态检查 hook 关联的 meta_io."
    ),
))
