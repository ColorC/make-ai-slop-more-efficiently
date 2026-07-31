# [OMNI] origin=omnicompany domain=omnicompany/doctor ts=2026-04-20T00:00:00Z
# [OMNI] material_id="material:diagnosis.doctor.material_definitions.registry.py"
"""doctor.formats — Material 定义 (terminology §6 · Clean Migration 2026-04-20).

Material kind 标注 (F-19 硬规则):

Format subdomain (4):
  doctor.material.request            → kind.source    (外部触发, 无 producer Worker)
  doctor.material.extracted          → kind.internal
  doctor.material.acc                → kind.internal
  doctor.material.health-record      → kind.sink

Router subdomain (7):
  diag.worker.request              → kind.source
  diag.worker.extracted            → kind.internal
  diag.worker.sig-checked          → kind.internal
  diag.worker.context              → kind.internal
  diag.worker.det-checks           → kind.internal
  diag.worker.audit                → kind.internal
  diag.worker.health-record        → kind.sink

Pipeline subdomain (diag.team.*): 只在 pipeline.py TransformerSpec 引用,
未在本文件声明为 Material 对象 (待后续补齐)。
"""

from omnicompany.packages.services._core.omnicompany import Material as Format  # alias
from omnicompany.protocol.format import FormatRegistry


FORMAT_REQUEST = Format(
    id="doctor.material.request",
    name="Doctor Format Request",
    description="触发 Format 诊断管线的入口请求，指定待诊断的 Format ID 及可选的源码根目录路径",
    parent="requirement",
    tags=["doctor", "input", "service", "kind.source"],
    examples=[
        {"format_id": "guardian.check-request"},
        {
            "format_id": "guardian.fs-report",
            "source_root": "e:/WindowsWorkspace/omnicompany/src/omnicompany",
        },
    ],
)

FORMAT_EXTRACTED = Format(
    id="doctor.material.extracted",
    name="Doctor Format Extracted",
    description=(
        "FormatExtractorRouter 的 AST 静态分析结果。"
        "携带 Format 对象在源码中的定义文件、常量名，以及从 Format() 实例提取的所有字段"
        "（format_obj: id/name/description/examples/tags/parent）和 FORMAT_IN/OUT 引用清单（usages）。"
    ),
    parent="requirement",
    tags=["doctor", "extracted", "internal", "kind.internal"],
    examples=[
        {
            "format_id": "guardian.check-request",
            "source_root": "e:/WindowsWorkspace/omnicompany/src/omnicompany",
            "found": True,
            "defined_in": "omnicompany/packages/services/guardian/formats.py",
            "constant_name": "FORMAT_CHECK_REQUEST",
            "format_obj": {
                "id": "guardian.check-request",
                "name": "Guardian Check Request",
                "description": "触发守护检查管线的入口请求，携带待检查的项目根目录路径",
                "parent": "requirement",
                "tags": ["guardian", "input", "service"],
                "examples": [{"project_root": "e:/WindowsWorkspace/omnicompany"}],
            },
            "usages": [
                {
                    "file": "omnicompany/packages/services/guardian/routers.py",
                    "lineno": 45,
                    "role": "INPUT",
                    "line": "FORMAT_IN = guardian.check-request",
                }
            ],
        }
    ],
)

FORMAT_ACC = Format(
    id="doctor.material.acc",
    name="Doctor Format Accumulator",
    description=(
        "Format 诊断管线的累积中间状态。由 SignatureDiffRouter 初始化，后续每个检查节点追加一条 check 记录到 checks 列表。"
        "每条 check 含 check 名/passed/severity/detail/sub_checks 五字段。"
        "extracted 字段保存 FormatExtractorRouter 的完整输出，含 format_obj。"
    ),
    parent="requirement",
    tags=["doctor", "accumulator", "internal", "kind.internal"],
    examples=[
        {
            "format_id": "guardian.check-request",
            "source_root": "e:/WindowsWorkspace/omnicompany/src/omnicompany",
            "extracted": {
                "format_id": "guardian.check-request",
                "found": True,
                "constant_name": "FORMAT_CHECK_REQUEST",
                "format_obj": {
                    "id": "guardian.check-request",
                    "name": "Guardian Check Request",
                    "tags": ["guardian", "input", "service"],
                },
                "usages": [],
            },
            "sig_diff_ok": True,
            "checks": [
                {
                    "check": "sig_diff",
                    "passed": True,
                    "severity": "CRITICAL",
                    "detail": "Format 对象定义于 guardian/formats.py 常量 FORMAT_CHECK_REQUEST",
                },
                {
                    "check": "five_element",
                    "passed": True,
                    "severity": "HIGH",
                    "detail": "5/5 要素通过",
                    "sub_checks": [
                        {"name": "id 含域前缀", "passed": True, "detail": "OK"},
                    ],
                },
            ],
        }
    ],
)

FORMAT_HEALTH_RECORD = Format(
    id="doctor.material.health-record",
    name="Doctor Format Health Record",
    description=(
        "HealthWriterRouter 汇总输出的 Format 健康档案. "
        "**schema v2 (2026-04-25 契约变更 #02)**: 不打分, 保留完整语义信号. "
        "含 schema_version (2) · verdict (healthy/unhealthy/uncertain) · passed (bool · counts[critical]==0) · "
        "checks (全量 raw, 保留原始 CRITICAL/HIGH/.. 供审计) · failures_by_severity (critical/major/minor 归一) · "
        "counts (类别计数 · 非加权求和) · summary (非判词式). "
        "**不含 health_score, health_grade** — severity 4 档归一 3 档: CRITICAL→critical, HIGH→major, "
        "MEDIUM/LOW→minor, INFO→丢弃."
    ),
    parent="requirement",
    tags=["doctor", "report", "health", "kind.sink"],
    examples=[
        {
            "schema_version": 2,
            "format_id": "guardian.check-request",
            "source_root": "e:/WindowsWorkspace/omnicompany/src/omnicompany",
            "format_def": {
                "id": "guardian.check-request",
                "name": "Guardian Check Request",
                "description": "触发守护检查管线的入口请求，携带待检查的项目根目录路径",
                "parent": "requirement",
                "tags": ["guardian", "input", "service"],
                "examples": [{"project_root": "e:/WindowsWorkspace/omnicompany"}],
            },
            "checks": [
                {"check": "sig_diff", "passed": True, "severity": "CRITICAL", "detail": "Format 对象定义存在"},
                {"check": "five_element", "passed": True, "severity": "HIGH", "detail": "5/5 要素通过"},
                {"check": "tag_coverage", "passed": True, "severity": "MEDIUM", "detail": "3/3 规范通过"},
                {"check": "parent_chain", "passed": True, "severity": "HIGH", "detail": "INPUT 1 处，parent=requirement"},
                {"check": "example_presence", "passed": True, "severity": "MEDIUM", "detail": "示例存在（1 个）"},
                {"check": "desc_eval", "passed": True, "severity": "LOW", "detail": "描述清晰，提到用途和生产者"},
            ],
            "verdict": "healthy",
            "passed": True,
            "failures_by_severity": {"critical": [], "major": [], "minor": []},
            "counts": {"total_checks": 6, "passed_checks": 6,
                       "critical": 0, "major": 0, "minor": 0},
            "sig_diff_ok": True,
            "summary": "Format 'guardian.check-request' 6/6 checks passed, 无 failure",
        }
    ],
)


