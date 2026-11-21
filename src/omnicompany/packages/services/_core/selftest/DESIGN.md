<!-- [OMNI] origin=claude-code domain=services/selftest ts=2026-05-04T13:40:00Z type=doc status=active belongs_to_service=selftest -->
<!-- [OMNI] material_id="material:core.selftest.design_document.markdown.md" -->

# selftest · 设计文档

> 设计目的请看 [README.md](README.md). 怎么用请看 [SKILL.md](SKILL.md). 本文档专管**架构内部** (接口 / 决策 / 数据流 / 局限).
>
> 形态: 行政部 Team (见 [terminology §2](../../../../../../docs/standards/_global/terminology.md) · 核心基础设施服务全公司).
> Clean Migration 完成 2026-04-20 夜 (Stage 2 完全迁移 · workers/ 子目录 + Worker 基类 + Material alias).

## 状态
- **版本**: V2 (2026-04-20 · Clean Migration)
- **成熟度**: active
- **下一步**: Phase 1 runtime 到位后, llm_reporter 可升级为 Agent Worker（若 material 需求动态化）

## 核心接口

- `build_pipeline()` → [pipeline.py](pipeline.py) 构造 4 Worker Team spec
- `build_bindings()` → [run.py](run.py) 映射 node_id → Worker 子类实例
- Worker 清单（[workers/](workers/)）:
  - `RegistryCheckerWorker`（Worker #1）· TRANSFORMER · source → registry-report · [workers/registry_checker.py](workers/registry_checker.py)
  - `FunctionalTesterWorker`（Worker #2）· TRANSFORMER · registry-report → selftest-report · [workers/functional_tester.py](workers/functional_tester.py)
  - `SelftestGateWorker`（Worker #3）· ANCHOR HARD · selftest-report → selftest-report (PASS/FAIL) · [workers/selftest_gate.py](workers/selftest_gate.py)
  - `LLMReporterWorker`（Worker #4）· ANCHOR SOFT · selftest-report → health-report (sink) · [workers/llm_reporter.py](workers/llm_reporter.py)
- 兼容 shim: [routers.py](routers.py) 旧名 `*Router` 作为 `*Worker` 别名保留（不要新增代码）
- 归档: [_archive/routers_legacy.py](_archive/routers_legacy.py) 原单文件 4-Router 实现

## 架构决策

### D1 — 4 Worker 粒度（对齐 R-18）

Team 按 "完整职责 + FORMAT 边界 + 独立测试价值" 划分为 4 Worker:
- RegistryChecker: 注册层扫描（独立单元）
- FunctionalTester: 功能冒烟（独立测试）
- SelftestGate: 门控判定（HARD Anchor, 独立验证点）
- LLMReporter: LLM 摘要生成（SOFT Anchor, 独立 LLM 调用）

不再细分: 4 个职责已边界清晰, 内部再拆无独立测试价值（违反 R-18）。

### D2 — Material kind 三分完整标注（对齐 F-16）

- `selftest.request`: **source** — 外部触发, 无 producer Worker（CLI `omni selftest run` 产出）
- `selftest.registry-report`: internal
- `selftest.selftest-report`: internal
- `selftest.health-report`: **sink** — 最终产物, 返回调用者, 无 consumer Worker

Q4 诊断下 RegistryChecker（订阅 source material）无上游 producer 合法, 不算孤儿。

### D3 — LLMReporter 不升级为 Agent Worker（对齐 R-20）

LLMReporter material 需求**明确固定**（selftest.selftest-report 单条输入）, 不符合"升级触发条件"（动态 material / schema 膨胀 / 大 context）。保留单 LLM Worker 形态。

Phase 1 若发现 LLM 需要额外 material（如查看具体失败细节）, 再升级为 Agent Worker。

### D4 — 不需要 Workspace（对齐 F-17）

所有 material 本体是结构化 JSON（KB 级）, 不含大明文。按 F-17 "小结构化走数据库正文"。无 workspace 配置。

### D5 — SelftestGate HARD + LLMReporter SOFT 配对（对齐 P-04）

Worker #3 SelftestGate 是 HARD anchor, 紧跟 Worker #2 FunctionalTester 的 SOFT 判定后做门控; Worker #4 LLMReporter 是 SOFT anchor, 前面有 HARD 拦截保证输入确定性。符合 "SOFT 紧跟 HARD" 原则。

## 数据流 / 拓扑

```
selftest.request (kind.source)
  ↓
RegistryCheckerWorker (Worker #1)
  ↓ selftest.registry-report (kind.internal)
  ↓
FunctionalTesterWorker (Worker #2)
  ↓ selftest.selftest-report (kind.internal)
  ↓
SelftestGateWorker (Worker #3, HARD ANCHOR)
  ├── PASS → LLMReporter
  └── FAIL → HALT
  ↓
LLMReporterWorker (Worker #4, SOFT ANCHOR)
  ↓ selftest.health-report (kind.sink)
  ↓
EMIT (最终产出返回 CLI / CI)
```

线性 4 Worker, 无 fan-out / fan-in / feedback 循环。

## 已知局限

1. **LLM 降级不明显** — `llm_reporter` SOFT 在 LLM 不可用时静默降级为 PASS，health-report 里 `llm_ok=false` 但整体 PASS。若 CI 依赖 llm_ok, 需额外检查。升级路径: 加 optional 参数 `strict_llm=True` 让 LLM 失败升 FAIL。
2. **不覆盖业务 Team 内部** — 只能检测 Team 能 build, 不检测 Team 内部 Worker 是否在真实输入下能跑通。升级路径: 每个 Team 声明 smoke_test_request sample, selftest 跑一遍。

## 参考资料

- Team 代码:
  - [pipeline.py](pipeline.py) · [run.py](run.py) · [formats.py](formats.py)
  - [workers/](workers/) · 4 Worker 独立文件（Clean Migration 后结构）
- 规范引用（Stage 1 Team 2 迁移依据）:
  - [terminology.md §6/§7/§8](../../../../../../docs/standards/_global/terminology.md)
  - [format.md F-16 Material kind](../../../../../../docs/standards/concepts/material.md)
  - [router.md R-18 Worker 粒度 / R-19 Agent Worker / R-20 升级规则](../../../../../../docs/standards/concepts/worker.md)
  - [pipeline.md P-14 Workspace](../../../../../../docs/standards/concepts/team.md)
- 迁移记录:
  - migration_log.md Team 2
