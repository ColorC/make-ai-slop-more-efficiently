# 目录清洁度守则

> 适用范围: Omnicompany 及其纳入 Guardian 的相邻项目。本文是通用守则, 不是 quant-lab 专用。

## 一、四条硬准则

### 1. 同类内容集中

同一消费对象、同一生命周期、同一事实来源的内容必须集中在一个主目录。允许有发布镜像, 但镜像目录必须只放机器消费产物, 不承载源文档或源代码的第二份事实。

判定:
- 人读判断与决策放 `reviews/` 或等价人读区。
- 机器读报告放 `reports/` 或等价机器产物区。
- 运行数据放 `data/`。
- 发布镜像放 `public/`、`site/`、`lab/data/` 等对外目录, 只允许由源目录生成。

禁止:
- 同一篇报告同时以源文档、导出 markdown、镜像 markdown 三种身份散落且没有索引关系。
- 代码、报告、日志、缓存同层平铺。
- 镜像目录反向成为编辑源。

### 2. 同层同级别

同一文件夹下的子目录必须回答同一个分类问题, 且规模、抽象层、生命周期接近。

判定:
- 同层元素都应是 code/data/doc/report/runtime 中的一类。
- 同层目录的变化频率应接近。
- 同层目录的颗粒度应接近, 不能同时放一个长期模块和一次性实验输出。

禁止:
- `server/` 下混放长期服务和一次性复现实验脚本。
- 根目录同时放 `lib/`、`data/`、`.tmp/`、`tmp_logs/`、`mlruns/`。
- `reports/` 下同时放按月报告、随手 CSV、调试截图和执行日志。

### 3. 临时与持久隔离

临时文件、运行缓存、一次性调试产物、备份副本必须进入声明过生命周期的位置。持久代码目录不接受一次性调试代码, 持久数据目录不接受未声明缓存。

推荐位置:
- `var/tmp/`: 可删除临时产物。
- `var/logs/` 或项目既有 `logs/`: 运行日志, 必须有保留期。
- `var/archive/`: 清理前保留的历史副本。
- `.omni/sandbox/`: agent 草稿、待提升材料。
- `tools/diagnostics/`: 可重复运行的诊断工具。

禁止:
- 根目录 `.tmp_*`、`tmp_*`、`debug_*`。
- `*.bak`、`.backup-*`、`*-old` 留在活跃目录。
- 截图脚本、浏览器调试脚本塞进 `logs/`。
- 账户 state/history 同层塞一批 `.bak.*` 目录。

### 4. 稳定命名, 版本入索引或归档

活跃路径使用稳定名。版本号、重试号、阶段号、copy/old/bak/retry/final 等标记只能出现在归档、实验批次、时间序列数据或明确声明的历史索引中。

允许:
- `journal/YYYY-MM-DD.md`
- `reports/YYYY-MM/<report-id>.yaml`
- `runs/<run-id>/`
- `_archive/<topic>/<date-or-run>/`
- 业务本身要求的日期分区。

禁止:
- `today-v2.json` 与 `today.json` 同层长期并存但没有迁移计划。
- `foo-v2.md`、`foo-v3.md`、`foo-final.md` 作为活跃文档。
- `account.bak.20260607` 留在活跃账户目录。

### 5. 工作区根 / 盘根 / 仓根是闭集

