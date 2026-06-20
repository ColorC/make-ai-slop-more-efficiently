# Changelog

本文件记录发布版的用户可见变更。包名 `omnicompany`，命令行 `omni`。

## [0.2.0] - 2026-06-20

首个公开平台版本。

### Added
- 通用 AI-agent 框架核心：Material / Worker / Team / Hook / Tool / Agent 构件模型 + 事件总线 + OmniMark 头注释全程留痕。
- 自带电池的内置领域：`research`（公开调研管线）/ `decisions`（决策库）/ `software_engineering`（工程多阶段）/ `publish`（发布治理）。
- 领域插件模型（对齐 agentskills.io）：每个领域带 `SKILL.md` 清单；`example_domain` 模板 + `scripts/new_domain.py` 脚手架 + `scripts/validate_domains.py` 契约校验器。
- Guardian 目录健康 / 架构漂移巡逻（`omni guardian patrol`）；CLI 对缺失的可选服务优雅降级。
- CI：3 个 Python 版本测试矩阵 + 干净环境可移植性冒烟 + ruff lint + CodeQL；release 工作流产 wheel + 三平台 PyInstaller 可执行文件。

### Notes
- 仓库名 "Make AI Slop More Efficiently" 是句自嘲；工具本身（`omni`）是给 AI agent 一个声明清晰、全程留痕、能自我诊断修复的工作环境。
