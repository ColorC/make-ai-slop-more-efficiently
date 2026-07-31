<!-- [OMNI] origin=ai-ide domain=services/pipeline_ci ts=2026-05-04T14:30:00Z type=doc status=active agent=ai-ide belongs_to_service=pipeline_ci -->
<!-- [OMNI] summary="pipeline_ci service 自我叙事 README — 自动审计 packages 下所有管线包, PR 合并前发现 critical 阻断 CI. 三节点全确定性串行, ErrorRouteAuditor + PipelineChecker 双检" -->
<!-- [OMNI] why="按 self_narrative_three_files.md §四 模板严格写. 抽核心目的到 README, DESIGN 留架构性内容" -->
<!-- [OMNI] tags=readme,pipeline_ci,diagnosis,ci,self-narrative -->
<!-- [OMNI] material_id="material:services._diagnosis.pipeline_ci.readme.self_narrative.md"-->

# pipeline_ci · 管线 CI 审计

> 自动审计 `packages/` 下所有管线包, 在 PR 合并前发现 critical 级别问题阻断 CI. 三节点全确定性串行 (DomainScanner → BatchAuditor → CIGate), 不调 LLM, 跑得快.

---

## 这是什么

pipeline_ci 是 omnicompany 的**管线 CI 审计 service**. 它扫所有含 `routers.py + pipeline.py` 的包, 跑 ErrorRouteAuditor + PipelineChecker 双检, critical 计数 > 0 则 FAIL 阻断 CI.

形态: **三节点串行 Team, 全确定性** (无 LLM 调用), 跑得快, 适合 CI 集成.

跟其他诊断 service 的边界:
- **pipeline_ci** 看**管线包结构** (有没有非法路由 / Worker 元数据是否合规), 跑得快, 给 PR gate
- **doctor** 看**单个 Format/Worker/Team 的语义健康** (含 LLM 检查器, 慢)
- **guardian** 看**源码 / 文档静态合规** (位置 / 命名 / 头)
- **lap_auditor / semantic_auditor** 看**协议 / 语义合规** (调 LLM)

跟其他几个对比 pipeline_ci 是**最浅最快** 的一层 — 只阻断明显 critical 不做语义判断.

## 解决什么 / 不解决什么

**解决**:
- packages/ 下所有管线包的 critical 级问题快速发现
- PR 合并前的 CI gate (FAIL 即阻断)
- ErrorRouteAuditor (Worker 元数据 / 错误路由) + PipelineChecker (静态类型) 双检

**不解决**:
- Material kind / FORMAT_IN_MODE 等新世界条款 (那由 Guardian OMNI-037/038 静态扫 + doctor blackboard 子域覆盖)
- 单 Format / Worker 语义健康 (找 doctor)
- 业务正确性 (各 domain Team 自测)
- 不调 LLM 的语义级判断 (找 lap_auditor / semantic_auditor)

## 设计目的与最终目标

**设计目的**: 给 omnicompany 一个**最浅最快** 的 CI gate. 不能依赖 doctor 的 LLM 检查器 (慢 + 贵, CI 跑不起), 也不能完全没 gate (PR 合并后才发现 critical 太晚). pipeline_ci 是介于"无 gate" 跟"全语义检查" 之间的折中 — 只看明显错, 跑 30 秒以内.

**理论锚点**: 跟 Guardian 的 pre-commit hook 同思路 — 浅 + 快 + 阻断 critical. 但 Guardian 看源码静态合规, pipeline_ci 看管线结构, 维度不同.

**最终目标** (当下能认知的):
- 扩更多 Auditor (例新世界 Worker / Material 合规检查)
- 跟 doctor blackboard 子域 协作分工: pipeline_ci 跑确定性快检, blackboard 子域跑语义级 + LLM 深检
- 把 ErrorRouteAuditor 从 workflow_factory 跨 Team 直调改为 Material 订阅消费 (待新 Runtime)

## 规划

- **当前 V1** (active, 2026-04-20 Phase D Clean Migration 完成)
- **下一步**: 可扩展更多 Auditor (新世界 Worker/Material 合规检查)
- **远景**: 跟 doctor blackboard 子域分工; ErrorRouteAuditor 改 Material 消费

## 构成

- 入口与 Team → [pipeline.py](pipeline.py) (`build_pipeline()`) + [run.py](run.py)
- Materials (3 条) → [formats.py](formats.py)
  - `pipeline_ci.scan-request` (kind.source) — 外部触发
  - `pipeline_ci.domains` (kind.internal) — 扫到的所有 Team 域
  - `pipeline_ci.ci-report` (kind.internal) — 审计 issue 清单 + critical_count
- Workers (3 个) → [workers/](workers/)
  - `DomainScannerWorker` — 扫所有含 `routers.py + pipeline.py` 的包, 读文件
  - `BatchAuditorWorker` — 调 ErrorRouteAuditor + PipelineChecker 双检, 聚合 issue
  - `CIGateWorker` — critical_count > 0 → FAIL 阻断 CI
- 旧名 compat shim → [routers.py](routers.py)
- 归档 → _archive/routers_legacy.py

技术架构详述见 [DESIGN.md](DESIGN.md), 操作手册见 [SKILL.md](SKILL.md).

## 想了解更多

- 架构 → [DESIGN.md](DESIGN.md)
- 操作手册 → [SKILL.md](SKILL.md)
- 跟 doctor 关系 (语义检查 vs CI gate) → ../doctor/README.md
- 跟 guardian 关系 (源码合规 vs 管线结构) → ../../_core/guardian/README.md
- 跨 Team 依赖 → ../../_core/workflow_factory/README.md (其中的 ErrorRouteAuditor)
- 项目根叙事 → ../../../../../README.md
