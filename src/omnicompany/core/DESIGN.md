<!-- [OMNI] origin=claude-code domain=core ts=2026-04-17T00:00:00Z type=doc status=active -->
<!-- [OMNI] material_id="material:omnicompany.core.design.document.md" -->

# core · 设计文档

## 状态
- **版本**: V2（Move 8 统一 db 路径之后）
- **成熟度**: active
- **下一步**: dispatch pre/post hooks 已有初步接入点（probe baseline），后续会增加 crystallize trigger、audit flush 等

## 核心目的

`core/` 是 OmniCompany 的**跨模块基础设施**——提供：
- 管线调度入口（`dispatch()`）
- 管线注册表（`registry.py`）
- 数据目录解析（`config.py`）
- 受保护的文件写入（`guarded_write.py`）
- OmniMark 头解析（`omnimark.py`）
- 事件总线观察（`observe.py`）
- 架构地图（`archmap.py`）

这些是**其他模块依赖的"地基"**，不是业务功能。改这里影响面大，变动要跨模块验证。

它不解决的问题：
- 不定义协议（那在 `protocol/`）
- 不执行具体管线（那在 `runtime/exec/`）
- 不具体业务域（那在 `packages/domains/`）

## 核心接口

- **`dispatch(pipeline_name, input_dict, *, db_path=None, max_steps=None)`** — 管线调度主入口，集成 probe baseline 体检 / 事件总线打开 / PipelineRunner 构造 / run — [dispatch.py](dispatch.py)
- **`register(entry: PipelineEntry)`** / **`get_or_raise(name)`** — 管线注册与查询 — [registry.py](registry.py)
- **`discover()`** — 自动发现所有已注册管线（委托到 `pipelines.register_all`）— [registry.py](registry.py)
- **`resolve_db_path(domain)`** / **`resolve_db_dir(domain)`** / **`resolve_domain_data_dir(domain)`** — 路径统一解析（Move 8 之后 `data/events.db` 一站统一）— [config.py](config.py)
- **`write_file(path, content, *, writer, domain, purpose)`** — 受保护写入（检查 writer 白名单、路径合规）— [guarded_write.py](guarded_write.py)
- **`parse_omnimark(content)`** — 解析 `[OMNI]` 头字段 — [omnimark.py](omnimark.py)

## 架构决策

### D1 — dispatch 是唯一入口，不允许绕过

所有管线运行都走 `core.dispatch(pipeline_name, input)`。理由：
- dispatch 集中处理 registry 查询、domain 路径解析、bus 连接、pre-run hooks（probe baseline 等）、runner 构造
- 业务侧不应关心这些基础设施细节，一个函数调用就够
- 若跳过 dispatch 直接构造 runner，会丢失 probe baseline 等跨 cutting-concern 能力

_验证来源: [code] `src/omnicompany/core/dispatch.py::dispatch`（registry 查询 / hook 触发 / runner 构造集中点）_

### D2 — PipelineEntry 是 registry 的最小声明单元

每条注册的管线由一个 `PipelineEntry` 描述：
```python
PipelineEntry(
    name="absorption-module-driven",  # CLI 名 / dispatch key
    description="...",
    domain="absorption",           # data/ 路径分隔用
    build_pipeline=lambda: ...,    # () → PipelineSpec
    build_bindings=lambda args: ...,  # (args) → dict[node_id, Router]
    default_db_dir=...,
    cli_args=[...],
    default_max_steps=50,
)
```

这是"spec + 构造器"分离：spec 描述"管线长什么样"，构造器做"需要的时候实例化"。好处：注册期不需要实例化 Router（没有依赖 LLM client 等重资源）。

_验证来源: [code] `src/omnicompany/core/registry.py::PipelineEntry` dataclass + `register_pipeline` 调用点_

### D3 — Move 8：统一数据库路径

2026-04 之前每个 domain 有自己的 `data/<domain>/events.db`（13 个文件），dashboard 要 rglob join。
Move 8 后统一到 `data/events.db`（大多数 domain）+ `data/ide_events.db`（IDE 专用）。

- **`resolve_db_path(domain)`** 返回统一路径（按 domain 选 events.db / ide_events.db）
- domain 字段仍有意义：作为 `FactoryEvent.source` 写入，dashboard `source LIKE 'X%'` 过滤
- 旧路径仍保留读兜底（迁移期兼容）

_验证来源: [git-log] 2026-04 "Move 8" 数据库统一迁移 + [code] `core/config.py::resolve_db_path`_

### D4 — guarded_write 防写入脏数据

`data/` 下的写入必须走 `guarded_write(path, content, writer=..., domain=..., purpose=...)`：
- writer 白名单（`internal-engine` / `internal-guardian` / 特定 agent）
- path 必须在合法域（`data/<domain>/*`）
- origin=claude-code 不允许直接写 data/（只允许 internal writer）

