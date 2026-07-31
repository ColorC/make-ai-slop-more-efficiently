# [OMNI] origin=claude-code domain=services/absorption/formats.py ts=2026-04-08T12:00:00Z
# [OMNI] material_id="material:learning.absorption.format_definitions.registry.py"
"""absorption.formats — Repo Absorption 管线的语义类型定义。

Stage 1 (Survey & Triage) 只定义"识别地标"链路上的 5 个 Format。
后续 Stage 增量扩展，每加一阶补对应 Format。

数据流 (Stage 1):
  user_request                          (用户原始请求)
       │ target_intake (RULE)
       ▼
  intake                                (规整后的吸纳意图)
       │ repo_facade_fetcher (HARD)     # 调 GitHub API，不 clone
       ▼
  facade_card                           (仓库门面元数据)
       │ landmark_picker (SOFT loop)    # LLM 阅读门面，挑地标
       ▼
  landmark_list                         (候选地标 + tier 评分)
       │ triage_gate (HARD)             # 至少 1 个 tier-1 才放行
       ▼
  triaged_landmarks                     (放行的地标 → Phase B 入口)

每个 Format 的 description 都按 SO 标准 (≥100 字)，
含: 内容语义 + 调试可见点 + 上下游契约。
"""

from __future__ import annotations

from omnicompany.packages.services._core.omnicompany import Material
from omnicompany.protocol.format import FormatRegistry

# Material = protocol.format.Format alias (omnicompany 层命名, 2026-04-20 Clean Migration)
# 保留 `Format` 名以维持本文件内其他文档字符串和对外 symbol 的连续性, 两者等价.
Format = Material

# ═══════════════════════════════════════════════════════════
# Phase A 模仿 (Survey) 链上的 5 个 Format
# ═══════════════════════════════════════════════════════════

ABSORPTION_USER_REQUEST = Format(
    id="absorption.user_request",
    name="AbsorptionUserRequest",
    description=(
        "用户提出的吸纳意图，自然语言或半结构化形式。包含一组目标 GitHub 仓库 URL "
        "或 owner/repo 短名 + 期望的吸纳 Profile (framework_absorption / "
        "domain_absorption)。这是整条 Repo Absorption 管线的入口点。"
        "Profile 决定 Phase E 是否需要 BackwardCompatGuard (L5)：framework 必须，"
        "domain 不必。调试可见点：检查 repos 列表是否合法、profile 是否在枚举内。"
    ),
    parent="requirement",
    tags=["domain.absorption", "stage.intake", "phase.a_survey", "kind.source"],
    json_schema={
        "type": "object",
        "properties": {
            "repos": {
                "type": "array",
                "items": {"type": "string"},
                "description": "目标仓库列表，形如 'openai/codex' 或完整 URL",
                "minItems": 1,
            },
            "profile": {
                "type": "string",
                "enum": ["framework_absorption", "domain_absorption"],
                "description": "吸纳 Profile：框架级补短板 vs 领域级冲 SOTA",
            },
            "notes": {
                "type": "string",
                "description": "可选：用户对本次吸纳的额外说明",
            },
        },
        "required": ["repos", "profile"],
    },
)


ABSORPTION_INTAKE = Format(
    id="absorption.intake",
    name="AbsorptionIntake",
    description=(
        "经 target_intake 节点规整后的吸纳意图。把 user_request 中的仓库短名展开为完整 "
        "owner/repo 形态、profile 转为枚举值、为本次 absorption 分配唯一 absorption_id "
        "(用于全管线追踪和血统记录)。这是一个确定性 Format —— 同样的 user_request "
        "应该得到完全一致的 intake。调试可见点：absorption_id 是否唯一、repos 是否全部合法。"
    ),
    parent="absorption.user_request",
    tags=["domain.absorption", "stage.normalized", "phase.a_survey", "structured", "kind.internal"],
    json_schema={
        "type": "object",
        "properties": {
            "absorption_id": {
                "type": "string",
                "description": "本次吸纳的全局唯一 ID，形如 'abs-2026-04-08-001'",
            },
            "repos": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "owner": {"type": "string"},
                        "name": {"type": "string"},
                        "url": {"type": "string"},
                    },
                    "required": ["owner", "name", "url"],
                },
            },
            "profile": {
                "type": "string",
                "enum": ["framework_absorption", "domain_absorption"],
            },
            "notes": {"type": "string"},
        },
        "required": ["absorption_id", "repos", "profile"],
    },
)


ABSORPTION_FACADE_CARD = Format(
    id="absorption.facade_card",
    name="AbsorptionFacadeCard",
    description=(
        "仓库门面卡片：在不 clone 仓库的前提下抓到的 README 摘要、topics、star 历史、"
        "commit 频率、主要贡献者、最近 release notes、子目录顶层结构。这些信息用 "
        "GitHub API 即可获取，目的是让下游 landmark_picker 在不下载源码的情况下做"
        "粗略判定。每个 repo 一张 card。调试可见点：每张 card 是否都有 readme_excerpt "
        "和 top_level_dirs；缺失则说明 GitHub API 拉取不完整，需在 Stage 2 之前 retry。"
    ),
    parent="absorption.intake",
    tags=["domain.absorption", "stage.fetched", "phase.a_survey", "structured", "kind.internal"],
    json_schema={
        "type": "object",
        "properties": {
            "absorption_id": {"type": "string"},
            "facade_cards": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "owner": {"type": "string"},
                        "name": {"type": "string"},
                        "url": {"type": "string"},
                        "readme_excerpt": {"type": "string"},
                        "topics": {
                            "type": "array",
                            "items": {"type": "string"},
                        },
                        "stars": {"type": "integer"},
                        "commit_frequency": {
                            "type": "string",
                            "description": "近 30 日 commit 频率描述，例如 'high' / 'medium' / 'low'",
                        },
                        "top_level_dirs": {
                            "type": "array",
                            "items": {"type": "string"},
                        },
                        "language_stats": {
                            "type": "object",
                            "additionalProperties": {"type": "number"},
                            "description": "语言占比，由 GitHub API linguist 数据",
                        },
                    },
                    "required": ["owner", "name", "url"],
                },
            },
        },
        "required": ["absorption_id", "facade_cards"],
    },
)


ABSORPTION_LANDMARK_LIST = Format(
    id="absorption.landmark_list",
    name="AbsorptionLandmarkList",
    description=(
        "由 landmark_picker (LLM) 从 facade_card 出发挑选的候选地标列表 + 两类盈余产物。"
        "landmarks: 每个地标是仓内值得 Phase B 抄写的子模块或子目录，含 path / "
        "why_interesting / tier 评分 / evidence 引用。tier-1 必须吸纳，tier-2 值得看，"
        "tier-3 只学经验不抄。本 Format 包含全部候选 (≤20)，下游 triage_gate 才过滤 tier-1。"
        "盈余字段 (Stage 1 SO): landscape_sketches 给每个 repo 一份'路过此地的速写画像'"
        "(one_liner 定位 + ≤3 个核心抽象 + 与 OmniCompany 差异); capability_gap_previews "
        "给每个 repo 一份'预估补的 OmniCompany 短板'(framework_gaps 或 domain_gaps)。"
        "这些盈余即使本次 absorption 不进入 Phase B 也会被 triage_gate 落盘到 landmark_pool。"
        "调试可见点：landmarks 列表是否非空、tier 分布是否合理、speakerSketches 是否覆盖每个 repo。"
    ),
    parent="absorption.omnicompany_snapshot",
    tags=["domain.absorption", "stage.judged", "phase.a_survey", "structured", "kind.internal"],
    json_schema={
        "type": "object",
        "properties": {
            "absorption_id": {"type": "string"},
            "landmarks": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "owner": {"type": "string"},
                        "name": {"type": "string"},
                        "path": {
                            "type": "string",
                            "description": "仓内子路径，可能是目录或文件",
                        },
                        "why_interesting": {
                            "type": "string",
                            "description": "≥1 句话说明为什么值得吸纳",
                        },
                        "tier": {
                            "type": "integer",
                            "enum": [1, 2, 3],
                            "description": "1=必须吸纳 / 2=值得看 / 3=只学经验",
                        },
                        "evidence": {
                            "type": "string",
                            "description": "支持判断的具体证据片段 (取自 README/目录结构)",
                        },
                    },
                    "required": ["owner", "name", "path", "why_interesting", "tier"],
                },
            },
            "landscape_sketches": {
                "type": "array",
                "description": "每个 repo 一份速写画像 (Stage 1 盈余)",
                "items": {
                    "type": "object",
                    "properties": {
                        "owner": {"type": "string"},
                        "name": {"type": "string"},
                        "one_liner": {"type": "string"},
                        "core_abstractions": {
                            "type": "array",
                            "items": {"type": "string"},
                            "maxItems": 3,
                        },
                        "diff_vs_omnicompany": {"type": "string"},
                    },
                },
            },
            "capability_gap_previews": {
                "type": "array",
                "description": "每个 repo 一份补短板/达 SOTA 预估 (Stage 1 盈余)",
                "items": {
                    "type": "object",
                    "properties": {
                        "owner": {"type": "string"},
                        "name": {"type": "string"},
                        "framework_gaps": {
                            "type": "array",
                            "items": {"type": "string"},
                            "maxItems": 5,
                        },
                        "domain_gaps": {
                            "type": "array",
                            "items": {"type": "string"},
                            "maxItems": 5,
                        },
                    },
                },
            },
        },
        "required": ["absorption_id", "landmarks"],
    },
)


ABSORPTION_OMNICOMPANY_SNAPSHOT = Format(
    id="absorption.omnicompany_snapshot",
    name="AbsorptionOmnicompanySnapshot",
    description=(
        "OmniCompany 本仓当前能力的自扫描快照, 无 LLM 无网络, 纯 FS 扫描产出。"
        "含 5 个顶层字段: packages (业务包 + pipeline ids + docstring), "
        "registered_pipelines (core/pipelines.py 中的 name/description), "
        "routers (全仓 class ...Router 定义的 class/base/file/description), "
        "builtin_tools (agent_loop_tools.py 中的 ToolDefinition 名字列表), "
        "core_modules (core/runtime/protocol/bus 下所有 .py 模块路径)。"
        "供下游 LandmarkPicker 的 LLM 通过 omni_capabilities 工具查询, "
        "避免凭想象判断 OmniCompany 有没有某能力。"
        "调试可见点: stats 数量要合理 (>20 packages / >200 routers / >10 tools)。"
    ),
    parent="absorption.facade_card",
    tags=["domain.absorption", "stage.self_introspected", "structured", "kind.internal"],
    json_schema={
        "type": "object",
        "properties": {
            "absorption_id": {"type": "string"},
            "omni_snapshot": {
                "type": "object",
                "properties": {
                    "packages": {"type": "object"},
                    "registered_pipelines": {"type": "array"},
                    "routers": {"type": "array"},
                    "builtin_tools": {"type": "array"},
                    "core_modules": {"type": "array"},
                },
                "required": ["packages", "routers", "builtin_tools", "core_modules"],
            },
        },
        "required": ["absorption_id", "omni_snapshot"],
    },
)


