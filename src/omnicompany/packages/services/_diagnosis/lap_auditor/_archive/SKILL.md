---
name: lap_auditor
description: omnicompany LAP 协议合规审计 service - 给 Python 代码按四大红线 (事件总线驱动/Format 真实性/接口规范实现/Domain 隔离) 跑 LLM 审计, 拿 Markdown 报告.
user-invocable: false
disable-model-invocation: false
---

<!-- [OMNI] origin=ai-ide domain=services/lap_auditor ts=2026-05-04T12:05:00Z type=doc status=active agent=ai-ide belongs_to_service=lap_auditor -->
<!-- [OMNI] summary="lap_auditor 操作手册 — 跑 LAP 审计的操作步骤 + 入口清单 + 故障排查" -->
<!-- [OMNI] why="按 self_narrative_three_files.md §六 模板严格写. DESIGN 偏架构, 缺'怎么用'段, 抽出独立 SKILL 让操作可定位" -->
<!-- [OMNI] tags=skill,lap_auditor,how-to,diagnosis -->
<!-- [OMNI] material_id="material:services._diagnosis.lap_auditor.skill.operations_manual.md"-->

# lap_auditor · 操作手册

> 设计目的请看 [README.md](README.md). 内部架构请看 [DESIGN.md](DESIGN.md).

---

## 适用范围

**用我**:
- 想知道某段 Python 代码是否符合 LAP 协议 (事件总线驱动 / Format 真实性 / 接口规范实现 / Domain 隔离)
- 重构前先扫一遍代码看哪部分该挪到哪
- CI 集成 — 给新提交的 .py 跑一遍 LAP 审计

**不用我**:
- 自动修代码 → 找 [services/repair/](../../) (lap_auditor 只产报告不动代码)
- 看单个 Format/Worker 健康 → 找 doctor service
- 看源码静态合规 (位置/命名/头) → 找 guardian service
- 审计非 Python 代码 → 当前不支持

## 前置条件

- omnifactory 已装 (`omni --help` 确认)
- 有 `THE_COMPANY_API_KEY` (SpecAuditorWorker 调 qwen-3.6-plus 走 the_company 聚合 API)
- target_path 是合法目录或 `.py` 文件 (相对项目根)
- 大目录 (千+ `.py`) 注意 LLM context 窗口

## 操作步骤

### 场景 A · 审计单个目录的 LAP 合规

```bash
omni run lap_auditor -i target_path="src/omnifactory/packages/services/_authoring/docauthor"
```

**验证**: Verdict.PASS + report 字段含 Markdown 文本, 内容按四大红线分类 (规范 LAP / 有缺陷 LAP / 绕过 LAP / 基础设施代码).

### 场景 B · 审计单个 .py 文件

```bash
omni run lap_auditor -i target_path="src/omnifactory/packages/services/_authoring/docauthor/run.py"
```

**用途**: 单文件粒度审计, 适合 git diff 后只审改动文件.

### 场景 C · 库调用 (作为其他 service 的依赖)

```python
from omnifactory.packages.services._diagnosis.lap_auditor.team import build_team
from omnifactory.runtime.exec import PipelineRunner

team = build_team()
runner = PipelineRunner(team)
result = runner.run({"target_path": "src/omnifactory/packages/..."})
report = result.outputs["lap_auditor.report"]["report"]  # Markdown
```

## 入口清单

| 入口 | 用途 | 主要参数 |
|---|---|---|
| `omni run lap_auditor` | 跑审计 Team | `-i target_path` |
| `build_team()` (Python) | 库调用 | 见 `team.py` |
| `ContextGetterWorker` / `SpecAuditorWorker` / `ReportFormatterWorker` | 单 Worker 调用 (测试用) | 见 `workers/` |

详细 CLI 规范: docs/standards/cli/omnicompany_cli.md

## 故障排查

| 现象 | 可能原因 | 怎么修 |
|---|---|---|
| 报 THE_COMPANY_API_KEY 缺失 | 环境变量没设 | 配 `~/.env` `THE_COMPANY_API_KEY=...` |
| LLM 报 context 超限 | target_path 下 .py 太多, 全量读超过 LLM 窗口 | 当前局限, 拆子目录分批跑 (按 service 粒度) |
| 报告分类粗糙 / 误判 | LLM 对 LAP 规范理解有偏差 | 调 `_AUDITOR_SYSTEM_PROMPT` (在 `_archive/routers_legacy.py`), 不绕 LLM 用规则替代 |
| 报告里没指出明显违规 | LLM 没看到完整代码 | 检查 ContextGetterWorker 是否真递归读全所有 .py |
| 跑得慢 | 大目录 + 单次 LLM 调用 | 当前 3 节点线性, 慢但准. 加速可拆子任务并行 (Phase 2) |
| Verdict 一直 PASS 即使审计结论是缺陷 | D5 决策: 审计不阻塞管线 | 这是设计, 调用方读 report 字段决策, 不靠 Verdict 区分 |

## 想了解更多

- 设计目的 → [README.md](README.md)
- 内部架构 (D1-D5 决策 / 数据流) → [DESIGN.md](DESIGN.md)
- LAP 规范权威 → docs/standards/pipeline.md
- 跟 repair 互补 → ../../repair/
- 跟 doctor / guardian 分工 → ../doctor/SKILL.md / ../../_core/guardian/SKILL.md