# ════════════════════════════════════════════════════════════════
# Worker 诊断管线 Materials
# ════════════════════════════════════════════════════════════════

RTR_FORMAT_REQUEST = Format(
    id="diag.worker.request",
    name="Router Diagnosis Request",
    description=(
        "触发 Router 诊断管线的入口请求。"
        "指定待诊断的 Router 类名、包含该类的源文件路径、以及 omnicompany 的 src 根目录。"
        "source_root 供后续节点做跨文件搜索（查找 FORMAT_IN/OUT 定义、上下游 Router、Pipeline 引用）。"
        "上游承诺: 无（管线入口）。"
        "下游用途: rtr_extractor 读取 router_class/source_file/source_root 定位 Router 类并执行 AST 提取。"
    ),
    parent="requirement",
    tags=["diag", "doctor", "worker", "input", "diagnosis", "kind.source"],
    examples=[
        {
            "router_class": "FormatExtractorRouter",
            "source_file": "e:/WindowsWorkspace/omnicompany/src/omnicompany/packages/services/doctor/routers.py",
            "source_root": "e:/WindowsWorkspace/omnicompany/src/omnicompany",
        },
    ],
)

RTR_FORMAT_EXTRACTED = Format(
    id="diag.worker.extracted",
    name="Router Diagnosis Extracted",
    description=(
        "RouterExtractorRouter 的 AST 静态分析结果。"
        "携带 Router 类的全部结构信息：类变量字面量（DESCRIPTION/FORMAT_IN/FORMAT_OUT/INPUT_KEYS/OUTPUT_KEYS/PASSTHROUGH）、"
        "__init__ 参数列表、run() 完整源码和行数，以及 7 类 AST 衍生信号。"
        "ast_signals 字段包含: router_kind('LLM'|'RULE'), llm_calls(调用位置列表), self_assignments(跨调用状态信号), "
        "input_keys_accessed(实际访问的 key), output_keys_produced(Verdict output 顶层 key), "
        "verdict_patterns(所有 return Verdict 的模式), exception_patterns(所有 except 块处理方式)。"
        "上游承诺: diag.worker.request 已提供合法路径。"
        "下游用途: rtr_signature 读 found/description/format_in/format_out 做存在性检查；后续节点通过 acc.extracted 访问所有信号。"
    ),
    parent="requirement",
    tags=["diag", "doctor", "worker", "extracted", "internal", "kind.internal"],
    examples=[
        {
            "router_class": "FormatExtractorRouter",
            "source_file": "e:/WindowsWorkspace/omnicompany/src/omnicompany/packages/services/doctor/routers.py",
            "source_root": "e:/WindowsWorkspace/omnicompany/src/omnicompany",
            "found": True,
            "description": "用 AST 从 formats.py 提取 Format 对象字段；扫描全部源码收集 FORMAT_IN/OUT 引用",
            "format_in": "doctor.material.request",
            "format_out": "doctor.material.extracted",
            "input_keys": ["format_id"],
            "output_keys": None,
            "passthrough": False,
            "init_params": ["source_root"],
            "run_source": "    def run(self, input_data: Any) -> Verdict:\n        format_id = input_data['format_id']\n        ...",
            "run_line_count": 52,
            "ast_signals": {
                "router_kind": "RULE",
                "llm_calls": [],
                "self_assignments": [],
                "input_keys_accessed": ["format_id", "source_root"],
                "output_keys_produced": ["format_id", "source_root", "found", "defined_in", "format_obj", "usages"],
                "verdict_patterns": [
                    {"kind": "PASS", "confidence": 1.0, "diagnosis": "FormatExtractor: xxx found=True usages=3", "granted_tags": []},
                    {"kind": "FAIL", "confidence": 1.0, "diagnosis": "FormatExtractor: source_root 不存在", "granted_tags": []},
                ],
                "exception_patterns": [],
            },
        }
    ],
)

# ── Worker 诊断管线: 4 段语义 Material (替代原 acc 累加器反模式) ──────────
# 数据流向：sig-checked → context → det-checks → audit → health-record
# 每段 Format 描述该阶段在前序基础上新增的字段；run() 逻辑仍以累积方式追加，
# Format 名称使阶段意图对 Doctor/Guardian 可见，消除 acc→acc→acc→acc 不透明性. Material 名称使阶段意图对 Doctor/Guardian 可见.

