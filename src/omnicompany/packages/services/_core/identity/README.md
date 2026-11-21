<!-- [OMNI] origin=ai-ide domain=services/identity ts=2026-05-04T17:12:00Z type=doc status=active agent=ai-ide belongs_to_service=identity -->
<!-- [OMNI] summary="identity service - omni 身份解析跟写入凭据. 跟 omni who/whoami/session CLI 配套, 是 PHASE3 注册体系的身份部分" -->
<!-- [OMNI] tags=readme,identity,core,session,self-narrative -->
<!-- [OMNI] material_id="material:services._core.identity.readme.md"-->

# identity · omni 身份解析

> omni session 身份解析 + 写入凭据签发. 跟 [omni who / whoami / session CLI](../../../../../../docs/standards/cli/identity.md) 配套, 是 CLI-PHASE3 注册体系的身份部分.

## 这是什么

identity 是 omnicompany 的**身份解析跟写入凭据 service**. 跟 register 命令族配套, 让 AI IDE 跟 agent worker 在写入 omnicompany 时有可追溯身份.

## 解决什么 / 不解决什么

**解决**: 当前 session 身份解析 (omni who) / 跨 compact 身份持久 (omni session bind) / 写入凭据签发 (data/services/registry/credentials/<id>.json).
**不解决**: 业务级权限 (那是 [protection lock](../protection/) 跟 [guardian](../guardian/) 的事); 注册体系本身 (那是 [registration CLI](../../../../../../docs/standards/cli/registration.md)).

## 设计目的与最终目标

**设计目的**: AI IDE 跟 agent 写入 omnicompany 都得有身份, 不能匿名. 身份 → 凭据 → 写入审计 → 出问题可追溯.

**最终目标**: 接 CORE-SELF-STABILITY 第二/三阶段 的内拦机制 — 写前校验身份 + 凭据 + agent 给的预期对照.

## 规划

- **当前 active** (CLI-PHASE3 完成 2026-05-04)
- **下一步**: 接 CORE-SELF-STABILITY 内拦机制

## 构成

- 身份解析 → [resolver.py](resolver.py)
- 写入凭据 → [writes.py](writes.py)

## 想了解更多

- [DESIGN.md](DESIGN.md) / [SKILL.md](SKILL.md)
- CLI omni who / whoami / session → [docs/standards/cli/identity.md](../../../../../../docs/standards/cli/identity.md)
- 注册体系 → [docs/standards/cli/registration.md](../../../../../../docs/standards/cli/registration.md)