ABSORPTION_COVERAGE_AUDIT = Format(
    id="absorption.coverage_audit",
    name="AbsorptionCoverageAudit",
    description=(
        "LandmarkPicker 探索覆盖度的审计报告。对比 facade_card.tree_recursive (总文件数) "
        "和 picker_read_files (实际用 gh_file_read 读过的文件), 产出 coverage_by_repo "
        "(每 repo 的 total_files / files_read / read_percent / top_dirs 分组统计 / "
        "unscanned_top_dirs) + 全局 overall_coverage_percent。本 Format 的目的是"
        "让最终报告能诚实声明 '我没看 X/Y/Z' —— 避免让使用者误以为 picker 的结论"
        "覆盖了整个仓库。调试可见点: coverage_percent 通常在 1-20%, 超过 30% 说明"
        "picker 读太多 (预算浪费); 低于 0.1% 说明 picker 读太少 (证据不足)。"
    ),
    parent="absorption.landmark_list",
    tags=["domain.absorption", "stage.audited", "structured", "kind.internal"],
    json_schema={
        "type": "object",
        "properties": {
            "coverage_audit": {
                "type": "object",
                "properties": {
                    "coverage_by_repo": {"type": "array"},
                    "overall_total_files": {"type": "integer"},
                    "overall_files_read": {"type": "integer"},
                    "overall_coverage_percent": {"type": "number"},
                },
                "required": ["coverage_by_repo", "overall_files_read"],
            },
        },
        "required": ["coverage_audit"],
    },
)


ABSORPTION_REPORT = Format(
    id="absorption.report",
    name="AbsorptionReport",
    description=(
        "Human-readable markdown 报告的路径与元数据。内容落盘到 "
        "data/absorption/reports/<absorption_id>.md, 本 Format 只携带 report_path "
        "和 report_size_bytes 供下游引用。报告本身含: TL;DR / 每个 repo 的 repository "
        "facts + landscape sketch + 所有 tier 级的 landmarks (带 evidence snippet) + "
        "capability gap 分析 (带 OmniCompany 对照) + 覆盖审计表 + 全局诚实局限声明。"
        "这是 Stage 1 真正的 '给人看的成果物', 区别于 landmark_pool.json 的机器格式。"
    ),
    parent="absorption.triaged_landmarks",
    tags=[
        "domain.absorption",
        "stage.reported",
        "structured",
        "human_readable",
        "ready_for_phase_b",
        "kind.sink",
    ],
    required_tags=["structured"],
    json_schema={
        "type": "object",
        "properties": {
            "absorption_id": {"type": "string"},
            "report_path": {"type": "string"},
            "report_size_bytes": {"type": "integer"},
            "tier_one_count": {"type": "integer"},
            "pool_path": {"type": "string"},
        },
        "required": ["absorption_id", "report_path", "report_size_bytes"],
    },
)


ABSORPTION_TRIAGED_LANDMARKS = Format(
    id="absorption.triaged_landmarks",
    name="AbsorptionTriagedLandmarks",
    description=(
        "经 triage_gate 过滤后的放行地标。triage_gate 的硬规则: 至少 1 个 tier-1 地标"
        "存在才放行；否则 HALT 并告知用户'该仓与 OmniCompany 当前能力地图无显著差异'。"
        "本 Format 是 Phase A (Survey) 的最终出口，下游 Phase B (Quarantine) 会基于此"
        "决定 clone 哪些仓的哪些路径 (sparse-checkout 的依据)。调试可见点："
        "tier_one_count 必须 ≥1，pool_path 必须指向已落地的 landmark_pool 留档文件。"
    ),
    parent="absorption.coverage_audit",
    tags=[
        "domain.absorption",
        "stage.triaged",
        "phase.a_survey",
        "structured",
        "ready_for_phase_b",
        "kind.internal",
    ],
    required_tags=["structured"],
    json_schema={
        "type": "object",
        "properties": {
            "absorption_id": {"type": "string"},
            "tier_one": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "owner": {"type": "string"},
                        "name": {"type": "string"},
                        "path": {"type": "string"},
                        "why_interesting": {"type": "string"},
                        "tier": {"type": "integer", "const": 1},
                    },
                    "required": ["owner", "name", "path", "why_interesting", "tier"],
                },
                "minItems": 1,
            },
            "tier_one_count": {
                "type": "integer",
                "minimum": 1,
            },
            "pool_path": {
                "type": "string",
                "description": "landmark_pool 留档 JSON 路径 (含 tier-2/3 全部候选)",
            },
        },
        "required": ["absorption_id", "tier_one", "tier_one_count"],
    },
)


# ═══════════════════════════════════════════════════════════
# 注册入口 (V1 — Survey & Triage)
# ═══════════════════════════════════════════════════════════

ALL_FORMATS = [
    ABSORPTION_USER_REQUEST,
    ABSORPTION_INTAKE,
    ABSORPTION_FACADE_CARD,
    ABSORPTION_OMNICOMPANY_SNAPSHOT,
    ABSORPTION_LANDMARK_LIST,
    ABSORPTION_COVERAGE_AUDIT,
    ABSORPTION_TRIAGED_LANDMARKS,
    ABSORPTION_REPORT,
]


# ═══════════════════════════════════════════════════════════
# V2 — 问题驱动的定向深读管线 (2026-04-13)
# 文档: docs/plans/[2026-04-13]REPO-ABSORPTION-V2/plan.md
# ═══════════════════════════════════════════════════════════

ABSORPTION_V2_REQUEST = Format(
    id="absorption.request",
    name="AbsorptionRequest",
    description=(
        "V2 管线入口。携带本次吸纳的目标 repo 路径（本地已克隆）、可选的初始问题清单（用户手动补充）。"
        "repo_local_path 必须指向已 git clone 的目录。"
        "initial_questions 为空时由 IntersectionPlanner 自动生成。"
        "\n\n**2026-04-18 变更**：self_portrait 字段**已废弃**（不再 required）。"
        "V3 管线通过 wiki 三路 fan-in（capability_inventory + gap_registry + reception_intents）"
        "动态加载 OmniCompany 自知识，彻底取代原硬编码自画像。保留 schema 字段仅向后兼容"
        "未更新调用方；新代码**不得**填充此字段。"
        "\n\n调试可见点：repo_local_path 是否存在、initial_questions 长度。"
    ),
    tags=["domain.absorption", "stage.v2.entry", "phase.v2", "kind.source"],
    json_schema={
        "type": "object",
        "properties": {
            "repo_name": {"type": "string", "description": "目标 repo 名称，如 'gemini-cli'"},
            "repo_local_path": {"type": "string", "description": "本地克隆路径，必须已存在"},
            "self_portrait": {
                "type": "string",
                "description": (
                    "DEPRECATED（2026-04-18，由 wiki 三路 composite 替代）。"
                    "新代码不得填充；保留仅为兼容旧调用方。"
                ),
            },
            "initial_questions": {
                "type": "array",
                "items": {"type": "string"},
                "description": "用户手动补充的初始问题（可选，空则由 IntersectionPlanner 生成）",
            },
        },
        "required": ["repo_name", "repo_local_path"],
    },
)

ABSORPTION_V2_RECON_MAP = Format(
    id="absorption.recon.map",
    name="AbsorptionReconMap",
    description=(
        "ReconScoutRouter（AgentNodeLoop）的侦察产物。在 ≤30 个文件内产出粗粒度能力图谱，"
        "含按功能域分组的 capability_map（每域 ≤3 句话）、最重要的 5-10 个关键模块（路径+一句话说明）、"
        "架构摘要和入口文件列表。不做深度分析，只做'我看到了什么'的客观陈述。"
        "调试可见点：key_modules 是否有 5-10 个条目、capability_map 覆盖了哪些功能域、"
        "files_read 是否 ≤30。"
    ),
    parent="absorption.request",
    tags=["domain.absorption", "stage.v2.recon", "phase.v2", "kind.internal"],
    json_schema={
        "type": "object",
        "properties": {
            "repo_name": {"type": "string"},
            "capability_map": {
                "type": "object",
                "description": "功能域 → 描述，每域 ≤3 句话",
                "additionalProperties": {"type": "string"},
            },
            "key_modules": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string"},
                        "description": {"type": "string"},
                    },
                    "required": ["path", "description"],
                },
                "minItems": 1,
                "maxItems": 10,
            },
            "architecture_summary": {"type": "string", "description": "≤3 段的架构摘要"},
            "entry_points": {
                "type": "array",
                "items": {"type": "string"},
                "description": "主要入口文件路径列表",
            },
            "files_read": {
                "type": "array",
                "items": {"type": "string"},
                "description": "侦察阶段实际读取的文件列表（用于覆盖审计）",
            },
        },
        "required": ["repo_name", "capability_map", "key_modules", "architecture_summary"],
    },
)

ABSORPTION_V2_QUESTION_LIST = Format(
    id="absorption.question-list",
    name="AbsorptionQuestionList",
    description=(
        "IntersectionPlannerRouter（LLM）生成的优先化问题清单。每条问题对应一个 OmniCompany 自画像缺口（G1-G7），"
        "包含问题文本、优先级（P0/P1/P2）、预期找答案的位置（模块路径或关键词）、"
        "以及为什么值得回答（或若此 repo 显然不涉及则直接给跳过理由）。"
        "每个 repo 最多 20 个问题。调试可见点：questions 数量是否 ≤20、每条是否都有 gap_id（G1-G7）、"
        "P0 问题是否非空。"
    ),
    parent="absorption.recon.map",
    tags=["domain.absorption", "stage.v2.questions", "phase.v2", "kind.internal"],
    json_schema={
        "type": "object",
        "properties": {
            "repo_name": {"type": "string"},
            "questions": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "string", "description": "问题 ID，如 'Q1'"},
                        "text": {"type": "string", "description": "问题文本"},
                        "gap_id": {
                            "type": "string",
                            "enum": ["G1", "G2", "G3", "G4", "G5", "G6", "G7"],
                            "description": "对应自画像缺口",
                        },
                        "priority": {
                            "type": "string",
                            "enum": ["P0", "P1", "P2"],
                        },
                        "expected_location": {
                            "type": "string",
                            "description": "预期在哪个模块/目录/文件找到答案",
                        },
                        "skip_reason": {
                            "type": "string",
                            "description": "若此 repo 显然不涉及该领域，给出跳过理由（空=不跳过）",
                        },
                    },
                    "required": ["id", "text", "gap_id", "priority"],
                },
                "maxItems": 20,
            },
        },
        "required": ["repo_name", "questions"],
    },
)