RTR_FORMAT_SIG_CHECKED = Format(
    id="diag.worker.sig-checked",
    name="Router Sig-Checked",
    description=(
        "rtr_signature 节点输出。Router 基础元数据校验完毕后的初始状态。"
        "顶层字段: router_class/source_file/source_root/"
        "extracted(AST提取结果: description/format_in/format_out/ast_signals/run_source/...)/sig_ok/checks=[签名检查]。"
        "FAIL 短路时 sig_ok=False，checks 仅含签名检查，后续节点跳过（EMIT→health_writer）。"
        "PASS 时 sig_ok=True，进入完整诊断链（context→det-checks→audit）。"
        "每条 check 记录: {check, standard, severity, passed:bool|null, observation, detail|null}。"
    ),
    parent="requirement",
    tags=["diag", "doctor", "worker", "sig-checked", "internal", "kind.internal"],
    examples=[
        {
            "router_class": "FormatExtractorRouter",
            "source_file": "e:/WindowsWorkspace/omnicompany/src/omnicompany/packages/services/doctor/routers.py",
            "source_root": "e:/WindowsWorkspace/omnicompany/src/omnicompany",
            "extracted": {
                "found": True,
                "description": "用 AST 从 formats.py 提取 Format 对象字段",
                "format_in": "doctor.material.request",
                "format_out": "doctor.material.extracted",
                "ast_signals": {"router_kind": "RULE", "llm_calls": [], "verdict_patterns": []},
            },
            "sig_ok": True,
            "checks": [
                {
                    "check": "signature",
                    "standard": "R-01/R-02 基础元数据存在性",
                    "severity": "CRITICAL",
                    "passed": True,
                    "observation": "DESCRIPTION 62 chars ✓; FORMAT_IN='doctor.material.request' ✓; FORMAT_OUT='doctor.material.extracted' ✓",
                    "detail": None,
                },
            ],
        }
    ],
)

RTR_FORMAT_CONTEXT = Format(
    id="diag.worker.context",
    name="Router Context",
    description=(
        "rtr_context_collector 节点输出。在 sig-checked 基础上追加 context 字段。"
        "新增字段: context={format_in_def/format_out_def（Format 对象 AST）/"
        "upstream_routers（FORMAT_OUT == 本 Router FORMAT_IN 的邻居）/"
        "downstream_routers（FORMAT_IN == 本 Router FORMAT_OUT 的邻居）/"
        "pipeline_refs（引用本 Router 的 pipeline.py 路径）/"
        "is_isolated（bool，未被任何 pipeline 引用）}。"
        "其余字段继承自 diag.worker.sig-checked。"
    ),
    parent="requirement",
    tags=["diag", "doctor", "worker", "context", "internal", "kind.internal"],
    examples=[
        {
            "router_class": "FormatExtractorRouter",
            "source_file": "e:/WindowsWorkspace/omnicompany/src/omnicompany/packages/services/doctor/routers.py",
            "extracted": {"found": True, "format_in": "doctor.material.request", "format_out": "doctor.material.extracted"},
            "sig_ok": True,
            "context": {
                "format_in_def": {"id": "doctor.material.request", "description": "触发 Format 诊断管线的入口请求"},
                "format_out_def": {"id": "doctor.material.extracted", "description": "AST 提取的 Format 对象字段"},
                "upstream_routers": [],
                "downstream_routers": [
                    {"class": "SignatureDiffRouter", "description": "校验 Format ID 是否以 Format() 对象形式定义"},
                ],
                "pipeline_briefs": [
                    {"pipeline_id": "doctor-format-diagnosis", "node_id": "format_extractor"},
                ],
                "context_gaps": [],
                "is_composite_format_in": False,
            },
        }
    ],
)

RTR_FORMAT_DET_CHECKS = Format(
    id="diag.worker.det-checks",
    name="Router Det-Checks",
    description=(
        "rtr_det_checker 节点输出。在 context 基础上追加确定性检查结果。"
        "checks 列表追加 R-01/R-02-list/R-02-fstring/R-04/R-04-async/"
        "R-05/R-06/R-10/R-11/R-12/R-13/R-17/R-18/R-07-signal 等检查记录。"
        "passed=null 表示信号类（R-07 分类）不计入评分，供 LLM 解读。"
        "其余字段继承自 diag.worker.context。"
    ),
    parent="requirement",
    tags=["diag", "doctor", "worker", "det-checks", "internal", "kind.internal"],
    examples=[
        {
            "router_class": "FormatExtractorRouter",
            "sig_ok": True,
            "checks": [
                {"check": "signature", "passed": True, "severity": "CRITICAL", "observation": "元数据完整"},
                {"check": "R-01", "passed": True, "severity": "HIGH", "observation": "DESCRIPTION 82 chars"},
                {"check": "R-04", "passed": True, "severity": "CRITICAL", "observation": "无直接 LLM import"},
                {"check": "R-05", "passed": True, "severity": "HIGH", "observation": "PASS ✓; FAIL ✓"},
                {"check": "R-10", "passed": True, "severity": "MEDIUM", "observation": "run() 52 行 ≤ 80"},
                {"check": "R-07-signal", "passed": None, "severity": "MEDIUM", "observation": "self.source_root = ... (INFO)"},
            ],
        }
    ],
)

RTR_FORMAT_AUDIT = Format(
    id="diag.worker.audit",
    name="Router Audit",
    description=(
        "rtr_contextual_audit 节点输出。在 det-checks 基础上追加 LLM 全语境审计结果。"
        "新增字段: audit_result={overall_grade(A/B/C/D)/findings/per_check_comments/"
        "improvement_suggestions}（LLM 审计）或 passed=null（hard 模式跳过）。"
        "新增字段: audit_path（git 存档路径，LLM 模式时）。"
        "其余字段继承自 diag.worker.det-checks。"
    ),
    parent="requirement",
    tags=["diag", "doctor", "worker", "audit", "internal", "kind.internal"],
    examples=[
        {
            "router_class": "FormatExtractorRouter",
            "checks": [
                {"check": "signature", "passed": True, "severity": "CRITICAL"},
                {
                    "check": "contextual_audit",
                    "passed": True,
                    "severity": "INFO",
                    "observation": "grade=A; a_info_sufficient=true; b_error_paths_complete=true",
                    "detail": {"overall_grade": "A", "key_findings": ["run() 边界处理完整"]},
                },
            ],
            "audit_path": "e:/WindowsWorkspace/omnicompany/data/doctor/audit/rtr_FormatExtractorRouter/abc1234.md",
        }
    ],
)

