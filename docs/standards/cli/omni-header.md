<!-- [OMNI] origin=ai-ide domain=omnicompany/standards ts=2026-05-01T00:00:00Z type=doc status=active -->
<!-- [OMNI] summary="OmniMark 文件头规范权威文档, v3 加 summary/why/tags 三字段" -->
<!-- [OMNI] why="给所有受管文件提供统一的身份标记格式, 让出问题时能追溯到是谁/什么时候/为什么写的" -->
<!-- [OMNI] tags=omnimark,header,standard,registry,provenance -->
<!-- [OMNI] material_id="material:standards.omnimark_header.spec.md" -->

# OmniMark 文件头规范 v3

> **权威实现**: `src/omnicompany/core/omnimark.py`
> **词汇约束**: `docs/taxonomy.yaml`
> **Guardian 规则**: OMNI-030 / OMNI-031 / OMNI-032 (命名纪律) / OMNI-033 (禁止别名)
>
> **2026-05-01 v3 升级 (用户拍板)**:
> - 加 `summary` / `why` / `tags` 三个核心管理字段, 让 "是什么 / 谁写的 / 内容简洁描述 / 为什么写 / tags" 五件事在头里一眼可见
> - 支持多行 [OMNI] 头, 长值用双引号包裹
> - 用户原话: "重要的是可管理"; 自由描述, 有需求再说

本规范覆盖两类:

1. **代码/文档文件内嵌头** (Python/Markdown/YAML, §格式 起)
2. **数据产物 sidecar** (JSON/JSONL/.db/.md 报告等, §数据产物 sidecar 变体 起 · 2026-04-23 I-20 扩, 2026-05-01 加三字段)

---

## 格式 (v3 多行支持)

所有受管文件 (Python / 一般注释类) 的第一行 (或 shebang/coding 声明之后) 起, 是连续的 [OMNI] 头行:

```python
# [OMNI] origin=<origin> domain=<domain> ts=<ISO8601> type=<type> status=<status>
# [OMNI] summary="一两句话讲这文件干嘛"
# [OMNI] why="为什么写在这个位置, 语义理由"
# [OMNI] tags=tag1,tag2,tag3
```

Markdown 和 HTML 文件使用 HTML 注释格式:

```markdown
<!-- [OMNI] origin=<origin> domain=<domain> ts=<ISO8601> type=<type> -->
<!-- [OMNI] summary="..." -->
<!-- [OMNI] why="..." -->
<!-- [OMNI] tags=... -->
```

YAML 文件用 # 注释 (跟 Python 同):

```yaml
# [OMNI] origin=<origin> domain=<domain> ts=<ISO8601> type=<type>
# [OMNI] summary="..."
# [OMNI] why="..."
# [OMNI] tags=...
```

**多行解析规则**: 解析器从第一个 [OMNI] 行开始, 连续的 [OMNI] 行都被合并解析; 一旦遇到非 [OMNI] 行 (空行 / docstring 起始 / 代码), 停止. 这样 docstring 内嵌的 [OMNI] 示例不会覆盖真头.

**单行长版 (兼容)**: 如果嫌多行麻烦, 仍可以单行写所有字段, 长值带引号即可:

```python
# [OMNI] origin=ai-ide ts=2026-05-01T00:00:00Z type=worker summary="..." why="..." tags=a,b,c
```

---

## 字段说明 (v3)

### 核心五字段 (用户 2026-05-01 拍板)

| 字段 | 必填 | 说明 |
|---|---|---|
| `type` | 是 | **这个是什么** — 实体类型, 见 taxonomy.yaml `entity_types` |
| `origin` (+ `agent` + `trace`) | 是 | **谁写的** — 写入者来源; LLM 写时填 agent, 管线产时填 trace + node |
| `summary` | 是 | **内容简洁描述** — 一两句话讲这文件干嘛 (双引号包裹长值) |
| `why` | 是 | **为什么写** — 这文件为什么写在这个位置, 语义理由 (双引号包裹长值) |
| `tags` | 是 | **tags** — 分类/搜索/归档用的标签集合, 逗号分隔 |

### 时间与状态

