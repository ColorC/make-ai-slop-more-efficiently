<!-- [OMNI] origin=ai-ide domain=omnicompany/standards ts=2026-05-02T05:00:00Z type=doc status=active agent=ai-ide-current -->
<!-- [OMNI] summary="G1 身份组规范 - claude code session 跨 hook/CLI/dashboard 统一身份链" -->
<!-- [OMNI] why="dashboard cc_wrapper 已实施 session 自动注册到 SQLite event bus, CLI 端要走直接共用逻辑. 这份规范明确双轨制(hook 自动 + CLI 兜底)走的逻辑必须一致" -->
<!-- [OMNI] tags=cli,identity,session,standard,G1 -->
<!-- [OMNI] material_id="material:standards.identity.session_resolver.spec.md" -->

# G1 身份组 (claude code session 统一身份)

> **状态**: 实装完成 2026-05-02
> **关联实装**: `services/_core/identity/` + `cli/commands/identity.py` + `dashboard/cc_wrapper/hooks/session_start.py` + `dashboard/sandbox_api.py`
> **关联规范**: `omnicompany_cli.md` (整体 CLI 设计) / `sandbox.md` (沙盒里身份的用法)

## 一、 这是干嘛的

omnicompany 治理的核心前提是**追溯写入身份** — 任何往 watched 目录写东西的人 / 工具 / agent 都要能查到 trace_id, 出问题反查得到来源.

身份链跨三层:
- **hook 层** — Claude Code 启动时 `SessionStart` hook 写身份到 `data/cc_session_active.json`
- **CLI 层** — `omni who` / `omni session` 命令读 / 写同一份身份文件
- **dashboard 层** — `/api/v2/identity/who` + `/api/v2/identity/writes` web 端只读暴露同一份身份

三层走**同一份代码** (`services/_core/identity/resolver.py`), 触发方式不同但走的逻辑一致.

## 二、 身份解析优先级

`resolve_active_trace_id()` 单一查询入口, 优先级链 (高→低):

1. `OMNI_CC_TRACE_ID` 环境变量 (CLI 显式 / 测试 / 脚本场景设)
2. `OMNI_CC_PTY_ID` 环境变量 (dashboard PTY 启动 claude 时传给子进程)
3. `data/cc_session_active.json` 里的 `trace_id` (SessionStart hook 写的)
4. fallback: `cc_unknown_<unix_ts>` (warn 级缺省)

## 三、 active session 文件格式

文件: `<omnicompany>/data/cc_session_active.json` (项目根 data/ 下, 不进 git)

字段:

```json
{
  "trace_id": "cc_<claude_session_id>" 或 "cc_<pty_id>",
  "claude_session_id": "<原始 session_id>",
  "pty_id": "<dashboard PTY id 或 null>",
  "active_plan": "<当前 active plan 路径或 null>",
  "cwd": "<当前工作目录>",
  "started_at": "<ISO 时间>",
  "source": "hook" | "cli_bind"
}
```

`source` 字段标触发方式: `hook` 表示由 SessionStart hook 写, `cli_bind` 表示由 `omni session bind` 显式写. **schema 完全一致**, 两路只是触发不同.

## 四、 双轨制使用

### 自动轨道 (hook 触发, 默认)

Claude Code 启动时 SessionStart hook 自动写入. 用户不用做任何事:

```
Claude Code 启动
  → cc_wrapper hooks/session_start.py 触发
  → 调 sh.trace_id_for(payload) 派生 trace_id
  → 调 record_active_session(trace_id, ..., source='hook')
  → data/cc_session_active.json 写入
```

### 显式轨道 (CLI 兜底, 测试 / 脚本)

测试场景 / hook 故障 / 脚本一次跑多 session 时, CLI 显式覆盖:

```bash
omni session bind --trace-id=manual_001 --claude-session-id=sid_abc
# 走同一份 record_active_session(), source='cli_bind'
```

### 跨进程身份继承

