---
omnikb_type: krepo
id: kb.repo.sample__fixture
name: Sample External Repo
summary: 一个用于测试的外部仓画像, 仅 fixture.
tags:
  - topic.sample
maturity: draft
scope: "external:sample/fixture"
last_surveyed: "2026-04-09"
last_sha: "abc1234"
download_state: deleted
capability_areas:
  - name: "Sample capability"
    paths:
      - "src/sample.rs"
    evidence_files:
      - "src/sample.rs"
    omni_parallel: "packages/services/knowledge/store.py"
prior_landmarks_tier_1: []
known_unread_areas:
  - "docs/"
  - "tests/"
related_experiments:
  - kb.experiment.sample
---

# Sample External Repo

Golden fixture for KRepoArchitectEntry parser test.

## Capability areas

一个 sample capability, 指向 src/sample.rs, OmniCompany 这边对应
packages/services/knowledge/store.py。
