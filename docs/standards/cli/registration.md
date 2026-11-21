<!-- [OMNI] origin=ai-ide domain=omnicompany/standards ts=2026-05-02T05:00:00Z type=doc status=active agent=ai-ide-current -->
<!-- [OMNI] summary="G2 注册中心规范 - omni register/lookup, 八种 kind 显式注册 + 跟 G1 trace_id 联动" -->
<!-- [OMNI] why="services/_core/registry 已实施 6 种 AST 扫描型, 加 data + plan 凑 omnicompany 8 种 + 显式注册入口让 AI IDE/agent 主动绑身份到内容" -->
<!-- [OMNI] tags=cli,register,registry,standard,G2 -->
<!-- [OMNI] material_id="material:standards.cli.registry_registration_protocol.md" -->

# G2 注册中心规范 (omni register / lookup)

> **状态**: 实装完成 2026-05-02
> **关联实装**: `services/_core/registry/` (复用) + `cli/commands/registration.py` + `dashboard/registry_api.py`
> **关联规范**: `omnicompany_cli.md` / `identity.md` / `sandbox.md`

## 一、 这是干嘛的

注册中心是 omnicompany 的**户籍系统** — 把项目内所有八种基础概念实例 (worker / material / team / agent / hook / tool / data / plan) 登记在案, 让守护 / 诊断 / 锁机制能定位每份内容的归属.

实施分两层:
- **AST 扫描层** (services/_core/registry/scanner.py, 已有) — 自动扫源码发现代码实体 (router class / Format() 实例 / Pipeline 等)
- **显式注册层** (G2 新加) — `omni register` 命令把不能 AST 扫描的内容 (data / plan / 沙盒草稿 / 业务文档等) 主动登记 + 绑当前 session trace_id

两层同库 (`data/services/registry/<type>/`), 通过 attrs.registered_via 区分 (`cli_explicit` vs `ast_scan`).

## 二、 八种 kind 类型

omnicompany 八种基础概念全部支持注册. 别名映射 (omnicompany 名 ↔ registry 内部名):

| omnicompany 名 | registry 内部 type | 数据目录 |
|---|---|---|
| worker | router | `data/services/registry/router/` |
| material | format | `data/services/registry/format/` |
| team | pipeline | `data/services/registry/pipeline/` |
| agent | agent_loop | `data/services/registry/agent_loop/` |
| hook | hook | `data/services/registry/hook/` |
| tool | tool | `data/services/registry/tool/` |
| **data** | data | `data/services/registry/data/` |
| **plan** | plan | `data/services/registry/plan/` |

最后两个 (data + plan) 由 G2 用 `register_type()` API 加, 凑齐 omnicompany 8 种. 其他 6 种是 services/_core/registry/meta.py 既有的.

## 三、 entity_id 格式

`{type}:{package}.{name}`

例:
- `router:demogame.team_table.SchemaAssembler`
- `format:demogame.season_book`
- `data:.omni.sandbox.drafts.data.season_research.season_research`
- `plan:docs.plans._infra.[2026-04-30]OMNICOMPANY-FORMAT-STANDARDIZATION.plan`

`type` 是 registry 内部名, package 是点分路径, name 是文件名 / 类名 / id. 写文件 `data/services/registry/<type>/<safe_id>.json`.

## 四、 InstanceEntry attrs 字段

```json
{
  "kind_omnicompany": "data",                  // 八种概念名 (不是 type)
  "trace_id": "cc_<sid>",                      // 注册时的 session 身份 (G1 联动)
  "registered_via": "cli_explicit",            // 区分 ast_scan vs cli_explicit
  "omnimark_header": {                         // OmniMark 头字段全保留
    "origin": "ai-ide",
    "ts": "2026-05-02T05:00:00Z",
    "type": "data",
    "summary": "...",
    "why": "...",
    "tags": "data,research"
  },
  "is_directory": false                        // 是文件还是目录 (例: team / plan 是目录)
}
```

## 五、 CLI 命令

### omni register --kind=<> --content=<>

显式注册一份内容到中心. 自动:
- 抓 OmniMark 头字段进 attrs
- 派生 entity_id (从 content 路径 + name)
- 绑当前 trace_id
- 写到 InstanceRegistry

```bash
# 注册沙盒草稿
omni register --kind=data --content=.omni/sandbox/drafts/data/season_research/season_research.md

# 显式给 name + package
omni register --kind=worker --content=src/omnicompany/packages/services/foo/bar.py \
              --name=BarWorker --package=services.foo

# 覆盖已存在
omni register --kind=data --content=... --force
```

