<!-- [OMNI] origin=ai-ide domain=omnicompany/standards/_global ts=2026-05-02T10:00:00Z type=doc status=active agent=ai-ide-current -->
<!-- [OMNI] summary="目录关系 - .claude/skills/ + .omni/sandbox/guides/ 都是 docs/standards/ 的薄包装 + 平台必要位置说明" -->
<!-- [OMNI] why="用户 2026-05-02 明示 - 不是迁移 .claude 到 .omni, 而是单源 + 薄包装. 详细在 single_source_thin_wrap.md, 本文件保留 .claude 平台必要位置说明" -->
<!-- [OMNI] tags=standard,directory,boundary,foundation,thin-wrapper
<!-- [OMNI] material_id="material:standards.global.dot_claude_dot_omni_directory_boundary.md" -->

# `.claude/` 平台必要位置 + 跟 omnicompany 的关系

> **状态**: v2 (2026-05-02), v1 "两套目录平行分工" 说法已撤
> **核心铁律**: [single_source_thin_wrap.md](single_source_thin_wrap.md) (唯一源 + 薄包装)
> **关联**: `directory_structure.md` / `cc_wrapper_hooks.md`

## 一、 用户原话语境

**原始需求 3.1.1 (2026-04-30)**: "放弃 .claude 目录"

**最新指示 (2026-05-02)**: ".claude 都不是迁移到 .omni 了, .omni 结构混乱. 主要是 skill 修改起来麻烦, 自动和 docs 文档自动统一, 或采用非常薄的包装. 具体规则, 工作顺序用唯一源."

合并理解:
- 不是物理迁移 `.claude/` 到 `.omni/`
- 不是搞两套平行目录
- **核心铁律**: `docs/standards/` 是规则 + 工作顺序的唯一源, `.claude/skills/` 跟 `.omni/sandbox/guides/` 都是薄包装. 详 [single_source_thin_wrap.md](single_source_thin_wrap.md)
- `.claude/` 仍存在 — 但内容**只**保留 Claude Code 强制需要的 (settings.json) + 薄包装 SKILL

## 二、 两套目录的明确分工

| 目录 | 谁的 | 强制位置 | omnicompany 用途 |
|---|---|---|---|
| `.claude/` | Claude Code 平台 | 是 (Claude Code 进程读) | 仅 hooks 配置入口 (settings.json) |
| `.claude/settings.json` | Claude Code | 是 | cc_wrapper 装 6 个 hook 命令 |
| `.claude/skills/` | Claude Code | 是 (skill 概念位置) | **不用** — omnicompany 不复用 skills |
| `.omni/` | omnicompany | 否 (我们设计) | 主目录 |
| `.omni/sandbox/` | omnicompany | - | G5 沙盒 |
| `.omni/quarantine/` | omnicompany | - | G4 锁隔离区 |
| `.omni/protection_policy.json` | omnicompany | - | G4 锁配置 |
| `.omni/protection_baseline.json` | omnicompany | - | G4 baseline |
| `.omni/sessions/` | omnicompany (历史) | - | session 持久化 (跟 G1 联动) |
| `.omni/guardian/` | omnicompany | - | guardian 状态 / 白名单 |
| `templates/` | omnicompany | 否 | 9 种 kind 模板四件套 |
| `data/` | omnicompany | 否 | data + 注册中心 + audit |
| `docs/` | omnicompany | 否 | 文档体系 (plan + report + standards) |

## 三、 `omnicompany` 内部边界

### 在 `.claude/` 的最小内容 (不能避免)

只有一份: `.claude/settings.json` 由 cc_wrapper `settings_installer` 维护, 含 hook 命令列表.

```json
{
  "hooks": {
    "SessionStart": [...],
    "PreToolUse": [...],
    "PostToolUse": [...],
    ...
  }
}
```

cc_wrapper 的 `_HOOK_MARK = "[omnicompany]"` 标识哪些 entry 是 omnicompany 自己的, 让 uninstall 能精准移除.

### 在 `.omni/` 的所有 omnicompany 设施

