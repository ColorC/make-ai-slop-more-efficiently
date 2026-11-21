<!-- [OMNI] origin=ai-ide domain=docs type=doc status=active -->

# Architecture

omnicompany 是一个 AI 原生的软件工厂：给 LLM 一个"显式声明、明文可读、全程留痕"的工作环境。
LLM 是引擎，omnicompany 是工厂。本文描述这个发布版的组织方式。

## 构件模型

一切都拆成可声明、可注册、可观测的构件：

| 构件 | 是什么 |
|---|---|
| **Material** | 数据契约（schema + 描述）。Worker 之间只通过 Material 交换。 |
| **Worker** | 单职责处理单元：订阅特定 Material、产出特定 Material。 |
| **Team** | Worker 的拓扑组合，跑端到端工作流（= 一条"管线"）。 |
| **Hook** | 周期 / 事件驱动的旁路触发。 |
| **Tool** | Worker 内调用的原子能力。 |
| **Agent** | 多轮 tool-loop 的复合 Worker。 |

每个文件 / 模块带可追溯的头注释（OmniMark），配合统一事件总线全程留痕——让 agent 不黑箱跑，漂移有抓手。

## 分层

```
src/omnicompany/
├── core/         # 注册中心、身份、Guardian(目录/架构健康)、自检、修复
├── runtime/      # 事件总线、agent loop、执行图、信号
├── protocol/     # Material / Worker / Team 的协议定义
├── cli/          # omni 命令入口
├── dashboard/    # 可选 Web UI（pip install -e ".[dashboard]"）
└── packages/
    ├── domains/  # 一个个领域（业务层）——自带电池
    └── services/ # 基础设施服务（_core / _diagnosis / _governance / _learning / _utility）
```

- **框架不进业务**：通用能力在 `core/` `runtime/` `packages/services/`；具体领域在 `packages/domains/`。
- **事件总线 + OmniMark**：执行落事件、文件带头注释，是"可观测 / 可追溯"的两条腿。
- **Guardian**：启动自检 + 巡逻，扫目录健康 / 架构漂移 / 头注释合规（`omni guardian patrol`）。

## 自带电池的领域

发布版内置几个通用领域，开箱即用，也是"照着加你自己的"的范例：

| 领域 | 做什么 |
|---|---|
| `research` | 公开调研管线：多视角拆题 → 并行联网 → 综合 + 源核查 → 库累积。 |
| `decisions` | 决策记录库：多源决策 → 统一契约 → 可搜索决策树。 |
| `software_engineering` | 软件工程多阶段管线（plan → design → tdd → implement → review → verify）。 |
| `publish` | 发布 / 脱敏 / 备份治理。 |

## 加你自己的领域

领域是 `packages/domains/<name>/` 下的一个包：一个 `team.py`（定义 Team = 管线拓扑）+ `DESIGN.md`（设计意图）+ 可选 `run.py`。
照着 `research` 写一个同形的包即可；框架按领域名调度（`omni run <domain>.<pipeline>`）。

## 命令

```bash
omni --help              # 命令总览
omni health              # 系统自检
omni guardian patrol     # 目录健康巡逻
omni run research.run --topic "..."   # 跑一个领域管线（需要 LLM key）
```

规范（Material / Worker / Team / 头注释约定等）见 [standards/](standards/)。
