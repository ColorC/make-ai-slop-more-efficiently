<!-- [OMNI] origin=claude-code domain=services/lap_auditor ts=2026-05-04T12:00:00Z type=doc status=active belongs_to_service=lap_auditor -->
<!-- [OMNI] material_id="material:diagnosis.lap_auditor.design_specification.md" -->

# lap_auditor · 设计文档

> 设计目的请看 [README.md](README.md). 怎么用请看 [SKILL.md](SKILL.md). 本文档专管**架构内部** (接口 / 决策 / 数据流 / 局限).

## 状态
- **版本**: V1 (Clean Migration 2026-04-21)
- **成熟度**: active
- **下一步**: 按需扩展审计维度 (如 Team/Material 规范检查)

## 核心接口

- [`workers/__init__.py`](workers/__init__.py) — 3 个 Worker：
  - `ContextGetterWorker` (ANCHOR) — 读取 .py 文件拼装代码上下文
  - `SpecAuditorWorker` (LLM) — 按四大红线审计代码，输出 Markdown 报告
  - `ReportFormatterWorker` (ANCHOR) — 格式化打印报告到控制台
- [`formats.py`](formats.py) — 4 个 Material 定义（source/internal/internal/sink）
- [`team.py`](team.py) — `build_team()` 返回 3 节点 TeamSpec

## 架构决策

### D1 三节点线性链路
ContextGetter → SpecAuditor → ReportFormatter。无分支，PASS 直穿，代码审计结果始终产出（即使审计结论是"缺陷代码"，Verdict 仍 PASS，结论在报告文本中）。

### D2 LLM 审计提示词固化在 _archive/routers_legacy.py
`_AUDITOR_SYSTEM_PROMPT` 是精心设计的四象限审计框架，不随版本迁移轻易修改，归档在 _archive 方便追溯。

### D3 Stage 3 独立文件架构
每个 Worker 独立一个文件（`workers/<name>_worker.py`），`workers/__init__.py` 仅 re-export。不使用 Diamond shortcut，`_archive/` 已删除（无遗留代码依赖）。旧 `from ...routers import XxxRouter` 通过 `routers.py` compat shim 继续工作。

### D4 铁律 A 修复 (SpecAuditorWorker)
原 `SpecAuditorRouter.run()` 有 `code_context[:80000]` 预防性截断（违反铁律 A）。Stage 3 迁移时在 `SpecAuditorWorker.run()` 中移除截断，LLM 接收完整代码上下文。

### D5 审计不阻塞管线
ReportFormatterWorker 始终返回 VerdictKind.PASS，不因审计结论为"缺陷代码"而阻塞链路。调用方负责解读 report 内容并决定后续行动。

## 数据流 / 拓扑

```
外部触发
  lap_auditor.input  (kind.source)
       │
       ▼
  ContextGetterWorker  ← 递归读取 target_path 下所有 .py 文件，拼装代码上下文
       │
  lap_auditor.context  (kind.internal)
       │
       ▼
  SpecAuditorWorker    ← LLM 按四大红线分类审计，输出 Markdown 报告
       │
  lap_auditor.report   (kind.internal)
       │
       ▼
  ReportFormatterWorker  ← 格式化打印到控制台，保留 report 字段供调用方读取
       │
  lap_auditor.done     (kind.sink)
```

## 已知局限

- **代码规模上限**：目录包含大量 .py 文件时 code_context 可能超过 LLM 上下文窗口。当前 ContextGetterWorker 全量读取（符合铁律 A），超限时由 LLM 或调用方分批处理。
- **审计准确性依赖 LLM**：SpecAuditorWorker 输出质量取决于 `qwen3.6-plus` 对 LAP 规范的理解。若误判，应通过调整 `_AUDITOR_SYSTEM_PROMPT` 提升准确率（而非绕过 LLM）。
- **不覆盖非 Python 代码**：当前仅读取 `.py` 文件；YAML/Markdown 等配置文件的 LAP 合规性不在审计范围内。

## 参考资料

- `docs/standards/pipeline.md` — LAP 规范原则
- `repair` 服务 — 与 lap_auditor 互补，负责自动修复 lap_auditor 发现的问题

## 新哲学对齐 (Stage 3 Clean Migration 2026-04-21)

| 旧世界 (Router/Format/Pipeline) | 新世界 (Worker/Material/Team) |
|---|---|
| `ContextGetterRouter` | `ContextGetterWorker` |
| `SpecAuditorRouter` | `SpecAuditorWorker` |
| `ReportFormatterRouter` | `ReportFormatterWorker` |
| `lap_auditor.input/context/report/done` (Format) | 同 id，Kind 标注补全 (Material F-19) |
| `PipelineSpec` / `build_pipeline()` | `TeamSpec` / `build_team()` |

迁移方式：Stage 3 完全搬迁（每个 Worker 一个独立文件，`_archive/` 已删除）+ `routers.py` 为 compat shim（仅别名 re-export）。
