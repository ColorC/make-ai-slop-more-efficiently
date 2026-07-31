# [OMNI] origin=claude-code domain=omnicompany/core ts=2026-04-08T03:23:35Z
# [OMNI] material_id="material:omnicompany.core.pipelines.pipeline_registrar.aggregator.py"
"""omnicompany.core.pipelines — 管线懒加载注册（基础设施）

将所有已知管线注册到全局 Registry，但使用延迟 import 避免在 CLI 启动时
拉入业务域等重依赖。

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

    # project-audit / project-discovery 于 2026-07-22 退役：多次真实运行无产出或识别 0 项。
    # 项目盘点由 Project Atlas 承担；计划审计改用 omni plan audit 与计划自身的测试/审阅证据。

    # ── ux-audit — 前端三维 UX 审计(交互/信息/跳转)· 确定性枚举 + 据矩阵/层级打错位标记 ──
    try:
        register(PipelineEntry(
            name="ux-audit",
            description=(
                "前端 src 三维 UX 审计 — 交互/信息/跳转 确定性枚举 + 据频率×重要性矩阵/信息层级打错位标记(平铺/删除无保护/无层级/说明冗余).\n"
                "  omni run ux-audit -i src_root=<your-frontend-src> -i app=omnidashboard\n"
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

    # Repo absorption 实验族已于 2026-07-22 退出活跃注册。
    # 外部仓库学习改走 research + 明确问题驱动的手工 SOP；历史实现保存在 _graveyard。

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
            description="按需轨迹归纳 — 从真实 trace 提取 SOP 与可审阅需求候选；不自动生成或注册 pipeline",
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
                CliArg(
                    name="source",
                    help="轨迹来源: auto、intent 或 external",
                    default="auto",
                ),
                CliArg(
                    name="provider",
                    help="外部 Agent: codex、claude 或 kimi",
                    default="",
                ),
                CliArg(
                    name="sync",
                    help="读取前仅增量同步指定会话",
                    type=bool,
                    default=True,
                ),
                CliArg(name="purpose", help="归纳目的描述"),
                CliArg(name="trace_ids", help="逗号分隔的 trace ID 列表"),
            ],
        ))
    except Exception as e:
        logger.debug("skip trace-induction: %s", e)

    # pattern-discovery 自动聚类/自动触发已于 2026-07-22 退役。
    # trace-induction 只在真实重复操作出现后按需调用。

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
                CliArg(name="src", help="快照源根(OMNI_AIWORKSPACE_ROOT 环境变量)", default=""),
                CliArg(name="dry_run", help="只算清单+diff 预览, 不提交不推送", is_flag=True),
                CliArg(name="push", help="提交后推送到 gitee(默认只本地提交, 显式 --push 才推)", is_flag=True),
                CliArg(name="max_file_mb", help="单文件大小上限 MB(超过当数据跳过)", type=int, default=2),
            ],
        ))
    except Exception as e:
        logger.debug("skip publish pipelines: %s", e)

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
                CliArg(name="model", help="两个 AGENT 节点使用的显式模型 id"),
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
                CliArg(name="target_team_id", help="待测目标团队 id"),
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
                "与 runtime-test 的能力收口仍需一次真实诊断任务验证。"
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
                CliArg(name="target_team_id", help="待监督的已注册 team id"),
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
    例: 'src/omnicompany/packages/domains/research/.../x.yaml'
       → 'research'
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
