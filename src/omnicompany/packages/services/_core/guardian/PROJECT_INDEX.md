---
omni_project: omni-guard
name: Omni Guard 守护防漂移
group: omnicompany
updated: 2026-06-12
roots:
  - path: C:/workspace/omnicompany/src/omnicompany/packages/services/_core/guardian
    note: 主目录(规则引擎 + 巡逻 + 罚单处置 + 长驻守护)
  - path: C:/workspace/omnicompany/src/omnicompany/packages/services/_core/protection
    note: 锁防护(主动防御, omni lock)
  - path: C:/workspace/omnicompany/.omni
    note: 运行态数据(sentinel 状态/巡逻报告/fix-queue/锁策略)
directory_contract:
  version: 1
  status: ready
  canonical_root: C:/workspace/omnicompany/src/omnicompany/packages/services/_core/guardian
  # 包目录整体是 omnicompany 源码（Git 跟踪），src 不写 "."。omnicompany/.omni 是全仓共用的运行区，不整体归本项目，只登记守护专属的子目录。
  roles:
    src: null
    docs: null
    tests: null
    input: null
    drafts: null
    runtime: null
    tmp: null
    archive: _archive
  placements:
    worktrees: null
    references: null
    publish: null
  lifecycle:
    tmp: 未单独声明；需要临时区时用工作区默认 .omni/tmp/<任务>/，任务结束核验后清理。
    runtime: 不在本根：sentinel 状态、巡逻报告、锁策略等写在 C:/workspace/omnicompany/.omni（另一个已声明根，根目录文件未细分），守护专属的子目录见 areas。
    archive: _archive/ 是旧巡逻实现的代码留档（README 2026-05-03），入 Git，不再写入。
  areas:
    - path: C:/workspace/omnicompany/.omni/guardian
      role: data
      producer: guardian/_patrol_shim.py（守护状态与 hygiene-whitelist.json）
      consumers: [guardian 巡逻与目录卫生检查]
      lifecycle: 守护状态与卫生白名单，正式状态，不按时间删
      status: active
    - path: C:/workspace/omnicompany/.omni/fix-queue
      role: drafts
      producer: guardian/auto_comment.py 的 write_fix_queue_entry（内部管线违规的 patch 草稿，按日期分目录）
      consumers: [人工处置罚单]
      lifecycle: patch 草稿；采纳或否决后由处置方收尾；草稿区不整棵删
      status: active
    - path: C:/workspace/omnicompany/.omni/quarantine
      role: archive
      producer: guardian/auto_comment.py 与 workspace_pollution.py 等（违规文件原件备份，按日期或来源分目录）
      consumers: [从 quarantine 恢复原件]
      lifecycle: 恢复依据，不按时间删；对应罚单结案后由业主决定是否清理
      status: active
entry_points:
  - path: C:/workspace/omnicompany/src/omnicompany/packages/services/_core/guardian/rules
    note: 20 条架构规则(OMNI-001 至 020), 纯计算无副作用
  - path: C:/workspace/omnicompany/src/omnicompany/packages/services/_core/guardian/sentinel.py
    note: 长驻守护进程(活跃才扫, 冷却节流, pid 单例)
  - path: C:/workspace/omnicompany/src/omnicompany/cli/commands/guardian.py
    note: omni guardian 全部子命令
  - path: C:/workspace/omnicompany/src/omnicompany/cli/commands/protection.py
    note: omni lock 全部子命令
  - path: C:/workspace/omnicompany/docs/standards/cli/omni-header.md
    note: OmniMark 文件身份头规范(权威, v3)
  - path: C:/workspace/omnicompany/docs/standards/cli/lock.md
    note: 锁防护规范(权威)