ABSORPTION_V2_QUESTION_LIST_APPROVED = Format(
    id="absorption.question-list.approved",
    name="AbsorptionQuestionListApproved",
    description=(
        "经人工审核的问题清单。与 absorption.question-list 结构相同，但已经过人工调整优先级/增删问题。"
        "由人工门（手动操作或等待编辑文件）产出，确保问题清单对齐当前吸纳目标。"
        "approved_at 记录审核时间，reviewer 记录审核者（可为 'human' 或具体名字）。"
        "调试可见点：approved_at 是否非空（空则说明跳过了人工审核步骤）、问题数量是否合理。"
    ),
    parent="absorption.question-list",
    tags=["domain.absorption", "stage.v2.approved", "phase.v2", "kind.internal"],
    json_schema={
        "type": "object",
        "properties": {
            "repo_name": {"type": "string"},
            "questions": {"type": "array", "items": {"type": "object"}},
            "approved_at": {"type": "string", "description": "审核时间戳（ISO 8601）"},
            "reviewer": {"type": "string", "description": "审核者"},
        },
        "required": ["repo_name", "questions"],
    },
)

ABSORPTION_V2_QUESTION = Format(
    id="absorption.question",
    name="AbsorptionQuestion",
    description=(
        "QuestionFanoutRouter（SCATTER）展开后的单个问题任务。每个 DirectedReaderRouter 实例"
        "接收一个此 Format 的任务，带着具体问题和预期位置进行定向深读。"
        "repo_root 是本地克隆的根路径，用于工具调用（Glob/Grep/ReadFile）。"
        "调试可见点：question_text 是否清晰、expected_location 是否有效、repo_root 是否存在。"
    ),
    parent="absorption.question-list.approved",
    tags=["domain.absorption", "stage.v2.reading", "phase.v2", "scatter_item", "kind.internal"],
    json_schema={
        "type": "object",
        "properties": {
            "repo_name": {"type": "string"},
            "question_id": {"type": "string"},
            "question_text": {"type": "string"},
            "gap_id": {"type": "string"},
            "priority": {"type": "string", "enum": ["P0", "P1", "P2"]},
            "expected_location": {"type": "string"},
            "repo_root": {"type": "string", "description": "本地克隆路径"},
        },
        "required": ["repo_name", "question_id", "question_text", "repo_root"],
    },
)

ABSORPTION_V2_QUESTION_ANSWER = Format(
    id="absorption.question.answer",
    name="AbsorptionQuestionAnswer",
    description=(
        "DirectedReaderRouter 对单个问题的答案。status 表示回答状态："
        "answered（找到可引用证据，置信度 ≥0.8）、partial（超资源上限，部分回答）、"
        "not_found（扫描 ≥5 个候选文件均无相关内容）、skipped（此 repo 显然不涉及该领域）。"
        "evidence 是代码证据列表，每条含文件路径、行号范围、引用片段（≤200字符）、相关性说明。"
        "files_read 记录实际读取的文件列表（用于资源消耗审计）。"
        "调试可见点：status 是否合理、evidence 是否有 file/lines/quote 三要素、files_read 是否 ≤15。"
    ),
    parent="absorption.question",
    tags=["domain.absorption", "stage.v2.answered", "phase.v2", "kind.internal"],
    json_schema={
        "type": "object",
        "properties": {
            "repo_name": {"type": "string"},
            "question_id": {"type": "string"},
            "question_text": {"type": "string"},
            "status": {
                "type": "string",
                "enum": ["answered", "partial", "not_found", "skipped"],
            },
            "answer": {"type": "string", "description": "对问题的文字回答"},
            "evidence": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "file": {"type": "string"},
                        "lines": {"type": "string", "description": "行号范围，如 '45-78'"},
                        "quote": {"type": "string", "maxLength": 200},
                        "relevance": {"type": "string"},
                    },
                    "required": ["file"],
                },
            },
            "files_read": {
                "type": "array",
                "items": {"type": "string"},
                "maxItems": 15,
            },
            "confidence": {
                "type": "number",
                "minimum": 0.0,
                "maximum": 1.0,
            },
        },
        "required": ["repo_name", "question_id", "question_text", "status"],
    },
)

ABSORPTION_V2_AUDIT = Format(
    id="absorption.audit",
    name="AbsorptionAudit",
    description=(
        "CoverageAuditorRouter（RULE）对全部问题答案的覆盖审计报告。"
        "检查 recon.map 中识别的关键模块是否都被至少一个问题覆盖到，"
        "汇总 answered/partial/not_found/skipped 的数量分布，计算 coverage_score（0-1）。"
        "gaps 列出未被任何问题覆盖的关键模块。"
        "调试可见点：coverage_score 是否合理（<0.3 说明问题清单设计不够好）、"
        "answered_count 是否占多数、uncovered_modules 是否超过 3 个。"
    ),
    parent="absorption.question.answer",
    tags=["domain.absorption", "stage.v2.audited", "phase.v2", "kind.internal"],
    json_schema={
        "type": "object",
        "properties": {
            "repo_name": {"type": "string"},
            "answered_count": {"type": "integer"},
            "partial_count": {"type": "integer"},
            "not_found_count": {"type": "integer"},
            "skipped_count": {"type": "integer"},
            "total_count": {"type": "integer"},
            "coverage_score": {"type": "number", "minimum": 0.0, "maximum": 1.0},
            "covered_modules": {"type": "array", "items": {"type": "string"}},
            "uncovered_modules": {"type": "array", "items": {"type": "string"}},
            "gaps": {
                "type": "array",
                "items": {"type": "string"},
                "description": "关键模块中未被覆盖的条目",
            },
        },
        "required": ["repo_name", "answered_count", "total_count", "coverage_score"],
    },
)

ABSORPTION_V2_SYNTHESIS = Format(
    id="absorption.synthesis",
    name="AbsorptionSynthesis",
    description=(
        "SynthesisRouter（LLM）聚合所有 question.answer 和审计报告后产出的综合产物集。"
        "包含 4 份产物：architecture_diagram（ASCII 或 mermaid 架构图）、"
        "highlights（对 OmniCompany 最有价值的 5-10 个亮点，每条对应一个 gap_id）、"
        "section_analysis（各模块的深度分析，键为模块路径或功能域）、"
        "omnicompany_alignment（对比自画像缺口 G1-G7 的对照分析，每条给出'学到了什么/建议怎么做'）。"
        "调试可见点：highlights 是否有 5-10 条、omnicompany_alignment 是否覆盖所有 P0 缺口。"
    ),
    parent="absorption.audit",
    tags=["domain.absorption", "stage.v2.synthesis", "phase.v2", "kind.internal"],
    json_schema={
        "type": "object",
        "properties": {
            "repo_name": {"type": "string"},
            "architecture_diagram": {"type": "string", "description": "ASCII 或 mermaid 架构图"},
            "highlights": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "title": {"type": "string"},
                        "description": {"type": "string"},
                        "gap_id": {"type": "string"},
                        "borrowing_suggestion": {"type": "string"},
                    },
                    "required": ["title", "description"],
                },
                "minItems": 5,
                "maxItems": 10,
            },
            "section_analysis": {
                "type": "object",
                "description": "模块/功能域 → 深度分析文本",
                "additionalProperties": {"type": "string"},
            },
            "omnicompany_alignment": {
                "type": "object",
                "description": "G1-G7 缺口 → {learned: ..., suggestion: ...}",
                "additionalProperties": {
                    "type": "object",
                    "properties": {
                        "learned": {"type": "string"},
                        "suggestion": {"type": "string"},
                    },
                },
            },
        },
        "required": ["repo_name", "highlights", "omnicompany_alignment"],
    },
)

ABSORPTION_V2_REPORT = Format(
    id="absorption.report.v2",
    name="AbsorptionReportV2",
    description=(
        "ReportWriterRouter（RULE）写入 data/absorption/<repo>/<date>/report.md 后产出的路径与元数据。"
        "与 V1 的 absorption.report 不同，V2 报告以'问题驱动'为主线组织内容："
        "每个问题的答案+证据、覆盖审计摘要、亮点清单、OmniCompany 对照建议。"
        "同时更新 data/absorption/<repo>/.omni/manifest.yaml 记录吸纳状态。"
        "调试可见点：report_path 是否存在、coverage_score 是否合理、answered_count/total_count 比例。"
    ),
    parent="absorption.synthesis",
    tags=["domain.absorption", "stage.v2.reported", "phase.v2", "human_readable", "kind.sink"],
    json_schema={
        "type": "object",
        "properties": {
            "repo_name": {"type": "string"},
            "report_path": {"type": "string", "description": "report.md 的本地路径"},
            "manifest_path": {"type": "string", "description": "manifest.yaml 的本地路径"},
            "coverage_score": {"type": "number", "minimum": 0.0, "maximum": 1.0},
            "answered_count": {"type": "integer"},
            "total_count": {"type": "integer"},
        },
        "required": ["repo_name", "report_path", "coverage_score"],
    },
)

ALL_V2_FORMATS = [
    ABSORPTION_V2_REQUEST,
    ABSORPTION_V2_RECON_MAP,
    ABSORPTION_V2_QUESTION_LIST,
    ABSORPTION_V2_QUESTION_LIST_APPROVED,
    ABSORPTION_V2_QUESTION,
    ABSORPTION_V2_QUESTION_ANSWER,
    ABSORPTION_V2_AUDIT,
    ABSORPTION_V2_SYNTHESIS,
    ABSORPTION_V2_REPORT,
]


# ── V3 Formats ──────────────────────────────────────────────────────────────
# 四层 Format 架构：repomap → important-modules → module.code → learning
# 设计文档：docs/plans/[2026-04-13]REPO-ABSORPTION-V3/DESIGN.md

