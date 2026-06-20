---
name: publish
description: 对外发布与知识备份治理域。首件管线 = 明文知识快照:遍历工作区收一切明文文本、排掉图片/媒体/压缩/二进制与构建/缓存/临时目录(逐文件二进制嗅探+超大跳过)、镜像进远端私有仓的暂存克隆、git add -A 算增删改、提交并可选推送。何时用:要把工作区的明文知识(源码+设计文档+配置)备份/镜像到私有仓、或预览一次快照会改动什么时。触发关键词:发布、备份、知识快照、镜像、snapshot、推送 gitee、明文备份、脱敏发布、release、publish、对外发布。全程确定性无 LLM;默认 dry_run 先预览,显式 --push 才推远端。
---

# publish

publish 是对外发布与知识备份的统一家(规划覆盖三远端发布:脱敏/全量 + 知识备份)。当前落地的首件管线是 AIWorkSpace 明文知识快照:把工作区里"我写的明文知识"(源码+设计文档+配置)镜像刷新到远端私有仓的 `aiworkspace-snapshot` 分支。全程 RULE 确定性、无 LLM;对外推送默认显式 `dry_run` 可预览。

## 何时用

- 把工作区明文知识备份/镜像到远端私有仓,排掉图片/媒体/二进制与构建产物、生成数据、工具私有态。
- 先预览一次快照将新增/修改/删除哪些文件(增删改统计),确认后再实际提交推送。
- 需要"选中明文集成为远端唯一真相"的增量镜像(删多余、只拷变更、按 size+mtime 跳过未变)。

## 怎么跑

```bash
# 预览(只算清单+diff,不提交不推送)
omni run publish.aiworkspace_snapshot -i dry_run=true
# 本地提交(默认不推远端)
omni run publish.aiworkspace_snapshot
# 提交并推送到远端私有仓 aiworkspace-snapshot 分支
omni run publish.aiworkspace_snapshot -i push=true
```

参数(CLI 实际注册):`src`(工作区根,默认 `OMNI_AIWORKSPACE_ROOT` 或内置默认)、`dry_run`(只预览)、`push`(提交后才推远端,默认只本地提交)、`max_file_mb`(单文件大小上限,默认 2,超过当数据跳过)。

## 管线节点

三节点确定性 Team(`publish.aiworkspace_snapshot`):

1. **scan**(RULE,ScanSource)—— 遍历源根选明文:扩展名黑名单(图片/媒体/压缩/文档二进制/字体/可执行/数据库)+ 逐文件二进制嗅探(读头 8KB,含空字节即二进制)+ 超大跳过;目录黑名单排构建/依赖/缓存/备份/临时/点目录/生成数据。出清单 + 统计(included/skipped_ext/skipped_binary/skipped_large/by_top),建 run_dir。
2. **stage**(RULE,StageMirror)—— 暂存克隆对齐远端分支(fetch + hard reset,远端无该分支则建孤儿分支)→ 增量镜像选中明文为暂存树唯一真相 → `git add -A` → 算相对 HEAD 的增删改 name-status。
3. **commit_push**(RULE,CommitPush,管线 sink)—— 无变更不提交;`dry_run` 只报 diff 并把暂存树 reset 回去(不污染);否则提交,`push=true` 推到远端 `<branch>:<branch>`,推送失败醒目标 FAIL。

Format 链:`snapshot_request → snapshot_manifest → snapshot_staged → snapshot_result`;产物落 `data/domains/publish`。

## 配置

- `OMNI_AIWORKSPACE_ROOT`:覆盖默认源根(也可用 `-i src=...`)。
- 推送(`-i push=true`)需对远端私有仓有 git 凭证;不推送时只在本地暂存克隆提交,无需远端鉴权。
- Windows 长路径/CJK 路径已在暂存克隆内置 `core.longpaths=true`、`core.quotepath=false`、`core.autocrlf=false`,无需手动配。
