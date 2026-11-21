---
name: protection
description: omnicompany 锁机制内核 - omni lock CLI 后端, handlers/policy/scanner. 当前是开关+扫描, 真硬拦待自稳第二/三阶段.
user-invocable: false
disable-model-invocation: false
---

<!-- [OMNI] origin=ai-ide domain=services/protection ts=2026-05-04T17:16:00Z type=doc status=active agent=ai-ide belongs_to_service=protection -->

# protection · 操作手册

> 设计目的请看 [README.md](README.md). 内部架构请看 [DESIGN.md](DESIGN.md).

## 适用范围

**用我**: 开/关 omni 写入锁 / 看锁状态 / 扫无身份写入.
**不用我**: guardian 静态合规 (找 [guardian](../guardian/)); 业务权限.

## 操作步骤

### 场景 A · 看锁状态

```bash
omni lock status
```

### 场景 B · 开/关锁

```bash
omni lock open      # = enable
omni lock close     # = disable
```

### 场景 C · 扫无身份写入

```bash
omni lock scan
```

## 入口清单

| 入口 | 用途 |
|---|---|
| `omni lock status / open / close / scan` | 锁管理 |

详细 CLI: [docs/standards/cli/lock.md](../../../../../../docs/standards/cli/lock.md)

## 故障排查

| 现象 | 修 |
|---|---|
| open 后写入仍能进 | 当前不是真硬拦 (D2 局限), 待自稳第二/三阶段升级 |
| scan 漏报无身份写入 | scanner 是被动扫, 真拦得加 PreToolUse hook |

## 想了解更多

- [README.md](README.md) / [DESIGN.md](DESIGN.md)
- omni lock CLI → [docs/standards/cli/lock.md](../../../../../../docs/standards/cli/lock.md)
- 自稳计划 → [docs/plans/guardian/[2026-05-04]CORE-SELF-STABILITY/plan.md](../../../../../docs/plans/guardian/%5B2026-05-04%5DCORE-SELF-STABILITY/plan.md)
