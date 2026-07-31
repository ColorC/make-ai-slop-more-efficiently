# [OMNI] origin=omnicompany domain=workflow_factory/formats.py ts=2026-04-20T00:00:00Z
# [OMNI] material_id="material:core.team_builder.material_registry.definitions.py"
"""workflow_factory — Material 定义 (原 Format 类 · Clean Migration 2026-04-20).

每个 Material 是一个有意义的中间产物, 可以被独立检查和复用.
Material description 五要素 (SKILL §2.1): 内容语义 / 字段含义 / 上游承诺 / 下游用途 / 最小样例.

Material 链呈语义递进:
  requirement_raw → requirement → format_chain → node_plan → project_skeleton
  → (compile/lap/route/integration 验证都用 project_skeleton 单主干 + reports 容器)
  → done

每经过一个验证步骤, 产物的语义身份都变了, 用不同的 Material 表达.

Material kind 标注 (F-19):
  wf.requirement_raw                → kind.source    (外部触发, 无 producer Worker)
  wf.requirement                    → kind.internal
  wf.format_chain                   → kind.internal
  wf.node_plan                      → kind.internal
  wf.framework_context_loader.input → kind.internal  (composite fan-in)
  wf.node_plan_augmented            → kind.internal
  wf.code_gen_state                 → kind.internal
  wf.project_skeleton               → kind.internal  (单主干 + reports 容器, 验证节点共用)
  wf.done                           → kind.sink      (最终产物 · 无 consumer Worker)
"""

from omnicompany.packages.services._core.omnicompany import Material
from omnicompany.protocol.format import FormatRegistry

# ═══════════════════════════════════════════════════════════
# F0: 原始需求输入
# ═══════════════════════════════════════════════════════════

WF_REQUIREMENT_RAW = Material(
    id="wf.requirement_raw",
    name="原始工作流需求",
    description=(
        "用户的自然语言需求描述文本。"
        "验证标准：非空字符串，至少包含目标描述。"
        "下游用途：req_analyzer 解析为结构化需求。"
        "Kind: source (外部触发 · 无 producer Worker · 见 F-19)。"
    ),
    parent="requirement",
    json_schema={
        "type": "object",
        "properties": {
            "text": {"type": "string", "description": "自然语言需求描述"},
        },
        "required": ["text"],
    },
    tags=["wf", "domain.workflow_factory", "stage.input", "kind.source"],
)

# ═══════════════════════════════════════════════════════════
# F1: 结构化需求
# ═══════════════════════════════════════════════════════════

WF_REQUIREMENT = Material(
    id="wf.requirement",
    name="结构化工作流需求",
    description=(
        "从自然语言解析出的结构化需求规格，包含目标、领域、约束、验证需求、"
        "错误场景、用户交互点。"
        "验证标准：goal/domain/input_description/output_description 非空，"
        "verification_requirements 至少 1 项。"
        "下游用途：format_designer 据此设计 Format 链。"
        "Kind: internal (Worker 间流转 · 见 F-19)。"
    ),
    parent="requirement",
    json_schema={
        "type": "object",
        "properties": {
            "goal": {"type": "string"},
            "domain": {"type": "string"},
            "input_description": {"type": "string"},
            "output_description": {"type": "string"},
            "constraints": {"type": "array", "items": {"type": "string"}},
            "reference_pipelines": {"type": "array", "items": {"type": "string"}},
            "verification_requirements": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "stage": {"type": "string"},
                        "method": {"type": "string", "enum": ["compiler", "test", "llm", "schema"]},
                        "criteria": {"type": "string"},
                    },
                },
            },
            "error_scenarios": {"type": "array", "items": {"type": "string"}},
            "needs_user_interaction": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["goal", "domain", "input_description", "output_description"],
    },
    tags=["wf", "domain.workflow_factory", "stage.design", "kind.internal"],
)

# ═══════════════════════════════════════════════════════════
# F2: Format 链设计
# ═══════════════════════════════════════════════════════════

WF_FORMAT_CHAIN = Material(
    id="wf.format_chain",
    name="Format 链设计",
    description=(
        "为目标工作流设计的 Format 继承链。每个 Format 包含 id/name/description/"
        "parent/json_schema/semantic_preconditions/granted_tags_on_pass。"
        "验证标准：所有 Format id 语义化（禁止机械编号），description 含三要素，"
        "chain 中相邻 Format 通过 via_node 连接。"
        "下游用途：node_planner 据此为每条转换设计 Router 节点。"
        "Kind: internal (Worker 间流转 · 见 F-19)。"
    ),
    parent="requirement",
    json_schema={
        "type": "object",
        "properties": {
            "formats": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "string"},
                        "name": {"type": "string"},
                        "description": {"type": "string"},
                        "parent": {"type": ["string", "null"]},
                        "json_schema": {"type": "object"},
                    },
                    "required": ["id", "name", "description"],
                },
            },
            "chain": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "from": {"type": "string"},
                        "to": {"type": "string"},
                        "via_node": {"type": "string"},
                    },
                },
            },
            "requirement_context": {
                "type": "object",
                "description": "上游需求的关键上下文（domain、goal），显式传递而非走私",
                "properties": {
                    "domain": {"type": "string"},
                    "goal": {"type": "string"},
                },
            },
        },
        "required": ["formats", "chain"],
    },
    tags=["wf", "domain.workflow_factory", "stage.design", "kind.internal"],
)

# ═══════════════════════════════════════════════════════════
# F3: 节点执行计划
# ═══════════════════════════════════════════════════════════

WF_NODE_PLAN = Material(
    id="wf.node_plan",
    name="节点执行计划",
    description=(
        "为目标工作流设计的完整节点规划。每个节点包含 id/kind/validator_kind/"
        "format_in/format_out/description/implementation_hint/error_routes/"
        "needs_user_inquiry/verification_binding。"
        "node_planner 同时从 goal 推导出 pipeline_name（下游 framework_context_loader "
        "用作 target_package_path 末段）和 domain（沿用上游需求的 domain 字段）。"
        "验证标准：每个 SOFT 节点有 FAIL 路由，description >= 50 字符，"
        "所有 format_in/format_out 在 format_chain 中存在。"
        "下游用途：framework_context_loader 按 pipeline_name 推导 target package, "
        "code_generator 据 nodes/edges 生成管线代码。"
        "注意：下游若需 requirement_context, 应通过 fan-in 直连 wf.format_chain, "
        "不由本 Material 搭便车（F-15 合规）。"
        "Kind: internal (Worker 间流转 · 见 F-19)。"
    ),
    parent="requirement",
    json_schema={
        "type": "object",
        "properties": {
            "nodes": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "string"},
                        "kind": {"type": "string", "enum": ["ANCHOR", "TRANSFORMER"]},
                        "validator_kind": {"type": "string", "enum": ["HARD", "SOFT"]},
                        "format_in": {"type": "string"},
                        "format_out": {"type": "string"},
                        "description": {"type": "string"},
                        "implementation_hint": {"type": "string"},
                        "error_routes": {"type": "object"},
                        "needs_user_inquiry": {"type": "boolean"},
                        "verification_binding": {"type": ["string", "null"]},
                    },
                    "required": ["id", "kind", "validator_kind", "format_in", "format_out", "description"],
                },
            },
            "edges": {"type": "array"},
            "feedback_loops": {"type": "array"},
            "pipeline_name": {
                "type": "string",
                "description": "node_planner 从 requirement.goal 推导的管线名（sanitize 后作 target_package_path 末段）。真产出, 非透传。",
            },
            "domain": {
                "type": "string",
                "description": "node_planner 从上游 requirement_context 取出并记录的 domain 名。真产出（node_planner 真的在用它分流）, 非透传。",
            },
        },
        "required": ["nodes", "edges"],
    },
    tags=["wf", "domain.workflow_factory", "stage.design", "kind.internal"],
)

