<!-- [OMNI] origin=ai-ide domain=services/doctor ts=2026-05-04T11:00:00Z type=doc status=active agent=ai-ide belongs_to_service=doctor -->
<!-- [OMNI] summary="doctor service 自我叙事 README — 管线级健康诊断, omnicompany 三大主轴能力之一 (诊断修复). 4 子域 30 Worker 三条独立管线 + Blackboard 订阅图诊断" -->
<!-- [OMNI] why="DESIGN.md 358 行混了核心目的+架构, 对大型 service 试拆三件. README 给 doctor 的语境/远景/规划新视角, DESIGN 收窄到架构性内容" -->
<!-- [OMNI] tags=readme,doctor,diagnosis,self-narrative -->
<!-- [OMNI] material_id="material:services._diagnosis.doctor.readme.self_narrative.md"-->

# doctor · 管线级健康诊断

> 回答"这个 Format / Worker / Team 是否结构合规、语义清晰、上下游匹配?". 4 子域 30 个 Worker 三条独立管线 + Blackboard 订阅图诊断, 输出结构化 Finding 不打分. 是 omnicompany 三大主轴能力之一 (诊断修复).

---

## 这是什么

doctor 是 omnicompany 的**管线级健康诊断 service**. 它扫源码 + 调 LLM, 对每个受管对象 (Material / Worker / Team / 订阅图) 跑独立诊断管线, 输出结构化 `Finding` (含等级 / 位置 / 现象 / 语义含义) 跟健康档案.

诊断维度独立, 共享一致架构 (Anchor 短路 + fan-out 并行检查 + fan-in health_writer 汇聚):

- **Format 诊断** (`doctor.material.*`) — 五要素 / tag 规范 / parent_chain / composite 合法性 / 示例质量 / LLM 语义审计
- **Worker 诊断** (`doctor.router.*`) — Worker signature / context 收集 / 确定性 / LLM 上下文审计
- **Team 拓扑诊断** (`doctor.pipeline.*`) — no_entry / isolated / format_break / cycle / soft_hard_pairing / maturity / narrative 叙事连贯
- **Blackboard 订阅图诊断** (新世界 V3) — Material kind 合法性 / FORMAT_IN_MODE / Verdict.output 平铺 / orphan/unconsumed / 子 job 发射规范

每个 Finding 有等级 `blocking` / `degrading` / `advisory` / `info`, **不打分** (打分主观掩盖语义) — 这是 D3 决策, 跟 [feedback_no_score_keep_semantic](../../../../../) 铁律对齐.

## 解决什么 / 不解决什么

**解决**:
- 单个 Format 是否合规 (字段完整 / tag 正确 / 有引用 / 有示例)
- 单个 Worker 是否合理 (signature 正确 / context 完整 / 确定性符合声明)
- 单个 Team 拓扑是否健康 (无孤儿 / 无环 / format 契约一致 / soft-hard 配对正确 / 叙事连贯)
- 订阅图级合规 (新世界 Material/Worker/Team 体系)
- 跨 commit 跟踪健康回归 (HealthArchive)

**不解决**:
- 修复 (那是 [services/repair/](../../) 职责, doctor 只产 Finding 不动代码)
- 监管源码合规 (那是 [services/_core/guardian/](../../_core/guardian/) 职责 — guardian 扫源码一次性, doctor 跑健康档案多次)
- 执行管线 (那是 [runtime/exec/PipelineRunner](../../../../runtime/exec/) 职责, doctor 自己也是被 PipelineRunner 跑的)
- 诊断业务正确性 (业务跟踪归各 domain 自己, doctor 只看结构 / 语义 / 合规)

## 设计目的与最终目标

**设计目的**: 让 omnicompany 的每个受管对象都能被"独立诊断" — 不用人去 grep 拼凑, 跑诊断管线就拿结构化 Finding. 让"这个 Format/Worker/Team 健康吗" 有真权威源.

**理论锚点**: doctor 体现项目主轴第二件能力 (PROGRESS.md §一) — "诊断修复". 没有 doctor 这层独立诊断, omnicompany 的"自维护" 就缺了"自诊断" 这一核心环节.

**最终目标** (当下能认知的):
- 接 LAP `crystallize` 回路 — Doctor 的 Finding 自动反哺 SpecPatch 候选, 让"发现问题"自动转"修复建议"
- `.omni/health/` 就近写盘 (Phase 2 计划) — 健康档案落到所属包的 `.omni/health/`, dashboard 按包聚合
- 跟 CORE-SELF-STABILITY 第二阶段 "诊断 + 分析" 能力对接 — 升级到含语义级 (不光结构) 异常检测

## 规划

- **当前 V3** (旧 World V3 active, 2026-04-20 New World Diagnostics Phase B): 30 Worker, 4 子域 (Format/Router/Pipeline/Blackboard), Clean Migration V2 完成
- **当前假设系统 V12** (active, 2026-05-07): V0→V12 全栈跑通 — 7 agent / 7 builder / 3 scanner / 8 tool / 253+ pytest / 真 LLM 跨 6 阶段实测过. 假设系统这一族在工具+CLI 层面 production-ready. 见 V7 final 架构汇报
- **下一步**: 旧 V3 — Phase 2 backlog .omni/health/ 就近写盘 + LAP crystallize. 假设系统 V13+ — MetaDiagnosticAgent 完整一轮真 LLM (待 token 授权) + Guardian/CI 集成 (架构级)
- **远景 (CORE-SELF-STABILITY 协作)**: 诊断升级到语义级, 接入自我画像漂移检测

