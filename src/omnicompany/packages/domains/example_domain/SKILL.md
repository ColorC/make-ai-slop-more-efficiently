---
name: example_domain
description: 模板域 —— 复制我来开始你自己的领域(domain)。展示一个领域的最小骨架(SKILL.md 本清单 + team.py 管线拓扑 + DESIGN.md 设计意图)。不是可跑的真功能, 是"照着写"的脚手架起点。触发词含 模板/template/脚手架/新建领域/add domain。
---

# example_domain

这是一个**领域模板**, 不提供真实功能——它展示"一个领域长什么样", 给你照着加自己的领域。

omnicompany 的"领域(domain)"= 一条端到端管线: 一个 `team.py`(声明管线拓扑) + `routers/`(各节点的 transform 逻辑) + Material 契约。

## 何时用

- 你想给 omnicompany 加一个新领域(一条新管线), 不知道从哪起步 → 复制这个目录。

## 怎么用

```bash
python scripts/new_domain.py my_domain      # 复制 example_domain → domains/my_domain 并改名
```

然后四步:

1. 在 `team.py` 把 2 个占位节点改成你的真实管线节点(加节点/边)。
2. 实现各节点的 transform 逻辑(参考 `domains/research/routers/`)。
3. 在 `src/omnicompany/core/pipelines.py` 注册你的域(一个 `_lazy` 条目)。
4. `python scripts/validate_domains.py` 校验 SKILL.md 合规。

## 管线节点

模板自带一个最小 2 节点线性管线:

- `intake`(RULE)—— 归一化输入, 建 run_dir。
- `process`(RULE)—— 把输入加工成产物。

照着加节点/边即可; 要调 LLM 把 `TransformMethod.RULE` 换成 `TransformMethod.LLM`。

## 配置

无需额外配置。
