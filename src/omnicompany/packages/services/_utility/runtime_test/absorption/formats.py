# [OMNI] origin=claude-code domain=services/absorption_runtime_test/formats ts=2026-04-27T00:00:00Z type=config
# [OMNI] material_id="material:utility.runtime_test.absorption.material_definitions.config.py"
"""absorption_runtime_test Team · Material 定义.

7 个 Material 全遵守 `feedback_semantic_sentences_not_classification`:
- 语义判定字段必须自然语言句子
- 禁 score / level / tier / tags / kind 离散标签
- 仅允许物理度量 (count / pct / 时间) 与协议层硬枚举

2026-04-27 改名 (旧: knowledge_runtime_test) + 删 independent_reeval_evidence (路 2).
"""
from __future__ import annotations

from omnicompany.packages.services._core.omnicompany import Material
from omnicompany.protocol.format import FormatRegistry


# ── 1. target_spec (源) ────────────────────────────────────────

M_TARGET_SPEC = Material(
    id="absorption_runtime_test.target_spec",
    name="absorption_runtime_test.target_spec",
    description=(
        "absorption_runtime_test 入口配置. 由外部触发. 描述要测哪个目标团队 / 给它什么 sample input / "
        "跑几次取样 / 抽几条提案做落地验证.\n\n"
        "字段含义: target_team_id (str): absorption 类目标的注册 id；当前无默认样例。sample_input (dict): 给目标团队真跑用的 input_data. "
        "run_count (int, ≥2): 跨次稳定性需要至少 2 次取样, 一般 2-3. spot_impl_count (int, ≥1): 抽样落地的提案数, 一般 1-2 节省 token.\n\n"
        "外部承诺: target_team_id 在 PipelineRegistry 已注册. sample_input 可被目标团队消费 (类型与目标 FORMAT_IN 兼容).\n\n"
        "TargetIngressWorker 消费: 校 target_team_id 存在性, 透传 sample_input/run_count 给下游."
    ),
    parent="doc",
    json_schema={
        "type": "object",
        "properties": {
            "target_team_id": {"type": "string", "minLength": 1},
            "sample_input": {"type": "object", "description": "给目标团队真跑的 input_data"},
            "run_count": {"type": "integer", "minimum": 2, "default": 2},
            "spot_impl_count": {"type": "integer", "minimum": 1, "default": 2},
        },
        "required": ["target_team_id", "sample_input"],
    },
    tags=["absorption_runtime_test", "kind.source"],
)


# ── 2. target_metadata (装入后) ───────────────────────────────

M_TARGET_METADATA = Material(
    id="absorption_runtime_test.target_metadata",
    name="absorption_runtime_test.target_metadata",
    description=(
        "TargetIngressWorker 装入的目标团队元数据. 含目标包代码路径 + 透传 sample_input/run_count/spot_impl_count.\n\n"
        "字段: target_team_id (str), team_code_dir (str), sample_input (dict), run_count (int), spot_impl_count (int).\n\n"
        "下游所有 worker 用 team_code_dir 作 ReadFile 根, 用 sample_input 调 SampleRunsExecutor 真跑."
    ),
    parent="doc",
    json_schema={
        "type": "object",
        "properties": {
            "target_team_id": {"type": "string"},
            "team_code_dir": {"type": "string", "description": "目标团队包目录绝对路径"},
            "sample_input": {"type": "object"},
            "run_count": {"type": "integer", "minimum": 2},
            "spot_impl_count": {"type": "integer", "minimum": 1},
        },
        "required": [
            "target_team_id",
            "team_code_dir",
            "sample_input",
            "run_count",
        ],
    },
    tags=["absorption_runtime_test", "kind.internal"],
)


# ── 3. sample_runs (取样后) ───────────────────────────────────

