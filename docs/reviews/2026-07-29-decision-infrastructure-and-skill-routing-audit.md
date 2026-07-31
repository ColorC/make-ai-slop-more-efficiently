# Omnicompany 决策设施与技能路由审计

日期：2026-07-29

## 结论

Omnicompany 已具备跨项目决策主库所需的 schema、追加式版本存储、索引、关系、
生命周期、完整性检查和案例查询设施。它应当是工作区统一的决策索引与生命周期
权威；项目仓库内的 ADR、规范或 Agent 入口仍是具体代码与资产的执行权威，并以
`anchor` 接入主库。

`decision-record` 先前把通用能力错误地写成了 quant-lab 中心能力，现已修正。
对两套技能目录进行全量语义审计后，没有发现第二个“通用技能默认路由到
quant-lab”的案例。

## Omnicompany 设施确认

### 数据模型

统一对象为 `decision.record`，`kind` 支持 `decision`、`belief`、`comment`。
公共信封包含：

- `id`、`kind`、`statement`、不可变 `version`；
- `scope`、`project`、`track`、`applies_to`、`anchor`；
- `origin`、`confidence`、`authority`、`status`；
- `tags`、`aliases`、`links`、创建与更新时间；
- verification、projection、challenge 与 resolution 信息。

正式决策包含 chosen/rejected decision space、rationale、evidence、boundary 与
human override。关系边支持 `rests_on`、`supersedes`、`parent`、`related`、
`enforced_by`。

### 存储与 CRUD 边界

- 存储：`data/domains/decisions/library/records.jsonl`，追加式写入；
- 当前态：按 ID 折叠最新版本，索引位于同目录 `index.json`；
- Create：`omni decisions record` / library `upsert`；
- Read：`list`、`find`、`show`、`graph`、`impacted`、`versions`；
- Update：追加新版本；CLI 通过 `mark`、`link`、`challenge`、`resolve` 等受控
  生命周期操作暴露，不提供任意 JSON 原地编辑；
- Delete：library 层为 soft delete，不物理抹除历史；
- Repair：`doctor`、`reindex`、`dedup`。

因此这里是完整的 CRUD 设施，但“U/D”刻意采用版本化更新和软删除，而不是
危险的原地覆盖或硬删除。

本次 `omni decisions status` 观察到 5,587 条 active 记录（3,825 decisions、
705 beliefs、1,057 comments），并报告 17 条 unhealthy 既有记录。本次工作不
改写这些历史异常。

### 使用规则

1. 先 `find` / `list` 查重，再 `record`。
2. 决策记录写“为什么选、为什么不选”；长篇执行规则留在项目规范。
3. 项目规范路径通过 `anchor` 接入。
4. 用户已明确采用且实现已落地后才 `mark adopted`。
5. 记录后必须用 `show` 加至少一个独立查询复核。

## 全技能 quant-lab 硬编码审计

扫描根：

- `C:/Users/user/.agents/skills`
- `C:/Users/user/.codex/skills`
- `C:/Users/user/.codex/plugins/cache`

扫描表达式：

```powershell
rg -l -i --glob SKILL.md "quant-lab|quant_lab|quant lab" <skill-root>
```

结果：

- 总计扫描 299 份物理 `SKILL.md`（含已安装插件技能）；
- `.agents`：39 个命中；
- `.codex`：38 个命中；
- 已安装 plugin cache：38 份 `SKILL.md`，0 个 quant-lab 命中；
- 合并后：39 个逻辑技能、77 个物理文件；
- `plan-tracker` 只有 `.agents` 一份，其余命中大多为双份同步副本。

### 已修正的误硬编码

| 技能 | 原问题 | 修正 |
| --- | --- | --- |
| `decision-record` | 通用决策能力以 quant-lab 本地目录为默认权威 | Omnicompany 成为可用时的跨项目主库；仓库规范为执行锚点；quant-lab 只保留本地镜像适配器 |

### 合法的非量化技能命中

这些不是默认路由硬编码，不应机械删除：

| 技能 | 命中性质 |
| --- | --- |
| `external-mount` | quant-lab 是已验证的外部挂载样例；机制本身面向任意外部仓 |
| `personal-homepage` | 站点确实有 quant-lab 索引构建功能，是该站点真实能力 |
| `research-report` | 只记录曾被隔离的 quant-lab 污染残骸，作用是防止复活错误设施 |

### 本来就是 quant-lab 对象技能

以下 35 个技能在名称、description、操作对象和权威源上都明确属于 quant-lab。
保留其项目路径是正确的领域绑定，不属于通用能力误硬编码：

`account-trust-analysis`、`audit-hygiene`、`auto-pipeline`、
`backtest-engine`、`daily-pipeline-recommender`、`data-catalog`、
`data-freshness-monitor`、`data-ingest-backfill`、`data-pipeline`、
`data-quality-checks`、`event-text-pipeline`、`execution-sim`、
`exit-rules`、`exit-strategies`、`factor-eval`、`factor-library`、
`factor-mining`、`figure-gallery`、`journal`、`leaderboard`、`model-zoo`、
`morning-report`、`paper-account`、`paper-account-runtime`、`plan-tracker`、
`portfolio-builder`、`portfolio-optimizer`、`public-report-site`、
`recommender`、`regime-detector`、`schema-contracts`、
`sentiment-news-classifier`、`shared-lib-base`、`statistical-test-suite`、
`walk-forward-ml-ranker`。

把这些技能中的 quant-lab 路径全局替换掉会破坏它们的唯一真源，不是去硬编码。

## 防回归判定

以后新增或修改技能时：

- 通用技能不得把 quant-lab 作为默认工作目录、默认记录库或默认产物目录；
- 量化专用技能必须在 frontmatter description 中明确量化对象和项目边界；
- 通用技能可以列 quant-lab 为经过验证的适配器或案例，但必须同时写明不得将
  无关项目路由进去；
- 工作区级决策默认进入 `omni decisions`，仓库内规范通过 anchor 关联。
