---
omni_project: omnidashboard
name: Omni Dashboard 驾驶舱
group: omnicompany
updated: 2026-06-12
roots:
  - path: E:/WindowsWorkspace/omnicompany/src/omnicompany/dashboard
    note: 主目录(后端 + 前端 + VSCode 扩展)
  - path: E:/WindowsWorkspace/omnicompany/docs/plans/dashboard
    note: 计划类目(权威进度文档都在这)
entry_points:
  - path: E:/WindowsWorkspace/omnicompany/src/omnicompany/dashboard/app.py
    note: dashboard 主进程入口(8210, FastAPI, 只装路由和静态资源)
  - path: E:/WindowsWorkspace/omnicompany/src/omnicompany/dashboard/boss_sight
    note: BOSS SIGHT 驾驶舱后端(项目工作板/计划/材料/子会话聚合)
  - path: E:/WindowsWorkspace/omnicompany/src/omnicompany/dashboard/ccdaemon
    note: 会话运行时独立进程(8201, 管 chat/pty 生命周期, 改代码不断会话)
  - path: E:/WindowsWorkspace/omnicompany/src/omnicompany/dashboard/controlplane
    note: 控制面路由/反向代理/版本总线(免重启更新的核心)
  - path: E:/WindowsWorkspace/omnicompany/src/omnicompany/dashboard/frontend
    note: React+Vite 前端, 构建产物挂到 static/
  - path: E:/WindowsWorkspace/omnicompany/src/omnicompany/dashboard/extensions/vscode-chat-sidebar
    note: VSCode 扩展, 在侧栏/标签页嵌入驾驶舱
  - path: E:/WindowsWorkspace/omnicompany/src/omnicompany/cli/commands/dashboard.py
    note: omni dashboard CLI(status/ui-update/ext-update/restart 等)
latest:
  - "2026-06-11 三层免重启更新上线: omni dashboard ui-update(前端构建后页面3秒自刷) / ext-update(扩展热换) / restart(只重启8210不断会话)"
  - "2026-06-07 评估原生会话 CLI+HOOK 总控方案, 见 docs/plans/dashboard/[2026-06-07]原生会话CLI-HOOK总控评估.md"
  - "2026-06-03 v2 主线收尾: 总控改走 Claude Code 本地订阅, 界面收束到驾驶舱唯一会话, 见 docs/plans/dashboard/[2026-06-03]界面迁移与报废/"
quick_actions:
  - label: 健康检查
    skill: null
    where: E:/WindowsWorkspace/omnicompany
    desc: venv/Scripts/omni.exe dashboard status (dashboard/ccdaemon 健康 + ui/ext 版本)
  - label: 重启dashboard
    skill: null
    where: E:/WindowsWorkspace/omnicompany
    desc: venv/Scripts/omni.exe dashboard restart (只重启 8210 控制面, 绝不碰 8201 会话进程)
  - label: 前端热更
    skill: null
    where: E:/WindowsWorkspace/omnicompany
    desc: venv/Scripts/omni.exe dashboard ui-update (npm 构建, 哈希变了所有打开页面 3 秒内自刷新)
  - label: 扩展热更
    skill: null
    where: E:/WindowsWorkspace/omnicompany
    desc: venv/Scripts/omni.exe dashboard ext-update (重编译 VSCode 扩展实现层, loader 5 秒内热换)
  - label: 委托Claude子worker
    skill: omni-claude-worker
    where: E:/WindowsWorkspace/omnicompany
    desc: 把调查/实现委托给受审计的 claude-code 子 worker
links:
  - label: 本地驾驶舱
    url: http://127.0.0.1:8210
---
# Omni Dashboard 驾驶舱

## 概况

omnicompany 的总控入口和观察台。人和总控 AI 在这里看全公司项目工作板、管线运行状态、
Claude Code/Codex 会话, 审阅材料反馈, 唤起总控处理决策。架构上拆成两个进程:
dashboard 控制面(8210, 可随便重启)和 ccdaemon 会话运行时(8201, 长驻不动),
改 dashboard 代码不会断正在跑的会话。

## 当前进展

最近一次大改是 2026-06-11 的三层免重启更新: 前端(ui-update)、VSCode 扩展(ext-update)、
后端(restart)三层各自独立更新, 都不打断会话。再往前是 v2 主线 12 个阶段
(2026-05-31 至 06-03 连续交付), 总控从 API key 改走 Claude Code 本地订阅,
界面收束到驾驶舱。下一步候选方向是"原生会话 CLI+HOOK 总控"(2026-06-07 评估完,
缺口约四块, 未动工)。权威进度看 docs/plans/dashboard/, 其中
[2026-05-23]BOSS-SIGHT/master_roadmap.md 是 BOSS SIGHT 的唯一权威路线图。

## 主要目录

