<!-- [OMNI] origin=ai-ide domain=services/team_loader ts=2026-05-04T17:18:00Z type=doc status=active agent=ai-ide belongs_to_service=team_loader -->
<!-- [OMNI] material_id="material:services._core.team_loader.design.md"-->

# team_loader · 设计文档

> 设计目的请看 [README.md](README.md). 怎么用请看 [SKILL.md](SKILL.md).

## 状态
- **版本**: V1
- **成熟度**: active
- **下一步**: 跟 docauthor 结合产 yaml

## 核心接口

- [yaml_loader.py](yaml_loader.py): `load_team_from_yaml(yaml_path) -> TeamSpec`

## 架构决策

### D1 · 简单 Team 走 yaml, 复杂 Team 走 Python
yaml 只能引用已注册 Worker (id), 不能在 yaml 里写 Python 代码. 复杂 Team 含动态逻辑仍写 Python pipeline.py.

## 数据流 / 拓扑

无独立管线, 是加载工具.

## 已知局限

- yaml schema 简单, 只支持线性 / fan-out 拓扑, 复杂 fan-in / 子 job 还得 Python

## 参考资料

- [yaml_loader.py](yaml_loader.py)
- omnicompany TeamSpec → [../omnicompany/DESIGN.md](../omnicompany/DESIGN.md)
