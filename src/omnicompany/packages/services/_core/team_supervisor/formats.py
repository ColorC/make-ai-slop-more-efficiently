# [OMNI] origin=claude-code domain=services/team_supervisor/formats ts=2026-04-26T00:00:00Z type=config
# [OMNI] material_id="material:core.team_supervisor.material_definitions.registry.py"
"""team_supervisor Team · Material 定义.

8 个 Material 全部遵守 `feedback_semantic_sentences_not_classification`:
- 语义判定字段必须用自然语言句子承载
- 禁 score / level / tier / tags / kind 类离散标签字段
- 仅允许物理度量 (count / length) 与协议层硬枚举 (VerdictKind)

Material description 五要素: 内容语义 / 字段含义 / 上游承诺 / 下游用途 / 最小样例.
"""
from __future__ import annotations

from omnicompany.packages.services._core.omnicompany import Material
from omnicompany.protocol.format import FormatRegistry


# ── 1. target_spec (源 · 外部触发) ─────────────────────────────────

M_TARGET_SPEC = Material(
    id="team_supervisor.target_spec",
    name="team_supervisor.target_spec",
    description=(
        "team_supervisor 管线入口配置. 由外部触发 (CLI 或编排层) 一次性注入. "
        "告诉 supervisor 要监督哪个 target team, 给什么 sample input 跑它, 跑几次, 是否累积上次 ledger.\n\n"
        "字段含义: target_team_id (str): 待监督的 team 的协议层 id, 必须在全局 PipelineRegistry 中已注册 "
        "(如 'csv-to-md'). sample_input (dict): 给 target team 跑用的 input_data; 留空则 supervisor "
        "尝试从 data/services/{target}/ 历史 traces 中找; 实在找不到则 TestExecutor 报 PARTIAL. "
        "run_count (int): 跑 target 的次数; 默认 1; ≥2 用于稳定性观察. previous_ledger_path (str): "
        "上次跑出的 hypothesis ledger 路径, 用于跨 run 累积新假设.\n\n"
        "外部承诺: target_team_id 必须是 registry.names() 列表中存在的 id, 否则 TargetIngressWorker FAIL. "
        "sample_input 若提供必须是 dict.\n\n"
        "TargetIngressWorker 作为消费者, 读 target_team_id 校验存在性, 读 sample_input 透传给后续, "
        "若空则发起 traces 查找."
    ),
    parent="doc",
    json_schema={
        "type": "object",
        "properties": {
            "target_team_id": {
                "type": "string",
                "description": "待监督 team 的协议层 id (如 'csv-to-md')",
                "minLength": 1,
            },
            "sample_input": {
                "type": "object",
                "description": "给 target team 跑用的 input_data; 留空则尝试从 traces 找",
            },
            "run_count": {
                "type": "integer",
                "minimum": 1,
                "default": 1,
                "description": "跑 target 的次数, ≥1",
            },
            "previous_ledger_path": {
                "type": "string",
                "description": "上次 hypothesis ledger 路径, 用于累积",
            },
        },
        "required": ["target_team_id"],
    },
    tags=["team_supervisor", "kind.source"],
)


# ── 2. target_metadata (装入 · TargetIngressWorker 产) ─────────────

