---
omni_project: omnidashboard
name: Omni Dashboard 驾驶舱
group: omnicompany
updated: 2026-06-12
roots:
  - path: C:/workspace/omnicompany/src/omnicompany/dashboard
    note: 主目录(后端 + 前端 + VSCode 扩展)
  - path: C:/workspace/omnicompany/docs/plans/dashboard
    note: 计划类目(权威进度文档都在这)
directory_contract:
  version: 1
  status: ready
  canonical_root: C:/workspace/omnicompany/src/omnicompany/dashboard
  # 包目录整体是 omnicompany 源码（boss_sight、ccdaemon、frontend 等均 Git 跟踪），src 不写 "."，以免把在用的 .omni/tmp 套进正式区。
  roles:
    src: null
    docs: C:/workspace/omnicompany/docs/plans/dashboard
    tests: null
    input: null
    drafts: null
    runtime: null
    tmp: .omni/tmp
    archive: null
  placements:
    worktrees: null
    references: null
    publish: null
  lifecycle:
    tmp: .omni/tmp/<任务>/ 按任务分批（整个 .omni Git 忽略；现有 codex-wheel-20261007），任务结束核验后清理。
    runtime: 不在本根：dashboard（8210）与 ccdaemon（8201）的运行数据不在包内，位置未在本契约核验。
    archive: 不在独立根：计划归档在 docs/plans/dashboard/_archive，属于计划文档。
entry_points:
  - path: C:/workspace/omnicompany/src/omnicompany/dashboard/app.py
    note: dashboard 主进程入口(8210, FastAPI, 只装路由和静态资源)
  - path: C:/workspace/omnicompany/src/omnicompany/dashboard/boss_sight
    note: BOSS SIGHT 驾驶舱后端(项目工作板/计划/材料/子会话聚合)
  - path: C:/workspace/omnicompany/src/omnicompany/dashboard/ccdaemon
    note: 会话运行时独立进程(8201, 管 chat/pty 生命周期, 改代码不断会话)
  - path: C:/workspace/omnicompany/src/omnicompany/dashboard/controlplane
    note: 控制面路由/反向代理/版本总线(免重启更新的核心)
  - path: C:/workspace/omnicompany/src/omnicompany/dashboard/frontend
    note: React+Vite 前端, 构建产物挂到 static/
  - path: C:/workspace/omnicompany/src/omnicompany/dashboard/extensions/vscode-chat-sidebar
    note: VSCode 扩展, 在侧栏/标签页嵌入驾驶舱
  - path: C:/workspace/omnicompany/src/omnicompany/cli/commands/dashboard.py
    note: omni dashboard CLI(status/ui-update/ext-update/restart 等)
latest:
  - "2026-06-11 三层免重启更新上线: omni dashboard ui-update(前端构建后页面3秒自刷) / ext-update(扩展热换) / restart(只重启8210不断会话)"
  - "2026-06-07 评估原生会话 CLI+HOOK 总控方案, 见 docs/plans/dashboard/[2026-06-07]原生会话CLI-HOOK总控评估.md"
  - "2026-06-03 v2 主线收尾: 总控改走 Claude Code 本地订阅, 界面收束到驾驶舱唯一会话, 见 docs/plans/dashboard/[2026-06-03]界面迁移与报废/"
