---
omnikb_type: kdec
id: kb.decision.sample
name: Sample Decision
summary: 一个用于测试的决策记录, 仅 fixture.
tags:
  - topic.sample
maturity: stable
date_decided: "2026-04-09"
status: decided
drivers:
  - "需要 golden fixture 验证 KDecisionEntry 解析"
options_considered:
  - "只写 KArch fixture (被否决, 无法测 KDec)"
  - "每种类型一份 fixture (选中)"
decision: "为每种 entry 类型各建一份 golden fixture"
consequences_positive:
  - "解析器被严格测试"
  - "后续新增字段时有回归 baseline"
consequences_negative:
  - "fixture 需要和 schema 同步更新"
related_karchs:
  - kb.arch.sample
---

# Sample Decision

ADR fixture for the OmniKB test suite.

## Drivers

测试需要为每种 entry type 至少提供一份 parseable sample。

## Decision

按类型建 fixture, 每份专注一种类型的全字段覆盖。
