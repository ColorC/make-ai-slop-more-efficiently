<!-- [OMNI] origin=ai-ide domain=services/configurable ts=2026-05-04T17:10:00Z type=doc status=active agent=ai-ide belongs_to_service=configurable -->
<!-- [OMNI] material_id="material:services._core.configurable.design.md"-->

# configurable · 设计文档

> 设计目的请看 [README.md](README.md). 怎么用请看 [SKILL.md](SKILL.md).

## 状态
- **版本**: V1
- **成熟度**: active
- **下一步**: 按需扩 spec 字段

## 核心接口

- [hook_spec.py](hook_spec.py): `HookSpec` (dataclass) + `ConfigurableHook` 基类
- [tool_spec.py](tool_spec.py): `ToolSpec` (dataclass) + `ConfigurableTool` 基类

## 架构决策

### D1 · 配置驱动替代子类
`HookSpec / ToolSpec` 的 dataclass 字段 (name / description / trigger / executor 等) 让一份基类承载 N 个变体. 子类只在需要业务级重写 (例 `_validate_command`) 时才写.

### D2 · 跟 ConfigurableAgent 同形态
跟 [agent.ConfigurableAgent + AgentSpec](../agent/) 同设计模式, import 路径互不依赖但概念对称.

## 数据流 / 拓扑

无独立管线, 是基类库. 业务子类 (例 `demogame.SafeBashRouter` / `config_service._query_bash`) 继承本包的基类 + 提供 SPEC.

## 已知局限

- spec 字段不全, 有些复杂行为 (例 多触发器组合 hook) 仍需子类硬编

## 参考资料

- [hook_spec.py](hook_spec.py) / [tool_spec.py](tool_spec.py)
- [agent/](../agent/) (兄弟 ConfigurableAgent)
