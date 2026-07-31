<!-- [OMNI] origin=codex domain=omnicompany/standards/_global ts=2026-07-10 type=doc status=active agent=codex -->
<!-- [OMNI] summary=".claude/.codex/.agents 平台目录与 .omni 单一真源边界" -->
<!-- [OMNI] why="旧文档只登记 Claude Code，已无法解释 Codex hooks、官方 .agents skills 与 Atlas 导出边界。" -->
<!-- [OMNI] tags=standard,directory,boundary,foundation,thin-wrapper,claude,codex -->
<!-- [OMNI] material_id="material:standards.global.dot_claude_dot_omni_directory_boundary.md" -->

# Agent 平台目录与 Omnicompany 的关系

> **状态**: v3（2026-07-10）
> **核心铁律**: [single_source_thin_wrap.md](single_source_thin_wrap.md)（唯一源 + 薄包装）

## 一、原则

- `.claude/`、`.codex/`、`.agents/` 是 agent runtime 的必要发现位置，不是 Omnicompany 业务真源。
- 规则、工作顺序和业务数据仍以 `docs/standards/`、`docs/plans/`、`data/` 与 `.omni/` 为真源。
- 项目 skill 应是薄入口：frontmatter 负责发现，正文链接权威标准/计划；不要复制一份会漂移的长规则。
- Claude 与 Codex 的配置格式、事件能力和信任模型不同，共用 hook 实现不等于共用配置文件。

## 二、目录分工

| 目录 | 消费者 | Omnicompany 用途 |
| --- | --- | --- |
| `.claude/settings.json` | Claude Code | 项目级 Claude hooks |
| `.claude/skills/<name>/SKILL.md` | Claude Code | Claude 项目 skill 薄入口 |
| `.codex/hooks.json` | Codex | 项目级 Codex lifecycle hooks；首次/变更后 `/hooks` 信任 |
| `.codex/config.toml` | Codex | 可选项目级 Codex 配置；不要与 hooks.json 重复定义同层 hooks |
| `.agents/skills/<name>/SKILL.md` | Codex / cross-agent | Codex 官方项目 skill 入口；从 cwd 向 git root 逐层发现 |
| `~/.agents/skills` | Codex | 官方用户级 skill 安装目标 |
| `$CODEX_HOME/skills` | Codex/兼容工具 | 内置、system 与旧版兼容发现；Atlas 不再向这里安装 |
| `.omni/` | Omnicompany | Guardian、cron、运行态与治理设施 |
| `data/` | Omnicompany | 会话台账、event DB 与业务数据 |
| `docs/` | 人与 agent | 标准、计划、报告的权威文本 |

## 三、配置桥

`dashboard/ccdaemon/` 是跨边界桥：

```text
Claude Code -> .claude/settings.json -> ccdaemon/hooks/*
Codex       -> .codex/hooks.json     -> ccdaemon/hooks/* --provider codex
                                           |
                                           v
                         identity ledger / ide_events.db / Guardian
```

安装入口：

```powershell
omni cc install --provider claude_code --scope project
omni cc install --provider codex --scope project
```

安装器只能管理带自身命令标识的 block，不能覆盖用户的其他设置。Codex project hooks 的信任属于 Codex runtime 状态，不写入 Omnicompany 数据。

## 四、技能的真源与导出

- Atlas canonical object-SKILL 是 `data/domains/project_atlas/skills/.../SKILL.md`。
- `omni atlas export` 把批准项导出到 `~/.claude/skills` 与 `~/.agents/skills`。
- `~/.codex/skills`/`$CODEX_HOME/skills` 只作旧版或内置内容读取，不再是 Atlas 写入目标。
- 仓库自带的 `.claude/skills` 与 `.agents/skills` 应保持薄；同一能力可以有两个入口，但正文应引用同一权威文档。
- Codex plugin skill 属于 plugin 安装产物，以 `$plugin:skill` 调用；不要把缓存目录当 canonical 编辑。

## 五、身份边界

- Claude Code：`provider=claude_code`，trace 通常为 `cc_<session_id>`。
- Codex：`provider=codex`，trace 为 `codex_<session_id>`。
- 通用字段是 `session_id`；`claude_session_id` 只保留兼容用途。
- 当前会话指针在 `data/cc_session_active.json`，多会话聚合读 `data/cc_session_bindings.json`。

## 六、反模式

- 把 `.agents/skills` 当成文档真源，直接在导出副本里长期维护规则。
- 继续把 Atlas Codex 安装目标写成 `~/.codex/skills`。
- 在 `.claude/settings.json` 中注册 Codex hook，或把 Claude hook block 原样抄进 `.codex/hooks.json`。
- 用 `lstrip("./")` 归一化隐藏 drawer 路径，导致 `.agents` 被误判成 `agents`。
- 让 Codex 记录冒充 `claude_session_id`，使 dashboard 聚合错 provider。
- 同一 config layer 同时在 `.codex/hooks.json` 与 `.codex/config.toml` 定义 hooks。

## 七、实施引用

- `src/omnicompany/dashboard/ccdaemon/installer.py` — Claude settings/MCP 安装
- `src/omnicompany/dashboard/ccdaemon/codex_installer.py` — Codex hooks 安装
- `src/omnicompany/dashboard/ccdaemon/hooks/` — 双 provider 共用 hook 实现
- `src/omnicompany/packages/services/_core/identity/resolver.py` — 跨 provider 身份台账
- `src/omnicompany/cli/commands/atlas.py` — skill 审批后导出
