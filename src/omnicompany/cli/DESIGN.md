<!-- [OMNI] origin=claude-code domain=cli ts=2026-04-25T00:00:00Z type=doc status=active -->
<!-- [OMNI] material_id="material:cli.subsystem_design.documentation.md" -->

# cli · 设计文档

## 状态
- **版本**: V2 (2026-04-25 填充核心接口 / 架构决策 / 接收意愿，统一命令组与观测管线已完备)
- **成熟度**: active
- **下一步**: 迁移 `docauthor` / `assistant` 子命令至独立 SDK Client 调用（解耦 CLI 直连 DB 与 dashboard 内部逻辑）；增加 `omni config` 交互式环境配置管理

## 核心目的
提供 OmniCompany 框架的**统一命令行入口**，封装执行、观测、管理与诊断操作。将分散在 runtime / bus / protocol / guardian 的能力暴露为标准化 Click 子命令，集中处理终端兼容性（GBK/Unicode 映射）、DB 连接解析与 Guardian 启动自检。
**不解决**：业务逻辑实现（委托给 runtime/packages SDK）；长期后台守护（由 Worker/daemon 负责）；Web UI 交互与数据可视化（dashboard 职责）。

## 核心接口
- **`main.py`** → `cli` 入口组 / `_guardian_precheck()` 自检拦截 [main.py](main.py)
- **`unified.py`** → `cmd_run`, `cmd_exec`, `cmd_replay`, `cmd_tail`, `cmd_traces`, `cmd_pipelines`, `cmd_health`, `cmd_nodes`, `cmd_errors`, `cmd_diagnose` 统一观测/执行组 [unified.py](unified.py)
- **`db.py`** → `open_db()`, `resolve_db()`, `fmt_time()`, `fmt_bool()`, `truncate()`, `type_ids()` 辅助工具 [db.py](db.py)
- **`commands/assistant.py`** → `cmd_assistant` (chat / status / history / goal CRUD) [commands/assistant.py](commands/assistant.py)
- **`commands/debt.py`** → `cmd_debt` (list / stats / resolve / scan / add) [commands/debt.py](commands/debt.py)
- **`commands/guardian.py`** → `cmd_guardian` (patrol / daemon / archmap / shield-status / zombies) [commands/guardian.py](commands/guardian.py)
- **`commands/llm_audit.py`** → `cmd_llm`, `cmd_pipeline` (LLM 调用审计) [commands/llm_audit.py](commands/llm_audit.py)
- **`commands/docauthor.py`** → `cmd_docauthor` (scan / run / run-all / observe / issues) [commands/docauthor.py](commands/docauthor.py)
- **其余命令组** → `cmd_trace`, `cmd_round`, `cmd_node`, `cmd_loops`, `cmd_pain`, `cmd_evo`, `cmd_domain`, `cmd_inquiry`, `cmd_human`, `cmd_registry` 均挂载于主组 [commands/](commands/)

## 架构决策
### D1 · 零业务逻辑原则（CLI 仅作路由与参数解析）
**决策**: `cli/` 目录下所有命令函数仅负责参数校验、Click 路由与结果格式化，核心执行逻辑 100% 委托给 `runtime/`, `packages/`, `dashboard/` 的 SDK。
**理由**: 避免 CLI 膨胀为“第二份业务代码”；保证 Web UI、CI 管线、SDK 调用共享同一套核心逻辑；符合 `distributed-docs.md` 的层隔离要求。

### D2 · 统一 `unified.py` 分组收敛观测/执行入口
**决策**: 将散落的 trace/round/node/pipeline 等命令整合至 `unified.py` 的 `@click.group()`，替代单文件散落模式。
**理由**: 初始设计（Phase 1）按文件名拆分导致维护碎片化；统一分组后共享上下文对象、错误处理中间件与 `_safe_echo` 渲染管线，降低重复代码。

### D3 · Windows GBK 安全输出映射管线
**决策**: 所有终端输出强制经过 `unified.py::_safe_echo()`，内置 `_UNICODE_SAFE` 字典将 Box-drawing / 警告 / 特殊符号转义为 ASCII 等价物，捕获 `UnicodeEncodeError` 后兜底替换。
**理由**: OmniCompany 主要在 Windows 开发机运行，默认 GBK 编码会因 `click.echo()` 吐出 UTF-8 特殊字符直接崩溃；硬转义保证核心状态反馈在任何终端可见。

### D4 · 启动强制 Guardian 自检（不阻塞但告警）
**决策**: `main.py` 的 `cli()` group callback 注入 `_guardian_precheck()`，每次运行任意 `omni` 命令前检查 `archmap.yaml` 有效性、`enforce_mode`、bypass 状态与未处理告警。异常时 stderr 红字输出。
**理由**: 确保任何 Agent/Human 使用 CLI 前，框架架构边界与合规状态处于已知态；防止在“架构图已损坏/规则被绕过”环境下盲目执行操作。

