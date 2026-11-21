---
name: selftest
description: omnicompany 自测套件 - 跑端到端冒烟 (注册体系/Stock/CLI/LLM 连通性), 给 CI / 人 PASS/FAIL gate.
user-invocable: false
disable-model-invocation: false
---

<!-- [OMNI] origin=ai-ide domain=services/selftest ts=2026-05-04T13:45:00Z type=doc status=active agent=ai-ide belongs_to_service=selftest -->
<!-- [OMNI] summary="selftest 操作手册 — 跑自测的操作步骤 + 入口清单 + 故障排查" -->
<!-- [OMNI] why="按 self_narrative_three_files.md §六 模板严格写. DESIGN 偏架构, 缺'怎么用'段, 抽出独立 SKILL 让操作可定位" -->
<!-- [OMNI] tags=skill,selftest,how-to,core -->
<!-- [OMNI] material_id="material:services._core.selftest.skill.operations_manual.md"-->

# selftest · 操作手册

> 设计目的请看 [README.md](README.md). 内部架构请看 [DESIGN.md](DESIGN.md).

---

## 适用范围

**用我**:
- 想知道 omnicompany 框架本身能不能正常跑 (注册中心 / Stock / CLI / LLM 连通性)
- CI 集成 — 给 PR 跑一次冒烟看框架健康
- 人工 onboard 后第一次跑确认环境 OK

**不用我**:
- 测业务正确性 → 找各 domain Team 的测试
- 测 Format/Worker/Team 单对象语义 → 找 [doctor](../../_diagnosis/doctor/SKILL.md)
- 测源码合规 → 找 [guardian](../guardian/SKILL.md)
- 测协议 / 语义合规 → 找 lap_auditor / semantic_auditor
- 测性能基准 → 找 benchmark Team

## 前置条件

- omnicompany 已装 (`omni --help` 确认)
- 有 `THE_COMPANY_API_KEY` (LLM 连通性测试要走 qwen-3.6-plus)
- omnicompany 注册中心已 build (老仓 / 新仓首次需要先 `omni registry rebuild`)

## 操作步骤

### 场景 A · 跑一次完整自测

```bash
omni run selftest
```

**验证**: 输出 `selftest.health-report` (Markdown), 内容含:
- registry 报告 (能 build 的 Team 数 / 失败列表)
- 功能冒烟结果 (Stock 读写 / CLI / LLM)
- gate 决定 (PASS / FAIL)
- LLM 摘要 (人类友好 summary)

### 场景 B · CI 集成

```bash
omni run selftest > selftest_report.md
echo "Exit: $?"   # 0 = PASS, 非 0 = FAIL
```

**用途**: PR 跑 selftest, FAIL 即阻断合并.

### 场景 C · 库调用

```python
from omnicompany.packages.services._core.selftest.team import build_team
from omnicompany.runtime.exec import PipelineRunner

team = build_team()
runner = PipelineRunner(team)
result = runner.run({})
report = result.outputs["selftest.health-report"]
```

## 入口清单

| 入口 | 用途 | 主要参数 |
|---|---|---|
| `omni run selftest` | 跑自测 | (无, 默认全跑) |
| `build_team()` (Python) | 库调用 | 见 [team.py](team.py) |
| 单 Worker 调用 (测试用) | 跑某段 | 见 [workers/](workers/) |

详细 CLI 规范: docs/standards/cli/omnicompany_cli.md

## 故障排查

| 现象 | 可能原因 | 怎么修 |
|---|---|---|
| selftest 报某 Team build 失败 | 该 Team 的 `build_pipeline()` 抛错 | 看错误堆栈, 去对应 Team `pipeline.py` 修 |
| Stock 读写测失败 | EventBus 配置错或 SQLite 路径不存在 | 检查 omnicompany 配置 / 数据库初始化 |
| LLM 连通性测失败 | THE_COMPANY_API_KEY 没设或网络问题 | 配 `~/.env` `THE_COMPANY_API_KEY=...`; 测 `curl` 能否到 LLM 服务 |
| selftest 整体 PASS 但 health-report 含 `llm_ok=false` | LLMReporter SOFT 静默降级了 (LLM 不可用但不 FAIL) | 当前局限 (D5), 想严格 FAIL 加 `strict_llm=True` (Phase 2 backlog) |
| selftest 跑很慢 | LLM 调用 + 大量 Team build | 跳过 LLM 部分 (本 service 暂不支持参数化, 见 D3 局限) |
| 注册中心扫不全 | `omni registry rebuild` 还没跑 | 先跑 rebuild, 再跑 selftest |
| selftest 通过但业务 Team 跑挂 | selftest 不覆盖业务 Team 内部 | 当前局限 (局限 2), 业务 Team 自测 / 找 doctor |

## 想了解更多

- 设计目的 → [README.md](README.md)
- 内部架构 (D1-D5 决策 / 4 Worker 数据流) → [DESIGN.md](DESIGN.md)
- 跟 doctor / guardian / lap_auditor / semantic_auditor 关系 → 项目根 [README.md `## 构成`](../../../../../../README.md)
- LLM 连通性配置 → [docs/standards/cli/llm_infrastructure.md](../../../../../../docs/standards/cli/llm_infrastructure.md)
