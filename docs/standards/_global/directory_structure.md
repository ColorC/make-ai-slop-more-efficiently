<!-- [OMNI] origin=ai-ide domain=docs/standards/_global ts=2026-05-01T00:00:00Z type=standard status=active agent=ai-ide-current -->
<!-- [OMNI] summary="目录结构规范, 立同级判定 / 收纳触发器 / 命名约定 / 反模式, 让目录组织有判定标准而不是凭感觉" -->
<!-- [OMNI] why="项目目录长期凭感觉组织 (services 38 个平铺) 边界不明, 立规范让'什么应同级 / 什么应收起来 / 什么时候多级化' 都有可判定的特征" -->
<!-- [OMNI] tags=directory,organization,naming,layering,foundation -->
<!-- [OMNI] material_id="material:standards.global.directory_organization_rules.md" -->

# 目录结构规范

## 一、 规范的根本

目录是**分类工具**, 不是**命名空间**. 把目录当成命名空间用就会出现:

- 一层平铺塞太多东西 (例如 38 个 service 平铺), 找东西靠 grep 不靠目录
- 同级里混了不同语义层级的内容 (例如 service 跟 domain 平铺), 概念边界糊
- 目录名重复语义 (例如 `services/agent/agent.py`), 命名空间套娃没意义

正确的目录组织: **每一级目录回答一个清晰的语义问题**. 进到子目录意味着"语义层级缩小一档" — 例如 `services/_core/` 缩到核心层服务, `services/_core/agent/` 进一步缩到具体一个核心服务. 不缩窄的目录 (例如 `misc/` / `utils/` / `helpers/`) 是反模式.

## 二、 同级判定 (横向)

两个目录或文件应该平铺在同一级当且仅当它们满足全部三条:

### 2.1 同语义层级

平铺的元素必须在同一抽象层级. 反例:

- `services/` 跟 `services/agent/` 平铺 (一个是分类目录, 一个是具体服务) — 错
- 单个 service 跟一组 service 子目录混在 `services/` 里 — 错

判定办法: 站在外部看, "这两个东西"的角色 / 颗粒度 / 生命周期是不是同一档?

### 2.2 同被消费方式

平铺的元素被外部访问 / 引用 / import 的模式应当类似. 反例:

- `services/` 装的全是 worker 业务代码, 但中间混进一份 `services/runtime_test_builder.py` 单文件 — 单文件跟整目录不是同消费方式

### 2.3 同生命周期

平铺的元素的"创建 / 修订 / 归档" 频率应当接近. 反例:

- 长期稳定的核心层 service (`agent` / `guardian`) 跟实验性 service (`absorption_runtime_test` / `code_runtime_test`) 平铺 — 生命周期不同, 应当分到不同子目录 (`_core/` 跟 `_experimental/` 等)

## 三、 收纳触发器 (纵向 — 何时进一步分级)

平铺到一定程度后, 下面的特征任一出现就触发**收到子目录**:

### 3.1 数量阈值

同级元素数量:

- ≤ 7 个: 平铺合理 (符合人类短期记忆 7±2 的认知容量)
- 8-15 个: 开始考虑分组, 看其他触发器
- ≥ 16 个: **必收子目录** (或者 grep 替代浏览)

当前 `services/` 38 个明显超阈值.

### 3.2 命名前缀重复

N 个元素带相同前缀, 触发收子目录. 例如:

- `repo_absorption / repo_architect / repo_learner` 已退役并移入 `_graveyard/services/_learning/repo/`；仍有独立价值的 `repo_exporter` 迁到 `_authoring/repo_exporter/`，不属于持续学习主线
- `kb_ingestion_agent / kb_multi_agent` → 收 `_learning/kb/`
- `hypothesis / hypothesis_library` → 收 `_learning/hypothesis/`
- `absorption_runtime_test / code_runtime_test / runtime_test_builder` → 收 `_utility/runtime_test/`

