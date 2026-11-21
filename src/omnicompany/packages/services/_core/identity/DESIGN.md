<!-- [OMNI] origin=ai-ide domain=services/identity ts=2026-05-04T17:12:00Z type=doc status=active agent=ai-ide belongs_to_service=identity -->
<!-- [OMNI] material_id="material:services._core.identity.design.md"-->

# identity · 设计文档

> 设计目的请看 [README.md](README.md). 怎么用请看 [SKILL.md](SKILL.md).

## 状态
- **版本**: V1 (CLI-PHASE3 完成 2026-05-04)
- **成熟度**: active
- **下一步**: 接 CORE-SELF-STABILITY 内拦机制

## 核心接口

- [resolver.py](resolver.py): 身份解析逻辑 (`omni who` / `omni whoami` 调用)
- [writes.py](writes.py): 写入凭据签发逻辑 (`omni register identity` 后落 `data/services/registry/credentials/<id>.json`)

## 架构决策

### D1 · 身份跨 compact 持久
session 身份绑定后跨 Claude Code compact 持久 (写入文件), 不是只在内存里.

### D2 · 凭据是写入门禁前提
注册成功落凭据是 PHASE3 第四段锁组的写入门禁前提. 凭据签发已实装, 锁组对接逻辑留 deferred (CORE-SELF-STABILITY 第二/三阶段).

## 数据流 / 拓扑

无独立管线, 是 CLI 命令族的内核. resolver 被 omni who/whoami 调, writes 被 omni register identity 调.

## 已知局限

- 当前没真接锁组 (deferred); 真"内拦未注册写入" 待 CORE-SELF-STABILITY 第二/三阶段
- agent 身份是否够细 (单 agent 跨多 trace 共享一身份还是每 trace 一身份) 暂统一一身份, 没遇真问题再扩

## 参考资料

- [resolver.py](resolver.py) / [writes.py](writes.py)
- CLI 标准 → [docs/standards/cli/identity.md](../../../../../../docs/standards/cli/identity.md)
- 自稳第二/三阶段 → [docs/plans/guardian/[2026-05-04]CORE-SELF-STABILITY/plan.md](../../../../../docs/plans/guardian/%5B2026-05-04%5DCORE-SELF-STABILITY/plan.md)