RTR_FORMAT_HEALTH_RECORD = Format(
    id="diag.worker.health-record",
    name="Router Diagnosis Health Record",
    description=(
        "RouterHealthWriterRouter 汇总输出的 Router 健康档案. "
        "**schema v2 (2026-04-25 契约变更 #02)**: 不打分, 保留完整语义信号. "
        "含 schema_version (2) · router_class/source_file/source_root · verdict (healthy/unhealthy/uncertain) · "
        "passed (bool) · checks (全量 raw) · failures_by_severity (critical/major/minor 归一) · counts · "
        "is_isolated · audit_path · summary. "
        "**不含 health_score, health_grade** (废 · severity 归一 3 档: CRITICAL→critical, HIGH→major, "
        "MEDIUM/LOW→minor, INFO→丢弃). "
        "上游承诺: diag.worker.audit 已含全部 checks 及 LLM 审计结果. "
        "下游用途: 写入 Registry 档案, 供批量扫描和 dashboard 展示."
    ),
    parent="requirement",
    tags=["diag", "doctor", "worker", "report", "health", "kind.sink"],
    examples=[
        {
            "schema_version": 2,
            "router_class": "FormatExtractorRouter",
            "source_file": "e:/WindowsWorkspace/omnicompany/src/omnicompany/packages/services/doctor/routers.py",
            "source_root": "e:/WindowsWorkspace/omnicompany/src/omnicompany",
            "verdict": "unhealthy",
            "passed": False,
            "is_isolated": False,
            "checks": [
                {"check": "signature", "passed": True, "severity": "CRITICAL", "observation": "基础元数据完整"},
                {"check": "R-10", "passed": False, "severity": "MEDIUM", "observation": "94 行 > 80 行"},
                {"check": "R-20-desc-quality", "passed": False, "severity": "CRITICAL", "observation": "DESCRIPTION 缺"},
            ],
            "failures_by_severity": {
                "critical": ["R-20-desc-quality: DESCRIPTION 缺"],
                "major": [],
                "minor": ["R-10: 94 行 > 80 行"],
            },
            "counts": {
                "total_checks": 3, "passed_checks": 1,
                "critical": 1, "major": 0, "minor": 1,
            },
            "audit_path": "e:/WindowsWorkspace/omnicompany/data/doctor/audit/rtr_FormatExtractorRouter/a5d1234.md",
            "summary": "Router 'FormatExtractorRouter' 3 checks, 1 critical + 1 minor failing",
        }
    ],
)


# ══════════════════════════════════════════════════════════════════════
# Blackboard 诊断子域 Material (7 条, New World Diagnostics Phase B · 2026-04-20)
# ══════════════════════════════════════════════════════════════════════
# 诊断对象: 某 Team 的订阅图 (Worker × Material 关系) 是否合规
# 共享输入: doctor.blackboard.audit_request (source)
# 各独立产出: 6 条 kind.sink 报告 (无 consumer, 供人读 / CI 消费)

BB_AUDIT_REQUEST = Format(
    id="doctor.blackboard.audit_request",
    name="BlackboardAuditRequest",
    description=(
        "对某 Team 的黑板订阅图做新世界诊断（kind 合法性 / FORMAT_IN_MODE / output 平铺 / "
        "孤儿 Worker / 未消费 Material / _emit_as_new_job 合规）的入口请求. "
        "指定 team_module_path (Python import path) 作为诊断目标, 本族 6 个 Worker 各独立产 sink report. "
        "下游用途: CI / 人审; 无后续 Worker 订阅 (本族 Worker 均订阅此 source). "
        "最小合法样例: {'team_module_path': 'omnicompany.packages.services.guardian'}. "
        "字段: team_module_path (str, 必填, 可 import 的 Team 包全路径) / severity_filter (str?, 可选 'HIGH' | 'MEDIUM' | 'LOW')."
    ),
    parent="requirement",
    tags=["doctor", "blackboard", "audit", "kind.source"],
    examples=[
        {"team_module_path": "omnicompany.packages.services.guardian"},
        {"team_module_path": "omnicompany.packages.services.selftest", "severity_filter": "HIGH"},
    ],
)

BB_KIND_LEGALITY_REPORT = Format(
    id="doctor.blackboard.kind_legality_report",
    name="BlackboardKindLegalityReport",
    description=(
        "Material kind 合法性诊断报告 (F-19/F-16). "
        "kind.source 有 producer Worker → 违规; kind.internal 无 producer 或无 consumer → 违规; "
        "kind.sink 有 consumer Worker → 违规. "
        "字段: team_module_path / findings[] (每条 {material_id, kind, violation, severity}) / scanned_count / violation_count. "
        "下游用途: 终端产物, 无 consumer Worker (sink). "
        "最小合法样例: {'team_module_path': '...', 'findings': [], 'scanned_count': 4, 'violation_count': 0}."
    ),
    parent="requirement",
    tags=["doctor", "blackboard", "kind", "kind.sink"],
    examples=[{"team_module_path": "omnicompany.packages.services.guardian", "findings": [], "scanned_count": 5, "violation_count": 0}],
)

BB_MODE_CHECK_REPORT = Format(
    id="doctor.blackboard.mode_check_report",
    name="BlackboardFormatInModeCheckReport",
    description=(
        "FORMAT_IN_MODE 显式声明检查报告 (R-24). "
        "Worker 的 FORMAT_IN 为 list[str] 时必须显式声明 FORMAT_IN_MODE = 'and' 或 'or', 缺则违规. "
        "字段: team_module_path / findings[] ({worker_class, format_in, mode_declared}) / scanned_count / violation_count. "
        "下游用途: 终端产物, 无 consumer (sink)."
    ),
    parent="requirement",
    tags=["doctor", "blackboard", "format-in-mode", "kind.sink"],
    examples=[{"team_module_path": "omnicompany.packages.services.selftest", "findings": [], "scanned_count": 4, "violation_count": 0}],
)

