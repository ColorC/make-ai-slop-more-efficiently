<!-- [OMNI] origin=claude-code domain=services/pipeline_ci ts=2026-05-04T14:30:00Z type=doc status=active belongs_to_service=pipeline_ci -->
<!-- [OMNI] material_id="material:diagnosis.pipeline_ci.design_specification.md" -->

# pipeline_ci · 设计文档

> 设计目的请看 [README.md](README.md). 怎么用请看 [SKILL.md](SKILL.md). 本文档专管**架构内部** (接口 / 决策 / 数据流 / 局限).

## 状态
- **版本**: V1 (Phase D Clean Migration 2026-04-20)
- **成熟度**: active
- **下一步**: 可扩展更多 Auditor（如新世界 Worker/Material 合规检查）

## 核心接口

- [workers/domain_scanner_worker.py](workers/domain_scanner_worker.py) — `DomainScannerWorker`
- [workers/batch_auditor_worker.py](workers/batch_auditor_worker.py) — `BatchAuditorWorker`
- [workers/ci_gate_worker.py](workers/ci_gate_worker.py) — `CIGateWorker`
- [workers/__init__.py](workers/__init__.py) — `ALL_WORKERS`
- [formats.py](formats.py) — 3 个 Material 定义
- [pipeline.py](pipeline.py) — `build_pipeline()` 管线装配
- [run.py](run.py) — 手动触发入口

## 架构决策

### D1 — 全确定性三节点串行

三个 Worker 均为确定性（不调 LLM），串行激活。BatchAuditorWorker 内部循环遍历每个域，不需要并行激活（域数量少，单线程足够）。

### D2 — ErrorRouteAuditor + PipelineChecker 双检

BatchAuditorWorker 内部调两个审计器：
- `ErrorRouteAuditorRouter`（来自 workflow_factory）检查 Router 元数据/错误路由
- `PipelineChecker`（来自 protocol.pipeline）做静态类型检查

未来可插拔更多 Auditor。

### D3 — CIGate FORMAT_IN = FORMAT_OUT 直通模式

CIGateWorker 的 FORMAT_IN 和 FORMAT_OUT 都是 `pipeline_ci.ci-report`，输出时只改 Verdict.kind（PASS/FAIL）而不改 output 结构。此设计简化管线，但在新世界语义下 ci-report 同时是 consumer（CIGate 订阅它）和 producer（CIGate 输出它）—— 属于 kind.internal 范畴，见"新哲学对齐"节。

### D4 — _lazy_import 容错

BatchAuditorWorker 用 `_lazy_import()` 软导入 `build_pipeline()` 函数，失败降级为 WARNING（不影响 ErrorRouteAuditor 的 CRITICAL 判断）。

### D5 — Clean Migration (Phase D 2026-04-20)

已从 routers.py 单文件迁到 workers/ 目录，`routers.py` 保留为兼容垫片（别名 *Router → *Worker）。原代码归档到 `_archive/routers_legacy.py`。

## 数据流 / 拓扑

```
scan-request (source)
  → DomainScannerWorker
  → domains (internal)
  → BatchAuditorWorker
  → ci-report (internal)
  → CIGateWorker
  → ci-report (passthrough, Verdict.kind=PASS|FAIL)
```

## 已知局限

1. **CIGate FORMAT_IN = FORMAT_OUT 直通** — ci-report 既被 CIGate 消费又被产出，语义上应拆为独立 `pipeline_ci.gate-result`（kind.sink）。当前直通方式简化代码但混淆 Material 生命周期。**升级路径**: 独立 plan 拆分，低优先级。

2. **ErrorRouteAuditor 来自 workflow_factory** — BatchAuditorWorker 内部 import `from workflow_factory.routers import ErrorRouteAuditorRouter`，跨 Team 直调（不走 MaterialDispatcher）。**升级路径**: 待新 Runtime 成熟后，workflow_factory 的审计能力应作为 Material 提供，pipeline_ci 订阅消费。

## 新哲学对齐（Phase D · 2026-04-20）

> 对照 13 条新世界条款逐项评估。

### Material 层（F-16/17/18/19）

| 条款 | 状态 | 说明 |
|---|---|---|
| F-16 kind 三分 | ✅ | scan-request=source (外部触发); domains=internal (DomainScanner→BatchAuditor); ci-report=internal (BatchAuditor→CIGate, CIGate 消费后也产出，见 D3) |
| F-17 Workspace 大明文 | N/A | 无大 payload，审计结果为结构化小 dict |
| F-18 Job × Material 绑定 | N/A | 传统 pipeline 模式，MaterialDispatcher 链路待新 Runtime |
| F-19 kind.* tag 必填 | ✅ | Phase D 修正：3 条 Material 全部补 kind.* |

### Worker 层（R-18~R-25）

| 条款 | 状态 | 说明 |
|---|---|---|
| R-18 粒度 | ✅ | 3 Worker 各有完整职责 + FORMAT 边界，独立可测 |
| R-19 Agent Worker 升级 | N/A | 全确定性，无动态 Material 需求 |
| R-20 Agent Worker 三件套 | N/A | 同上 |
| R-21 Diagnosis Agent Worker | N/A | pipeline_ci 是 CI 工具，不需对上游质疑 |
| R-22 WorkspaceWriterWorker | N/A | 无 workspace 文件写入 |
| R-23 Verdict.output 平铺 | ✅ | 3 Worker 输出均无嵌套 format_id 包裹 |
| R-24 FORMAT_IN_MODE | N/A | 所有 Worker FORMAT_IN 为单 str |
| R-25 子 job | N/A | 无 _emit_as_new_job |

### Team 层（P-13~P-17）

| 条款 | 状态 | 说明 |
|---|---|---|
| P-13 声明即消费 | ✅ | 各 Worker 只消费 FORMAT_IN 声明的 Material |
| P-14~17 Workspace 目录 | N/A | 无 workspace 目录约定 |

**结论**: 全面对齐。F-19 缺口已修正，Clean Migration 完成（workers/ 拆分 + kind.* + _archive/）。

## 参考资料

- [workers/](workers/) — 3 个 Worker 实现
- [formats.py](formats.py) — 3 个 Material 定义
- _archive/routers_legacy.py — 原 Router 归档（迁移前）
- ../workflow_factory/routers.py — ErrorRouteAuditorRouter (跨 Team 依赖)