ABSORPTION_V3_REPOMAP = Format(
    id="absorption.repomap",
    name="AbsorptionRepomap",
    description=(
        "RepoMapperRouter（纯计算，无 LLM）对整个本地 repo 扫描产出的双层符号地图。"
        "\n\n【字段】"
        "\n- repo_name: 仓库名称"
        "\n- project_thesis: 从 README 提取的项目自述核心特色区段（安装/使用节之前的部分，完整无截断）。"
        "  供 ModuleExplorer 对照'宣称特色 vs 实际代码'。若无 README 则为空字符串。"
        "\n- coarse_view: 全量粗粒度文本，每文件 1 行（路径[行数]:symbol...），按 importance_score 降序。"
        "  解决 V2 Scout 系统性漏读正交基础设施模块的根因问题——所有文件均可见。"
        "\n- detail_views: {path: 细粒度符号树文本} dict，供 ModuleExplorer 按需展开。"
        "\n- files[]: 每文件的 {path, line_count, symbol_count, top_symbols, importance_score}。"
        "\n- total_files: 扫描到的总文件数。"
        "\n- coarse_token_count: coarse_view 的近似 token 数（空格分割估算）。"
        "\n\n【上游承诺】RepoMapperRouter 确定性产出；project_thesis 来自 README 全文语义切割，"
        "非随机截断；若 README 不存在则为空字符串（不报错）。"
        "\n\n【下游用途】ModuleExplorer 消费：coarse_view 决定探索方向，project_thesis 确保宣称特色被覆盖，"
        "detail_views 支撑 local_read 之外的符号扩展。CapabilityInventoryQueryBuilder / "
        "GapRegistryQueryBuilder / ReceptionIntentsQueryBuilder 读 repo_name 构建查询。"
        "\n\n【调试可见点】total_files 是否覆盖全量、project_thesis 是否非空（README 存在时）、"
        "coarse_view 里是否能看到已知大文件。"
        "\n\n【最小合法样例】"
        '{"repo_name":"hermes-agent","project_thesis":"# hermes-agent\\nAgent 框架...\\n## 特色\\n1. 智能路由...","coarse_view":"## Repository Map: hermes-agent\\n...","files":[...],"detail_views":{...},"total_files":42}'
    ),
    parent="absorption.request",
    tags=["domain.absorption", "stage.v3.repomap", "phase.v3", "kind.internal"],
    json_schema={
        "type": "object",
        "properties": {
            "repo_name": {"type": "string"},
            "coarse_view": {
                "type": "string",
                "description": "全量粗粒度文本，每个文件 1 行，按 importance_score 降序",
            },
            "files": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string"},
                        "line_count": {"type": "integer"},
                        "symbol_count": {"type": "integer"},
                        "top_symbols": {"type": "array", "items": {"type": "string"}},
                        "importance_score": {"type": "number"},
                    },
                    "required": ["path", "line_count", "importance_score"],
                },
            },
            "detail_views": {
                "type": "object",
                "description": "path → 细粒度符号树文本（含行号），按需展开",
                "additionalProperties": {"type": "string"},
            },
            "total_files": {"type": "integer"},
            "coarse_token_count": {"type": "integer"},
            "project_thesis": {
                "type": "string",
                "description": (
                    "README 中项目自述的核心特色区段（安装/使用节之前），"
                    "完整无截断。无 README 时为空字符串。"
                ),
            },
        },
        "required": ["repo_name", "coarse_view", "files", "detail_views", "total_files"],
    },
)

ABSORPTION_V3_IMPORTANT_MODULES = Format(
    id="absorption.important-modules",
    name="AbsorptionImportantModules",
    description=(
        "ModulePickerRouter（LLM 单次调用）看 coarse_view + self_portrait 后语义选出的重要模块清单。"
        "不是 PageRank top-N，是 LLM 判断哪些文件与 G1-G7 缺口相关。"
        "每个模块：路径、绑定的缺口 gap_id、优先级、选择理由、从 detail_views 展开的细粒度内容。"
        "可设置 HumanApprovalGate，让人工增删模块后再进入 ModuleReader。"
        "调试可见点：modules 数量是否合理（5-15 个）、每个模块的 reason 是否言之有据、"
        "modules_skipped 是否明确说明了为什么不选。"
    ),
    parent="absorption.repomap",
    tags=["domain.absorption", "stage.v3.picker", "phase.v3", "kind.internal"],
    json_schema={
        "type": "object",
        "properties": {
            "repo_name": {"type": "string"},
            "modules": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string"},
                        "gap_id": {"type": "string", "description": "G1-G7"},
                        "priority": {"type": "string", "enum": ["P0", "P1", "P2"]},
                        "reason": {"type": "string"},
                        "detail_view": {"type": "string", "description": "从 repomap.detail_views 展开"},
                    },
                    "required": ["path", "gap_id", "priority", "reason"],
                },
            },
            "selection_rationale": {"type": "string"},
            "modules_skipped": {
                "type": "array",
                "items": {"type": "string"},
                "description": "明确判断不重要的文件路径列表",
            },
        },
        "required": ["repo_name", "modules"],
    },
)

ABSORPTION_V3_MODULE_CODE = Format(
    id="absorption.module.code",
    name="AbsorptionModuleCode",
    description=(
        "ModuleReaderRouter 对 important-modules 里每个模块的实际代码内容。"
        "不同于 V2 按问题读文件，V3 按模块读——每个模块一条记录，含完整代码内容。"
        "read_method 说明来源：detail_view（直接用 repomap 的细粒度视图）、"
        "local_read（补充读取完整文件）、或两者结合。"
        "调试可见点：每个模块的 content 是否非空、read_method 是否合理、"
        "files_read 列表是否与 important-modules 一致。"
    ),
    parent="absorption.important-modules",
    tags=["domain.absorption", "stage.v3.reader", "phase.v3", "kind.internal"],
    json_schema={
        "type": "object",
        "properties": {
            "repo_name": {"type": "string"},
            "module_readings": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string"},
                        "gap_id": {"type": "string"},
                        "priority": {"type": "string"},
                        "content": {"type": "string"},
                        "line_count": {"type": "integer"},
                        "read_method": {
                            "type": "string",
                            "enum": ["detail_view", "local_read", "detail_view+local_read"],
                        },
                    },
                    "required": ["path", "gap_id", "content", "read_method"],
                },
            },
            "files_read": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["repo_name", "module_readings", "files_read"],
    },
)

ABSORPTION_V3_LEARNING = Format(
    id="absorption.learning",
    name="AbsorptionLearning",
    description=(
        "LearningExtractorRouter（LLM）看 module.code 后判断的可操作学习发现。"
        "每条 finding：具体实现了什么（what_it_does，基于代码不是宣传）、"
        "与 OmniCompany 当前的差距（omnicompany_delta）、具体行动（action）、"
        "可移植性评级（directly_reusable / worth_learning / reference_only）。"
        "overall_assessment 给出整体吸纳价值和优先缺口。"
        "调试可见点：findings 是否有代码证据支撑（evidence 非空）、"
        "portability 评级是否与 what_it_does 一致、action 是否具体可执行。"
    ),
    parent="absorption.module.code",
    tags=["domain.absorption", "stage.v3.learning", "phase.v3", "kind.internal"],
    json_schema={
        "type": "object",
        "properties": {
            "repo_name": {"type": "string"},
            "findings": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "gap_id": {"type": "string"},
                        "priority": {"type": "string", "enum": ["P0", "P1", "P2"]},
                        "title": {"type": "string"},
                        "what_it_does": {"type": "string"},
                        "omnicompany_delta": {"type": "string"},
                        "action": {"type": "string"},
                        "portability": {
                            "type": "string",
                            "enum": ["directly_reusable", "worth_learning", "reference_only"],
                        },
                        "evidence": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "file": {"type": "string"},
                                    "lines": {"type": "string"},
                                    "quote": {"type": "string"},
                                },
                            },
                        },
                    },
                    "required": ["gap_id", "priority", "title", "what_it_does", "action", "portability"],
                },
            },
            "overall_assessment": {
                "type": "object",
                "properties": {
                    "absorption_value": {"type": "string", "enum": ["high", "medium", "low"]},
                    "top_priority_gaps": {"type": "array", "items": {"type": "string"}},
                    "summary": {"type": "string"},
                },
            },
        },
        "required": ["repo_name", "findings", "overall_assessment"],
    },
)

ALL_V3_FORMATS = [
    ABSORPTION_V3_REPOMAP,
    ABSORPTION_V3_IMPORTANT_MODULES,
    ABSORPTION_V3_MODULE_CODE,
    ABSORPTION_V3_LEARNING,
]


# ── V3 Stage 2: 报告管线 Formats ─────────────────────────────────────────────
# 文档：docs/plans/[2026-04-13]REPO-ABSORPTION-V3/DESIGN.md §七

ABSORPTION_V3_REPORT = Format(
    id="absorption.report.v3",
    name="AbsorptionReportV3",
    description=(
        "ReportWriterV3Router（LLM）把 absorption.learning 的 findings 整理成综合化人类可读报告。"
        "报告落盘到 data/absorption/<repo>/report.md（单文件累积，每轮追加 iteration 章节）。"
        "structured 字段包含机器可读版本（repo_overview / architecture / capability_map / "
        "highlights / proposals），供外部 agent 消费。iteration 记录当前是第几轮反馈迭代。"
        "feedback_incorporated 列出已合并的人工反馈摘要（多轮累积）。"
        "调试可见点：report_path 是否存在、report_md 是否非空、iteration 是否递增。"
    ),
    parent="absorption.learning",
    tags=["domain.absorption", "stage.v3.report", "phase.v3", "human_readable", "kind.internal"],
    json_schema={
        "type": "object",
        "properties": {
            "repo_name": {"type": "string"},
            "report_path": {"type": "string", "description": "data/absorption/<repo>/report.md"},
            "report_md": {"type": "string", "description": "报告全文 Markdown"},
            "structured": {
                "type": "object",
                "properties": {
                    "repo_overview": {"type": "string"},
                    "architecture": {"type": "string"},
                    "capability_map": {"type": "object", "additionalProperties": {"type": "string"}},
                    "highlights": {"type": "array"},
                    "proposals": {"type": "array"},
                },
            },
            "iteration": {"type": "integer", "minimum": 1},
            "feedback_incorporated": {
                "type": "array",
                "items": {"type": "string"},
                "description": "已合并的人工反馈摘要（多轮迭代累积）",
            },
        },
        "required": ["repo_name", "report_path", "report_md", "iteration"],
    },
)

ABSORPTION_V3_FEEDBACK = Format(
    id="absorption.feedback",
    name="AbsorptionFeedback",
    description=(
        "人工写入 data/absorption/<repo>/feedback.md 后，HumanFeedbackGateV3Router 读取并解析产出的反馈结构。"
        "feedback_text 保留原文，directions 是解析出的补充学习方向列表（如'补学 Feishu 接入'）。"
        "若 feedback.md 不存在则为空反馈（directions=[]），视为本轮完成，FeedbackRouterV3 路由到 EMIT。"
        "读取后将 feedback.md 重命名为 feedback_<iteration>.md.done，避免重复读。"
        "调试可见点：feedback_text 是否为原文、directions 解析是否合理、iteration 是否对应正确轮次。"
    ),
    parent="absorption.report.v3",
    tags=["domain.absorption", "stage.v3.feedback", "phase.v3", "kind.internal"],
    json_schema={
        "type": "object",
        "properties": {
            "repo_name": {"type": "string"},
            "feedback_text": {"type": "string", "description": "人工意见原文（无反馈则为空字符串）"},
            "directions": {
                "type": "array",
                "items": {"type": "string"},
                "description": "解析出的补充学习方向列表（空=本轮完成）",
            },
            "iteration": {"type": "integer"},
            "reviewer": {"type": "string", "description": "审核者（可为 'auto-pass'）"},
            "has_feedback": {"type": "boolean", "description": "是否有实质性人工反馈"},
        },
        "required": ["repo_name", "feedback_text", "directions", "iteration", "has_feedback"],
    },
)