# ═══════════════════════════════════════════════════════════
# F3-CTX: framework_context_loader 的 composite 输入契约 (M2.β v2, 2026-04-19)
#
# framework_context_loader 真正消费 wf.format_chain.requirement_context,
# 但这个字段不属于 wf.node_plan 语义（node_plan 不产不改它）。用 composite
# Material 显式声明 fan-in 两路:
#   - wf.node_plan        (来自 node_plan_auditor 的 PASS)
#   - wf.format_chain     (直连来自 format_designer 的 PASS)
# Router.run() 通过 input_data[<component_id>] 访问, runner composite mode 自动
# 用 format_out id 作 key (见 runtime/exec/runner.py::_merge_inputs use_format_keys)。
# ═══════════════════════════════════════════════════════════

WF_FRAMEWORK_CONTEXT_LOADER_INPUT = Material(
    id="wf.framework_context_loader.input",
    name="框架上下文注入节点的 composite 输入",
    description=(
        "composite Material: 把 framework_context_loader 需要的两路上游契约化,"
        "避免让 wf.node_plan 搭便车承载非自己语义的 requirement_context。"
        "\n\n"
        "【components】"
        "- wf.node_plan: 来自 node_plan_auditor 的 PASS, 带 nodes/edges/pipeline_name/domain"
        "- wf.format_chain: 来自 format_designer 的 PASS, 带 formats/chain/requirement_context"
        "\n\n"
        "【消费姿态】Router 以 FORMAT_IN='wf.framework_context_loader.input' 单字声明;"
        "runner composite mode 把 input_data 组织为 {'wf.node_plan': {...}, 'wf.format_chain': {...}}。"
        "\n\n"
        "【下游用途】framework_context_loader 从 wf.node_plan 取 pipeline_name/domain,"
        "从 wf.format_chain 取 requirement_context 组装 target_package_path; 输出 wf.node_plan_augmented。"
        "Kind: internal (composite fan-in 中间态 · 见 F-19)。"
    ),
    parent="wf.node_plan",
    components=[
        "wf.node_plan",
        "wf.format_chain",
    ],
    tags=["wf", "domain.workflow_factory", "stage.design", "composite", "kind.internal"],
)

# ═══════════════════════════════════════════════════════════
# F3a: 注入框架真源码后的节点执行计划
# ═══════════════════════════════════════════════════════════

WF_NODE_PLAN_AUGMENTED = Material(
    id="wf.node_plan_augmented",
    name="带框架源码的节点执行计划",
    description=(
        "WF_NODE_PLAN 经过 framework_context_loader 注入真实框架源码后的版本。"
        "在 WF_NODE_PLAN 全部字段之上，必须包含 framework_context 字段，"
        "内含 inspect.getsource 拉取的 Router/Verdict/LLMClient/AnchorSpec 等真源码"
        "和至少一份 MATURE 参考域（selftest）的 routers.py/pipeline.py/formats.py 全文。"
        "\n\n"
        "【为什么单独定义一个 Material】"
        "code_generator 是代码生成类 SOFT 节点，skill §3.3 要求必须注入框架真源码和参考实现，"
        "否则 LLM 会靠记忆幻觉 import 路径和 API signature。此 Material 把"
        "'框架上下文是否已注入'变成类型层面的前置条件——没经过 framework_context_loader 的"
        "node_plan 无法进入 code_generator。"
        "\n\n"
        "【字段】"
        "- 继承 WF_NODE_PLAN 全部字段（nodes/edges/feedback_loops/requirement_context 等）"
        "- framework_context: 真源码字典，必含键："
        "  · router_base_src: Router 基类 inspect.getsource 源码"
        "  · verdict_dataclass_src: Verdict dataclass 定义源码"
        "  · verdictkind_enum_src: VerdictKind enum 成员源码"
        "  · llmclient_init_sig: LLMClient.__init__ 签名字符串"
        "  · anchor_spec_src: AnchorSpec 类定义源码"
        "  · pipeline_node_src: TeamNode 类定义源码"
        "  · pipeline_spec_src: TeamSpec 类定义源码"
        "  · nodekind_enum_src: NodeKind enum 源码"
        "  · format_class_src: Format 类定义源码"
        "  · ref_selftest_routers: selftest/routers.py 全文"
        "  · ref_selftest_pipeline: selftest/pipeline.py 全文"
        "  · ref_selftest_formats: selftest/formats.py 全文"
        "  · target_package_path: 目标生成包的完整 import 路径"
        "\n\n"
        "【上游承诺】node_plan 已通过 node_planner 产出（包含 nodes/edges）"
        "\n\n"
        "【下游用途】code_generator 在生成每个 .py 文件时必须从 framework_context 读取"
        "对应的真源码，**禁止**凭记忆写 import 路径或 API 调用方式。"
        "\n\n"
        "【granted_tag】通过此 Material 验证的节点必须授予 `framework-ctx-injected` tag。"
        "Kind: internal (Worker 间流转 · 见 F-19)。"
    ),
    parent="wf.node_plan",
    json_schema={
        "type": "object",
        "properties": {
            "nodes": {"type": "array"},
            "edges": {"type": "array"},
            "feedback_loops": {"type": "array"},
            "requirement_context": {"type": "object"},
            "framework_context": {
                "type": "object",
                "description": "inspect.getsource 拉取的框架真源码 + 参考域实现全文",
                "properties": {
                    "router_base_src": {"type": "string"},
                    "verdict_dataclass_src": {"type": "string"},
                    "verdictkind_enum_src": {"type": "string"},
                    "llmclient_init_sig": {"type": "string"},
                    "anchor_spec_src": {"type": "string"},
                    "pipeline_node_src": {"type": "string"},
                    "pipeline_spec_src": {"type": "string"},
                    "nodekind_enum_src": {"type": "string"},
                    "format_class_src": {"type": "string"},
                    "ref_selftest_routers": {"type": "string"},
                    "ref_selftest_pipeline": {"type": "string"},
                    "ref_selftest_formats": {"type": "string"},
                    "target_package_path": {"type": "string"},
                },
                "required": [
                    "router_base_src", "verdict_dataclass_src", "verdictkind_enum_src",
                    "llmclient_init_sig", "anchor_spec_src", "pipeline_node_src",
                    "pipeline_spec_src", "nodekind_enum_src", "format_class_src",
                    "ref_selftest_routers", "ref_selftest_pipeline", "ref_selftest_formats",
                    "target_package_path",
                ],
            },
        },
        "required": ["nodes", "edges", "framework_context"],
    },
    tags=["wf", "domain.workflow_factory", "stage.design", "framework-ctx-injected", "kind.internal"],
)


# ═══════════════════════════════════════════════════════════
# F3b: 增量代码生成中间态 (P7.2 SCATTER 拆分新增)
# ═══════════════════════════════════════════════════════════

WF_CODE_GEN_STATE = Material(
    id="wf.code_gen_state",
    name="增量代码生成中间态",
    description=(
        "code_gen_formats / code_gen_pipeline / code_gen_routers / code_gen_run 四个子节点"
        "之间流转的中间态。继承 wf.node_plan_augmented 全部字段，新增可累加的 files 字典。"
        "每经过一个子节点，files 多一个键 (formats.py / pipeline.py / routers.py / run.py)。"
        "\n\n"
        "【为什么单独定义】GAP §1.2-A 指出原 code_generator 在一个 Router 内顺序跑 4 次 LLM "
        "违反单节点纯粹性, 任何一步出错只能整块 retry。本 Material 把 4 个生成步骤拆成独立节点, "
        "失败时只 retry 该步骤, 同时让 trace-view 看得到每一步的中间产物。"
        "\n\n"
        "【字段】"
        "- 继承 wf.node_plan_augmented 全部字段 (nodes/edges/framework_context/...)"
        "- files: dict[str, str] — 已生成的文件名 → 源码内容, 增量累加"
        "- pipeline_name: str — 目标管线名 (在 code_gen_formats 节点初始化)"
        "- package_path: str — 目标包路径 (在 code_gen_formats 节点初始化)"
        "\n\n"
        "【上游承诺】framework_context 已注入, requirement_context.domain/goal 已确定"
        "【下游用途】code_gen_run 完成后转为 wf.project_skeleton 供 compile_checker 验证"
        "Kind: internal (Worker 间流转 · 见 F-19)。"
    ),
    parent="wf.node_plan_augmented",
    json_schema={
        "type": "object",
        "properties": {
            "nodes": {"type": "array"},
            "edges": {"type": "array"},
            "framework_context": {"type": "object"},
            "files": {
                "type": "object",
                "description": "已生成的文件名 → 源码内容, 增量累加",
            },
            "pipeline_name": {"type": "string"},
            "package_path": {"type": "string"},
        },
        "required": ["files", "framework_context"],
    },
    tags=["wf", "domain.workflow_factory", "stage.generation", "kind.internal"],
)