M_TARGET_METADATA = Material(
    id="team_supervisor.target_metadata",
    name="team_supervisor.target_metadata",
    description=(
        "TargetIngressWorker 产出的 target team 元数据. 包含 target 在文件系统中的代码路径与协议层信息, "
        "供下游所有 worker 通过 ReadFile/Glob 工具按需读取真实代码内容. 不预拷贝代码内容 (workspace 动态引用 · "
        "feedback_workspace_material_stock_diagnosis_first).\n\n"
        "字段: target_team_id (str): 透传. team_code_dir (str): target team 代码目录绝对路径 "
        "(如 'src/omnicompany/packages/services/repo_absorption/'). team_design_md_path (str): "
        "DESIGN.md 文件绝对路径; 若不存在则空字符串. team_py_path (str): team.py 路径. "
        "workers_dir (str): workers/ 子目录路径. format_out_id (str): target team 末节点的 FORMAT_OUT material id "
        "(从 build_team() 解析得). format_in_id (str): target team 入口节点 FORMAT_IN material id. "
        "worker_files (list[str]): workers/ 下所有 .py 文件的相对路径列表 (供 agent 探索). "
        "historical_traces_dir (str): 历史 trace 目录 'data/services/{target}/' 路径; 不存在则空. "
        "sample_input (dict): 透传 target_spec.sample_input 或 None. run_count: 透传.\n\n"
        "TargetIngressWorker 承诺: 所有路径在文件系统真实存在 (file/dir is_file/is_dir 校验通过), "
        "format_out_id / format_in_id 通过 build_team() 解析得真值, worker_files 是真实存在的 py 文件名列表.\n\n"
        "下游所有 worker 用这些路径作为 ReadFile/Glob 的输入, 自由探索代码内容."
    ),
    parent="doc",
    json_schema={
        "type": "object",
        "properties": {
            "target_team_id": {"type": "string"},
            "team_code_dir": {"type": "string", "description": "target team 包目录绝对路径"},
            "team_design_md_path": {"type": "string", "description": "DESIGN.md 路径; 不存在则空字符串"},
            "team_py_path": {"type": "string", "description": "team.py 路径"},
            "workers_dir": {"type": "string", "description": "workers/ 目录路径"},
            "format_out_id": {"type": "string", "description": "末节点 FORMAT_OUT material id"},
            "format_in_id": {"type": "string", "description": "入口节点 FORMAT_IN material id"},
            "worker_files": {
                "type": "array",
                "items": {"type": "string"},
                "description": "workers/ 下 .py 文件相对路径列表",
            },
            "historical_traces_dir": {"type": "string", "description": "trace 目录; 不存在则空"},
            "sample_input": {
                "type": ["object", "null"],
                "description": "透传 target_spec.sample_input",
            },
            "run_count": {"type": "integer", "minimum": 1},
        },
        "required": [
            "target_team_id",
            "team_code_dir",
            "team_py_path",
            "format_out_id",
            "worker_files",
        ],
    },
    tags=["team_supervisor", "kind.internal"],
)


# ── 3. product_form_brief (Q1 · ProductFormAnalyzer 产) ──────────

M_PRODUCT_FORM_BRIEF = Material(
    id="team_supervisor.product_form_brief",
    name="team_supervisor.product_form_brief",
    description=(
        "Q1 产物形式答案. ProductFormAnalyzerWorker 通过读 target team 末节点 FORMAT_OUT schema + "
        "末节点 worker 代码 + 历史 trace 产物 后, 用自然语言句子回答'这个 team 产物长什么样'.\n\n"
        "**反模式禁令** (按 feedback_semantic_sentences_not_classification): 字段值必须是完整自然语言句子或句子列表. "
        "禁 complexity / quality / type / tags 等 vibe 标签. 仅允许 schema_fields_observed (物理事实).\n\n"
        "字段含义:\n"
        "- essence (str, 句子): 一句话说本质 ('这个 team 产物是 ... 用于 ...')\n"
        "- minimal_passing_evidence (str, 句子): 最低合格产物长什么样 (具体, 含阈值/数量/锚点)\n"
        "- failure_signals (list[str]): 已知或推断的失败长什么样 · 每条是具体特征句子\n"
        "- concrete_examples (list[obj]): 历史 trace 中的具体例子 · 每条 {path, excerpt, note}\n"
        "- schema_fields_observed (list[str]): target FORMAT_OUT schema 真字段列表 (物理事实, 非 vibe)\n\n"
        "ProductFormAnalyzerWorker 承诺: essence 是完整句子 (≥20 字符 · 含主谓宾). "
        "minimal_passing_evidence 含具体可程序化判定的特征 (如 '≥3 段 markdown 且每段引用真实代码'). "
        "failure_signals 至少 2 条, 每条具体到可观察 (如 'proposals 列表为 0' 而非 'quality is low'). "
        "schema_fields_observed 来自实际读 schema, 不虚构.\n\n"
        "下游 HealthCriteriaDesigner 综合本 brief 与 Q2 设计判据; HypothesisGenerator 用 failure_signals 作产假设种子; "
        "HealthReportAssembler 透传到 health_report.three_questions.q1."
    ),
    parent="doc",
    json_schema={
        "type": "object",
        "properties": {
            "essence": {
                "type": "string",
                "minLength": 20,
                "description": "一句话说本质 · 自然语言完整句子",
            },
            "minimal_passing_evidence": {
                "type": "string",
                "minLength": 20,
                "description": "最低合格产物长什么样 · 含具体阈值或可程序化特征",
            },
            "failure_signals": {
                "type": "array",
                "minItems": 2,
                "items": {"type": "string", "minLength": 10},
                "description": "失败信号列表 · 每条具体特征句",
            },
            "concrete_examples": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string"},
                        "excerpt": {"type": "string"},
                        "note": {"type": "string"},
                    },
                    "required": ["path", "note"],
                },
                "description": "历史 trace 中的具体例子",
            },
            "schema_fields_observed": {
                "type": "array",
                "items": {"type": "string"},
                "description": "FORMAT_OUT schema 真字段列表 · 物理事实",
            },
        },
        "required": ["essence", "minimal_passing_evidence", "failure_signals"],
    },
    tags=["team_supervisor", "kind.internal"],
)


