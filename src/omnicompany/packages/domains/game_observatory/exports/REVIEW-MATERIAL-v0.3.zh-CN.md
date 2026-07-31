# 逆向游戏设计案设施 v0.3 · 完整设施验收与双样本审阅材料

> 计划：`REVERSE-ENGINEERED-GAME-DESIGN-FACILITY`  
> 审阅状态：Gate 0–5 技术验证全部通过；Gate 5 等待非开发者对“是否真的像一份反推策划案、是否帮助理解游戏设计”作出裁决。  
> 运行边界：本地设施；不含公网部署、USB 真机和远程 ADB。AFK Journey 只使用本机 MuMu。

## 一 · 请审阅什么

这不是文章库、截图画廊或用户研究工具。设施的唯一发布本体是“从外部玩家可见内容反推的游戏设计案”：它把真实画面、反推页面设计稿、归一化布局、交互流程、状态、机制、资源、数值、反馈、教程、失败恢复、依赖、玩家声音、来源与客观 benchmark 组织成同一个可追溯对象。

本次请重点判断：

1. 不看源码和实现说明，仅阅读网站中的 AFK Journey 或 Minecraft 档案，能否理解系统的入口、页面、流程、规则、资源、反馈与失败恢复？
2. “真实画面 / 反推设计稿 / 布局”三种视图与 Wireflow 是否足以把页面和交互讲清楚？
3. 玩家声音是否被恰当地绑定到具体设计对象，而没有替代设计案本体？
4. 整体形态是否已经是“反推出来的策划案”，而不是披着设计术语的文章？

## 二 · 本地入口

- 设施首页：<http://127.0.0.1:8210/game-observatory/>
- AFK Journey · 英雄厅与赛季英雄升级：<http://127.0.0.1:8210/game-observatory/report/afk-journey-hero-upgrade>
- Minecraft · 世界内生火、营火烹饪与熟食反馈：<http://127.0.0.1:8210/game-observatory/report/minecraft-first-night-fire-and-food>
- 健康检查：<http://127.0.0.1:8210/api/game-observatory/health>
- 结构化目录：<http://127.0.0.1:8210/api/game-observatory/catalog>

冷重启后，上述首页、health、catalog、两份设计案和 Minecraft gates 证据端点均返回 200；HTML 路由带 Content Security Policy。8210 当前由 Omnicompany dashboard 正式启动，8201 ccdaemon 不受重启影响。

## 三 · 设施链路

```text
本地 MuMu / Minecraft 客户端与固定世界
    ↓ 租约、动作、截图/视频帧、UI/运行状态
证据与来源存储（hash、版本、运行记录、玩家声音）
    ↓ 对象级关系
Canonical ReverseEngineeredGameDesignSpec v0.3
    ↓ 编译
结构化 JSON + 语义 HTML + SVG 图 + 交互网站
    ↓
真实浏览器质量门 + benchmark + monitor + 备份恢复 + 审阅台
```

设施当前保存 183 个设计对象、914 条设计关系、156 个资产、157 个运行记录、59 份来源快照和 19 条玩家声音账本记录。两份发布档案都满足 v0.3 合同；三份旧的文章式/不完整档案仍保留为草稿，不能进入公开目录。

## 四 · 双样本结果

### 4.1 AFK Journey · 英雄厅与赛季英雄升级

- 发布对象：`report.afk-journey.hero-upgrade.v1`
- 真实环境：AFK Journey CN 1.7.21，Android 15，本机 MuMu，1080×1920。
- 内容：4 个真实页面/状态、4 张真实画面、4 份反推线框图、1 份 Wireflow、完整设计章节、源码 oracle、3 条绑定到对象的玩家声音。
- 客观判定：OCR 从真实英雄详情读出赛季等级 `357`、永久等级 `305`、战力 `151万`、金币 `21567万/13561`、赛季资源 `29950/8518`；源码仍含 `GetLevelUpCost`、下一等级计算和 `btn_upgrade` 路径。
- canonical benchmark：7/7 通过，所有 `actual` 已写回；`no-resource-mutation.actual=true`。
- 安全边界：没有执行升级、购买、领奖或账号设置变更。当前没有获准的可复位账号快照，因此不把资源扣除和升级后反馈伪装成已验证事实。
- 运行证据：`data/domains/game_observatory/exports/afk-mumu-hero-upgrade-observation.json`
- 浏览器截图：`data/domains/game_observatory/proofs/stage3/afk-live-design-reader-desktop.png`、`afk-live-design-reader-mobile-fixed.png`、`afk-surface-real-to-wireframe.png`

### 4.2 Minecraft · 第一夜生火、食物与火光

- 发布对象：`report.minecraft.voxelcraft-fire-food.v2`
- 真实环境：Minecraft Java 1.21.1 / ProtoWorld first-night v4，固定世界快照 `eba1aee`。
- 内容：4 个页面/世界界面对象、7 张真实客户端画面、4 份反推线框图、1 份 Wireflow、18 个完整设计章节、源码/配置 oracle、3 条绑定到对象的玩家声音。
- 客观判定：真实客户端输入完成散石、堆枝、生火、烤制、进食、身体恢复、三餐、夜间威胁、火光和进阶食物链；`G1–G24` 为 `24/24`。
- 恢复：临时玩家退出后，`reset_world.ps1` 归档并重建固定世界，恢复为快照 `eba1aee`。
- 任务边界：ProtoWorld 的 `RecipeZeroingHook` 清空原版配方，实时探针返回 `Unknown recipe: minecraft:stone_pickaxe`。因此石镐不是本实例的有效任务；本轮使用计划允许的等价系统验证，并把配方缺失、空配方书和恢复路径作为设计依赖保存，没有伪造石镐流程。
- 运行证据：`data/domains/game_observatory/exports/minecraft-first-night-fire-food.json`
- 浏览器截图：`data/domains/game_observatory/proofs/stage4/minecraft-design-spec-desktop.png`、`minecraft-design-spec-mobile.png`、`minecraft-campfire-reconstruction.png`

