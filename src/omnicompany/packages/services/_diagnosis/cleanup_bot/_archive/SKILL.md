---
name: cleanup_bot
description: omnicompany 系统环境异常清理 service - 给 AI agent 路径拼接错误产生的错位嵌套垃圾目录跑 LLM 判断 + 生成 PowerShell 清理脚本, 只打印不自动删除.
user-invocable: false
disable-model-invocation: false
---

<!-- [OMNI] origin=ai-ide domain=services/cleanup_bot ts=2026-05-04T13:05:00Z type=doc status=active agent=ai-ide belongs_to_service=cleanup_bot -->
<!-- [OMNI] summary="cleanup_bot 操作手册 — 跑磁盘扫描 + LLM 判 + 生成 PowerShell 清理脚本的操作步骤 + 入口清单 + 故障排查" -->
<!-- [OMNI] why="按 self_narrative_three_files.md §六 模板严格写. DESIGN 偏架构, 缺'怎么用'段, 抽出独立 SKILL 让操作可定位" -->
<!-- [OMNI] tags=skill,cleanup_bot,how-to,diagnosis -->
<!-- [OMNI] material_id="material:services._diagnosis.cleanup_bot.skill.operations_manual.md"-->

# cleanup_bot · 操作手册

> 设计目的请看 [README.md](README.md). 内部架构请看 [DESIGN.md](DESIGN.md).

---

## 适用范围

**用我**:
- 怀疑宿主机有 AI agent 误触产生的垃圾目录 (例 `E:\e\workspace` 这种嵌套错位)
- 想得到 PowerShell 清理脚本 (用户手动审过再执行)
- 大致知道"垃圾在哪个区域"(给 root_dir + keyword)

**不用我**:
- 自动删文件 (本 service 设计就**不删**, 只产计划由用户跑)
- 非路径异常 (注册表 / 进程残留 / 内存泄漏 / 等)
- 项目内合规 → 找 doctor / guardian / lap_auditor

## 前置条件

- omnifactory 已装 (`omni --help` 确认)
- 有 `THE_COMPANY_API_KEY` (AnomalyDetectorWorker 调 qwen-3.6-plus)
- 在 Windows (产出是 PowerShell 脚本); 其他 OS 也能跑但脚本要手工调
- 知道大致 root_dir (例 `E:\` 或 `D:\`) + keyword (例 `e` / `workspace`)

## 操作步骤

### 场景 A · 扫某磁盘根, 找含某 keyword 的可疑路径

```bash
omni run cleanup_bot -i root_dir="E:\\" -i keyword="e"
```

**输出**: Markdown 报告含 PowerShell 脚本, 例如:

```powershell
# 清理建议 (用户审过执行):
Remove-Item -Path "E:\e\workspace" -Recurse -Force
Remove-Item -Path "E:\e\foo\bar" -Recurse -Force
```

**验证**: 不要直接 Run 输出的 PowerShell 脚本 — 先肉眼审一遍, 确认每条 Remove-Item 的路径都是真垃圾.

### 场景 B · 扫工作区找特定 keyword 嵌套

```bash
omni run cleanup_bot -i root_dir="D:\workspace" -i keyword="workspace"
```

**用途**: 工作区里出现的奇怪嵌套 (例 `D:\workspace\workspace\workspace\...`).

### 场景 C · 库调用

```python
from omnifactory.packages.services._diagnosis.cleanup_bot.team import build_team
from omnifactory.runtime.exec import PipelineRunner

team = build_team()
runner = PipelineRunner(team)
result = runner.run({"root_dir": "E:\\", "keyword": "e"})
plan = result.outputs["cleanup.plan"]["anomaly_report"]  # Markdown
```

## 入口清单

| 入口 | 用途 | 主要参数 |
|---|---|---|
| `omni run cleanup_bot` | 跑扫描 + 判断 + 计划 | `-i root_dir` `-i keyword` |
| `build_team()` (Python) | 库调用 | 见 [team.py](team.py) |
| `EvidenceGathererWorker` / `AnomalyDetectorWorker` / `RollbackPlannerWorker` | 单 Worker 调用 (测试用) | 见 [workers/](workers/) |

详细 CLI 规范: docs/standards/cli/omnicompany_cli.md

## 故障排查

| 现象 | 可能原因 | 怎么修 |
|---|---|---|
| 报 THE_COMPANY_API_KEY 缺失 | 环境变量没设 | 配 `~/.env` `THE_COMPANY_API_KEY=...` |
| EvidenceGatherer 返回 FAIL 中止 | 没扫到任何含 keyword 的路径 | 检查 keyword 是否拼对; 或换更宽松的 keyword |
| 扫描超时 / 内存爆 | root_dir 太大 + max_depth=5 仍递归过深 | 当前局限 (D2), 缩小 root_dir 范围 |
| LLM 误判正常目录为垃圾 | LLM 对路径模式理解有偏差 | **绝不直接跑输出脚本**, 肉眼审; 调 `_CLEANUP_SYSTEM_PROMPT` 加更多反例 |
| LLM 漏判真垃圾 | 路径模式不像典型 AI 误触 | 当前只检测路径名模式, 内容感知是局限 (D5 + 已知局限 2) |
| PowerShell 脚本里含奇怪命令 | LLM 自由发挥 | RollbackPlanner 只打印, 用户审; 异常情况报 issue 调 prompt |
| 想多 keyword 一次扫 | 当前不支持 | 局限 3, 多次调 / 改 EvidenceGathererWorker 加循环 |
| 已知是 keyword 但 EvidenceGatherer 跳过某些路径 | 受 max_depth=5 保护 | D2 设计, 改默认深度需改 worker 代码 |

## 想了解更多

- 设计目的 → [README.md](README.md)
- 内部架构 (D1-D5 决策 / 数据流) → [DESIGN.md](DESIGN.md)
- 类似设计模式 → ../lap_auditor/SKILL.md (同三节点 ANCHOR-LLM-ANCHOR 链路)