BB_OUTPUT_FLAT_REPORT = Format(
    id="doctor.blackboard.output_flat_report",
    name="BlackboardVerdictOutputFlatReport",
    description=(
        "Verdict.output 平铺检查报告 (R-23). "
        "扫 Worker.run 源码查找 `return Verdict(..., output={'<format_id>': ...})` 嵌套反模式. "
        "字段: team_module_path / findings[] ({worker_class, file, lineno, suspect_key}) / scanned_count / violation_count. "
        "下游用途: 终端产物, 无 consumer (sink)."
    ),
    parent="requirement",
    tags=["doctor", "blackboard", "output-flat", "kind.sink"],
    examples=[{"team_module_path": "omnicompany.packages.services.guardian", "findings": [], "scanned_count": 6, "violation_count": 0}],
)

BB_ORPHAN_WORKER_REPORT = Format(
    id="doctor.blackboard.orphan_worker_report",
    name="BlackboardOrphanWorkerReport",
    description=(
        "孤儿 Worker 诊断报告 (Q4). "
        "某 Worker 订阅了 FORMAT_IN 但该 Material 在 Team 内无 producer Worker 且其 kind 非 source → 孤儿. "
        "字段: team_module_path / findings[] ({worker_class, format_in, missing_producer_for}) / worker_count / orphan_count. "
        "下游用途: 终端产物, 无 consumer (sink)."
    ),
    parent="requirement",
    tags=["doctor", "blackboard", "orphan", "kind.sink"],
    examples=[{"team_module_path": "omnicompany.packages.services.selftest", "findings": [], "worker_count": 4, "orphan_count": 0}],
)

BB_UNCONSUMED_MATERIAL_REPORT = Format(
    id="doctor.blackboard.unconsumed_material_report",
    name="BlackboardUnconsumedMaterialReport",
    description=(
        "未消费 Material 诊断报告 (Q4). "
        "某 Material 被 Worker 产出 (FORMAT_OUT) 但 Team 内无 Worker 订阅且 kind 非 sink → 疑似冗余. "
        "字段: team_module_path / findings[] ({material_id, producer, kind}) / material_count / unconsumed_count. "
        "下游用途: 终端产物, 无 consumer (sink)."
    ),
    parent="requirement",
    tags=["doctor", "blackboard", "unconsumed", "kind.sink"],
    examples=[{"team_module_path": "omnicompany.packages.services.guardian", "findings": [], "material_count": 5, "unconsumed_count": 0}],
)

BB_EMIT_CHECK_REPORT = Format(
    id="doctor.blackboard.emit_check_report",
    name="BlackboardEmitAsNewJobCheckReport",
    description=(
        "_emit_as_new_job 子 job 发射合规检查报告 (R-25). "
        "Worker.run 源码出现 '_emit_as_new_job' 时, DESCRIPTION 或 docstring 必须解释 '发子 job' 的用途 (防滥用). "
        "字段: team_module_path / findings[] ({worker_class, file, lineno, reason_documented:bool}) / worker_count / violation_count. "
        "下游用途: 终端产物, 无 consumer (sink)."
    ),
    parent="requirement",
    tags=["doctor", "blackboard", "emit-new-job", "kind.sink"],
    examples=[{"team_module_path": "omnicompany.packages.services.omnicompany", "findings": [], "worker_count": 4, "violation_count": 0}],
)

# ══════════════════════════════════════════════════════════════════════
# 诊断重制初期 Material (2026-05-05) — 落 plan §5.6 通用层
#
# 用户 5 条铁律:
#   - 假设也是 material (不自创 yaml schema)
#   - 一切都是 material (finding / exemplar / 各诊断方法的 request/verdict)
#   - 规范诊断不抽取硬规则, 让 LLM 读规范文档判 → 走 ConfigurableAgent
#   - 后续很多自然语言判定 (description/evidence/notes/reasoning 都自然语言句子)
#
# V0 骨架 — 字段语义已定型, 但实例库为空, 没诊断 worker 真消费.
# 待: SpecDiagnosticAgent / HypothesisDiagnosticAgent / ExemplarDiagnosticAgent /
#     PlanDiagnosticAgent 立后才真激活.
# ══════════════════════════════════════════════════════════════════════

DIAG_HYPOTHESIS_STATEMENT = Format(
    id="doctor.hypothesis.statement",
    name="Doctor Diagnostic Hypothesis Statement",
    description=(
        "一条健康假设 — 自然语言句子表达 '应满足什么 + 为什么'. 由 plan / 规范 / 代码派生 "
        "(见 plan §5.3 假设派生子域). 假设作为 material 库的一条记录, 让诊断 agent "
        "拿它对照待诊断对象产 finding.\n\n"
        "用户铁律 (2026-05-05): 不打分不数字. 假设的'应当性'用 motivation 字段承载来龙去脉, "
        "不用 severity 枚举."
    ),
    parent="requirement",
    tags=["doctor", "hypothesis", "kind.source", "skeleton"],
    examples=[
        {
            "id": "H-2026-05-05-001",
            "source_kind": "spec",
            "source_path": "docs/standards/concepts/worker.md",
            "source_excerpt": "Worker 必有 FORMAT_OUT (R-01)",
            "statement": "任意 Worker 子类必须显式声明 FORMAT_OUT 类属性, 不得继承默认或留空",
            "motivation": (
                "Worker 跟 omnicompany 总线交互的契约就是 FORMAT_OUT — 没声明的话总线无法路由产出, "
                "Worker 等于断链状态. 这是 Worker 概念的本质必要条件, 不是品味偏好."
            ),
            "applies_to": "worker",
            "evidence_query": "看 worker class 体内有没 FORMAT_OUT = 赋值",
            "status": "active",
        },
    ],
)

DIAG_EXEMPLAR = Format(
    id="doctor.exemplar",
    name="Doctor Exemplar (Standard-of-Reference)",
    description=(
        "一份标杆样例 — 让诊断 agent 把待诊断对象跟这个比, 看差在哪. "
        "存在的目的不是规范 (规范在 standards), 而是 '已知合规且高质量' 的具象参考. "
        "见 plan §5.2 样例诊断子域."
    ),
    parent="requirement",
    tags=["doctor", "exemplar", "kind.source", "skeleton"],
    examples=[
        {
            "id": "E-worker-csv_reader-2026-05-05",
            "kind_of_entity": "worker",
            "exemplar_path": "src/omnicompany/packages/services/_utility/csv_to_md/workers/csv_reader.py",
            "qualified_reason": "HARD worker 典型, 18 项设计单完整, FORMAT_IN/OUT 清晰, 单一语义",
            "tags": ["hard", "transformer", "minimal"],
        },
    ],
)

