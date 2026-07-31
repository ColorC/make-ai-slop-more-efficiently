# [OMNI] origin=internal-engine domain=services/knowledge ts=2026-04-08T09:06:19Z
---
omnikb_type: karch
id: kb.arch.package.services_workflow_factory
name: 'Package: packages/services/workflow_factory'
tags:
- topic.package
- layer.services
- domain.workflow_factory
maturity: draft
summary: workflow_factory — 造工作流的工作流
scope: omnicompany
code_anchors:
- src/omnicompany/packages/services/workflow_factory/__init__.py
- src/omnicompany/packages/services/workflow_factory/pipeline.py
- src/omnicompany/packages/services/workflow_factory/routers.py
- src/omnicompany/packages/services/workflow_factory/run.py
- src/omnicompany/packages/services/workflow_factory/formats.py
---

# Package: packages/services/workflow_factory

> **Layer**: `services` · **Package name**: `workflow_factory` · **Maturity (this wiki entry)**: draft (auto-seeded)

## Auto-seeded summary

This is the verbatim docstring from `__init__.py`, preserved here so future
agents and humans can see the package's own self-description without re-reading
the source. R3.2 LLM enrichment should keep this and add the structured
sections below.

```
workflow_factory — 造工作流的工作流

元管线：输入自然语言需求 → 输出通过全部验证的 LAP-native 工作流代码。

10 节点 4 回路:
  A(req_analyzer) → B(format_designer) → C(node_planner)
  → D(code_generator) → E(compile_checker) → F(lap_verifier)
  → G(error_route_auditor) → H(integration_tester) → J(finalizer)
  + E'(syntax_fixer), I(auto_fixer) 修复回路
```

## Why this package exists

_(待补充: 这个包解决什么问题? 为什么是独立的包而不是其他包的子模块?)_

## Public surface

_(待补充: 哪些类/函数/管线是其他包应该 import 的? 哪些是内部细节?)_

## Internal structure

_(待补充: 子模块布局, 关键文件作用)_

## Files

- `src/omnicompany/packages/services/workflow_factory/__init__.py`
- `src/omnicompany/packages/services/workflow_factory/pipeline.py`
- `src/omnicompany/packages/services/workflow_factory/routers.py`
- `src/omnicompany/packages/services/workflow_factory/run.py`
- `src/omnicompany/packages/services/workflow_factory/formats.py`

## Related

- _(待补充: 依赖的 KArchitecture / KDecision)_
- _(待补充: 调用此包的其他 KArchitecture)_

## Known limitations

_(待补充: 当前已知限制 / TODO / 设计妥协)_

## Change log

- 2026-04-08 — auto-seeded from `__init__.py` docstring
