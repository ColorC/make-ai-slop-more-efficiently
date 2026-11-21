<!-- [OMNI] origin=ai-ide domain=services/meta_io ts=2026-05-04T17:14:00Z type=doc status=active agent=ai-ide belongs_to_service=meta_io -->
<!-- [OMNI] material_id="material:services._core.meta_io.design.md"-->

# meta_io · 设计文档

> 设计目的请看 [README.md](README.md). 怎么用请看 [SKILL.md](SKILL.md).

## 状态
- **版本**: V1
- **成熟度**: active
- **下一步**: 跟 agent ToolDispatch 真接通

## 核心接口

- [audit.py](audit.py): tool 调用 audit 留痕
- [builtins.py](builtins.py): 内置工具集
- [definitions.py](definitions.py): 工具 schema 定义
- [registry.py](registry.py): 工具注册 (跟 omni meta-io 命令组配套)
- [state_check.py](state_check.py): 状态前后校验

## 架构决策

### D1 · 5 模块各管一段
audit / registry / definitions / state_check / builtins 职责清晰, 不混. tool 调用走"definitions 查 schema → registry 找 impl → state_check 前置 → 调 → audit 留痕 → state_check 后置".

## 数据流 / 拓扑

无独立管线, 是 tool 操作的横切设施.

## 已知局限

- 跟 agent ToolDispatch 没真接通 (Phase 1 backlog)
- audit 落盘格式跟 [llm_audit](../../) 不统一 (升级路径: 走 ServiceBus)

## 参考资料

- 5 模块文件
- 用户原始需求 6.6 (tool 操作可观测)
