# [OMNI] origin=omnicompany domain=omnicompany/guardian ts=2026-04-10T00:00:00Z
# [OMNI] material_id="material:core.guardian.format_definitions.registry.py"
"""guardian.formats — 守护检查管线的 Format 定义（正式 Format 对象）"""

from omnicompany.protocol.format import Format, FormatRegistry


FORMAT_CHECK_REQUEST = Format(
    id="guardian.check-request",
    name="Guardian Check Request",
    description=(
        "触发守护检查管线的入口请求，携带待检查的项目根目录路径、调用来源与运行上下文。"
        "上游通常来自 CLI、hook 或自动巡检任务；下游由 FsScannerWorker 消费并产出文件系统报告，"
        "用于后续架构审计与健康报告聚合。"
    ),
    parent="requirement",
    tags=["guardian", "input", "service", "kind.source"],
    examples=[
        {"project_root": "e:/WindowsWorkspace/omnicompany"},
    ],
)

FORMAT_FS_REPORT = Format(
    id="guardian.fs-report",
    name="Guardian FS Report",
    description=(
        "FsScannerRouter 的文件系统污染扫描结果。"
        "携带根目录非法条目、data/ 散落文件、类型命名临时文件等问题清单。"
        "每条 issue 含 category / severity / path / detail / suggestion 五字段。"
    ),
    parent="requirement",
    tags=["guardian", "report", "filesystem", "kind.internal"],
    examples=[
        {
            "project_root": "e:/WindowsWorkspace/omnicompany",
            "fs_issues": [
                {
                    "category": "root_contamination",
                    "severity": "high",
                    "path": "some_file.db",
                    "detail": "项目根目录出现非法 file: some_file.db",
                    "suggestion": "移动到 data/ 或 tmp/ 下",
                },
            ],
            "fs_issue_count": 1,
        },
    ],
)

FORMAT_ARCH_REPORT = Format(
    id="guardian.arch-report",
    name="Guardian Arch Report",
    description=(
        "ArchAuditorRouter 的架构规范问题清单。"
        "在 fs-report 全部字段基础上追加 arch_issues 和 arch_issue_count。"
        "arch_issues 每条含 category / severity / path / detail / suggestion。"
    ),
    parent="guardian.fs-report",
    tags=["guardian", "report", "architecture", "kind.internal"],
    examples=[
        {
            "project_root": "e:/WindowsWorkspace/omnicompany",
            "fs_issues": [],
            "fs_issue_count": 0,
            "arch_issues": [
                {
                    "category": "deprecated_module",
                    "severity": "low",
                    "path": "src/omnicompany/runtime/old.py",
                    "detail": "发现 DEPRECATED 标记",
                    "suggestion": "删除或迁移到 _graveyard/",
                },
            ],
            "arch_issue_count": 1,
        },
    ],
)

FORMAT_NODE_REPORT = Format(
    id="guardian.node-report",
    name="Guardian Node Report",
    description=(
        "[PLANNED, NOT IMPLEMENTED] lap_node_inspector 节点的 LAP 节点实现质量报告。"
        "计划检查 Router 类的 INPUT_KEYS / DESCRIPTION / FORMAT_IN / FORMAT_OUT 声明完整性。"
        "当前无节点产出此 Format。"
    ),
    parent="requirement",
    tags=["guardian", "report", "node", "planned", "kind.internal"],
    examples=[],  # 未实装，无示例
)

FORMAT_HEALTH_REPORT = Format(
    id="guardian.health-report",
    name="Guardian Health Report",
    description=(
        "HealthReporterRouter 的 LLM 综合健康评估结果. "
        "**契约变更 #01 (2026-04-25)**: 不打分, 保留完整语义信号. "
        "含 verdict (语义标签) · passed (binary · critical==0) · issues 全量 (含 severity/evidence/fix_hint) · "
        "counts 类别计数 (不加权求和) · top_actions 改进建议 · report 可读文本. "
        "**不含 health_score** — 分数无统一尺度, 已去除."
    ),
    parent="requirement",
    tags=["guardian", "report", "health", "kind.sink"],
    examples=[
        {
            "verdict": "healthy",
            "passed": True,
            "issues": [
                {
                    "severity": "minor",
                    "category": "root_contamination",
                    "field": "root_stray_file",
                    "message": "根目录有 1 个 .log 文件",
                    "evidence": "扫出 /scratch.log 在仓库根",
                    "fix_hint": "迁到 data/ 或删",
                },
            ],
            "counts": {"critical": 0, "major": 0, "minor": 1},
            "total_issues": 1,
            "top_actions": ["清理根目录散落文件"],
            "fs_issues": [],
            "arch_issues": [],
            "report": "项目整体健康, 仅 1 处轻微污染.",
            "summary": "仅 1 minor",
        },
    ],
)