### omni lookup [filters]

统一查询入口, 支持 8 种 kind + 6 种过滤维度.

```bash
omni lookup --kind=plan                       # 列所有 plan
omni lookup --id=router:demogame.foo.Bar         # 精确查
omni lookup --package=demogame                   # 按 package
omni lookup --trace-id=cc_xxx                 # 跟 G1 联动 - 看某 session 注册的
omni lookup --source=explicit                 # 只看显式注册
omni lookup --source=ast_scan                 # 只看 AST 扫描发现的
omni lookup --source=all                      # 默认, 两者都看
```

`--source` 区分:
- `explicit` — `omni register` 显式注册的
- `ast_scan` — Scanner 扫源码自动发现的
- `all` — 两者一起 (默认)

### omni register-types

列已注册 kind 类型, 验证 8 种齐.

```bash
$ omni register-types
已注册 8 种 kind 类型:
  format         (Material)  → data/services/registry/format/
  router         (Worker)    → data/services/registry/router/
  pipeline       (Team)      → data/services/registry/pipeline/
  agent_loop     (Agent)     → data/services/registry/agent_loop/
  tool           (Tool)      → data/services/registry/tool/
  hook           (Hook)      → data/services/registry/hook/
  data           (Data)      → data/services/registry/data/
  plan           (Plan)      → data/services/registry/plan/
```

## 六、 dashboard API (只读)

| 端点 | 内容 |
|---|---|
| `GET /api/v2/registry/types` | 已注册 kind 类型 (8 种) |
| `GET /api/v2/registry/instances?kind=<>&source=<>&limit=N` | 实体列表 (跟 omni lookup 同源) |
| `GET /api/v2/registry/instances/{entity_id}` | 单 entity 详情 |
| `GET /api/v2/registry/by-trace/{trace_id}` | 按 trace_id 查 (G1 联动) |

跟现有 `/api/teams /api/materials /api/workers` (走 AST 扫描) 不冲突, 两套并存. 长期目标是 catalogue API 优先用 G2 注册中心数据 + fallback 到 AST 扫描.

## 七、 跟 G1 / G3 / G5 联动

- **G1 身份** — `omni register` 时 attrs.trace_id 自动从 `current_session_meta()` 拿. `omni lookup --trace-id` 反查
- **G3 沙盒** — `omni sandbox promote` 流程内部调 `omni register` (sandbox/promote 默认带 register, 可 `--skip-register` 跳过)
- **G5 指引** — `omni new` 立草稿后, 用户走 `omni sandbox promote --kind=<>` 转正 + 自动 register

## 八、 反模式

**绕开 CLI 直接写 entity_id** — 直接 `reg.write(InstanceEntry(...))` 不带 trace_id 不带 OmniMark 头, 后续追溯失败.

**注册时不抓 OmniMark 头** — attrs 缺 origin / ts / summary 等字段, dashboard 显示时缺基本信息.

**`omni register` 不验证 content 在合法路径** — 任何路径都能注册, 失去管理意义. 当前实施允许任何路径但建议跟 G4 锁配合走 `omni sandbox promote` 流程.

**用 type 写注册中心却用 kind 查询** — 别名映射不做就两边混. CLI 层用 `_KIND_ALIAS` dict 统一转换.

## 九、 实施引用

- `omnicompany/src/omnicompany/packages/services/_core/registry/__init__.py` - registry 入口
- `omnicompany/src/omnicompany/packages/services/_core/registry/meta.py` - 8 种类型定义
- `omnicompany/src/omnicompany/packages/services/_core/registry/instance.py` - 存储
- `omnicompany/src/omnicompany/packages/services/_core/registry/query.py` - 链式查询
- `omnicompany/src/omnicompany/cli/commands/registration.py` - omni register / lookup CLI
- `omnicompany/src/omnicompany/dashboard/registry_api.py` - dashboard 只读 API

## 十、 演进点

- **catalogue API 切到 registry 优先** — 现有 `/api/teams /api/materials /api/workers` 是 AST 扫描型, 改成"先查 registry, fallback AST"
- **批量注册** — `omni register-batch --from=manifest.yaml` 一次注册多份 (跟 feedback_omnicompany_cli_register_before_write 对齐)
- **质量字段填充** — 当前 InstanceEntry attrs 不带 quality_fields (registry meta.py 定义了但没写). 后续诊断流程要消费这个
- **registry vs scanner 数据合并** — 当前两套各自存, dashboard 看到重复条目可能性. 长期合并到一份视图