# ═══════════════════════════════════════════════════════════
# F4: 生成的代码骨架
# ═══════════════════════════════════════════════════════════

WF_PROJECT_SKELETON = Material(
    id="wf.project_skeleton",
    name="工作流代码骨架",
    description=(
        "code_gen_run 输出的完整管线代码。包含 package_path（包路径）和 "
        "files（文件名→内容映射，至少含 formats.py/routers.py/pipeline.py/run.py），"
        "以及一个增量累加的 reports 字典（compile/lap_audit/error_route/integration 等）。"
        "\n\n"
        "P7.3 重构 (2026-04-09): 单主干 Material + reports 容器 + granted_tags 模式 (SKILL §2.3)。"
        "compile_checker / error_route_auditor / integration_tester 都用本 Material "
        "作为 format_in 和 format_out, 把自家报告写进 reports[key], 用 Verdict.granted_tags 累加状态。"
        "(2026-07-03 批4: lap_verifier 九维检查器废止, 已从验证链摘除。)"
        "废弃了 wf.compiled_skeleton / wf.audited_skeleton / wf.route_checked_skeleton / wf.tested_skeleton "
        "四个克隆 Material (代码本体未变只是验收印章, GAP §1.2-A 反模式)。"
        "\n\n"
        "验证标准：所有 files 通过 py_compile, package_path 合法。reports 是可选字段。"
        "下游用途：compile_checker → error_route_auditor/integration_tester → finalizer。"
        "Kind: internal (验证节点共用单主干 · Worker 间流转 · 见 F-19)。"
    ),
    parent="requirement",
    json_schema={
        "type": "object",
        "properties": {
            "package_path": {"type": "string"},
            "files": {
                "type": "object",
                "description": "文件名 → 文件内容",
            },
            "pipeline_name": {"type": "string"},
            "reports": {
                "type": "object",
                "description": (
                    "增量累加的验证报告字典 (P7.3 reports container)。"
                    "key: compile / lap_audit / error_route / integration / deterministic_fix。"
                    "auto_fixer 从这里读历史失败报告而不再瞎子修复 (GAP §1.2-H)。"
                ),
            },
        },
        "required": ["package_path", "files", "pipeline_name"],
    },
    tags=["wf", "domain.workflow_factory", "stage.generation", "kind.internal"],
)

# ═══════════════════════════════════════════════════════════
# F5-F9: 已删除 (Fix 7, 2026-04-09)
#
# 老的 wf.compile_report / wf.lap_audit_report / wf.error_route_report /
# wf.test_report / wf.fix_patch 5 个 Material 都不再被任何节点作为 format_in
# 或 format_out 消费。P7.3 改造后报告全部塞进 wf.project_skeleton.reports 字典,
# 修复后的 skeleton 也直接回流 wf.project_skeleton 本身, 所以这 5 个 Material
# 彻底变成死代码, 随 F4a-d (skeleton 克隆链) 一起清理。
# ═══════════════════════════════════════════════════════════

# ═══════════════════════════════════════════════════════════
# F10: 最终产物
# ═══════════════════════════════════════════════════════════

WF_DONE = Material(
    id="wf.done",
    name="工作流最终产物",
    description=(
        "通过全部验证的工作流代码 + 质量总结报告。包含 pipeline_name、"
        "package_path、quality_summary（编译/LAP/路由/测试四项得分）。"
        "验证标准：所有检查 passed=True。"
        "下游用途：注册到全局 pipeline registry，可直接使用。"
        "Kind: sink (最终产物 · 无 consumer Worker · 见 F-19)。"
    ),
    parent="requirement",
    json_schema={
        "type": "object",
        "properties": {
            "pipeline_name": {"type": "string"},
            "package_path": {"type": "string"},
            "quality_summary": {
                "type": "object",
                "properties": {
                    "compile": {"type": "boolean"},
                    "lap_audit": {"type": "boolean"},
                    "error_routes": {"type": "boolean"},
                    "integration": {"type": "boolean"},
                },
            },
            "registered": {"type": "boolean"},
        },
        "required": ["pipeline_name", "package_path", "quality_summary"],
    },
    tags=["wf", "domain.workflow_factory", "stage.output", "kind.sink"],
)

# ═══════════════════════════════════════════════════════════
# A3 (2026-04-23) · agent-first material 设计 (8 类 = 7 artifact + 1 中间)
# ───────────────────────────────────────────────────────────
# 7 类 artifact: 产出 package 周围的本体文件 (DESIGN.md / .omni/* 等)
# 1 类中间: intent_analysis (IntentAnalyzerWorker 产 · TeamArchitect 消费)
#          中间 material 不落 package 本体, 只进 EventBus (agent 间传递)
# ═══════════════════════════════════════════════════════════
# 用户 2026-04-23 明示: team_builder 必须产出多个中间 material 作为
# 整个 team 的设计产物 · 入 EventBus 注册 + 本体留 package 旁.
#
# 每类 material 有两份存在:
#   (a) Format 注册 (本段声明) — 进 EventBus 事件流
#   (b) 本体文件 — 生成的 package 内对应路径 (见各 material description)
# ═══════════════════════════════════════════════════════════

TB_REQUEST_TRIGGER = Material(
    id="team_builder.material.request_trigger",
    name="CLI 触发载荷",
    description=(
        "CLI 层面的触发 material, 承载 --text 自然语言请求. "
        "由 OriginRequestLoader 消费, 包装成完整 origin_request. "
        "kind.source (runner 从 input_dict 注入, 无 producer Worker)."
    ),
    parent="requirement",
    json_schema={
        "type": "object",
        "properties": {
            "text": {"type": "string", "description": "自然语言请求"},
            "target_package_path": {
                "type": "string",
                "description": "可选的精确 Team package 目录；提供后下游必须原样复用",
            },
        },
        "required": ["text"],
    },
    tags=["team_builder", "stage.input", "kind.source", "cli_entry"],
)


TB_INTENT_ANALYSIS = Material(
    id="team_builder.material.intent_analysis",
    name="用户意图结构化分析",
    description=(
        "IntentAnalyzerWorker 从 origin_request 提炼的结构化意图: 目标 Team 的 domain / purpose / "
        "scope / key_capabilities / constraints. 由 TeamArchitectWorker 消费规划总体骨架. "
        "kind.internal (agent 中间产物 · 不落产出 package 本体, 只进 EventBus)."
    ),
    parent="requirement",
    json_schema={
        "type": "object",
        "properties": {
            "domain": {"type": "string", "description": "业务领域识别 (例: 'software_engineering' / 'data_pipeline' / 'game_config')"},
            "purpose": {"type": "string", "description": "用户要解决的核心问题 1-2 句"},
            "scope": {
                "type": "object",
                "properties": {
                    "in_scope": {"type": "array", "items": {"type": "string"}},
                    "out_of_scope": {"type": "array", "items": {"type": "string"}},
                },
            },
            "key_capabilities": {
                "type": "array",
                "items": {"type": "string"},
                "description": "必须有的能力清单 (将映射到 Worker 设计)",
            },
            "constraints": {
                "type": "array",
                "items": {"type": "string"},
                "description": "硬约束 (铁律 / 外部合同 / 成本等)",
            },
            "ambiguities": {
                "type": "array",
                "items": {"type": "string"},
                "description": "明显需要人类澄清的歧义点 (直接返回当前 agent 对话)",
            },
            "target_package_path": {
                "type": "string",
                "description": "从入口确定性透传的精确 Team package 目录",
            },
        },
        "required": ["domain", "purpose", "key_capabilities"],
    },
    tags=["team_builder", "stage.analysis", "kind.internal", "agent_first"],
)

