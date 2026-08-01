<!-- [OMNI] origin=ai-ide domain=root type=doc status=active -->

# OmniCompany — Make AI Slop More Efficiently

[![CI](https://github.com/ColorC/make-ai-slop-more-efficiently/actions/workflows/ci.yml/badge.svg)](https://github.com/ColorC/make-ai-slop-more-efficiently/actions/workflows/ci.yml)
![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue)
![License: MIT](https://img.shields.io/badge/license-MIT-green)

> AI 原生的软件工厂：给命令行 AI agent 一个**声明清晰、明文可读、全程留痕、能自我诊断修复**的工作环境。
> LLM 是引擎，omnicompany 是工厂。

## ⚠️ 先说清楚

**这是一个自用软件。** 它是我（一个人 + 一群 AI agent）日常工作的核心基础设施，不是为"别人拿来就能跑"设计的。

- 我**不保证**它能在你的电脑上良好运行。事实上大概率不能——它深度集成了一堆我个人工作流里的 agent adapter（Claude Code / Codex / Kimi / OpenCode）、一个 FastAPI dashboard、一个 React 前端、一个 vendored 的 ClaudeCodeUI，以及若干还在实验阶段的治理/决策/学习管线。
- 但它**能跑起来**。`pip install -e . && omni --help` 不需要任何 API key 就能用。核心 CLI + 框架层是稳定的。
- 我**欢迎你**：提 bug fix PR、提 feature PR、或者单纯开一个 issue 命令我去修某个 bug。对，命令我。这是你的权利。

## 这是什么

omnicompany 把"AI agent 怎么工作 / 怎么协作 / 怎么自我修复"这套**很容易随时间漂移失控**的事情，拆成显式的几类构件：

- **Material**（物料）：数据契约，由 schema + 描述声明
- **Worker**（工人）：单职责处理单元，订阅特定 Material、产出特定 Material
- **Team**（团队）：Worker 的拓扑组合，跑端到端工作流（= 一条"管线"）
- **Hook**（钩子）：周期 / 事件驱动的旁路触发
- **Tool**（工具）：Worker 内调用的原子能力
- **Agent**（代理）：多轮 tool-loop 的复合工人

每个文件 / 模块都带可追溯的头注释（OmniMark），配合统一的事件总线全程留痕——让 AI agent 不再黑箱跑，有问题能查、漂移有抓手。

## 快速开始

```bash
git clone https://github.com/ColorC/make-ai-slop-more-efficiently.git
cd make-ai-slop-more-efficiently
pip install -e .
```

跑几条不需要任何 key 的本地命令确认装好：

```bash
omni --help              # 命令总览
omni health              # 系统自检
omni guardian patrol     # 目录健康巡逻(结构/头注释漂移扫描)
omni research --help     # 看一个内置域(公开调研管线)
```

需要 LLM 的命令，在仓库根目录建一个 `.env`（见 [.env.example](.env.example)）配好 API key 即可；纯本地命令不需要。

## 项目结构

```
src/omnicompany/
├── core/         # 注册中心、身份、Guardian(目录/架构健康)、配置解析
├── bus/          # 事件总线（SQLite / Redis / 内存）
├── protocol/     # Material / Worker / Team / Anchor 协议定义
├── runtime/      # agent loop、LLM 客户端、执行图、路由、信号
├── cli/          # omni 命令入口（60+ 子命令）
├── dashboard/    # 可选 Web UI（FastAPI + React + vendored ClaudeCodeUI）
│   ├── controlplane/   # ~50 个 API 路由
│   ├── ccdaemon/       # Claude Code / Codex / Kimi / OpenCode 会话守护进程
│   ├── boss_sight/     # 总控驾驶舱（项目工作板、审阅台、控制器）
│   ├── frontend/       # React + Vite SPA
│   └── chatui/         # vendored ClaudeCodeUI (Node.js)
├── packages/
│   ├── domains/  # 领域插件（内置: research / decisions / software_engineering / publish / slidecast / project_atlas / frontend_design）
│   └── services/ # 基础设施服务（guardian / doctor / repair / team_builder / governance / learning / authoring）
└── tools/        # 步进调试器等开发工具
```

## 内置领域

| 领域 | 做什么 |
|---|---|
| `research` | 公开调研管线：多视角拆题 → 并行联网 → 综合 + 源核查 → 库累积 |
| `decisions` | 决策记录库：多源决策 → 统一契约 → 可搜索决策树 |
| `software_engineering` | 软件工程多阶段管线（plan → design → tdd → implement → review → verify） |
| `publish` | 发布 / 快照 / 备份治理 |
| `slidecast` | AIGC 演示式讲解生成 |
| `project_atlas` | 项目收集、健康检查 |
| `frontend_design` | UI/UX 审计与综合 |

加你自己的领域：`packages/domains/<name>/` 下写一个 `team.py`（拓扑）+ `DESIGN.md`（意图），照着 `research` 抄。`omni run <domain>.<pipeline>` 跑。

## Agent 适配器

Dashboard 的 ccdaemon 内置了 7 个外部 AI coding agent 的适配器：

| Agent | 说明 |
|---|---|
| Claude Code | Anthropic 的 Claude Code CLI |
| Codex | OpenAI Codex CLI |
| Kimi | Moonshot AI Kimi CLI |
| OpenCode | sst OpenCode CLI |
| CodeBuddy | Tencent CodeBuddy |
| Omni Agent | omnicompany 原生 agent |
| Omni Agent CLI | CLI 变体 |

装了哪个 CLI 就能用哪个，没装的优雅降级。

## Dashboard

仓库包含 Dashboard 的 FastAPI 后端、React/Vite 前端、ccdaemon 会话守护进程和已构建的静态资源。

```bash
pip install -e ".[dashboard]"
python scripts/start_dashboard_dev.py --no-reload
# open http://127.0.0.1:8200/
```

Dashboard 与 ccdaemon 是两个独立进程：重启 Dashboard 不会故意终止已打开的 CLI 会话。
详见 [`src/omnicompany/dashboard/PROJECT_INDEX.md`](src/omnicompany/dashboard/PROJECT_INDEX.md)。

## omni-recover（Alpha）

`omni-recover` 可以从 Codex、Claude Code、Kimi Code 和 OpenCode 的本机会话证据中，
按时序查找并恢复可验证的文件候选：

```bash
omni-recover sources
omni-recover providers
omni-recover plan --help
```

它是技术预览，不是云备份替代品。归档内容可能包含源码、提示词、路径和工具输出，
默认内容寻址但不由 omni-recover 加密。使用前请阅读
[`packages/omni-recover/README.md`](packages/omni-recover/README.md)。

## 实验性模块（标注：不成熟）

以下模块处于早期设计阶段，正在朝**全云办公 → 持续学习 → 形成稳定复杂决策管线 → 复用决策形成决策方法**的路线演进：

- **治理管家群**（`services/_governance/`）：commit / doc / plan / progress / prose steward — 各类自动化管家
- **决策体系**（`domains/decisions/`）：多源决策记录 + 探索性因果抽取
- **假设体系**（`services/_learning/hypothesis*/`）：已开启设计和初步尝试，但目前还没有起效
- **Guardian**（`services/_core/guardian/`）：目录健康 / 架构漂移巡逻 — 这个倒是已经比较稳定了
- **Team Supervisor**（`services/_core/team_supervisor/`）：用 7 节点管线审计另一个管线的健康度

这些模块的 API 随时会变。用之前做好心理准备。

## 了解更多

| 想知道 | 看 |
|---|---|
| 整体架构 / 怎么加你自己的领域 | [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) |
| 命令行用法 | `omni --help` |
| 各种规范（Material / Worker / Team / 头注释等） | [docs/standards/](docs/standards/) |
| 怎么贡献 | [CONTRIBUTING.md](CONTRIBUTING.md) |

## 相关仓库

| 仓库 | 说明 |
|---|---|
| [make-ai-slop-remote-efficiently](https://github.com/ColorC/make-ai-slop-remote-efficiently) | LOFA — 局域网 Android 远程客户端，从手机远看本机 omnicompany 的一切 |
| [summon-the-slop](https://github.com/ColorC/summon-the-slop) | Overlay Shell — 桌面透明悬浮窗，Ctrl+Alt 召唤的全屏工作台 |

## License

MIT — 见 [LICENSE](LICENSE)。