- boss_sight: 驾驶舱后端, 项目工作板(project 注册表)、计划、材料、子会话聚合
- ccdaemon: 会话运行时独立进程, WebSocket 帧契约见其 DESIGN.md
- controlplane: 控制面路由分层、反向代理、版本总线
- frontend / static: React+Vite 前端源码和构建产物
- extensions/vscode-chat-sidebar: VSCode 侧栏扩展
- 架构权威: 本目录 DESIGN.md + ccdaemon/DESIGN.md + controlplane/DESIGN.md

## 能做什么

1. 项目工作板: omni project 注册表是首页数据源, 人和总控共用的项目入口
2. 观察会话: 看 Claude Code/Codex/omni-agent 会话, 审阅材料, 唤起总控
3. 系统可观测: 管线运行状态、Trace 拓扑、Format/Router 注册表健康度
4. VSCode 集成: 侧栏/标签页嵌入驾驶舱 UI
5. 三层免重启更新: 前端/扩展/后端各自热更, 开发迭代不断会话

## 常见展开方式

- 改前端: frontend/ 下开发, 完了 omni dashboard ui-update, 页面自刷
- 改后端: 直接改, omni dashboard restart(会话不受影响)
- 改扩展: omni dashboard ext-update, 不用重启 VSCode
- 接需求先读 docs/plans/dashboard/ 下对应计划目录, 架构问题读三份 DESIGN.md
- 排查健康: omni dashboard status 看两进程和版本 token

## 自动补漏候选(机器生成,并入正文或删除即可)
<!-- projidx-candidates:begin -->
- [ ] (2026-07-04) 在 entry_points 或主要目录补充 dashboard/chatui，说明其为收编 claudecodeui 后的统一聊天 UI/provider 入口，并标注旧手搓聊天 UI 已不再作为入口。 — 证据: 2026-06-25 提交摘要: '@ vendor: 收编上游 claudecodeui 进 dashboard/chatui'; 随后 2026-06-25/26 多条摘要显示 'dashboard 聊天入口全屏导航到 chatui'、'驾驶舱普通人用聊天统一到 chatui'、'全面剥离并删除驾驶舱手搓聊天 UI, 统一走收编 chatui'。当前 PROJECT_INDEX.md 的 entry_points/主要目录未列 E:/WindowsWorkspace/omnicompany/src/omnicompany/dashboard/chatui。
- [ ] (2026-07-04) 补充驾驶舱定时任务管理资产：CronView、/api/cron、/api/cron/history、/api/cron/log，以及运行历史落盘 runs/<name>/<ts>.log/jsonl 的用途。 — 证据: 2026-06-25 提交摘要: 'feat(dashboard): 定时任务管理视图 — 控制器加"定时任务"顶层视图(CronView: 11任务/LLM标注/立即跑/心跳状态) + 后端 /api/cron(GET 读 + POST run, detached 无窗口)'；同日还有 '运行历史(scheduler 记录 runs.jsonl + /api/cron/history 端点)' 与 'runs/<name>/<ts>.log + jsonl ... /api/cron/log'。当前 PROJECT_INDEX.md 未提到 CronView、/api/cron 或 runs/<name>/<ts>.log。
- [ ] (2026-07-04) 在主要目录/能力中补充 universal-capture 相关资产与入口：/surface、/resolve、/omni-capture-beacon.js、data-omni-uri/CaptureBody 契约，用于说明驾驶舱材料/页面捕获与定位能力。 — 证据: 2026-06-27 至 2026-07-01 多条提交摘要新增 universal-capture/surface resolver：'/surface /resolve endpoints + CaptureBody(omni_uri/modality/verdict) + beacon + data-omni-uri'、'/omni-capture-beacon.js'、'beacon tracks cursor element'、'beacon reports viewport content atoms'。当前 PROJECT_INDEX.md 未列 universal-capture、surface resolver 或 omni-capture-beacon.js。
- [ ] (2026-07-04) 在主要目录/能力中补充统一设计工作室/材料轨迹画布资产：frontend/src/entities/review-canvas、/review-canvas 投影、决策树/强制标签/next-step-dispatch 等入口，说明其承接审阅材料到裁决与下一步分派的链路。 — 证据: 2026-07-04 多条提交摘要集中新增统一设计工作室: '统一设计工作室一期: enforced_by契约+首棵可见决策树+留痕钩子+核查脚本'、'统一设计工作室二期后端: Material 契约链端到端+强制标签阻断+M4回填+画布投影端点'、'统一设计工作室二期前端: 材料轨迹画布+项目页默认tab+队列降待办+发起下一步最小形态'、'发起下一步·增强形态后端: POST /{id}/next-step-dispatch 经 omni dispatch route 路由决策'。相关前端路径存在 E:/WindowsWorkspace/omnicompany/src/omnicompany/dashboard/frontend/src/entities/review-canvas/ReviewCanvas.tsx 与 StudioDemoMount.tsx。
<!-- projidx-candidates:end -->