## 五 · Gate 0–5 裁决

| Gate | 验收对象 | 技术状态 | 有效性状态 |
|---|---|---|---|
| 0 | v0.3 合同、发布门、本地 catalog/sitemap、来源审计 | passed | 客观证据已验证 |
| 1 | Canonical 设计对象、关系、来源、资产、编辑与修订 | passed | 客观证据已验证 |
| 2 | 编译器、完整设计案网站、桌面/移动浏览器与并发 | passed | 客观证据已验证 |
| 3 | AFK Journey MuMu 真实样本 | passed | 客观证据已验证 |
| 4 | Minecraft 固定世界真实样本 | passed | 客观证据已验证 |
| 5 | 冷启动、质量门、monitor、备份与恢复 | passed | 等待非开发者审阅 |

机器索引：`data/domains/game_observatory/exports/phase-proofs/index.json`。最终裁决为：`review_ready=true`、`technical_passed=[0,1,2,3,4,5]`、`overall_passed=[0,1,2,3,4]`、`review_pending=[5]`。

## 六 · 质量与恢复证据

- 领域回归：`66 passed`；Ruff、JavaScript 语法、`pyproject.toml` 解析全部通过。
- 当前依赖：项目 venv 已安装 RapidOCR；`numpy 1.26.4`、`opencv-python 4.11.0.86` 与 Airtest 1.4.3 兼容，`pip check` 无冲突。
- 网站质量：`ok=true`、`site_shell_ready=true`、`archive_complete=true`。
- 性能：本地 HTTP p95 `24.831 ms`；真实浏览器导航 p95 `927.982 ms`；20 并发请求墙钟 `246.896 ms`。
- 浏览器：两份报告的桌面/390px 移动端无横向溢出；图片全部解码；无重复 ID、无无名控件、无控制台错误、无失败请求、无 HTTP 错误。
- Monitor：SQLite integrity `ok`；156 个资产全部检查，无缺失、损坏、无效画面和缺失公开输出。
- 最终备份：`data/domains/game_observatory/backups/20260713T122434Z-c500bd8c`，404 个文件校验通过。
- 恢复演练：`data/domains/game_observatory/recovery-drills/20260713T122437Z-721e726b`，数据库与对象计数一致，恢复副本 monitor 通过。
- 质量文件：`data/domains/game_observatory/exports/public-site-quality-validation.json`
- 浏览器证据：`data/domains/game_observatory/exports/public-site-browser-evidence.json`
- Monitor：`data/domains/game_observatory/exports/monitor.json`
- 恢复证据：`data/domains/game_observatory/exports/recovery-drill.json`

## 七 · 复现命令

在 `E:\WindowsWorkspace\omnicompany` 下执行：

```powershell
.\venv\Scripts\omni.exe dashboard restart
.\venv\Scripts\python.exe -m omnicompany.packages.domains.game_observatory.cli validate
.\venv\Scripts\python.exe -m omnicompany.packages.domains.game_observatory.cli promote-afk-live-design --file data\domains\game_observatory\captures\afk-hero-hall-20260713\manifest.json
.\venv\Scripts\python.exe -m omnicompany.packages.domains.game_observatory.cli promote-minecraft-live-design --file data\domains\game_observatory\captures\minecraft-first-night-20260713\manifest.json
.\venv\Scripts\python.exe -m omnicompany.packages.domains.game_observatory.cli site-quality --base-url http://127.0.0.1:8210
.\venv\Scripts\python.exe -m omnicompany.packages.domains.game_observatory.cli monitor
.\venv\Scripts\python.exe -m omnicompany.packages.domains.game_observatory.cli phase-proofs
.\venv\Scripts\python.exe -m pytest tests\domains\game_observatory -q
```

备份恢复：

```powershell
.\venv\Scripts\python.exe -m omnicompany.packages.domains.game_observatory.cli verify-backup --destination data\domains\game_observatory\backups\20260713T122434Z-c500bd8c
.\venv\Scripts\python.exe -m omnicompany.packages.domains.game_observatory.cli recovery-drill --destination data\domains\game_observatory\backups\20260713T122434Z-c500bd8c
```

## 八 · 已知边界与审计设施观察

- 公网部署、USB 真机、远程 ADB 和 AFK 资源变更型 benchmark 明确不在当前计划内。
- 三份旧档案只作为迁移/失败样本保留为草稿，不是当前发布内容。
- `project-audit` 管线按现有计划入口运行时，在输出节点 prompt/schema 不完整的 probe 告警后持续挂起且没有生成一份新的项目审计报告，已终止该次运行。它暴露的是审计管线自身成熟度问题；本材料没有把 2026-07-10 的旧 `probe_baseline.json` 伪装成当前审计结果，也没有让该失败替代 Gate 0–5 的确定性证据。
- `debug-loop` 未用于本次实现与验收。

## 九 · 希望得到的审阅结论

请对以下一句话作出接受或驳回，并说明最妨碍理解的具体页面/章节：

> 当前设施已经能够把真实游戏游玩证据反推成一份可读、可追溯、可机器处理的游戏设计案；AFK Journey 与 Minecraft 两份样本证明它不是文章库，也不是单一平台或单一 UI 类型的特化工具。