TB_ORIGIN_REQUEST = Material(
    id="team_builder.material.origin_request",
    name="原需求存档",
    description=(
        "用户发起 team_builder 构建时的原始自然语言请求 + 元信息 (时间/触发者/tags). "
        "本体位置: <generated_pkg>/.omni/origin_request.md. "
        "下游用途: team_design / worker_design 回溯需求来源; agent-first 观测阶段对比实际产物 vs 原始意图. "
        "Kind: source (产出链起点 · 无 producer Worker)."
    ),
    parent="requirement",
    json_schema={
        "type": "object",
        "properties": {
            "request_text": {"type": "string", "description": "用户原始请求文本"},
            "triggered_at": {"type": "string", "description": "触发时间 ISO8601"},
            "triggered_by": {"type": "string", "description": "触发者 (L1/L2 标识)"},
            "tags": {"type": "array", "items": {"type": "string"}},
            "body_path": {"type": "string", "description": ".omni/origin_request.md 本体路径"},
            "target_package_path": {
                "type": "string",
                "description": "入口显式给出的精确 Team package 目录",
            },
        },
        "required": ["request_text", "triggered_at", "body_path"],
    },
    tags=["team_builder", "stage.input", "kind.source", "agent_first"],
)

TB_TEAM_DESIGN = Material(
    id="team_builder.material.team_design",
    name="Team 总体设计",
    description=(
        "整个生成 Team 的架构设计 (七节 DESIGN.md · 遵 OMNI-034). "
        "包含: 职责边界 / 节点拓扑 / 数据流 / 已知局限 / 未来方向. "
        "本体位置: <generated_pkg>/DESIGN.md. "
        "下游用途: Guardian patrol 校验结构 / 新人阅读入口 / team_builder 后续演化参考."
    ),
    parent="doc",
    json_schema={
        "type": "object",
        "properties": {
            "team_id": {"type": "string"},
            "team_name": {"type": "string"},
            "target_package_path": {
                "type": "string",
                "description": (
                    "唯一部署位置；必须是 packages/services/<bucket>/<team_name>/ "
                    "或 packages/domains/<domain>/<team_name>/"
                ),
            },
            "design_path": {"type": "string"},
            "sections": {"type": "array", "items": {"type": "string"}, "minItems": 7},
            "node_count": {"type": "integer"},
            "material_count": {"type": "integer"},
            "workers_skeleton": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "worker_name": {"type": "string"},
                        "impl_type": {
                            "type": "string",
                            "enum": ["HARD", "SOFT", "AGENT", "EXISTING"],
                        },
                        "brief": {"type": "string"},
                        "position_id": {"type": "string"},
                        "binding_ref": {"type": "string"},
                        "binding_kwargs": {"type": "object"},
                    },
                    "required": ["worker_name", "impl_type", "brief", "position_id"],
                },
            },
            "positions": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "string"},
                        "name": {"type": "string"},
                        "responsibilities": {
                            "type": "array",
                            "items": {"type": "string"},
                            "minItems": 1,
                        },
                        "non_responsibilities": {
                            "type": "array",
                            "items": {"type": "string"},
                            "minItems": 1,
                        },
                        "activation": {
                            "type": "string",
                            "enum": ["active", "on_demand"],
                        },
                        "activation_evidence_refs": {
                            "type": "array",
                            "items": {"type": "string"},
                        },
                        "context_refs": {
                            "type": "array",
                            "items": {"type": "string"},
                        },
                        "benchmark_refs": {
                            "type": "array",
                            "items": {"type": "string"},
                        },
                        "exit_conditions": {
                            "type": "array",
                            "items": {"type": "string"},
                        },
                    },
                    "required": [
                        "id",
                        "name",
                        "responsibilities",
                        "non_responsibilities",
                        "activation",
                        "activation_evidence_refs",
                        "context_refs",
                        "benchmark_refs",
                        "exit_conditions",
                    ],
                },
            },
            "generation": {
                "type": "object",
                "properties": {
                    "method": {"const": "team_builder"},
                    "builder_id": {"const": "team-builder"},
                    "request_ref": {"type": "string"},
                    "source_refs": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                    "parent_team_id": {"type": "string"},
                },
                "required": [
                    "method",
                    "builder_id",
                    "request_ref",
                    "source_refs",
                ],
            },
        },
        "required": [
            "design_path",
            "team_id",
            "team_name",
            "target_package_path",
            "sections",
            "workers_skeleton",
            "positions",
            "generation",
        ],
    },
    tags=["team_builder", "stage.design", "kind.internal", "agent_first"],
)

TB_TEAM_REFERENCES = Material(
    id="team_builder.material.team_references",
    name="Team 设计参考资料清单",
    description=(
        "生成 Team 时引用的外部参考: standards 文档 / similar teams / skills / prior plans. "
        "每条含源路径 + 引用原因. 本体位置: <generated_pkg>/.omni/references.yaml. "
        "下游用途: 可审计 (知道为什么这样设计), 后续精炼参考."
    ),
    parent="doc",
    json_schema={
        "type": "object",
        "properties": {
            "references": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "source_path": {"type": "string"},
                        "reason": {"type": "string"},
                        "kind": {"type": "string", "enum": ["standard", "similar_team", "skill", "plan", "memory"]},
                    },
                    "required": ["source_path", "reason", "kind"],
                },
            },
            "body_path": {"type": "string"},
        },
        "required": ["references", "body_path"],
    },
    tags=["team_builder", "stage.design", "kind.internal"],
)

TB_WORKER_DESIGN = Material(
    id="team_builder.material.worker_design",
    name="Worker 设计 (单条)",
    description=(
        "生成 Team 中单个 Worker 的设计规格: 职责 / FORMAT_IN / FORMAT_OUT / 实现类型 (HARD/SOFT/agent). "
        "本体位置: <generated_pkg>/workers/<w>/DESIGN.md 或 worker docstring. "
        "下游用途: 代码生成器生成 Worker 代码 / Guardian OMNI-034f Worker 规范化校验."
    ),
    parent="doc",
    json_schema={
        "type": "object",
        "properties": {
            "worker_id": {"type": "string"},
            "format_in": {"type": "string"},
            "format_out": {"type": "string"},
            "impl_type": {
                "type": "string",
                "enum": ["HARD", "SOFT", "AGENT", "EXISTING"],
            },
            "description": {"type": "string"},
            "body_path": {"type": "string"},
        },
        "required": ["worker_id", "format_in", "format_out", "impl_type"],
    },
    tags=["team_builder", "stage.design", "kind.internal"],
)

TB_MATERIAL_DESIGN = Material(
    id="team_builder.material.material_design",
    name="Material 设计 (单条)",
    description=(
        "生成 Team 中单个 Material 的设计规格: id / 语义 / json_schema / 上游 producer / 下游 consumer. "
        "本体位置: <generated_pkg>/materials/<m>.md 或 formats.py 中 Material 实例 docstring. "
        "下游用途: Material registry 注册 / 类型契约校验 / 跨 Worker 数据流追踪."
    ),
    parent="doc",
    json_schema={
        "type": "object",
        "properties": {
            "material_id": {"type": "string"},
            "semantic": {"type": "string"},
            "schema": {"type": "object"},
            "producer_worker": {"type": "string"},
            "consumer_workers": {"type": "array", "items": {"type": "string"}},
            "body_path": {"type": "string"},
        },
        "required": ["material_id", "semantic", "schema"],
    },
    tags=["team_builder", "stage.design", "kind.internal"],
)

