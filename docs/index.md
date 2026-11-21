# Make AI Slop More Efficiently

> AI 原生的软件工厂 —— 给命令行 AI agent 一个**声明清晰、明文可读、全程留痕、能自我诊断修复**的工作环境。
> 包名 `omnicompany`，命令行 `omni`。仓库名是句自嘲（我们主要拿它更高效地批量生产 AI slop）。

## 快速开始

```bash
git clone https://github.com/ColorC/make-ai-slop-more-efficiently.git
cd make-ai-slop-more-efficiently
pip install -e .
omni --help
```

## 从这里读起

- [架构](ARCHITECTURE.md) —— 构件模型（Material / Worker / Team / Hook / Tool / Agent）、分层、自带电池的领域、怎么加你自己的领域。
- [规范](standards/) —— Material / Worker / Team / 头注释等约定。

## 它是什么 / 不是什么

它不替代 LLM，而是给 LLM 一个可信任的工作环境：**LLM 是引擎，它是工厂**。用显式契约 + 事件落盘 + 守护规则，把"AI agent 跑着跑着失控/漂移"侦测出来、查得清、修得了。

发布版与可下载可执行文件见 [GitHub Releases](https://github.com/ColorC/make-ai-slop-more-efficiently/releases)。
