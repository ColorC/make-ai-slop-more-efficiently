# [OMNI] origin=internal-engine domain=services/knowledge ts=2026-04-08T09:06:21Z
---
omnikb_type: kexp
id: kb.experiment.20260328_private_domain_isolation
name: 私域隔离文件结构设计
tags:
- topic.plan
- date.2026-03-28
maturity: draft
summary: '| 原则 | 含义 | |------|------| | **明文性** | 私域资产（节点定义、prompt、类型）以文本文件存储，人类可读、可
  diff、可 git 管理 | | **私域可学习** | 私域节点的 embedding、hit/success 统计与全局共享，系统可从私域节点学习路由 |
  | **网络同构性** | 私域节点与公开节点在 semantic_network 中具有完全相同的结构，路由器无需区分 | | **垄断打破式灵活度** |
  任何组织可接入自己的私域，不依赖中央授权；私域可以随时独立部署、迁移或删除 |'
date_started: '2026-03-28'
method_summary: see docs/plans/[2026-03-28]PRIVATE-DOMAIN-ISOLATION/
status: documented
followups:
- docs/plans/[2026-03-28]PRIVATE-DOMAIN-ISOLATION/README.md
---

# 私域隔离文件结构设计

> Plan directory: `docs/plans/[2026-03-28]PRIVATE-DOMAIN-ISOLATION/`
> Auto-seeded from README.md (excerpt below).

## Plan README excerpt

```markdown
# 私域隔离文件结构设计

## 设计原则

| 原则 | 含义 |
|------|------|
| **明文性** | 私域资产（节点定义、prompt、类型）以文本文件存储，人类可读、可 diff、可 git 管理 |
| **私域可学习** | 私域节点的 embedding、hit/success 统计与全局共享，系统可从私域节点学习路由 |
| **网络同构性** | 私域节点与公开节点在 semantic_network 中具有完全相同的结构，路由器无需区分 |
| **垄断打破式灵活度** | 任何组织可接入自己的私域，不依赖中央授权；私域可以随时独立部署、迁移或删除 |

---

## 核心约束

- **全局共用一个数据库**：`semantic_network.db` 所有域共享（路由索引、embedding 向量）
- **全局共享 trace_id 命名空间**：跨域 trace 可以联通，一次任务可跨越公域和私域节点
- **私域资产独立存储**：节点定义文件、tool 实现、hook 脚本存放在私域目录
- **数据库中的私域标记**：`source_channel` 字段标记节点来源（如 `"private:acme"`, `"private:local"`）

---

## 目录结构

### 全局布局（仓库根）

```
omnicompany/
├── src/omnicompany/          # 框架核心（公开）
├── domains/                  # 私域挂载点（gitignored 或子模块）
│   ├── .gitkeep
│   └── README.md             # 说明如何注册私域
├── data/
│   └── autonomous/
│       └── semantic_network.db   # 全局共享（所有域写入此处）
└── config/
    └── domains.yaml          # 域注册表（哪些私域被激活）
```

### 单个私域结构

```
domains/
└── acme/                     # 企业私域示例
    ├── domain.yaml           # 域元数据（id、描述、优先级）
    ├── nodes/                # 节点定义（明文 YAML/TOML）
    │   ├── acme.invoice_parser.yaml
    │   ├── acme.contract_reviewer.yaml
    │   └── _index.yaml       # 该域所有节点列表（可选）
    ├── tools/                # Tool 实现（Python 函数/脚本）
    │   ├── acme_invoice_tool.py
    │   └── acme_contract_tool.py
    ├── hooks/                # 触发 Hook（入口点定义）
    │   └── acme_task_hook.py
    └── traces/               # 可选：离线 trace 快照（审计用）
        └── .gitkeep
```

---

## domain.yaml 格式

```yaml
# doma
```

## Plan files

- `docs/plans/[2026-03-28]PRIVATE-DOMAIN-ISOLATION/README.md`

## Hypothesis

_(待补充: 这个 plan 的核心假设, 为什么需要做)_

## Method

_(待补充: 实施方法, 关键步骤)_

## Samples

_(待补充: 跑过的样本, 各自结果)_

## Findings

_(待补充: 关键发现, 哪些假设被验证, 哪些被推翻)_

## Followups

_(待补充: 后续 TODO, 关联其他 plan / 计划目录中的文件已自动列在 frontmatter)_

## Change log

- 2026-04-08 — auto-seeded from plan README