# ── 4. design_purpose_brief (Q2 · PurposeInterpreter 产) ─────────

M_DESIGN_PURPOSE_BRIEF = Material(
    id="team_supervisor.design_purpose_brief",
    name="team_supervisor.design_purpose_brief",
    description=(
        "Q2 设计目的答案. PurposeInterpreterWorker 通过读 target DESIGN.md + team.py docstring + "
        "worker docstring + dispatch 调用方代码 后, 用自然语言句子回答'这个 team 为什么存在'.\n\n"
        "**反模式禁令**: 全字段自然语言句子. 禁 type / category / domain 等分类标签 (除非透传协议层硬枚举).\n\n"
        "字段含义:\n"
        "- essence (str, 句子): 它解决什么具体问题 ('这个 team 用来 ... 解决 ...')\n"
        "- replaces (str, 句子): 没有它时, 用什么手段做这事 (人工? 别的工具? 没人做?)\n"
        "- non_goals (list[str]): 它**不**做什么 · 反向定义 · 每条句子\n"
        "- stakeholder_use (str, 句子): 谁会消费它产出, 怎么用 (具体场景句)\n"
        "- evidence_sources (list[obj]): 推断依据 · 每条 {file, section} 引用具体来源 (DESIGN.md 哪节 / docstring)\n\n"
        "PurposeInterpreterWorker 承诺: essence 不空 (≥30 字符 · 含具体问题描述). "
        "non_goals 至少 1 条 (反向定义有助下游识别越界). "
        "evidence_sources 至少 1 条引用 (避免 LLM 凭空发挥).\n\n"
        "下游 HealthCriteriaDesigner 综合 Q1+Q2 推健康判据; HypothesisGenerator 用 non_goals 产'不应做 X'反向假设; "
        "HealthReportAssembler 透传到 q2."
    ),
    parent="doc",
    json_schema={
        "type": "object",
        "properties": {
            "essence": {
                "type": "string",
                "minLength": 30,
                "description": "解决什么具体问题 · 完整句子",
            },
            "replaces": {
                "type": "string",
                "minLength": 10,
                "description": "没有它时用什么手段 · 句子",
            },
            "non_goals": {
                "type": "array",
                "minItems": 1,
                "items": {"type": "string", "minLength": 10},
                "description": "它不做什么 · 反向定义 · 句子列表",
            },
            "stakeholder_use": {
                "type": "string",
                "minLength": 20,
                "description": "谁消费产出怎么用 · 句子",
            },
            "evidence_sources": {
                "type": "array",
                "minItems": 1,
                "items": {
                    "type": "object",
                    "properties": {
                        "file": {"type": "string"},
                        "section": {"type": "string"},
                    },
                    "required": ["file"],
                },
                "description": "推断依据来源",
            },
        },
        "required": ["essence", "replaces", "non_goals", "stakeholder_use", "evidence_sources"],
    },
    tags=["team_supervisor", "kind.internal"],
)