退役目录只保留历史实现，不参与活包发现；当前持续学习唯一入口见 `docs/projects/ai-learning-observatory/PROJECT_INDEX.md`。

### 3.3 共同消费方

N 个元素被同一类消费方调用, 触发收子目录:

- 例如 `docauthor / report_author / publish_pipeline / privacy_publish / publishing_commons` 都是发布 / 文档生成相关 → 收 `_authoring/` 子目录

### 3.4 语义聚合

N 个元素属于同一语义类别 (即使前缀不同), 触发收子目录:

- 例如 `agent / guardian / registry / repair / selftest / evolution` 都是核心系统类服务 → 收 `_core/` 子目录

### 3.5 抽象层级跨越

同级里出现明显跨抽象层级的元素, 触发**抽离**:

- 例如 `services/agent/` (抽象 agent 框架) 跟 `services/personal_site/` (具体业务网站) 不在同抽象层级 — `personal_site` 应当抽离到 `domains/personal_site/` 或类似业务区

## 四、 子目录命名约定

### 4.1 _ 前缀 (内部 / 元 / 特殊语义)

下划线前缀的子目录有约定语义, **不当业务子目录用**:

| 前缀 | 语义 | 例子 |
|---|---|---|
| `_global/` | 跨概念跨业务的全局规范 / 工具 | `docs/standards/_global/` |
| `_meta/` | 关于本目录或本类内容的元信息 | `docs/standards/_meta/standards-index.yaml` |
| `_core/` | 核心层 (区别于业务层) | `services/_core/` (核心系统服务) |
| `_archive/` | 历史档案, 不再活跃但保留 | `docs/plans/_archive/` |
| `_graveyard/` | 已废弃, 不让 import / 引用 | `src/omnicompany/_graveyard/` |
| ~~`_infra/`~~ | (2026-05-15 撤) plans/ 跨服务的中间层, 已替换为主题区单轴 + `omnicompany-` 前缀 | — |
| ~~`_cross/`~~ | (2026-05-15 撤) plans/ 跨多 package 中间层, 已撤; 新模型取主导一个主题区, 多 package 时按改动量取最大 | — |
| `_domain_specific/` | 特定 domain 的子内容 | `docs/standards/_domain_specific/demogame/` |
| `_experimental/` | 实验性 / 临时, 可能废弃 | `services/_experimental/` |

`_scratch/` 为历史存量位置，不再新建。一次性运行临时文件与待转正草稿分别按 [目录清洁度守则](directory_cleanliness.md) 的落点优先级处理；不得把所有临时运行输出都塞进草稿区，也不得因旧目录退役而直接删除或移动其内容。

下划线前缀的目录在排序时排在前面 (lexicographic), 自然区别于业务子目录.

### 4.2 业务子目录命名

不带下划线的子目录是业务类目录 (跟系统类区分):

- 用单数英文蛇形 (`agent`, `guardian`, 不是 `agents` / `Guardian`)
- 短名 (3-15 字符), 反映本子目录装什么
- 跟父目录语义有继承关系 (子目录是父目录的细化, 不是平行概念)

### 4.2.1 services/ 下 5 个 bucket 语义 (2026-05-02 立 J 管线 dogfood 时澄清)

`src/omnicompany/packages/services/` 下当前 5 个 `_` 前缀 bucket, 收纳边界:

| bucket | 语义 | 收什么 | 不收什么 | 现有范例 |
|---|---|---|---|---|
| `_core/` | 跨 bucket 共用**抽象 + 设施** (Lib 形态) | agent/hook/tool/team 抽象 · registry · protection · identity · configurable · meta_io 等基础设施 | 业务跑数据的 pipeline 不收 (跑数据归 `_diagnosis/`) | identity, registry, agent, configurable, meta_io, omnicompany, protection |
| `_diagnosis/` | 检查 / 审计 / 治理执行类 | 扫问题出报告 (doctor) · 清遗留文件 (cleanup_bot) · 规则审计 (lap_auditor) · 治理执行 pipeline (mass_materialization) | 不收纯抽象 (归 `_core/`) · 不收创作 (归 `_authoring/`) | cleanup_bot, doctor, domain_scout, lap_auditor, mass_materialization (待 promote) |
| `_authoring/` | 创作 / 写作类 | designer · writer · publisher · 文档生成 pipeline | 不收"读出报告" 类 (归 `_diagnosis/`) | designer 等 |
| `_learning/` | 学习 / 归纳类 | absorption (从 trace 学) · hypothesis (假设管理) · knowledge (知识库) · trace_induction | 不收"先验规则" 类 (归 `_core/`) | absorption, hypothesis, knowledge, repo, trace_induction |
| `_utility/` | 通用工具 / helper | 跨服务 helper · conversion · adapter | 不要变成 `misc/` 万能桶 (5.5 反模式) | (按需补) |

**判定流程 (新 service 该归哪个 bucket)**:

1. 这 service 是 **lib (无 pipeline)**? → `_core/`
2. 这 service 是 **扫 / 审 / 治理执行**? → `_diagnosis/`
3. 这 service 是 **创作 / 写**? → `_authoring/`
4. 这 service 是 **学 / 归纳**? → `_learning/`
5. 跨服务**可复用 helper**? → `_utility/`
6. **都不像** → 报告给用户, 不擅自立新 bucket (违反不变量第 8 条)

**新 service 声明**: 在 `_<bucket>/<service>/.omni/manifest.yaml` 声明 `parent_bucket: _<bucket>`, 跟规范对齐. `omni sandbox promote --target=...` 用本规范判定 target 路径合法性, 不合规直接拦.

### 4.3 service vs domain 边界 (用户原话提的边界澄清)

这是判 `services/` vs `domains/` 顶层的硬规则:

- **service**: 抽象框架 / 跨业务复用 / 给系统跑业务用. 例如 agent / guardian / registry / docauthor / csv_to_md (csv 是通用格式, 跨业务用)
- **domain**: 具体业务领域 / 私域 / 不跨业务. 例如 demogame (具体游戏) / voxelcraft (具体 mod) / personal_site (个人站, 私域) / business_explorer (具体业务探索, 跟 demogame 关联)

判定办法 (按用户原话"核心层和具体业务私域的区别"):

- 这个东西的代码可能被另一个不同的业务复用吗? 是 → service. 不是 → domain.
- 这个东西的 material schema 含具体业务实体名 (demogame.season_book / voxelcraft.entity_spec) 吗? 是 → domain. 不是 → service.
- 这个东西的产出是给具体某个客户 / 项目 / 业务用的吗? 是 → domain. 通用 → service.

## 五、 反模式

### 5.1 全平铺反模式

特征: 同级目录数量 ≥ 16 + 没有任何收子目录的尝试.
后果: 找东西靠 grep, 目录失去分类作用.
例: 当前 `services/` (38 个平铺), 当前 `docs/plans/` 修订前 (73 份平铺).

### 5.2 过度多级反模式

特征: 进到第 5 级以上才能找到具体内容.
后果: 路径长, 引用麻烦, 修改时打字错误率高.
判定: 一份内容的路径深度 (从项目根到文件) 超过 6 级 → 过度.
反模式例子: `src/omnicompany/packages/services/_authoring/docs/internal/style_guide.md` (6 级 packages 之下还层层嵌套).

### 5.3 命名不统一反模式

特征: 同级目录混不同命名风格 (复数 / 单数 / camelCase / 蛇形 / 中划线).
例: `services/csv_to_md` (蛇形) 跟 `services/asset-library` (中划线) 同时存在 — 选一种风格统一.

### 5.4 边界不明反模式

特征: 同级元素属于不同抽象层级 (service 跟 domain 混 / 框架跟实例混).
例: 当前 `services/personal_site/` 是具体业务私域不是框架 service, 应当挪到 `domains/personal_site/`.

### 5.5 misc / utils / helpers 反模式