参看上面表格. `.omni/` 是 omnicompany 主目录, 跨所有 G1-G4 设施.

### `.claude/skills/` 的形态 (薄包装)

Claude Code 有 `.claude/skills/<skill>/` 概念. omnicompany 用法:
- skill 文件**只是薄包装**, 内容是 `docs/standards/` 唯一源的引用 (详 [single_source_thin_wrap.md](single_source_thin_wrap.md))
- omnicompany 模板有自己的体系 `templates/<kind>/` (跟 `.claude/skills/` 平行存在但分工: skill 是 Claude 入口, templates 是模板源)
- 一个 skill 可以引用多个 docs/standards/<X>.md 跟多个 templates/<kind>/ 但**不复述**它们的内容

## 四、 跨边界的协作

### cc_wrapper 是桥梁

`dashboard/cc_wrapper/` 是 omnicompany 内代码, 但它**写** `.claude/settings.json` 让 Claude Code 触发我们的 hook. 这是必要的跨边界:

```
Claude Code 进程 ── 读 .claude/settings.json ── 触发 hook 命令
                                                    │
                                                    ▼
                                       <python> -m omnicompany.dashboard.ccdaemon.hooks.<X>
                                                    │
                                                    ▼
                                       写 .omni/sandbox/ / data/ide_events.db / ...
```

这是 dashboard cc_wrapper 主要作用 — 把 Claude Code 跟 omnicompany 联动起来.

### 跨边界的 trace_id

trace_id 跨 `.claude` 跟 `.omni`:
- Claude Code 给的 `session_id` (在 hook 的 stdin payload)
- dashboard PTY 给的 `OMNI_CC_PTY_ID` (env 传给 claude 子进程)
- omnicompany 用 `cc_<session_id>` 或 PTY_ID 派生 trace_id, 落 `.omni/data/cc_session_active.json`

身份链跨平台, 但**身份解析逻辑**在 omnicompany 内 (`services/_core/identity/resolver.py`).

## 五、 演进 (留下一阶段)

- **`.claude/settings.json` 不可避免** — Claude Code 平台强制位置, cc_wrapper 必须写这
- **`.claude/skills/` 是薄包装** — 详 [single_source_thin_wrap.md](single_source_thin_wrap.md). 当前两个 skill (omnicompany-dev / omnicompany-use) 还是厚副本形态, 待治理
- **`.claude/agents/` 不复用** — 我们用 `services/_core/agent/` + `templates/agent/`
- **`.omni/` 持续扩展** — 但每加一个 `.omni/<sub>/` 必先经用户批准 (跟 auto-memory `feedback_no_dir_creation_without_approval` 一致)

## 六、 反模式

**SKILL.md 厚副本 / 复述 docs 规则** — 用户原话"修改起来麻烦"的根源. 详 [single_source_thin_wrap.md](single_source_thin_wrap.md).

**业务包写 `.claude/skills/<X>` 当 omnicompany 模板** — 模板落 `templates/<kind>/`, skill 是引用 / 入口型.

**hook 文件直接写 `.claude/<其他>`** — hook 落 `.omni/sessions/` / `data/ide_events.db`, 不写 `.claude/` 内容 (除了 cc_wrapper 维护 settings.json).

**新增 `.claude/<新子目录>` 给 omnicompany 用** — `.claude/` 是 Claude Code 的, 不扩展.

**hook 路径写死 `~/.claude/...`** — 跨平台兼容用 `Path.home() / ".claude" / ...` + scope=user/project.

**新增 `.omni/<子目录>` 不经批准** — 跟新建任何目录一样必须先问用户 (feedback_no_dir_creation_without_approval).

## 七、 实施引用

- `omnicompany/src/omnicompany/dashboard/cc_wrapper/settings_installer.py` - 写 `.claude/settings.json`
- `omnicompany/src/omnicompany/dashboard/cc_wrapper/hooks/*.py` - 跨边界桥
- `omnicompany/src/omnicompany/packages/services/_core/identity/resolver.py` - 跨边界身份解析
