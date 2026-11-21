---
name: pipeline_ci
description: omnicompany 管线 CI 审计 - 三节点全确定性串行扫 packages/ 下管线包, ErrorRouteAuditor + PipelineChecker 双检, critical>0 阻断 CI.
user-invocable: false
disable-model-invocation: false
---

<!-- [OMNI] origin=ai-ide domain=services/pipeline_ci ts=2026-05-04T14:35:00Z type=doc status=active agent=ai-ide belongs_to_service=pipeline_ci -->
<!-- [OMNI] summary="pipeline_ci 操作手册 — 跑 CI 审计的操作步骤 + 入口清单 + 故障排查" -->
<!-- [OMNI] why="按 self_narrative_three_files.md §六 模板严格写. DESIGN 偏架构, 缺'怎么用'段, 抽出独立 SKILL 让操作可定位" -->
<!-- [OMNI] tags=skill,pipeline_ci,how-to,ci -->
<!-- [OMNI] material_id="material:services._diagnosis.pipeline_ci.skill.operations_manual.md"-->

# pipeline_ci · 操作手册

> 设计目的请看 [README.md](README.md). 内部架构请看 [DESIGN.md](DESIGN.md).

---

## 适用范围

**用我**:
- CI 集成 — PR 跑一次看 packages/ 下管线包是否合规
- 想在 commit 前自查管线 (类似 pre-push hook)
- 想看哪些管线包有 critical 级别问题

**不用我**:
- 想做语义级深度检查 → 找 doctor (含 LLM 检查器, 慢但深)
- 想扫源码静态合规 → 找 guardian
- 想测框架本身能跑 → 找 selftest
- 想跑业务 Team → 直接 omni run <team_id>, 不归 pipeline_ci

## 前置条件

- omnifactory 已装 (`omni --help` 确认)
- 在 git repo 内 (PipelineChecker 需要静态扫源码)
- packages/ 下有管线包 (含 routers.py + pipeline.py)

## 操作步骤

### 场景 A · CI 集成 (PR gate)

```bash
omni run pipeline_ci
echo "Exit: $?"   # 0 = PASS (critical=0), 非 0 = FAIL (critical>0)
```

**验证**: 输出 `pipeline_ci.ci-report` 含每个管线包的 issue 列表 + critical_count + warning_count. CI 用 exit code 决定阻断 / 放行.

### 场景 B · 看具体某域审计结果

```bash
omni run pipeline_ci -i scope="packages/domains/demogame"
```

**用途**: 缩范围审计某 domain (默认全 packages/ 扫).

### 场景 C · 库调用

```python
from omnifactory.packages.services._diagnosis.pipeline_ci.pipeline import build_pipeline
from omnifactory.runtime.exec import PipelineRunner

team = build_pipeline()
runner = PipelineRunner(team)
result = runner.run({})
report = result.outputs["pipeline_ci.ci-report"]
print(f"critical: {report['critical_count']}")
```

## 入口清单

| 入口 | 用途 | 主要参数 |
|---|---|---|
| `omni run pipeline_ci` | 跑 CI 审计 | `-i scope` (默认全扫) |
| `build_pipeline()` (Python) | 库调用 | 见 [pipeline.py](pipeline.py) |
| 单 Worker 调用 (测试用) | 跑某段 | 见 [workers/](workers/) |

详细 CLI 规范: docs/standards/cli/omnicompany_cli.md

## 故障排查

| 现象 | 可能原因 | 怎么修 |
|---|---|---|
| ci-report 报某 Team 找不到 `routers.py` 或 `pipeline.py` | DomainScanner 扫不到该包必备文件 | 检查包是否真有 routers.py / pipeline.py; 或加 `__init__.py` 让 Python 识别为包 |
| ErrorRouteAuditor 报错 | `_lazy_import` 失败 (D4 容错降级 WARNING) | 看 BatchAuditor 输出, ErrorRouteAuditor 失败不阻塞 PipelineChecker |
| critical_count > 0 但看不出具体哪条违规 | ci-report 输出按 issue 聚合 | 看 ci-report 完整 JSON (用 `--json` 模式), 含每条 issue 的具体信息 |
| 跑得突然变慢 | packages/ 包数变多 | 当前确定性扫, 域多了线性变慢, 暂无并行优化 |
| 想看 ErrorRouteAuditor 在做什么 | 它来自 workflow_factory (跨 Team 直调, 局限 2) | 看 ../../_core/workflow_factory/ 里 ErrorRouteAuditor 实现 |
| ci-report 既被 CIGate 消费又被产出 (D3 直通) | CIGate 用 FORMAT_IN = FORMAT_OUT, Verdict.kind 决定 PASS/FAIL | 这是当前简化设计 (局限 1), 升级路径独立拆 `pipeline_ci.gate-result` |
| 在 CI 里 exit code 不对 | omni run 默认 0/1 不细分 | 用 `omni run pipeline_ci || exit 1` 显式判 |

## 想了解更多

- 设计目的 → [README.md](README.md)
- 内部架构 (D1-D5 决策 / 3 Worker 数据流) → [DESIGN.md](DESIGN.md)
- 跟 doctor 关系 → ../doctor/SKILL.md (语义级深度检查 vs CI gate)
- 跨 Team 依赖 ErrorRouteAuditor → ../../_core/workflow_factory/