latest:
  - "2026-05-02 锁防护离线版完成(enable/scan/handle/baseline 全套), 实时拦截是下一阶段, 规范见 docs/standards/cli/lock.md"
  - "2026-05-01 OmniMark 身份头规范升到 v3, 见 docs/standards/cli/omni-header.md"
  - "2026-04-23 sentinel 最近一次自动巡逻; 巡逻能力本身完整(按 git diff/全量/回溯), 近期以手动 patrol 为主"
quick_actions:
  - label: 巡逻一遍
    skill: null
    where: C:/workspace/omnicompany
    desc: venv/Scripts/omni.exe guardian patrol (按 git diff 跑 20 条规则, 只警告不改文件; --full 全量)
  - label: 健康检查
    skill: null
    where: C:/workspace/omnicompany
    desc: venv/Scripts/omni.exe guardian health (完整守护管线, --fix 自动清理根目录违规文件)
  - label: 看罚单
    skill: null
    where: C:/workspace/omnicompany
    desc: venv/Scripts/omni.exe guardian tickets (违规罚单列表; whitelist/restore 处理)
  - label: 守护报告
    skill: null
    where: C:/workspace/omnicompany
    desc: venv/Scripts/omni.exe guardian report (聚合规则扫/巡逻/审计成 Markdown 报告)
  - label: 锁状态
    skill: null
    where: C:/workspace/omnicompany
    desc: venv/Scripts/omni.exe lock status / scan / handle (看锁、离线扫违规、按类处置)
links: []
---
# Omni Guard 守护防漂移

## 概况

omnicompany 的守护设施, 防止仓库被各路 AI 写漂: 20 条架构规则盯着代码不偏离规范,
每个文件带 OmniMark 身份头可溯源(谁写的/什么时候/哪条 trace), 违规生成罚单按来源
自动处置(内部管线的进 fix-queue 等确认, 外部 agent 的警告-隔离-清理三段式),
再加一把"锁"防外部直写关键目录。

## 当前进展

主体能力齐了: 规则引擎 20 条、巡逻(手动 patrol + sentinel 长驻自动)、罚单全生命周期
(生成/白名单/恢复/溯源)、OmniMark 身份头 v3、锁防护离线版(2026-05-02 完成)。
锁的下一阶段是实时拦截(写入前钩子 + 文件监视), 分五级逐步收紧, 还没动工。
两条已知未修违规: narrative 包未注册到管线注册表、lang_rewrite 有死 Router。
权威规范在 docs/standards/cli/(omni-header.md / lock.md), 计划在 docs/plans/guardian/。

## 主要目录

- guardian/rules: 20 条规则模块, 每条独立检查
- guardian/workers: 巡逻三段链(扫 git diff、跑规则、出罚单)
- guardian/sentinel.py: 长驻守护进程, 看 .omni/core_activity_ts.json 判断有没有新活动
- guardian/tow_truck.py + auto_comment.py: 罚单管理 + 按来源双轨处置
- protection/: 锁防护(策略/扫描/处置)
- .omni/: 运行态(sentinel 状态、最新巡逻报告、fix-queue、锁策略)

## 能做什么

1. 规则巡逻: 按 git diff 或全量扫 20 条架构规则, 只警告不动文件
2. 罚单处置: 违规生成罚单, 按来源自动决策(修复草稿/警告/隔离), 可白名单豁免
3. 身份溯源: 给文件打/查 OmniMark 头, 违规能溯到来源 agent 和 trace
4. 锁防护: 圈定监视目录, 离线扫内部错位和外部直写, 打注释或移出
5. 长驻自动: sentinel 守护进程活跃触发增量扫, 冷却节流
6. 周边工具: 僵尸进程扫描、架构地图校验、Format/Router 描述质量报告

## 常见展开方式

- 例行体检: omni guardian patrol 或 health, 有违规看 tickets 再决定 whitelist/修
- 出守护报告: omni guardian report, 落在 data/services/guardian/reports/
- 查"这文件谁写的": omni guardian who <文件>; 溯源违规用 trace-violation
- 动锁之前先读 docs/standards/cli/lock.md, 历史存量先 baseline 快照再开
- 改规则/加规则: 去 guardian/rules/, 每条规则一个模块

