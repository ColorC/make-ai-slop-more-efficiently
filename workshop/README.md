# Workshop 核心（公开部分）

Workshop 是与 omnicompany Dashboard 并列的协作工作台：人和 AI 在同一块画布上放卡片、连线、挂材料，再由插件把画布内容转换成别的产物。两者在 2026-08 的架构裁决里确定为**零互相依赖、共享同一套画布内核**——Dashboard 的 builtin 画布与 Workshop 服务端各自起一份画布存储实例，唯一共享物是下面这些包。

这个目录和 `packages/` 下的画布内核包，是 Workshop 里**通用、能独立读懂和运行**的部分。

## 收了什么

| 位置 | 内容 | 怎么跑 |
|---|---|---|
| `packages/canvas-store/` | 画布文档存储的唯一实现：文件布局、单调 revision、base_revision CAS、幂等 mutation、实体级冲突、原子写；带 HTTP 服务端 `server.ts` | `cd packages/canvas-store && node --experimental-strip-types --test test/*.test.mjs` |
| `packages/canvas-domain/` | 画布域契约：宿主感知、操作外部内容域的统一接口，含磁盘参考实现 | 同上 |
| `packages/write-identity/` | 写入身份解析：机器、agent 运行时、原生会话 id，写进每次画布变更的署名 | 同上 |
| `workshop/shared/schema/` | 画布 / 卡片 / 连线 / 插件四份 JSON Schema（draft 2020-12），互相引用闭合 | `cd workshop && npm install && npm test` |
| `workshop/server/src/plugins/` | 插件运行时：转换型插件跑在 QuickJS（WASM）沙箱里，只拿到注入的接口；脚本型插件跑在 Node 权限模型子进程里，只能读写清单声明的目录，墙钟 / CPU / 内存 / scratch 都有硬上限 | 同上 |
| `workshop/server/plugins-runtime/launcher.mjs` | 脚本型插件的子进程启动器 | 同上 |

画布内核包只依赖已公开的 `packages/material-core`。Dashboard 的 `canvas_store_service` 会优先使用本仓 `packages/canvas-store/server.ts`，也可以用 `OMNI_CANVAS_STORE_SERVER` 指向别处。

运行要求：Node.js 22.18 或更高（用到 TypeScript 类型剥离和权限模型 `--permission`）；CI 用 Node 24。

## 没有收什么，为什么

- **Workshop 服务端整体、`wsh` 命令行、网页应用**：入口文件把登录、内容同步、任务派发直接接在团队内部的协作平台、版本库和项目管理系统上，去掉之后跑不起来；等这些集成拆成可插拔适配器后再考虑公开。
- **画布前端应用**：主组件仍引用 Workshop 的内部共享模块和团队业务卡片，暂不单独发布。
- **业务数据、项目插件、部署配置、录制与运维工具**：属于团队私有内容。

这些部分不在公开仓里出现，也没有占位或桩代码；公开的部分自成闭包，测试在 CI 里随主仓一起跑。
