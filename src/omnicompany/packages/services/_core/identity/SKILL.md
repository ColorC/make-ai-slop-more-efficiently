---
name: identity
description: omnicompany 身份解析跟写入凭据 - 跟 omni who/whoami/session CLI 配套, PHASE3 注册体系的身份部分.
user-invocable: false
disable-model-invocation: false
---

<!-- [OMNI] origin=ai-ide domain=services/identity ts=2026-05-04T17:12:00Z type=doc status=active agent=ai-ide belongs_to_service=identity -->

# identity · 操作手册

> 设计目的请看 [README.md](README.md). 内部架构请看 [DESIGN.md](DESIGN.md).

## 适用范围

**用我**: 看当前身份 / 绑 session 跨 compact / 注册新身份签凭据.
**不用我**: 业务级权限 (找 [protection](../protection/) / [guardian](../guardian/)); 注册材料/批量 (找 omni register material/batch).

## 操作步骤

### 场景 A · 看当前身份

```bash
omni who          # 现身份
omni whoami       # 同 (alias)
```

### 场景 B · 绑 session 身份 (跨 compact 持久)

```bash
omni session bind --trace-id=<trace_id>
```

### 场景 C · 注册新身份 + 签凭据

```bash
omni register identity --kind=ai-ide --name=...
# 凭据落 data/services/registry/credentials/<id>.json
```

## 入口清单

| 入口 | 用途 |
|---|---|
| `omni who` / `omni whoami` | 看身份 |
| `omni session bind --trace-id=...` | 绑 session |
| `omni register identity --kind=... --name=...` | 注册身份 + 凭据 |

详细 CLI 标准: [docs/standards/cli/identity.md](../../../../../../docs/standards/cli/identity.md)

## 故障排查

| 现象 | 修 |
|---|---|
| 跨 compact 身份丢 | session 没 bind, 显式 `omni session bind` |
| 凭据找不到 | `data/services/registry/credentials/` 看是否真落了 |
| 身份冲突 (多个同名) | resolver 逻辑当前简单, 真冲突手动改 credentials |

## 想了解更多

- [README.md](README.md) / [DESIGN.md](DESIGN.md)
- CLI 标准 → [docs/standards/cli/identity.md](../../../../../../docs/standards/cli/identity.md)
