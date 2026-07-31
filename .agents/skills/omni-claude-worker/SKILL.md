---
name: omni-claude-worker
description: Use when Codex or Claude is working in omnicompany and should delegate spec-driven investigation or implementation to Claude Code through the audited `omni worker run claude-code` CLI while Codex keeps planning, review, validation, and cleanup ownership.
---

# Omni Claude Worker

你正在 omnicompany 内部使用 Claude Code 子 worker 工作法。

完整规则和工作顺序只维护在唯一源：

- [`docs/standards/cli/claude_code_subagent_worker.md`](../../../docs/standards/cli/claude_code_subagent_worker.md)

本 skill 只作为项目内入口，不在这里复写规则。需要执行时先读取上面的标准，再用 `omni worker providers --json` 和 `omni worker run claude-code ...` 进行只读调查或实现型委托。