TB_AGENT_WORKER_DESIGN = Material(
    id="team_builder.material.agent_worker_design",
    name="Agent Worker 设计 (单条)",
    description=(
        "生成 Team 中 agent 形态 Worker 的特殊说明: loop 类型 / tools 清单 / budget / prompt 模板. "
        "与 HARD/SOFT Worker 不同, agent worker 含 AgentNodeLoop, 需单独设计文档. "
        "本体位置: <generated_pkg>/workers/<aw>/agent.md. "
        "下游用途: AgentNodeLoop 构造 / 工具权限审批 / prompt 注入."
    ),
    parent="doc",
    json_schema={
        "type": "object",
        "properties": {
            "worker_id": {"type": "string"},
            "loop_type": {"type": "string", "description": "AgentNodeLoop 子类名"},
            "tools": {"type": "array", "items": {"type": "string"}},
            "budget": {"type": "object", "properties": {"max_turns": {"type": "integer"}}},
            "prompt_template_path": {"type": "string"},
            "body_path": {"type": "string"},
        },
        "required": ["worker_id", "loop_type", "tools"],
    },
    tags=["team_builder", "stage.design", "kind.internal", "agent_first"],
)

TB_WORKSPACE_DESIGN = Material(
    id="team_builder.material.workspace_design",
    name="Workspace 设计",
    description=(
        "生成 Team 所在 package 的 workspace 声明: write_prefixes (紧) / read_prefixes (宽) / bash_cwd_prefixes. "
        "每 package 对自己的子域 arch 固定, 防架构漂移污染 (用户 2026-04-23 明示). "
        "本体位置: <generated_pkg>/.omni/workspace.py (导出 `workspace = Workspace(...)`). "
        "下游用途: ServiceBus 构造时加载, 限 agent 读写范围."
    ),
    parent="doc",
    json_schema={
        "type": "object",
        "properties": {
            "name": {"type": "string"},
            "write_prefixes": {"type": "array", "items": {"type": "string"}},
            "read_prefixes": {"type": "array", "items": {"type": "string"}},
            "bash_cwd_prefixes": {"type": "array", "items": {"type": "string"}},
            "body_path": {"type": "string"},
        },
        "required": ["name", "write_prefixes", "body_path"],
    },
    tags=["team_builder", "stage.design", "kind.internal", "workspace"],
)

# ═══════════════════════════════════════════════════════════
# V2 · Phase 2/4-7 新加 7 类 material (待对应 Worker 实装)
# 详细用途 + workflow 位置见 `.omni/build_workflow.md`
# ═══════════════════════════════════════════════════════════

TB_SCALE_ASSESSMENT = Material(
    id="team_builder.material.scale_assessment",
    name="规模研判",
    description=(
        "ScaleAssessorWorker 产出 · 综合 intent+refs 判目标 Team 规模 (small/medium/large) "
        "+ 是否需拆子 team + 拆分维度. 路由器: size=small/medium 直入 TeamArchitect; "
        "size=large 进 DecompositionPlanner. kind.internal."
    ),
    parent="requirement",
    json_schema={
        "type": "object",
        "properties": {
            "size": {"type": "string", "enum": ["small", "medium", "large"]},
            "recommend_decompose": {"type": "boolean"},
            "decompose_axis": {
                "type": ["string", "null"],
                "enum": ["by_capability", "by_domain", "by_phase", None],
            },
            "rationale": {"type": "string"},
            "estimated_worker_count": {"type": "integer"},
            "estimated_material_count": {"type": "integer"},
        },
        "required": ["size", "recommend_decompose", "rationale"],
    },
    tags=["team_builder", "stage.decompose", "kind.internal", "agent_first"],
)

TB_DECOMPOSITION_PLAN = Material(
    id="team_builder.material.decomposition_plan",
    name="大需求拆分方案",
    description=(
        "DecompositionPlannerWorker 产出 (仅 size=large 激活) · 把原需求拆成若干子 team + "
        "声明子 team 间契约 material. 下游对每个 sub_team 递归启动 team-builder. kind.internal."
    ),
    parent="requirement",
    json_schema={
        "type": "object",
        "properties": {
            "sub_teams": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string"},
                        "purpose": {"type": "string"},
                        "input_contract": {"type": "string", "description": "入 contract material id"},
                        "output_contract": {"type": "string", "description": "出 contract material id"},
                    },
                    "required": ["name", "purpose"],
                },
                "minItems": 2,
            },
            "inter_team_contracts": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "producer": {"type": "string"},
                        "consumer": {"type": "string"},
                        "material": {"type": "string"},
                        "semantics": {"type": "string"},
                    },
                    "required": ["producer", "consumer", "material", "semantics"],
                },
            },
        },
        "required": ["sub_teams", "inter_team_contracts"],
    },
    tags=["team_builder", "stage.decompose", "kind.internal", "agent_first"],
)

TB_WORKER_DESIGN_DETAILED = Material(
    id="team_builder.material.worker_design_detailed",
    name="Worker 深化设计 (单条)",
    description=(
        "WorkerDesignerWorker × N 产出 · 每 Worker 独立上下文产一份: FORMAT_IN/OUT schema + "
        "impl_type (HARD/SOFT/AGENT/EXISTING) + routes + prompt、rule 或既有绑定引用. "
        "kind.internal (N 份 fan-out)."
    ),
    parent="doc",
    json_schema={
        "type": "object",
        "properties": {
            "worker_id": {"type": "string"},
            "impl_type": {
                "type": "string",
                "enum": ["HARD", "SOFT", "AGENT", "EXISTING"],
            },
            "format_in": {"oneOf": [{"type": "string"}, {"type": "array", "items": {"type": "string"}}]},
            "format_in_mode": {"type": ["string", "null"], "enum": ["and", "or", None]},
            "format_out": {"type": "string"},
            "routes": {
                "type": "object",
                "properties": {
                    "PASS": {"type": "object"},
                    "FAIL": {"type": "object"},
                    "PARTIAL": {"type": "object"},
                },
            },
            "prompt_template": {"type": ["string", "null"]},
            "rule_spec": {"type": ["string", "null"]},
            "binding_ref": {"type": ["string", "null"]},
            "binding_kwargs": {"type": "object"},
            "context_sources": {
                "type": "array",
                "items": {"type": "string"},
                "description": "F-15 诚实: 必须从哪些 material/文件读",
            },
            "output_token_budget": {"type": "integer"},
            "hallucination_risks": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["worker_id", "impl_type", "format_in", "format_out", "routes"],
    },
    tags=["team_builder", "stage.design", "kind.internal", "agent_first"],
)

TB_MATERIAL_DESIGN_DETAILED = Material(
    id="team_builder.material.material_design_detailed",
    name="Material 深化设计 (单条)",
    description=(
        "MaterialDesignerWorker × M 产出 · 每 Material 独立上下文产一份: json_schema + "
        "description 五要素 + lifecycle + producer/consumer. kind.internal (M 份 fan-out)."
    ),
    parent="doc",
    json_schema={
        "type": "object",
        "properties": {
            "material_id": {"type": "string"},
            "parent": {"type": "string"},
            "json_schema": {"type": "object"},
            "producer": {"type": "string", "description": "producer worker_id"},
            "consumers": {"type": "array", "items": {"type": "string"}},
            "lifecycle": {"type": "string", "enum": ["source", "internal", "sink"]},
            "description_5elems": {
                "type": "object",
                "properties": {
                    "content_semantic": {"type": "string"},
                    "field_meaning": {"type": "string"},
                    "upstream_promise": {"type": "string"},
                    "downstream_use": {"type": "string"},
                    "minimal_sample": {"type": "string"},
                },
                "required": ["content_semantic", "field_meaning", "upstream_promise", "downstream_use"],
            },
        },
        "required": ["material_id", "json_schema", "lifecycle", "description_5elems"],
    },
    tags=["team_builder", "stage.design", "kind.internal", "agent_first"],
)

TB_WORKSPACE_SPEC = Material(
    id="team_builder.material.workspace_spec",
    name="Workspace 规范化声明",
    description=(
        "WorkspaceDesignerWorker 产出 · HARD 规则 · 从已验证 target_package_path 推 `docs/standards/workspace.md` "
        "合规 workspace.yaml 内容. 对应生成 package 的 .omni/workspace.yaml 本体. kind.internal."
    ),
    parent="doc",
    json_schema={
        "type": "object",
        "properties": {
            "name": {"type": "string"},
            "write_prefixes": {"type": "array", "items": {"type": "string"}, "minItems": 2},
            "read_prefixes": {"oneOf": [{"type": "string"}, {"type": "array", "items": {"type": "string"}}]},
            "bash_cwd_prefixes": {"type": "array", "items": {"type": "string"}},
            "target_package_path": {"type": "string"},
        },
        "required": ["name", "write_prefixes", "bash_cwd_prefixes", "target_package_path"],
    },
    tags=["team_builder", "stage.design", "kind.internal", "workspace"],
)

TB_CONTRACT_AUDIT = Material(
    id="team_builder.material.contract_audit",
    name="Worker-Material 契约静态审计",
    description=(
        "ContractAuditorWorker 产出 · HARD · 跨 Worker FORMAT_IN/OUT 连接性静态审计 "
        "(P-13 充分性 + F-15 诚实). 列 orphan/dangling/composite_fan_ins/source_materials/"
        "sink_materials. FAIL → 回到 Phase 3/4/4' RETRY. kind.internal."
    ),
    parent="doc",
    json_schema={
        "type": "object",
        "properties": {
            "connections": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "producer_worker": {"type": "string"},
                        "format_out": {"type": "string"},
                        "consumer_worker": {"type": "string"},
                        "format_in": {"type": "string"},
                        "ok": {"type": "boolean"},
                        "issue": {"type": "string"},
                    },
                    "required": ["producer_worker", "consumer_worker", "ok"],
                },
            },
            "orphan_workers": {"type": "array", "items": {"type": "string"}},
            "dangling_materials": {"type": "array", "items": {"type": "string"}},
            "composite_fan_ins": {"type": "array", "items": {"type": "object"}},
            "source_materials": {"type": "array", "items": {"type": "string"}},
            "sink_materials": {"type": "array", "items": {"type": "string"}},
            "overall_ok": {"type": "boolean"},
        },
        "required": ["connections", "overall_ok"],
    },
    tags=["team_builder", "stage.validate", "kind.internal"],
)