| 字段 | 必填 | 合法值 | 说明 |
|---|---|---|---|
| `ts` | 是 | ISO8601 (`2026-05-01T00:00:00Z`) | 创建时间 (不随编辑更新) |
| `status` | 非 active 时必填 | 见 taxonomy.yaml `status_values` | 缺省为 `active` |

### 来源细分

| 字段 | 必填 | 合法值 | 说明 |
|---|---|---|---|
| `origin` | 是 | 见 taxonomy.yaml `origin_values` (含 `ai-ide` / `human` / `omnicompany` / ...) | 文件产生来源 |
| `agent` | LLM 产生时必填 | 模型 ID 字符串 | 产生该文件的模型 |
| `trace` | 管线产生时必填 | trace ID | 产生该文件的管线 trace |
| `node` | 管线产生时必填 | node ID | 产生该文件的管线节点 |

### 域归属 (强烈建议)

| 字段 | 必填 | 合法值 | 说明 |
|---|---|---|---|
| `domain` | 强烈建议 | `<category>/<name>` 格式 | 所属业务域 |
| `module` | 严格成员建议填 | 注册系统中的 module 路径 | 供注册系统快速定位实体 |

### 自我画像关联 (2026-05-04 起, CORE-SELF-STABILITY plan 第一阶段加)

| 字段 | 必填 | 合法值 | 说明 |
|---|---|---|---|
| `belongs_to_service` | 严格成员强烈建议填, 其他建议填 | service 子目录名 (如 `docauthor` / `guardian` / `registry` / `agent`) | 这文件属于哪个 service. self-check 验证: 文件路径必须含 `packages/services/(_<group>/)?<service_name>/` 形态. |

**写入规则**:
- 值是 service 目录名 (路径中 `packages/services/` 的下一级或下下级目录, 跳过下划线开头的 group 段如 `_authoring` / `_core`)
- 一个文件只能属于一个 service (跨 service 共享的文件应放 packages/services/_shared/ 或独立 commons 包, 不双归属)
- packages/services/ 之外的文件 (例 docs/ / scripts/ / src/omnicompany/core/) 不需要填这个字段, 留空即可

**后续阶段预留字段**:
- `verifies_rule` — 引用 Guardian 规则 ID (例 OMNI-014). 第二阶段 (Guardian 集中规则注册表建好后) 才启用. 当前不要写.

---

## 示例 (v3 完整版)

**Python 工人文件** (人类编写):

```python
# [OMNI] origin=human domain=demogame/table_learning ts=2026-04-12T00:00:00Z type=router status=active
# [OMNI] summary="从 CSV 头两行抽取表结构, 不带语义判定"
# [OMNI] why="demogame 服务包需要把表结构作为材料独立暴露, 给下游字段语义工人消费"
# [OMNI] tags=demogame,table,schema,extractor
```

**管线自动生成的 Python 工人**:

```python
# [OMNI] origin=workflow-factory domain=demogame/table_learning ts=2026-04-12T08:30:00Z
# [OMNI] type=router agent=qwen3.6-plus trace=01ABC123 node=router_writer
# [OMNI] summary="自动生成的 demogame 表语义抽取工人"
# [OMNI] why="补 demogame 表学习管线缺失的语义抽取节点, 由 workflow-factory 在 trace 01ABC123 自动产出"
# [OMNI] tags=demogame,table,semantic,auto-generated
```

**Markdown 文档** (废弃状态):

```markdown
<!-- [OMNI] origin=human domain=doctor ts=2026-03-01T00:00:00Z type=doc status=deprecated -->
<!-- [OMNI] summary="旧的诊断者设计文档, 已被新版取代" -->
<!-- [OMNI] why="保留作历史参考, 新设计见 docs/standards/cli/omni-header.md" -->
<!-- [OMNI] tags=doctor,deprecated,history -->
<!-- 替代文档: docs/standards/cli/omni-header.md -->
```

**临时脚本**（位置按 [目录清洁度守则](../_global/directory_cleanliness.md)：项目已声明临时区优先，无声明时为项目 `.omni/tmp/<task-or-session>/`；下例只说明头部格式）：