FORMAT_HYGIENE_REQUEST = Format(
    id="guardian.hygiene-request",
    name="Guardian Hygiene Request",
    description=(
        "触发 Guardian 运行空间卫生巡查的入口. 可选 project_root 字段指定扫描根. "
        "本 Material 驱动 HygieneScanWorker 扫空文件夹 (OMNI-047) / 临时文件 (OMNI-048) / "
        "过期产物 (OMNI-049) / 体积异常 (OMNI-050) 四维, 不动数据, 只产告警清单."
    ),
    parent="requirement",
    tags=["guardian", "hygiene", "input", "kind.source"],
    examples=[{}, {"project_root": "e:/WindowsWorkspace/omnicompany"}],
)

FORMAT_HYGIENE_REPORT = Format(
    id="guardian.hygiene-report",
    name="Guardian Hygiene Report",
    description=(
        "HygieneScanWorker 运行空间卫生扫描结果, 双轨设计:\n"
        "  - violations: 硬规则命中 (OMNI-047 / OMNI-048a / OMNI-049 / OMNI-050)\n"
        "  - candidates_for_judge: 语义可疑候选 (OMNI-048b 等 needs_judgment 规则), "
        "    待 GuardianAgent LLM 复核 (I-25 接入).\n"
        "本 Material 是纯告警 (plan §九 '告警 ≠ 清理' 边界), 下游清理设施消费决定动作."
    ),
    parent="requirement",
    tags=["guardian", "hygiene", "report", "kind.sink"],
    examples=[
        {
            "project_root": "e:/WindowsWorkspace/omnicompany",
            "scan_ts": "2026-04-23T10:00:00Z",
            "violations": [
                {
                    "rule_id": "OMNI-047",
                    "severity": "LOW",
                    "path": "data/services/foo/scratch",
                    "message": "data/services/foo/scratch: 空目录. ...",
                },
                {
                    "rule_id": "OMNI-048a",
                    "severity": "MEDIUM",
                    "path": "src/foo/old_data.bak",
                    "message": "src/foo/old_data.bak: 临时文件残留 (硬模式命中)...",
                },
            ],
            "violation_count": 2,
            "by_rule": {"OMNI-047": 1, "OMNI-048a": 1},
            "candidates_for_judge": [
                {
                    "rule_id": "OMNI-048b",
                    "path": "src/foo/scratch_new_approach.py",
                    "severity": "LOW",
                    "message": "...气味像临时品, 待 GuardianAgent 复核...",
                    "pending_review": True,
                },
            ],
            "candidate_count": 1,
            "by_rule_pending": {"OMNI-048b": 1},
        },
    ],
)

GUARDIAN_SCAN_REQUEST = Format(
    id="guardian.scan_request",
    name="Guardian Patrol Scan Request",
    description=(
        "External request to run a Guardian patrol scan. The request carries "
        "scan_mode, project_root, n_commits, committed/uncommitted flags, "
        "use_agent, and the legacy auto_tow option. auto_tow is retained for "
        "API compatibility and does not imply an active audit/tow sink stage."
    ),
    parent="requirement",
    tags=["guardian", "domain.guardian", "stage.input", "kind.source"],
    examples=[
        {
            "scan_mode": "diff",
            "project_root": "e:/WindowsWorkspace/omnicompany",
            "committed": True,
            "uncommitted": True,
            "n_commits": 1,
            "use_agent": False,
            "auto_tow": True,
        }
    ],
)

GUARDIAN_FILE_CONTEXT_SET = Format(
    id="guardian.file_context_set",
    name="Guardian File Context Set",
    description=(
        "Files selected for rule evaluation. Produced by GitDiffScanWorker and "
        "consumed by RuleEngineWorker."
    ),
    parent="guardian.scan_request",
    tags=["guardian", "domain.guardian", "stage.scan", "kind.internal"],
    examples=[
        {
            "scan_ts": "2026-06-13T00:00:00Z",
            "scan_mode": "diff",
            "files": [
                {
                    "path": "src/omnicompany/example.py",
                    "abs_path": "e:/WindowsWorkspace/omnicompany/src/omnicompany/example.py",
                    "change_type": "M",
                    "content": "...",
                    "omnimark": None,
                }
            ],
        }
    ],
)