quick_actions:
  - label: 健康检查
    skill: null
    where: C:/workspace/omnicompany
    desc: venv/Scripts/omni.exe dashboard status (dashboard/ccdaemon 健康 + ui/ext 版本)
  - label: 重启dashboard
    skill: null
    where: C:/workspace/omnicompany
    desc: venv/Scripts/omni.exe dashboard restart (只重启 8210 控制面, 绝不碰 8201 会话进程)
  - label: 前端热更
    skill: null
    where: C:/workspace/omnicompany
    desc: venv/Scripts/omni.exe dashboard ui-update (npm 构建, 哈希变了所有打开页面 3 秒内自刷新)
  - label: 扩展热更
    skill: null
    where: C:/workspace/omnicompany
    desc: venv/Scripts/omni.exe dashboard ext-update (重编译 VSCode 扩展实现层, loader 5 秒内热换)
  - label: 委托Claude子worker
    skill: omni-claude-worker
    where: C:/workspace/omnicompany
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
- [ ] (2026-07-04) 在 entry_points 或主要目录补充 dashboard/chatui，说明其为收编 claudecodeui 后的统一聊天 UI/provider 入口，并标注旧手搓聊天 UI 已不再作为入口。 — 证据: 2026-06-25 提交摘要: '@ vendor: 收编上游 claudecodeui 进 dashboard/chatui'; 随后 2026-06-25/26 多条摘要显示 'dashboard 聊天入口全屏导航到 chatui'、'驾驶舱普通人用聊天统一到 chatui'、'全面剥离并删除驾驶舱手搓聊天 UI, 统一走收编 chatui'。当前 PROJECT_INDEX.md 的 entry_points/主要目录未列 C:/workspace/omnicompany/src/omnicompany/dashboard/chatui。
- [ ] (2026-07-04) 补充驾驶舱定时任务管理资产：CronView、/api/cron、/api/cron/history、/api/cron/log，以及运行历史落盘 runs/<name>/<ts>.log/jsonl 的用途。 — 证据: 2026-06-25 提交摘要: 'feat(dashboard): 定时任务管理视图 — 控制器加"定时任务"顶层视图(CronView: 11任务/LLM标注/立即跑/心跳状态) + 后端 /api/cron(GET 读 + POST run, detached 无窗口)'；同日还有 '运行历史(scheduler 记录 runs.jsonl + /api/cron/history 端点)' 与 'runs/<name>/<ts>.log + jsonl ... /api/cron/log'。当前 PROJECT_INDEX.md 未提到 CronView、/api/cron 或 runs/<name>/<ts>.log。
- [ ] (2026-07-04) 在主要目录/能力中补充 universal-capture 相关资产与入口：/surface、/resolve、/omni-capture-beacon.js、data-omni-uri/CaptureBody 契约，用于说明驾驶舱材料/页面捕获与定位能力。 — 证据: 2026-06-27 至 2026-07-01 多条提交摘要新增 universal-capture/surface resolver：'/surface /resolve endpoints + CaptureBody(omni_uri/modality/verdict) + beacon + data-omni-uri'、'/omni-capture-beacon.js'、'beacon tracks cursor element'、'beacon reports viewport content atoms'。当前 PROJECT_INDEX.md 未列 universal-capture、surface resolver 或 omni-capture-beacon.js。
- [ ] (2026-07-04) 在主要目录/能力中补充统一设计工作室/材料轨迹画布资产：frontend/src/entities/review-canvas、/review-canvas 投影、决策树/强制标签/next-step-dispatch 等入口，说明其承接审阅材料到裁决与下一步分派的链路。 — 证据: 2026-07-04 多条提交摘要集中新增统一设计工作室: '统一设计工作室一期: enforced_by契约+首棵可见决策树+留痕钩子+核查脚本'、'统一设计工作室二期后端: Material 契约链端到端+强制标签阻断+M4回填+画布投影端点'、'统一设计工作室二期前端: 材料轨迹画布+项目页默认tab+队列降待办+发起下一步最小形态'、'发起下一步·增强形态后端: POST /{id}/next-step-dispatch 经 omni dispatch route 路由决策'。相关前端路径存在 C:/workspace/omnicompany/src/omnicompany/dashboard/frontend/src/entities/review-canvas/ReviewCanvas.tsx 与 StudioDemoMount.tsx。
- [ ] (2026-07-07) 在主要目录/能做什么中补充驾驶舱审阅框架/全局审阅台资产，说明审核队列、审阅阶段、材料路径查询/复制、顶栏动作与统一设计工作室之间的关系。 — 证据: 2026-07-07 提交摘要: 'dashboard: 审阅框架平台件(用户2026-07-06指示, DEC-2026-07-06-082/083)'；2026-07-07 另有 'feat(dashboard/ui): 优化审核队列聚合折叠与交互提示'，2026-07-05 有 '审阅顶栏精简实装...'。当前 PROJECT_INDEX.md 只泛称“审阅材料反馈/材料聚合”，未列审阅框架平台件或全局审阅台作为驾驶舱核心资产。
- [ ] (2026-07-07) 在 controlplane 条目或系统能力中补充局域网访问控制、本地服务路由、设备信道/反代守护等控制面新增职责。 — 证据: 2026-07-07 提交摘要: 'feat(dashboard/control): 新增局域网访问控制与本地服务路由'；2026-06-29 也有 'feat(control-plane): 扩展设备信道、反代代理与自重启守护模块'。当前 PROJECT_INDEX.md 对 controlplane 的说明仅为'控制面路由/反向代理/版本总线'，未覆盖局域网访问控制与本地服务路由。
- [ ] (2026-08-03) 在 roots 或 entry_points 补充 C:/workspace/omnicompany/src/omnicompany/desktop 作为签名 Windows 桌面客户端/隔离 WebView shell/远程只读节点资产，并在 controlplane 说明中点名 desktop_nodes.py 与 /api/desktop-nodes。 — 证据: 2026-08-03 提交摘要连续出现 'feat(desktop): ship signed Windows release channel'、'add signed remote node transport'、'govern readonly execution node'、'add isolated webview shell'；实际新增/存在路径 C:/workspace/omnicompany/src/omnicompany/desktop/README.md、C:/workspace/omnicompany/src/omnicompany/desktop/src-tauri/tauri.conf.json，且 dashboard 侧有 C:/workspace/omnicompany/src/omnicompany/dashboard/controlplane/desktop_nodes.py 提供 /api/desktop-nodes。当前 PROJECT_INDEX.md roots/entry_points 未列 desktop 客户端或 desktop_nodes 控制面。
- [ ] (2026-08-03) 在 ccdaemon/能做什么中补充 Kiro CLI PTY 会话集成、原生 Kiro 历史会话发现与 provider runtime state 跟踪。 — 证据: 2026-08-03 提交摘要: 'feat(dashboard): integrate Kiro CLI PTY sessions'、'fix(dashboard): resolve Kiro CLI after install'、'feat(dashboard): track Kiro PTY runtime state'、'feat(dashboard): discover native Kiro sessions'；代码路径 C:/workspace/omnicompany/src/omnicompany/dashboard/ccdaemon/pty.py 和 import_routes.py 中已有 Kiro CLI/session discovery/runtime tracking 相关实现。当前 PROJECT_INDEX.md 仍只描述 Claude Code/Codex/omni-agent 会话，未提 Kiro CLI provider。
- [ ] (2026-08-03) 在 boss_sight 主要目录/能力中补充 boss_sight/material_wiki，说明其提供材料 canonical wiki HTTP 与 studio/知识面。 — 证据: 2026-08-02 提交摘要: 'feat(material): add canonical wiki HTTP and studio'、'feat(material-wiki): rebuild Vilo knowledge surface'；实际路径 C:/workspace/omnicompany/src/omnicompany/dashboard/boss_sight/material_wiki/routes.py，且 C:/workspace/omnicompany/src/omnicompany/dashboard/app.py 挂载 'omnicompany.dashboard.boss_sight.material_wiki' 的 material_wiki_router。当前 PROJECT_INDEX.md 未列 material_wiki 或 canonical wiki HTTP/studio。
- [ ] (2026-08-03) 在 controlplane/前端能力中补充 AI Learning Observatory 仪表面与 /api/ai-learning-observatory 后端入口。 — 证据: 2026-08-02 提交摘要: 'feat(ai): integrate AI Learning Observatory CLI, backend and dashboard'；实际路径 C:/workspace/omnicompany/src/omnicompany/dashboard/controlplane/ai_learning_observatory.py、C:/workspace/omnicompany/src/omnicompany/dashboard/frontend/src/api/aiLearningObservatoryClient.ts、C:/workspace/omnicompany/src/omnicompany/dashboard/frontend/src/entities/project/AiLearningObservatoryPanel.tsx。当前 PROJECT_INDEX.md 未提 AI Learning Observatory。
- [ ] (2026-08-06) 在 entry_points 或主要目录补充 dashboard 的 desktop/原生窗口客户端相关资产与能力, 说明其作为驾驶舱除 Web 与 VSCode 扩展外的桌面承载入口, 并补充 detach tabs/native windows 能力。 — 证据: 2026-08-03 至 2026-08-06 多条提交摘要显示新增桌面/原生客户端链路: 'feat(desktop): add isolated webview shell'、'feat(desktop): ship signed Windows release channel'、'feat(desktop): add auto updates and true fullscreen'、'feat(desktop): default to LAN dashboard and integrate window chrome'、'feat(dashboard): support detaching tabs into native windows'。当前 PROJECT_INDEX.md 的概况/entry_points 仍只写 dashboard 后端、前端、VSCode 扩展, 未提到 desktop/native window 客户端资产。
- [ ] (2026-08-06) 在主要目录/能做什么中补充远程节点/桌面节点资产, 标明 trusted-exec-l2、节点授权 grant、任务下发与远程一键配对是驾驶舱新的受控执行节点链路。 — 证据: 2026-08-03 至 2026-08-06 多条提交摘要集中新增 remote-node/desktop-node 能力: 'feat(desktop): govern readonly execution node'、'feat(desktop): add signed remote node transport'、'feat(remote-node): route trusted-exec-l2 end to end'、'feat(desktop-node): 一键开启所有权限 + 补上任务下发入口'、'feat(desktop-node): 远程一键配对, 节点私钥即操作凭据'。当前 PROJECT_INDEX.md 未列 remote-node/desktop-node、trusted-exec-l2、节点 grant/权限或远程配对入口。
- [ ] (2026-08-06) 在 entry_points/能力中补充 Spark config center 与 Spark WebTwin 网关入口, 标注 /spark-webtwin/ 与 /spark-play 为驾驶舱代理/嵌入的实时宿主路径。 — 证据: 2026-08-04/05/06 提交摘要显示新增 Spark 相关网关资产: 'feat: serve Spark config center through Omni gateway'、'dashboard: 把 Spark WebTwin 实时宿主挂成网关路径 /spark-webtwin/'、'dashboard: spark-webtwin 反代改走原始字节, 让上游的 gzip 能透传'、'dashboard: 给 Spark WebTwin 加别名路径 /spark-play'。当前 PROJECT_INDEX.md 未提到 Spark config center、Spark WebTwin、/spark-webtwin/ 或 /spark-play。
- [ ] (2026-08-09) 在主要目录/能做什么中补充远程节点与桌面节点控制面资产：/api/remote-nodes、/api/desktop-nodes、trusted-exec-l2、配对/授权/任务下发，并引用 controlplane/remote_nodes*.py、desktop_nodes*.py 与 cli/commands/desktop_node.py。 — 证据: 2026-08-03 至 2026-08-06 提交摘要集中出现远程/桌面节点能力: 'feat(desktop): add signed remote node transport'、'feat(desktop): govern readonly execution node'、'feat(remote-node): route trusted-exec-l2 end to end'、'feat(desktop-node): 一键开启所有权限 + 补上任务下发入口'、'feat(desktop-node): 远程一键配对, 节点私钥即操作凭据'；对应 dashboard 路由路径包括 C:/workspace/omnicompany/src/omnicompany/dashboard/controlplane/remote_nodes.py、remote_nodes_core.py、desktop_nodes.py、desktop_nodes_core.py，以及 CLI C:/workspace/omnicompany/src/omnicompany/cli/commands/desktop_node.py。当前 PROJECT_INDEX.md 只泛称 controlplane，未列远程节点/桌面节点作为入口资产。
- [ ] (2026-08-12) 在 entry_points/主要目录或 quick_actions 补充会话札记/材料画布资产：controlplane/overlay_notes.py、notes-bridge 队列桥、材料卡 freeze/unfreeze/overlay_snapshot，以及 omni canvas CLI(list/show/update/remove/viewport/focus/highlight 等)的用途。 — 证据: 2026-08-12 提交摘要连续新增 notes-bridge/overlay-notes 能力: 'feat(notes-bridge): 材料卡冻结服务端 — overlay_snapshot 单文件烘焙...'、'feat(notes-bridge): 活画布操控设施 — cb 文件队列桥... + omni canvas CLI...'、'feat(overlay-notes): 画布视图存储 + 文字材料桥...'；代码路径已存在 C:/workspace/omnicompany/src/omnicompany/dashboard/controlplane/overlay_notes.py，且 CLI 入口在 C:/workspace/omnicompany/src/omnicompany/cli/commands/canvas.py / main.py 注册 'omni canvas'。当前 PROJECT_INDEX.md 未提到 overlay_notes、notes-bridge、材料卡冻结或 omni canvas。
- [ ] (2026-08-15) 把 "BOSS SIGHT 唯一权威路线图" 的引用改为 docs/plans/dashboard/_archive/[2026-05-23]BOSS-SIGHT/master_roadmap.md，或标注该路线图已随计划目录整体归档、不再作为现行权威（同批 [2026-06-03]界面迁移与报废/ 也已迁入 _archive，index latest 里的旧路径同样失效）。 — 证据: index 正文称 "[2026-05-23]BOSS-SIGHT/master_roadmap.md 是 BOSS SIGHT 的唯一权威路线图"，路径指向 docs/plans/dashboard/[2026-05-23]BOSS-SIGHT/；但实际该目录已整体归档，master_roadmap.md 现位于 docs/plans/dashboard/_archive/[2026-05-23]BOSS-SIGHT/master_roadmap.md（_archive 列表可见 [2026-05-23]BOSS-SIGHT 及其中 master_roadmap.md，而 docs/plans/dashboard 顶层已无 [2026-05-23]BOSS-SIGHT；对 docs 目录搜 master_roadmap 仅命中归档路径）。index 引用的权威路线图路径已迁移到 _archive，属于旧路径废弃。
- [ ] (2026-08-15) 在 entry_points/controlplane 能力中补充驾驶舱托管 Workshop(:5460) 及 /bside/* 历史兼容反代：说明 BSide 工作站已被 Workshop 取代，/bside/ 仅作旧 URL 兼容，避免阅读者把 BSide 当现行产品。 — 证据: git log：2026-08-07 'dashboard: 托管 BSide 工作站(/bside/* 反代, :8340)'；2026-08-14 'feat: route legacy BSide URLs to Workshop'。app.py 现有实现：L80-82 注释 '/bside/ 是历史兼容 URL；真实产品和唯一 writer 已切到 Workshop(:5460)'（_BSIDE_UPSTREAM 默认 http://127.0.0.1:5460），L993-1013 bside_proxy 把 /bside/{path} 前缀剥离后同源反代到 Workshop，L1020 懒启动 _ensure_hosted_app('bside')。index 未列 BSide/Workshop 托管入口或该网关能力。
- [ ] (2026-08-18) 将 entry_points 的 boss_sight 条目更新为真实包路径 C:/workspace/omnicompany/src/omnicompany/packages/services/boss_sight，并标注 dashboard/boss_sight 仅为兼容 shim（新代码一律走新路径），避免读者把 shim 当权威源码。 — 证据: git log 2026-08-17: 'arch(p2): boss_sight 整包下沉 packages/services + ccdaemon 公共 facade'（同批 'merge: ARCH-RATCHET-SURGERY 激活'）。dashboard/boss_sight/__init__.py 现为纯兼容 shim，头部注释 '兼容 shim：boss_sight 已整包迁往 omnicompany.packages.services.boss_sight'（origin=arch-ratchet-surgery ts=2026-08-17 type=shim），并用 meta_path redirect 兜历史 import；ccdaemon/main.py、pty_routes.py、chat.py 等已全部直接 import omnicompany.packages.services.boss_sight.*。PROJECT_INDEX.md 的 entry_points 仍把 C:/workspace/omnicompany/src/omnicompany/dashboard/boss_sight 标为 'BOSS SIGHT 驾驶舱后端' 权威路径。
- [ ] (2026-08-18) 在 entry_points/主要目录补充 dashboard/notes-web（唯一画布前端真源，移植入仓）与 controlplane/canvas_store_service.py（dashboard 自持的 canvas-store :8212 存储服务），并标注 overlay-shell/BlockSuite 已退役、画布写入改道 builtin 存储根。 — 证据: git log 2026-08-16/17: 'arch(notes-web): 唯一画布前端真源移植入仓 + overlay-shell 彻底退役'、'arch(bridge): builtin 画布改由 dashboard 自持 canvas-store 服务 — 零依赖修正'、'feat(canvas-domain): omnidashboard 为主体的画布域架构 — 写入改道 builtin + 跨域感知/审阅'、'fix(canvas): dashboard 画布面回指唯一前端 notes-web'。实际存在 dashboard/notes-web/（notes-web.tsx、webNotesInvoke.ts 等，dist-web 构建挂进 ui-update）与 controlplane/canvas_store_service.py；app.py L744 注释 '画布 Python 桥: 透明转发 canvas-store(:8212, TS 唯一权威)。overlay-shell/BlockSuite 已退役(2026-08-17)'。PROJECT_INDEX.md 主要目录/entry_points 未列 notes-web、canvas-store(:8212) 或画布域资产。
- [ ] (2026-08-18) 在主要目录/能力中补充 game_observatory 域服务资产：packages/domains/game_observatory（域包本体）+ controlplane/game_observatory_service.py/:8213 + game_observatory_proxy.py 的 /game-observatory 与 /api/game-observatory 入口，说明其为 dashboard 托管的新域服务。 — 证据: git log 2026-08-18: 'feat(dashboard): game_observatory 域出背板(DOMAIN-AUTONOMY-P1 A2)——独立托管服务(127.0.0.1:8213)+8210 薄反代, 对外路径与行为不变(142路由指纹对拍/字节同体/上游死502隔离已冒烟)'。实际存在 controlplane/game_observatory_service.py（startup 拉起/崩后重拉/健康探测 :8213）与 controlplane/game_observatory_proxy.py（/api/game-observatory/* 与 /game-observatory/* → 127.0.0.1:8213 同路径反代），服务本体为 packages/domains/game_observatory/（service_main.py、api.py、README.md、SKILL.md，含 studio/live/ai-player/report 等路由，见 controlplane/lan_access.py 白名单）。PROJECT_INDEX.md 未列 game_observatory 域服务或 :8213。
- [ ] (2026-08-25) 在主要目录/能力中补充 CronView、/api/cron(/history//log)及运行历史落盘 runs/<name>/<ts>.log/jsonl 的用途。 — 证据: 定时任务管理资产已存在但 index 未提: frontend/src/entities/controller/CronView.tsx(定时任务视图)实存; controlplane/cron.py 定义 GET /api/cron(列任务+心跳)与 POST /api/cron/run/{name}(detached 无窗口)。
<!-- projidx-candidates:end -->
