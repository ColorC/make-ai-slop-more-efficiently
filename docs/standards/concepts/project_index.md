<!-- [OMNI] origin=claude-code domain=standards/concepts ts=2026-06-13T03:00:00Z type=standard status=active -->
<!-- [OMNI] summary="PROJECT_INDEX.md 契约 — 每个项目唯一的元数据权威源, frontmatter 必填四键 + 可选字段, 被 projects_registry / dashboard / omni project CLI 消费" -->
<!-- [OMNI] why="契约此前只活在代码常量 (projects_registry.INDEX_REQUIRED_KEYS) 和 5 个实例里, docs/standards 无条目, 违反元规范'唯一权威源'要求 (2026-06-13 二重权威调研坐实'缺权威')" -->
<!-- [OMNI] tags=standards,concepts,project,index,registry -->
<!-- [OMNI] material_id="material:standards.concepts.project_index_contract.md" -->

# PROJECT_INDEX.md 规范

> **权威**: 本文件（从真源提取: `core/projects_registry.py` 的解析/校验逻辑 + 现存实例）。
> 改字段契约时必同步 `projects_registry.INDEX_REQUIRED_KEYS` 与本文件, 不一致即出问题信号。
> **合规样本**: [`src/omnicompany/dashboard/PROJECT_INDEX.md`](../../../src/omnicompany/dashboard/PROJECT_INDEX.md)（驾驶舱项目, 字段全、活跃消费中）

## 一 · PROJECT_INDEX.md 是什么

每个项目（projects.json 注册表里的一条）有且仅有一份 PROJECT_INDEX.md, 是该项目元数据的**唯一权威源**:
项目是什么、根目录在哪、入口在哪、最近发生了什么、有哪些一键可做的工作。
注册表 (`data/registry/projects.json`) 只存身份与归属（id/name/group/plan_categories/index_path 等）,
"项目长什么样"全部以 index 文件为准, dashboard 与总控读到的是同一份。

放置位置: 项目主根目录下（如 `src/omnicompany/dashboard/PROJECT_INDEX.md`）;
立项期项目可临时放计划类目根（如 `docs/plans/remote-control/PROJECT_INDEX.md`）, 落地后迁主根。

## 二 · frontmatter 契约

文件以 YAML frontmatter 开头（`--- ... ---`）, 解析入口唯一:
`core/projects_registry.parse_index_file()`（20s TTL 缓存版 `parse_index_file_cached`）。

**必填四键**（缺任一解析报错, dashboard 标 index_ok=false）:

| 键 | 类型 | 语义 |
|---|---|---|
| `omni_project` | str | 项目唯一 id, 与 projects.json 的 id 一致 |
| `name` | str | 人类可读项目名 |
| `group` | str | 分组: omnicompany / demogame / indie-game / other（可扩展） |
| `roots` | list[{path, note}] | 项目根目录列表, 每条带用途说明 |

**可选推荐键**:

| 键 | 类型 | 语义 |
|---|---|---|
| `updated` | date | 最近一次实质性更新 |
| `entry_points` | list[{path, note}] | 关键入口（主进程/核心模块/CLI） |
| `latest` | list[str] | 最新进展, 由新至旧, 每条带日期 |
| `quick_actions` | list[{label, skill, where, desc}] | 一键工作项; `skill` 无对应注册技能时**必须填 null**, 禁止捏造 skill 名 |
| `links` | list[{label, url}] | 相关链接（本地服务/外部文档） |

## 三 · 消费方（改契约前查这些）

- `core/projects_registry.enrich_projects()` — 读 index 算 last_active / index_ok / quick_actions 浮出
- dashboard `GET /api/projects` 与项目详情页 — 首页项目工作板 / 详情五页签的数据源
- `omni project show` CLI — 总控看到同一份
- 治理部门 plan 归属(`resolve_project_plans`)只依据 plan_governance 覆盖表与 plan_categories 前缀, **不**读 index

## 四 · 反模式

- 同一项目多份 index 或字段散落别处再抄一份 — 违反唯一权威源
- quick_actions 填不存在的 skill 名（2026-06-12 治理清理过 14 个捏造值）
- latest 写成流水账不带日期、或长期不更新（dashboard 的 last_active 会失真）
- 把项目"是什么"写进 projects.json 的 desc 长文 — desc 只一句话, 长内容归 index 正文

## 五 · 正文（自由结构, 建议节）

frontmatter 之后是给人读的正文, 常见节: 概况 / 当前进展 / 主要目录 / 能做什么 / 常见展开方式。
正文不进解析器, 不做硬校验。