```python
# [OMNI] origin=human domain=demogame ts=2026-04-12T00:00:00Z type=scratch status=draft
# [OMNI] summary="临时跑数脚本, 验证 demogame 配表 LineGroup 字段差异"
# [OMNI] why="跑数验证用, 跑完结果进 absorption 后此脚本应清理"
# [OMNI] tags=scratch,demogame,linegroup,one-shot
```

---

## 版本标记规则

**禁止在文件名中出现版本标记**. 版本通过以下方式管理:

- **历史版本** → git history (`git log -- <file>`)
- **"这是旧设计"** → 文件内声明 + `status=deprecated` + 替代路径注释
- **"两个版本并存"** → 应重新设计为两个有明确边界的模块, 各自独立命名
- **API 命名空间目录** (如 `api/v1/`) → 允许, 但目录内需有 `DESIGN.md` 声明并存原因

---

## 严格成员要求 (Phase 2 起强制)

加入注册系统后标记为 `strict_member: true` 的实体, 所有文件必须:

1. 第一行有合法 `[OMNI]` 头
2. `type=` 字段存在且值在 taxonomy.yaml `entity_types` 列表中
3. `domain=` 字段存在且格式为 `<category>/<name>`
4. **2026-05-01 加**: `summary` / `why` / `tags` 三个新字段存在且非空
5. 文件名不匹配任何 `forbidden_filename_patterns`
6. `domain=` 和 `type=` 字段的值不在对应的 `forbidden_aliases` 列表中

违规行为:

- 缺失头 → 触发 OMNI-033 (warn → block)
- 版本化文件名 → 触发 OMNI-030 (所有文件, 立即 warn)
- test_*.py 前缀 → 触发 OMNI-031 (所有 Python 文件, 立即 warn)
- temp_*/tmp_* 在非 scratch 目录 → 触发 OMNI-032 (立即 warn)
- 使用别名 → 触发 OMNI-033 (严格成员, warn → block)
- **2026-05-01 待加规则**: v3 五字段缺失 → 新规则 (草稿)

---

## 罚单文件 (Fine File · 2026-05-01 用户拍板细节)

文档/脚本/测试放置位置不正确时, 守护机制将:

1. 把文件内容作为"非法写入材料"归档到归档区 (例如 `omnicompany/.archive/illegal_writes/<日期>/<原路径>`)
2. 在原位置留下同名罚单文件 (用户原话"原位置, 用来提醒写入者"), 内容格式:

```
# [OMNI] origin=guardian-stamp domain=omnicompany/meta ts=<ISO8601> type=doc status=quarantined
# [OMNI] summary="这个位置发生过一次未注册写入, 被罚单化"
# [OMNI] why="写入者没走 omnicompany CLI 注册流程, 内容已没收归档"
# [OMNI] tags=fine,illegal-write,reminder
# FINE: 此文件已被移动到归档区
# 归档位置: <归档区完整路径>
# 没收原因: <违规说明 — 没注册 / 路径错 / 类型错 / ...>
# 清理截止: <ISO8601, 7 天后>
# 处理: 走 omni register 把内容转正后删此罚单, 或等 7 天自动清理
```

**生存期**: 7 天 (用户 2026-05-01 拍板, 从原 30 天调整)
**双轨可查**: 7 天内罚单留在原位提醒写入者; 7 天后罚单清理但**没收的原内容仍在归档区永久可查** (作为非法写入材料归档)
**归档语义**: 没收的内容不是直接丢, 是作为一种特殊材料类型 (kind=illegal-write) 入归档区, 用户和 AI IDE 后续可以从归档区找回 / 转正 / 真删

罚单文件本身被守护机制跟踪. 超过截止日期未清理 → 自动清理罚单 (但归档不动).

---

## 文档就近存放约定

```
packages/domains/demogame/table_learning/
  ├── table_learning_pipeline.py      # type=pipeline
  ├── formats.py                      # type=format (多个 Format 对象)
  ├── routers/
  │     ├── benchmark_validator_router.py  # type=router
  │     └── benchmark_validator_router_test.py  # type=test (就近测试)
  └── .omni/
        ├── manifest.yaml             # Pipeline 综合声明档案
        └── health/
              ├── pipeline.jsonl      # 管线健康档案
              └── routers/
                    └── BenchmarkValidatorRouter.jsonl  # Router 健康档案
```

