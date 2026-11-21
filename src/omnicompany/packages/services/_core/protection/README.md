<!-- [OMNI] origin=ai-ide domain=services/protection ts=2026-05-04T17:16:00Z type=doc status=active agent=ai-ide belongs_to_service=protection -->
<!-- [OMNI] summary="protection service - omni lock 主动防御 service 内核. handlers (锁开关处理) + policy (策略) + scanner (无身份扫描)" -->
<!-- [OMNI] tags=readme,protection,core,lock,self-narrative -->
<!-- [OMNI] material_id="material:services._core.protection.readme.md"-->

# protection · omni 锁机制内核

> [omni lock](../../../../../../docs/standards/cli/lock.md) 主动防御 service 内核. 含 handlers (锁开关处理) + policy (策略) + scanner (无身份扫描). PHASE3 第四段实装.

## 这是什么

protection 是 omnicompany 的**锁机制 service 内核**. omni lock open/close/status CLI 调用本 service. 做"内拦写入 / 外清无身份" 双轨 (用户 2026-04-30 立).

## 解决什么 / 不解决什么

**解决**: omni 写入路径锁开关 / 锁状态查询 / 无身份写入扫描.
**不解决**: guardian 静态合规 (那是 [guardian](../guardian/)); 业务级权限 (那是各 service 自己的事); 真"内拦"机制 (待 CORE-SELF-STABILITY 第二/三阶段 升级).

## 设计目的与最终目标

**设计目的**: 用户铁律"omnicompany 守护拒绝所有未注册写入" 的硬锁实施层. 当前是开关 + 扫描雏形, 真"内拦 agent 写入" 待自稳第二/三阶段升级.

**最终目标**: 接通 [agent.ToolDispatchRouter](../agent/) 写前 hook + 预期对照协议 (CORE-SELF-STABILITY 自稳目的对齐部分).

## 规划

- **当前 active** (PHASE3 完成 2026-05-04)
- **下一步**: 接 CORE-SELF-STABILITY 第二阶段 — 内拦 agent 写入 + 预期对照协议

## 构成

- handlers → [handlers.py](handlers.py) (锁开关 + status)
- policy → [policy.py](policy.py) (锁策略定义)
- scanner → [scanner.py](scanner.py) (无身份扫描)

## 想了解更多

- [DESIGN.md](DESIGN.md) / [SKILL.md](SKILL.md)
- omni lock CLI → [docs/standards/cli/lock.md](../../../../../../docs/standards/cli/lock.md)
- 自稳计划 → [docs/plans/guardian/[2026-05-04]CORE-SELF-STABILITY/plan.md](../../../../../docs/plans/guardian/%5B2026-05-04%5DCORE-SELF-STABILITY/plan.md)
