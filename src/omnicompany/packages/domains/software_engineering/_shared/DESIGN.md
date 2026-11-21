<!-- [OMNI] origin=claude-code domain=software_engineering/_shared ts=2026-04-25T00:00:00Z type=doc status=active -->
<!-- [OMNI] material_id="material:domains.software_engineering.shared_design_spec.md" -->

# _shared · 设计文档

## 状态
- **版本**: V1 (2026-04-25 从 skeleton 升级至 active，基于 common_formats.py 已落地)
- **成熟度**: active
- **下一步**: 将硬编码分片阈值迁移至 `.omni/manifest.yaml` 或配置中心，支持按模型上下文窗口动态调参；补充 JSON Schema 强校验注册到 FormatRegistry

## 核心目的
为 `software_engineering` 域内所有子管线（debugger / review / implement / tdd 等）提供统一的 **Format 定义基座**与 **数据分片/截断规则**。
- **解决**：9 种核心材料（task-input, project-snapshot, file-content, change-set 等）的命名规范、`sw.` 领域前缀统一，及面向 LLM 上下文窗口的分片标准。
- **不解决**：具体管线的业务逻辑编排（由各 sibling 包负责）、Worker 路由策略、LLM Prompt 模板生成、运行时健康诊断。

## 核心接口
- **`DOMAIN = "sw"`** — 统一领域前缀，用于 Format ID 构建 — [common_formats.py](common_formats.py)
- **分片阈值常量** (`MAX_FILE_CONTENT_BYTES`, `MAX_FILE_BATCH_SIZE`, `MAX_FILE_BATCH_BYTES`, `MAX_CHANGE_SET_SIZE`, `MAX_TREE_BYTES`, `AGENT_LOOP_THRESHOLD_FILES`) — 控制上下文裁剪与拆批边界 — [common_formats.py](common_formats.py)
- **9 项标准 Format ID** — `sw.task-input`, `sw.project-snapshot`, `sw.file-content`, `sw.file-batch`, `sw.code-change`, `sw.change-set`, `sw.test-exec-result`, `sw.llm-review`, `sw.report` — 作为跨包协议标识传递 — [common_formats.py](common_formats.py)
- **Format 注册依赖** — 依赖 `omnicompany.protocol.format.Format` / `FormatRegistry`，本包提供声明规范 — [common_formats.py](common_formats.py)

## 架构决策
### D1 · 统一 `sw.` 前缀与显式分片阈值集中管理
**决策**: 所有 SE 域 Format 强制使用 `sw.` 命名空间；分片规则（如 8KB 截断、5 文件/批）以模块级常量集中定义，禁止散落至各业务 Worker。
**理由**: 避免跨管线（如 `review` vs `implement`）对同一材料（如 `file-content`）的裁剪逻辑不一致，导致 LLM 上下文窗口溢出或丢失关键 imports/signatures。集中常量便于后续统一调优与 Guardian 静态审计。

### D2 · 本包严格限定为协议/格式定义层，不承载执行逻辑
**决策**: `_shared/` 仅承载 `Format` 声明、ID 命名规范与分片阈值常量；不引入任何 `Worker`、`Pipeline` 构建或 `Router` 代码。
**理由**: 遵循分布式文档 §二 域边界原则。执行逻辑由各业务子包按需引入 `Format`。若在本包堆积管线代码，将破坏 `domains/<domain>/` 的扁平协作模型，增加跨包循环依赖风险。

### D3 · 分片规则优先保障代码结构完整性（非纯随机截断）
**决策**: `file-content` > 8KB 时，采用 `imports + signatures + 首 200 行` 策略，而非末尾硬截断。
**理由**: LLM 对代码的理解高度依赖导入与函数签名上下文。截断保留结构信息可显著降低 `sw.code-change` 与 `sw.llm-review` 的幻觉率。该策略为经验性最佳实践，直接固化在共享层避免各管线重复实现。

## 数据流 / 拓扑
```
[上游输入 / 代码库扫描]
      ↓ (触发任务或读取文件)
┌─────────────────────────────────────────┐
│ _shared (common_formats.py)             │
│  • 判定材料类型 (sw.xxx)                │
│  • 比对体积阈值 (MAX_XXX 常量)          │
│  • 应用结构截分策略 (imports+sig/拆批)  │
└─────────────────────────────────────────┘
      ↓ 产出标准 Material
┌─────────────────────────────────────────┐
│ 业务管线 (debugger / review / tdd ...)  │
│  • 消费 sw.file-batch / sw.change-set   │
│  • 路由至对应 LLM Worker 处理           │
│  • 产出 sw.llm-review / sw.report       │
└─────────────────────────────────────────┘
      ↓ 协议同步 / 落盘审计
[Protocol Registry] ← Format 元数据注册表同步 → [Guardian 审计节点]
```

## 已知局限
- **分片阈值硬编码**：`MAX_FILE_CONTENT_BYTES = 8192` 等常量写死在 `.py` 中，无法按不同模型上下文窗口（8K/32K/128K）动态调整。 · 升级路径: M1 迁移至 `.omni/manifest.yaml` 或独立 `config/chunk_limits.yaml`，Worker 启动时读取配置覆盖模块常量。
- **缺乏强类型 Schema 校验**：9 个 Format 目前仅以 docstring 和字符串常量声明，依赖下游 Worker 隐式容错，非法 payload 在运行时才被拒绝。 · 升级路径: M2 引入 `pydantic` 模型或 JSON Schema 注册到 `FormatRegistry`，在管线入口增加 `validate()` 钩子，提前拦截格式违规并输出结构化错误。
- **无版本化协议追踪**：Format 变更（如新增字段）无显式版本号，下游管线需全量同步升级。 · 升级路径: M3 在 `FormatRegistry` 中引入 `version` 字段与向后兼容声明，支持多版本并行解析与渐进式迁移。

## 参考资料
- 协议基座: `src/omnicompany/packages/protocol/format.py` (Format / FormatRegistry 定义)
- 分布式文档规范: `docs/standards/distributed-docs.md` (§二 六域结构 / §四 src 白名单 / §5.2 DESIGN.md 结构)
- 审计计划: `docs/plans/_archive/[2026-04-07]voxelcraft-STRUCTURES-VISION-REDESIGN/UNDERSCORE_SMUGGLING_AUDIT.md` (提及 `_shared` 反模式审查背景)
- 兄弟包消费方: `src/omnicompany/packages/domains/software_engineering/review/DESIGN.md`
- 兄弟包消费方: `src/omnicompany/packages/domains/software_engineering/implement/DESIGN.md`