GUARDIAN_VIOLATION_SET = Format(
    id="guardian.violation_set",
    name="Guardian Rule Violation Set",
    description=(
        "RuleEngineWorker output containing confirmed, needs_judgment, and "
        "duplicate violation lists."
    ),
    parent="guardian.file_context_set",
    tags=["guardian", "domain.guardian", "stage.rule_engine", "kind.internal"],
    examples=[
        {
            "scan_ts": "2026-06-13T00:00:00Z",
            "scan_mode": "diff",
            "confirmed": [{"ticket_id": "TICKET-2026-06-13-001", "severity": "HIGH"}],
            "needs_judgment": [],
            "duplicates": [],
        }
    ],
)

GUARDIAN_VIOLATION_SET_JUDGED = Format(
    id="guardian.violation_set.judged",
    name="Guardian Reviewed Violation Set",
    description=(
        "Internal merged violation set after optional GuardianAgent review. "
        "The active patrol shim returns this data as part of the patrol result; "
        "there is no separate audit/tow sink worker in the active chain."
    ),
    parent="guardian.violation_set",
    tags=["guardian", "domain.guardian", "stage.judge", "kind.internal"],
    examples=[
        {
            "scan_ts": "2026-06-13T00:00:00Z",
            "scan_mode": "diff",
            "violations": [{"ticket_id": "TICKET-2026-06-13-001", "severity": "HIGH"}],
            "agent_reviewed": 0,
            "agent_confirmed": 0,
        }
    ],
)

GUARDIAN_PATROL_MATERIALS = [
    GUARDIAN_SCAN_REQUEST,
    GUARDIAN_FILE_CONTEXT_SET,
    GUARDIAN_VIOLATION_SET,
    GUARDIAN_VIOLATION_SET_JUDGED,
]