工作区根 (`C:/workspace/`)、盘根 (`E:\`)、单项目仓根 (如 `omnicompany/`) 的**第一层**只允许出现已登记的条目: 已知项目、约定 dotfolder (`.git`/`.omni`/`.claude` 等)、约定归档 (`_archive`/`_scratch`)。任何没登记的新顶层目录/文件默认是污染, 必须当场解释清楚或清掉。

判定 (出现下面任一即污染):
- 顶层冒出一个不在闭集里的目录, 没人能说清它哪来的、归谁。
- 一个目录该归属某个具体项目, 却散落在工作区根/盘根 (例: `C:/workspace/data\intent_traces.db` 本属 `omnicompany/data/`)。
- 参考项目 (别人的仓、上游源码) 直接铺在工作区根, 而不是收进 `参考项目/` 或所属项目的 vendor 子目录 (例: `affine`、`_vendor_tmp` 该进 `参考项目/`)。
- 单项目仓根冒出未声明的 dotfolder (例: `omnicompany/.agent_state/` 该并进 `data/` 或 `.omni/`, 不单立一个根目录)。

禁止:
- 盘根/工作区根出现 `e`、`root`、`tmp`、`bin`、`data` 这种**像路径片段的裸目录** (几乎一定是手误, 见 §6)。
- 把一个项目的私有运行数据写到另一个项目或工作区根 (例: `WS\AIWorkSpace\sandbox` — 真 `AIWorkSpace` 在别处)。
- 包管理器把全局缓存默认落在盘根 (例: `E:\.pnpm-store` — 应配 store-dir 到项目内或用户级)。

### 6. 根因铁律: 绝不用相对路径写工作区根/盘根

上面 §5 的污染**绝大多数是同一个根因**: agent 或命令在错误的工作目录 (盘根 `E:\` 或工作区根 `C:/workspace/`) 下, 用了**相对路径**写文件, 于是垃圾长在了当前目录的顶层。

真实证据 (2026-06-26 实扫):
- `E:\e\WindowsWorkspace` — 在 `E:\` 下跑了形如 `xxx e/WindowsWorkspace/...` 的相对路径 (本该是绝对 `C:/workspace/` 或 `/e/WindowsWorkspace`)。
- `E:\tmp\*.txt` — 在 `E:\` 下用相对 `tmp/` 当临时区 (本该用项目内 `var/tmp` 或沙盒)。
- `C:/workspace/data\intent_traces.db`、`C:/workspace/AIWorkSpace\sandbox` — 在工作区根下用相对 `data/...`、`AIWorkSpace/...` (本该进 `omnicompany/data/`、真 AIWorkSpace)。

铁律:
- 写文件、建目录**一律用绝对路径**, 或**先 `cd` 进目标项目再用项目内相对路径**; 永不在盘根/工作区根直接用裸相对路径。
- 临时产物去声明过生命周期的位置 (§3), 不要图省事写 `./tmp`、`./data`。
- 一旦发现盘根/工作区根有像路径片段的裸目录 (`e`/`root`/`tmp`/`bin`/`data`), 先当手误处置: 查内容、查创建时间、定位是哪条命令/哪个 agent 写的, 再清理。

预防三层 (2026-06-26 落地):
1. **规范层**: 本节铁律, agent 读规范即知。
2. **实时 hook 层**: ccdaemon `lock_pretooluse` PreToolUse 守卫复用 hygiene-profile 闭集, 在【写入前】拦截 —— 写工具(Write/Edit)写到顶层 stray 会带根因详细提示(`stray_guard_mode=enforce` 时直接报错阻断, 默认 warn); Bash 在盘根/工作区根下跑写命令会警告"别用相对路径"。全程 fail-open。
3. **每日兜底层**: `guard-hygiene-daily` 扫顶层闭集 (§三), 漏网的次日必现。

## 二、修改工作流

1. 先审查, 不直接删除。
2. 对每个候选路径标注类别: `keep`、`move`、`archive`、`ignore`、`needs-owner`。
3. 只移动低风险对象: 临时文件、备份副本、诊断产物、空目录、可重生产物。
4. 对运行链路中的路径先查引用, 再移动。引用超过一处时先写迁移计划。
5. 移动前建目标归档目录, 不覆盖已有文件。
6. 移动后运行 Guardian hygiene、项目测试或至少运行路径扫描。
7. 把仍未清理的例外写进 profile, 让后续扫描持续提醒。

## 三、Guardian 接入要求

纳入 Guardian 的项目应至少提供:

- `docs/archmap.yaml`: data 根目录闭集。
- `.omni/hygiene-profile.yaml`: 项目根闭集、相关根、禁止 glob、版本化扫描。
- `.gitignore`: 覆盖项目真实运行产物, 不能只覆盖语言默认噪音。
- `docs/directory-hygiene.md`: 项目专属目录画像和例外说明。

Guardian 只负责警示和排队, 不默认删除。任何自动移动必须进入归档或隔离区, 并保留可审计记录。

**工作区根 / 盘根本身也是扫描根** (§一·5): omnicompany 的 `.omni/hygiene-profile.yaml` 额外声明两个相对根 —— `workspace` (`path: ".."` = `C:/workspace/`) 与 `drive` (`path: "../.."` = `E:\`), 各带顶层闭集 (`allowed_root_dirs`/`allowed_root_files`)。对这种巨大外部根**只走一层闭集判定, 绝不递归** (profile 里标 `closed_set_only: true`), 避免扫炸。闭集外的顶层条目会被带**根因分类**告警 (手误目录 / 错误相对写 / vendored 参考 / 游离副本)。

**每天兜底**: 目录级 hygiene 扫描 (空目录 / 临时残留 / 老化 / 体积 / profile 闭集) 不在 `omni guardian patrol` 的 git-diff 链里, 必须由定时任务 `guard-hygiene-daily` (`omni guardian hygiene list`) 每天跑一次, 否则顶层污染会长期隐形 (2026-06-26 教训: `omnicompany/data/` 长期临时日志混永久库无人扫, 正因 hygiene 没进 cron)。