进度细节: docs/PROGRESS.md (项目级) + [DESIGN.md `## 状态`](DESIGN.md) (本 service 状态).

## 构成

doctor 现含两套并存子域:

### 一 · 旧 World V3 诊断管线 (Format/Worker/Team/Blackboard, 30 Worker)

按**子域组织** 30 个 Worker 分 4 子域 (旧 V3 立, 跑得通在用):

| 子域 | Worker 数 | 职责 | 入口 |
|---|---|---|---|
| Format 诊断 | 9 | 单 Format 五要素 / 规范 / 语义审计 | workers/format/ |
| Worker 诊断 | 6 | 单 Worker signature / context / 确定性 / LLM 审计 | workers/router/ |
| Team 拓扑诊断 | 9 | 单 Team 结构 / 契约 / 成熟度 / 叙事 | workers/pipeline/ |
| Blackboard 订阅图诊断 | 6 | 新世界订阅图级 (kind / mode / orphan / unconsumed / 子 job) | [workers/blackboard/](workers/blackboard/) |

加 3 个 run.py 内 passthrough Worker (bindings 内部细节).

### 二 · 假设系统 + 元诊断 (V0→V12, 2026-05-07 立)

跟旧 World V3 子域并行, 走"假设撰写 → 优先怀疑排序 → LLM 真证否 → finding 反向链 → 自动升级 confidence" 闭环. **完整架构汇报**: [`docs/plans/diagnosis/[2026-05-05]DIAGNOSIS-RECONSOLIDATION/reports/challenge_agent_v7_architecture_final_2026-05-07.md`](../../../../../../docs/plans/diagnosis/%5B2026-05-05%5DDIAGNOSIS-RECONSOLIDATION/challenge_agent_v7_architecture_final_2026-05-07.md).

| 子域 | 组件 | 职责 |
|---|---|---|
| LLM Agent ([agents/](agents/)) | 7 agent | spec / hypothesis / exemplar / plan / meta / hypothesis_deriver / **challenge** (V3 新立, 真证否假设本身) |
| 纯函数 ([builders/](builders/)) | 7 builder | V1Upgrader / ChallengeQueue / ChallengeRecorder / ResolutionRecorder / ConfidenceAuditor / PytestSkeletonBuilder / HypothesisAgentPromptBuilder |
| 客观扫描器 ([scanners/](scanners/)) | 3 scanner | FacilityScanner / WorkPatternAnomalyScanner / PromptPatchPileScanner (AP-024) |
| Agent 工具 ([tools/](tools/)) | 8 tool | git_log / write_finding / submit_verdict / write_hypothesis / submit_derivation_report / record_hypothesis_challenge / record_hypothesis_resolution / rank_hypothesis_challenge_queue |

#### 真用户怎么用 (V11 + V12 CLI, 三步走通)

```bash
# 1. V1 升级假设 yaml + 反向链 archive (V5.2 默认接通)
python -m omnicompany.packages.services._diagnosis.doctor.builders.hypothesis_v1_upgrader \
  --hypotheses-dir data/services/doctor/hypotheses \
  --map-path docs/plans/.../canonical_anchors/standards_authority_map.yaml

# 2. 看 ChallengeQueue 排序 (V11 dry-run 默认无 LLM):
python -m omnicompany.packages.services._diagnosis.doctor.agents \
  --hypotheses-dir data/services/doctor/hypotheses \
  --applies-to worker --focus-count 5 --dry-run

# 3. 看 ConfidenceAuditor 升级建议 (V12 only-needs-upgrade):
python -m omnicompany.packages.services._diagnosis.doctor.builders.hypothesis_confidence_auditor \
  --hypotheses-dir data/services/doctor/hypotheses --only-needs-upgrade
```

第 2 步加 `--no-dry-run --focus-count 1` 真调 ChallengeAgent (qwen-3.6-plus, ~10K token / agent run).

数据流向: `派生 → V1 升级 → ChallengeQueue 排序 → ChallengeAgent 真证否 → finding 落 archive → V1Upgrader 重升 (反向链) → ConfidenceAuditor 自动升级建议`. schema §三 5 步全实施.

技术骨架:
- 入口与 Team → [run.py](run.py) + [team.py](team.py) + [pipeline.py](pipeline.py) (三个 build_*_pipeline 函数)
- Materials → [formats.py](formats.py) (11 Material, kind 100% 覆盖)
- 检查纯函数库 → [checks/](checks/) (非 Worker, 被 Worker 内部调)
- 旧名兼容 → [routers.py](routers.py) (compat shim 22 别名) + [pipeline_topology.py](pipeline_topology.py)
- 归档 Legacy → [_archive/routers_legacy.py](_archive/) + [_archive/pipeline_topology_legacy.py](_archive/) (业务逻辑源, Worker 子类继承)

技术架构详述见 [DESIGN.md](DESIGN.md), 操作手册见 [SKILL.md](SKILL.md).

## 想了解更多

- 架构 → [DESIGN.md](DESIGN.md) (含 D1-D7 决策 / 三条管线拓扑 / Clean Migration V2 实施策略)
- 操作手册 → [SKILL.md](SKILL.md)
- 上层 _diagnosis 域 → [../README.md](../) (待建)
- 命名跟概念 → [terminology.md](../../../../../../docs/standards/_global/terminology.md)
- guardian (源码合规, 跟 doctor 互补) → [../../_core/guardian/](../../_core/guardian/)
- 项目根叙事 → [../../../../../README.md](../../../../../../README.md)
