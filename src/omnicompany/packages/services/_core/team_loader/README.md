<!-- [OMNI] origin=ai-ide domain=services/team_loader ts=2026-05-04T17:18:00Z type=doc status=active agent=ai-ide belongs_to_service=team_loader -->
<!-- [OMNI] summary="team_loader service - yaml 加载 Team 配置. 跟 omni team 命令组配套, 让 Team 可以纯 yaml 定义不写 Python pipeline.py" -->
<!-- [OMNI] tags=readme,team_loader,core,yaml,self-narrative -->
<!-- [OMNI] material_id="material:services._core.team_loader.readme.md"-->

# team_loader · yaml 加载 Team 配置

> 让 Team 可以**纯 yaml 定义** 不写 Python pipeline.py. 跟 [omni team](../../../../../../docs/standards/cli/) 命令组配套. 简单 Team (无业务逻辑) 直接 yaml 写, 复杂 Team 仍用 Python.

## 这是什么

team_loader 是 omnicompany 的**yaml Team 加载 service**. 含 [yaml_loader.py](yaml_loader.py) 一份, 解析 yaml → TeamSpec.

## 解决什么 / 不解决什么

**解决**: 简单 Team 用 yaml 定义不需 Python pipeline.py.
**不解决**: 复杂 Team (Worker 内有业务逻辑) — 仍写 Python; Worker 实现 — yaml 只能引用已注册的 Worker.

## 设计目的与最终目标

**设计目的**: omnicompany 大量简单组合 Team (例几个 Worker 串起来跑) 不需要 Python pipeline.py 模板代码, yaml 一份 + omni team load 即可.

**最终目标**: 跟 docauthor 自动化结合 — docauthor 产 yaml Team 定义而非 Python (机械部分自动化).

## 规划

- **当前 active**
- **下一步**: 跟 docauthor 结合产 yaml

## 构成

- yaml loader → [yaml_loader.py](yaml_loader.py)

## 想了解更多

- [DESIGN.md](DESIGN.md) / [SKILL.md](SKILL.md)
- omni team CLI → 项目根 README cli 段
- 跟 docauthor 结合 → [../../_authoring/docauthor/](../../_authoring/docauthor/)