M_SAMPLE_RUNS = Material(
    id="absorption_runtime_test.sample_runs",
    name="absorption_runtime_test.sample_runs",
    description=(
        "SampleRunsExecutor 真跑目标团队 N 次后的产物清单. 每条含 verdict + output (目标产物原样) + elapsed_sec.\n\n"
        "字段: target_team_id (str), runs (list[obj]) 每条 {run_id: int, verdict: str, output: dict, elapsed_sec: number}, "
        "successful_count (int 物理度量), total_count (int).\n\n"
        "SampleRunsExecutor 承诺: runs 数组长度 = total_count = run_count (不论成功/失败都收齐). 每条 output 是目标真产物 dict 不裁剪. "
        "失败的跑 (verdict=FAIL) 也保留, 验证器据此可判稳定性.\n\n"
        "下游 3 验证器读 runs 数组取样比对."
    ),
    parent="doc",
    json_schema={
        "type": "object",
        "properties": {
            "target_team_id": {"type": "string"},
            "runs": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "run_id": {"type": "integer"},
                        "verdict": {"type": "string"},
                        "output": {"type": "object"},
                        "elapsed_sec": {"type": "number"},
                    },
                    "required": ["run_id", "verdict"],
                },
            },
            "successful_count": {"type": "integer", "minimum": 0},
            "total_count": {"type": "integer", "minimum": 1},
        },
        "required": ["target_team_id", "runs", "successful_count", "total_count"],
    },
    tags=["absorption_runtime_test", "kind.internal"],
)


# ── 4. cross_run_evidence (路 1) ──────────────────────────────

M_CROSS_RUN_EVIDENCE = Material(
    id="absorption_runtime_test.cross_run_evidence",
    name="absorption_runtime_test.cross_run_evidence",
    description=(
        "路 1 跨次稳定性证据. 比较 N 次跑出产物 (proposals 列表) 在两个层级的重叠.\n\n"
        "字段:\n"
        "- file_overlap_pct (number, 物理度量): 跨次涉及文件 set 重叠率 0-1\n"
        "- topic_overlap_pct (number): 跨次主题 LLM 判重叠率 0-1\n"
        "- file_intersection (list[str]): 共同涉及的文件\n"
        "- file_union_size (int): 总涉及文件数\n"
        "- stability_observation (str, 句子, ≥30 字符): 自然语言描述这次稳定性如何 (避免分类标签)\n"
        "- divergence_signals (list[str], 句子): 不稳定的信号 (e.g. '主题层 80% 但文件层只 25%, 可能假稳定')\n\n"
        "CrossRunStabilityVerifier 承诺: file_overlap_pct 由真集合计算, topic_overlap_pct 由 LLM 判. "
        "stability_observation 是综合判断的句子, 不是分类码."
    ),
    parent="doc",
    json_schema={
        "type": "object",
        "properties": {
            "file_overlap_pct": {"type": "number", "minimum": 0, "maximum": 1},
            "topic_overlap_pct": {"type": "number", "minimum": 0, "maximum": 1},
            "file_intersection": {"type": "array", "items": {"type": "string"}},
            "file_union_size": {"type": "integer", "minimum": 0},
            "stability_observation": {"type": "string", "minLength": 30},
            "divergence_signals": {
                "type": "array",
                "items": {"type": "string", "minLength": 10},
            },
        },
        "required": [
            "file_overlap_pct",
            "topic_overlap_pct",
            "stability_observation",
        ],
    },
    tags=["absorption_runtime_test", "kind.internal"],
)


# ── 5. spot_impl_evidence (路 3 · absorption 特化) ────────────