DIAG_HEALTH_FINDING = Format(
    id="doctor.health_finding",
    name="Doctor Health Finding (Unified)",
    description=(
        "诊断结果统一格式 — 任何诊断 agent 产出的健康判定都用这格式. 走 SQLiteBus 落盘, "
        "registry HealthArchive 接收存档. 替代 4 子域分散的 health-record.\n\n"
        "用户铁律 (2026-05-05): 拒绝打分拥抱评论, 拒绝数字要来龙去脉. 不要 severity=critical/major/minor "
        "枚举, 不要 confidence 数字. 用 commentary (自然语言评论) + concern (来龙去脉, 解释为什么这是问题, "
        "影响什么, 出于什么考虑) 承载语义."
    ),
    parent="requirement",
    tags=["doctor", "finding", "kind.sink", "skeleton"],
    examples=[
        {
            "entity_id": "src/omnicompany/packages/services/_diagnosis/doctor/workers/format/format_extractor.py",
            "entity_kind": "worker",
            "finding_kind": "spec",
            "evidence": (
                "format_extractor.py 第 87 行的 Format 实例化里, description 字段写的是 "
                "'extract format from src'. 这只是说做什么, 没说为什么 / 何时用 / 输出对接哪条管线."
            ),
            "commentary": (
                "Worker 规范 worker.md 提到 description 应该让看到的人理解 Worker 的语义 (做什么+对接谁+边界), "
                "不只是动作动词. 这条 description 只满足'做什么', 不满足'对接谁/边界'. 现状能跑但下游 agent "
                "看着 description 选 Worker 时容易选错."
            ),
            "concern": (
                "如果不修, 下游 agent 把它误用到不该用的场景概率上升. 当前 doctor 只有这一处 Worker 用 "
                "format_extractor, 用错风险有限; 但当 doctor 成为通用诊断中心后, 误选成本会放大. "
                "改起来代价是 1 行注释扩充, 价值是阻断后续误选成本."
            ),
            "applied_standards": ["docs/standards/concepts/worker.md#R-01", "docs/standards/concepts/worker.md#R-04"],
            "applied_hypotheses": ["H-2026-05-05-001"],
            "ts": "2026-05-05T20:30:00Z",
            "commit_hash": "ceedbad",
            "agent_id": "SpecDiagnosticAgent",
        },
    ],
)

DIAG_SPEC_REQUEST = Format(
    id="doctor.spec_diagnosis.request",
    name="Doctor Spec-Driven Diagnosis Request",
    description=(
        "规范型诊断起点 — 触发 SpecDiagnosticAgent 拿 docs/standards/ 规范文档 + "
        "待诊断对象, 用 LLM 自然语言判合不合规. 不抽硬规则 (硬规则归 guardian)."
    ),
    parent="requirement",
    tags=["doctor", "spec", "kind.source", "skeleton"],
    examples=[
        {
            "target_entity_path": "src/omnicompany/packages/services/_diagnosis/doctor/workers/material/material_extractor.py",
            "target_entity_kind": "worker",
            "applicable_standards": ["docs/standards/concepts/worker.md"],
        },
    ],
)

DIAG_HYPOTHESIS_REQUEST = Format(
    id="doctor.hypothesis_diagnosis.request",
    name="Doctor Hypothesis-Driven Diagnosis Request",
    description=(
        "假设型诊断起点 — 触发 HypothesisDiagnosticAgent 拿一组假设 (data/services/doctor/hypotheses/ "
        "下的 yaml 实例) + 待诊断对象, 用 LLM 自然语言判对象违反/满足哪些假设. "
        "假设是 doctor.hypothesis.statement Material 实例, 比规范更细颗粒 (每条独立)."
    ),
    parent="requirement",
    tags=["doctor", "hypothesis", "kind.source", "skeleton"],
    examples=[
        {
            "target_entity_path": "src/omnicompany/packages/services/_diagnosis/doctor/workers/format/format_extractor.py",
            "target_entity_kind": "worker",
            "applicable_hypothesis_paths": [
                "docs/plans/diagnosis/[2026-05-05]DIAGNOSIS-RECONSOLIDATION/samples/sample_hypothesis_H-2026-05-05-001.yaml",
            ],
        },
    ],
)

DIAG_EXEMPLAR_REQUEST = Format(
    id="doctor.exemplar_diagnosis.request",
    name="Doctor Exemplar-Driven Diagnosis Request",
    description=(
        "样例型诊断起点 — 触发 ExemplarDiagnosticAgent 拿一组样例 (data/services/doctor/exemplars/ "
        "下的 yaml 实例, 或显式 path) + 待诊断对象, 用 LLM 自然语言判对象跟样例差在哪. "
        "样例不是规范, 是'已知合规且高质量'的具象参考. 比规范更具体, 帮 LLM 看到'这个面应该长什么样'."
    ),
    parent="requirement",
    tags=["doctor", "exemplar", "kind.source", "skeleton"],
    examples=[
        {
            "target_entity_path": "src/omnicompany/packages/services/_diagnosis/doctor/workers/format/format_extractor.py",
            "target_entity_kind": "worker",
            "applicable_exemplar_paths": [
                "docs/plans/diagnosis/[2026-05-05]DIAGNOSIS-RECONSOLIDATION/samples/sample_exemplar_E-worker-csv_reader-2026-05-05.yaml",
            ],
        },
    ],
)

