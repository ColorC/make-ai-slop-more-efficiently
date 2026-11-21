<!-- [OMNI] origin=ai-ide domain=omnicompany/standards ts=2026-05-02T05:00:00Z type=doc status=active agent=ai-ide-current -->
<!-- [OMNI] summary="G4 锁组规范 - omni lock, 离线扫描+处理 + baseline grandfather + 实时拦截待下阶段" -->
<!-- [OMNI] why="目录规范化在主动防御之前必须完成 (用户硬规则). 这份是离线 G4 MVP 规范, 内部错位留 OMNI-LOCK-VIOLATION 注释 + 外部直接写移到 quarantine + 留指导" -->
<!-- [OMNI] tags=cli,lock,protection,defense,standard,G4 -->
<!-- [OMNI] material_id="material:standards.lock.protection_policy.spec.md" -->

# G4 锁组规范 (omni lock)

> **状态**: 离线 MVP 实装完成 2026-05-02. 实时拦截层留下一阶段
> **关联实装**: `services/_core/protection/` + `cli/commands/protection.py` + `dashboard/lock_api.py`
> **关联规范**: `omnicompany_cli.md` / `sandbox.md` / `identity.md` / `registration.md`

## 一、 这是干嘛的

omnicompany 主动防御. 用户硬规则:
- 内部代码尝试写到错位置 → 源头注释掉 + 引用规范文档教正确写法
- 外部直接写入 (绕过 cc_wrapper hook) → 移除 + 原地留指导文件 (注册身份 + 合法方式)
- 范围性开启 (不是 all-or-nothing 全局锁)
- 灵活规则 — 什么目录扫 / 什么目录清 / 什么目录追根除

## 二、 当前阶段 vs 下一阶段

**当前阶段 (离线 MVP, 2026-05-02 完工)**:
- `omni lock scan` — 扫违规候选, 列两类
- `omni lock handle [--mode=notice|evict|both]` — 离线处理
- `omni lock baseline --snapshot` — grandfather 历史路径

**下一阶段 (实时拦截层)**:
- PreToolUse hook 拦 cc_wrapper Edit/Write 实时阻断 — 用户角度: 锁开 = 写不了
- File watcher daemon 拦外部直接写 — 文件出现就立即 evict
- 灵活规则 (用户原话)
  - 什么目录"扫" (列违规, 不动)
  - 什么目录"清" (扫到就 evict)
  - 什么目录"追根除" (含子目录 / git 历史里也要查)

## 三、 判定逻辑

`scan_violations()` 走 watched_paths 内每个文件:

| 条件 | 处理 |
|---|---|
| 不在 watched | 跳过 (不查) |
| 在白名单 patterns | 跳过 |
| 在 baseline (grandfather 历史快照) | 跳过 |
| 在注册中心 (in registry) | 跳过 (合法实体) |
| event bus 找到 agent.tool.call 写过 → has trace | **internal_misplace** |
| event bus 找不到 → no trace | **external_write** |

internal vs external 通过 SQLite event bus (`data/ide_events.db`) 反查 agent.tool.call 事件的 file_path 判定. 这是跟 G1 身份链的联动 — cc_wrapper hook 已记录的写入视为内部.

## 四、 处理逻辑

### internal_misplace (内部错位)

**保留文件不删**. 在文件头插一段 `OMNI-LOCK-VIOLATION` 注释, 引用 `omni new` / `omni sandbox promote` 规范流程教用户走正确路径.

例子 (Python 文件):

```python
# ════════════════════════════════════════════════════════════════════════
# OMNI-LOCK-VIOLATION · 这份文件是 internal_misplace 违规.
# 写入工具: Edit  trace_id: cc_xxx  时间: 2026-05-02T05:00:00Z
# 路径不在 omnicompany 注册中心, 也不在白名单. 修法 (按规范文档):
#   1. 读 docs/standards/cli/sandbox.md 跟 docs/standards/concepts/<对应 kind>.md
#   2. 走 omni new --kind=<kind> --name=<name> 立沙盒草稿,
#   3. 沙盒里反复改, omni sandbox check 自检通过,
#   4. omni sandbox promote --content=<草稿> --target=<正式区> --kind=<kind>
# ════════════════════════════════════════════════════════════════════════
```

幂等性: 文件头已含 `OMNI-LOCK-VIOLATION` 字面则不重复加.

### external_write (外部直接)

**移除文件 + 原地留指导**. 文件移到 `.omni/quarantine/<YYYY-MM-DD-HHMM>/<原 rel_path>`, 原地留 `<原文件名>.OMNI-EVICTED.md` 指导文件.

指导文件内容:
- 这是什么 (原文件被守护移除, 完整保留在隔离区路径)
- 为什么会发生 (没经过 omnicompany 体系, event bus 找不到 trace_id)
- 怎么改正 (3 步: 注册身份 → 合法写入流程 → 验证)
- 如果原文件内容很重要 (从隔离区拷出走合法流程)
- 删除这份指导 (走完合法流程后)
- 联系 (改 protection_policy.json 的 whitelist_patterns 加豁免)

## 五、 范围性开启

watched_paths 默认 4 项 (src/omnicompany + docs + templates + data/services), 范围太大. 推荐窄化:

```bash
# 只锁新工作区
omni lock enable --watched=data/_new_authoring/

# 只锁 templates 跟 data/services (跟项目分发的内容相关)
omni lock enable --watched=templates/ --watched=data/services/
```