ABSORPTION_V3_SUPPLEMENT_REQUEST = Format(
    id="absorption.supplement_request",
    name="AbsorptionSupplementRequest",
    description=(
        "FeedbackRouterV3 在判断需要补充学习时构建的定向补充请求，路由回 ModuleExplorerRouter。"
        "supplement_guidance 携带本轮补充方向（来自人工反馈，传给 ModuleExplorer 的系统 prompt）。"
        "previous_findings 和 previous_files_read 避免重复探索已有内容。"
        "此 Format 通过 JUMP 路由回到 module_explorer 节点，形成反馈迭代循环。"
        "调试可见点：supplement_guidance 是否具体可操作、previous_files_read 是否覆盖了已读文件。"
    ),
    parent="absorption.feedback",
    tags=["domain.absorption", "stage.v3.supplement", "phase.v3", "kind.internal"],
    json_schema={
        "type": "object",
        "properties": {
            "repo_name": {"type": "string"},
            "repo_local_path": {"type": "string"},
            "self_portrait": {"type": "string"},
            "supplement_guidance": {
                "type": "string",
                "description": "本轮补充方向（具体指导 ModuleExplorer 去哪里找什么）",
            },
            "previous_findings": {
                "type": "array",
                "description": "已有发现（避免重复，传给 ModuleExplorer 上下文）",
            },
            "previous_files_read": {
                "type": "array",
                "items": {"type": "string"},
                "description": "已读文件列表（避免重复读）",
            },
            "coarse_view": {"type": "string", "description": "全量地图（复用原 repomap）"},
            "detail_views": {"type": "object", "description": "细粒度视图（复用原 repomap）"},
            "iteration": {"type": "integer"},
        },
        "required": ["repo_name", "repo_local_path", "self_portrait", "supplement_guidance", "iteration"],
    },
)

ALL_V3_STAGE2_FORMATS = [
    ABSORPTION_V3_REPORT,
    ABSORPTION_V3_FEEDBACK,
    ABSORPTION_V3_SUPPLEMENT_REQUEST,
]


# ── V3 Stage 3: 工作流修改管线 Formats ───────────────────────────────────────
# 设计文档：docs/plans/[2026-04-14]STAGE3-WORKFLOW-MODIFIER/plan.md

_PROPOSAL_ITEM_SCHEMA = {
    "type": "object",
    "properties": {
        "proposal_id": {"type": "string", "description": "PRO-NNN 格式"},
        "title": {"type": "string"},
        "summary": {"type": "string"},
        "source": {
            "type": "object",
            "properties": {
                "repo": {"type": "string"},
                "finding": {"type": "string"},
                "gap_id": {"type": "string"},
                "priority": {"type": "string", "enum": ["P0", "P1", "P2"]},
                "reference_file": {"type": "string"},
                "reference_lines": {"type": "string"},
            },
            "required": ["repo", "finding", "gap_id", "priority"],
        },
        "type": {
            "type": "string",
            "enum": ["new_package", "new_router", "new_format", "modify_existing"],
        },
        "target_changes": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "action": {"type": "string", "enum": ["create", "modify"]},
                    "description": {"type": "string"},
                },
                "required": ["path", "action", "description"],
            },
        },
        "acceptance_criteria": {"type": "array", "items": {"type": "string"}},
        "risk_level": {"type": "string", "enum": ["low", "medium", "high"]},
        "human_approval_required": {"type": "boolean"},
        "estimated_files": {"type": "integer"},
        "estimated_lines": {"type": "integer"},
    },
    "required": ["proposal_id", "title", "summary", "source", "type",
                 "target_changes", "risk_level", "human_approval_required"],
}

ABSORPTION_S3_PROPOSAL_LIST = Format(
    id="absorption.proposal.list",
    name="AbsorptionProposalList",
    description=(
        "SpecParserRouter（RULE+LLM）从 absorption.report.v3 的改进提案字段解析出的结构化任务列表。"
        "每个 proposal 包含 proposal_id（PRO-NNN）、type（new_package/new_router/new_format/modify_existing）、"
        "target_changes（精确到文件+操作+接口描述）、risk_level（low/medium/high）和 acceptance_criteria。"
        "risk_level=high 的 proposal 将经过 DangerGateRouter 人工审批。"
        "调试可见点：proposals 数量是否合理（3-10个）、每个 proposal 的 type 是否准确分类、"
        "risk_level 推断是否保守（modify_existing 改核心 runner.py 应为 high）。"
    ),
    parent="absorption.report.v3",
    tags=["domain.absorption", "stage.v3.stage3", "phase.v3.s3", "kind.internal"],
    json_schema={
        "type": "object",
        "properties": {
            "repo_name": {"type": "string"},
            "proposals": {"type": "array", "items": _PROPOSAL_ITEM_SCHEMA},
            "total_count": {"type": "integer"},
            "p0_count": {"type": "integer"},
            "pending_review_path": {
                "type": "string",
                "description": "人工审批文件路径 data/domains/absorption/<repo>/pending_proposals.md",
            },
        },
        "required": ["repo_name", "proposals", "total_count"],
    },
)

ABSORPTION_S3_PROPOSAL_APPROVED = Format(
    id="absorption.proposal.approved",
    name="AbsorptionProposalApproved",
    description=(
        "HumanApprovalGateS3Router（RULE）人工审核后的提案列表。"
        "approved_proposals 列出通过审批的 proposal_id；rejected_proposals 含拒绝理由。"
        "auto 模式下 risk=low 直接通过，risk=medium/high 需等待人工写入 approved_proposals.txt。"
        "WorkflowGeneratorRouter 只处理 approved_proposals 中的 proposal，其余跳过。"
        "调试可见点：approved_proposals 是否非空、rejected 的拒绝理由是否有意义。"
    ),
    parent="absorption.proposal.list",
    tags=["domain.absorption", "stage.v3.stage3", "phase.v3.s3", "kind.sink"],
    json_schema={
        "type": "object",
        "properties": {
            "repo_name": {"type": "string"},
            "proposals": {"type": "array", "items": _PROPOSAL_ITEM_SCHEMA},
            "approved_proposals": {"type": "array", "items": {"type": "string"}},
            "rejected_proposals": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "proposal_id": {"type": "string"},
                        "reason": {"type": "string"},
                    },
                },
            },
            "pending_proposals": {
                "type": "array",
                "items": {"type": "string"},
                "description": "待人工审批的 proposal_id（risk=medium/high）",
            },
        },
        "required": ["repo_name", "proposals", "approved_proposals"],
    },
)

ABSORPTION_S3_WORKFLOW_DIFF = Format(
    id="absorption.workflow.diff",
    name="AbsorptionWorkflowDiff",
    description=(
        "WorkflowGeneratorRouter（AgentNodeLoop）逐 proposal 生成代码变更后的产物。"
        "生成的文件写到 data/domains/absorption/<repo>/generated/，不直接修改 src/。"
        "每个 generated_change 包含 proposal_id、文件列表（path+action+content/diff）、"
        "生成前自检结果（doctor_pre_check）和 risk_level。"
        "调试可见点：succeeded 是否与 approved_proposals 数量一致、"
        "doctor_pre_check 是否通过（grade≥C）、failed 的失败原因是否具体。"
    ),
    parent="absorption.proposal.approved",
    tags=["domain.absorption", "stage.v3.stage3", "phase.v3.s3", "kind.internal"],
    json_schema={
        "type": "object",
        "properties": {
            "repo_name": {"type": "string"},
            "generated_changes": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "proposal_id": {"type": "string"},
                        "files": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "path": {"type": "string"},
                                    "action": {"type": "string"},
                                    "content": {"type": "string"},
                                    "description": {"type": "string"},
                                },
                                "required": ["path", "action", "content"],
                            },
                        },
                        "doctor_pre_check": {"type": "object"},
                        "risk_level": {"type": "string"},
                    },
                    "required": ["proposal_id", "files"],
                },
            },
            "generated_dir": {"type": "string", "description": "生成文件的输出目录"},
            "total_proposals_attempted": {"type": "integer"},
            "succeeded": {"type": "integer"},
            "failed": {"type": "integer"},
            "failed_reasons": {"type": "object", "description": "proposal_id → 失败原因"},
        },
        "required": ["repo_name", "generated_changes", "succeeded", "failed"],
    },
)

ABSORPTION_S3_WORKFLOW_APPROVED = Format(
    id="absorption.workflow.approved",
    name="AbsorptionWorkflowApproved",
    description=(
        "DangerGateRouter（RULE）对高风险变更人工审批后的产物。"
        "danger_gate_decisions 记录每个 proposal_id 的决策：approved | rejected | skipped_pending_review。"
        "risk=low/medium 的变更自动通过；risk=high 需等待 data/domains/absorption/<repo>/danger_approved.txt。"
        "WorkflowValidatorRouter 只对 approved 的变更执行 Doctor 检查。"
        "调试可见点：skipped_pending_review 的条目是否有对应的 danger_review.md 说明。"
    ),
    parent="absorption.workflow.diff",
    tags=["domain.absorption", "stage.v3.stage3", "phase.v3.s3", "kind.internal"],
    json_schema={
        "type": "object",
        "properties": {
            "repo_name": {"type": "string"},
            "generated_changes": {"type": "array"},
            "danger_gate_decisions": {
                "type": "object",
                "description": "proposal_id → 'approved' | 'rejected' | 'skipped_pending_review'",
                "additionalProperties": {"type": "string"},
            },
            "danger_review_path": {"type": "string"},
        },
        "required": ["repo_name", "generated_changes", "danger_gate_decisions"],
    },
)