特征: 子目录名是 `misc / utils / helpers / common` 这种"放不下的东西" 都塞这里.
后果: 反"语义层级缩小一档" 原则, 这些目录不缩窄反而扩张.
修法: 强迫重新分类 — 这里面装的内容到底是什么? 按真语义另立子目录, 不要 misc.

### 5.6 命名空间套娃反模式

特征: 子目录名跟父目录名字面重复.
例: `services/agent/agent.py` (agent 套娃 — 既是父目录名又是文件名).
修法: 子文件改名反映其在父目录里的具体角色 (`services/agent/loop.py` 就好).

### 5.7 单文件混入目录组反模式

特征: 一组目录里混进一份单文件 (不是目录).
例: `services/` 里大部分是 service 子目录, 但混进 `services/runtime_test_builder.py` 单文件.
修法: 单文件抽到 `services/_utility/` 或归到对应 service 子目录.

## 六、 跟现有目录结构的对应

本规范立后, omnicompany 项目里几个已经按规范多级化的实例:

- `docs/standards/`: _global / concepts / cli / protocol / _meta / _domain_specific 6 子目录, 25 份规范分类
- `docs/plans/`: _infra / _cross / domain/demogame / domain/voxelcraft / _archive 5 子目录, 73 份 plan 分类
- `src/omnicompany/_graveyard/primitives/`: 旧 primitives 归档区, 含原 DESIGN.md + __init__.py.archived

待按规范多级化的:

- `src/omnicompany/packages/services/`: 38 个平铺 (本规范立后下一步处理)
- `src/omnicompany/packages/domains/`: 4 个平铺 (数量 OK 但要看 service / domain 边界澄清后是否新增)

## 七、 不变量 (永远的硬约束)

无论怎么重排, 下面的规则永远不破:

1. 单文件 .py 不跟整目录平铺 (5.7 反模式)
2. 子目录跟父目录字面重名 (5.6 反模式)
3. 路径深度 (从项目根到文件) ≤ 6 级 (5.2 过度多级)
4. _前缀的目录不当业务子目录用 (4.1 约定)
5. service 跟 domain 边界判定按 4.3 (服务跨业务 vs 业务私域)
6. 每个目录回答一个清晰的语义问题 (一、 根本)
7. **新目录先确定归属** — 已授权任务在项目既有分区内创建必要目录可直接实施；新顶层项目、改变项目主目录或跨项目迁移应先明确归属和依赖，不能以建目录代替设计判断。已有用户授权不重复索取。
8. **不擅自立新 bucket** — `services/` 下 `_authoring / _core / _diagnosis / _learning / _utility` 5 个 bucket 是固定清单, 新 bucket 必先改本规范 §4.2.1 加范围 + 用户批准

## 八、 修订协议

修订本规范时:

1. 改之前先看本规范当前怎么定义"修订"
2. 改完跑一次现有目录结构的 audit, 看有没有新违反的
3. audit 过程与证据追加当前治理计划 `progress.md`，不为一次修订另建平行计划
4. 修订日期跟 OmniMark `ts` 字段同步

## 九、 跟其他规范的关系

- `_global/distributed-docs.md` 立六域结构 + 内容类型矩阵 — 本规范是"目录形态" 层, distributed-docs 是"内容归属" 层, 不冲突
- `concepts/template.md` §一 三层分类 — AI 控制体系 / 文档体系 / 元模板. 本规范的 `_core/` / `_authoring/` / 等是这三层的物理目录组织
- `cli/sandbox.md` 沙盒规范 — `.omni/sandbox/` 管待转正草稿；运行临时文件位置统一按 `_global/directory_cleanliness.md`，不扩大沙盒语义
- `_global/single_source_thin_wrap.md` 唯一源 + 薄包装铁律 — 跟本规范 `templates/<kind>/` (源) 跟 `.claude/skills/<X>/SKILL.md` (薄包装) 的关系一致
- `concepts/plan.md` plan 目录规范 — 本规范是父级, plan 规范是子规范的具体化