核心层结构:

```
packages/services/doctor/
  ├── routers.py                      # type=router (多个 Router)
  ├── pipeline.py                     # type=pipeline
  ├── DESIGN.md                       # type=doc (设计文档)
  └── .omni/
        └── health/
              ├── pipeline.jsonl
              └── routers/
                    └── <RouterClass>.jsonl
```

---

## 数据产物 sidecar 变体 (kind: data, 2026-04-23 I-20 + 2026-05-01 加三字段)

### 动机

代码/文档文件可内嵌 `# [OMNI]` 注释头, 但数据产物无法或不便内嵌:

- JSON 标准不支持注释
- `.db` / `.png` / `.pyc` 等二进制无头可加
- JSONL 逐行记录, 头会干扰消费

**方案**: 统一用 **sidecar 文件** `<data_path>.omni.json` 存署名元数据. 本体不改, 旁路文件带身份.

### 命名规则

任何数据产物 `foo.ext` 的 sidecar 是 `foo.ext.omni.json`:

| 数据产物 | sidecar |
|---|---|
| `report.json` | `report.json.omni.json` |
| `patrol.md` | `patrol.md.omni.json` |
| `events.db` | `events.db.omni.json` |
| `README` | `README.omni.json` |

### sidecar schema (v1.1, 2026-05-01 加 summary/why/tags)

```json
{
  "version": "1.1",
  "kind": "data",
  "written_by": "<module>.<class> 或 cli:<cmd>",
  "ts": "2026-05-01T10:45:00Z",
  "origin": "omnicompany",
  "summary": "一两句话讲这数据是什么",
  "why": "为什么这个工人/CLI 在这个时机产出这数据",
  "tags": ["tag1", "tag2"],
  "run_id": "optional, pipeline run 归属",
  "job_id": "optional, MaterialDispatcher job 归属",
  "trace": "optional, trace id",
  "source_path": "optional, 写入逻辑源码路径",
  "ttl_days": 30
}
```

### 合法写入入口范式

I-20 为 **"合法入口白名单 · 其余违规"** 范式提供基础设施:

- 任何 data/ 下写入必须由合法 Worker 产出并伴随 sidecar
- 无 sidecar 的文件 → 身份不明 → **违规候选**, 送 GuardianAgent LLM 复核
- LLM prompt 不枚举违规形式, 只给 "合法写入者白名单" (从 registry 获取), 其余即违规

### 调用方式

```python
from omnicompany.core.omnimark import write_data_sidecar

write_data_sidecar(
    report_path,
    written_by="omnicompany.packages.services.guardian.workers.HygieneScanWorker",
    source_path=__file__,
    ttl_days=30,
    summary="守护卫生扫描的违规清单报告",
    why="给用户和守护后续巡检看哪些目录有卫生问题",
    tags=("guardian", "hygiene", "report"),
)
```

反查:

```bash
omni guardian trace data/services/guardian/hygiene/hygiene-2026-05-01-xxx.json
```

### 与其他规则的协作

- **OMNI-049 老化**: aging policy 匹配 `*.json` 时, sidecar 自身 (`*.omni.json`) **不单独报告**, 与主文件共命运
- **OMNI-050 体积**: sidecar 通常 <1KB, 不会触发体积告警
- **OMNI-047 空目录**: sidecar 是文件, 其存在使目录非空; 若目录只剩 sidecar 而主文件被删, 应当由清理设施统一扫除

### 实装状态 (2026-05-01)

- ✅ `core/omnimark.py` `write_data_sidecar` / `read_data_sidecar` / `sidecar_path`
- ✅ `HygieneScanWorker` / `PatrolWorker` 落盘自动写 sidecar
- ✅ `omni guardian trace <path>` CLI
- ✅ **2026-05-01**: sidecar schema v1.1, 加 summary/why/tags 三字段
- 🔲 扩到其他 Worker (doctor / absorption 等) — 分波推进, 这次规范化时统一接
- 🔲 GuardianAgent LLM prompt 消费 sidecar 白名单