DIAG_EXEMPLAR_VERDICT = Format(
    id="doctor.exemplar_diagnosis.verdict",
    name="Doctor Exemplar-Driven Diagnosis Verdict",
    description=(
        "样例型诊断输出 — 结构跟 doctor.spec_diagnosis.verdict / hypothesis_diagnosis.verdict 同 "
        "(共用 submit_verdict 工具). 区别在 finding.applied_exemplars 字段含具体样例 id, finding_kind=exemplar. "
        "narrative 大局观察应聚焦 '差在哪' 跟 '能不能学到', 不是合规判定."
    ),
    parent="requirement",
    tags=["doctor", "exemplar", "kind.internal", "skeleton"],
    examples=[
        {
            "target_entity_path": "src/omnicompany/.../format_extractor.py",
            "target_entity_kind": "worker",
            "findings": ["<list[doctor.health_finding] with finding_kind=exemplar>"],
            "narrative": (
                "本次拿 csv_reader 作样例 (HARD worker 标杆) 看 format_extractor.py. 主要差异: "
                "csv_reader 边界处理 (文件不存在 / 编码不匹配 / 空 csv 等) 都显式 Verdict 返回, "
                "format_extractor 在 source_root 不存在等同类边界看上去也覆盖了, 学到了样例的边界处理思路."
            ),
            "consulted_references": [
                "docs/plans/.../sample_exemplar_E-worker-csv_reader-2026-05-05.yaml",
            ],
            "agent_id": "ExemplarDiagnosticAgent",
        },
    ],
)


DIAG_PLAN_REQUEST = Format(
    id="doctor.plan_diagnosis.request",
    name="Doctor Plan-Driven Diagnosis Request",
    description=(
        "计划型诊断起点 — 触发 PlanDiagnosticAgent 拿 docs/plans/<topic>/[date]<plan>/plan.md "
        "+ docs/standards/protocol/plan_template.md, 用 LLM 自然语言判 plan.md 是否按模板结构 "
        "+ 产物清单的 path 是否真实存在 (静态) + 验收标准能否复现 (动态, V1 后接). "
        "不修复, 只产 finding 提示. 不达标项按 plan.md 五节'不达标处置'走技术债 vs 阻断."
    ),
    parent="requirement",
    tags=["doctor", "plan", "kind.source", "skeleton"],
    examples=[
        {
            "target_plan_path": "docs/plans/diagnosis/[2026-05-05]DIAGNOSIS-RECONSOLIDATION/plan.md",
            "applicable_template_paths": ["docs/standards/protocol/plan_template.md"],
            "check_modes": ["static"],
        },
    ],
)

DIAG_PLAN_VERDICT = Format(
    id="doctor.plan_diagnosis.verdict",
    name="Doctor Plan-Driven Diagnosis Verdict",
    description=(
        "计划型诊断输出 — 结构跟其他三种 verdict 同 (共用 submit_verdict). "
        "区别在 finding.applied_standards 里含 plan_template path:节, finding_kind=plan. "
        "narrative 总结 plan 完成度 (产物存在性 + 结构合规 + 动态验收) 跟未达标项的处置建议."
    ),
    parent="requirement",
    tags=["doctor", "plan", "kind.internal", "skeleton"],
    examples=[
        {
            "target_plan_path": "docs/plans/.../plan.md",
            "target_entity_kind": "plan",
            "findings": ["<list[doctor.health_finding] with finding_kind=plan>"],
            "narrative": (
                "本次拿 plan_template 看 [2026-05-05]DIAGNOSIS-RECONSOLIDATION plan.md. "
                "整体合规度高: 一-八节齐, OMNI 头齐, 产物清单 12 条全部 path 存在. "
                "差异点: '验收标准' 节没分静态/动态, 只列了一段总验收. 模板要求分子节, "
                "建议补 3.1/3.2 子节. 不阻断, 走 advisory."
            ),
            "consulted_references": [
                "docs/standards/protocol/plan_template.md",
                "docs/plans/.../plan.md",
            ],
            "agent_id": "PlanDiagnosticAgent",
        },
    ],
)


DIAG_META_REQUEST = Format(
    id="doctor.meta_diagnosis.request",
    name="Doctor Meta-Diagnosis Request",
    description=(
        "元诊断起点 — 触发 MetaDiagnosticAgent 看 team 整体健康. "
        "走用户 5/6 立的 10 问 + 7 假设. 跟 4 个对象级诊断 agent 不同 — 看 team 整体不看单一对象."
    ),
    parent="requirement",
    tags=["doctor", "meta-diagnosis", "kind.source", "skeleton"],
    examples=[{
        "team_path": "src/omnicompany/packages/services/_utility/csv_to_md/",
        "focus_questions": [1, 3, 5, 8, 10],
        "depth": "full",
    }],
)

DIAG_META_VERDICT = Format(
    id="doctor.meta_diagnosis.verdict",
    name="Doctor Meta-Diagnosis Verdict",
    description=(
        "元诊断输出 — 含 10 问回答 + 推荐验证设施清单. 跟 4 诊断 agent verdict 同共用 submit_verdict 工具. "
        "区别在 narrative 应含'团队整体健康总结 + 推荐设施'."
    ),
    parent="requirement",
    tags=["doctor", "meta-diagnosis", "kind.internal", "skeleton"],
    examples=[{
        "team_path": "src/omnicompany/.../csv_to_md/",
        "target_entity_kind": "team",
        "findings": ["<list[doctor.health_finding]>"],
        "narrative": "csv_to_md team 整体健康度高 ...",
        "consulted_references": ["docs/plans/.../anti_patterns/archetypes.yaml", "..."],
        "agent_id": "MetaDiagnosticAgent",
    }],
)


DIAG_HYPOTHESIS_DERIVATION_REQUEST = Format(
    id="doctor.hypothesis_derivation.request",
    name="Doctor Hypothesis Derivation Request",
    description=(
        "假设派生起点 — 触发 HypothesisDeriverAgent 拿一组源 (规范文档 / plan / 代码) "
        "派生新假设入库 (`data/services/doctor/hypotheses/<id>.yaml`). "
        "派生 agent 的存在解决 '假设从哪来' 瓶颈 — 不靠人手撑库, 通过 LLM 阅读规范/计划/代码自动派生."
    ),
    parent="requirement",
    tags=["doctor", "hypothesis-derivation", "kind.source", "skeleton"],
    examples=[
        {
            "source_paths": [
                "docs/standards/concepts/worker.md",
            ],
            "derivation_focus": "worker",
            "max_hypotheses": 5,
        },
    ],
)

