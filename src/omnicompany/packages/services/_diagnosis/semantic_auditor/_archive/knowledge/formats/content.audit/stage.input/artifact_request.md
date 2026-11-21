---
omnikb_type: kformat
id: "kb.services.semantic_auditor.artifact_request"
name: "语义审计 · Artifact 请求"
tags: []
relates_to_formats:
  - semantic_auditor.artifact-request
relates_to_krouters:
  - kb.services.semantic_auditor.artifact_selector
maturity: design
summary: "SemanticAuditor 管线唯一入口：给出待审文件路径（或扫描源），触发语义合规审计。"
---

# 语义审计 · Artifact 请求

`semantic_auditor` 管线的唯一入口 Format。给定一批待审 artifact，产出它们应按哪些 standards 审计。

## 已知结构特征

三种入口形态（择一）：

| 形态 | 字段 | 典型触发 |
|---|---|---|
| 显式 paths | `{"paths": [...], "project_root": "..."}` | 手动 CLI / hook 指定审谁 |
| git-diff 扫描 | `{"source": "git-diff", "project_root": "..."}` | pre-commit / 变更触发 |
| full-scan 扫描 | `{"source": "full-scan", "project_root": "..."}` | 周期定时 / 全仓复核 |

必有字段：`project_root` — 项目根绝对路径，用于定位 `docs/standards/standards-index.yaml`。

## 验证要点

1. `project_root` 存在且可读
2. `paths` 或 `source` 至少有一个（若都缺 → FAIL）
3. `source` 的允许值：`"git-diff"` / `"full-scan"`（其他值 → FAIL）
4. paths 中的路径相对于 `project_root`

## 下游用途

- **`ArtifactSelectorRouter`** 消费此 Format：把路径列表/扫描源转为 `artifact-set`
- 对应可执行 Format ID：`semantic_auditor.artifact-request`

## 关联 Format 说明

本 KFormat 描述语义；可执行 Format `semantic_auditor.artifact-request` 是 JSON schema
化版本。两者应同步更新。
