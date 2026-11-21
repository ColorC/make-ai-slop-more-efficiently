# [OMNI] origin=claude-code domain=services/code_runtime_test/formats ts=2026-04-26T00:00:00Z type=config
# [OMNI] material_id="material:utility.runtime_test.code.material_definitions.config.py"
"""code_runtime_test Team · Material 定义.

代码产物有 ground truth · byte-diff 等量化梯度 · 全 HARD 不调 LLM.
"""
from __future__ import annotations

from omnicompany.packages.services._core.omnicompany import Material
from omnicompany.protocol.format import FormatRegistry


# ── 1. target_spec (源) ────────────────────────────────────────

M_TARGET_SPEC = Material(
    id="code_runtime_test.target_spec",
    name="code_runtime_test.target_spec",
    description=(
        "code_runtime_test 入口配置. 描述目标团队 + 用例集 + 输出抽取规则.\n\n"
        "字段:\n"
        "- target_team_id (str, required): 注册 id (如 'csv-to-md').\n"
        "- test_cases (list[obj], required, ≥1): 用例集. 每条:\n"
        "  - name (str): 用例名\n"
        "  - kind (str): 'success' | 'error' | 'reproducibility'\n"
        "  - input (obj): 给目标团队的 input_data\n"
        "  - (success kind) expected_path (str): 标杆产物文件路径\n"
        "  - (error kind) expected_verdict (str): 期望 verdict 值 ('FAIL'/'PARTIAL')\n"
        "  - (error kind) diagnosis_keywords (list[str]): diagnosis 应含的关键词 (任一即可)\n"
        "- output_extractor (str, optional): output 字典里取实际 markdown 的 key (默认 'report_markdown' 或 'markdown'; 若 output 直接是 str 就空)"
    ),
    parent="doc",
    json_schema={
        "type": "object",
        "properties": {
            "target_team_id": {"type": "string", "minLength": 1},
            "test_cases": {
                "type": "array",
                "minItems": 1,
                "items": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string"},
                        "kind": {"type": "string", "enum": ["success", "error", "reproducibility"]},
                        "input": {"type": "object"},
                        "expected_path": {"type": "string"},
                        "expected_verdict": {"type": "string"},
                        "diagnosis_keywords": {"type": "array", "items": {"type": "string"}},
                    },
                    "required": ["name", "kind", "input"],
                },
            },
            "output_extractor": {"type": "string"},
        },
        "required": ["target_team_id", "test_cases"],
    },
    tags=["code_runtime_test", "kind.source"],
)


# ── 2. target_metadata ─────────────────────────────────────────

M_TARGET_METADATA = Material(
    id="code_runtime_test.target_metadata",
    name="code_runtime_test.target_metadata",
    description=(
        "TargetIngress 装入. 含 target_team_id + 用例分类 + 输出抽取器.\n\n"
        "字段: target_team_id, success_cases (list), error_cases (list), reproducibility_cases (list), output_extractor (str)."
    ),
    parent="doc",
    json_schema={
        "type": "object",
        "properties": {
            "target_team_id": {"type": "string"},
            "success_cases": {"type": "array", "items": {"type": "object"}},
            "error_cases": {"type": "array", "items": {"type": "object"}},
            "reproducibility_cases": {"type": "array", "items": {"type": "object"}},
            "output_extractor": {"type": "string"},
        },
        "required": [
            "target_team_id",
            "success_cases",
            "error_cases",
            "reproducibility_cases",
        ],
    },
    tags=["code_runtime_test", "kind.internal"],
)


# ── 3. golden_evidence ─────────────────────────────────────────

M_GOLDEN_EVIDENCE = Material(
    id="code_runtime_test.golden_evidence",
    name="code_runtime_test.golden_evidence",
    description=(
        "路 1 标杆对标证据. 跑 success cases, 每条做 byte-diff vs expected.\n\n"
        "字段:\n"
        "- case_results (list[obj]): {name, verdict, byte_exact (bool), byte_diff_count (int), line_diff_count (int), elapsed_sec (number), diagnosis (str if not byte_exact)}\n"
        "- byte_exact_pct (number): 字节级完全等同的比例\n"
        "- mean_byte_diff_count (number)\n"
        "- contract_observation (str, ≥30 字符): 自然语言段落"
    ),
    parent="doc",
    json_schema={
        "type": "object",
        "properties": {
            "case_results": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string"},
                        "verdict": {"type": "string"},
                        "byte_exact": {"type": "boolean"},
                        "byte_diff_count": {"type": "integer", "minimum": 0},
                        "line_diff_count": {"type": "integer", "minimum": 0},
                        "elapsed_sec": {"type": "number"},
                        "diagnosis": {"type": "string"},
                    },
                    "required": ["name", "verdict", "byte_exact"],
                },
            },
            "byte_exact_pct": {"type": "number", "minimum": 0, "maximum": 1},
            "mean_byte_diff_count": {"type": "number", "minimum": 0},
            "contract_observation": {"type": "string", "minLength": 30},
        },
        "required": ["case_results", "byte_exact_pct", "contract_observation"],
    },
    tags=["code_runtime_test", "kind.internal"],
)