# ── 5. health_criteria (Q3 · HealthCriteriaDesigner 产) ──────────

M_HEALTH_CRITERIA = Material(
    id="team_supervisor.health_criteria",
    name="team_supervisor.health_criteria",
    description=(
        "Q3 健康判据. HealthCriteriaDesignerWorker 综合 Q1 product_form_brief + Q2 design_purpose_brief 后, "
        "用自然语言句子说明: 看 target team 产物时, 应观察什么 / 红旗信号是什么 / 如何程序化验证.\n\n"
        "**反模式禁令**: 全字段句子. 禁 oracle 用 metric_name=value 表达 (用语义描述加实现 hint 替代).\n\n"
        "字段含义:\n"
        "- key_observations (list[str]): 看产物时应主动观察什么 · 每条句子\n"
        "- red_flags (list[str]): 出现什么就该警惕 · 具体特征句\n"
        "- oracle_strategies (list[obj]): 验证策略列表 · 每条 {what_to_check, implementation_hint} · 全句子\n"
        "  - what_to_check: 验证什么 (语义描述 · 不是 'metric > 0.8' 形式)\n"
        "  - implementation_hint: 怎么程序化实现这个 oracle (语义 hint, 给 TestExecutor 参考)\n\n"
        "HealthCriteriaDesigner 承诺: key_observations ≥3 条; red_flags ≥2 条; oracle_strategies ≥3 条 "
        "(下游 HypothesisGenerator 至少能据此产 ≥10 条假设).\n\n"
        "下游 HypothesisGenerator 把 oracle_strategies 翻译成具体 (条件→预期) 假设; "
        "HealthReportAssembler 透传到 q3."
    ),
    parent="doc",
    json_schema={
        "type": "object",
        "properties": {
            "key_observations": {
                "type": "array",
                "minItems": 3,
                "items": {"type": "string", "minLength": 10},
                "description": "应观察什么 · 句子列表",
            },
            "red_flags": {
                "type": "array",
                "minItems": 2,
                "items": {"type": "string", "minLength": 10},
                "description": "红旗信号 · 具体特征句",
            },
            "oracle_strategies": {
                "type": "array",
                "minItems": 3,
                "items": {
                    "type": "object",
                    "properties": {
                        "what_to_check": {"type": "string", "minLength": 15},
                        "implementation_hint": {"type": "string", "minLength": 15},
                    },
                    "required": ["what_to_check", "implementation_hint"],
                },
                "description": "验证策略 · 全句子",
            },
        },
        "required": ["key_observations", "red_flags", "oracle_strategies"],
    },
    tags=["team_supervisor", "kind.internal"],
)


# ── 6. hypothesis_set (HypothesisGenerator 产) ──────────────────