### D5 · DB 路径解析延迟加载与环境变量优先
**决策**: `db.py` 提供 `resolve_db(db_option)`，优先使用 CLI `--db` 参数，次优 `OMNI_DB` 环境变量，最后 fallback 到 `__file__` 推算的 `data/autonomous/semantic_network.db`。连接池在函数级 `open_db()` 实例化。
**理由**: 避免模块导入期（import time）硬触发文件系统路径计算；支持多实例/容器化部署动态指定 DB 位置；`PRAGMA journal_mode=WAL` 与 `foreign_keys=ON` 保证 CLI 高频只读查询不锁死主进程。

### D6 · `.env` 拦截加载置于导入链最顶端
**决策**: `main.py` 文件顶部 `try: load_dotenv()` 包裹在无其他 import 之前。
**理由**: `runtime/llm/` 等模块在导入时直接读取 `os.environ`；若 `.env` 在 `load_dotenv()` 之前加载，会导致关键 API Key / 路由配置为空引发隐式崩溃。

## 数据流 / 拓扑
```
[User Shell] → `omni <subcommand> [args]`
    ↓
main.py::cli (click.group)
    ├─ _guardian_precheck() → 校验 archmap / enforce_mode / bypass → stderr 告警 (不中断)
    └─ 解析子命令入口
        ↓
commands/<name>.py 或 unified.py
    ├─ 参数校验 & --json / --db 覆盖
    └─ 调用 SDK / Runtime Worker / DB Helper
        ↓
runtime / bus / dashboard / guardian SDK
    ├─ 执行业务逻辑 / 查询 semantic_network.db
    └─ 返回结构化 dict / list / generator
        ↓
unified.py::_safe_echo() / json.dumps()
    └─ Unicode 转义 & GBK 兜底 → print 到 stdout/stderr
```

## 已知局限
- `docauthor` / `assistant` 子命令直接 import `dashboard/assistant_context_builder` 与内部 DB 路径，违反 CLI/业务分层边界 · 升级路径: 抽离为 `omnicompany.services.assistant` / `docauthor` SDK CLI Client，CLI 仅调用标准 SDK 接口或本地队列，彻底消除对 `dashboard/` 源码的硬依赖
- `db.py` 默认路径依赖 `__file__` 相对推算，在 venv 符号链接或容器只读层下可能解析失效 · 升级路径: 全面迁移至 `OMNI_DATA_DIR` 环境变量 + `pathlib.Path.resolve()` 强校验，提供 `cli config set-db-path` 初始化命令替代隐式 fallback
- 缺乏统一命令级审计/权限中间件，所有子命令直连 runtime 无执行留档 · 升级路径: 在 `cli()` 组层接入 `click.pass_context` 注入 `AuditMiddleware`，统一拦截 `invoke`，将 `command_name / args_hash / exit_code / duration` 异步写入 `data/events.db::cli_audit` 表，供 `omni trace` / guardian 消费

## 参考资料
- 规范: `docs/standards/distributed-docs.md` (OMNI-001/034 架构边界与文档合规)
- 关联 Plan: `docs/plans/_archive/[2026-04-04]CLI-INTROSPECT-AND-USE-SKILL/README.md` (Phase 1 CLI 数据层设计)
- 关联 Plan: `docs/plans/_archive/[2026-04-08]OMNIGUARDIAN-ACTIVATION/SESSION_3e_AND_LONG_TERM.md` (S3e.4 CLI 启动自检机制)
- 关联 Plan: `docs/plans/_archive/[2026-04-09]INFO-AUDIT-INFRASTRUCTURE-DESIGN/plan.md` (P5.2 `omni llm audit` CLI 集成)
- 兄弟包边界: `src/omnicompany/runtime/` (核心执行), `src/omnicompany/bus/` (消息), `src/omnicompany/dashboard/` (UI/DB 宿主), `src/omnicompany/core/` (GuardedWrite/Archmap)

## 接收意愿
- **接收**: 新增面向 runtime/bus 的观测/诊断子命令 (如 `omni inspect worker`, `omni replay bus`)；跨平台终端渲染优化 (colorama/ansi 自动降级)；统一 CLI 配置管理 (`omni config`)
- **不接收**: 业务规则实现 (Rule/Patrol/Agent Loop)；数据模型定义 (Schema/Migration)；长期后台任务调度 (Daemon/Cron/Worker 注册)
- **边界信号**: 子命令包含 >50 行业务逻辑；直接操作非 `data/` 下的业务文件；绕过 `_safe_echo` 直接 `print()`；CLI 直连 `dashboard/` 或 `packages/` 内部实现而非 SDK 接口