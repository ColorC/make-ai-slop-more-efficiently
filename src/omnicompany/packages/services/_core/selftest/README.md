<!-- [OMNI] origin=ai-ide domain=services/selftest ts=2026-05-04T13:40:00Z type=doc status=active agent=ai-ide belongs_to_service=selftest -->
<!-- [OMNI] summary="selftest service 自我叙事 README — omnicompany 自测套件, 端到端冒烟验证 (注册体系/Stock 读写/CLI 健康). 4 Worker 线性 Team 产 health-report" -->
<!-- [OMNI] why="按 self_narrative_three_files.md §四 模板严格写. 抽核心目的到 README, DESIGN 留架构性内容" -->
<!-- [OMNI] tags=readme,selftest,core,self-narrative -->
<!-- [OMNI] material_id="material:services._core.selftest.readme.self_narrative.md"-->

# selftest · omnicompany 自测套件

> 端到端冒烟验证 — 注册体系能否 build / Stock 读写通 / LLM 连通性 / CLI 健康端点. 4 Worker 线性 Team 产 `selftest.health-report` (sink material) 供人 / CI 读.

---

## 这是什么

selftest 是 omnicompany 的**自测套件 service**. 它跑端到端冒烟验证, 检查"omnicompany 框架本身能不能正常工作", 跟具体业务 Team 的内部测试解耦.

形态: **4 Worker 线性 Team** (RegistryChecker → FunctionalTester → SelftestGate HARD → LLMReporter SOFT) → `health-report`.

跟其他诊断 service 的边界:
- **selftest** 测**框架本身** (注册中心 / Stock / CLI / LLM 连通性)
- **doctor** 测**单个 Format/Worker/Team 的语义健康**
- **guardian** 测**源码 / 文档静态合规**
- **lap_auditor / semantic_auditor** 测**协议 / 语义合规**

selftest 是基础测试, 上面三件是更细分维度的检查.

## 解决什么 / 不解决什么

**解决**:
- 注册中心中的 Team 能否全部 `build_pipeline()` / `build_bindings()` 成功
- Stock 读写通道是否通 (原 EventBus roundtrip)
- LLM 连通性是否正常
- CLI 基础功能是否可用
- 给 CI / 人类一个"框架本身能跑吗" 的 PASS/FAIL gate

**不解决**:
- 业务层正确性 (由各 Team 的 domain 测试负责)
- 跨 Job 行为 (一次 selftest = 一个 Job)
- 性能基准 (另由 benchmark Team 负责)

## 设计目的与最终目标

**设计目的**: 让 omnicompany 框架自己有"我还能跑吗" 的快速冒烟. 没 selftest 框架挂了人不知道, 业务测试报错也分不清是业务问题还是框架问题. selftest 是 sanity check 层.

**理论锚点**: omnicompany 自稳定主轴第二件能力"诊断修复" 的**最浅层** — 比 doctor 浅 (doctor 看每个对象细节), 比 guardian 浅 (guardian 看源码合规). selftest 只回答"还能跑吗".

**最终目标** (当下能认知的):
- llm_reporter 在 LLM 不可用时显式 FAIL (当前 SOFT 静默降级 PASS)
- 覆盖业务 Team 内部冒烟 (每 Team 声明 smoke_test_request sample, selftest 跑一遍)
- Phase 1 runtime 到位后 llm_reporter 升级 Agent Worker (若 material 需求动态化)

## 规划

- **当前 V2** (active, 2026-04-20 Clean Migration 完成)
- **下一步**: Phase 1 runtime 到位后 llm_reporter 可升级 Agent Worker
- **远景**: 覆盖业务 Team 内部冒烟

## 构成

- 入口与 Team → [team.py](team.py) + [pipeline.py](pipeline.py) (`build_pipeline()`) + [run.py](run.py) (`build_bindings()`)
- Materials (4 条) → [formats.py](formats.py)
  - `selftest.request` (kind.source) — CLI 触发
  - `selftest.registry-report` (kind.internal) — 注册层扫描结果
  - `selftest.selftest-report` (kind.internal) — 功能冒烟结果
  - `selftest.health-report` (kind.sink) — 最终 Markdown 报告
- Workers (4 个) → [workers/](workers/)
  - `RegistryCheckerWorker` (TRANSFORMER) — 注册层扫描 (能否 build)
  - `FunctionalTesterWorker` (TRANSFORMER) — 功能冒烟 (Stock / CLI / LLM)
  - `SelftestGateWorker` (HARD ANCHOR) — 门控 PASS/FAIL
  - `LLMReporterWorker` (SOFT ANCHOR) — LLM 摘要生成
- 旧名 compat shim → [routers.py](routers.py)

技术架构详述见 [DESIGN.md](DESIGN.md), 操作手册见 [SKILL.md](SKILL.md).

## 想了解更多

- 架构 → [DESIGN.md](DESIGN.md)
- 操作手册 → [SKILL.md](SKILL.md)
- 跟 doctor 关系 → [../../_diagnosis/doctor/README.md](../../_diagnosis/doctor/README.md)
- 跟 guardian 关系 → [../guardian/README.md](../guardian/README.md)
- 项目根叙事 → [../../../../../README.md](../../../../../../README.md)