M_SPOT_IMPL_EVIDENCE = Material(
    id="absorption_runtime_test.spot_impl_evidence",
    name="absorption_runtime_test.spot_impl_evidence",
    description=(
        "路 3 抽样落地证据. 挑 spot_impl_count 条提案, 让另一 LLM 读完整源码 + 写实施代码, 然后用第二轮 LLM 判是否真解决 problem.\n\n"
        "absorption 特化: 仅适用代码改进提案类工作. 套故事/配表/UI 等非可实施产物错.\n\n"
        "字段:\n"
        "- attempts (list[obj]): 每条 {proposal_id, title, implementable (bool), reason_if_not (str), implementation_excerpt (str), truly_solves (bool), judge_reason (str)}\n"
        "- implementable_pct (number): 多少比例提案 LLM 写得出实施代码\n"
        "- truly_solves_pct (number): 多少比例真解决 problem\n"
        "- combined_pct (number): (impl + solves) / 2\n"
        "- groundedness_observation (str, 句子): 自然语言描述提案的具体性 (是空泛多还是具体多)\n\n"
        "SpotImplVerifier 承诺: 真跑 LLM 让它写代码 (非模拟). truly_solves 由独立 LLM 判 (避免自验)."
    ),
    parent="doc",
    json_schema={
        "type": "object",
        "properties": {
            "attempts": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "proposal_id": {"type": "string"},
                        "title": {"type": "string"},
                        "implementable": {"type": "boolean"},
                        "reason_if_not": {"type": "string"},
                        "implementation_excerpt": {"type": "string"},
                        "truly_solves": {"type": "boolean"},
                        "judge_reason": {"type": "string"},
                    },
                    "required": ["proposal_id", "implementable", "truly_solves"],
                },
            },
            "implementable_pct": {"type": "number", "minimum": 0, "maximum": 1},
            "truly_solves_pct": {"type": "number", "minimum": 0, "maximum": 1},
            "combined_pct": {"type": "number", "minimum": 0, "maximum": 1},
            "groundedness_observation": {"type": "string", "minLength": 30},
        },
        "required": [
            "attempts",
            "implementable_pct",
            "truly_solves_pct",
            "groundedness_observation",
        ],
    },
    tags=["absorption_runtime_test", "kind.internal"],
)


# ── 6. source_coverage_evidence (路 4 · absorbing 特化) ────────────

M_SOURCE_COVERAGE_EVIDENCE = Material(
    id="absorption_runtime_test.source_coverage_evidence",
    name="absorption_runtime_test.source_coverage_evidence",
    description=(
        "路 4 源覆盖证据. 扫目标输入源仓库 (从 sample_input 取 repo_path), 用引用数 + 文件大小机械排名 top-K 候选, "
        "LLM 在候选里选语义关键模块 (减少 LLM 自评成分), 检查目标团队提案有没碰它们.\n\n"
        "absorbing 特化: 仅适用 target 消费 repo_path 类源仓库. 不消费时设 applicable=false 不参与综合.\n\n"
        "字段:\n"
        "- key_modules_identified (list[obj]): 关键模块清单 · 每条 {file, importance_reason, ranked_metrics}\n"
        "  - ranked_metrics (obj, 程序化): {referenced_by_count, loc, rank}\n"
        "- key_modules_total (int): 数\n"
        "- key_modules_touched_by_target (list[str]): 目标团队碰过的关键模块\n"
        "- key_modules_missed_by_target (list[str]): 目标团队漏的关键模块\n"
        "- coverage_pct (number): touched / total\n"
        "- coverage_observation (str, 句子): 自然语言描述覆盖情况\n"
        "- candidate_pool_size (int): 程序化排名后给 LLM 的候选池大小 (top-K 默认 30)\n\n"
        "SourceCoverageVerifier 承诺: 候选池由程序化排名 (引用数 + LOC) 产, LLM 仅在候选里选."
    ),
    parent="doc",
    json_schema={
        "type": "object",
        "properties": {
            "applicable": {"type": "boolean", "default": True},
            "key_modules_identified": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "file": {"type": "string"},
                        "importance_reason": {"type": "string"},
                        "ranked_metrics": {"type": "object"},
                    },
                    "required": ["file", "importance_reason"],
                },
            },
            "key_modules_total": {"type": "integer", "minimum": 0},
            "key_modules_touched_by_target": {
                "type": "array",
                "items": {"type": "string"},
            },
            "key_modules_missed_by_target": {
                "type": "array",
                "items": {"type": "string"},
            },
            "coverage_pct": {"type": "number", "minimum": 0, "maximum": 1},
            "coverage_observation": {"type": "string", "minLength": 30},
            "candidate_pool_size": {"type": "integer", "minimum": 0},
        },
        "required": ["applicable", "coverage_observation"],
    },
    tags=["absorption_runtime_test", "kind.internal"],
)