理由：早期踩过"scratch 脚本把结果写到代码目录"的坑。guarded_write 强制显式声明 writer 身份。

_验证来源: [code] `src/omnicompany/core/guarded_write.py` writer 白名单 + 路径校验实现_

### D5 — dispatch 的 pre-run hooks 用 try/except 包裹，永不阻塞主路径

probe baseline 体检、crystallize trigger 等都是"锦上添花"能力。它们失败不应该让主管线跑不起来。

```python
try:
    maybe_probe_baseline(pipeline, domain=entry.domain)
except Exception:
    pass  # 永不阻塞
```

这是"信息审计不阻塞主路径"的 §9.2 风险对策的延伸。

_验证来源: [code] `core/dispatch.py::dispatch` 各 hook 位 `try/except Exception: pass` 包裹_

### D6 — OmniMark 头是跨模块元数据，不是单纯注释

每个 Python 文件的 `# [OMNI] origin=... domain=... ts=...` 头被 `omnimark.parse_omnimark(content)` 解析，供 Guardian、archmap、dashboard 等消费：
- Guardian OMNI-001 检查 origin 字段
- archmap 按 domain 画架构图
- dashboard 按 origin 分组"claude-code 写的" vs "human 写的"

约定在 docs/standards/omni-header.md。

_验证来源: [归纳] 从 Guardian OMNI-001/024 等规则对 OmniMark 的消费归纳；[code] `core/omnimark.py::parse_omnimark`_

## 数据流 / 拓扑

**dispatch 调用链**：

```
CLI / 业务代码
    ↓ dispatch("pipeline-id", input, max_steps=...)
    ↓
core/dispatch.py
    ├─ load_dotenv()
    ├─ registry.get_or_raise(name) → PipelineEntry
    ├─ entry.build_pipeline() → PipelineSpec
    ├─ entry.build_bindings(input) → {node_id: Router}
    ├─ config.resolve_db_path(entry.domain) → Path
    ├─ maybe_probe_baseline(pipeline, domain)  ← M2 pre-hook
    └─ async with SQLiteBus(db):
           PipelineRunner(spec, bindings, bus, max_steps=...).run(input)
              ↓ (runtime/exec/runner.py)
```

**registry 发现链**：

```
app 启动 / CLI / dispatch()
    ↓ core.registry.discover()
    ↓ core/pipelines.py → register_all()
    ├─ packages/services/*/run.py 里的 register() 被调
    ├─ 每个业务域的 PipelineEntry 被塞入 _REGISTRY
    └─ get(name) / list_all() / names() 可查
```

## 已知局限

1. **dispatch 的 hook 系统是 ad-hoc**，当前只硬编码了 `maybe_probe_baseline`。未来 crystallize trigger / audit flush / intent tracing 等 hook 是堆在一起还是用统一的 hook registry 待定。升级路径：加一个 `PreRunHook` / `PostRunHook` 协议。

2. **registry 无"版本化"** — 同一个 pipeline_name 只能注册一个 build_pipeline。想对比 v1 vs v2 得用不同 name。不方便做 A/B 或灰度。

3. **guarded_write 的 writer 白名单硬编码** — 新 agent 类型要加 writer 值得改代码，无动态注册。小痛点。

4. **OmniMark 头还没对 YAML / Markdown 完全统一** — Python 用 `# [OMNI]`，Markdown/YAML 约定 `<!-- [OMNI] -->`，但执行不一致。

## 参考资料

- 关联 protocol：[protocol/DESIGN.md](../protocol/DESIGN.md)
- 关联 runtime：[runtime/exec/DESIGN.md](../runtime/exec/DESIGN.md)
- 关联规范：docs/standards/omni-header.md / distributed-docs.md
- 关联 plan：`docs/plans/[2026-04-15]INFO-SUFFICIENCY/FOUR_TIER_PLAN.md`（dispatch 接入 probe baseline）

## 接收意愿

core/ 是 omnicompany 的**可执行基础设施底座** (archmap / config / guarded_write / omnimark / dispatch 等公共能力). 对外接收意愿:

- **接收**: 任何需要"架构地图查询 / 安全写盘 / 配置解析 / Material 分发"能力的组件, 都应通过 core 提供的公共 API 引用, 不要另起炉灶
- **不接收**: 具体业务逻辑 / domain-specific 数据处理 (归 services/domains); LLM 调用封装 (归 runtime/llm/)
- **边界信号**: 若某 core 模块被 < 2 个不同 service 引用, 说明它可能属于那个唯一使用者, 应下沉; 若 core 模块开始含业务决策 (而非基础设施), 应拆出
