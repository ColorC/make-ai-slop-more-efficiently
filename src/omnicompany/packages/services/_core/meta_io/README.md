<!-- [OMNI] origin=ai-ide domain=services/meta_io ts=2026-05-04T17:14:00Z type=doc status=active agent=ai-ide belongs_to_service=meta_io -->
<!-- [OMNI] summary="meta_io service - 元 IO 工具操作 + 状态绑定. 用户原始需求 6.6 — tool 操作可观测 + 状态可绑. 含 audit/builtins/definitions/registry/state_check 5 模块" -->
<!-- [OMNI] tags=readme,meta_io,core,tools,audit,self-narrative -->
<!-- [OMNI] material_id="material:services._core.meta_io.readme.md"-->

# meta_io · 元 IO 工具操作

> 用户原始需求 6.6 实施 — tool 操作可观测 + 状态可绑. 含 5 模块: audit (留痕) / builtins (内置工具) / definitions (工具定义) / registry (注册) / state_check (状态校验).

## 这是什么

meta_io 是 omnicompany 的**元 IO 服务**. 给 omni 内部 tool 操作提供统一的留痕 + 注册 + 状态绑定基础设施.

## 解决什么 / 不解决什么

**解决**: 工具调用统一 audit / 工具注册 / 工具操作前后状态校验.
**不解决**: 工具自身业务逻辑 (各 SingleToolRouter 子类自己负责); LLM 调用 audit (那是 [llm_audit](../../) 命令族).

## 设计目的与最终目标

**设计目的**: 用户原始需求 6.6 提的 — 让 tool 操作不黑箱, 调一个工具应自动留痕到 audit + 跟 trace 关联. meta_io 把这事 service 化.

**最终目标**: 跟 [agent.ToolDispatchRouter](../agent/) 真接通 — 每次工具分发都过 meta_io.audit + state_check.

## 规划

- **当前 active**
- **下一步**: 跟 agent ToolDispatch 真接通

## 构成

- audit → [audit.py](audit.py)
- 内置工具 → [builtins.py](builtins.py)
- 定义 → [definitions.py](definitions.py)
- 注册 → [registry.py](registry.py)
- 状态校验 → [state_check.py](state_check.py)

## 想了解更多

- [DESIGN.md](DESIGN.md) / [SKILL.md](SKILL.md)
- 跟 agent ToolDispatch → [../agent/README.md](../agent/README.md)