M_HYPOTHESIS_SET = Material(
    id="team_supervisor.hypothesis_set",
    name="team_supervisor.hypothesis_set",
    description=(
        "假设集合. HypothesisGeneratorWorker 综合 Q1+Q2+Q3 + 实读 target 代码后, 产 ≥10 条 (条件→预期) 假设, "
        "每条带可程序化判定的 oracle hint.\n\n"
        "**反模式禁令**: 假设字段全自然语言句子. id 是物理标识 (允许). 禁 priority / severity / category 等 vibe 字段.\n\n"
        "字段:\n"
        "- hypotheses (list[obj]): 假设列表, 每条:\n"
        "  - id (str, 物理标识): 'H-001', 'H-002', ... 递增唯一\n"
        "  - condition (str, 句子): 什么情况下应观察 ('当 target 被喂 sample_input X 跑完后...')\n"
        "  - expectation (str, 句子): 期望什么具体特征 ('应观察到 verdict=PASS 且 output.proposals 数量 ≥3')\n"
        "  - oracle_code_hint (str, 句子): 怎么程序化判定 ('解析 verdict.output 找 proposals.length ≥ 3')\n"
        "  - rationale (str, 句子): 这个假设来自哪个 Q · 为什么有意义\n\n"
        "HypothesisGenerator 承诺: hypotheses ≥ 10 条 (跨 Q1+Q2+Q3 派生). "
        "每条 condition + expectation + oracle_hint 全是完整句子. id 唯一无重复.\n\n"
        "下游 TestExecutor 真 dispatch target + 跑 oracle 评估每条假设; HealthReportAssembler 用作 evaluated 数据源."
    ),
    parent="doc",
    json_schema={
        "type": "object",
        "properties": {
            "hypotheses": {
                "type": "array",
                "minItems": 10,
                "items": {
                    "type": "object",
                    "properties": {
                        "id": {
                            "type": "string",
                            "pattern": "^H-\\d{3}$",
                            "description": "形如 H-001 唯一 id",
                        },
                        "condition": {"type": "string", "minLength": 20},
                        "expectation": {"type": "string", "minLength": 20},
                        "oracle_code_hint": {"type": "string", "minLength": 15},
                        "rationale": {"type": "string", "minLength": 15},
                    },
                    "required": [
                        "id",
                        "condition",
                        "expectation",
                        "oracle_code_hint",
                        "rationale",
                    ],
                },
                "description": "假设列表",
            },
        },
        "required": ["hypotheses"],
    },
    tags=["team_supervisor", "kind.internal"],
)


# ── 7. test_results (TestExecutor 产) ──────────────────────────

M_TEST_RESULTS = Material(
    id="team_supervisor.test_results",
    name="team_supervisor.test_results",
    description=(
        "测试执行结果. TestExecutorWorker 真 dispatch target team 跑出产物, 然后逐条假设跑 oracle, "
        "记每条假设是否 passed, 实际观察到什么.\n\n"
        "**反模式禁令**: hypothesis_evaluations 中 passed 是真布尔有客观判据 (允许). "
        "禁 confidence_score / quality_rating 等 vibe 字段. observed 是自然语言句子.\n\n"
        "字段:\n"
        "- target_run_verdict (str, 协议层硬枚举): 'PASS'|'FAIL'|'PARTIAL' · target dispatch 出来的 verdict\n"
        "- target_output_summary (str, 句子): target 产物要点摘要\n"
        "- target_traces_path (str): 本次跑出的 trace 落盘路径 (供溯源)\n"
        "- hypothesis_evaluations (list[obj]): 每条假设评估:\n"
        "  - hypothesis_id (str): 引用 hypothesis_set 中的 id\n"
        "  - passed (bool): 是否通过 (真布尔有客观判据)\n"
        "  - observed (str, 句子): 实际观察到什么\n"
        "  - evidence (list[obj]): 引用锚点 · 每条 {path, line/key, excerpt}\n\n"
        "TestExecutor 承诺: target dispatch 真跑 (不模拟); 每条假设都跑 oracle 不跳过 (跳过则记 passed=false + observed='oracle 跑失败 ...'); "
        "evidence 引用 target 真产物或代码锚点.\n\n"
        "下游 HealthReportAssembler 装配 health_report."
    ),
    parent="doc",
    json_schema={
        "type": "object",
        "properties": {
            "target_run_verdict": {
                "type": "string",
                "enum": ["PASS", "FAIL", "PARTIAL"],
            },
            "target_output_summary": {
                "type": "string",
                "minLength": 20,
                "description": "target 产物摘要 · 句子",
            },
            "target_traces_path": {"type": "string"},
            "hypothesis_evaluations": {
                "type": "array",
                "minItems": 1,
                "items": {
                    "type": "object",
                    "properties": {
                        "hypothesis_id": {"type": "string", "pattern": "^H-\\d{3}$"},
                        "passed": {"type": "boolean"},
                        "observed": {"type": "string", "minLength": 10},
                        "evidence": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "path": {"type": "string"},
                                    "excerpt": {"type": "string"},
                                },
                                "required": ["path"],
                            },
                        },
                    },
                    "required": ["hypothesis_id", "passed", "observed"],
                },
                "description": "假设评估结果",
            },
        },
        "required": [
            "target_run_verdict",
            "target_output_summary",
            "hypothesis_evaluations",
        ],
    },
    tags=["team_supervisor", "kind.internal"],
)