Dashboard PTY 启动 claude 时通过 env 传 `OMNI_CC_PTY_ID`. claude 子进程 + 子进程的 CLI 调用都继承到. 这是为什么 dashboard 启动的 session 跟 CLI 看到同一身份.

## 五、 CLI 命令

| 命令 | 用途 |
|---|---|
| `omni who` | 显示当前身份 + 写过的文件清单 |
| `omni session current` | 输出 trace_id 一行字符串 (供 shell `$(omni session current)` 嵌入) |
| `omni session bind --trace-id=<>` | 显式绑定 (兜底) |
| `omni session meta` | 完整元数据, 不带 writes |

`omni who --json` 跟 `omni who --no-writes` 控制输出格式跟内容.

## 六、 dashboard API (只读)

| 端点 | 内容 |
|---|---|
| `GET /api/v2/identity/who` | 当前身份元数据 (跟 `omni who` 同源) |
| `GET /api/v2/identity/writes?limit=N` | 当前 session 写过的文件清单 |
| `GET /api/v2/registry/by-trace/{trace_id}` | 某 session 注册过的实体 (跟 G2 注册中心联动) |

dashboard 严格只读, 写仍走 CLI (跟 dashboard 设计原则 D2 一致).

## 七、 session writes 派生

"当前 session 写过哪些文件" 不另立数据库, 从 cc_wrapper 已有 SQLite event bus
(`data/ide_events.db`) 派生:

```
查询条件: trace_id = <current> AND event_type = 'agent.tool.call'
        AND payload.tool ∈ {Edit, Write, MultiEdit, NotebookEdit, str_replace_editor}
返回: payload.args.file_path 列表 (按时间倒序)
```

这是 dashboard ide_api `/api/v2/ide/trace/{trace_id}/files` 跟 G1 `omni who --writes` 共用的同一查询. 派生层在 `services/_core/identity/writes.py`.

## 八、 反模式

**CLI 自己派生 trace_id** — 不走 `resolve_active_trace_id()` 单一入口, 各自算 → 同一 session 在不同命令看到不同身份, 注册中心追溯失败.

**hook 写不同 schema** — hook 跟 CLI bind 写出 active 文件字段不一致 → 解析时 fallback 到 unknown.

**dashboard 写身份** — dashboard 应该只读. 让 dashboard 起新 session 走 cc_wrapper PTY 启动 + hook 写, 不直接调 `record_active_session()`.

**注册中心不带 trace_id** — `omni register` 时 attrs 漏了 trace_id, 后续无法反查"这个内容是哪个 session 注册的". G2 实施已强制带.

## 九、 实施引用

- `omnicompany/src/omnicompany/packages/services/_core/identity/__init__.py` - 公共模块入口
- `omnicompany/src/omnicompany/packages/services/_core/identity/resolver.py` - 身份解析 + 写入
- `omnicompany/src/omnicompany/packages/services/_core/identity/writes.py` - session_writes 派生
- `omnicompany/src/omnicompany/cli/commands/identity.py` - `omni who` / `omni session` CLI
- `omnicompany/src/omnicompany/dashboard/cc_wrapper/hooks/session_start.py` - hook 集成 (含 record_active_session 调用)
- `omnicompany/src/omnicompany/dashboard/sandbox_api.py` - dashboard 只读 API

## 十、 演进点 (留给后续阶段)

- **多 session 并行** — 当前一份 active 文件只支持一份当前 session. 多 claude session 并行 (例如同 workspace 下两个 IDE 窗口) 需要扩多文件 (`<trace_id>.json` 加 `_current.txt` 指针).
- **trace_id 轮换** — claude session resume 时 hook 可能跑两次, 后一次会覆盖. 需要 hook 端做幂等性保护或者加 session 续接逻辑.
- **跨机身份** — 当前身份只在一台机器. 跨机协作时需要全局 trace_id 命名空间 (例如 `<host>.<pid>.<sid>`).
