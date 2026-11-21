<!-- [OMNI] origin=ai-ide domain=omnicompany/standards ts=2026-05-01T00:00:00Z type=doc status=active agent=ai-ide-bd9cde92 -->
<!-- [OMNI] summary="omnicompany 沙盒目录规范 — 自由写入的草稿区, 定期归档, 预置规范向导" -->
<!-- [OMNI] why="为防修改锁开启之后, 写入者仍有一个不需要预先注册的草稿空间反复试错, 想清楚再走注册流程转正式区" -->
<!-- [OMNI] tags=sandbox,standard,registry,sessions,omnicompany -->
<!-- [OMNI] material_id="material:standards.cli.sandbox_directory_specification.md" -->

# omnicompany 沙盒目录规范

> **状态**: 设施层规范第一版 (2026-05-01)
> **关联实现**: `<workspace>/.omni/sandbox/` 真目录 (本规范同时落盘了)
> **关联记忆**: `feedback_omnicompany_sandbox_directory.md`

## 一、 沙盒目录是干嘛的

omnicompany 防修改锁开启之后, 锁内任何未注册的写入都会被罚单化。但写入者 (AI IDE / 用户 / 工人) 经常需要"先写出来再判断这是什么"的缓冲空间 — 还没想好类型 / 还在反复改 / 临时跑数 / 验证一段代码能不能跑。直接进锁内会被拦, 但创作不能没有缓冲。

沙盒目录就是这个缓冲区。**写入沙盒不需要预先注册**, 可以自由写、改、删。但沙盒不是长期存放区, 内容会被定期全量归档转走, 沙盒本身不沉淀。

跟罚单区是两件事: 沙盒是合法路径, 写入者主动进入; 罚单区是写入者绕过流程写到锁内导致内容被没收的归宿, 不应该主动写。

## 二、 路径设计

沙盒落在 omnicompany 项目根的 `.omni/` 下面:

```
omnicompany/.omni/
  sandbox/                  — 沙盒目录 (本规范)
    drafts/                 — 写入者放草稿的地方
      <kind>/<name>/        — omni new 立的草稿目录
    archive/                — 沙盒归档去处
      <YYYY-MM-DD-HHMM>/    — 一次 omni sandbox archive 打包一份
```

`drafts/` 是真正的草稿区, 写入者随便写. `archive/` 是 `omni sandbox archive` 把 drafts/ 全量归档的去处.

**guides/ 不预置副本** (2026-05-02 跟"唯一源 + 薄包装" 铁律对齐): 各 kind 的规范向导**不在沙盒预存副本**, 走 CLI 实时读源:

```bash
omni sandbox guide --kind=material   # 实时读 templates/material/向导.md
omni sandbox guide --kind=worker     # 同上
omni sandbox guide --kind=header     # 实时读 docs/standards/cli/omni-header.md
```

唯一源在 `templates/<kind>/向导.md` (kind 模板向导) 跟 `docs/standards/` (规范文档). 不复制副本到沙盒避免漂移. 详 [single_source_thin_wrap.md](../_global/single_source_thin_wrap.md).

`session 持久化` 现实位置在 `data/cc_session_active.json` (跟 G1 联动), 不在 `.omni/sessions/`. 详 [identity.md](identity.md).

## 三、 沙盒里的工作流程

写入者从沙盒到正式区, 标准步骤是这样:

第一步, **先在 `drafts/` 自由写**。文件名随便, 内容随便。这一步不需要走任何注册流程, 不需要补 OmniMark 头, 不需要分类。目的是把想法落地, 反复改, 一直到自己确认这份内容是真要往正式区放的。

第二步, **读 `guides/` 里对应概念的规范副本**。例如要新增一份工人, 读 `guides/worker.md`; 要建一份团队, 读 `guides/team.md`。规范副本里有那个概念的字段约束、命名规范、文件头要求、检查清单。