# ═══════════════════════════════════════════════════════════
# V3.2 CodeGenerator 子 team · 9 个分形 material (2026-04-24)
# 每个"必做产物"拆独立 material → 对应独立 Worker (HARD 或 SOFT)
# feedback_100pct_required_goes_to_skeleton: 6 纯模板 HARD + 2 SOFT (code/design_md) + 1 orchestrator
# ═══════════════════════════════════════════════════════════

TB_FORMATS_PY = Material(
    id="team_builder.material.formats_py",
    name="生成的 formats.py 代码",
    description=(
        "内容语义: 目标 team 的 Material 定义文件源码 (单 str). "
        "字段含义: rel_path 固定 'formats.py', content 为含 OMNI 头 + imports + Material × M 实例化 + register_formats 函数的完整 Python 源. "
        "上游承诺: FormatsFileGenerator (HARD · 纯模板) 依 material_design_detailed list 渲染, 不调 LLM. "
        "下游用途: CodeAggregator 合进 code_package.files['formats.py']. "
        "最小样例: \"# [OMNI] ...\\nfrom ...import Material\\nMAT1 = Material(id='...')\\ndef register_formats(r): r.register(MAT1)\". "
        "kind.internal."
    ),
    parent="doc",
    json_schema={
        "type": "object",
        "properties": {
            "rel_path": {"type": "string", "const": "formats.py"},
            "content": {"type": "string", "minLength": 100},
        },
        "required": ["rel_path", "content"],
    },
    tags=["team_builder", "stage.codegen", "kind.internal", "file_artifact"],
)

TB_TEAM_PY = Material(
    id="team_builder.material.team_py",
    name="生成的 team.py 代码",
    description=(
        "内容语义: 目标 team 的 TeamSpec 声明文件源码 (单 str). "
        "字段含义: content 含 OMNI 头 + imports (TeamSpec/TeamNode/TeamEdge/...) + build_team() 函数返回 TeamSpec(nodes=..., edges=...). "
        "上游承诺: TeamFileGenerator (HARD · 纯模板) 依 team_design.workers_skeleton + 各 worker 的 routes 渲染 nodes/edges. "
        "下游用途: CodeAggregator 合进 code_package.files['team.py']. "
        "最小样例: \"def build_team():\\n  nodes=[TeamNode(id='w1',...)]\\n  return TeamSpec(nodes=nodes, edges=[...])\". "
        "kind.internal."
    ),
    parent="doc",
    json_schema={
        "type": "object",
        "properties": {
            "rel_path": {"type": "string", "const": "team.py"},
            "content": {"type": "string", "minLength": 100},
        },
        "required": ["rel_path", "content"],
    },
    tags=["team_builder", "stage.codegen", "kind.internal", "file_artifact"],
)

TB_RUN_PY = Material(
    id="team_builder.material.run_py",
    name="生成的 run.py 代码",
    description=(
        "内容语义: 目标 team 的 build_bindings 函数文件源码 (单 str). "
        "字段含义: content 含 OMNI 头 + 动态 import (from .workers import WorkerClass × N) + build_bindings(input_dict=None) → dict[str, Worker]. "
        "上游承诺: RunFileGenerator (HARD · 纯模板) 依 worker_design_detailed list 渲染 worker_id → ClassName() 映射. "
        "下游用途: CodeAggregator 合进 code_package.files['run.py']. "
        "最小样例: \"def build_bindings(input_dict=None):\\n  return {'w1': FooWorker(), 'w2': BarWorker()}\". "
        "kind.internal."
    ),
    parent="doc",
    json_schema={
        "type": "object",
        "properties": {
            "rel_path": {"type": "string", "const": "run.py"},
            "content": {"type": "string", "minLength": 80},
        },
        "required": ["rel_path", "content"],
    },
    tags=["team_builder", "stage.codegen", "kind.internal", "file_artifact"],
)

TB_PKG_INIT_PY = Material(
    id="team_builder.material.pkg_init_py",
    name="生成的 __init__.py 代码",
    description=(
        "内容语义: 目标 team 顶层包 __init__.py 源码 (单 str). "
        "字段含义: content 含 OMNI 头 + docstring + 可选的 re-export (from .team import build_team 等). "
        "上游承诺: PackageInitGenerator (HARD · 样板) 依 team_name 产简单样板, 不含业务逻辑. "
        "下游用途: CodeAggregator 合进 code_package.files['__init__.py']. "
        "最小样例: \"# [OMNI] origin=... type=config\\n'''package docstring'''\\nfrom .team import build_team\". "
        "kind.internal."
    ),
    parent="doc",
    json_schema={
        "type": "object",
        "properties": {
            "rel_path": {"type": "string", "const": "__init__.py"},
            "content": {"type": "string", "minLength": 30},
        },
        "required": ["rel_path", "content"],
    },
    tags=["team_builder", "stage.codegen", "kind.internal", "file_artifact"],
)

TB_WORKERS_INIT_PY = Material(
    id="team_builder.material.workers_init_py",
    name="生成的 workers/__init__.py 代码",
    description=(
        "内容语义: 目标 team workers 子包 __init__.py 源码 (单 str). "
        "字段含义: content 含 OMNI 头 + from .<name> import <WorkerClass> × N + ALL_WORKERS list + __all__ 列表. "
        "上游承诺: WorkersInitGenerator (HARD · 纯模板) 依 worker_design_detailed list 渲染 per-worker import + export. "
        "下游用途: CodeAggregator 合进 code_package.files['workers/__init__.py']. "
        "最小样例: \"from .w1 import W1Worker\\nfrom .w2 import W2Worker\\nALL_WORKERS=[W1Worker,W2Worker]\". "
        "kind.internal."
    ),
    parent="doc",
    json_schema={
        "type": "object",
        "properties": {
            "rel_path": {"type": "string", "const": "workers/__init__.py"},
            "content": {"type": "string", "minLength": 50},
        },
        "required": ["rel_path", "content"],
    },
    tags=["team_builder", "stage.codegen", "kind.internal", "file_artifact"],
)