DIAG_HYPOTHESIS_DERIVATION_REPORT = Format(
    id="doctor.hypothesis_derivation.report",
    name="Doctor Hypothesis Derivation Report",
    description=(
        "假设派生输出 — 不是 verdict 形态, 是派生过程报告. "
        "含 source_paths (派生时考虑了哪些源) + derived_hypothesis_ids (产了哪些假设 id) "
        "+ narrative (派生策略 / hard rule 候选 vs 软语义判定). "
        "实际假设实例 yaml 走 write_hypothesis 工具落 hypothesis 库, 不在 report 内嵌."
    ),
    parent="requirement",
    tags=["doctor", "hypothesis-derivation", "kind.internal", "skeleton"],
    examples=[
        {
            "source_paths": ["docs/standards/concepts/worker.md"],
            "derived_hypothesis_ids": ["H-2026-05-06-001", "H-2026-05-06-002", "H-2026-05-06-003"],
            "narrative": (
                "本次拿 worker.md 派生 worker 类假设 3 条. 策略: 找规范里'必须/应/不得'类硬性表述, "
                "每条独立成假设. 其中 H-2026-05-06-001 (FORMAT_OUT 必声明) 是 hard rule 候选 "
                "(ast 解析能查), 已在现 sample_hypothesis_H-2026-05-05-001 重叠 — 跳过重复. "
                "另 2 条 (DESCRIPTION 具象 + Verdict diagnosis 写明) 是软语义, 留给 doctor."
            ),
            "agent_id": "HypothesisDeriverAgent",
        },
    ],
)


DIAG_HYPOTHESIS_VERDICT = Format(
    id="doctor.hypothesis_diagnosis.verdict",
    name="Doctor Hypothesis-Driven Diagnosis Verdict",
    description=(
        "假设型诊断输出 — 结构跟 doctor.spec_diagnosis.verdict 同 (共用 submit_verdict 工具). "
        "区别在 finding.applied_hypotheses 字段含具体假设 id, finding_kind=hypothesis."
    ),
    parent="requirement",
    tags=["doctor", "hypothesis", "kind.internal", "skeleton"],
    examples=[
        {
            "target_entity_path": "src/omnicompany/.../format_extractor.py",
            "target_entity_kind": "worker",
            "findings": ["<list[doctor.health_finding] with finding_kind=hypothesis>"],
            "narrative": (
                "本次根据 1 条假设 (H-2026-05-05-001 'Worker 必有 FORMAT_OUT 显式声明') 看 format_extractor.py. "
                "对象本身满足该假设 (FORMAT_OUT 已声明), 但发现一处副作用值得记: 类没有 docstring 解释 FORMAT_OUT 的语义."
            ),
            "consulted_references": [
                "docs/plans/.../sample_hypothesis_H-2026-05-05-001.yaml",
            ],
            "agent_id": "HypothesisDiagnosticAgent",
        },
    ],
)


DIAG_SPEC_VERDICT = Format(
    id="doctor.spec_diagnosis.verdict",
    name="Doctor Spec-Driven Diagnosis Verdict",
    description=(
        "规范型诊断输出 — 含一组 finding (走 doctor.health_finding 格式) + agent 自然语言整体评论 "
        "(narrative). 下游接 health_finding 走 sink 落 registry. agent 必须通过 submit_verdict 工具产, "
        "schema 校验通过才合法 (堵不如疏: 出口检查替代 prompt 强迫)."
    ),
    parent="requirement",
    tags=["doctor", "spec", "kind.internal", "skeleton"],
    examples=[
        {
            "target_entity_path": "src/omnicompany/.../material_extractor.py",
            "target_entity_kind": "worker",
            "findings": ["<list[doctor.health_finding]>"],
            "narrative": (
                "整体看, 这个 Worker 的代码骨架按 worker 规范来的, 元数据齐. "
                "主要观察落在 description 字段不够具体 (单一 Worker 看勉强够, 当 doctor 通用诊断中心铺开后会成本放大), "
                "跟 confidence 字段缺 (确定性 Worker 应显式 1.0, 让下游路由判定可读). "
                "都不是阻断性问题, 但越早改代价越低."
            ),
            "consulted_references": ["docs/standards/concepts/worker.md"],
            "agent_id": "SpecDiagnosticAgent",
        },
    ],
)


ALL_FORMATS = [
    FORMAT_REQUEST,
    FORMAT_EXTRACTED,
    FORMAT_ACC,
    FORMAT_HEALTH_RECORD,
    RTR_FORMAT_REQUEST,
    RTR_FORMAT_EXTRACTED,
    RTR_FORMAT_SIG_CHECKED,
    RTR_FORMAT_CONTEXT,
    RTR_FORMAT_DET_CHECKS,
    RTR_FORMAT_AUDIT,
    RTR_FORMAT_HEALTH_RECORD,
    # Blackboard 子域 (Phase B · 2026-04-20)
    BB_AUDIT_REQUEST,
    BB_KIND_LEGALITY_REPORT,
    BB_MODE_CHECK_REPORT,
    BB_OUTPUT_FLAT_REPORT,
    BB_ORPHAN_WORKER_REPORT,
    BB_UNCONSUMED_MATERIAL_REPORT,
    BB_EMIT_CHECK_REPORT,
    # 诊断重制初期 (2026-05-05) — V0 骨架
    DIAG_HYPOTHESIS_STATEMENT,
    DIAG_EXEMPLAR,
    DIAG_HEALTH_FINDING,
    DIAG_SPEC_REQUEST,
    DIAG_SPEC_VERDICT,
    DIAG_HYPOTHESIS_REQUEST,
    DIAG_HYPOTHESIS_VERDICT,
    DIAG_EXEMPLAR_REQUEST,
    DIAG_EXEMPLAR_VERDICT,
    DIAG_PLAN_REQUEST,
    DIAG_PLAN_VERDICT,
    DIAG_HYPOTHESIS_DERIVATION_REQUEST,
    DIAG_HYPOTHESIS_DERIVATION_REPORT,
    DIAG_META_REQUEST,
    DIAG_META_VERDICT,
]


def register_formats(registry: FormatRegistry) -> None:
    """将所有 doctor Formats 注册到给定的 registry。"""
    for fmt in ALL_FORMATS:
        if not registry.is_registered(fmt.id):
            try:
                registry.register(fmt)
            except ValueError:
                pass
