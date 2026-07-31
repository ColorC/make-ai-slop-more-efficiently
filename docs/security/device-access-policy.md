# Omnicompany 设备入站策略

> **已于 2026-07-29 停用。** 浏览器 Cookie、IP、UA 指纹和 claim 审批不再承担
> Dashboard 网关鉴权。现行权威是
> [`surface-access-policy.md`](surface-access-policy.md) 与
> `C:\Users\user\codeweb\Caddyfile.tmpl`。本文件仅保留为迁移历史，
> 不得据此重新启用 `DeviceAccessMiddleware` 或 Caddy `forward_auth`。

生效范围：Omnicompany Dashboard、ChatUI、浏览器版代码入口、AIWorkSpace Portal，以及经 Dashboard 暴露的 LOFA 控制接口。目标是让所有可操控 AI 或设备的自建入口默认拒绝未知设备，并保留本机恢复通道。

## 信任边界

- 本机回环地址 `127.0.0.1` / `::1` 始终保留为恢复入口。
- 远端访问必须同时满足：已批准设备凭据、稳定设备指纹，以及批准的网络范围。
- 浏览器首次出现时只能提交申请，不能自动加入白名单。
- 只有本机，或已持有 `approver` 凭据的管理员设备，能批准、拒绝或撤销设备。
- 审批必须人工填写并确认主机名；网站不能可靠读取浏览器所在操作系统的真实主机名。
- 浏览器凭据保存在 `HttpOnly`、`SameSite=Strict` Cookie 中，并在首次使用时绑定操作系统、浏览器家族和可用的设备型号。固定 IP 设备变更地址后重新审批；飞连设备可在已批准的 `10.0.0.0/8` 内换地址，每次漂移都会写审计记录。超出网络范围或稳定指纹变化时拒绝访问。
- Android/LOFA 原生注册只能使用预置 LOFA 设备 ID，或先进入待审批队列；未知设备不能改写当前配对记录。

## 初始允许设备

| 标识 | 身份 | 已知网络/指纹 | 权限 |
| --- | --- | --- | --- |
| `local-workstation` | 本机 `L-20260227CXLFJ` | 回环地址、`10.3.43.246` | 操作、审批 |
| `operator-pc-current` | 当前操控电脑 | 首次 `10.3.7.25`、飞连 `10.0.0.0/8`、Windows + Edge + 设备凭据 | 操作 |
| `operator-pc-secondary` | 第二台电脑 | 首次 `10.3.43.247`、飞连 `10.0.0.0/8`、Windows + Edge + 设备凭据 | 操作 |
| `lofa-tablet` | HONOR YLP-W00 平板 | `10.3.7.21` / `10.1.3.23`、Android YLP-W00 | 操作、审批 |
| `lofa-phone` | LOFA 手机 `lofa-nue7lfm4q8rh` | 历史 `10.3.101.126`，当前经 `10.3.7.25` 注册、Android | 操作、审批 |

两台电脑的真实主机名无法从历史浏览器请求中可靠取得。它们只在迁移期按已知 IP 和浏览器指纹领取一次设备凭据；拿到凭据后迁移放行自动结束，后续飞连 IP 变化不再被当成新设备。当前操控电脑已经领取凭据；第二台电脑若离开旧 IP 后仍未领取，会先进入设备码式待审批流程，由本机或管理员设备批准一次。

这里没有接入外部身份提供商。当前流程采用与 OAuth Device Authorization Grant 相同的核心模型：申请设备持有一次性 claim，管理员在另一台受信设备上批准，申请设备轮询领取不可猜测的 bearer credential。若以后有公司统一 OIDC/飞连身份 API，可以把“管理员批准”替换成 OIDC 登录，但设备凭据和指纹层仍应保留。

## 入口覆盖

- Dashboard 的全部 HTTP 与 WebSocket 请求由应用中间件验证。
- Caddy 对 `/`、`/chatui/*`、`/code` 先调用 `/api/lan-access/authorize`，避免 ChatUI 和代码服务绕过 Dashboard。
- `/device-access` 与 `/api/lan-access/*` 保持可达，用于未知设备申请和已授权管理员审批。
- CA、客户端配置和 APK 下载保持公开；它们不具备控制能力。
- LOFA 原生注册、配对和隧道端点由路由自身验证设备 ID、配对令牌和设备准入状态。
- ChatUI 和 LOFA ADB devview 只绑定回环地址，不再接受 LAN 直连。
- LOFA ws-scrcpy 只绑定 `127.0.0.1:8781`，镜像与反控只能经受保护的上层入口使用。
- AIWorkSpace Portal 后端只绑定 `127.0.0.1:47823`；LAN 入口为 `https://10.3.43.246:12444`，由同一 Caddy `forward_auth` 设备门禁保护。旧的 `http://10.3.43.246:47823` 不再可达。
- GRaid 图编辑器等 `writes_local` 子应用只允许由 Portal 在回环地址按需启动；遗留的 `0.0.0.0:47835` 直连进程已移除，独立启动默认也改为回环。

## 新设备审批

1. 新设备访问站点后打开 `/device-access`，提交用途与它自报的主机名。
2. 本机打开 `http://127.0.0.1:8210/device-access`，或管理员手机/平板通过 LOFA 打开同一页面。
3. 管理员核对页面展示的来源 IP、平台、浏览器、屏幕与设备指纹，并手工填写确认过的真实主机名。
4. 选择 `operator` 或 `approver`，并选择“固定 IP”或“飞连动态 IP”。`approver` 可以继续批准其他设备，应只授予本机和管理员手机/平板。
5. 申请设备轮询领取凭据。固定 IP 变化、飞连地址超出批准范围或稳定指纹变化时，需要再次审批；飞连范围内正常换 IP 只记录审计。

运行数据位于 `data/security/device_access.json`，审计记录位于 `data/security/device_access_audit.jsonl`。两者含设备身份与网络信息，不应提交到版本库或对外发布。

## 本机恢复

1. 优先从本机访问 `http://127.0.0.1:8210/device-access`，回环入口不依赖远端凭据。
2. 若 Caddy 配置异常，Dashboard 仍可从回环地址直接进入。
3. 若应用鉴权本身阻止启动，可临时把 `config/security/device_access_bootstrap.json` 的 `enforce` 改为 `false`，重启 Dashboard，修复设备记录后立即恢复为 `true`。
4. Caddy 改动后先运行 `caddy validate --config Caddyfile --adapter caddyfile`，验证成功再执行 reload。

## 验证基线

- 本机回环入口返回成功。
- 当前控制路径 `10.3.7.25 → https://10.3.43.246:12443 → Caddy → Dashboard` 不被迁移切换阻断。
- 未知 IP/设备访问控制入口得到 `403` 或设备申请页，且只产生 pending 记录。
- 普通 operator 不能批准设备；本机和 approver 可以。
- 伪造 `X-Forwarded-For` 的直连请求不能获得信任。
- `7348`（ChatUI）和 LOFA devview 不再监听 `0.0.0.0`。
- `47823`（AIWorkSpace Portal）、`8770`（LOFA devview）和 `8781`（ws-scrcpy）均只监听回环地址。
