<!-- [OMNI] origin=ai-ide domain=services/configurable ts=2026-05-04T17:10:00Z type=doc status=active agent=ai-ide belongs_to_service=configurable -->
<!-- [OMNI] summary="configurable service - ConfigurableHook + ConfigurableTool spec 类承载. 让 hook/tool 通过外部 spec 配置驱动, 不硬编码业务子类" -->
<!-- [OMNI] tags=readme,configurable,core,spec,self-narrative -->
<!-- [OMNI] material_id="material:services._core.configurable.readme.md"-->

# configurable · 配置驱动 spec 库

> 提供 `ConfigurableHook + HookSpec` / `ConfigurableTool + ToolSpec` 两个配置驱动基类. 让 hook / tool 通过外部 spec 配置驱动 (一行 SPEC), 不必为每个变体写硬编码子类. 跟 [agent](../agent/) 的 `ConfigurableAgent + AgentSpec` 同形态.

## 这是什么

configurable 是 omnicompany 的**配置驱动 spec 基类库**. 含两个配置驱动的基类:
- `ConfigurableHook + HookSpec` ([hook_spec.py](hook_spec.py)) — 周期性 / 事件驱动 hook 的配置驱动版
- `ConfigurableTool + ToolSpec` ([tool_spec.py](tool_spec.py)) — Worker 内调用工具的配置驱动版

跟 [agent.ConfigurableAgent](../agent/) 配套, 都是"一行 SPEC 替代一个子类" 范式.

## 解决什么 / 不解决什么

**解决**: hook/tool 变体写 spec 不写子类, 减重复代码.
**不解决**: 业务逻辑 (各 SPEC 自己定); 非 hook/tool 类型的配置 (Agent SPEC 在 [agent](../agent/), Worker 仍是子类).

## 设计目的与最终目标

跟 omnicompany "agent-first 工具范式 ≤10 个" 铁律对齐 — 不写 N 个领域分类工具子类, 用 SPEC 配置驱动一份基类.

**最终目标**: hook/tool 全用 SPEC 写, 子类继承基类 + 重写 `_validate_command` 等小 hook 即可 (例 [agent.BashRouter](../agent/) 子类只重写 `_validate_command` 加业务白名单).

## 规划

- **当前 active** — 2 spec 类已用 (demogame_kb_storywiki / config_service / 等)
- **下一步**: 持续观察, 按需扩展 spec 字段

## 构成

- HookSpec → [hook_spec.py](hook_spec.py)
- ToolSpec → [tool_spec.py](tool_spec.py)

## 想了解更多

- [DESIGN.md](DESIGN.md) / [SKILL.md](SKILL.md)
- 兄弟 ConfigurableAgent → [../agent/README.md](../agent/README.md)
- agent_first 工具范式 → [docs/standards/concepts/agent_first.md](../../../../../../docs/standards/concepts/agent_first.md)