ABSORPTION_S3_WORKFLOW_RESULT = Format(
    id="absorption.workflow.result",
    name="AbsorptionWorkflowResult",
    description=(
        "WorkflowValidatorRouter（RULE+Doctor）对生成变更执行 Doctor 检查后的最终结果。"
        "applied_changes 记录每个变更的文件写入路径（生成目录）和 Doctor 报告。"
        "validation_passed 列出 grade≥C 的 proposal；validation_failed 列出 grade=D 的（含失败原因）。"
        "doctor_summary 汇总 router_grades / format_issues / pipeline_issues，供人工快速定位问题。"
        "此 Format 是 Stage 3 管线的最终输出，EMIT 后人工决策是否将 generated/ 文件合入 src/。"
        "调试可见点：validation_failed 是否有具体 Doctor 诊断、router_grades 的 D 级原因。"
    ),
    parent="absorption.workflow.approved",
    tags=["domain.absorption", "stage.v3.stage3", "phase.v3.s3", "human_readable", "kind.sink"],
    json_schema={
        "type": "object",
        "properties": {
            "repo_name": {"type": "string"},
            "applied_changes": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "proposal_id": {"type": "string"},
                        "files_written": {"type": "array", "items": {"type": "string"}},
                        "doctor_report": {"type": "object"},
                        "grade": {"type": "string", "enum": ["A", "B", "C", "D"]},
                    },
                },
            },
            "validation_passed": {"type": "array", "items": {"type": "string"}},
            "validation_failed": {"type": "array", "items": {"type": "string"}},
            "doctor_summary": {
                "type": "object",
                "properties": {
                    "router_grades": {"type": "object"},
                    "format_issues": {"type": "array"},
                    "pipeline_issues": {"type": "array"},
                },
            },
            "generated_dir": {"type": "string"},
            "next_step": {
                "type": "string",
                "description": "人工决策提示：哪些文件可以合入 src/",
            },
        },
        "required": ["repo_name", "validation_passed", "validation_failed", "doctor_summary"],
    },
)

ABSORPTION_S3_PROPOSAL_FEEDBACK = Format(
    id="absorption.proposal.feedback",
    name="AbsorptionStage3ProposalFeedback",
    description=(
        "ProposalFeedbackGateRouter 读 proposal_feedback.md 后产出的反馈结构，供 "
        "ProposalFeedbackRouterRouter 消费来决定 PASS 到 approval_gate 还是 JUMP 回 spec_parser。"
        "\n\n【字段】"
        "\n- repo_name: 仓库名"
        "\n- feedback_text: proposal_feedback.md 原文（完整无截断）"
        "\n- directions[]: 从 feedback_text 解析的补充方向列表（每项自由文本）"
        "\n- has_feedback: bool，文件存在且内容非空为 true"
        "\n- iteration: 当前迭代轮次（初始 1）"
        "\n- proposals[]: 上一轮产出的完整提案清单（透传给下游决策）"
        "\n\n【上游承诺】ProposalFeedbackGateRouter 保证：若 proposal_feedback.md 不存在则 "
        "has_feedback=False、directions=[]；若存在则完整读入原文（铁律 A，不截断），解析为 "
        "directions 并把文件重命名为 proposal_feedback_<iteration>.md.done。"
        "\n\n【下游用途】ProposalFeedbackRouterRouter 按 has_feedback 分路：空 → PASS 给 "
        "HumanApprovalGateS3；非空 → JUMP 回 spec_parser 带 supplement_guidance。"
        "\n\n【调试可见点】feedback_text 长度、directions 条数、has_feedback 是否与 directions 一致。"
        "\n\n【最小合法样例】"
        '{"repo_name":"hermes-agent","feedback_text":"- learning_loop 漏了","directions":["learning_loop 漏了"],"has_feedback":true,"iteration":1,"proposals":[...]}'
    ),
    parent="absorption.proposal.list",
    tags=["domain.absorption", "stage.v3.stage3", "phase.v3", "feedback", "kind.internal"],
    json_schema={
        "type": "object",
        "properties": {
            "repo_name": {"type": "string"},
            "feedback_text": {"type": "string"},
            "directions": {"type": "array", "items": {"type": "string"}},
            "has_feedback": {"type": "boolean"},
            "iteration": {"type": "integer"},
            "proposals": {"type": "array"},
        },
        "required": ["repo_name", "feedback_text", "directions", "has_feedback", "iteration"],
    },
)


ABSORPTION_S3_PROPOSAL_SUPPLEMENT_REQUEST = Format(
    id="absorption.proposal.supplement_request",
    name="AbsorptionStage3ProposalSupplementRequest",
    description=(
        "ProposalFeedbackRouterRouter 在判断需要补充综合时构建的定向补充请求，路由回 SpecParserRouter。"
        "复现 Stage 2 的 supplement pattern，结构对 spec_parser 的 composite FORMAT_IN 兼容"
        "（每个 components 的字段都平铺在顶层，spec_parser 走兜底路径读取）。"
        "\n\n【字段】"
        "\n- repo_name: 仓库名"
        "\n- supplement_guidance: 从 directions 组合的补充要求自由文本（注入 spec_parser prompt）"
        "\n- iteration: 下一轮迭代号（= 当前 iteration + 1）"
        "\n- previous_proposals[]: 上一轮产出的提案清单（避免重复；spec_parser 加 'don't duplicate' 指令）"
        "\n- absorption.report.v3 / omni.self.capability_inventory / omni.self.gap_registry: 复合 FORMAT_IN 三路原料透传"
        "\n\n【上游承诺】ProposalFeedbackRouterRouter 保证：supplement_guidance 非空（空的话走 PASS 路径不发 JUMP）；三路 components 都从 feedback 输入里透传。"
        "\n\n【下游用途】SpecParserRouter JUMP 消费。SpecParser 读 supplement_guidance，在原 prompt 基础上加一段 '本轮补充要求 + 已产出提案（别重复）' 指令。"
        "\n\n【调试可见点】supplement_guidance 非空；previous_proposals 有条目；iteration 递增。"
        "\n\n【最小合法样例】"
        '{"repo_name":"hermes-agent","supplement_guidance":"补 learning_loop","iteration":2,"previous_proposals":[...],"absorption.report.v3":{...},"omni.self.capability_inventory":{...},"omni.self.gap_registry":{...}}'
    ),
    parent="absorption.proposal.feedback",
    tags=["domain.absorption", "stage.v3.stage3", "phase.v3", "supplement", "kind.internal"],
    json_schema={
        "type": "object",
        "properties": {
            "repo_name": {"type": "string"},
            "supplement_guidance": {"type": "string"},
            "iteration": {"type": "integer"},
            "previous_proposals": {"type": "array"},
            "absorption.report.v3": {"type": "object"},
            "omni.self.capability_inventory": {"type": "object"},
            "omni.self.gap_registry": {"type": "object"},
        },
        "required": ["repo_name", "supplement_guidance", "iteration"],
    },
)


ALL_V3_STAGE3_FORMATS = [
    ABSORPTION_S3_PROPOSAL_LIST,
    ABSORPTION_S3_PROPOSAL_APPROVED,
    ABSORPTION_S3_WORKFLOW_DIFF,
    ABSORPTION_S3_WORKFLOW_APPROVED,
    ABSORPTION_S3_WORKFLOW_RESULT,
    ABSORPTION_S3_PROPOSAL_FEEDBACK,
    ABSORPTION_S3_PROPOSAL_SUPPLEMENT_REQUEST,
]


# ══════════════════════════════════════════════════════════════════════════
# OmniCompany 自知识 Formats (2026-04-18, P-13/F-15 示范, 命名空间 omni.self.*)
# ══════════════════════════════════════════════════════════════════════════
# 这些 Format 描述 OmniCompany 自身（不是 absorption 的产物）。
# 命名用 omni.self.* 而非 absorption.* 是因为：ModuleExplorer / WorkflowGenerator
# / 未来的自审都可能复用。物理上放这里因为 absorption 是首个消费者。
# ══════════════════════════════════════════════════════════════════════════

# ── Query Formats（Loader 的输入）──────────────────────────────────────

OMNI_SELF_CAPABILITY_INVENTORY_QUERY = Format(
    id="omni.self.capability_inventory_query",
    name="OmniSelfCapabilityInventoryQuery",
    description=(
        "CapabilityInventoryLoader 的查询参数 Format。"
        "描述'这次要加载哪些模块的能力清单'。"
        "本 Format 所有字段均可选，全部缺省 = 取全部默认（active+design 的所有模块）。"
        "\n\n【字段语义】"
        "\n- filter_maturity[]: 仅收这些 maturity 的 DESIGN.md。缺省 ['active','design']。"
        " 不含 'skeleton' 因其核心目的是 TBD 占位。"
        "\n- filter_tags[]: 仅收这些能力分类的模块。缺省 = 不过滤。"
        "\n- include_readme_map: 是否在输出里带 README §一能力表原文。缺省 true。"
        "\n- requested_by: 调用方标识（trace 用）。缺省 'absorption.query_builder'。"
        "\n\n【值域/枚举】"
        "\n- filter_maturity 每项 ∈ {active, design}"
        "\n- filter_tags 每项 ∈ {learning, diagnosis, execution, persistence, protocol, domain}"
        "\n\n【上游承诺】由 CapabilityInventoryQueryBuilder 从 absorption.repomap 的 repo_name 派生，"
        "始终产出合法查询（即使 repo_name 缺失也走默认值）。"
        "\n\n【下游用途】CapabilityInventoryLoaderRouter 唯一消费；依照字段裁剪实际扫盘范围。"
        "\n\n【最小合法样例】"
        '{"filter_maturity": ["active","design"], "include_readme_map": true, "requested_by": "absorption"}'
    ),
    parent="absorption.repomap",
    tags=["omni.self", "phase.knowledge", "query", "kind.internal"],
    json_schema={
        "type": "object",
        "properties": {
            "filter_maturity": {
                "type": "array",
                "items": {"type": "string", "enum": ["active", "design"]},
                "description": "要收的 maturity 级别，缺省 ['active','design']",
            },
            "filter_tags": {
                "type": "array",
                "items": {"type": "string", "enum": ["learning", "diagnosis", "execution", "persistence", "protocol", "domain"]},
                "description": "按能力分类过滤，缺省 = 全部",
            },
            "include_readme_map": {
                "type": "boolean",
                "description": "是否带 README §一能力表原文，缺省 true",
            },
            "requested_by": {
                "type": "string",
                "description": "调用方标识（trace 用）",
            },
        },
    },
)