# ── 8. health_report (sink · HealthReportAssembler 产) ──────────

M_HEALTH_REPORT = Material(
    id="team_supervisor.health_report",
    name="team_supervisor.health_report",
    description=(
        "终态健康报告 (sink). HealthReportAssemblerWorker 装配三问 brief + 假设评估 + 总体诊断, "
        "由 L1/L2 消费决定如何修.\n\n"
        "**反模式禁令**: verdict 是协议层硬枚举 (允许). diagnosis 是自然语言段落. "
        "禁 health_score / overall_grade / risk_level 等 vibe 字段.\n\n"
        "字段:\n"
        "- verdict (str, 硬枚举): 'PASS'|'PARTIAL'|'FAIL' (>=80% 假设过 → PASS · ≥50% → PARTIAL · 否则 FAIL)\n"
        "- target_team_id (str): 透传\n"
        "- three_questions (obj): {q1, q2, q3} 透传三问 brief\n"
        "- hypotheses_evaluated_count (int): 物理度量\n"
        "- passed_count (int): 物理度量\n"
        "- failed_hypotheses (list[obj]): 失败的具体哪些 (id + condition + expectation + observed)\n"
        "- diagnosis (str, 段落): 总体诊断段落 · 自然语言 · 含'这个 team 健康吗 · 为什么 · 主要问题在哪'\n"
        "- ledger_increment (list[obj]): 这次新增的假设 · 用于下次累积\n\n"
        "HealthReportAssembler 承诺: verdict 由 passed_count / total 比例决定 (无 LLM); "
        "三问 brief 完整透传 (不重新 LLM); diagnosis ≥ 100 字符且含具体引用 (非泛化语).\n\n"
        "外部消费: L1 抽样审 / L2 交叉对照 / 后续可累积成长期 ledger."
    ),
    parent="requirement",
    json_schema={
        "type": "object",
        "properties": {
            "verdict": {"type": "string", "enum": ["PASS", "PARTIAL", "FAIL"]},
            "target_team_id": {"type": "string"},
            "three_questions": {
                "type": "object",
                "properties": {
                    "q1": {"type": "object"},
                    "q2": {"type": "object"},
                    "q3": {"type": "object"},
                },
                "required": ["q1", "q2", "q3"],
            },
            "hypotheses_evaluated_count": {"type": "integer", "minimum": 0},
            "passed_count": {"type": "integer", "minimum": 0},
            "failed_hypotheses": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "string"},
                        "condition": {"type": "string"},
                        "expectation": {"type": "string"},
                        "observed": {"type": "string"},
                    },
                    "required": ["id", "expectation", "observed"],
                },
            },
            "diagnosis": {"type": "string", "minLength": 100},
            "ledger_increment": {
                "type": "array",
                "items": {"type": "object"},
                "description": "新增假设条目 · 累积用",
            },
        },
        "required": [
            "verdict",
            "target_team_id",
            "three_questions",
            "hypotheses_evaluated_count",
            "passed_count",
            "diagnosis",
        ],
    },
    tags=["team_supervisor", "kind.sink"],
)


ALL_MATERIALS = [
    M_TARGET_SPEC,
    M_TARGET_METADATA,
    M_PRODUCT_FORM_BRIEF,
    M_DESIGN_PURPOSE_BRIEF,
    M_HEALTH_CRITERIA,
    M_HYPOTHESIS_SET,
    M_TEST_RESULTS,
    M_HEALTH_REPORT,
]


def register_formats(registry: FormatRegistry) -> None:
    """注册 team_supervisor 所有 Material 到 registry."""
    for mat in ALL_MATERIALS:
        if not registry.is_registered(mat.id):
            try:
                registry.register(mat)
            except Exception:
                pass