# ── 4. error_evidence ──────────────────────────────────────────

M_ERROR_EVIDENCE = Material(
    id="code_runtime_test.error_evidence",
    name="code_runtime_test.error_evidence",
    description=(
        "路 2 错误处理证据. 跑 error cases, 检 verdict + diagnosis 关键词.\n\n"
        "字段:\n"
        "- case_results (list[obj]): {name, actual_verdict, expected_verdict, verdict_match (bool), diagnosis (str), keywords_hit (list[str]), keyword_match (bool)}\n"
        "- verdict_match_pct: 正确 verdict 比例\n"
        "- keyword_match_pct: 正确 diagnosis 关键词比例\n"
        "- error_handling_observation (str, ≥30): 自然语言段落"
    ),
    parent="doc",
    json_schema={
        "type": "object",
        "properties": {
            "case_results": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string"},
                        "actual_verdict": {"type": "string"},
                        "expected_verdict": {"type": "string"},
                        "verdict_match": {"type": "boolean"},
                        "diagnosis": {"type": "string"},
                        "keywords_hit": {"type": "array", "items": {"type": "string"}},
                        "keyword_match": {"type": "boolean"},
                    },
                    "required": ["name", "actual_verdict", "verdict_match"],
                },
            },
            "verdict_match_pct": {"type": "number", "minimum": 0, "maximum": 1},
            "keyword_match_pct": {"type": "number", "minimum": 0, "maximum": 1},
            "error_handling_observation": {"type": "string", "minLength": 30},
        },
        "required": ["case_results", "verdict_match_pct", "error_handling_observation"],
    },
    tags=["code_runtime_test", "kind.internal"],
)


# ── 5. reproducibility_evidence ────────────────────────────────

M_REPRODUCIBILITY_EVIDENCE = Material(
    id="code_runtime_test.reproducibility_evidence",
    name="code_runtime_test.reproducibility_evidence",
    description=(
        "路 3 重现性证据. 跑 repro cases, 同 input 跑 2 次, byte-identical.\n\n"
        "字段:\n"
        "- case_results (list[obj]): {name, run1_byte_count, run2_byte_count, byte_identical (bool), diff_byte_count (int)}\n"
        "- byte_identical_pct (number)\n"
        "- reproducibility_observation (str, ≥30)"
    ),
    parent="doc",
    json_schema={
        "type": "object",
        "properties": {
            "case_results": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string"},
                        "run1_byte_count": {"type": "integer"},
                        "run2_byte_count": {"type": "integer"},
                        "byte_identical": {"type": "boolean"},
                        "diff_byte_count": {"type": "integer", "minimum": 0},
                    },
                    "required": ["name", "byte_identical"],
                },
            },
            "byte_identical_pct": {"type": "number", "minimum": 0, "maximum": 1},
            "reproducibility_observation": {"type": "string", "minLength": 30},
        },
        "required": [
            "case_results",
            "byte_identical_pct",
            "reproducibility_observation",
        ],
    },
    tags=["code_runtime_test", "kind.internal"],
)


# ── 6. portrait (sink) ─────────────────────────────────────────

M_PORTRAIT = Material(
    id="code_runtime_test.portrait",
    name="code_runtime_test.portrait",
    description=(
        "终态画像. 装配 3 路 evidence + 自然语言段落 + 做得好/漏列表.\n\n"
        "**反模式禁令**: 全字段除物理度量自然语言句子. 禁打分/标签.\n\n"
        "字段:\n"
        "- verdict: PASS / PARTIAL / FAIL\n"
        "- target_team_id\n"
        "- evidence_paths: {golden, error, reproducibility} 透传\n"
        "- portrait_paragraph (≥150 字)\n"
        "- what_target_does_well (list[str])\n"
        "- what_target_misses (list[str])\n"
        "- physical_metrics: 各路百分比 + 平均 byte_diff_count + 总 elapsed_sec"
    ),
    parent="requirement",
    json_schema={
        "type": "object",
        "properties": {
            "verdict": {"type": "string", "enum": ["PASS", "PARTIAL", "FAIL"]},
            "target_team_id": {"type": "string"},
            "evidence_paths": {"type": "object"},
            "portrait_paragraph": {"type": "string", "minLength": 150},
            "what_target_does_well": {"type": "array", "items": {"type": "string"}},
            "what_target_misses": {"type": "array", "items": {"type": "string"}},
            "physical_metrics": {"type": "object"},
        },
        "required": [
            "verdict",
            "target_team_id",
            "portrait_paragraph",
            "what_target_does_well",
            "what_target_misses",
        ],
    },
    tags=["code_runtime_test", "kind.sink"],
)


ALL_MATERIALS = [
    M_TARGET_SPEC,
    M_TARGET_METADATA,
    M_GOLDEN_EVIDENCE,
    M_ERROR_EVIDENCE,
    M_REPRODUCIBILITY_EVIDENCE,
    M_PORTRAIT,
]


def register_formats(registry: FormatRegistry) -> None:
    for mat in ALL_MATERIALS:
        if not registry.is_registered(mat.id):
            try:
                registry.register(mat)
            except Exception:
                pass