第三步, **自检内容是否符合规范**。这一步是用户原话"反思内容是否符合位置"的具体落地。看自己的草稿: 字段齐不齐? 文件头规范不规范? 命名按规则吗? 该补的 summary / why / tags 三字段补了吗?

第四步, **走 omnicompany CLI 的注册流程把内容转到正式区**。CLI 命令大致是 `omni sandbox promote --content=<草稿路径> --target=<正式路径>` (具体命令规范在 omnicompany CLI 草案文档里)。注册成功后内容进正式区, 沙盒里的草稿可以删, 也可以等定期归档时一并转走。

第五步, **确认转移成功**。读正式区那份新文件, 确认 OmniMark 头正确、内容跟草稿一致、注册中心有登记。没问题这一轮工作就完了。

## 四、 沙盒的生命周期

**写入**: 自由写, 任何来源 (AI IDE / 用户 / 工人) 都能写, 不需要预先注册。

**短期存放**: 草稿可以在 `drafts/` 里反复改, 没有强制时长, 但写入者应当尽快走转正式区或者主动删。

**定期归档**: 守护进程定期 (推荐每周一次, 具体频率由用户配) 把 `drafts/` 里所有内容打包到 `archive/<YYYY-MM-DD-HHMM>/` 目录, 然后清空 `drafts/`。归档目录是只读的, 不会再被新写入污染。

**归档保留期**: `archive/` 里的归档保留多久, 由用户配 (推荐 90 天)。过期归档由清理工人扫除, 但清理前会再做一次"是否有内容应该转正式区却没转"的复核。

## 五、 跟其他设施的协作

**跟 session 持久化的关系**: 沙盒里的内容**不强制带 session ID**, 但建议加 (例如在草稿头里写 `# wrote in session bd9cde92`), 让事后归档时知道是谁的草稿。OmniMark 文件头规范的 `agent` 字段刚好用来填 session 短 ID。

**跟防修改锁的关系**: 锁开启之后, `<workspace>/.omni/sandbox/drafts/` 是少数允许自由写入的"锁外"路径之一。锁机制需要把 sandbox 路径加到放行白名单。

**跟罚单机制的关系**: 罚单区 (`<workspace>/.omni/penalty/` 或类似) 是写入者绕过沙盒+注册流程导致内容被没收的归宿; 沙盒是写入者**主动**用的合法草稿区, 两者不重叠也不替代。

**跟 omnicompany CLI 的关系**: CLI 提供 `omni sandbox open` (返回沙盒路径) / `omni sandbox check --content=<草稿路径>` (用规范确认办法自检) / `omni sandbox promote` (走注册流程转正式区) / `omni sandbox archive` (手动触发归档) / `omni sandbox guide --kind=<概念类型>` (展示对应规范向导)。具体命令规范在 omnicompany CLI 草案文档里。

## 六、 反模式

**把沙盒当永久仓库**: 沙盒不是 `~/Documents/`, 不是个人云盘。任何在 drafts 里超过一周没动过的内容应该走转移或主动删。`archive/` 里的归档保留时长用户可配但有上限。

**绕过沙盒直接写正式区**: 这正是防修改锁要拦的事。"反正都是 AI IDE 写的, 直接走 Edit 不就行了" — 不行, 没经过注册和反思的内容进正式区会拖累整个 omnicompany 的可管理性, 这是用户立沙盒+锁的根本动机。

**沙盒里写代码塞进文档目录之类**: 沙盒 drafts 不强制分类, 但不等于鼓励混乱。最好按概念类型分子目录 (`drafts/workers/`, `drafts/docs/`), 哪怕草稿阶段也保持基本秩序, 这样转移时少点纠结。

**SessionStart hook 没启用就开始用沙盒**: 沙盒文件不强制 session ID, 但建议带。如果 hook 没装, `current_writer_identity()` 返回 `ai-ide-unknown`, 沙盒里写出来的草稿事后追溯困难。
