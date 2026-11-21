<!-- [OMNI] origin=claude-code domain=services/registry ts=2026-05-04T10:00:00Z type=doc status=active belongs_to_service=registry -->
<!-- [OMNI] material_id="material:core.registry.system_design.document.md" -->

# registry · 设计文档

> 设计目的请看 [README.md](README.md) (语境 / 解决什么 / 最终目标). 怎么用请看 [SKILL.md](SKILL.md) (CLI 命令 / 操作步骤 / 故障排查). 本文档专管**架构内部** (接口 / 决策 / 数据流 / 局限).
>
> 形态: 行政部元服务库 (见 [terminology §2](../../../../../../docs/standards/_global/terminology.md) · 服务全公司的基础设施).

## 状态
- **版本**: V1 (2026-04-20 Stage 1 Team 3 迁移完成 + 2026-05-04 G2 索引并入)
- **成熟度**: active
- **下一步**: Phase 1 新 runtime 到位后, 加入 agent_worker 类型 (取代 agent_loop)

## 管理的六元类型 (protocol 层基础)

| 注册类型 | omnicompany 对应 | 注册对象示例 |
|---|---|---|
| `format`  | **Material 注册** | `Format(id="guardian.file_context_set", ...)` |
| `router`  | **Worker 注册** | `class FooRouter(Router): ...` |
| `pipeline` | **Team 注册** | `PipelineSpec(id="selftest", ...)` |
| `agent_loop` | **Agent Worker 注册** (过渡期) | 旧 AgentNodeLoop 子类; 新 Agent Worker 见 R-19 |
| `tool` | **Tool Script Worker 注册** | `Tool(name="read_file", ...)` |
| `hook` | Hook | `Hook(name="pre_run", ...)` |

外加: `material_id_index` (CORE-SELF-STABILITY 第一阶段加, G2 索引, 不属六元但也是 registry 管的注册数据).

## 核心接口

- **MetaTypeRegistry**（[meta.py](meta.py)）: 元类型注册（六元 + 开放扩展 `register_type()`）
- **InstanceRegistry**（[instance.py](instance.py)）: 实体实例存储（JSONL per-type）
- **Scanner**（[scanner.py](scanner.py)）: AST 扫描源码 → 产 InstanceEntry
- **RegistryQuery**（[query.py](query.py)）: 链式查询 API（`.type().package().tag().execute()`）
- **HealthArchive**（[archive.py](archive.py)）: 健康快照历史
- **IncrementalDiagnosis**（[incremental.py](incremental.py)）: git diff 驱动的增量扫描

## 架构决策

### D1 — 六元注册是 protocol 层基础, 不改名

六元类型名（format / router / pipeline / agent_loop / tool / hook）保留 protocol 层原命名。
这些是代码契约层的类型系统, 命名应通用稳定（对齐 terminology §6）。

omnicompany 新名映射（Material/Worker/Team/Agent Worker/Tool Script Worker/Hook）只在 DESIGN / SKILL / plan 叙述层体现, 不改 registry 内部实现。

### D2 — Meta 开放扩展

`register_type()` 允许新加类型（如未来的 `knowledge` / `prompt_template` / `data_source`）。
六元不是硬限制, 是起点。

### D3 — 不是 Team / 是"元服务库"（新迁移分类 · 类 C）

registry 没有 pipeline.py / routers.py / formats.py — 不按 Team 形态实现。
它是**行政部工具库**（shared infrastructure）, 供所有 Team 调用。
迁移动作极简（填 DESIGN + 概念映射 + 0 代码改动）, 不做 Worker 拆分。

### D4 — InstanceRegistry 存储方案

六元各一份 JSONL 文件（`data/registry/<type>.jsonl`）, 每行一条 InstanceEntry。
原因: 便于 git diff（文本可读）/ 便于增量 append / 便于按 type 独立加载。

### D5 — 健康档案与 HealthArchive 绑 git commit hash

HealthSnapshot 每次 doctor 诊断后追加一行（JSONL, `data/registry/health/`）, 绑定当前 git commit hash。
支持 `regressions_since(<commit_hash>)` 回归检测。

## 数据流 / 拓扑

```
源码变更 (git diff)
  ↓
IncrementalDiagnosis.run()
  ├── get_changed_files() → [file paths]
  ├── scanner.scan_file(path) → list[InstanceEntry]
  ├── InstanceRegistry.upsert(entries)
  ├── 触发对应 doctor 重诊断（外部）
  └── HealthArchive.append(snapshot)

查询路径:
query(reg).type(...).package(...).tag(...).execute() → list[InstanceEntry]
```

registry 不是数据流 Team, 没有 FORMAT_IN → FORMAT_OUT 管线。属于**被动库**形态。

## 已知局限

1. **增量扫描依赖 git** — `get_changed_files()` 只在 git 仓库内可用; 非 git 环境需要 full_scan。升级路径: 加文件系统 mtime 兜底。
2. **健康档案无压缩** — HealthArchive JSONL 无限追加, 长期运行会膨胀。升级路径: Phase 3 加滚动归档（按月）或 snapshot 差量存储。

## 参考资料

- **代码位置**: [meta.py](meta.py) / [instance.py](instance.py) / [scanner.py](scanner.py) / [query.py](query.py) / [archive.py](archive.py) / [incremental.py](incremental.py)
- **新架构规范**（Stage 1 Team 3 迁移依据）:
  - [terminology.md §6 两层命名](../../../../../../docs/standards/_global/terminology.md)
  - [format.md F-16 Material kind](../../../../../../docs/standards/concepts/material.md)
  - [router.md R-18 Worker 粒度 / R-19 Agent Worker](../../../../../../docs/standards/concepts/worker.md)
- **Agent Worker 迁移**: 当前 `agent_loop` 类型保留; Phase 1 runtime/agent 重构后新增 `agent_worker` 类型
- **migration_log**: Team 3 registry
