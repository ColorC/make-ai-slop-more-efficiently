# example_domain — 领域模板设计

这是给"想加自己领域"的人看的模板。它本身不做事, 只把一个领域的最小形状摆出来。

## 一个领域由什么组成

| 文件 | 作用 |
|---|---|
| `SKILL.md` | 插件清单(agentskills.io 格式): `name`(必须等于目录名) + `description`(做什么/何时用)。让人和 agent 能发现、判断该不该用它。 |
| `team.py` | 声明管线拓扑(`TeamSpec`): 节点(`TeamNode`) + 边(`TeamEdge`) + 入口。管线只能是 Team。 |
| `routers/` | 各节点的真实 transform 逻辑(本模板从略, 参考 `domains/research/routers/`)。 |
| `DESIGN.md` | 设计意图(就是本文件)。 |

## 加你自己的领域

1. `python scripts/new_domain.py <name>` —— 复制本目录成 `domains/<name>/` 并把 `example_domain`/`example` 改成你的名字。
2. 在 `team.py` 里把占位的 `intake → process` 改成你的真实节点和边。
3. 在 `routers/` 实现每个节点的 transform（RULE=确定性, LLM=调模型）。
4. 在 `src/omnicompany/core/pipelines.py` 注册你的域（一个 `_lazy` 条目, 照其它域的写法）。
5. `python scripts/validate_domains.py` 校验合规, 然后 `omni run <name>.run` 跑。

## 约定

- `name` 必须等于目录名（`scripts/validate_domains.py` 会查）。
- 业务/真源数据进 `data/domains/<name>/`（gitignore），管线代码进本目录。
- 框架不进业务: 通用能力复用 `packages/services/`，领域只组装。
