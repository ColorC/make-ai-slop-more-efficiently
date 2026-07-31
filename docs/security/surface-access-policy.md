# Omnicompany Surface 访问策略

生效时间：2026-07-29；2026-07-30 增补 LOFA 配对设备会话与证书分发策略。

## 目标

Omnicompany 的 LAN Web 入口不再自行模拟设备身份。Caddy 是浏览器鉴权执行点，
应用只为已配对 LOFA 设备校验不可伪造的设备令牌。体系不依赖穿透服务、VPN、
IP 白名单、浏览器指纹、claim Cookie 或重定向审批链。

## 信任域

| 信任域 | 入口 | 策略 |
| --- | --- | --- |
| 受保护操作面 | `https://10.3.43.246:12443` 的 Dashboard、Agent、ChatUI、审阅材料及控制 API | Caddy HTTPS `basic_auth` |
| LAN 兼容操作面 | `http://10.3.43.246:8210` | 仅绑定指定 LAN IP；复用 12443 的同一套路由和 `basic_auth`，不再强制跳转 |
| 受保护 Portal | `https://10.3.43.246:12444` | 同一组 Caddy HTTPS 凭据 |
| 公开分享面 | `/share/<id>/`，以及已登记的只读展示 surface | 无登录；只允许 `GET`、`HEAD`、`OPTIONS` |
| 原生自鉴权 | 精确列出的 Android 配对、注册、轮询和资源端点 | 路由自身的设备令牌/配对令牌 |
| LOFA WebView | 受保护页面和 API | 原生插件把已配对设备令牌写入 `Secure`、`HttpOnly` 会话 Cookie；Caddy `forward_auth` 校验 |
| 本机恢复面 | `http://127.0.0.1:8210` | 仅回环可达，不经过 LAN |

`http://10.3.43.246:8210` 是为受控局域网设备保留的兼容入口：Caddy 将请求转发到
12443 网关，因此鉴权、公开路由和上游分流仍只有一份权威配置，但浏览器侧不再强制
切换到内部 CA HTTPS。该入口不得映射到互联网；其 Basic Auth 传输没有 TLS 保护，
凭据必须为 Omnicompany 独占，不得复用到其他系统。

## 路由契约

1. 受保护面默认落入 Caddy 最后的 `handle`，先鉴权再反代。
2. 新增给他人直接查看的网站统一挂在 `/share/<id>/`。该命名空间天然公开，
   不需要为每个站点复制鉴权例外。
3. 存量公开展示面必须在受保护兜底之前显式登记，并限制为只读方法。
4. 写入、启动进程、Agent、终端、设备控制、审阅材料和内部 API 不得进入公开 matcher。
5. 原生端点只有在自身已验证不可伪造的设备/配对令牌时才能绕过浏览器鉴权。
6. 未登记的新根级路径落入受保护兜底；不存在的路径不得跳转到登录/审批循环。
7. `/api/healthz` 与合法 CORS 预检可在建立 LOFA 会话前访问；其他通用 API
   必须通过浏览器 Basic Auth 或已配对 LOFA 会话。
8. LOFA 会话 Cookie 只送往鉴权检查，不得转发给 Dashboard、ChatUI 或 serve-web
   上游，也不得写入访问日志。

当前公开登记：

- `/share/*`
- `/walker-game*`
- `/voxelcraft-assets*`
- `/vilo-demo*`
- `/vilo-os*`
- Game Observatory 明确列出的只读库与直播查看路径
- CA 和 APK 下载

## 迁移状态

- 1.0.49 候选 APK 已实现 HTTPS 同源、配对设备会话及带凭据的跨源请求，尚未进入
  线上 OTA。
- 在已配对设备完成 1.0.49 升级并验证前，Caddy 暂时保留旧版 LOFA 所需的
  `/api/*` 过渡放行；这不是最终策略。
- 最终切换必须移除通用 `/api/*` 匿名放行，并把 `/lofa-config.json` 纳入
  Basic Auth 或已配对 LOFA 会话。切换前后都要执行下方访问矩阵。

## 凭据与恢复

- Caddyfile 只保存 bcrypt 哈希，不保存明文密码。
- 本机恢复凭据保存在
  `C:\Users\user\codeweb\omni-access-credentials.txt`，ACL 仅允许当前
  Windows 用户访问；不得提交仓库或放进公开材料。
- 浏览器收到标准 `401` 和 `WWW-Authenticate`，不会经过应用重定向。
- 若需换密码，使用 `caddy hash-password` 生成新哈希，同时修改
  `Caddyfile.tmpl` 与当前渲染的 `Caddyfile`，验证后 reload。
- 内部设备首次访问若提示证书不受信任，从
  `http://10.3.43.246:8210/codeweb-root-CA.crt` 获取根证书并安装到该设备的
  受信任根证书库。不得要求外部访客安装内部根证书。

## 审计

Caddy 对三个 LAN 入口记录全部请求，不启用 sampling：

- `C:\Users\user\codeweb\logs\omni-12443-access.jsonl`
- `C:\Users\user\codeweb\logs\omni-12444-access.jsonl`
- `C:\Users\user\codeweb\logs\omni-8210-redirect-access.jsonl`

日志包含时间、来源 IP、方法、URI、状态、耗时和已认证用户名。`Authorization`、
Cookie 等敏感头沿用 Caddy 默认脱敏，不得开启 `log_credentials`。

日志按大小轮转并设置保留期，避免长期运行耗尽磁盘。

## 变更门禁

Caddy 配置变更顺序固定为：

1. 同步修改 `Caddyfile.tmpl` 与当前 `Caddyfile`。
2. 运行 `caddy validate --config Caddyfile --adapter caddyfile`。
3. reload。
4. 验证访问矩阵：
   - 受保护路径无凭据为 `401`，不得出现 `3xx` 审批循环；
   - 正确凭据可达真实上游；
   - 公开路径无凭据可达；
   - 公开路径写请求为 `401`；
   - 明文 `8210` 的受保护路径无凭据为 `401`、正确凭据为 `200`，且无 `Location` 跳转；
   - HTTPS `12443` 的匿名与授权访问行为保持不变；
   - 三个访问日志均产生记录，敏感凭据为 `REDACTED`。

## 边界

Basic Auth 是当前 LAN 单操作者场景的轻量方案，不提供逐人账号生命周期、单设备撤销
或企业 SSO。若未来出现多人独立身份、离职回收或 MFA 需求，应把 Caddy 的鉴权执行点
替换为 Authelia/OIDC，而不是重新启用自研设备指纹与 claim 流程。

LAN 兼容入口以可用性优先，不能提供 TLS 的窃听防护。它只允许在受控局域网使用，
不得作为跨网段、访客网络或互联网分享入口。

面向互联网的有限分享应使用独立公开域名和公开 CA 证书，只映射显式登记的只读
`/share/*` surface。公开分享域不得承载 Dashboard、LOFA 会话 Cookie、写入 API、
Agent、终端或设备控制。
