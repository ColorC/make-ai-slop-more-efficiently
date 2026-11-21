<!-- [OMNI] origin=ai-ide domain=services/protection ts=2026-05-04T17:16:00Z type=doc status=active agent=ai-ide belongs_to_service=protection -->
<!-- [OMNI] material_id="material:services._core.protection.design.md"-->

# protection · 设计文档

> 设计目的请看 [README.md](README.md). 怎么用请看 [SKILL.md](SKILL.md).

## 状态
- **版本**: V1 (PHASE3 完成 2026-05-04)
- **成熟度**: active
- **下一步**: 接 CORE-SELF-STABILITY 第二阶段内拦机制

## 核心接口

- [handlers.py](handlers.py): omni lock open / close / status 命令处理
- [policy.py](policy.py): 锁策略定义 (开 / 关 / 部分开)
- [scanner.py](scanner.py): 扫无身份写入

## 架构决策

### D1 · open/close 是 enable/disable 的 alias
PHASE3 加 open/close 跟 plan §5.4 命名对齐, 内部仍是 enable/disable 实现.

### D2 · 当前是开关 + 扫描雏形, 不是真硬拦
真"内拦 agent 写入" + "外清无身份立刻清原位" 待 CORE-SELF-STABILITY 第二/三阶段升级. 当前只 status 查询 + scanner 扫无身份. PHASE3 用户明示"硬拦截开关上线前用户必审".

## 数据流 / 拓扑

无独立管线, CLI handler 直接调内部函数.

## 已知局限

- 锁状态变更没落事件总线 (PHASE3 deferred)
- 没真接 PreToolUse hook 拦 agent 写入 (CORE-SELF-STABILITY 第三阶段做)
- scanner 当前是被动扫, 不是写前拦

## 参考资料

- 3 模块文件
- omni lock CLI → [docs/standards/cli/lock.md](../../../../../../docs/standards/cli/lock.md)
- 自稳计划 → [docs/plans/guardian/[2026-05-04]CORE-SELF-STABILITY/plan.md](../../../../../docs/plans/guardian/%5B2026-05-04%5DCORE-SELF-STABILITY/plan.md)
