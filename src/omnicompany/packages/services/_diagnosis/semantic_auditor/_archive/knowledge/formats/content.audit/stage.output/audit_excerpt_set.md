---
omnikb_type: kformat
id: "kb.services.semantic_auditor.audit_excerpt_set"
name: "语义审计 · Audit Excerpt 集合"
tags: []
relates_to_formats:
  - semantic_auditor.audit-excerpt-set
relates_to_krouters:
  - kb.services.semantic_auditor.excerpt_retriever
maturity: design
summary: "SemanticAuditor Phase B1 管线的最终产出：每个 artifact × 适用标准 的摘录清单，供 Phase B2 LLMAuditRouter 消费。"
---

# 语义审计 · Audit Excerpt 集合

`semantic_auditor` 管线 Phase B1 的出口 Format。对每个待审 artifact，按其适用的
standards 提取摘录文本，打成三元组清单，供下游 LLMAuditRouter（Phase B2）消费。

## 已知结构特征

```
{
  "project_root": "<abs>",
  "excerpts": [
    {
      "target": {"path": "...", "kind": "router"},   # 被审 artifact
      "standard_id": "STANDARD-ROUTER",               # 适用标准
      "excerpt_text": "...",                           # 按 excerpt_strategy 取的内容
      "excerpt_len": 4823                              # 字符数
    },
    ...
  ],
  "excerpt_count": N,
  "failed_retrievals": [                               # 取不到摘录的条目
    {"target_path": "...", "standard_id": "...", "reason": "..."}
  ]
}
```

## 验证要点

1. 每个 excerpt 必含 `target.path` + `standard_id` + `excerpt_text`
2. `excerpt_len == len(excerpt_text)`
3. `standard_id` 在 `docs/standards/standards-index.yaml` 里可查
4. 同一 (target.path, standard_id) 只出现一次

## 下游用途

- **Phase B2 `LLMAuditRouter` (AgentNodeLoop)** 消费此 Format：每条 excerpt 送 LLM
  并配 artifact 全文，产出 `Finding` 列表
- 对应可执行 Format ID：`semantic_auditor.audit-excerpt-set`

## 关联说明

- 摘录策略由 `standards-index.yaml` 的 `excerpt_strategy` 决定（full / section）
- `section` 模式的 key_sections 全部未命中时自动 fallback full（宁可多喂不漏）
- failed_retrievals 供人工排查规范文件缺失或 standard_id 误录
