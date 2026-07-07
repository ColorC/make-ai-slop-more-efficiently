<!-- [OMNI] origin=ai-ide domain=root type=doc status=active -->

# OmniCompany

[![CI](https://github.com/ColorC/make-ai-slop-more-efficiently/actions/workflows/ci.yml/badge.svg)](https://github.com/ColorC/make-ai-slop-more-efficiently/actions/workflows/ci.yml)
![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue)
![License: MIT](https://img.shields.io/badge/license-MIT-green)

> AI 原生的软件工厂：让 AI agent 在一个"严密声明、明文可读"的环境里持续工作、协作、自我诊断与修复。

## 这是什么

omnicompany 把"AI agent 怎么工作 / 怎么协作 / 怎么自我修复"这套**很容易随时间漂移失控**的事情，拆成显式的几类构件：

- **Material**（物料）：数据契约，由 schema + 描述声明
- **Worker**（工人）：单职责处理单元，订阅特定 Material、产出特定 Material
- **Team**（团队）：Worker 的拓扑组合，跑端到端工作流
- **Hook**（钩子）：周期 / 事件驱动的旁路触发
- **Tool**（工具）：Worker 内调用的原子能力
- **Agent**（代理）：多轮 tool-loop 的复合工人

每个文件 / 模块都带可追溯的头注释，配合统一的事件总线全程留痕——让 AI agent 不再黑箱跑，有问题能查、漂移有抓手。它不替代 LLM，而是给 LLM 一个可信任的工作环境：LLM 是引擎，omnicompany 是工厂。

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

也可以用脚本一键安装（优先 pipx，退化 pip）：

```bash
bash scripts/install.sh                                       # macOS / Linux
powershell -ExecutionPolicy Bypass -File scripts/install.ps1  # Windows
```

## 解决什么 / 不解决什么

**解决：**

- AI agent 长时间跑着跑着失控 —— 用显式契约 + 事件落盘 + 守护规则把漂移侦测出来
- 多个 agent / 工作流互相影响难调试 —— 用统一注册中心 + 身份追溯查清"谁动的"
- agent 写代码 / 文档不合规范 —— 用沙盒 + 守护规则做约束
- 项目设计意图随时间丢失 —— 用 README + DESIGN + SKILL 三件套让认知可定位

**不解决：**

- 让 LLM 本身变聪明（那是模型供应商的事）
- 替代具体业务代码（业务归 `packages/domains/`，框架不进业务）
- 通用对话 chatbot（它是工厂不是助手）

## 项目结构

按层组织：

- **核心层** —— `core/` / `bus/` / `protocol/` / `runtime/` / `tracing/`
- **接口层** —— `cli/`（命令入口） / `dashboard/`（Web UI，可选 `pip install -e ".[dashboard]"`）
- **业务层** —— `packages/domains/`（一个个领域，自带电池：内置 `research` / `decisions` / `software_engineering` / `publish`） / `packages/services/`（基础设施服务）

整体架构见 [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)，各类规范见 [docs/standards/](docs/standards/)。

## 了解更多

| 想知道 | 看 |
|---|---|
| 整体架构 / 怎么加你自己的领域 | [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) |
| 命令行用法 | `omni --help` |
| 各种规范（Material / Worker / Team / 头注释等） | [docs/standards/](docs/standards/) |
| 怎么贡献 | [CONTRIBUTING.md](CONTRIBUTING.md) |

## License

MIT —— 见 [LICENSE](LICENSE)。