ALL_FORMATS = [
    *GUARDIAN_PATROL_MATERIALS,
    FORMAT_CHECK_REQUEST,
    FORMAT_FS_REPORT,
    FORMAT_ARCH_REPORT,
    FORMAT_NODE_REPORT,
    FORMAT_HEALTH_REPORT,
    FORMAT_HYGIENE_REQUEST,
    FORMAT_HYGIENE_REPORT,
    # 2026-04-21 C4: PatrolWorker LLM 精准巡查
    Format(
        id="guardian.patrol-request",
        name="Guardian Patrol Request",
        description=(
            "触发 Guardian LLM 精准巡查入口. 可选 services_root 字段指定 services/ "
            "根 (默认走 src/omnicompany/packages/services). PatrolWorker 会遍历其下 "
            "每个 service, 组装结构化上下文交 qwen3.6-plus 评估 Stage 3 真伪/DESIGN.md "
            "对齐度/目录卫生. 规则层 (OMNI-040/041/042) 负责仁慈快速, 本 Material 触发 "
            "LLM 精准, 两层互补."
        ),
        parent="requirement",
        tags=["guardian", "patrol", "input", "kind.source"],
        examples=[{}, {"services_root": "src/omnicompany/packages/services"}],
    ),
    Format(
        id="guardian.patrol-report",
        name="Guardian Patrol Report",
        description=(
            "PatrolWorker LLM 巡查产出. 含 patrolled_services (扫描 service 数量), "
            "report_path (Markdown 报告落盘路径 data/services/guardian/patrol/patrol-<ts>.md), "
            "report_md (Markdown 原文). 每个 service 三维标签: Stage3-OK|Stage2-Diamond|Skeleton|Hybrid "
            "× aligned|drift|stale × clean|dirty, 附 top 1-3 关键问题."
        ),
        parent="requirement",
        tags=["guardian", "patrol", "report", "kind.sink"],
        examples=[
            {
                "patrolled_services": 10,
                "report_path": "data/services/guardian/patrol/patrol-2026-04-21-203000.md",
                "report_md": "## lap_auditor (Stage3-OK / aligned / clean)\n...",
            }
        ],
    ),
    # 2026-04-28 · prompt 反模式 LLM 巡查 · OMNI-090/091/092
    Format(
        id="guardian.prompt-scan-request",
        name="Guardian Prompt Anti-Pattern Scan Request",
        description=(
            "触发 PromptAntiPatternScanWorker 巡查入口. 可选字段: "
            "scope (str · 扫描根, 默认 src/omnicompany/packages/services), "
            "rule_filter (list[str] · 限定 ['OMNI-090'] 等子集), "
            "force_rescan (bool · 绕 audit 缓存重判). 单命令触发, 走 MaterialDispatcher."
        ),
        parent="requirement",
        tags=["guardian", "prompt-scan", "input", "kind.source"],
        examples=[{}, {"scope": "src/omnicompany/packages/services/docauthor"},
                  {"rule_filter": ["OMNI-090"], "force_rescan": True}],
    ),
    Format(
        id="guardian.prompt-scan-report",
        name="Guardian Prompt Anti-Pattern Scan Report",
        description=(
            "PromptAntiPatternScanWorker 复核产出. 含 findings (按文件::prompt 归属 · "
            "rule_id ∈ {OMNI-090, OMNI-091, OMNI-092} · evidence + fix_hint + confidence), "
            "by_rule / by_verdict 计数, prompts_scanned / prompts_cached 区分新调 LLM 与缓存命中, "
            "audit_records_appended 落盘条数, report_path Markdown 报告路径 "
            "(data/services/guardian/prompt-scan/<ts>.md). audit_store 五元组缓存防重跑."
        ),
        parent="requirement",
        tags=["guardian", "prompt-scan", "report", "kind.sink"],
        examples=[
            {
                "findings": [
                    {
                        "file_path": "src/omnicompany/packages/services/foo/workers/bar.py",
                        "prompt_name": "_BAR_SYSTEM_PROMPT",
                        "lineno": 23,
                        "rule_id": "OMNI-091",
                        "severity": "MEDIUM",
                        "evidence": "若用户输入是A则...若是B则...若是C则...",
                        "fix_hint": "改写为: 给目标和约束让 LLM 自判, 不替它分类",
                        "confidence": 0.82,
                        "from_cache": False,
                    },
                ],
                "by_rule": {"OMNI-091": 1},
                "by_verdict": {"confirmed": 1, "dismissed": 8},
                "prompts_scanned": 3,
                "prompts_cached": 0,
                "prompts_total": 3,
                "audit_records_appended": 9,
                "scan_root": "e:/WindowsWorkspace/omnicompany/src/omnicompany/packages/services",
                "report_path": "data/services/guardian/prompt-scan/prompt-scan-2026-04-28T18-30-00.md",
                "report_md": "# Guardian Prompt Anti-Pattern Scan · 2026-04-28T18:30:00\n...",
            }
        ],
    ),
    # 2026-04-25 · 人类一手观察接口 · GuardianReportWorker 聚合 markdown
    Format(
        id="guardian.report-request",
        name="Guardian Report Request",
        description=(
            "触发 GuardianReportWorker 一手信息聚合 markdown 报告. 可选 with_llm_prose "
            "字段 (后续接 LLM 自然语言总结开篇). 单命令触发 · 非常驻 · 走 MaterialDispatcher."
        ),
        parent="requirement",
        tags=["guardian", "report", "input", "kind.source"],
        examples=[{}, {"with_llm_prose": False}],
    ),
    Format(
        id="guardian.report-output",
        name="Guardian Report Output",
        description=(
            "GuardianReportWorker 聚合 markdown 报告产出. 含 report_path (落盘到 "
            "data/services/guardian/reports/<ts>.md, 同时复制 latest.md), report_md "
            "(原文 markdown), source_counts (按数据源统计). 一手信息: 规则扫描 + LLM "
            "patrol + audit 判定 + docauthor 工作队列, **不二手转述**, 都给原始证据."
        ),
        parent="requirement",
        tags=["guardian", "report", "kind.sink"],
        examples=[
            {
                "report_path": "data/services/guardian/reports/report-2026-04-25-180000.md",
                "report_md": "# omnicompany 守护一手观察 · 2026-04-25T18:00Z\n\n...",
                "source_counts": {
                    "rule_scan_violations": 2,
                    "patrol_reports": 5,
                    "audit_records": 124,
                    "docauthor_quarantine": 2,
                    "docauthor_skeleton_design": 0,
                    "docauthor_missing_manifest": 0,
                },
            }
        ],
    ),
]


def register_formats(registry: FormatRegistry) -> None:
    """将所有 guardian Formats 注册到给定的 registry。"""
    for fmt in ALL_FORMATS:
        if not registry.is_registered(fmt.id):
            try:
                registry.register(fmt)
            except ValueError:
                pass  # parent 尚未注册（如 guardian.arch-report 依赖 guardian.fs-report）