OMNI_SELF_GAP_REGISTRY_QUERY = Format(
    id="omni.self.gap_registry_query",
    name="OmniSelfGapRegistryQuery",
    description=(
        "GapRegistryLoader 的查询参数 Format。"
        "描述'这次要加载哪些缺口'。"
        "本 Format 所有字段均可选，全部缺省 = 取全部默认（docs/gaps/ 下所有非归档的 G*.md）。"
        "\n\n【字段语义】"
        "\n- filter_priority[]: 仅收这些优先级的 gap。缺省 = ['P0','P1','P2']（全部）。"
        "\n- filter_state[]: 仅收这些状态的 gap。缺省 = 不过滤。"
        "\n- include_index_summary: 是否带 gaps/INDEX.md 摘要。缺省 true。"
        "\n- requested_by: 调用方标识。缺省 'absorption.query_builder'。"
        "\n\n【值域/枚举】"
        "\n- filter_priority 每项 ∈ {P0, P1, P2}"
        "\n- filter_state 每项 ∈ {未动, 进展中, 已完成, 已废弃}"
        "\n\n【上游承诺】由 GapRegistryQueryBuilder 从 absorption.repomap.repo_name 派生。"
        "\n\n【下游用途】GapRegistryLoaderRouter 唯一消费。"
        "\n\n【最小合法样例】"
        '{"filter_priority": ["P0","P1","P2"], "include_index_summary": true, "requested_by": "absorption"}'
    ),
    parent="absorption.repomap",
    tags=["omni.self", "phase.knowledge", "query", "kind.internal"],
    json_schema={
        "type": "object",
        "properties": {
            "filter_priority": {
                "type": "array",
                "items": {"type": "string", "enum": ["P0", "P1", "P2"]},
            },
            "filter_state": {
                "type": "array",
                "items": {"type": "string"},
            },
            "include_index_summary": {"type": "boolean"},
            "requested_by": {"type": "string"},
        },
    },
)

# ── Knowledge Formats（Loader 的输出）──────────────────────────────────

OMNI_SELF_CAPABILITY_INVENTORY = Format(
    id="omni.self.capability_inventory",
    name="OmniSelfCapabilityInventory",
    description=(
        "OmniCompany 当前代码里实际存在的模块清单（能力快照）。"
        "由 CapabilityInventoryLoaderRouter 扫 src/omnicompany/**/DESIGN.md 产出。"
        "\n\n【字段语义】"
        "\n- generated_at: ISO8601 时间戳。"
        "\n- source_root: 扫描根路径（固定 'src/omnicompany'）。"
        "\n- source_commit: git 提交短 hash 或 'working tree'。"
        "\n- module_count: modules 列表长度（冗余便于 LLM 快速看规模）。"
        "\n- modules[]: 每条 {path, maturity, one_line, tags}"
        "\n  - path: 相对 source_root 的路径，例如 'packages/services/absorption'。列表内唯一。"
        "\n  - maturity: DESIGN.md 的 status 字段。"
        "\n  - one_line: 来自 ## 核心目的 节第一段（非 TBD）的压缩版，≤5 行。"
        "\n  - tags: 能力分类标签，从 README §一推断。"
        "\n- readme_capability_map: README §一能力分类表的原文 Markdown（可为空字符串）。"
        "\n\n【值域/枚举】"
        "\n- maturity ∈ {active, design}（不含 skeleton，其核心目的是 TBD）"
        "\n- tags 每项 ∈ {learning, diagnosis, execution, persistence, protocol, domain, unknown}"
        "\n\n【上游承诺】Loader 保证 modules 都是从实际 DESIGN.md 扫到的、核心目的非 TBD。"
        "\n\n【下游用途】"
        "\n- SpecParserRouter: 判断 proposal 是否对应已有模块（already_exists 检测）"
        "\n- WorkflowGenerator（未来）: 决定新代码归哪个包"
        "\n- 自审节点（可能未来）: 同上"
        "\n\n【不包含什么（显式边界）】"
        "\n- 模块内部接口细节（下游自读 DESIGN.md 的 ## 核心接口）"
        "\n- 模块间依赖关系（下游用 Format.parent/components）"
        "\n- 缺口 / 希望有什么（见 omni.self.gap_registry）"
        "\n- 架构铁律（未来的 omni.self.architecture_invariants）"
        "\n\n【调试可见点】module_count 是否接近当前 Guardian OMNI-034 报告的 active+design 总数；"
        "tags 分布是否合理；readme_capability_map 是否非空。"
        "\n\n【最小合法样例】"
        '{"generated_at": "2026-04-18T14:00:00Z", "source_root": "src/omnicompany", '
        '"source_commit": "working tree", "module_count": 1, '
        '"modules": [{"path": "protocol", "maturity": "active", '
        '"one_line": "LAP 核心协议。纯数据声明。", "tags": ["protocol"]}], '
        '"readme_capability_map": "..."}'
    ),
    parent="omni.self.capability_inventory_query",
    tags=["omni.self", "phase.knowledge", "snapshot", "kind.internal"],
    json_schema={
        "type": "object",
        "properties": {
            "generated_at": {"type": "string", "description": "ISO8601"},
            "source_root": {"type": "string"},
            "source_commit": {"type": "string"},
            "module_count": {"type": "integer", "minimum": 0},
            "modules": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string"},
                        "maturity": {"type": "string", "enum": ["active", "design"]},
                        "one_line": {"type": "string"},
                        "tags": {
                            "type": "array",
                            "items": {"type": "string", "enum": [
                                "learning", "diagnosis", "execution",
                                "persistence", "protocol", "domain", "unknown"
                            ]},
                        },
                    },
                    "required": ["path", "maturity", "one_line"],
                },
            },
            "readme_capability_map": {"type": "string"},
        },
        "required": ["generated_at", "source_root", "module_count", "modules"],
    },
)

OMNI_SELF_GAP_REGISTRY = Format(
    id="omni.self.gap_registry",
    name="OmniSelfGapRegistry",
    description=(
        "OmniCompany 已识别的希望有但还没有的能力清单（缺口档案）。"
        "由 GapRegistryLoaderRouter 扫 docs/gaps/G*.md 产出。"
        "\n\n【字段语义】"
        "\n- generated_at: ISO8601。"
        "\n- source_dir: 扫描根（固定 'docs/gaps'）。"
        "\n- gap_count: gaps 列表长度。"
        "\n- gaps[]: 每条 {id, title, priority, state, verification, what_missing, source_path}"
        "\n  - id: G+数字，档案文件名前缀。列表内唯一。"
        "\n  - title: 从档案一级标题提取（去掉 'G1 · ' 前缀后的部分）。"
        "\n  - priority: 从 ## 元信息 节的 **优先级** 字段。"
        "\n  - state: 从 ## 元信息 节的 **状态** 字段。"
        "\n  - verification: 从 OmniMark 头的 verification= 字段。"
        "\n  - what_missing: ## 缺什么 节原文（压缩到 ≤12 行）。"
        "\n  - source_path: 档案相对仓库根的路径。"
        "\n- index_summary: docs/gaps/INDEX.md 的前 30 行非空内容。"
        "\n\n【值域/枚举】"
        "\n- priority ∈ {P0, P1, P2}"
        "\n- state ∈ {未动, 进展中, 已完成, 已废弃}"
        "\n- verification ∈ {verified, needs_verification, partial}"
        "\n\n【上游承诺】Loader 保证只收 docs/gaps/G*.md（跳过 INDEX/_template/archived）。"
        "\n\n【下游用途】"
        "\n- SpecParserRouter: 判断 proposal 对应哪个 gap_id，填 source.gap_id"
        "\n- ModuleExplorer（可扩展）: 按 gap 引导吸纳探索方向"
        "\n- dashboard（未来）: 可视化 gap 进展"
        "\n\n【不包含什么】"
        "\n- 能力清单（见 omni.self.capability_inventory）"
        "\n- 解决方案（LLM 接到本 Format 自己构造）"
        "\n- pending SpecPatch / proposal 队列"
        "\n- gap 间依赖关系（V1 不建模）"
        "\n\n【调试可见点】gap_count 应等于 docs/gaps/G*.md 文件数；"
        "priority / state / verification 无异常值；what_missing 非空。"
        "\n\n【最小合法样例】"
        '{"generated_at": "2026-04-18T14:00:00Z", "source_dir": "docs/gaps", '
        '"gap_count": 1, "gaps": [{"id": "G1", "title": "工具鲁棒性", "priority": "P0", '
        '"state": "进展中", "verification": "partial", '
        '"what_missing": "Tool 层无统一重试...", '
        '"source_path": "docs/gaps/G1_tool_robustness.md"}], "index_summary": "..."}'
    ),
    parent="omni.self.gap_registry_query",
    tags=["omni.self", "phase.knowledge", "snapshot", "kind.internal"],
    json_schema={
        "type": "object",
        "properties": {
            "generated_at": {"type": "string"},
            "source_dir": {"type": "string"},
            "gap_count": {"type": "integer", "minimum": 0},
            "gaps": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "string", "pattern": "^G\\d+$"},
                        "title": {"type": "string"},
                        "priority": {"type": "string", "enum": ["P0", "P1", "P2"]},
                        "state": {"type": "string"},
                        "verification": {"type": "string", "enum": ["verified", "needs_verification", "partial"]},
                        "what_missing": {"type": "string"},
                        "source_path": {"type": "string"},
                    },
                    "required": ["id", "title", "priority", "what_missing"],
                },
            },
            "index_summary": {"type": "string"},
        },
        "required": ["generated_at", "source_dir", "gap_count", "gaps"],
    },
)

OMNI_SELF_RECEPTION_INTENT_QUERY = Format(
    id="omni.self.reception_intent_query",
    name="OmniSelfReceptionIntentQuery",
    description=(
        "ReceptionIntentsLoader 的查询参数 Format。"
        "描述'这次要加载哪些基础设施模块的接收意愿档案'。"
        "本 Format 所有字段均可选，全部缺省 = 取全部默认"
        "（src/omnicompany/runtime|protocol|core|bus|primitives|tools|tracing 下 active|design DESIGN.md"
        "且含第 8 节 ## 接收意愿 的模块）。"
        "\n\n【字段语义】"
        "\n- filter_modules[]: 仅收这些模块路径（相对 src/omnicompany）。缺省 = 不过滤。"
        "\n- include_soft_preferences: 是否返回 soft_preferences 字段。缺省 true。"
        "\n- requested_by: 调用方标识。缺省 'absorption.query_builder'。"
        "\n\n【上游承诺】由 ReceptionIntentsQueryBuilder 从 absorption.repomap.repo_name 派生，"
        "始终产出合法查询（即使 repo_name 缺失也走默认值）。"
        "\n\n【下游用途】ReceptionIntentsLoaderRouter 唯一消费。"
        "\n\n【最小合法样例】"
        '{"filter_modules": null, "include_soft_preferences": true, "requested_by": "absorption"}'
    ),
    parent="absorption.repomap",
    tags=["omni.self", "phase.knowledge", "query", "kind.internal"],
    json_schema={
        "type": "object",
        "properties": {
            "filter_modules": {
                "type": ["array", "null"],
                "items": {"type": "string"},
                "description": "模块路径过滤（如 ['runtime/llm','runtime/agent']），缺省 null = 全部",
            },
            "include_soft_preferences": {
                "type": "boolean",
                "description": "是否带 soft_preferences 字段",
            },
            "requested_by": {"type": "string"},
        },
    },
)