## 自动补漏候选(机器生成,并入正文或删除即可)
<!-- projidx-candidates:begin -->
- [ ] (2026-07-04) 更新 entry_points/主要目录中 `guardian/rules` 的说明，补入近期新增规则族（至少 OMNI-093/094/095/099/100）及其作用，避免继续标成仅 20 条 OMNI-001 至 020。 — 证据: index 仍写 `guardian/rules` 为“20 条架构规则(OMNI-001 至 020)”；但当前规则聚合器 `C:/workspace/omnicompany/src/omnicompany/packages/services/_core/guardian/rules/__init__.py` 已接入 `authority_convergence`、`custom_llm_call`、`self_built_agent`、`plan_bindings_guardian`、`testmap_sync` 等，规则范围扩展到 OMNI-100；近 30 天提交也有 `2026-07-04 feat(governance): 建立测试台账(testmap)契约与同步规则`、`2026-07-03 ... 绑定注册表化...guardian worker真接线...`、`2026-06-21 router 改走标准 worker + Guardian OMNI-094 禁自定义 LLM 调用`。
- [ ] (2026-07-04) 在主要目录或 entry_points 补入 PlanBindingsScanWorker / plan_bindings_guardian，说明其负责绑定注册表巡检（缺锚/登记不完整/悬空）。 — 证据: 近 30 天提交 `2026-07-03 ... 绑定注册表化(四件登记结构化+omni governance bind全量写入+guardian worker真接线+定时巡检...)`；代码中存在新巡检资产 `C:/workspace/omnicompany/src/omnicompany/packages/services/_core/guardian/workers/plan_bindings_scan_worker.py` 与规则 `C:/workspace/omnicompany/src/omnicompany/packages/services/_core/guardian/rules/plan_bindings_guardian.py`，但 index 的 `guardian/workers` 仍只描述“巡逻三段链(扫 git diff、跑规则、出罚单)”。
- [ ] (2026-07-04) 在规则能力或周边工具中补入 `guardian/rules/testmap_sync.py`（OMNI-100），说明它检查源码变更后 testmap.yaml/测试锚是否同步。 — 证据: 近 30 天提交 `2026-07-04 feat(governance): 建立测试台账(testmap)契约与同步规则`；Guardian root 下已有 `C:/workspace/omnicompany/src/omnicompany/packages/services/_core/guardian/rules/testmap_sync.py`，其 OMNI 头说明为“Guardian 规则 OMNI-100 · testmap 同步提醒”，但 index 未提到 testmap 同步提醒/功能点-测试台账守护。
- [ ] (2026-07-04) 补充目录卫生/项目 hygiene profile 作为重要守护资产，列出 `rules/project_profile_hygiene.py`、`workers/hygiene_scan_worker.py` 以及 `.omni/hygiene-profile.yaml` 的关系。 — 证据: 近 30 天提交 `2026-06-26 feat(guardian): 新增目录清洁守卫、规则与测试`、`2026-06-19 feat(guardian): 新增项目卫生规则扫描与配置模块`；当前存在 `C:/workspace/omnicompany/src/omnicompany/packages/services/_core/guardian/rules/project_profile_hygiene.py`（读取 `.omni/hygiene-profile.yaml`）与 `C:/workspace/omnicompany/src/omnicompany/packages/services/_core/guardian/workers/hygiene_scan_worker.py`，但 index 只笼统提 `.omni` 和“周边工具”，未列项目卫生配置/目录清洁守卫这一新增守护面。
- [ ] (2026-07-04) 补入计划硬闸与项目索引体检/巡检模块的入口或权威目录，说明其在 guardian/governance 巡检链路中的职责。 — 证据: 近 30 天提交 `2026-07-04 feat(governance): 实现计划硬闸、项目索引体检与巡检模块`；index 当前 entry_points、主要目录、能做什么均未提“计划硬闸”或“项目索引体检/巡检模块”。
- [ ] (2026-07-07) 在规则能力或周边工具中补入 Python 字节码清理规则，说明其属于近期新增的目录/运行产物卫生守护能力。 — 证据: 近 30 天提交 `2026-07-01 feat(guardian): 新增 Python 字节码清理规则`；index 当前只概括“周边工具: 僵尸进程扫描、架构地图校验、Format/Router 描述质量报告”，未提 Python 字节码清理规则。
- [ ] (2026-08-06) 将 `C:/workspace/omnicompany/docs/archmap.yaml` 补入 entry_points 或权威链接，说明它是 Guardian 合法 drawer/archmap 规则（如 OMNI-014/015/021）的唯一权威，近期已登记 `src/omnicompany/desktop` drawer。 — 证据: 近 30 天提交摘要含 `2026-08-03 chore(governance): register desktop drawer`；权威文件 `C:/workspace/omnicompany/docs/archmap.yaml` 写明 `OmniGuardian 的唯一权威 drawer 定义来源`，并在 v13 注释中记录 `Register src/omnicompany/desktop as the Windows Tauri host for Dashboard Web Core`。当前 index 只在正文笼统提到“架构地图校验”，entry_points/权威规范未列 `docs/archmap.yaml`。
- [ ] (2026-08-12) 在 Omni Guard index 的主要目录或周边工具/quick_actions 中补入 `guardian/dispatch_omission.py` 与 `omni guardian dispatch-omission`，说明其扫描 ide_events.db 中长机械工具调用且无派发动作的 trace，并输出提示性报告、不出罚单不阻断。 — 证据: 近 30 天提交摘要包含 `2026-08-10 SWARM-DISPATCH-PROACTIVITY P5: guardian dispatch-omission 该派没派巡逻(只记录不阻断)`；当前代码已有新守护资产 `C:/workspace/omnicompany/src/omnicompany/packages/services/_core/guardian/dispatch_omission.py`（文件头/模块说明为“该派没派巡逻 (SWARM-DISPATCH-PROACTIVITY P5) — 仅记录, 不阻断”），并在 `C:/workspace/omnicompany/src/omnicompany/cli/commands/guardian.py` 注册 `omni guardian dispatch-omission` 子命令。index 当前 entry_points/主要目录/能做什么/quick_actions 均未提该巡逻能力。
- [ ] (2026-08-21) 在主要目录与能做什么(及 entry_points 的 guardian.py 条目说明)补入 guardian/perf_scan.py: 确定性性能巡检 PERF-OBSERVATORY(四源+latency 快照, 按 config/perf/slo.yaml 判红黄绿/no_data, 产 offenders by_session/by_path/by_instance/by_owner 榜, 落 data/services/guardian/perf/), CLI 为 omni guardian perf scan / perf latest。 — 证据: 近30天git log 2026-08-16 feat(guardian): perf scan 确定性性能巡检+犯罪榜 (PERF-OBSERVATORY P2) 与 2026-08-17 feat(guardian): perf scan 接入资源治理三 SLO + by_owner 溯源榜 (PERF-OBSERVATORY); 代码资产 C:/workspace/omnicompany/src/omnicompany/packages/services/_core/guardian/perf_scan.py(PerfScanWorker, 约60KB, 纯确定性零LLM, 判据真源 docs/plans/dashboard/[2026-08-16]PERF-OBSERVATORY/P2-P3-口径合同.md, 阈值真源 config/perf/slo.yaml + config/perf/resource-policy.yaml), 已接 CLI: src/omnicompany/cli/commands/guardian.py:2103 起 omni guardian perf scan / perf latest; index 的 entry_points/主要目录/能做什么均未提性能巡检。
<!-- projidx-candidates:end -->
