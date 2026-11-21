<!-- [OMNI] origin=ai-ide domain=omnicompany/standards/_global ts=2026-05-02T07:30:00Z type=doc status=active agent=ai-ide-current -->
<!-- [OMNI] summary="omnicompany 概念三层分类 - AI 控制体系 6 种 / 文档体系 2 种 / 元模板 1 种" -->
<!-- [OMNI] why="memory 里有但 standards 没明确写. 用户 2026-05-01 立的分类铁律, 不再用平铺八种" -->
<!-- [OMNI] tags=standard,concept,classification,foundation -->
<!-- [OMNI] material_id="material:standards.global.concept_three_layer_classification.md" -->

# omnicompany 概念三层分类

> **状态**: 规范 v1.1 (2026-05-02 更新, 加 material 伞概念), 用户 2026-05-01 + 05-02 立
> **关联**: `concepts/template.md` §一 (template 元层定义) + `directory_structure.md` (目录组织)

## 〇、 material 是伞概念 (2026-05-02 立)

**用户原话 (2026-05-02)**: "一切都是 material, data 也是 material 的一种 — material 是一类东西而不是一个东西."

含义:
- **material** 是**伞概念** (umbrella) — 系统内一切已注册有语义的"东西"都是 material
- 下面三层共 9 种 (agent / hook / tool / team / worker / material(代码契约) / data / plan(=doc) / template) 都是 material 的**子类型**
- 加上 G2 后续扩展的 `external_pointer` (二进制/外部项目指针) / `meta_io` (元 IO 原子) 也是 material 子类型
- "material" 这个词在两层意义上用:
  1. **伞**: 一切已注册的东西
  2. **子类型 (代码层)**: AI 控制体系里的 1 种, 等价于 `Format` (数据契约), 跟 worker / team 等并列 — 这层称"代码 material" 或继续叫 material 都行, 看上下文消歧

注册到 G2 时 type 前缀 (`material:` / `data:` / `template:` / `external_pointer:` / 等) 是**子类型标签**, 不是兄弟分类. id 主体 (`<scope>.<service>.<subject>.<role>.<format>`) 表达具体语义.

## 一、 三层分类全图

omnicompany 把核心概念分**三层** (都属于 material 伞概念下), 不是平铺八种:

| 层 | 数量 | 概念 | 落点 | 典型用法 |
|---|---|---|---|---|
| **AI 控制体系** | 6 种 | agent / hook / tool / material / team / worker | `src/` 内 | 跑业务 / 跑诊断 / 跑发布 |
| **文档体系** | 2 种 | data / doc(plan/report) | `src/` 外 | 给人看 + 知识沉淀 |
| **元模板** | 1 种 | template | `templates/` | 立其他 9 种实例的样板 |

合计 9 种 (含 template). 旧叙述里的"八种"是历史称呼 (template 没算入), 不再用.

## 二、 层一 · AI 控制体系 (6 种)

落 `src/omnicompany/` 内. 是 omnicompany 的执行核心 — 跑业务管线 + 跑守护 + 跑诊断都靠这 6 种.

| 概念 | 说明 | 真基类 |
|---|---|---|
| **agent** | LLM 驱动的复杂工人, 内部 mini-team (LLM 调用 + 工具调用 + 上下文压缩) | `services/_core/agent/loop.py.AgentNodeLoop` |
| **hook** | 外部信号 → 内部 material 的入口工人 (有入无出, 事件驱动) | `protocol/hook.py` `BaseHook / PeriodicHook / EventHook` |
| **tool** | material 跨外部边界的搬运工 (读外 / 写外) | `protocol/tool.py` `BaseTool / AsyncBaseTool` + `services/_core/agent/routers/single_tool.SingleToolRouter` |
| **material** | 数据契约 / 流转单元 (alias of `Format`) | `services/_core/omnicompany.Material = protocol/format.Format` |
| **team** | worker + edge 拓扑组成的执行图 | `protocol/team.TeamSpec` |
| **worker** | 处理 material 的最小执行单位 | `runtime/routing/router.Router` 跟 `protocol/anchor.AnchorSpec` |

**v2 配置驱动外包装** (跟 ConfigurableAgent 同路线, 已就位):
- `services/_core/agent/configurable.ConfigurableAgent` + `AgentSpec`
- `services/_core/configurable.ConfigurablePeriodicHook` + `ConfigurableEventHook` + `HookSpec`
- `services/_core/configurable.ConfigurableTool` + `ConfigurableAsyncTool` + `ToolSpec`

material / team / worker 暂不需要 ConfigurableXxx 外包装 (它们是声明式数据结构, 直接 dataclass / pydantic 已够).

## 三、 层二 · 文档体系 (2 种)