OMNI_SELF_RECEPTION_INTENTS = Format(
    id="omni.self.reception_intents",
    name="OmniSelfReceptionIntents",
    description=(
        "OmniCompany 基础设施模块的'接收意愿'清单——各模块声明\"我欢迎吸收什么主题的进步\"。"
        "用于 absorption 管线在对比外部仓库与自家模块时识别**超出已识别缺口范围**的潜在吸纳价值。"
        "\n\n【字段语义】"
        "\n- generated_at: ISO8601。"
        "\n- source_root: 扫描根（固定 'src/omnicompany'）。"
        "\n- module_count: intents 列表长度。"
        "\n- intents[]: 每条 {module_path, maturity, welcome_themes, hard_constraints, "
        "soft_preferences, maturity_preference, source_path}"
        "\n  - module_path: 相对 source_root 的路径（如 'runtime/llm'）。列表内唯一。"
        "\n  - maturity: 来源 DESIGN.md 的 status 字段。"
        "\n  - welcome_themes[]: 欢迎吸收的主题（自由文本），每条为一个领域/概念。"
        "\n  - hard_constraints[]: 违反即不吸纳的硬约束。"
        "\n  - soft_preferences[]: 违反降低优先级但不阻塞（查询可选剔除）。"
        "\n  - maturity_preference: 对外部源代码成熟度的要求。"
        "\n  - source_path: DESIGN.md 档案相对仓库根的路径。"
        "\n\n【值域/枚举】"
        "\n- maturity ∈ {active, design}"
        "\n- maturity_preference ∈ {any, stable_only, production_validated}"
        "\n\n【上游承诺】Loader 只扫基础设施前缀下 active|design 的 DESIGN.md 的第 8 节；"
        "空骨架（三个列表字段都空）不会出现在 intents 中。"
        "\n\n【下游用途】"
        "\n- ModuleExplorer: 判断外部仓库文件属于 '已有可改进 / 已知缺口 / 愿接收新主题 / 架构冲突' 哪一档；"
        "hard_constraints 字段用于识别架构冲突并**显式标注不吸纳**。"
        "\n\n【不包含什么】"
        "\n- 能力清单（见 omni.self.capability_inventory）"
        "\n- 缺口档案（见 omni.self.gap_registry）"
        "\n- 具体解决方案（LLM 消费本 Format 后自己构造）"
        "\n\n【调试可见点】module_count 应等于基础设施模块中含第 8 节的数量；"
        "welcome_themes 每条非空；maturity_preference 无非法值。"
        "\n\n【最小合法样例】"
        '{"generated_at": "2026-04-18T14:00:00Z", "source_root": "src/omnicompany", '
        '"module_count": 1, "intents": [{"module_path": "runtime/llm", "maturity": "active", '
        '"welcome_themes": ["多模型 ensemble","智能模型路由"], "hard_constraints": ["qwen3.6-plus 主模型铁律"], '
        '"soft_preferences": [], "maturity_preference": "any", '
        '"source_path": "src/omnicompany/runtime/llm/DESIGN.md"}]}'
    ),
    parent="omni.self.reception_intent_query",
    tags=["omni.self", "phase.knowledge", "snapshot", "kind.internal"],
    json_schema={
        "type": "object",
        "properties": {
            "generated_at": {"type": "string"},
            "source_root": {"type": "string"},
            "module_count": {"type": "integer", "minimum": 0},
            "intents": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "module_path": {"type": "string"},
                        "maturity": {"type": "string", "enum": ["active", "design"]},
                        "welcome_themes": {"type": "array", "items": {"type": "string"}},
                        "hard_constraints": {"type": "array", "items": {"type": "string"}},
                        "soft_preferences": {"type": "array", "items": {"type": "string"}},
                        "maturity_preference": {
                            "type": "string",
                            "enum": ["any", "stable_only", "production_validated"],
                        },
                        "source_path": {"type": "string"},
                    },
                    "required": [
                        "module_path",
                        "maturity",
                        "welcome_themes",
                        "hard_constraints",
                        "maturity_preference",
                    ],
                },
            },
        },
        "required": ["generated_at", "source_root", "module_count", "intents"],
    },
)

ALL_OMNI_SELF_FORMATS = [
    OMNI_SELF_CAPABILITY_INVENTORY_QUERY,
    OMNI_SELF_GAP_REGISTRY_QUERY,
    OMNI_SELF_RECEPTION_INTENT_QUERY,
    OMNI_SELF_CAPABILITY_INVENTORY,
    OMNI_SELF_GAP_REGISTRY,
    OMNI_SELF_RECEPTION_INTENTS,
]

# ── Composite Format (SpecParser fan-in 汇聚点) ─────────────────────────

ABSORPTION_PROPOSAL_CONTEXT = Format(
    id="absorption.proposal.context",
    name="AbsorptionProposalContext",
    description=(
        "SpecParser 生成提案所需的**复合上下文**。三路 fan-in 的 composite Format，"
        "components 声明了三个独立语义单元，Runner 在 _merge_inputs 时用 components 的"
        "format_id 作 key（避免三个 Format 顶层字段碰撞如 generated_at）。"
        "\n\n【components 语义】"
        "\n- absorption.report.v3: 来自 ReportWriterV3 的吸纳报告 + 结构化 findings / proposals"
        "\n- omni.self.capability_inventory: OmniCompany 当前模块清单（判断 already_exists 用）"
        "\n- omni.self.gap_registry: 已识别缺口（填 proposal.source.gap_id 用）"
        "\n\n【访问方式】SpecParser.run() 通过 input_data['<component_id>'] 精确访问各路，"
        "例如 `input_data['absorption.report.v3']['structured']['proposals']`。"
        "\n\n【上游承诺】所有三个 components 都已产出 PASS Verdict；若任一失败，本 Format"
        "不会被 runner 构造（target 节点拿不到合并输入，FAIL 路由触发）。"
        "\n\n【下游用途】SpecParserRouter 唯一消费。"
        "\n\n【调试可见点】input_data 是否三个 format_id 都存在；每个子 Format 的 required 字段是否齐全。"
        "\n\n【最小合法样例】"
        '{"absorption.report.v3": {...}, "omni.self.capability_inventory": {...}, "omni.self.gap_registry": {...}}'
    ),
    parent="absorption.report.v3",
    components=[
        "absorption.report.v3",
        "omni.self.capability_inventory",
        "omni.self.gap_registry",
    ],
    tags=["domain.absorption", "stage.v3.stage3", "phase.v3.s3", "composite", "kind.internal"],
    json_schema={
        "type": "object",
        "properties": {
            "absorption.report.v3": {"type": "object"},
            "omni.self.capability_inventory": {"type": "object"},
            "omni.self.gap_registry": {"type": "object"},
        },
        "required": [
            "absorption.report.v3",
            "omni.self.capability_inventory",
            "omni.self.gap_registry",
        ],
    },
)

ALL_OMNI_SELF_FORMATS.append(ABSORPTION_PROPOSAL_CONTEXT)


ABSORPTION_MODULE_EXPLORATION_CONTEXT = Format(
    id="absorption.module_exploration.context",
    name="AbsorptionModuleExplorationContext",
    description=(
        "ModuleExplorer 探索外部仓库所需的**复合上下文**。四路 fan-in 的 composite Format，"
        "components 声明四个独立语义单元（外部仓库地图 + OmniCompany 能力清单 + 缺口 + 接收意愿），"
        "Runner 在 _merge_inputs 时用 format_id 作 key。"
        "\n\n【components 语义】"
        "\n- absorption.repomap: 来自 RepoMapper 的外部仓库粗/细粒度地图，含 repo_local_path / repo_name"
        "\n- omni.self.capability_inventory: OmniCompany 当前模块清单（判断 '已有可改进'）"
        "\n- omni.self.gap_registry: 已识别缺口（判断 '已知缺口'）"
        "\n- omni.self.reception_intents: 基础设施模块的接收意愿（判断 '愿接收新主题 / 架构冲突'）"
        "\n\n【访问方式】ModuleExplorer.run() 通过 input_data['<component_id>'] 精确访问各路，"
        "例如 `input_data['absorption.repomap']['detail_views']` / "
        "`input_data['omni.self.reception_intents']['intents']`。"
        "\n\n【上游承诺】四路均产出 PASS Verdict；任一失败则本 Format 不被构造（FAIL 路由触发）。"
        "ModuleExplorer 内部对 wiki 三路有缓存兜底（进程级 lru_cache），supplement/feedback 回路"
        "JUMP 回本节点时仍可工作，但会在 diagnosis 记录 '走缓存兜底' 警告。"
        "\n\n【下游用途】ModuleExplorerRouter 唯一消费。四元判断原则："
        "\n  1) 已有可改进（外部文件 → OmniCompany 某模块可被替换/优化）"
        "\n  2) 已知缺口（外部文件 → 对应某个 gap）"
        "\n  3) 愿接收新主题（外部文件 → 对应某个 reception_intent.welcome_themes）"
        "\n  4) 架构冲突（外部文件违反某个 hard_constraint → 标注但不跳过，交下游评估）"
        "\n\n【调试可见点】input_data 是否四个 format_id 都存在；"
        "intents/modules/gaps 计数是否符合预期。"
        "\n\n【最小合法样例】"
        '{"absorption.repomap": {...}, "omni.self.capability_inventory": {...}, '
        '"omni.self.gap_registry": {...}, "omni.self.reception_intents": {...}}'
    ),
    parent="absorption.repomap",
    components=[
        "absorption.repomap",
        "omni.self.capability_inventory",
        "omni.self.gap_registry",
        "omni.self.reception_intents",
    ],
    tags=["domain.absorption", "stage.v3", "phase.v3.explore", "composite", "kind.internal"],
    json_schema={
        "type": "object",
        "properties": {
            "absorption.repomap": {"type": "object"},
            "omni.self.capability_inventory": {"type": "object"},
            "omni.self.gap_registry": {"type": "object"},
            "omni.self.reception_intents": {"type": "object"},
        },
        "required": [
            "absorption.repomap",
            "omni.self.capability_inventory",
            "omni.self.gap_registry",
            "omni.self.reception_intents",
        ],
    },
)

ALL_OMNI_SELF_FORMATS.append(ABSORPTION_MODULE_EXPLORATION_CONTEXT)


def register_formats(registry: FormatRegistry) -> None:
    """把 absorption 域的 Format 注册到全局 FormatRegistry。

    依赖内置 Format `intent` (BUILTIN_FORMATS 提供)。
    可被 cli/unified.py 的 _try_load_format_registry 自动发现。
    """
    for fmt in (ALL_FORMATS + ALL_V2_FORMATS + ALL_V3_FORMATS +
                ALL_V3_STAGE2_FORMATS + ALL_V3_STAGE3_FORMATS +
                ALL_OMNI_SELF_FORMATS):
        if not registry.is_registered(fmt.id):
            registry.register(fmt)