# ── 7. portrait (sink) ────────────────────────────────────────

M_PORTRAIT = Material(
    id="absorption_runtime_test.portrait",
    name="absorption_runtime_test.portrait",
    description=(
        "终态画像. PortraitAssembler 装配 3 条路证据 + 自然语言段落 + 做得好/漏掉两段句子列表.\n\n"
        "**反模式禁令** (按 feedback_semantic_sentences_not_classification): 全字段除物理度量外是自然语言句子. "
        "禁 score / quality_grade / risk_level / tags 等 vibe 字段.\n\n"
        "字段:\n"
        "- verdict: PASS / PARTIAL / FAIL (协议层硬枚举)\n"
        "- target_team_id: 透传\n"
        "- evidence_paths: {cross_run, spot_impl, source_coverage} 3 条路证据原样镜像 (供溯源)\n"
        "- portrait_paragraph (str, ≥150 字): 总体画像段落 自然语言\n"
        "- what_target_does_well (list[str]): 它做得好的方面 句子列表\n"
        "- what_target_misses (list[str]): 它漏掉的方面 句子列表 (核心)\n"
        "- physical_metrics: 仅物理度量 {run_count, file_overlap_pct, topic_overlap_pct, impl_combined_pct, coverage_pct}\n"
        "- run_id (str): 本次跑唯一 id (供未来 ledger 累积)\n"
        "- markdown_report (str): 完整 markdown 文档 (主输出, 给人直接读). 上面字段是它的索引视图\n\n"
        "verdict 派生规则: 3/3 路达标 → PASS; 2/3 → PARTIAL; <2 → FAIL.\n"
        "(具体阈值在 PortraitAssembler 实现里; 协议层只规约枚举值.)"
    ),
    parent="requirement",
    json_schema={
        "type": "object",
        "properties": {
            "verdict": {"type": "string", "enum": ["PASS", "PARTIAL", "FAIL"]},
            "target_team_id": {"type": "string"},
            "evidence_paths": {
                "type": "object",
                "properties": {
                    "cross_run": {"type": "object"},
                    "spot_impl": {"type": "object"},
                    "source_coverage": {"type": "object"},
                },
            },
            "portrait_paragraph": {"type": "string", "minLength": 150},
            "what_target_does_well": {
                "type": "array",
                "items": {"type": "string", "minLength": 10},
            },
            "what_target_misses": {
                "type": "array",
                "items": {"type": "string", "minLength": 10},
            },
            "physical_metrics": {"type": "object"},
            "run_id": {"type": "string"},
            "markdown_report": {"type": "string", "minLength": 200},
        },
        "required": [
            "verdict",
            "target_team_id",
            "evidence_paths",
            "portrait_paragraph",
            "what_target_does_well",
            "what_target_misses",
            "markdown_report",
        ],
    },
    tags=["absorption_runtime_test", "kind.sink"],
)


ALL_MATERIALS = [
    M_TARGET_SPEC,
    M_TARGET_METADATA,
    M_SAMPLE_RUNS,
    M_CROSS_RUN_EVIDENCE,
    M_SPOT_IMPL_EVIDENCE,
    M_SOURCE_COVERAGE_EVIDENCE,
    M_PORTRAIT,
]


def register_formats(registry: FormatRegistry) -> None:
    """注册 absorption_runtime_test 所有 Material."""
    for mat in ALL_MATERIALS:
        if not registry.is_registered(mat.id):
            try:
                registry.register(mat)
            except Exception:
                pass