TB_WORKSPACE_YAML = Material(
    id="team_builder.material.workspace_yaml",
    name="生成的 .omni/workspace.yaml 配置",
    description=(
        "内容语义: 目标 team 的 workspace 声明 yaml 文件内容 (单 str). "
        "字段含义: content 为 yaml.safe_dump(workspace_spec dict) 的结果, 含 name/write_prefixes/read_prefixes/bash_cwd_prefixes/target_package_path. "
        "上游承诺: WorkspaceYamlGenerator (HARD · 纯 yaml dump) 依 workspace_spec material 直接序列化. "
        "下游用途: CodeAggregator 合进 code_package.files['.omni/workspace.yaml']. "
        "最小样例: \"name: foo\\nwrite_prefixes:\\n  - data/foo/\\nbash_cwd_prefixes:\\n  - /\". "
        "kind.internal."
    ),
    parent="doc",
    json_schema={
        "type": "object",
        "properties": {
            "rel_path": {"type": "string", "const": ".omni/workspace.yaml"},
            "content": {"type": "string", "minLength": 20},
        },
        "required": ["rel_path", "content"],
    },
    tags=["team_builder", "stage.codegen", "kind.internal", "file_artifact"],
)

TB_WORKER_CODE_FILE = Material(
    id="team_builder.material.worker_code_file",
    name="生成的单 Worker 业务代码",
    description=(
        "内容语义: 单个 Worker 的完整 Python 实现 (单 str · 每 sub-agent 一份). "
        "字段含义: worker_id 对应 worker_design_detailed.worker_id; rel_path 形如 'workers/<name>.py'; content 含 OMNI 头 + imports + class <Name>Worker(Worker) + run() 方法真实现. "
        "上游承诺: _WorkerCodeSingleAgent (SOFT · per worker 独立 LLM 调用) 产出, 含 ServiceBus 铁律 lint 过. "
        "下游用途: WorkerCodeOrchestrator 汇总为 worker_code_files_bundle · 再被 CodeAggregator 合进 code_package.files. "
        "最小样例: {\"worker_id\":\"foo\",\"rel_path\":\"workers/foo.py\",\"content\":\"# [OMNI] ...\\nclass FooWorker(Worker):\\n  def run(self,input_data):return Verdict(...)\"}. "
        "kind.internal."
    ),
    parent="doc",
    json_schema={
        "type": "object",
        "properties": {
            "worker_id": {"type": "string"},
            "rel_path": {"type": "string"},
            "content": {"type": "string", "minLength": 100},
            "lint_issues": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["worker_id", "rel_path", "content"],
    },
    tags=["team_builder", "stage.codegen", "kind.internal", "file_artifact", "agent_first"],
)

TB_WORKER_CODE_FILES_BUNDLE = Material(
    id="team_builder.material.worker_code_files_bundle",
    name="所有 Worker 代码汇总 bundle",
    description=(
        "内容语义: WorkerCodeOrchestrator 产 · N 个 worker_code_file 合并结果. "
        "字段含义: files 为 {rel_path: content} dict (key 形如 'workers/foo.py'); success_count/fail_count 记录 orchestrator 产出成功率; lint_summary 聚合所有 ServiceBus 反模式问题. "
        "上游承诺: WorkerCodeOrchestrator (Orchestrator · asyncio.gather) 对每 worker_design_detailed 并行跑 _WorkerCodeSingleAgent. "
        "下游用途: CodeAggregator 合进 code_package.files (保持 rel_path). "
        "最小样例: {\"files\":{\"workers/foo.py\":\"...\",\"workers/bar.py\":\"...\"},\"success_count\":2,\"fail_count\":0}. "
        "kind.internal."
    ),
    parent="doc",
    json_schema={
        "type": "object",
        "properties": {
            "files": {
                "type": "object",
                "description": "rel_path → content",
                "additionalProperties": {"type": "string"},
            },
            "success_count": {"type": "integer"},
            "fail_count": {"type": "integer"},
            "lint_summary": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["files", "success_count"],
    },
    tags=["team_builder", "stage.codegen", "kind.internal", "agent_first"],
)

TB_DESIGN_MD = Material(
    id="team_builder.material.design_md",
    name="生成的 DESIGN.md 文档",
    description=(
        "内容语义: 目标 team 的 OMNI-034 七节 DESIGN.md 文档内容 (单 str). "
        "字段含义: content 含七节规范名: 状态 / 核心目的 / 核心接口 / 架构决策 / 数据流 / 拓扑 / 已知局限 / 参考资料. "
        "上游承诺: DesignMdGenerator (SOFT · 骨架预填章节标题 · LLM 仅填各节内容, 骨架规范化后兜底合并). "
        "下游用途: CodeAggregator 合进 code_package.files['DESIGN.md']. "
        "最小样例: \"# DESIGN\\n\\n## 状态\\n...\\n## 核心目的\\n...\\n## 参考资料\\n- docs/...\". "
        "kind.internal."
    ),
    parent="doc",
    json_schema={
        "type": "object",
        "properties": {
            "rel_path": {"type": "string", "const": "DESIGN.md"},
            "content": {"type": "string", "minLength": 100},
            "missing_sections": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["rel_path", "content"],
    },
    tags=["team_builder", "stage.codegen", "kind.internal", "file_artifact", "agent_first"],
)

TB_CODE_REVIEW_REPORT = Material(
    id="team_builder.material.code_review_report",
    name="代码交叉审核报告",
    description=(
        "内容语义: CodeReviewer (V3.2 P6 HARD · 他评) 产 · 检查 Material schema required ⇔ "
        "Worker code Verdict.output 字段一致性, class 名对齐, 文件清单完整性. "
        "字段含义: issues 数组 (severity critical/warning + category + fix_hint); verdict pass/fail; "
        "critical_count + warning_count. "
        "上游承诺: CodeReviewer 走 AST 静态分析 + 命名约定 (_module_name_for/_class_name_for) 对齐. "
        "下游用途: Registrar 读此报告 · review_report.verdict==fail 时拒绝落盘 (上 JUMP 触发重产). "
        "最小样例: {\"verdict\": \"pass\", \"critical_count\": 0, \"warning_count\": 1, \"issues\": [{\"severity\": \"warning\", \"category\": \"extra_files\"}]}. "
        "kind.internal."
    ),
    parent="doc",
    json_schema={
        "type": "object",
        "properties": {
            "issues": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "severity": {"type": "string", "enum": ["critical", "warning", "info"]},
                        "worker_id": {"type": "string"},
                        "category": {"type": "string"},
                        "issue": {"type": "string"},
                        "fix_hint": {"type": "string"},
                    },
                    "required": ["severity", "category", "issue"],
                },
            },
            "verdict": {"type": "string", "enum": ["pass", "fail"]},
            "critical_count": {"type": "integer", "minimum": 0},
            "warning_count": {"type": "integer", "minimum": 0},
        },
        "required": ["issues", "verdict"],
    },
    tags=["team_builder", "stage.review", "kind.internal", "he_review"],
)


