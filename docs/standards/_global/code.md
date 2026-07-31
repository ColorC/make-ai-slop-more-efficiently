# Code 健康标准

> 面向 `packages/` 下业务代码的单文件级检查.
> **必要不充分**. 强制度: `[MUST]` / `[SHOULD]` / `[MAY]`
>
> 参考: `docs/ARCHITECTURE.md` (依赖契约) / `docs/archmap.yaml` (drawer 定义)
> 参考: `.claude/skills/omnicompany-dev/SKILL.md` §1, §5, 附录 A

---

## 核心原则

### 原则 1 · 六元纯粹 (Six-Primitive Purity)

只用 Hook / Tool / Node / Format / Signal / Intent 六种原语构建一切.

| 原语 | 调 LLM | 改状态 | 做决策 |
|---|---|---|---|
| Hook | 禁止 | 禁止 | 禁止 (只观测) |
| Tool | 禁止 | 允许 | 禁止 (只执行) |
| Node (Router) | 允许 | 禁止 | 允许 |
| Format / Signal / Intent | N/A | N/A | N/A (纯数据) |

### 原则 2 · 写入安全 (Write Safety)

任何文件写入经过 `guarded_write`, 确保 Shield 审计 + OmniMark 头 + archmap 路径校验.

### 原则 3 · 调用统一 (Unified LLM Access)

所有 LLM 调用通过 `runtime.llm.LLMClient`. InfoAudit 审计自动挂载 / 模型切换配置控制 / token 统计统一.

### 原则 4 · 架构边界 (Architecture Boundary)

packages/ 内代码不跨 package 导入 / 不从 _graveyard 导入 / 不直接操作框架层内部状态.

---

## 标准项

### 写入与调用

**C-01** `[MUST]` **文件写入使用 guarded_write** (原则 2)

`from omnicompany.core.guarded_write import write_file` — 不用 `open('w')` / `Path.write_text` / `shutil.copy`.
已有执行: Guardian OMNI-013. 合法债务加 `# OMNI-013 ALLOW: <理由>` 注释豁免.

**C-02** `[MUST]` **LLM 调用使用 LLMClient** (原则 3)

不直接 `import openai / anthropic / requests`.

**C-03** `[MUST]` **默认模型 qwen-3.6-plus**

硬编码其他模型 (如 `model="gpt-4"`) 属于违规, 除非有 L1 明确批准记录.

### 溯源与架构

**C-04** `[SHOULD]` **OmniMark 头存在**

文件顶部 `# [OMNI] origin=... domain=... ts=...`. 已有执行: Guardian OMNI-001.

**C-05** `[MUST]` **无跨 package 导入** (原则 4)

`packages/domains/A/` 不 import `packages/domains/B/`. 跨 package 交互通过 primitives 接口 + core/dispatch.
已有执行: Guardian OMNI-003.

**C-06** `[MUST]` **不从 _graveyard 导入**

已有执行: Guardian OMNI-004.

### 六元边界

**C-07** `[MUST]` **Hook 不调 LLM** (原则 1)

Hook 是纯观测者 (PeriodicHook / EventHook), 不调 LLM、不改状态、不做决策.

**C-08** `[MUST]` **Tool 不做路由决策** (原则 1)

Tool 只执行 (读文件 / 跑命令 / 调外部 API), 不决定"下一步走哪个节点". 决策权在 Router.

**C-09** `[MUST]` **Node (Router) 不直接写数据库/状态** (原则 1)

Node 做判断和转换, 状态写入交给 Tool. Node 内 `self.xxx = value` 跨调用 = 违反无状态原则.

### 通用能力

**C-10** `[SHOULD]` **通用规则沉淀为 Tool**

Router 内的"Python 源码清理" / "从 LLM 响应提代码块" / "JSON schema 验证" 等与当前业务无关的通用能力, 应沉淀为 `runtime/tools/` 下独立 Tool. 锁死在某个 Router 里 = 复用性灾难.

### 事件与运维

**C-11** `[MUST]` **事件走统一事件库**

所有 factory/pipeline/domain 事件 → `data/events.db`. IDE agent loop → `data/ide_events.db`.
不自己造 `data/<domain>/events.db` 子目录 (domain 靠 `FactoryEvent.source` 区分).
不传字符串字面量给 `SQLiteBus()`; 用默认构造或 `basename=` 参数.

**C-12** `[MUST]` **业务代码在正确位置**

业务代码放 `packages/<namespace>/<domain>/`, 严禁放 `src/omnicompany/` 根 / `protocol/` / `runtime/` / `bus/` / `cli/`.

**C-13** `[MUST]` **不在仓库根放日志/报告/临时文件**

`*.log` / `*_report.*` / `scratch_*` / `tmp_*` 禁止出现在仓库根. 报告放 `docs/reports/<category>/`, 日志放 `logs/`, 临时文件放 `.omni/tmp/`.
已有执行: Guardian OMNI-015, archmap v12.

---

## 反模式

| 编号 | 名称 | 描述 |
|---|---|---|
| CA-01 | 裸写 | `open('output.csv', 'w')` 不经 guarded_write |
| CA-02 | 裸调 | `openai.ChatCompletion.create(...)` 不经 LLMClient |
| CA-03 | 硬编码模型 | `model="gpt-4-turbo"` 绕过配置 |
| CA-04 | 幽灵导入 | voxelcraft 代码里 `from omnicompany.packages.domains.demogame.xxx import yyy` |
| CA-05 | 墓地复活 | `from omnicompany._graveyard.xxx import yyy` |
| CA-06 | Hook 里调 LLM | PeriodicHook 子类里 `LLMClient().complete()` |
| CA-07 | Tool 做决策 | Tool 里判断 `VerdictKind.PASS / FAIL` 并返回 |
| CA-08 | Router 写数据库 | `run()` 里 `sqlite3.connect(...).execute("INSERT ...")` |
| CA-09 | 根层垃圾 | `C:/workspace/omnicompany/output.log` |
| CA-10 | system prompt 当 changelog | 堆积"LLM 常犯错误"禁令代替真源码注入 |

---

## 检查优先级

全部确定性, 秒级:
C-04 → C-05 → C-06 → C-12 → C-13 → C-07 → C-08 → C-09 → C-02 → C-03 → C-01 → C-11 → C-10
