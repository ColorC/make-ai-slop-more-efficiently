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
            name="sw-verify",
            description="软件验证 — 验证声称是否有命令输出证据支持",
            domain="sw_verify",
            build_team=_lazy("omnicompany.packages.domains.software_engineering.verify.team", "build_team"),
            build_bindings=_lazy("omnicompany.packages.domains.software_engineering.verify.run", "build_bindings"),
            default_db_dir="data/domains/software_engineering",
            default_max_steps=20,
        ))
    except Exception as e:
        logger.debug("skip sw-verify: %s", e)

    try:
        register(PipelineEntry(
            name="sw-review",
            description="代码审查 — diff 收集 + 上下文探索 + LLM 深度审查",
            domain="sw_review",
            build_team=_lazy("omnicompany.packages.domains.software_engineering.review.team", "build_team"),
            build_bindings=_lazy("omnicompany.packages.domains.software_engineering.review.run", "build_bindings"),
            default_db_dir="data/domains/software_engineering",
            default_max_steps=25,
        ))
    except Exception as e:
        logger.debug("skip sw-review: %s", e)

    try:
        register(PipelineEntry(
            name="sw-plan",
            description="实施计划 — 代码库探索 + TDD 计划生成 + 自检循环",
            domain="sw_plan",
            build_team=_lazy("omnicompany.packages.domains.software_engineering.plan.team", "build_team"),
            build_bindings=_lazy("omnicompany.packages.domains.software_engineering.plan.run", "build_bindings"),
            default_db_dir="data/domains/software_engineering",
            default_max_steps=30,
        ))
    except Exception as e:
        logger.debug("skip sw-plan: %s", e)

    try:
        register(PipelineEntry(
            name="sw-design",
            description="设计审查 — 架构扫描 + 模式分析 + LLM 审查",
            domain="sw_design",
            build_team=_lazy("omnicompany.packages.domains.software_engineering.design.team", "build_team"),
            build_bindings=_lazy("omnicompany.packages.domains.software_engineering.design.run", "build_bindings"),
            default_db_dir="data/domains/software_engineering",
            default_max_steps=25,
        ))
    except Exception as e:
        logger.debug("skip sw-design: %s", e)

    try:
        register(PipelineEntry(
            name="sw-tdd",
            description="TDD 执行 — 写测试 + 跑测试 + 写实现 + 修复回路",
            domain="sw_tdd",
            build_team=_lazy("omnicompany.packages.domains.software_engineering.tdd.team", "build_team"),
            build_bindings=_lazy("omnicompany.packages.domains.software_engineering.tdd.run", "build_bindings"),
            default_db_dir="data/domains/software_engineering",
            default_max_steps=30,
        ))
    except Exception as e:
        logger.debug("skip sw-tdd: %s", e)

    try:
        register(PipelineEntry(
            name="sw-implement",
            description="独立实施 — 需求解析 + 代码库扫描 + LLM 实施",
            domain="sw_implement",
            build_team=_lazy("omnicompany.packages.domains.software_engineering.implement.team", "build_team"),
            build_bindings=_lazy("omnicompany.packages.domains.software_engineering.implement.run", "build_bindings"),
            default_db_dir="data/domains/software_engineering",
            default_max_steps=25,
        ))
    except Exception as e:
        logger.debug("skip sw-implement: %s", e)

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
                                "build_explore_pipeline"),
            build_bindings=_lazy("omnicompany.packages.domains.demogame.unity_explore.run_pipeline",
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
                "build_team",
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
                "build_team",
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
                "build_team",
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
                "build_team",
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
                "build_team",
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
    # 概念并入 doctor _hypothesis/. guardian 留纯规则部分.

    # pipeline-ci pipeline 移除 (2026-05-05 诊断重制 step 5) — pipeline_ci 整体归档,
    # 三 Auditor 概念并入 doctor _spec/ 跟 _hypothesis/. 详 docs/plans/diagnosis/[2026-05-05]DIAGNOSIS-RECONSOLIDATION/plan.md

    # ── hypothesis 假设探索管线 ──
    try:
        register(PipelineEntry(
            name="hypothesis",
            description=(
                "假设探索 — agent 自由探索目标系统，自动归纳假设到主题文档。"
                "产出 data/knowledge/hypotheses/<domain>.md。"
                "真实多轮循环由 hypothesis.pipeline.run_session 驱动。"
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
        ))
    except Exception as e:
        logger.debug("skip hypothesis: %s", e)

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
            name="voxelcraft.design",
            description="voxelcraft 策划管线 — vision → GDD → balance → review",
            domain="voxelcraft",
            build_team=_lazy(f"{_bw_pkg}.team", "build_design_pipeline"),
            build_bindings=_lazy_fn(f"{_bw_pkg}.run", "build_design_bindings"),
            default_db_dir="data/domains/voxelcraft",
            default_max_steps=20,
        ))
        register(PipelineEntry(
            name="voxelcraft.engineering",
            description="voxelcraft 工程管线 — GDD → code → compile → debug loop",
            domain="voxelcraft",
            build_team=_lazy(f"{_bw_pkg}.team", "build_engineering_pipeline"),
            build_bindings=_lazy_fn(f"{_bw_pkg}.run", "build_engineering_bindings"),
            default_db_dir="data/domains/voxelcraft",
            default_max_steps=30,
        ))
        register(PipelineEntry(
            name="voxelcraft.combat_test",
            description="voxelcraft 战斗测试管线 — config → build → server → RCON test → evolve",
            domain="voxelcraft",
            build_team=_lazy(f"{_bw_pkg}.team", "build_combat_test_pipeline"),
            build_bindings=_lazy_fn(f"{_bw_pkg}.run", "build_combat_test_bindings"),
            default_db_dir="data/domains/voxelcraft",
            default_max_steps=30,
        ))
        register(PipelineEntry(
            name="voxelcraft.pm",
            description="voxelcraft PM 管线 — epic → sprint goals → schedule DAG",
            domain="voxelcraft",
            build_team=_lazy(f"{_bw_pkg}.team", "build_pm_pipeline"),
            build_bindings=_lazy_fn(f"{_bw_pkg}.run", "build_pm_bindings"),
            default_db_dir="data/domains/voxelcraft",
            default_max_steps=15,
        ))
        register(PipelineEntry(
            name="voxelcraft.art",
            description="voxelcraft 美术管线 — 通用资产搜索 + 分析 + 验证",
            domain="voxelcraft",
            build_team=_lazy(f"{_bw_pkg}.team", "build_art_pipeline"),
            build_bindings=_lazy_fn(f"{_bw_pkg}.run", "build_art_bindings"),
            default_db_dir="data/domains/voxelcraft",
            default_max_steps=15,
        ))
        register(PipelineEntry(
            name="voxelcraft.visual_assets",
            description="voxelcraft 兵种外观管线 — entity model search → style eval → texture map",
            domain="voxelcraft",
            build_team=_lazy(f"{_bw_pkg}.team", "build_visual_asset_pipeline"),
            build_bindings=_lazy_fn(f"{_bw_pkg}.run", "build_visual_assets_bindings"),
            default_db_dir="data/domains/voxelcraft",
            default_max_steps=25,
        ))
        register(PipelineEntry(
            name="voxelcraft.structures",
            description="voxelcraft 建筑管线 — schematic search → parse → validate → FillOp",
            domain="voxelcraft",
            build_team=_lazy(f"{_bw_pkg}.team", "build_structure_pipeline"),
            build_bindings=_lazy_fn(f"{_bw_pkg}.run", "build_structures_bindings"),
            default_db_dir="data/domains/voxelcraft",
            default_max_steps=15,
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

    # ── research 公开调研管线（2026-06-14 新开）──
    _research_pkg = "omnicompany.packages.domains.research"
    try:
        register(PipelineEntry(
            name="research.run",
            description="公开调研 — 入题查重→联网检索带来源→性价比模型综合→落统一研究库(累积/不重复)",
            domain="research",
            build_team=_lazy(f"{_research_pkg}.team", "build_research_pipeline"),
            build_bindings=_lazy_fn(f"{_research_pkg}.run", "build_research_bindings"),
            default_db_dir="data/domains/research",
            default_max_steps=10,
            cli_args=[
                CliArg(name="topic", help="调研题目(自然语言)", required=True),
                CliArg(name="max_results", help="保留片段数量级", type=int, default=6),
                CliArg(name="dry_run", help="离线 mock 检索(配 OMNI_WEB_SEARCH_DRY_RUN=1)", is_flag=True),
            ],
        ))
    except Exception as e:
        logger.debug("skip research pipelines: %s", e)

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
                "(本质意译去术语+加结构+真demo)→对抗门→落地建索引→脱敏门发布。默认 --dry_run 短路工人。"
            ),
            domain="personal_site",
            build_team=_lazy(f"{_psite_pkg}.team", "build_personal_site_pipeline"),
            build_bindings=_lazy_fn(f"{_psite_pkg}.run", "build_personal_site_bindings"),
            default_db_dir="data/domains/personal_site",
            default_max_steps=12,
            cli_args=[
                CliArg(name="targets", help="目标 JSON 数组 [{kind:work|devlog,slug,report,repo,focus,company?,tags?}]", default=""),
                CliArg(name="stages", help="要跑的阶段(逗号分隔)", default="generate,restyle,verify,place,publish"),
                CliArg(name="dry_run", help="短路工人,只走确定性节点(冒烟/查拓扑)", is_flag=True),
                CliArg(name="deploy", help="发布节点脱敏门过后提示部署命令(管线不直接 ssh,留人工闸)", is_flag=True),
            ],
        ))
    except Exception as e:
        logger.debug("skip personal_site pipelines: %s", e)

    # ── narrative 叙事管线 ──
    _narrative_pkg = "omnicompany.packages.domains.narrative"
    try:
        register(PipelineEntry(
            name="narrative.a5_loop",
            description="Narrative A5 闭环 — 作者意图 → 执行偏置 → 生成 scene → 达成度报告",
            domain="narrative",
            build_team=_lazy(f"{_narrative_pkg}.team", "build_a5_loop_pipeline"),
            build_bindings=_lazy_fn(f"{_narrative_pkg}.run", "build_a5_loop_bindings"),
            default_db_dir="data/domains/narrative",
            default_max_steps=10,
            cli_args=[
                CliArg(name="text", help="作者意图（自然语言）", required=True),
                CliArg(name="scope", help="意图范围：scene/session/character/global", default="scene"),
            ],
        ))
        register(PipelineEntry(
            name="narrative.beat.generate",
            description="Narrative Beat 生成 — 骨架节点→CSL约束注入→场景生成→一致性检查→戏剧验证",
            domain="narrative",
            build_team=_lazy(f"{_narrative_pkg}.team_beat", "build_beat_pipeline"),
            build_bindings=_lazy_fn(f"{_narrative_pkg}.run", "build_beat_bindings"),
            default_db_dir="data/domains/narrative",
            default_max_steps=15,
            cli_args=[
                CliArg(name="scene_id", help="Scene ID", required=True),
                CliArg(name="dramatic_function", help="这个场景必须完成的戏剧职能", required=True),
                CliArg(name="entity_refs", help="涉及的实体 ID（逗号分隔）", default=""),
                CliArg(name="scene_context", help="时间/地点/情境描述", default=""),
            ],
        ))
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

    logger.debug("register_all: done")


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