落 `src/omnicompany/` 外 (`docs/` / `data/` / `templates/`). 给人看 + 知识沉淀, 不直接跑.

| 概念 | 说明 | 落点 |
|---|---|---|
| **data** | 内容性资料 (业务事实 / 调研结果 / 知识库) | `data/<domain>/` |
| **doc** | 文档 — 含两子类型: <br> · `plan` 过程记录 <br> · `report` 阶段产出 | `docs/plans/` / `docs/reports/` |

DESIGN.md 是 team 的内部组成 (跟 pipeline.py / formats.py 同地放), 不另立"design" 类. 算到层一 team 里.

## 四、 层三 · 元模板 (1 种)

落 `templates/` 目录.

| 概念 | 说明 | 落点 |
|---|---|---|
| **template** | 元层 — 立其他 9 种实例的样板 (含范本 / 骨架 / 向导 / 注册件四件套) | `templates/<kind>/` |

template 不开放给业务用户走 `omni new` 创建 (元模板由项目维护者维护).

## 五、 注册中心 9 种 type 对应

`services/_core/registry/meta.py` 里注册的 9 种 EntityTypeDef 类型, 跟本三层分类的对应:

| 三层 | 概念 | registry type | data_dir |
|---|---|---|---|
| 一 | worker | `router` (旧名 grandfathered) | `data/services/registry/router/` |
| 一 | material | `format` (旧名) | `format/` |
| 一 | team | `pipeline` (旧名) | `pipeline/` |
| 一 | agent | `agent_loop` | `agent_loop/` |
| 一 | hook | `hook` | `hook/` |
| 一 | tool | `tool` | `tool/` |
| 二 | data | `data` | `data/` |
| 二 | plan (doc 子类) | `plan` | `plan/` |
| (扩展) | 元 IO | `meta_io` | `meta_io/` |

注: `report` (doc 子类) 当前没单独 registry type, 走 `data` (因为 report 也是内容性资料). 后续如需独立可加.

template 不在 registry 里 (元层不注册自己).

## 六、 跟 directory_structure 的对应

| 概念 | src 内 / 外 | 标准位置 |
|---|---|---|
| worker / material / team / agent / hook / tool | src 内 | `src/omnicompany/packages/services/<bucket>/<service>/` 或 `domains/<domain>/<sub>/` |
| data | src 外 | `data/<domain>/<file>` |
| plan (doc) | src 外 | `docs/plans/<topic>/[YYYY-MM-DD]<NAME>/` |
| template | src 外 | `templates/<kind>/` |

`src/` 跟 `data/`+`docs/`+`templates/` 严格不交叉. 这是层一 vs 层二+三的边界.

## 七、 反模式

**用旧"八种"称呼 + 把 template 当第八** — 旧叙述把 template 算第八种, 但 template 是元层不是平层. 用三层分类避免歧义.

**把 data 跟 material 混淆** — 两者完全不同:
- data 是文件型内容性资料 (静态, 给人看)
- material 是 worker 间流转的运行时数据契约 (动态, 给系统用)

**plan 跟 DESIGN.md 当独立概念** — DESIGN.md 是 team 的内部组成, 不另立类. plan 是过程记录文档 (doc 子类), 跟 DESIGN.md 完全不同.

**层一不在 src 内** — 层一是 AI 控制体系核心, 必须落 src 内. 例 `templates/team/` 里的 yaml 不是 team 实例 (是模板), 真 team 在 `src/.../packages/services/`.

**ConfigurableAgent 跟 hook/tool 不一致** — 三个 ConfigurableXxx 路线必一致 (基类 + SPEC + override 钩子). 偏离会让模板转正流程出错.

## 八、 演进

- **registry type 名跟 omnicompany 名对齐** — 目前 router/format/pipeline/agent_loop 是 protocol 层旧名. 长期可加新 type (worker/material/team/agent) 跟旧名 alias 共存
- **report 类独立 registry type** — 当前合并到 data, 后续按需求拆
- **新 kind 加入** (例 knowledge / prompt_template) 走 `register_type()` API, 跟本三层分类讨论归属

## 九、 实施引用

- `omnicompany/src/omnicompany/packages/services/_core/registry/meta.py` - 9 种类型定义
- `omnicompany/src/omnicompany/packages/services/_core/agent/configurable.py` - ConfigurableAgent
- `omnicompany/src/omnicompany/packages/services/_core/configurable/` - ConfigurableHook + ConfigurableTool
- `omnicompany/templates/` - 9 种模板四件套
- `omnicompany/docs/standards/concepts/template.md` §一 - template 元层定义
- `omnicompany/docs/standards/_global/directory_structure.md` - 目录组织规范
