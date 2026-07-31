# chatui — vendored 上游 claudecodeui(收编源码)

本目录是 **vendor 进仓的上游 CCUI 源码**(不是手抄/重写),当 omnidashboard 的人用聊天后端跑
(独立 node 进程 :7348,生产构建单端口同时供 SPA + API + provider)。决策见 plan D8
(`docs/plans/dashboard/[2026-06-23]聊天后端迁上游CCUI/plan.md` §9 Phase 3)。

## 上游锁定基线

| 项 | 值 |
|---|---|
| 上游仓 | github.com/siteboon/claudecodeui (origin/main) |
| 锁定 commit | **`4712431`** |
| 上次拉取基线 | `beb0a50`(2026-05-04,领先它 113 提交时收编) |
| package 名/版本 | `@cloudcli-ai/cloudcli` 1.34.0(vendor 后改名,上游叫 claudecodeui) |
| pristine 参考 worktree | `参考项目/claudecodeui-omni` @ `4712431`,分支 `omni/chat-backend`(留作升级 diff 基线,**别在那改**) |

> 升级时对 pristine `4712431` 做 diff 才能分清「上游变更」vs「我们的 omni 改动」。

## 我们叠加的 omni 改动(re-vendor 时要重新套上)

**新增(自家薄层)**
- `server/_omni-windows-hide.js` — win32 wrap child_process(spawn/exec/fork 注入 windowsHide),`server/index.js` 首个 import(本机禁前台窗口硬约束)。
- `server/omni-agent-cli.js` + `server/modules/providers/list/omni_agent/` — omni_agent provider(spawn python shim 跑 AgentNodeLoop)。
- `server/controller-cli.js` + `server/modules/providers/list/controller/` — 总控(controller)provider(复用 claude 链路 + 注入总控系统提示 + opus)。
- 各注册点(types/registry/routes/index.js/provider-capabilities/session-synchronizer)加 omni_agent + controller。
- 前端 provider 集成(类型/选择器/logo/i18n)加 omni_agent + controller。

**改动**
- `server/claude-sdk.js` — 加可选 `systemPromptAppend`(不传时 claude 行为不变,供 controller 注入总控提示)。
- 硬编码 `IS_PLATFORM=true`(`src/constants/config.ts` + `server/constants/config.js`)免登录,不依赖 .env。
- `ProtectedRoute.tsx` 去 OSS 登录/setup 分支。

**删除(剪 cruft / 不需要的)**
- 项目元数据:.github / docker / docs / 8 个译版 README / redirect-package / release 工具 / plugins.starter 子模块 / .gitmodules。
- 登录:`LoginForm.tsx` / `SetupForm.tsx`。
- taskmaster 短路为休眠(`server/routes/taskmaster.js`)。

> ⚠ Phase 3 候选裁剪项(taskmaster / browser-use / plugins / 未用的 cursor·gemini·opencode provider)
> **按用户决定保留**(provider 留作参考、browser-use 留求职反爬、plugins 留未来接口、taskmaster 休眠),re-vendor 时别误删。

## 与 ccdaemon 的分工(两栈边界)

仓里有两套"创建会话"栈,**功能不重复、都在服役**——不要再争论删哪套,也不允许发明第三套:

- **chatui(本目录,Node vendored CCUI :7348)** — web 驾驶舱的会话后端,驾驶舱"新建会话"UI 全部走这里。
- **ccdaemon(`../ccdaemon/`,Python FastAPI :8201)** — lofa 手机端的会话 API,链路:lofa app → Caddy 12443 → dashboard 8210 → `controlplane/cc_proxy.py` 透传 `/api/cc/*` → ccdaemon `/cc/*`;同时是 BOSS SIGHT 的 Python 进程内 spawn 通道(`boss_sight/captures/routes.py`、`boss_sight/services/workflow_orchestrator.py` 直接 `import ccdaemon.chat`)。**lofa + BOSS SIGHT 双依赖,不能删。**

约定:新增 AI CLI 时两边都评估——web 驾驶舱用得上,就在本包按 `server/modules/providers/README.md` 加 Node provider;lofa 手机端用得上,就同步在 ccdaemon 加 Python provider(见 `../ccdaemon/DESIGN.md` "与 chatui 的分工"一节)。

## 构建产物不入仓(换机/clone 后必跑 setup)

`dist/`(client)+ `dist-server/`(server)+ `node_modules/` 全 gitignore。fresh clone / 换机后:

```
omni cc chatui setup     # npm install + npm run build(前台,流到终端)
omni cc chatui start     # 后台 detached 起 :7348
omni cc chatui status    # built / stale / alive
omni cc chatui restart   # 改了 controller-cli.js / 重 build 后对齐运行态
```

`status.stale==true` = server 源比 dist-server 产物新,该 `npm run build:server` + `restart`。

## re-vendor(升级到上游新版)步骤

1. 在 pristine worktree `参考项目/claudecodeui-omni` 拉上游新 commit,记新 ref。
2. `git diff 4712431 <new-ref>` 看上游变更面;同时 `git diff` 本 vendor 目录 vs pristine `4712431` 导出「我们的 omni 改动」清单(即上面那份)。
3. robocopy 上游新版进本目录(**排除** `node_modules` `dist` `dist-server` `.git`),再按上面清单重剪 cruft + 重套 omni 改动(冲突处人工合)。
4. 更新本文件的「锁定 commit」+ 改动清单。
5. `omni cc chatui setup` 重建 → `omni cc chatui restart` → 真 UI 验证(provider 选择器出 omni_agent/总控、能发能回)。