`omni lock config` 直接编辑 `.omni/protection_policy.json` 也可以, 但建议走 CLI.

## 六、 baseline (历史快照豁免)

第一次 `omni lock enable` 后跑 `omni lock baseline --snapshot`, 把 watched 内现存所有非白名单 / 非注册中心文件加到 baseline 里. 之后 scan 跳过 baseline 内的, 只查**新增**写入.

不打 baseline 的话锁 enable 后 scan 会找出几千条 (整个项目历史), 不可处理.

baseline 跟白名单的区别:
- 白名单: glob pattern, 静态豁免 (例 `**/*.pyc`)
- baseline: 固定路径列表, 动态(快照型) — 文件被 promote 到注册中心后从 baseline 视角消失 (in_registry 优先判)

## 七、 CLI 命令

| 命令 | 用途 |
|---|---|
| `omni lock status` | 看锁状态 (开关 + watched + 白名单 + baseline 数) |
| `omni lock enable [--watched=<>]` | 开锁 |
| `omni lock disable` | 关锁 |
| `omni lock scan [--limit=N]` | 离线扫描违规候选 (不处理) |
| `omni lock handle --mode=notice/evict/both [--dry-run]` | 离线处理 |
| `omni lock baseline [--snapshot/--clear]` | baseline 管理 |
| `omni lock config [--reset]` | 看 / 重置 policy |

## 八、 dashboard API (只读)

| 端点 | 内容 |
|---|---|
| `GET /api/v2/lock/status` | 锁状态 |
| `GET /api/v2/lock/violations?classification=&limit=N` | 违规列表 (跑一次 scan) |
| `GET /api/v2/lock/baseline?limit=N` | baseline 路径预览 |

dashboard 不暴露 enable/disable/handle/baseline 写操作 — 跟 D2 只读聚合层原则一致.

## 九、 反模式

**全局开锁不打 baseline** — 数千条历史违规, 不能处理也不能滚回, 死锁.

**handle --mode=evict 在没有 baseline 时跑** — 把项目源码全移到 quarantine, 灾难.

**watched_paths 包含 .omni/sandbox** — 沙盒被锁住反而违规. `.omni/sandbox/**` 已在默认白名单, 别动.

**白名单加 `**` 全部豁免** — 锁失效但不显式 disable, 误以为还在保护.

**外部直接写处理失败但 quarantine 已建** — handler 异常时部分状态. 建议总是先 dry-run 看一遍.

## 十、 实施引用

- `omnicompany/src/omnicompany/packages/services/_core/protection/__init__.py` - 模块入口
- `omnicompany/src/omnicompany/packages/services/_core/protection/policy.py` - watched + whitelist + baseline
- `omnicompany/src/omnicompany/packages/services/_core/protection/scanner.py` - 扫描 + 分类
- `omnicompany/src/omnicompany/packages/services/_core/protection/handlers.py` - notice + evict 处理
- `omnicompany/src/omnicompany/cli/commands/protection.py` - omni lock CLI
- `omnicompany/src/omnicompany/dashboard/lock_api.py` - dashboard 只读 API

## 十一、 实时拦截层 (下一阶段设计)

按用户原话 "这个玩意儿必须打开, 并且可以灵活调整规则", 实时拦截要做这些:

### 内部拦截 (PreToolUse hook)

新加 `cc_wrapper/hooks/lock_pretooluse.py`:

```
PreToolUse hook
  → 收到 Edit/Write tool_input
  → 解析 file_path
  → 走 protection.policy.is_watched + is_whitelisted + is_in_baseline
  → 走注册中心查 in_registry
  → 都不命中 → exit 2 阻断 + stderr 给修改方式 (复用 handle_internal_misplace 的注释模板)
```

### 外部拦截 (file watcher daemon)

新加 `services/_core/protection/watcher.py`:

```
watchdog Observer 监听 watched_paths
  → on_modified / on_created 事件
  → file_path 走判定
  → 不命中 → 实时 evict (复用 handle_external_write)
  → 守护进程跑后台
```

### 灵活规则配置

policy 加 per-path mode:

```json
{
  "watched_paths": [
    {"path": "src/omnicompany/", "mode": "scan"},      // 列违规, 不动
    {"path": "data/_writable/", "mode": "evict"},      // 立即 evict
    {"path": "docs/", "mode": "scan_with_root"}        // 含 git 历史里也查
  ]
}
```

mode 三档:
- `scan` — 只扫不处理 (默认, 现状)
- `evict` — 实时移除
- `scan_with_root` — 追到根 (扫当前 + 查 git log 谁加的)

### 跟 G1 联动加强

实时拦截必须知道是哪个 session 触发的. 如果 trace_id 找不到 (env 不在 + active 文件不在 + hook 没启动), 守护进程发 PainSignal 让用户处理.

### 范围性开启路径

阶段化:
1. **Stage 0** (现在) — 离线 MVP
2. **Stage 1** — 内部拦截 (PreToolUse hook), 默认 mode=warn (打 OMNI-LOCK-VIOLATION 注释但不 exit 2)
3. **Stage 2** — 内部拦截升级到 exit 2 阻断
4. **Stage 3** — 外部拦截 (file watcher) 上线, 默认 mode=scan
5. **Stage 4** — 外部拦截升级到 evict
6. **Stage 5** — 全部 watched_paths 默认 mode=evict, 全防御开启

每阶段切换都让用户决定. 范围性开启 = 阶段递进 + 范围递增 + 模式逐步收紧.