TB_CODE_PACKAGE = Material(
    id="team_builder.material.code_package",
    name="生成的代码包 (聚合)",
    description=(
        "CodeAggregator (V3.2 HARD · 8 路 composite fan-in) 产出 · 合并 formats_py/team_py/run_py/pkg_init_py/"
        "workers_init_py/workspace_yaml/worker_code_files_bundle/design_md 为 code_package.files dict. "
        "**不直接落盘**, 仅产 material 交 Registrar 决定是否写入. kind.internal."
    ),
    parent="doc",
    json_schema={
        "type": "object",
        "properties": {
            "team_name": {"type": "string"},
            "target_package_path": {
                "type": "string",
                "description": "已验证的 canonical services bucket 或 business domain package 目录",
            },
            "files": {
                "type": "object",
                "description": "rel_path → Python code content (例 'formats.py': '...', 'workers/__init__.py': '...')",
                "additionalProperties": {"type": "string"},
            },
            "compile_summary": {"type": "object", "description": "py_compile 自检结果 (若跑了)"},
        },
        "required": ["team_name", "target_package_path", "files"],
    },
    tags=["team_builder", "stage.codegen", "kind.internal", "agent_first"],
)

TB_REGISTRATION_PLAN = Material(
    id="team_builder.material.registration_plan",
    name="注册计划 (dry_run)",
    description=(
        "RegistrarWorker (Phase 10 HARD) 产出 · 含 target package 落盘清单 + PipelineEntry 条目代码 + "
        "dry_run 标记. **V3 MVP 不真落盘** (保护 src/ 不被 agent 随意污染), 产物交 L1 人类审阅. "
        "需要用户授权时在当前 agent 对话确认后执行. kind.sink (terminal)."
    ),
    parent="doc",
    json_schema={
        "type": "object",
        "properties": {
            "team_name": {"type": "string"},
            "target_package_path": {"type": "string"},
            "files_to_write": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "rel_path": {"type": "string"},
                        "abs_path": {"type": "string"},
                        "size_bytes": {"type": "integer"},
                        "sha256_preview": {"type": "string"},
                    },
                    "required": ["rel_path", "abs_path", "size_bytes"],
                },
            },
            "pipeline_entry_code": {"type": "string", "description": "要追加到 core/pipelines.py 的 PipelineEntry 代码段"},
            "dry_run": {"type": "boolean", "description": "V3 MVP 固定 true · 不真落盘"},
            "human_review_required": {"type": "boolean"},
            "notes": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["team_name", "target_package_path", "files_to_write", "dry_run"],
    },
    tags=["team_builder", "stage.register", "kind.sink", "agent_first"],
)

TB_DESIGN_VALIDATION_REPORT = Material(
    id="team_builder.material.design_validation_report",
    name="草图级完整验证报告",
    description=(
        "DesignValidatorWorker 产出 · HARD + SOFT 补判 · 7 维草图健康: "
        "格式/命名(B 层)/workspace 合规/ServiceBus 对接/契约闭环/F-15 诚实/Worker 18 项清单. "
        "PASS → 进 Phase 8 代码生成. FAIL → JUMP 回对应阶段 RETRY. kind.internal."
    ),
    parent="doc",
    json_schema={
        "type": "object",
        "properties": {
            "format_check": {"type": "object", "properties": {"passed": {"type": "boolean"}, "issues": {"type": "array"}}},
            "naming_check": {"type": "object"},
            "workspace_check": {"type": "object"},
            "servicebus_adoption_check": {"type": "object"},
            "contract_closure_check": {"type": "object"},
            "f15_honesty_check": {"type": "object"},
            "worker_18item_check": {"type": "object"},
            "overall": {"type": "string", "enum": ["PASS", "PARTIAL", "FAIL"]},
            "must_fix": {"type": "array", "items": {"type": "string"}},
            "should_fix": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["overall", "must_fix", "should_fix"],
    },
    tags=["team_builder", "stage.validate", "kind.internal", "design_quality"],
)


# 16 类 material 聚合 (V1 9 类 + V2 7 类 · 见 .omni/build_workflow.md)
TB_A3_MATERIALS = [
    # ── V1 · 9 类 · agent-first MVP (2026-04-23 已跑通 E2E) ──
    TB_REQUEST_TRIGGER,     # CLI 触发 (kind.source)
    TB_INTENT_ANALYSIS,     # agent 中间 (IntentAnalyzer → TeamArchitect)
    TB_ORIGIN_REQUEST,      # 原需求存档 artifact
    TB_TEAM_DESIGN,         # 总体设计 artifact (草图级, V1 sink)
    TB_TEAM_REFERENCES,     # 参考清单 artifact
    TB_WORKER_DESIGN,       # Worker 单条 artifact (TeamArchitect 产出的 skeleton)
    TB_MATERIAL_DESIGN,     # Material 单条 artifact (skeleton)
    TB_AGENT_WORKER_DESIGN, # Agent Worker 特殊说明
    TB_WORKSPACE_DESIGN,    # Workspace 声明 (yaml · 用户向声明)
    # ── V2 · 7 类 · Phase 2/4-7 (待对应 Worker 实装) ──
    TB_SCALE_ASSESSMENT,           # Phase 2 · 规模研判
    TB_DECOMPOSITION_PLAN,         # Phase 2 · 大需求拆分 (conditional)
    TB_WORKER_DESIGN_DETAILED,     # Phase 4 · Worker 深化 (N 份 fan-out)
    TB_MATERIAL_DESIGN_DETAILED,   # Phase 4' · Material 深化 (M 份 fan-out)
    TB_WORKSPACE_SPEC,             # Phase 5 · Workspace 规范化 (HARD 推)
    TB_CONTRACT_AUDIT,             # Phase 6 · 契约静态审计 (HARD · P-13+F-15)
    TB_DESIGN_VALIDATION_REPORT,   # Phase 7 · 草图级 7 维验证
    # ── V3 · Phase 8/10 (2026-04-23) ──
    TB_CODE_PACKAGE,               # Phase 8 · CodeAggregator (V3.2 · 原 CodeGeneratorLoop)
    TB_REGISTRATION_PLAN,          # Phase 10 · Registrar 产 (dry_run · 终点)
    # ── V3.2 · CodeGenerator 子 team 分形 (2026-04-24) ──
    TB_FORMATS_PY,                 # Wh1 · HARD
    TB_TEAM_PY,                    # Wh2 · HARD
    TB_RUN_PY,                     # Wh3 · HARD
    TB_PKG_INIT_PY,                # Wh4 · HARD
    TB_WORKERS_INIT_PY,            # Wh5 · HARD
    TB_WORKSPACE_YAML,             # Wh6 · HARD
    TB_WORKER_CODE_FILE,           # Ws7 sub-agent output (per worker)
    TB_WORKER_CODE_FILES_BUNDLE,   # Ws7 orchestrator 合并
    TB_DESIGN_MD,                  # Ws8 · SOFT (骨架规范化 + LLM 填内容)
    TB_CODE_REVIEW_REPORT,         # P6 · HARD 他评 (2026-04-24)
]

# ═══════════════════════════════════════════════════════════
# 注册所有 Material
# ═══════════════════════════════════════════════════════════

ALL_FORMATS = [
    # 旧的 workflow_factory 内部 Material (Diamond 归档作参考 · 2026-04-23)
    WF_REQUIREMENT_RAW, WF_REQUIREMENT, WF_FORMAT_CHAIN, WF_NODE_PLAN,
    WF_FRAMEWORK_CONTEXT_LOADER_INPUT,  # M2.β v2 (2026-04-19) composite fan-in 输入
    WF_NODE_PLAN_AUGMENTED,
    WF_CODE_GEN_STATE,  # P7.2 增量代码生成中间态
    WF_PROJECT_SKELETON,  # P7.3 单主干: 验证节点都用本 Material + reports 容器
    WF_DONE,  # 最终产物
    # ── A3 · agent-first 7 类 material ──
    *TB_A3_MATERIALS,
]

# 兼容别名 (新代码推荐 ALL_MATERIALS)
ALL_MATERIALS = ALL_FORMATS


def register_formats(registry: FormatRegistry) -> None:
    """注册所有 workflow_factory Material 到给定 registry."""
    for fmt in ALL_FORMATS:
        if not registry.is_registered(fmt.id):
            try:
                registry.register(fmt)
            except Exception:
                pass  # 父类型未注册等情况，静默跳过
