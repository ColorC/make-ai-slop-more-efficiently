# [OMNI] origin=claude-code domain=omnicompany/core ts=2026-04-08T03:23:35Z
# [OMNI] material_id="material:omnicompany.core.pipelines.pipeline_registrar.aggregator.py"
"""omnicompany.core.pipelines — 管线懒加载注册（基础设施）

将所有已知管线注册到全局 Registry，但使用延迟 import 避免在 CLI 启动时
拉入 demogame/unity/evolution 等重依赖。

原则：
- 简单管线（workflow 类）直接在自己的模块里 _register()
- 复杂管线在此统一做懒注册，build_team/build_bindings 均为 lambda
  内部 import
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def register_all() -> None:
    """注册所有已知管线。可安全重复调用。"""
    from omnicompany.core.registry import register, PipelineEntry, CliArg
    from omnicompany.core.config import omni_workspace_root

    # ── omnicompany 自管理核心管线 ──

    try:
        register(PipelineEntry(
            name="material-diagnosis",
            description=(
                "Material 健康诊断 — 单 Material 或批量 (原 format-diagnosis, 2026-04-22 命名规范化).\n"
                "  单 Material: omni run material-diagnosis --material_id guardian.check-request\n"
                "  多 ID:       omni run material-diagnosis --ids guardian.check-request,bw.epic\n"
                "  目录:        omni run material-diagnosis --folder src/omnicompany/packages/services/guardian\n"
                "  文件:        omni run material-diagnosis --file .../formats.py\n"
                "  域前缀:      omni run material-diagnosis --domain bw"
            ),
            domain="doctor",
            build_team=_lazy("omnicompany.packages.services._diagnosis.doctor.team",
                                 "build_team"),
            build_bindings=_lazy_fn("omnicompany.packages.services._diagnosis.doctor.run",
                                    "build_bindings"),
            default_db_dir="data/services/doctor",
            default_max_steps=10,
            cli_args=[
                CliArg(name="material_id", help="单个 Material ID (如 guardian.check-request)"),
                CliArg(name="ids", help="多个 Material ID, 逗号分隔"),
                CliArg(name="folder", help="扫描目录下所有 formats.py"),
                CliArg(name="file", help="扫描指定 formats.py 文件"),
                CliArg(name="domain", help="扫描指定域前缀 (如 bw、guardian)"),
                CliArg(name="source_root", help="源码根目录 (默认 src/omnicompany/)"),
                CliArg(name="grade", help="只显示指定等级, 逗号分隔 (如 C,D,F)"),
            ],
        ))
    except Exception as e:
        logger.debug("skip material-diagnosis: %s", e)

    try:
        register(PipelineEntry(
            name="format-repair",
            description="Format 自动修复 — 诊断失败项后调用 LLM 规划并 patch 源码，循环至 A 级",
            domain="repair",
            build_team=_lazy("omnicompany.packages.services._core.repair.team",
                                 "build_team"),
            default_db_dir="data/services/repair",
            default_max_steps=5,
            cli_args=[
                CliArg(name="format_id", help="待修复的 Format ID（如 bw.code_spec）",
                       required=True),
                CliArg(name="source_root", help="源码根目录（默认 src/omnicompany/）"),
                CliArg(name="max_iterations", help="最大修复迭代次数（默认 3）"),
            ],
        ))
    except Exception as e:
        logger.debug("skip format-repair: %s", e)

    try:
        register(PipelineEntry(
            name="project-audit",
            description=(
                "项目遍历 + 据真源(我的原始prompt + 真实代码内容 + 文件树)逐条核实完成度 — 不信报告/说明/复选框.\n"
                "  omni run project-audit -i name=quant-lab -i root=E:/WindowsWorkspace/quant-lab\n"
                "  产出: 真实规模 + 采到的原始 prompt + 读过的代码 + 每条计划项 done/partial/not_done/uncertain (claimed 与 verdict 不一致点是重点)"
            ),
            domain="project_audit",
            build_team=_lazy("omnicompany.packages.services._diagnosis.project_audit.team",
                                 "build_team"),
            build_bindings=_lazy_fn("omnicompany.packages.services._diagnosis.project_audit.run",
                                    "build_bindings"),
            default_db_dir="data/services/project_audit",
            default_max_steps=10,
            cli_args=[
                CliArg(name="name", help="项目名 (自有项目可点名)"),
                CliArg(name="root", help="项目根目录绝对路径", required=True),
                CliArg(name="max_plans", help="单次审计计划数上限 (默认 12, 防失控)"),
            ],
        ))
    except Exception as e:
        logger.debug("skip project-audit: %s", e)

    # ── 项目发现 — 据真源(会话 cwd + 仓库扫描)枚举我真做过的项目, 归属过滤掉纯开源 ──
    try:
        register(PipelineEntry(
            name="project-discovery",
            description=(
                "据真源发现'我真做过的项目' — 扫 ~/.claude+~/.codex 会话真实 cwd 频次 + 仓库扫描, 按归属边界标 owned.\n"
                "  omni run project-discovery\n"
                "  产出: 项目清单 (name/root/owned/session_count/evidence) — 完整性铁律: owned=True 的需逐个遍历核实"
            ),
            domain="project_audit",
            build_team=_lazy("omnicompany.packages.services._diagnosis.project_audit.team",
                                 "build_discovery_team"),
            build_bindings=_lazy_fn("omnicompany.packages.services._diagnosis.project_audit.run",
                                    "build_discovery_bindings"),
            default_db_dir="data/services/project_audit",
            default_max_steps=5,
            cli_args=[
                CliArg(name="repo_roots", help="仓库扫描根, 逗号分隔 (默认 E:/WindowsWorkspace,D:/P4/main/AIWorkSpace)"),
                CliArg(name="min_sessions", help="一个 cwd 至少出现几次会话才算项目 (默认 1)"),
            ],
        ))
    except Exception as e:
        logger.debug("skip project-discovery: %s", e)

    # ── ux-audit — 前端三维 UX 审计(交互/信息/跳转)· 确定性枚举 + 据矩阵/层级打错位标记 ──
    try:
        register(PipelineEntry(
            name="ux-audit",
            description=(
                "前端 src 三维 UX 审计 — 交互/信息/跳转 确定性枚举 + 据频率×重要性矩阵/信息层级打错位标记(平铺/删除无保护/无层级/说明冗余).\n"
                "  omni run ux-audit -i src_root=E:/WindowsWorkspace/omnicompany/src/omnicompany/dashboard/frontend/src -i app=omnidashboard\n"
                "  产出: 每界面交互/信息/跳转计数 + 错位界面清单 + markdown 总表(落 data/services/ux_audit/). 可复跑于 lofa/poof/whatnow.\n"
                "  口径: frostpane/REBUILD-STANDARD.md + INTERACTION-AUDIT.md. 语义两轴(重要性/频率/含义)留 LLM 增补节点(SOFT 待加)."
            ),
            domain="ux_audit",
            build_team=_lazy("omnicompany.packages.services._diagnosis.ux_audit.team",
                                 "build_team"),
            build_bindings=_lazy_fn("omnicompany.packages.services._diagnosis.ux_audit.run",
                                    "build_bindings"),
            default_db_dir="data/services/ux_audit",
            default_max_steps=8,
            cli_args=[
                CliArg(name="src_root", help="前端 src 目录绝对路径", required=True),
                CliArg(name="app", help="应用名 (omnidashboard/lofa/…), 仅报告标题用"),
            ],
        ))
    except Exception as e:
        logger.debug("skip ux-audit: %s", e)

    # ── 完整性临界 — 每个 owned 项目都有真源报告+到-bar 页才算完, 否则列出缺失打回 ──
    try:
        register(PipelineEntry(
            name="audit-completeness",
            description=(
                "完整性临界 — 核对每个 owned 项目是否都有真源报告+到九维-bar 的作品页; 缺一 FAIL 并列 missing.\n"
                "  (一般由编排工作流调用, 传入 owned_projects/reports/pages)"
            ),
            domain="project_audit",
            build_team=_lazy("omnicompany.packages.services._diagnosis.project_audit.team",
                                 "build_completeness_team"),
            build_bindings=_lazy_fn("omnicompany.packages.services._diagnosis.project_audit.run",
                                    "build_completeness_bindings"),
            default_db_dir="data/services/project_audit",
            default_max_steps=5,
            cli_args=[
                CliArg(name="owned_projects", help="应覆盖的项目名, 逗号分隔"),
            ],
        ))
    except Exception as e:
        logger.debug("skip audit-completeness: %s", e)

    # lap-audit pipeline 移除 (2026-05-05 诊断重制 step 3) — lap_auditor 整体归档,
    # 概念并入 doctor _spec/ 子域. 详 docs/plans/diagnosis/[2026-05-05]DIAGNOSIS-RECONSOLIDATION/plan.md

    # cleanup pipeline 移除 (2026-05-05 诊断重制 step 4) — cleanup_bot 整体归档,
    # 不属诊断 (是清理工具的取证). 详 docs/plans/diagnosis/[2026-05-05]DIAGNOSIS-RECONSOLIDATION/plan.md
    # ── sw-* 软件工程管线（full-spec, lazy-load）──
    try:
        register(PipelineEntry(
            # 2026-07-03 批4 ㋓: 收敛到"域.动作"点号命名(sw = software_engineering),
            # 旧 kebab 名走 aliases 过渡, 调用方无需改动。
            name="sw.verify",
            description="软件验证 — 验证声称是否有命令输出证据支持",
            domain="sw_verify",
            build_team=_lazy("omnicompany.packages.domains.software_engineering.verify.team", "build_team"),
            build_bindings=_lazy("omnicompany.packages.domains.software_engineering.verify.run", "build_bindings"),
            default_db_dir="data/domains/software_engineering",
            default_max_steps=20,
            aliases=("sw-verify",),
        ))
    except Exception as e:
        logger.debug("skip sw.verify: %s", e)

    try:
        register(PipelineEntry(
            name="sw.review",
            description="代码审查 — diff 收集 + 上下文探索 + LLM 深度审查",
            domain="sw_review",
            build_team=_lazy("omnicompany.packages.domains.software_engineering.review.team", "build_team"),
            build_bindings=_lazy("omnicompany.packages.domains.software_engineering.review.run", "build_bindings"),
            default_db_dir="data/domains/software_engineering",
            default_max_steps=25,
            aliases=("sw-review",),
        ))
    except Exception as e:
        logger.debug("skip sw.review: %s", e)

    try:
        register(PipelineEntry(
            name="sw.plan",
            description="实施计划 — 代码库探索 + TDD 计划生成 + 自检循环",
            domain="sw_plan",
            build_team=_lazy("omnicompany.packages.domains.software_engineering.plan.team", "build_team"),
            build_bindings=_lazy("omnicompany.packages.domains.software_engineering.plan.run", "build_bindings"),
            default_db_dir="data/domains/software_engineering",
            default_max_steps=30,
            aliases=("sw-plan",),
        ))
    except Exception as e:
        logger.debug("skip sw.plan: %s", e)

    try:
        register(PipelineEntry(
            name="sw.design",
            description="设计审查 — 架构扫描 + 模式分析 + LLM 审查",
            domain="sw_design",
            build_team=_lazy("omnicompany.packages.domains.software_engineering.design.team", "build_team"),
            build_bindings=_lazy("omnicompany.packages.domains.software_engineering.design.run", "build_bindings"),
            default_db_dir="data/domains/software_engineering",
            default_max_steps=25,
            aliases=("sw-design",),
        ))
    except Exception as e:
        logger.debug("skip sw.design: %s", e)

    try:
        register(PipelineEntry(
            name="sw.tdd",
            description="TDD 执行 — 写测试 + 跑测试 + 写实现 + 修复回路",
            domain="sw_tdd",
            build_team=_lazy("omnicompany.packages.domains.software_engineering.tdd.team", "build_team"),
            build_bindings=_lazy("omnicompany.packages.domains.software_engineering.tdd.run", "build_bindings"),
            default_db_dir="data/domains/software_engineering",
            default_max_steps=30,
            aliases=("sw-tdd",),
        ))
    except Exception as e:
        logger.debug("skip sw.tdd: %s", e)

    try:
        register(PipelineEntry(
            name="sw.implement",
            description="独立实施 — 需求解析 + 代码库扫描 + LLM 实施",
            domain="sw_implement",
            build_team=_lazy("omnicompany.packages.domains.software_engineering.implement.team", "build_team"),
            build_bindings=_lazy("omnicompany.packages.domains.software_engineering.implement.run", "build_bindings"),
            default_db_dir="data/domains/software_engineering",
            default_max_steps=25,
            aliases=("sw-implement",),
        ))
    except Exception as e:
        logger.debug("skip sw.implement: %s", e)

    # ── Skill 导入器 (2026-04-09 重构) ──
    # 主管线: 解析 skill → 产 workflow-factory 可消费的需求稿
    # (不再自己生成 Python 代码, 那是 workflow-factory 的权威职责)
    try:
        register(PipelineEntry(
            name="skill-import",
            description=(
                "Skill 导入器 — 解析外部 Claude Code Skill, 产出 workflow-factory "
                "可消费的 markdown 需求稿 (parse → analyze → infer → draft_requirement)"
            ),
            domain="workflow",
            build_team=_lazy(
                "omnicompany.packages.services._utility.skill_importer.run",
                "build_skill_importer_pipeline",
            ),
            build_bindings=_lazy_fn(
                "omnicompany.packages.services._utility.skill_importer.run",
                "build_skill_importer_bindings",
            ),
            default_db_dir="data/services/workflow",
            default_max_steps=20,
            cli_args=[
                CliArg(name="skill_dir", help="Skill 目录路径", required=True),
            ],
        ))
    except Exception as e:
        logger.debug("skip skill-import: %s", e)

    # ── Skill Importer Verify — 忠实度检验 (workflow-factory 产物后运行) ──
    try:
        register(PipelineEntry(
            name="skill-import-verify",
            description=(
                "忠实度检验 — 检查 workflow-factory 生成的 package 是否忠实实现了 "
                "原 Claude Code Skill 的所有节点 / 约束 / 覆盖预期, 产出 compliance 报告"
            ),
            domain="workflow",
            build_team=_lazy(
                "omnicompany.packages.services._utility.skill_importer.run",
                "build_verify_pipeline",
            ),
            build_bindings=_lazy_fn(
                "omnicompany.packages.services._utility.skill_importer.run",
                "build_verify_bindings",
            ),
            default_db_dir="data/services/workflow",
            default_max_steps=5,
        ))
    except Exception as e:
        logger.debug("skip skill-import-verify: %s", e)

    # ── Repo Architect — 仓库架构深度分析 (absorption 核心工具) ──
    try:
        register(PipelineEntry(
            name="repo-architect",
            description=(
                "仓库架构深度分析管线 — 输入 GitHub URL 或本地路径, 输出完整架构报告 + "
                "覆盖率证明 + OmniKB 条目。翻译自 yzddmr6/repo-analyzer SOTA skill, "
                "20 节点 DAG 覆盖 16 Format 完整链路 (人工补齐 workflow-factory 首轮截断)"
            ),
            domain="workflow",
            build_team=_lazy(
                "omnicompany.packages.services._learning.repo.architect.run",
                "build_repo_architect_pipeline",
            ),
            build_bindings=_lazy_fn(
                "omnicompany.packages.services._learning.repo.architect.run",
                "build_repo_architect_bindings",
            ),
            default_db_dir="data/absorption",
            default_max_steps=40,
            cli_args=[
                CliArg(name="url", help="GitHub 仓库 URL"),
                CliArg(name="local_path", help="本地仓库路径 (和 url 互斥)"),
                CliArg(name="focus", help="分析焦点 (自然语言, <=2000 字符)"),
                CliArg(name="mode", help="分析模式 quick/standard/deep"),
            ],
        ))
    except Exception as e:
        logger.debug("skip repo-architect: %s", e)

    # ── Repo Learner — 带目的的 repo 学习支流 (AgentNodeLoop 主 agent + sub agent) ──
    try:
        register(PipelineEntry(
            name="repo-learner",
            description=(
                "带目的的 repo 学习支流 — 主 agent (150 turns) 自由读仓库, 最多 spawn "
                "3 个子 agent (50 turns each) 深读模块, 产出自由格式 learning report "
                "(Learning Value + Learning Locations 两段必含)。与 repo-architect 并列, "
                "共享前 4 个基础节点 (input_validator / repo_acquirer / repo_identity_anchor "
                "/ scale_surveyor)。"
            ),
            domain="workflow",
            build_team=_lazy(
                "omnicompany.packages.services._learning.repo.learner.run",
                "build_repo_learner_pipeline",
            ),
            build_bindings=_lazy_fn(
                "omnicompany.packages.services._learning.repo.learner.run",
                "build_repo_learner_bindings",
            ),
            default_db_dir="data/absorption",
            default_max_steps=10,  # pipeline 外层只有 6 节点; AgentNodeLoop 内部 turns 才是大头
            cli_args=[
                CliArg(name="url", help="GitHub 仓库 URL"),
                CliArg(name="local_path", help="本地仓库路径 (和 url 互斥)"),
                CliArg(name="focus", help="学习焦点 hint (自然语言, 可空)"),
            ],
        ))
    except Exception as e:
        logger.debug("skip repo-learner: %s", e)

    # ── OmniKB 知识库审计 ──
    # 设计文档: docs/plans/[2026-04-09]KNOWLEDGE-REVIVAL-AND-ABSORPTION-REDESIGN/
    try:
        register(PipelineEntry(
            name="omnikb-audit",
            description=(
                "OmniKB 全量审计 — 校验知识库引用完整性、code_anchor 漂移、"
                "孤儿 Router、Format 覆盖"
            ),
            domain="knowledge",
            build_team=_lazy(
                "omnicompany.packages.services._learning.knowledge.run",
                "build_audit_pipeline",
            ),
            build_bindings=_lazy_fn(
                "omnicompany.packages.services._learning.knowledge.run",
                "build_audit_bindings",
            ),
            default_db_dir="data/services/knowledge",
            default_max_steps=5,
        ))
    except Exception as e:
        logger.debug("skip omnikb-audit: %s", e)

    # ── Repo Absorption (Stage 1: Survey & Triage) ──
    # 设计文档: docs/plans/[2026-04-08]REPO-ABSORPTION-WORKFLOW/
    # 当前为骨架冒烟阶段，4 个 Router 都是 stub。后续 Stage 增量扩展。
    try:
        register(PipelineEntry(
            name="absorption-survey",
            description=(
                "Repo Absorption · Stage 1 Survey & Triage — "
                "从 GitHub 仓库列表识别值得吸纳的地标，不下载源码"
            ),
            domain="absorption",
            build_team=_lazy(
                "omnicompany.packages.services._learning.absorption.run",
                "build_survey_pipeline",
            ),
            build_bindings=_lazy_fn(
                "omnicompany.packages.services._learning.absorption.run",
                "build_survey_bindings",
            ),
            default_db_dir="data/absorption",
            default_max_steps=15,
            cli_args=[
                CliArg(
                    name="repos",
                    help="目标仓库列表 (JSON 数组或逗号分隔)，如 'openai/codex,google-gemini/gemini-cli'",
                    required=True,
                ),
                CliArg(
                    name="profile",
                    help="吸纳 Profile: framework_absorption | domain_absorption",
                    default="framework_absorption",
                ),
            ],
        ))
    except Exception as e:
        logger.debug("skip absorption-survey: %s", e)

    # ── Repo Absorption V3 (模块驱动四层地图, Phase A: RepoMapper 实化) ──
    # 设计文档: docs/plans/[2026-04-13]REPO-ABSORPTION-V3/DESIGN.md
    try:
        register(PipelineEntry(
            name="absorption-module-driven",
            description=(
                "Repo Absorption V3 · 模块驱动四层地图管线 — "
                "RepoMapper 全量扫描双层地图，ModulePicker LLM 语义选模块，"
                "ModuleReader 展开代码，LearningExtractor 提炼发现"
            ),
            domain="absorption",
            build_team=_lazy(
                "omnicompany.packages.services._learning.absorption.run",
                "build_v3_pipeline",
            ),
            build_bindings=_lazy_fn(
                "omnicompany.packages.services._learning.absorption.run",
                "build_v3_bindings",
            ),
            default_db_dir="data/absorption",
            default_max_steps=10,
            cli_args=[
                CliArg(
                    name="repo_name",
                    help="目标 repo 名称，如 'hermes-agent'",
                    required=True,
                ),
                CliArg(
                    name="repo_local_path",
                    help="本地克隆路径（已 git clone 的目录）",
                    required=True,
                ),
            ],
        ))
    except Exception as e:
        logger.debug("skip absorption-module-driven: %s", e)

    # ── Repo Absorption V3 Stage 3 (工作流修改管线, Phase 1 骨架) ──
    # 设计文档: docs/plans/[2026-04-14]STAGE3-WORKFLOW-MODIFIER/plan.md
    try:
        register(PipelineEntry(
            name="absorption-workflow-modifier",
            description=(
                "Repo Absorption V3 Stage 3 · 工作流修改管线 — "
                "SpecParser 解析改进提案，HumanApprovalGate 人工审批，"
                "WorkflowGenerator 生成变更（Phase 2），DangerGate + Validator 检查（Phase 3）"
            ),
            domain="absorption",
            build_team=_lazy(
                "omnicompany.packages.services._learning.absorption.run",
                "build_v3_stage3_pipeline",
            ),
            build_bindings=_lazy_fn(
                "omnicompany.packages.services._learning.absorption.run",
                "build_v3_stage3_bindings",
            ),
            default_db_dir="data/domains/absorption",
            default_max_steps=5,
            cli_args=[
                CliArg(
                    name="repo_name",
                    help="目标 repo 名称，如 'hermes-agent'",
                    required=True,
                ),
            ],
        ))
    except Exception as e:
        logger.debug("skip absorption-workflow-modifier: %s", e)

    # ── Repo Absorption V2 (问题驱动定向深读, Phase 1 骨架) ──
    # 设计文档: docs/plans/[2026-04-13]REPO-ABSORPTION-V2/plan.md
    try:
        register(PipelineEntry(
            name="absorption-baseline",
            description=(
                "Repo Absorption V2 · 问题驱动定向深读管线 — "
                "以自画像缺口(G1-G7)为问题来源，带着问题进行定向深读，终止条件是'问题被回答'"
            ),
            domain="absorption",
            build_team=_lazy(
                "omnicompany.packages.services._learning.absorption.run",
                "build_v2_pipeline",
            ),
            build_bindings=_lazy_fn(
                "omnicompany.packages.services._learning.absorption.run",
                "build_v2_bindings",
            ),
            default_db_dir="data/absorption",
            default_max_steps=30,
            cli_args=[
                CliArg(
                    name="repo_name",
                    help="目标 repo 名称，如 'gemini-cli'",
                    required=True,
                ),
                CliArg(
                    name="repo_local_path",
                    help="本地克隆路径（已 git clone 的目录）",
                    required=True,
                ),
            ],
        ))
    except Exception as e:
        logger.debug("skip absorption-baseline: %s", e)

    # ── Unity 探索 ──
    try:
        register(PipelineEntry(
            name="unity-explore",
            description="Unity 游戏环境探索管线 — 自动化 UI 交互与观察",
            domain="unity",
            build_team=_lazy("omnicompany.packages.domains.demogame.unity_explore.pipeline",
                                "build_unity_explore_pipeline"),
            build_bindings=_lazy_fn("omnicompany.packages.domains.demogame.unity_explore.run_pipeline",
                                "build_explore_bindings"),
            default_db_dir="data/domains/unity_qa",
            default_max_steps=50,
        ))
    except Exception as e:
        logger.debug("skip unity-explore: %s", e)

    # ── Unity QA (新版：discover / playtest / design / execute / fix) ──
    try:
        register(PipelineEntry(
            name="unity-discover",
            description="AI 驱动的 Unity 游戏广度探索 — 自动发现界面、建图、记录 bug",
            domain="unity-qa",
            build_team=_lazy(
                "omnicompany.packages.domains.demogame.unity_qa.discover.pipeline",
                "build_pipeline",
            ),
            build_bindings=_lazy_fn(
                "omnicompany.packages.domains.demogame.unity_qa.discover.run",
                "build_bindings",
            ),
            default_db_dir="data/domains/unity_qa",
            default_max_steps=100,
            cli_args=[
                CliArg(name="max_steps", help="最大探索步数", default="50"),
                CliArg(name="bridge_port", help="AgentBridge 端口", default="18820"),
            ],
        ))
    except Exception as e:
        logger.debug("skip unity-discover: %s", e)

    try:
        register(PipelineEntry(
            name="unity-playtest",
            description="AI 驱动的 Unity 游戏目标导向游玩 — 在指定界面完成具体任务",
            domain="unity-qa",
            build_team=_lazy(
                "omnicompany.packages.domains.demogame.unity_qa.playtest.pipeline",
                "build_pipeline",
            ),
            build_bindings=_lazy_fn(
                "omnicompany.packages.domains.demogame.unity_qa.playtest.run",
                "build_bindings",
            ),
            default_db_dir="data/domains/unity_qa",
            default_max_steps=50,
            cli_args=[
                CliArg(name="target_state", help="目标界面状态名", required=True),
                CliArg(name="task", help="任务描述", required=True),
                CliArg(name="bridge_port", help="AgentBridge 端口", default="18820"),
            ],
        ))
    except Exception as e:
        logger.debug("skip unity-playtest: %s", e)

    try:
        register(PipelineEntry(
            name="unity-execute",
            description="Unity 测试执行器 — 执行 TestSuite 并产出 TestReport",
            domain="unity-qa",
            build_team=_lazy(
                "omnicompany.packages.domains.demogame.unity_qa.execute.pipeline",
                "build_pipeline",
            ),
            build_bindings=_lazy_fn(
                "omnicompany.packages.domains.demogame.unity_qa.execute.run",
                "build_bindings",
            ),
            default_db_dir="data/domains/unity_qa",
            default_max_steps=200,
            cli_args=[
                CliArg(name="suite", help="TestSuite YAML 路径或内联定义", required=True),
                CliArg(name="bridge_port", help="AgentBridge 端口", default="18820"),
            ],
        ))
    except Exception as e:
        logger.debug("skip unity-execute: %s", e)

    try:
        register(PipelineEntry(
            name="unity-fix",
            description="AI 驱动的 Roadmap 修复 — 诊断失败路径、修复 detect 规则、回归验证",
            domain="unity-qa",
            build_team=_lazy(
                "omnicompany.packages.domains.demogame.unity_qa.fix.pipeline",
                "build_pipeline",
            ),
            build_bindings=_lazy_fn(
                "omnicompany.packages.domains.demogame.unity_qa.fix.run",
                "build_bindings",
            ),
            default_db_dir="data/domains/unity_qa",
            default_max_steps=30,
            cli_args=[
                CliArg(name="issue", help="问题描述", required=True),
                CliArg(name="target_state", help="问题相关状态"),
                CliArg(name="bridge_port", help="AgentBridge 端口", default="18820"),
            ],
        ))
    except Exception as e:
        logger.debug("skip unity-fix: %s", e)

    try:
        register(PipelineEntry(
            name="unity-design",
            description="AI 驱动的测试用例生成 — 视觉探索 UI、自动生成 TestSuite",
            domain="unity-qa",
            build_team=_lazy(
                "omnicompany.packages.domains.demogame.unity_qa.design.pipeline",
                "build_pipeline",
            ),
            build_bindings=_lazy_fn(
                "omnicompany.packages.domains.demogame.unity_qa.design.run",
                "build_bindings",
            ),
            default_db_dir="data/domains/unity_qa",
            default_max_steps=20,
            cli_args=[
                CliArg(name="target_module", help="目标游戏模块（如 Tavern）", required=True),
                CliArg(name="test_type", help="测试类型 smoke/functional/boundary", default="smoke"),
                CliArg(name="test_focus", help="测试重点描述"),
                CliArg(name="bridge_port", help="AgentBridge 端口", default="18820"),
            ],
        ))
    except Exception as e:
        logger.debug("skip unity-design: %s", e)

    # ── 跨语言改写 ──
    try:
        register(PipelineEntry(
            name="lang-rewrite",
            description="跨语言改写管线 — 将 Python 引擎层模块改写为 TypeScript / Rust",
            domain="rewrite",
            build_team=_lazy("omnicompany.packages.domains.software_engineering.lang_rewrite.team",
                                "build_team"),
            build_bindings=_lazy_fn("omnicompany.packages.domains.software_engineering.lang_rewrite.run",
                                   "build_bindings"),
            default_db_dir="data/domains/rewrite",
            default_max_steps=30,
            cli_args=[
                CliArg(name="source_path", help="Python 源文件路径", required=True),
                CliArg(name="target_lang", help="目标语言: typescript / rust",
                       default="typescript"),
                CliArg(name="work_dir", help="目标语言项目工作目录"),
            ],
        ))
    except Exception as e:
        logger.debug("skip lang-rewrite: %s", e)

    # ── 等价性测试 ──
    try:
        register(PipelineEntry(
            name="equiv-test",
            description="[EXPERIMENTAL] 跨语言语义等价性测试管线 — Golden File 模式验证 Python↔TS 行为一致性",
            domain="equiv",
            build_team=_lazy("omnicompany.packages.domains.software_engineering.equiv_test.team",
                                "build_team"),
            build_bindings=_lazy_fn("omnicompany.packages.domains.software_engineering.equiv_test.run",
                                   "build_bindings"),
            default_db_dir="data/domains/software_engineering/equiv",
            default_max_steps=20,
            cli_args=[
                CliArg(name="py_path", help="Python 源文件路径", required=True),
                CliArg(name="ts_path", help="TypeScript 翻译文件路径", required=True),
                CliArg(name="module_name", help="模块名", default=""),
                CliArg(name="ts_dir", help="TS 工作目录",
                       default="data/rewrite/ts_phase1"),
            ],
        ))
    except Exception as e:
        logger.debug("skip equiv-test: %s", e)

    # ── 通用调试器 ──
    try:
        register(PipelineEntry(
            name="debug",
            description="假设驱动调试工作流 — 通用跨语言 debug 管线",
            domain="debug",
            build_team=_lazy("omnicompany.packages.domains.software_engineering.debugger.team",
                                "build_team"),
            build_bindings=_lazy_fn("omnicompany.packages.domains.software_engineering.debugger.run",
                                   "build_bindings"),
            default_db_dir="data/_runtime/debug",
            default_max_steps=50,
            cli_args=[
                CliArg(name="error_output", help="编译/测试错误输出", required=True),
                CliArg(name="language", help="目标语言", default="typescript"),
                CliArg(name="compile_command", help="编译/测试命令"),
                CliArg(name="work_dir", help="工作目录"),
            ],
        ))
    except Exception as e:
        logger.debug("skip debug: %s", e)

    # ── 守护检查 ──
    try:
        register(PipelineEntry(
            name="guardian",
            description="守护检查管线 — 文件系统污染扫描 + 架构规范审计 + 健康报告",
            domain="guardian",
            build_team=_lazy("omnicompany.packages.services._core.guardian.team",
                                "build_team"),
            build_bindings=_lazy_fn("omnicompany.packages.services._core.guardian.run",
                                   "build_bindings"),
            default_db_dir="data/services/guardian",
            default_max_steps=10,
            cli_args=[
                CliArg(name="project_root", help="项目根目录路径",
                       default=str(omni_workspace_root())),
            ],
        ))
    except Exception as e:
        logger.debug("skip guardian: %s", e)

    # guardian-patrol pipeline 移除 (2026-05-05 诊断重制 step 8) — patrol_worker LLM 巡查归档,
    # 概念并入 doctor 诊断假设体系(data/services/doctor/hypotheses/ 的 H-*.yaml 断言,
    # 与决策库 belief/探索学习 hypothesis 管线是同名异物, 见合并清单#3). guardian 留纯规则部分.

    # pipeline-ci pipeline 移除 (2026-05-05 诊断重制 step 5) — pipeline_ci 整体归档,
    # 三 Auditor 概念并入 doctor _spec/ 跟 doctor 诊断假设体系(同上, 非决策库 belief).
    # 详 docs/plans/diagnosis/[2026-05-05]DIAGNOSIS-RECONSOLIDATION/plan.md

    # ── hypothesis 假设探索管线 ──
    try:
        register(PipelineEntry(
            name="hypothesis",
            description=(
                "假设探索 — agent 自由探索目标系统，把可证伪猜想沉进统一决策库(kind=belief,"
                "tags=[hypothesis-explore, domain:<x>])。主题摘要=生成投影(omni decisions knowledge)。"
                "真实多轮循环由 hypothesis.team.run_session 驱动。"
            ),
            domain="hypothesis",
            build_team=_lazy("omnicompany.packages.services._learning.hypothesis.team",
                                "build_team"),
            build_bindings=_lazy_fn("omnicompany.packages.services._learning.hypothesis.run",
                                    "build_bindings"),
            default_db_dir="data/services/hypothesis",
            default_max_steps=10,
            cli_args=[
                CliArg(name="domain", help="主题域名（如 lark-cli）", required=True),
                CliArg(name="goal", help="探索目标", required=True),
                CliArg(name="max_iterations", help="最大迭代次数（默认 2）", default="2"),
            ],
            when={"semantic": "要摸清一个陌生系统/工具的行为、把探索所得沉成可证伪猜想时",
                  "match_keys": ["hypothesis", "explore"], "judge": "llm"},
            scale={"tier": "long", "minutes": "10-30", "cost": "Experimenter+Reflector 双 agent 多轮"},
            confirm=True,
            book_refs=("docs/ontology/20-探索通则.md#反证优先",
                       "docs/ontology/20-探索通则.md#回传必做(收工钩子)"),
        ))
    except Exception as e:
        logger.debug("skip hypothesis: %s", e)

    # ── gddecon 游戏设计拆解管线 (事件型) ──
    try:
        register(PipelineEntry(
            name="gddecon-aspect-tree",
            description=(
                "游戏设计拆解 — 读设计源 + 当前 build，用方面发现法（透镜×展开规则×完备性）"
                "产出方面树（设计应被拆成哪些维度）。产出 data/knowledge/aspect_trees/<game>.md。"
            ),
            domain="gddecon",
            engine="event",
            build_team=_lazy("omnicompany.packages.services._learning.gddecon.run",
                             "build_team_workers"),
            build_bindings=lambda *a, **k: {},
            entry_material="gddecon.deconstruction-request",
            default_db_dir="data/services/gddecon",
            default_max_steps=4,
            cli_args=[
                CliArg(name="game_name", help="游戏名", required=True),
                CliArg(name="build_root", help="当前 build 根目录", default=""),
                CliArg(name="design_sources", help="设计文档/目录路径（逗号分隔）", default=""),
                CliArg(name="focus", help="可选：只下钻某子领域", default=""),
                CliArg(name="project_root", help="只读寻址根", default="E:/WindowsWorkspace"),
            ],
        ))
    except Exception as e:
        logger.debug("skip gddecon-aspect-tree: %s", e)

    # ── gddecon 差距分析管线 (事件型) ──
    try:
        register(PipelineEntry(
            name="gddecon-gap-report",
            description=(
                "游戏设计差距盘点 — 对方面树每个方面做应然↔实然↔差距分析（无现成树先跑拆解）。"
                "产出 data/knowledge/aspect_trees/<game>-差距.md。"
            ),
            domain="gddecon",
            engine="event",
            build_team=_lazy("omnicompany.packages.services._learning.gddecon.run",
                             "build_gap_workers"),
            build_bindings=lambda *a, **k: {},
            entry_material="gddecon.deconstruction-request",
            default_db_dir="data/services/gddecon",
            default_max_steps=4,
            cli_args=[
                CliArg(name="game_name", help="游戏名", required=True),
                CliArg(name="build_root", help="当前 build 根目录", default=""),
                CliArg(name="design_sources", help="设计文档/目录路径（逗号分隔）", default=""),
                CliArg(name="project_root", help="只读寻址根", default="E:/WindowsWorkspace"),
            ],
        ))
    except Exception as e:
        logger.debug("skip gddecon-gap-report: %s", e)

    # ── gddecon UI 设计 · 跟进UI标准 (事件型) ──
    try:
        register(PipelineEntry(
            name="gddecon-ui-standard",
            description=(
                "跟进UI标准 — 从 UI 规格 + 方面树 UI 簇制定可检查的 UI 标准库（信息/交互两类，每条带证据与检查法）。"
                "产出 data/knowledge/aspect_trees/<game>-UI标准.md。"
            ),
            domain="gddecon",
            engine="event",
            build_team=_lazy("omnicompany.packages.services._learning.gddecon.run",
                             "build_ui_standard_workers"),
            build_bindings=lambda *a, **k: {},
            entry_material="gddecon.deconstruction-request",
            default_db_dir="data/services/gddecon",
            default_max_steps=4,
            cli_args=[
                CliArg(name="game_name", help="游戏名", required=True),
                CliArg(name="design_sources", help="UI 设计规格路径（逗号分隔）", default=""),
                CliArg(name="build_root", help="当前 build 根目录", default=""),
                CliArg(name="project_root", help="只读寻址根", default="E:/WindowsWorkspace"),
            ],
        ))
    except Exception as e:
        logger.debug("skip gddecon-ui-standard: %s", e)

    # ── gddecon UI 设计 · 建立UI设计稿(按真后端) (事件型) ──
    try:
        register(PipelineEntry(
            name="gddecon-ui-build",
            description=(
                "建立UI设计稿 — 读真实后端代码(game-state/command/segment…)产出 complete-expression "
                "界面设计稿(把后端所有状态+操作完整暴露,先不美化)。产出 data/knowledge/ui_mockups/<game>-<scope>-backend-design.html。"
            ),
            domain="gddecon",
            engine="event",
            build_team=_lazy("omnicompany.packages.services._learning.gddecon.run",
                             "build_ui_build_workers"),
            build_bindings=lambda *a, **k: {},
            entry_material="gddecon.deconstruction-request",
            default_db_dir="data/services/gddecon",
            default_max_steps=4,
            cli_args=[
                CliArg(name="game_name", help="游戏名", required=True),
                CliArg(name="build_root", help="游戏 build 根(读真后端代码)", required=True),
                CliArg(name="scope", help="范围(默认 战斗屏)", default="战斗屏"),
            ],
        ))
    except Exception as e:
        logger.debug("skip gddecon-ui-build: %s", e)

    # ── gddecon UI 设计 · 制定信息层级(界面信息维度) (事件型) ──
    try:
        register(PipelineEntry(
            name="gddecon-info-hierarchy",
            description=(
                "制定信息层级 — 把一屏完整表达清单按玩家注意力/行为频次排成层级表(常驻/揭示)，"
                "并把'展开信息'当操作记录(揭示即操作)。产出 data/knowledge/ui_mockups/<game>-<scope>-信息层级.md。"
            ),
            domain="gddecon",
            engine="event",
            build_team=_lazy("omnicompany.packages.services._learning.gddecon.run",
                             "build_info_hierarchy_workers"),
            build_bindings=lambda *a, **k: {},
            entry_material="gddecon.deconstruction-request",
            default_db_dir="data/services/gddecon",
            default_max_steps=4,
            cli_args=[
                CliArg(name="game_name", help="游戏名", required=True),
                CliArg(name="scope", help="范围(默认 战斗屏)", default="战斗屏"),
                CliArg(name="inventory", help="完整表达清单文本", default=""),
                CliArg(name="concept", help="游戏核心循环", default=""),
            ],
        ))
    except Exception as e:
        logger.debug("skip gddecon-info-hierarchy: %s", e)

    # ── gddecon UI 设计 · 操作交互模型(界面操作维度) (事件型) ──
    try:
        register(PipelineEntry(
            name="gddecon-interaction-model",
            description=(
                "操作交互模型 — 把一屏操作全集(指令+揭示)逐操作排成交互规范(频次×手势/反馈/确认安全/可用相位/选择模型)，"
                "界面操作维度、信息层级的对偶。产出 data/knowledge/ui_mockups/<game>-<scope>-操作交互模型.md。"
            ),
            domain="gddecon",
            engine="event",
            build_team=_lazy("omnicompany.packages.services._learning.gddecon.run",
                             "build_interaction_model_workers"),
            build_bindings=lambda *a, **k: {},
            entry_material="gddecon.deconstruction-request",
            default_db_dir="data/services/gddecon",
            default_max_steps=4,
            cli_args=[
                CliArg(name="game_name", help="游戏名", required=True),
                CliArg(name="scope", help="范围(默认 战斗屏)", default="战斗屏"),
                CliArg(name="ops", help="操作全集文本", default=""),
                CliArg(name="concept", help="游戏核心循环", default=""),
            ],
        ))
    except Exception as e:
        logger.debug("skip gddecon-interaction-model: %s", e)

    # ── Selftest e2e 功能自测 ──
    try:
        register(PipelineEntry(
            name="selftest",
            description="OmniCompany e2e 功能自测 — 验证管线注册、bindings、EventBus 和 CLI 基础功能",
            domain="selftest",
            build_team=_lazy("omnicompany.packages.services._core.selftest.team",
                                "build_team"),
            build_bindings=_lazy_fn("omnicompany.packages.services._core.selftest.run",
                                   "build_bindings"),
            default_db_dir="data/services/selftest",
            default_max_steps=10,
        ))
    except Exception as e:
        logger.debug("skip selftest: %s", e)

    # ── team-builder · agent-first 新拓扑 (A3 2026-04-23) ──
    try:
        register(PipelineEntry(
            name="team-builder",
            description=(
                "造 Team 的 Team · agent-first 设计 · 4 节点 "
                "(OriginRequestLoader → {IntentAnalyzer, ReferenceScout} → TeamArchitect) "
                "→ 输出七节 team_design 骨架"
            ),
            domain="team_builder",
            build_team=_lazy(
                "omnicompany.packages.services._core.team_builder.team",
                "build_team_agent_first",
            ),
            build_bindings=_lazy_fn(
                "omnicompany.packages.services._core.team_builder.run",
                "build_bindings_agent_first",
            ),
            default_db_dir="data/services/team_builder",
            default_max_steps=1000,  # 铁律 B: 预算宽松
            cli_args=[
                CliArg(name="text", help="自然语言 Team 需求描述"),
            ],
        ))
    except Exception as e:
        logger.debug("skip team-builder: %s", e)

    # ── workflow-factory (legacy · 保留旧拓扑作 Diamond 参考 · 2026-04-23) ──
    try:
        register(PipelineEntry(
            name="workflow-factory",
            description=(
                "legacy 旧 workflow_factory 拓扑 (Diamond shortcut 归档作参考) · "
                "新代码用 team-builder (agent-first)"
            ),
            domain="team_builder",
            build_team=_lazy(
                "omnicompany.packages.services._core.team_builder.team",
                "build_team",  # 旧 build_team
            ),
            build_bindings=_lazy_fn(
                "omnicompany.packages.services._core.team_builder.run",
                "build_bindings",  # 旧 build_bindings
            ),
            default_db_dir="data/services/team_builder_legacy",
            default_max_steps=1000,
            cli_args=[
                CliArg(name="text", help="(legacy) 自然语言工作流需求"),
            ],
        ))
    except Exception as e:
        logger.debug("skip workflow-factory (legacy): %s", e)

    # ── trace-induction 轨迹归纳管线 ──
    try:
        register(PipelineEntry(
            name="trace-induction",
            description="轨迹归纳 — 从历史 trace 提取 SOP → 生成需求 → WF 产出 pipeline → 注册",
            domain="workflow",
            build_team=_lazy(
                "omnicompany.packages.services._learning.trace_induction.team",
                "build_team",
            ),
            build_bindings=_lazy_fn(
                "omnicompany.packages.services._learning.trace_induction.run",
                "build_bindings",
            ),
            default_db_dir="data/services/trace_induction",
            default_max_steps=30,
            cli_args=[
                CliArg(name="purpose", help="归纳目的描述"),
                CliArg(name="trace_ids", help="逗号分隔的 trace ID 列表"),
            ],
        ))
    except Exception as e:
        logger.debug("skip trace-induction: %s", e)

    # ── pattern-discovery 后台模式发现管线 ──
    try:
        register(PipelineEntry(
            name="pattern-discovery",
            description="后台模式发现 — 从行为保全摘要中聚类发现重复模式 → 自动触发轨迹归纳",
            domain="workflow",
            build_team=_lazy(
                "omnicompany.packages.services._core.pattern_discovery.team",
                "build_team",
            ),
            build_bindings=_lazy_fn(
                "omnicompany.packages.services._core.pattern_discovery.run",
                "build_bindings",
            ),
            default_db_dir="data/services/pattern_discovery",
            default_max_steps=50,
            cli_args=[
                CliArg(name="db_path", help="compression_summaries 所在的数据库路径"),
            ],
        ))
    except Exception as e:
        logger.debug("skip pattern-discovery: %s", e)

    # ── voxelcraft 游戏制作所管线 ──
    _bw_pkg = "omnicompany.packages.domains.voxelcraft"

    try:
        register(PipelineEntry(
            name="voxelcraft.qa.balance",
            description="voxelcraft 战斗平衡闭环 — config → build → server → RCON test → evolve (原 voxelcraft.combat_test, 1-5 改名)",
            domain="voxelcraft",
            build_team=_lazy(f"{_bw_pkg}.qa.balance.team", "build_combat_test_pipeline"),
            build_bindings=_lazy_fn(f"{_bw_pkg}.run", "build_combat_test_bindings"),
            default_db_dir="data/domains/voxelcraft",
            default_max_steps=30,
            aliases=("voxelcraft.combat_test",),
        ))
        register(PipelineEntry(
            name="voxelcraft.assimilate.mods",
            description="voxelcraft mod 同化 — Modrinth 搜索/下载/分析/许可证校验 (原 voxelcraft.art, 1-5 改名)",
            domain="voxelcraft",
            build_team=_lazy(f"{_bw_pkg}.content.assimilation.mod_intake.team", "build_art_pipeline"),
            build_bindings=_lazy_fn(f"{_bw_pkg}.run", "build_art_bindings"),
            default_db_dir="data/domains/voxelcraft",
            default_max_steps=15,
            aliases=("voxelcraft.art",),
        ))
        register(PipelineEntry(
            name="voxelcraft.assimilate.troop_visuals",
            description="voxelcraft 兵种外观同化 — mod探索→贴图评估→映射→等距渲染→真机加载 (原 voxelcraft.visual_assets, 1-5 改名)",
            domain="voxelcraft",
            build_team=_lazy(f"{_bw_pkg}.content.assimilation.troop_visuals.team", "build_visual_asset_pipeline"),
            build_bindings=_lazy_fn(f"{_bw_pkg}.run", "build_visual_assets_bindings"),
            default_db_dir="data/domains/voxelcraft",
            default_max_steps=25,
            aliases=("voxelcraft.visual_assets",),
        ))
        register(PipelineEntry(
            name="voxelcraft.assimilate.structures",
            description="voxelcraft 建筑结构同化 — schematic 搜索→解析→评估→方块替换→校验→FillOp Java (原 voxelcraft.structures, 1-5 改名)",
            domain="voxelcraft",
            build_team=_lazy(f"{_bw_pkg}.content.assimilation.structures.team", "build_structure_pipeline"),
            build_bindings=_lazy_fn(f"{_bw_pkg}.run", "build_structures_bindings"),
            default_db_dir="data/domains/voxelcraft",
            default_max_steps=15,
            aliases=("voxelcraft.structures",),
        ))

        # ── 五条内容路径 (阶段一 1-5 补 B3 缺口): event 引擎 + worktree 隔离 run_context ──
        from omnicompany.core.registry import CliArg as _CliArg  # noqa: F811

        def _bw_run_context(input_dict):
            from omnicompany.packages.domains.voxelcraft.run import voxelcraft_worktree_context
            return voxelcraft_worktree_context(input_dict)

        _BW_PATH_CLI = [
            CliArg(name="text", help="内容愿景 (自然语言, 如: 我想要一颗银色的宝石)", required=True),
            CliArg(name="author", help="需求提出者", default="omni-cli"),
            CliArg(name="run_id", help="run 标识 (缺省时间戳; 亦是 worktree 目录名)"),
            CliArg(name="keep_worktree", help="保留隔离副本供调试", is_flag=True),
        ]
        for _path, _steps in (("block", 1000), ("item", 1000), ("entity", 1000),
                              ("mechanism", 1000), ("worldgen", 1000)):
            register(PipelineEntry(
                name=f"voxelcraft.content.{_path}",
                description=(
                    f"voxelcraft {_path} 内容路径 — vision→Designer→AssetPicker→Engineer→LoadChecker; "
                    "全程 eternal-war worktree 隔离副本 (run_context), 通过后归档 approved_samples"
                ),
                domain="voxelcraft",
                engine="event",
                build_team=_lazy_fn(f"{_bw_pkg}.run", f"build_{_path}_path_workers"),
                build_bindings=lambda *a, **k: {},
                entry_material=f"bw.{_path}.vision",
                run_context=_bw_run_context,
                default_db_dir="data/domains/voxelcraft",
                default_max_steps=_steps,
                cli_args=list(_BW_PATH_CLI),
            ))
    except Exception as e:
        logger.debug("skip voxelcraft pipelines: %s", e)

    # ── vilo 内容评测管线（2026-06-13 框架级内化）──
    _vilo_pkg = "omnicompany.packages.domains.vilo"
    try:
        register(PipelineEntry(
            name="vilo.eval.domestic",
            description="Vilo 国产模型对照评测 — 上下文→多模型(统一 LLMClient)→报告",
            domain="vilo",
            build_team=_lazy(f"{_vilo_pkg}.team", "build_domestic_pipeline"),
            build_bindings=_lazy_fn(f"{_vilo_pkg}.run", "build_domestic_bindings"),
            default_db_dir="data/domains/vilo",
            default_max_steps=10,
            cli_args=[
                CliArg(name="models", help="逗号分隔参试模型(默认 deepseek/glm/kimi/qwen)", default=""),
                CliArg(name="max_tokens", help="单模型 max_tokens", type=int, default=3000),
                CliArg(name="max_context_chars", help="上下文字符上限", type=int, default=60000),
                CliArg(name="dry_run", help="只建上下文不调模型", is_flag=True),
            ],
            when={"semantic": "vilo 文本产出要与国产模型对照定位质量水位时",
                  "match_keys": ["vilo", "eval", "domestic"], "judge": "llm"},
            scale={"tier": "short", "minutes": "3-8", "cost": "4 个模型各 1 次调用"},
            book_refs=("docs/ontology/10-vilo叙事.md#线路逻辑质量判断",),
        ))
        register(PipelineEntry(
            name="vilo.eval.matrix",
            description="Vilo 文本矩阵评测 — 准备→执行(统一 LLMClient)→评分报告",
            domain="vilo",
            build_team=_lazy(f"{_vilo_pkg}.team", "build_matrix_pipeline"),
            build_bindings=_lazy_fn(f"{_vilo_pkg}.run", "build_matrix_bindings"),
            default_db_dir="data/domains/vilo",
            default_max_steps=10,
            cli_args=[
                CliArg(name="models", help="逗号分隔参试模型", default=""),
                CliArg(name="task_set", help="full/smoke", default="smoke"),
                CliArg(name="tasks", help="显式任务 id(逗号分隔,如 A1,C9)", default=""),
                CliArg(name="execute", help="真调模型(否则仅准备)", is_flag=True),
            ],
            when={"semantic": "vilo 批量文本任务矩阵评测(交稿前质量门/模型选型)时",
                  "match_keys": ["vilo", "eval", "matrix"], "judge": "llm"},
            scale={"tier": "long", "minutes": "15-60(full)/3-10(smoke)",
                   "cost": "任务数×模型数 次调用"},
            confirm=True,
            segments=({"name": "smoke", "only": [], "desc": "-i task_set=smoke 冒烟子集,不必确认全量"},),
            book_refs=("docs/ontology/10-vilo叙事.md#线路逻辑质量判断",
                       "docs/ontology/10-vilo叙事.md#立意雷区与衡量基准"),
        ))
        register(PipelineEntry(
            name="vilo.eval.source_first",
            description="Vilo source-first 评测 — 准备→执行(统一 LLMClient,硬超时)→报告",
            domain="vilo",
            build_team=_lazy(f"{_vilo_pkg}.team", "build_source_first_pipeline"),
            build_bindings=_lazy_fn(f"{_vilo_pkg}.run", "build_source_first_bindings"),
            default_db_dir="data/domains/vilo",
            default_max_steps=10,
            cli_args=[
                CliArg(name="models", help="逗号分隔参试模型", default=""),
                CliArg(name="task_set", help="full/smoke", default="smoke"),
                CliArg(name="tasks", help="显式任务 id(逗号分隔)", default=""),
                CliArg(name="execute", help="真调模型(否则仅准备)", is_flag=True),
            ],
        ))
        for _vname, _vbuild, _vbind, _vdesc in [
            ("vilo.eval.agentic", "build_agentic_pipeline", "build_agentic_bindings",
             "Vilo agentic worker 评测 — 准备工作区→工具循环(统一 LLMClient)→报告"),
            ("vilo.eval.concrete", "build_concrete_pipeline", "build_concrete_bindings",
             "Vilo 具体文本 v5 评测 — 准备→工具循环(自审/重写)→整合报告"),
            ("vilo.rank.anonymous", "build_anonymous_pipeline", "build_anonymous_bindings",
             "Vilo 匿名质量排名 — 候选+评委工作区→盲评工具循环→排名聚合"),
        ]:
            register(PipelineEntry(
                name=_vname,
                description=_vdesc,
                domain="vilo",
                build_team=_lazy(f"{_vilo_pkg}.team", _vbuild),
                build_bindings=_lazy_fn(f"{_vilo_pkg}.run", _vbind),
                default_db_dir="data/domains/vilo",
                default_max_steps=10,
                cli_args=[
                    CliArg(name="models", help="逗号分隔参试模型", default=""),
                    CliArg(name="execute", help="真跑 agent(否则仅准备)", is_flag=True),
                    CliArg(name="max_turns", help="agent 最大轮数", type=int, default=12),
                ],
                when={"semantic": "vilo 文本要 agent 式试写/盲评排名时(execute 才真跑)",
                      "match_keys": ["vilo", _vname.split(".")[-1]], "judge": "llm"},
                scale={"tier": "long", "minutes": "10-40", "cost": "agent 多轮工具循环×模型数"},
                confirm=True,
                book_refs=("docs/ontology/10-vilo叙事.md#线路逻辑质量判断",),
            ))
        for _vname, _vbuild, _vbind, _vdesc in [
            ("vilo.assets.card_index", "build_card_index_pipeline", "build_card_index_bindings",
             "Vilo 卡片资产索引 — 从 wiki/demo 重建卡片内容资产(确定性)"),
            ("vilo.assets.matrix_md", "build_matrix_md_pipeline", "build_matrix_md_bindings",
             "Vilo 矩阵整合 markdown — 从已有矩阵 run 重建整合报告(确定性)"),
            ("vilo.fetch.style_texts", "build_fetch_style_pipeline", "build_fetch_style_bindings",
             "Vilo 参考文本抓取 — 下载开放版权文本到外部参考库(网络,确定性)"),
        ]:
            register(PipelineEntry(
                name=_vname,
                description=_vdesc,
                domain="vilo",
                build_team=_lazy(f"{_vilo_pkg}.team", _vbuild),
                build_bindings=_lazy_fn(f"{_vilo_pkg}.run", _vbind),
                default_db_dir="data/domains/vilo",
                default_max_steps=5,
                cli_args=[CliArg(name="only", help="(fetch)仅抓取某作品 id", default="")],
            ))
    except Exception as e:
        logger.debug("skip vilo pipelines: %s", e)

    # ── research 公开调研管线（2026-06-14 新开;2026-06-30 转原生搜索)──
    _research_pkg = "omnicompany.packages.domains.research"
    try:
        register(PipelineEntry(
            name="research.run",
            description="公开调研 — 入题查重→codex 原生 web 搜索搜读核源综合→落统一研究库(累积/不重复,无外部搜索 API)",
            domain="research",
            build_team=_lazy(f"{_research_pkg}.team", "build_research_pipeline"),
            build_bindings=_lazy_fn(f"{_research_pkg}.run", "build_research_bindings"),
            default_db_dir="data/domains/research",
            default_max_steps=10,
            cli_args=[
                CliArg(name="topic", help="调研题目(自然语言)", required=True),
                CliArg(name="timeout_s", help="codex 调研超时秒数", type=int, default=900),
            ],
        ))
    except Exception as e:
        logger.debug("skip research pipelines: %s", e)

    # ── frontend_design 前端设计与制作管线（2026-07-01 新开; 内化自 frostpane 方法论, 两平级分支）──
    _fd_pkg = "omnicompany.packages.domains.frontend_design"
    try:
        register(PipelineEntry(
            name="frontend_design.dashboard",
            description=(
                "前端设计·dashboard 分支 — dashboard 类网页(驾驶舱/poof/lofa)审查: "
                "标尺→确定性门禁→VLM相对评审→改进闭环+决策沉淀。标尺真源=docs/projects/frontend-design/dashboard。"
            ),
            domain="frontend_design",
            build_team=_lazy(f"{_fd_pkg}.team", "build_dashboard_review_pipeline"),
            build_bindings=_lazy_fn(f"{_fd_pkg}.run", "build_dashboard_bindings"),
            default_db_dir="data/domains/frontend_design",
            default_max_steps=10,
            cli_args=[
                CliArg(name="surface", help="要审的界面(url/截图路径/DOM快照)", required=True),
                CliArg(name="archetype", help="分支(自动)", default="dashboard"),
                CliArg(name="ruler_ref", help="标尺真源指针(默认 frostpane)", default=""),
                CliArg(name="baseline_ref", help="相对评审基准图(可选)", default=""),
            ],
            when={"semantic": "改完 dashboard 类网页(驾驶舱/poof/lofa)的界面要过设计审查门时;小改动(改个按钮)走快速路径直改+亮线检查,不必跑本管线",
                  "match_keys": ["dashboard", "frontend", "ui-review"], "judge": "llm"},
            scale={"tier": "short", "minutes": "3-10", "cost": "确定性门禁+1-2 次 VLM 评审"},
        ))
        register(PipelineEntry(
            name="frontend_design.webgame",
            description=(
                "前端设计·webgame 分支 — webgame UI 审查: "
                "标尺→确定性门禁→VLM相对评审→改进闭环+决策沉淀。标尺真源=tabletop-engine/README+walker specs(指针)。"
            ),
            domain="frontend_design",
            build_team=_lazy(f"{_fd_pkg}.team", "build_webgame_review_pipeline"),
            build_bindings=_lazy_fn(f"{_fd_pkg}.run", "build_webgame_bindings"),
            default_db_dir="data/domains/frontend_design",
            default_max_steps=10,
            cli_args=[
                CliArg(name="surface", help="要审的游戏屏(url/截图路径/DOM快照)", required=True),
                CliArg(name="archetype", help="分支(自动)", default="webgame"),
                CliArg(name="ruler_ref", help="标尺真源指针(默认 tabletop-engine/README)", default=""),
                CliArg(name="baseline_ref", help="相对评审基准图(上一版, 可选)", default=""),
            ],
            when={"semantic": "webgame UI 屏改动后要过设计审查门时(游戏屏全屏+悬浮HUD 标准)",
                  "match_keys": ["webgame", "frontend", "ui-review"], "judge": "llm"},
            scale={"tier": "short", "minutes": "3-10", "cost": "确定性门禁+1-2 次 VLM 评审"},
        ))
    except Exception as e:
        logger.debug("skip frontend_design pipelines: %s", e)

    # ── demogame.design_lint 细化案门禁管线（2026-07-05 收编; 用户拍板"都接入"统一设计工作室）──
    # 收编 design_doc_lint 的 lint.py + semantic_review.py 成 omni run: lexicon(可选)→lint→
    # semantic_review(--no-llm 默认)→landing(审阅台材料 project=demogame-design)。节点全 RULE。
    _design_lint_pkg = "omnicompany.packages.domains.demogame.design_doc_lint.pipeline"
    try:
        register(PipelineEntry(
            name="demogame.design_lint",
            description=(
                "细化案门禁 — lint(协作/交付) + 语义审查(命名白名单三层网, --no-llm 默认) → 仓内 gate_reports 报告.\n"
                "  omni run demogame.design_lint -i doc=<细化案.md> -i mode=协作\n"
                "  omni run demogame.design_lint -i doc=<细化案.md> -i mode=交付 -i no_llm=0  (开 LLM 兜底)\n"
                "  落点: 默认 data/domains/demogame/designer_workflow/gate_reports/ 文件; 过程报告不进审阅台, 显式 -i land=1 才建材料(track=细化案协作稿|细化案交付稿)"
            ),
            domain="demogame_design_doc",
            build_team=_lazy(_design_lint_pkg, "build_design_lint_pipeline"),
            build_bindings=_lazy_fn(_design_lint_pkg, "build_design_lint_bindings"),
            default_db_dir="data/domains/demogame/design_doc_lint",
            default_max_steps=10,
            cli_args=[
                CliArg(name="doc", help="要审的细化案 md 路径(绝对或相对)", required=True),
                CliArg(name="mode", help="协作|交付(默认 协作)", default="协作"),
                CliArg(name="no_llm", help="语义审查是否只跑确定性扫描(默认 1=是; -i no_llm=0 开 LLM 兜底)", default="1"),
                CliArg(name="model", help="语义审查 LLM 兜底模型(no_llm=0 时用, 默认 gpt-5.5)", default="gpt-5.5"),
                CliArg(name="build_lexicon", help="是否重建权威词表(默认否; 读外部 wiki 真源)", default=""),
            ],
        ))
    except Exception as e:
        logger.debug("skip demogame.design_lint pipeline: %s", e)

    # ── project_atlas 项目及业务收集管线（2026-06-21 新开, 挂 omnidashboard 下）──
    _project_atlas_pkg = "omnicompany.packages.domains.project_atlas"
    try:
        register(PipelineEntry(
            name="project_atlas.run",
            description=(
                "项目及业务收集 — 勘察工作空间→按操作/生产对象切 object-SKILL(lark-cli 粒度)→落 staging 待人审 + 维护项目速览名录。\n"
                "  omni run project_atlas.run -i space=omnicompany\n"
                "  omni run project_atlas.run -i space=omnicompany -i dry_run=1   (不调模型, 验管线)"
            ),
            domain="project_atlas",
            build_team=_lazy(f"{_project_atlas_pkg}.team", "build_project_atlas_pipeline"),
            build_bindings=_lazy_fn(f"{_project_atlas_pkg}.run", "build_project_atlas_bindings"),
            default_db_dir="data/domains/project_atlas",
            default_max_steps=10,
            cli_args=[
                CliArg(name="space", help="工作空间(omnicompany/quant-lab/webworks/poof/aiworkspace)", required=True),
                CliArg(name="dry_run", help="不调模型, 走占位验管线", is_flag=True),
            ],
        ))
    except Exception as e:
        logger.debug("skip project_atlas pipelines: %s", e)

    # ── slidecast 演示式讲解/说书生成管线（2026-06-20 新开, 类别 aigc-video-content）──
    _slidecast_pkg = "omnicompany.packages.domains.slidecast"
    try:
        register(PipelineEntry(
            name="slidecast.run",
            description=(
                "演示式讲解/说书生成 — 文章/主题→大纲→会动的 slide IR→校验→渲染 Slidev→构建可交互 HTML。\n"
                "  omni run slidecast.run -i article=<文章md路径>\n"
                "  omni run slidecast.run -i topic=\"<题目>\" -i build=0\n"
                "  产物: data/domains/slidecast/runs/<slug>/(slides.md + dist/ 会动的 HTML)"
            ),
            domain="slidecast",
            build_team=_lazy(f"{_slidecast_pkg}.team", "build_slidecast_pipeline"),
            build_bindings=_lazy_fn(f"{_slidecast_pkg}.run", "build_slidecast_bindings"),
            default_db_dir="data/domains/slidecast",
            default_max_steps=10,
            cli_args=[
                CliArg(name="article", help="文章 md 路径(personal-homepage 的 curated/works md,绝对或相对)"),
                CliArg(name="topic", help="题目(无文章时,从题目起)"),
                CliArg(name="style", help="风格: 讲解|说书", default="讲解"),
                CliArg(name="build", help="是否 slidev build 出 HTML(默认是,-i build=0 只产 slides.md)", default="1"),
            ],
        ))
    except Exception as e:
        logger.debug("skip slidecast pipelines: %s", e)

    # ── publish 对外发布 / 知识备份管线（2026-06-15 新开）──
    _publish_pkg = "omnicompany.packages.domains.publish"
    try:
        register(PipelineEntry(
            name="publish.aiworkspace_snapshot",
            description=(
                "AIWorkSpace 知识快照 — 收明文(排图片/构建/二进制, 二进制嗅探)→镜像进 gitee 暂存克隆"
                "→提交并(--push)推 aiworkspace-snapshot 分支。默认 --dry_run 先预览增删改。"
            ),
            domain="publish",
            build_team=_lazy(f"{_publish_pkg}.team", "build_aiworkspace_snapshot_pipeline"),
            build_bindings=_lazy_fn(f"{_publish_pkg}.run", "build_aiworkspace_snapshot_bindings"),
            default_db_dir="data/domains/publish",
            default_max_steps=10,
            cli_args=[
                CliArg(name="src", help="AIWorkSpace 根(默认 d:/P4/main/AIWorkSpace 或 OMNI_AIWORKSPACE_ROOT)", default=""),
                CliArg(name="dry_run", help="只算清单+diff 预览, 不提交不推送", is_flag=True),
                CliArg(name="push", help="提交后推送到 gitee(默认只本地提交, 显式 --push 才推)", is_flag=True),
                CliArg(name="max_file_mb", help="单文件大小上限 MB(超过当数据跳过)", type=int, default=2),
            ],
        ))
    except Exception as e:
        logger.debug("skip publish pipelines: %s", e)

    # ── personal_site 作品集生产管线（2026-06-20 内化）──
    _psite_pkg = "omnicompany.packages.domains.personal_site"
    try:
        register(PipelineEntry(
            name="personal_site.run",
            description=(
                "colorc.cc 作品集/dev-log 生产 — 入题→生成(起 claude-code 工人深读真源)→改造"
                "(本质意译去术语+加结构+真demo)→对抗门→落地建索引→脱敏门发布→[可选]demo分支(委托 slidecast 出会动演示)。默认 --dry_run 短路工人。"
            ),
            domain="personal_site",
            build_team=_lazy(f"{_psite_pkg}.team", "build_personal_site_pipeline"),
            build_bindings=_lazy_fn(f"{_psite_pkg}.run", "build_personal_site_bindings"),
            default_db_dir="data/domains/personal_site",
            default_max_steps=12,
            cli_args=[
                CliArg(name="targets", help="目标 JSON 数组 [{kind:work|devlog,slug,report,repo,focus,company?,tags?}]", default=""),
                CliArg(name="stages", help="要跑的阶段(逗号分隔);加 demo 则过审后出演示deck", default="generate,restyle,verify,place,publish"),
                CliArg(name="dry_run", help="短路工人,只走确定性节点(冒烟/查拓扑)", is_flag=True),
                CliArg(name="deploy", help="发布节点脱敏门过后提示部署命令(管线不直接 ssh,留人工闸)", is_flag=True),
            ],
        ))
    except Exception as e:
        logger.debug("skip personal_site pipelines: %s", e)

    # ── narrative 叙事管线 ──
    # narrative.a5_loop / narrative.beat.generate 已退役(2026-07-04,统一设计工作室计划 D1):
    # 域 DESIGN.md 自判"简陋雏形不要再推进",真正的叙事工作台=packages/narrative_studio;
    # 模块文件带 superseded 标头留档,注册摘除。csl.ingest 保留。
    _narrative_pkg = "omnicompany.packages.domains.narrative"
    try:
        register(PipelineEntry(
            name="narrative.csl.ingest",
            description="Narrative CSL 摄入 — scene 写完后自动记账，提议 anchor/state/hook 供作者确认",
            domain="narrative",
            build_team=_lazy(f"{_narrative_pkg}.team_csl", "build_csl_ingest_pipeline"),
            build_bindings=_lazy_fn(f"{_narrative_pkg}.run", "build_csl_ingest_bindings"),
            default_db_dir="data/domains/narrative",
            default_max_steps=10,
            cli_args=[
                CliArg(name="scene_id", help="Scene ID", required=True),
                CliArg(name="entity_refs", help="涉及的实体 ID（逗号分隔）", default=""),
            ],
        ))
    except Exception as e:
        logger.debug("skip narrative pipelines: %s", e)




    # ── csv-to-md · 由 team_builder V3 生成 (dry_run · 2026-04-23) ──
    try:
        register(PipelineEntry(
            name="csv-to-md",
            description='Csv To Md · 由 team_builder 自动生成 (9 个文件, 29053 bytes)',
            domain="csv_to_md",
            build_team=_lazy(
                "omnicompany.packages.services._utility.csv_to_md.team",
                "build_team",
            ),
            build_bindings=_lazy_fn(
                "omnicompany.packages.services._utility.csv_to_md.run",
                "build_bindings",
            ),
            default_db_dir="data/services/csv_to_md",
            default_max_steps=1000,
            cli_args=[
                CliArg(name="text", help="自然语言需求"),
            ],
        ))
    except Exception as e:
        logger.debug("skip csv-to-md: %s", e)

    # ── repo-absorption · 由 team_builder V3 生成 (dry_run · 2026-04-23) ──
    try:
        register(PipelineEntry(
            name="repo-absorption",
            description='Repo Absorption · 由 team_builder 自动生成 (12 个文件, 116812 bytes)',
            domain="repo_absorption",
            build_team=_lazy(
                "omnicompany.packages.services._learning.repo.absorption.team",
                "build_team",
            ),
            build_bindings=_lazy_fn(
                "omnicompany.packages.services._learning.repo.absorption.run",
                "build_bindings",
            ),
            default_db_dir="data/services/repo_absorption",
            default_max_steps=1000,
            cli_args=[
                CliArg(name="text", help="自然语言需求"),
            ],
        ))
    except Exception as e:
        logger.debug("skip repo-absorption: %s", e)

    # ── runtime-test-builder · 真 meta 层 v2 (2026-04-27 Phase C 重构, 替旧伪 meta) ──
    # 当场针对生成假设 + 调度验证, 不再二选一固定模板
    try:
        register(PipelineEntry(
            name="runtime-test-builder",
            description=(
                "真 meta 层 v2 测试团队构建器 — 给 target_team_id 深探 target 包 + "
                "综合 hypothesis_library 当场针对生成假设清单 (3-10 条特化假设) + "
                "调度每条验证 + 装画像. 非二选一固定模板."
            ),
            domain="runtime_test_builder",
            build_team=_lazy(
                "omnicompany.packages.services._utility.runtime_test.builder.team",
                "build_team",
            ),
            build_bindings=_lazy_fn(
                "omnicompany.packages.services._utility.runtime_test.builder.run",
                "build_bindings",
            ),
            default_db_dir="data/services/runtime_test_builder",
            default_max_steps=1000,
            cli_args=[
                CliArg(name="target_team_id", help="待测目标团队 id"),
            ],
        ))
    except Exception as e:
        logger.debug("skip runtime-test-builder: %s", e)

    # ── code-runtime-test · 代码产物测试团队 (2026-04-26 立) ──
    # 标杆对标 + 错误处理 + 重现性 · 全 HARD 不调 LLM · 跟 absorption-runtime-test 平行
    try:
        register(PipelineEntry(
            name="code-runtime-test",
            description=(
                "代码产物测试团队 — 跑 target 多个 fixtures 跟 expected byte-diff + "
                "error path verdict 验 + 重现性 byte-identical. 全 HARD 不调 LLM. 代码产物专用."
            ),
            domain="code_runtime_test",
            build_team=_lazy(
                "omnicompany.packages.services._utility.runtime_test.code.team",
                "build_team",
            ),
            build_bindings=_lazy_fn(
                "omnicompany.packages.services._utility.runtime_test.code.run",
                "build_bindings",
            ),
            default_db_dir="data/services/code_runtime_test",
            default_max_steps=1000,
            cli_args=[
                CliArg(name="target_team_id", help="待测目标团队 id (如 'csv-to-md')"),
            ],
        ))
    except Exception as e:
        logger.debug("skip code-runtime-test: %s", e)

    # ── absorption-runtime-test · absorption 类工作的特化测试团队 (2026-04-27 改名 + 砍路 2 + 升路 4) ──
    # 旧名 knowledge-runtime-test (2026-04-26 立, 4 路通用模板) — 误抽象层级
    # 现 (Phase A): 标明 absorption 特化 · 3 路 (稳定 + 抽样落地 + 源覆盖) · 升路 4 程序化排名
    # 沉淀自 data/domains/test_team/scratch/ 的 3 实验 + plan.md 来龙去脉
    try:
        register(PipelineEntry(
            name="absorption-runtime-test",
            description=(
                "absorption 类工作的特化测试团队 — 真跑 target N 次 + 3 路特化验证 (跨次稳定 / "
                "抽样落地 / 源覆盖 程序化排名 top-K) → 产画像 (非契约扫, 非通用模板). "
                "仅适用 absorption 类 (代码改进提案) target. 真通用层在 Phase B/C 立."
            ),
            domain="absorption_runtime_test",
            build_team=_lazy(
                "omnicompany.packages.services._utility.runtime_test.absorption.team",
                "build_team",
            ),
            build_bindings=_lazy_fn(
                "omnicompany.packages.services._utility.runtime_test.absorption.run",
                "build_bindings",
            ),
            default_db_dir="data/services/absorption_runtime_test",
            default_max_steps=1000,
            cli_args=[
                CliArg(name="target_team_id", help="待测目标团队 id (如 'repo-absorption')"),
            ],
        ))
    except Exception as e:
        logger.debug("skip absorption-runtime-test: %s", e)

    # ── team-supervisor · 通用 team 健康监督 (2026-04-26 立) ──
    # 设计文档: docs/plans/[2026-04-26]TEAM-SUPERVISOR/plan.md
    try:
        register(PipelineEntry(
            name="team-supervisor",
            description=(
                "通用 team 健康监督 — 三问 (Q1 产物形式 / Q2 设计目的 / Q3 健康判据) + "
                "假设进化 + 信号模式. 只产 health_report, 不修复 target. "
                "首批喂 repo-absorption."
            ),
            domain="team_supervisor",
            build_team=_lazy(
                "omnicompany.packages.services._core.team_supervisor.team",
                "build_team",
            ),
            build_bindings=_lazy_fn(
                "omnicompany.packages.services._core.team_supervisor.run",
                "build_bindings",
            ),
            default_db_dir="data/services/team_supervisor",
            default_max_steps=1000,
            cli_args=[
                CliArg(name="target_team_id", help="待监督的 team id (如 'repo-absorption')"),
            ],
        ))
    except Exception as e:
        logger.debug("skip team-supervisor: %s", e)

    # ── 自动注册 G2 里的 yaml team (F7 修复, 2026-05-02 加) ──
    try:
        _register_g2_yaml_teams()
    except Exception as e:
        logger.debug("skip _register_g2_yaml_teams: %s", e)

    # ── 注册 2026-06-23 沉淀的事件型 team (脑子=自建 gpt-5.5 tool-agent) ──
    try:
        _register_sedimented_event_teams()
    except Exception as e:
        logger.debug("skip _register_sedimented_event_teams: %s", e)

    # ── 注册外部域挂载管线 (批7首件, 2026-07-03): 独立业务仓(quant-lab 等)经
    #    config/external_mounts.yaml + 各仓根 .omni-mount.yaml 动态挂进注册表 ──
    try:
        _register_external_mounts()
    except Exception as e:
        logger.debug("skip _register_external_mounts: %s", e)

    logger.debug("register_all: done")


def _register_sedimented_event_teams() -> None:
    """注册 2026-06-23 沉淀的事件型 team(engine=event, 踩 E1 按名可跑)。

    脑子是自建 gpt-5.5 tool-agent(runtime.agent.tool_agent), 手是确定性落地 worker。
    每个 team 独立 try/except — 缺一个不影响其它; build_team 闭包内懒导入, 注册期不拉模块。
    """
    import importlib

    from omnicompany.core.registry import PipelineEntry, register

    def _ev(name: str, desc: str, domain: str, module: str, entry_material: str) -> None:
        def _build_team(_m: str = module) -> list:
            mod = importlib.import_module(_m)
            return [W() for W in mod.ALL_WORKERS]

        register(PipelineEntry(
            name=name, description=desc, domain=domain, engine="event",
            build_team=_build_team, build_bindings=lambda *a, **k: {},
            entry_material=entry_material, default_db_dir=f"data/services/{domain}",
        ))

    teams = [
        ("plan-progress-recorder",
         "[沉淀·事件型] 读一个计划自评进度并记录进 whatnow(脑子=gpt-5.5 tool-agent)",
         "focus",
         "omnicompany.packages.services._focus.plan_progress_recorder.workers",
         "planprog.request"),
        ("conversation-operation-sedimenter",
         "[沉淀·事件型] 从 CC/codex 对话提取常见操作并提出可沉淀 team 骨架(脑子=gpt-5.5 tool-agent)",
         "agent_framework",
         "omnicompany.packages.services._learning.conversation_sedimenter.workers",
         "convop.request"),
    ]
    for args in teams:
        try:
            _ev(*args)
        except Exception as e:  # noqa: BLE001
            logger.debug("skip event team %s: %s", args[0], e)


def _register_external_mounts() -> None:
    """把外部业务仓(quant-lab 等)的挂载管线注册进 core.registry (批7首件, 2026-07-03).

    借 _register_g2_yaml_teams() 的"读声明式清单动态注册"形状, 但新增了全仓无先例的
    "外部路径进导入路径"层 —— 具体实现在
    omnicompany.packages.services._core.registry.external_mounts.register_external_pipelines(),
    单条挂载失败隔离、名字/前缀/重名三重校验、数据落业务仓自己的 data/ 均在那里。

    此处只是把它接进 register_all() 的注册末尾, 并把结果记到 debug 日志。真实登记表
    (config/external_mounts.yaml)不存在时 register_external_pipelines 自然返回空报告,
    不影响既有管线注册。
    """
    try:
        from omnicompany.packages.services._core.registry.external_mounts import (
            register_external_pipelines,
        )
    except ImportError as e:
        logger.debug("外部挂载注册跳过 (依赖不可用): %s", e)
        return

    report = register_external_pipelines()
    if report.get("registered"):
        logger.debug("外部挂载注册 %d 条: %s", len(report["registered"]), report["registered"])
    for item in report.get("skipped", []):
        logger.debug("外部挂载跳过: %s", item)
    for item in report.get("rejected", []):
        logger.warning("外部挂载拒绝(重名/未带前缀): %s", item)


def _register_g2_yaml_teams() -> None:
    """自动把 G2 注册中心里的 yaml team 注册到 core.registry._REGISTRY.

    F7 修复: G2 (元数据) vs core.registry (调度) 两份没打通. 任何 yaml team 立完就
    自动有 PipelineEntry 让 `omni run` 能调.

    流程:
      1. 查 G2 中心 type=pipeline 的 entries
      2. 过滤 source_file 是 .yaml 的 (yaml team form)
      3. 读 yaml 拿 team.id / team.description (如果失败用 fallback)
      4. 立 PipelineEntry, build_team 是闭包调 load_team_from_yaml
    """
    from omnicompany.core.registry import register, PipelineEntry
    try:
        from omnicompany.packages.services._core.registry import get_registry
        from omnicompany.packages.services._core.team_loader import load_team_from_yaml
    except ImportError as e:
        logger.debug("G2 yaml team 自动注册跳过 (依赖不可用): %s", e)
        return

    reg = get_registry()
    proj_root = _project_root_for_g2()
    count = 0
    for entry in reg.list_all():
        if entry.type != "pipeline":
            continue
        if not entry.source_file.endswith((".yaml", ".yml")):
            continue
        yaml_abs = proj_root / entry.source_file
        if not yaml_abs.is_file():
            logger.debug("G2 yaml team source 不存在, 跳过: %s", yaml_abs)
            continue

        # 读 yaml 拿元数据 (失败用 fallback)
        team_id = entry.name
        team_desc = entry.attrs.get("description", "")
        if not team_desc:
            try:
                import yaml as _yaml
                with open(yaml_abs, encoding="utf-8") as f:
                    raw = _yaml.safe_load(f)
                team_id = raw.get("id", team_id)
                team_desc = raw.get("description", f"yaml team auto-registered from G2: {team_id}")
            except Exception:
                team_desc = f"yaml team auto-registered from G2: {team_id}"

        # domain: 从 source_file 派生 (例: src/.../services/_authoring/mass_materialization/teams/x.yaml → "_authoring.mass_materialization")
        domain = _derive_domain_from_path(entry.source_file)

        # 闭包: build_team 调 load_team_from_yaml,
        # build_bindings 通过 G2 反查找 team 同 service 内的 router/agent 类自动绑定
        yaml_path_str = str(yaml_abs)
        source_file_str = entry.source_file
        def _build_team(yaml_path=yaml_path_str):
            return load_team_from_yaml(yaml_path)
        def _build_bindings(input_dict=None, yaml_path=yaml_path_str, src_file=source_file_str):
            return _resolve_yaml_team_bindings(yaml_path, src_file)

        try:
            register(PipelineEntry(
                name=team_id,
                description=team_desc,
                domain=domain,
                build_team=_build_team,
                build_bindings=_build_bindings,
                default_db_dir=f"data/services/{domain.replace('.', '/')}",
                default_max_steps=1000,
            ))
            count += 1
        except Exception as e:
            logger.debug("yaml team %s 注册失败: %s", team_id, e)

    if count:
        logger.debug("F7 自动注册 %d 个 G2 yaml team 到 core.registry", count)


class _NoopBus:
    """Placeholder bus for yaml team binding 阶段 (TeamRunner 后续会替换为真 bus).

    SingleToolRouter / AgentNodeLoop 在 init 时硬要求 bus != None, 但 build_bindings
    早于真 bus 创建. 此 stub 提供最小接口让 init 通过, 真 bus 由 runner._bus = real_bus 注入.
    """
    async def publish(self, event):
        return "noop_event_id"
    def emit(self, *a, **kw):
        pass


def _resolve_yaml_team_bindings(yaml_path: str, source_file: str) -> dict:
    """yaml team 的 binding 从 G2 反查找:

    1. 装载 team 拿 node_ids
    2. 派生 service package prefix (从 source_file)
    3. G2 查询 type in (router/agent_loop) + package 在 service 下
    4. 名字匹配 (snake node_id ↔ CamelCase 类名 - 后缀): 例 file_scanner ↔ FileScannerWorker
    5. import + 实例化, 组装 dict[node_id, instance]
    """
    import importlib
    import re
    from omnicompany.packages.services._core.registry import get_registry
    from omnicompany.packages.services._core.team_loader import load_team_from_yaml

    team = load_team_from_yaml(yaml_path)
    node_ids = {n.id for n in team.nodes}

    # service package prefix: 从 source_file 派生, 保留到 service 一级 (不进 teams/agents/workers/tools)
    parts = source_file.replace("\\", "/").split("/")
    pkg_prefix = ""
    try:
        idx = parts.index("services")
        if idx + 2 < len(parts):
            pkg_prefix = ".".join(parts[: idx + 3])  # src.omnicompany.packages.services._<bucket>.<service>
    except ValueError:
        pass
    if not pkg_prefix:
        return {}

    def _camel_to_snake(name: str) -> str:
        s1 = re.sub(r"(.)([A-Z][a-z]+)", r"\1_\2", name)
        return re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", s1).lower()

    bindings: dict = {}
    reg = get_registry()
    for entry in reg.list_all():
        if entry.type not in ("router", "agent_loop"):
            # tool 类不直接进 team binding (tool 由 agent 调)
            continue
        if not entry.package.startswith(pkg_prefix):
            continue
        # G2 entry.name 是 snake 模块名 (例 "file_scanner"), 不是类名
        # 直接跟 node_id 比较
        if entry.name not in node_ids:
            continue
        node_id = entry.name

        # import 模块, 找类: snake_to_camel + 按 type 加后缀 (Worker / Agent)
        # 候选顺序: 先看类名是否已含后缀 (例 material_id_agent.py 内 class MaterialIdAgent),
        # 再试加后缀 (例 file_scanner.py 内 class FileScannerWorker)
        camel = "".join(p.capitalize() for p in entry.name.split("_"))
        type_suffix = {"router": "Worker", "agent_loop": "Agent"}.get(entry.type, "")
        # 名字已含后缀就不重叠: material_id_agent → MaterialIdAgent (不加 Agent 再加)
        already_has_suffix = type_suffix and camel.endswith(type_suffix)
        if already_has_suffix:
            candidate_class_names = [camel, f"{camel}{type_suffix}"]
        else:
            candidate_class_names = [f"{camel}{type_suffix}", camel]
        try:
            module_path = entry.package + "." + entry.name
            mod = importlib.import_module(module_path)
            cls = None
            for cn in candidate_class_names:
                cls = getattr(mod, cn, None)
                if cls is not None:
                    break
            if cls is None:
                logger.debug("yaml team binding %s: 找不到类 (候选 %s) in %s",
                            node_id, candidate_class_names, module_path)
                continue
            # AgentNodeLoop 跟其内部 SingleToolRouter 都硬要求 bus != None 在 init.
            # 但 build_bindings 早于真 bus 创建. 用 _NoopBus 占位让 init 过, TeamRunner
            # 后续走 router._bus = self.bus 注入真 bus.
            if entry.type == "agent_loop":
                bindings[node_id] = cls(bus=_NoopBus())
            else:
                bindings[node_id] = cls()
        except Exception as e:
            logger.debug("yaml team binding %s 实例化失败: %s", node_id, e)
    return bindings


def _project_root_for_g2() -> "Path":  # noqa: F821 (forward ref)
    """omnicompany 项目根 (跟 sandbox._project_root 一致)."""
    from pathlib import Path
    here = Path(__file__).resolve()
    for p in (here, *here.parents):
        if (p / "src" / "omnicompany").is_dir() and (p / "docs").is_dir():
            return p
    return here.parents[3]


def _derive_domain_from_path(source_file: str) -> str:
    """从 source_file 派生 domain 标识.

    例: 'src/omnicompany/packages/services/_authoring/mass_materialization/teams/x.yaml'
       → '_authoring.mass_materialization'
    例: 'src/omnicompany/packages/domains/demogame/.../x.yaml'
       → 'demogame'
    """
    parts = source_file.replace("\\", "/").split("/")
    try:
        idx = parts.index("services")
        if idx + 2 < len(parts):
            return f"{parts[idx + 1]}.{parts[idx + 2]}"
    except ValueError:
        pass
    try:
        idx = parts.index("domains")
        if idx + 1 < len(parts):
            return parts[idx + 1]
    except ValueError:
        pass
    return "default"


# ── 懒加载工具 ─────────────────────────────────────────────────────────────

def _lazy(module_path: str, attr_name: str):
    """返回一个 callable，首次调用时才 import 目标模块并获取属性。"""
    _cache = {}
    def wrapper(*args, **kwargs):
        if "fn" not in _cache:
            import importlib
            mod = importlib.import_module(module_path)
            _cache["fn"] = getattr(mod, attr_name)
        return _cache["fn"](*args, **kwargs)
    return wrapper


def _lazy_fn(module_path: str, attr_name: str):
    """与 _lazy 相同，但专用于 build_bindings —— 函数可能不存在时返回空 dict。"""
    _cache = {}
    def wrapper(*args, **kwargs):
        if "fn" not in _cache:
            import importlib
            try:
                mod = importlib.import_module(module_path)
                _cache["fn"] = getattr(mod, attr_name, lambda *a, **k: {})
            except ImportError:
                _cache["fn"] = lambda *a, **k: {}
        return _cache["fn"](*args, **kwargs)
    return wrapper
