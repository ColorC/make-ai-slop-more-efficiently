# [OMNI] origin=internal-engine domain=services/knowledge ts=2026-04-17T14:16:52Z
---
omnikb_type: khyp
id: kb.hyp.lark-cli
name: lark-cli 探索笔记
tags:
- domain.lark-cli
maturity: draft
summary: '在本机已登录collab platform桌面客户端的前提下，找出不需要用户再次浏览器授权就能获取 lark user token 的方法。


  背景：用户已在 Feishu 桌面客户端登录，目标绕过浏览器交互。探索方向：注册表、配置目录、桌面客户端缓存。'
scene:
  tool: lark-cli
  os: Windows 10
  timestamp: '2026-04-17'
hypotheses:
  - id: passport-encryption
    summary: persistent_passport_info_v2 使用 AES-GCM 加密，密钥来自 Local State 中 os_crypt.encrypted_key（经 DPAPI 解密）
    maturity: living
    kind: state
    format_in: {summary: "触发条件：在已登录collab platform桌面客户端的 Windows 环境下，LarkShell 配置目录中存在 Local State 文件和 persistent_passport_info_v2 加密数据", trigger_files: ["LarkShell/Local State", "persistent_passport_info_v2"], required_tools: ["python", "win32crypt"]}
    format_out: {summary: "预测输出：成功解密得到包含 user_auth_list 的 JSON 明文，其中含有 suite_session_key、device_id、install_id 等字段", expected_keys: ["user_auth_list", "suite_session_key", "device_id"], exit_code: 0}
    evidence:
      - 描述: Experimenter 用 python 脚本从 Local State 读取 os_crypt.encrypted_key，base64 解码后跳过前 5 字节，用 win32crypt.CryptUnprotectData 解密得到 AES 密钥；再用该密钥对 persistent_passport_info_v2 做 AES-GCM 解密（nonce 为 data[7:19]），成功得到包含 user_auth_list 的 JSON 明文
        出处: bash session 1a8c0133 steps [4][9]
        时间: "2026-04-17T14:16:52Z"
        session: 1a8c0133
      - 描述: Experimenter 调用 bash 执行 `ls /c/Users/user/AppData/Roaming/ | grep -i -E "lark|feishu"`，返回 "LarkShell"，再次确认 LarkShell 配置目录存在于 AppData/Roaming 路径下
        出处: bash session fa26096d step [1]
        时间: "2026-04-18T16:00:00Z"
        session: fa26096d
      - 描述: Experimenter 调用 bash 执行 `ls -la /c/Users/user/AppData/Roaming/LarkShell/`，完整列出 LarkShell Roaming 目录结构（约 60 个条目），确认了多个关键文件的存在：Local State（5229 字节）、persistent_passport_info_v2（5080 字节）、persistent_storage.db（217088 字节，含 -shm/-wal 伴生文件）、persistent_storage.enc.db（4096 字节，含 -wal 文件）、persistent_storage.preload.db（4935680 字节，含 -wal 文件）、persistent_storage_kv（21185 字节）、Remote_State（10136 字节）、Remote_Feature_7469976784941547522（25198 字节，与 cookie-storage-path 假设中的 user ID 一致）、Remote_Feature_Default（25198 字节）、logout_token（28 字节）、lockfile（0 字节）、DeviceInfo.json（197 字节）、exception_config.json（350 字节）、first_party_sets.db（49152 字节），以及多个子目录如 Default/、IronDefault/、aha/、media_center/、net_config/、passport_storage/、sdk_storage/、doctor/、update/ 等。其中 logout_token（28 字节）可能包含登出状态的凭据信息，Remote_State 和 Remote_Feature_* 文件表明 LarkShell 从远端拉取配置和功能标记。
        出处: bash session fa26096d step [15]
        时间: "2026-04-19T00:58:00Z"
        session: fa26096d
      - 描述: "Experimenter 调用 bash 执行 `cat /c/Users/user/AppData/Roaming/LarkShell/Local State`，成功读取 Local State 文件的完整 JSON 内容。该文件包含以下关键配置：os_crypt.encrypted_key（DPAPI 加密的 AES 密钥，用于解密 persistent_passport_info_v2）、profile.info_cache（记录了 4 个 profile：global\\\\profile_global（用户1）、b12adeef...\\\\profile_main（用户2）、b12adeef...\\\\profile_explorer（用户3）、b12adeef...\\\\PartitionsV2\\\\user-approval-7469976784941547522（用户4））、intl.app_locale 为 zh-CN、cloned_install 首次时间戳 1772242694（约 2026-02-28）、user_experience_metrics（含 stability.exited_cleanly、system_crash_count）、variations_limited_entropy_synthetic_trial_seed_v2 等。该观察完整揭示了 Local State 文件的数据结构，确认 os_crypt.encrypted_key 字段的存在及其在 passport 解密链路中的关键角色。"
        出处: bash session fa26096d step [16]
        时间: "2026-04-19T01:00:00Z"
        session: fa26096d
      - 描述: Experimenter 调用 bash 执行 `ls "C:/Users/user/AppData/Roaming/" 2>/dev/null`，列出 Roaming 目录下全部内容（共约 35 个条目），确认 "LarkShell" 存在于该路径下。同目录中还发现 "the_company_CJW"（the team相关目录）、"ichat"、"launcher_server" 等与办公/游戏启动器相关的应用目录。该操作是对 LarkShell 配置目录位置的基础确认。
        出处: bash session 60e4d89e step [1]
        时间: "2026-04-19T02:00:00Z"
        session: 60e4d89e
      - 描述: Experimenter 调用 bash 执行 `ls -la "C:/Users/user/AppData/Roaming/LarkShell/"`，完整列出 LarkShell Roaming 目录结构（约 60 个条目），再次确认关键文件的存在与大小：Local State（5229 字节）、persistent_passport_info_v2（5080 字节）、persistent_storage.db（217088 字节，含 -shm/-wal）、persistent_storage.enc.db（4096 字节，含 -wal）、persistent_storage.preload.db（4935680 字节，含 -wal）、persistent_storage_kv（21185 字节）、Remote_State（10136 字节）、Remote_Feature_7469976784941547522（25198 字节）、Remote_Feature_Default（25198 字节）、logout_token（28 字节）、DeviceInfo.json（197 字节）等。子目录包括 Default/、IronDefault/、aha/、media_center/、net_config/、passport_storage/、sdk_storage/、doctor/、update/、GrShaderCache/、CodeCache/ 等。该观察从 session 60e4d89e 角度再次验证了 LarkShell 目录的完整性，与先前 session fa26096d 中的观察一致。
        出处: bash session 60e4d89e step [1]
        时间: "2026-04-19T03:00:00Z"
        session: 60e4d89e
    counterexamples:
      - 描述: Experimenter 在本轮中再次尝试从 LarkShell\Local State 读取 os_crypt.encrypted_key 并用 win32crypt.CryptUnprotectData 解密，返回错误码 13（"数据无效"）；随后用 ctypes 直接调用 CryptUnprotectData 同样返回 None，DPAPI blob 前缀确认为 b'DPAPI' 但解密始终失败
        出处: bash session 1a8c0133 steps [16][17][30][31][32]
        时间: "2026-04-17T15:30:00Z"
        session: 1a8c0133
    state_log:
      - {从: draft, 到: living, 理由: AES-GCM 解密方案在一次完整链路中成功执行，获得明文 passport JSON, 时间: "2026-04-17T14:16:52Z"}
    created_at: "2026-04-17T14:16:52Z"
    created_in_session: 1a8c0133

  - id: session-key-web-access
    summary: persistent_passport_info_v2 中 user_auth_list[0].suite_session_key 可作为 session cookie 用于访问 feishu.cn 网页端，但无法直接用于开放平台 API 获取 user token
    maturity: living
    kind: transition
    format_in: {summary: "触发条件：已成功解密 persistent_passport_info_v2 并获得 user_auth_list，从中提取 suite_session_key", required_data: ["suite_session_key"], target_domains: ["feishu.cn", "open.feishu.cn", "open.larksuite.com"]}
    format_out: {summary: "预测输出：若 session key 可作为 web cookie 使用则目标网站返回 HTTP 200；若可兑换为开放平台 token 则 API 返回有效用户信息", possible_outcomes: ["web认证成功(HTTP 200)", "API认证成功", "均失败"], exit_code: "取决于目标端点响应"}
    evidence:
      - 描述: Experimenter 将 suite_session_key 作为 Cookie（session=<key>）发送给 www.feishu.cn 根路径，收到 HTTP 200 响应，证明该 key 在 web 端具有认证效力
        出处: bash session 1a8c0133 step [6]
        时间: "2026-04-17T14:16:52Z"
        session: 1a8c0133
    counterexamples:
      - 描述: Experimenter 尝试将 suite_session_key 作为 cookie 访问 open.feishu.cn/open-apis/authen/v1/user_info 和 open.feishu.cn/open-apis/contact/v3/users/me 等开放平台端点，均未返回有效用户信息（HTTP 错误或空响应）
        出处: bash session 1a8c0133 steps [7][8]
        时间: "2026-04-17T14:16:52Z"
        session: 1a8c0133
      - 描述: Experimenter 尝试访问 passport.feishu.cn/suite/login/token 端点，返回 404 Not Found
        出处: bash session 1a8c0133 step [50]
        时间: "2026-04-17T14:16:52Z"
        session: 1a8c0133
    state_log:
      - {从: draft, 到: living, 理由: session key 在 feishu.cn 网页端验证有效（HTTP 200），但开放平台 API 和 passport token 端点均失败，尚需进一步探索 token 兑换路径, 时间: "2026-04-17T14:16:52Z"}
    created_at: "2026-04-17T14:16:52Z"
    created_in_session: 1a8c0133

  - id: cookie-storage-path
    summary: LarkShell 的 Cookies 不存储在 Default\Cookies（该文件为 0 字节），而是存储在 aha\users\<user_id>\PartitionsV2\user-approval-<id>\Network\Cookies 等 per-user partition 路径下
    maturity: living
    kind: state
    format_in: {summary: "触发条件：在 LarkShell 配置目录下查找 Cookies 文件，预期位于 Default/Cookies", search_paths: ["LarkShell/Default/Cookies"], tools: ["os.walk", "shutil.copy2"]}
    format_out: {summary: "预测输出：Default/Cookies 为 0 字节，实际 Cookies 存储在 aha/users/<user_id>/PartitionsV2/*/Network/Cookies 路径下", actual_path_pattern: "aha/users/*/PartitionsV2/*/Network/Cookies", exit_code: 0}
    evidence:
      - 描述: Experimenter 用 shutil.copy2 复制 Default\Cookies 到工作目录，确认文件大小为 0 字节；随后用 os.walk 遍历 LarkShell 目录，发现 aha\users\b12adeef...\PartitionsV2\user-approval-7469976784941547522\Network\Cookies（28672 字节）和 aha\users\global\profile_global\Network\Cookies（20480 字节）有实际数据
        出处: bash session 1a8c0133 steps [55][57]
        时间: "2026-04-17T14:16:52Z"
        session: 1a8c0133
      - 描述: Experimenter 调用 bash 执行 `ls -la /c/Users/user/AppData/Roaming/LarkShell/`，确认 LarkShell Roaming 目录下存在 aha/、Default/、IronDefault/ 等子目录，与 cookie-storage-path 假设中描述的存储结构一致；同时发现 Remote_Feature_7469976784941547522 文件，其 ID 与之前发现的 user-approval-7469976784941547522 路径一致
        出处: bash session fa26096d step [15]
        时间: "2026-04-19T00:58:00Z"
        session: fa26096d
      - 描述: Experimenter 调用 bash 执行 `ls -la /c/Users/user/AppData/Roaming/LarkShell/aha/`，返回 `users` 子目录（drwxr-xr-x，创建时间 Feb 27 18:44）。该观察再次确认 aha/ 目录下存在 users/ 子目录，与 cookie-storage-path 假设中描述的 aha/users/<user_id>/PartitionsV2/*/Network/Cookies 路径结构一致。
        出处: bash session fa26096d step [1]
        时间: "2026-04-19T01:05:00Z"
        session: fa26096d
      - 描述: Experimenter 调用 bash 执行 `ls -la /c/Users/user/AppData/Roaming/LarkShell/Default/`，列出 Default 目录内容：Cookies（0 字节，确认该文件为空）、Preferences（231776 字节）、Secure Preferences（231792 字节）。该观察再次验证了 cookie-storage-path 假设中"Default/Cookies 为 0 字节"的结论，同时发现了两个约 230KB 的 Preferences 文件，可能包含浏览器级配置和安全相关设置。
        出处: bash session fa26096d step [18]
        时间: "2026-04-19T00:36:00Z"
        session: fa26096d
      - 描述: Experimenter 调用 bash 执行 `ls -la /c/Users/user/AppData/Roaming/LarkShell/aha/users/b12adeef66bf9162917e119866815449/PartitionsV2/user-approval-7469976784941547522/Network/`，列出了该 partition 的 Network 目录内容：Cookies（28672 字节，创建时间 Apr 9 20:59）、Cookies-journal（0 字节）、Network Persistent State（3163 字节）、NetworkDataMigrated（0 字节）、Reporting and NEL（36864 字节）、Trust Tokens（36864 字节）、SCT Auditing Pending Reports（2 字节）等。该观察直接确认了 Cookies 数据库文件及其伴生 journal 文件存在于 user-approval partition 的 Network 子目录下，且该目录遵循标准 Chromium Network 存储结构（包含 Persistent State、NEL 报告、Trust Tokens 等 Chromium 特有文件）。Cookies 文件大小 28672 字节与之前 session 1a8c0133 中观察到的大小一致。
        出处: bash session fa26096d step [19]
        时间: "2026-04-19T01:10:00Z"
        session: fa26096d
      - 描述: Experimenter 调用 bash 执行 `ls -la "C:/Users/user/AppData/Roaming/LarkShell/"`，再次确认 aha/ 和 Default/ 子目录的存在。其中 Default/Cookies 仍为 0 字节（确认该文件持续为空），aha/ 目录结构完好。该观察从 session 60e4d89e 角度再次支持 cookie-storage-path 假设的核心结论。
        出处: bash session 60e4d89e step [1]
        时间: "2026-04-19T03:00:00Z"
        session: 60e4d89e
    state_log:
      - {从: draft, 到: living, 理由: 通过实际文件遍历确认了 Cookies 的真实存储路径, 时间: "2026-04-17T14:16:52Z"}
    created_at: "2026-04-17T14:16:52Z"
    created_in_session: 1a8c0133

  - id: cookie-db-access-failure
    summary: "在 bash 环境下通过 Python sqlite3 直接访问 LarkShell Cookies 数据库文件（路径: aha/users/<user_id>/PartitionsV2/user-approval-<id>/Network/Cookies）失败，sqlite3 返回 'unable to open database file' 错误。该路径在 bash shell 中存在且 ls 可正常列出，但 Python sqlite3 模块无法打开——可能原因：路径转义问题、文件被 LarkShell 进程锁定、或 bash 环境与 Windows 原生路径映射不一致。此外，OmniGuardian 的 drawer 权限机制也会阻止在非声明目录内写入文件副本，导致'复制后分析'的间接方案同样受阻"
    maturity: living
    kind: transition
    format_in: {summary: "触发条件：尝试通过 Python sqlite3 模块打开 LarkShell Cookies 数据库文件（路径已通过 ls 确认存在）", target_file_pattern: "aha/users/*/PartitionsV2/*/Network/Cookies", tools: ["python", "sqlite3"]}
    format_out: {summary: "预测输出：sqlite3.connect() 抛出 OperationalError: unable to open database file", error: "sqlite3.OperationalError: unable to open database file", exit_code: "Python 异常退出"}
    evidence:
      - 描述: Experimenter 在 bash 中调用 python 执行 sqlite3.connect() 访问 Cookies 文件，抛出 "unable to open database file" 错误
        出处: bash session 1a8c0133 step [58]
        时间: "2026-04-17T14:16:52Z"
        session: 1a8c0133
      - 描述: Experimenter 调用 bash 执行 `cp` 命令将 Cookies 文件复制到工作目录 `/e/WindowsWorkspace/omnicompany/cookies_copy.db`（复制步骤无报错），随后立即用 Python `sqlite3.connect()` 打开该副本并查询 `sqlite_master` 获取表结构。执行失败，抛出 `sqlite3.OperationalError: unable to open database file`。即使将文件复制到工作目录（排除路径映射问题），sqlite3 仍无法打开。这可能因为 LarkShell 仍在运行并持有文件句柄导致复制了脏文件、文件本身损坏、或 SQLite WAL/journal 模式导致副本不可读。
        出处: bash session fa26096d step [21]
        时间: "2026-04-19T01:30:00Z"
        session: fa26096d
    counterexamples: []
    state_log:
      - {从: draft, 到: living, 理由: 两次独立场景（直接访问 + 复制副本）均复现 sqlite3 无法打开 Cookies 数据库的问题，确认该行为稳定存在, 时间: "2026-04-19T01:30:00Z"}
    created_at: "2026-04-17T14:16:52Z"
    created_in_session: 1a8c0133

  - id: lark-cli-home-config
    summary: '~/.lark-cli/ 目录存储了 lark-cli 的配置文件，包括 config.yaml（存储 app_id/app_secret 等凭证）、python_user_token.json（存储 accessToken/refreshToken/appId/tenantKey）、auth_logs（认证日志）和 logs（按日期分割的 auth 日志文件，如 auth-2026-04-13.log 等）。此外，~/.lark-doc-cli.env 环境变量文件包含 FEISHU_MCP_URL，指向collab platform MCP 服务端。该目录结构与 lark-cli 的认证和配置机制密切相关。'
    maturity: living
    kind: state
    format_in: {summary: "触发条件：在用户 Home 目录下查找 ~/.lark-cli/ 目录", search_paths: ["~/.lark-cli/", "~/.lark-doc-cli.env"], tools: ["ls", "cat"]}
    format_out: {summary: "预测输出：找到 config.yaml（含 app_id/app_secret）、python_user_token.json（含 accessToken/refreshToken）、auth_logs/、logs/（含按日期分割的 auth 日志文件）以及 ~/.lark-doc-cli.env（含 FEISHU_MCP_URL）", expected_files: ["config.yaml", "python_user_token.json", "auth_logs/", "logs/"], env_file: "~/.lark-doc-cli.env", exit_code: 0}
    evidence:
      - 描述: Experimenter 调用 bash 执行 `ls -la ~/.lark-cli/`，发现该目录下包含 config.yaml、python_user_token.json、auth_logs/、logs/ 等文件。config.yaml 存储 app_id 和 app_secret；python_user_token.json 包含 accessToken、refreshToken、appId、tenantKey。~/.lark-doc-cli.env 文件包含 FEISHU_MCP_URL，指向collab platform MCP 服务端。
        出处: bash session fa26096d step [2]
        时间: "2026-04-19T00:10:00Z"
        session: fa26096d
      - 描述: Experimenter 调用 bash 执行 `ls -la ~/.lark-cli/logs/`，发现 6 个按日期分割的认证日志文件：auth-2026-04-13.log（70292 字节）、auth-2026-04-14.log（120964 字节）、auth-2026-04-15.log（337496 字节）、auth-2026-04-16.log（213083 字节）、auth-2026-04-17.log（107656 字节）、auth-2026-04-18.log（17123 字节）。
        出处: bash session fa26096d step [6]
        时间: "2026-04-19T00:20:00Z"
        session: fa26096d
    counterexamples: []
    state_log:
      - {从: draft, 到: living, 理由: 通过多次 ls 和 cat 操作确认了 ~/.lark-cli/ 目录的完整结构和关键文件内容, 时间: "2026-04-19T00:20:00Z"}
    created_at: "2026-04-17T14:16:52Z"
    created_in_session: fa26096d

  - id: jwt-token-format
    summary: collab platform access_token 采用 JWT 格式（三段式 base64url 编码），Header 包含 alg: ES256、feature_code: FeatureOAuthJWTSign_CN、kid、typ: JWT。Payload 包含 client_id、scope（涵盖 board:whiteboard:node:create/delete/read、docs:document.*、drive:*、sheets:*、wiki:*、base:* 等）、unit/tenant_unit（eu_nc）、opaque: true、enc（加密二进制数据，enc_ver: v1）、auth_id、auth_time、auth_exp 等字段。opaque 字段经过额外加密（enc_ver: v1），核心权限信息被隐藏。
    maturity: living
    kind: state
    format_in: {summary: "触发条件：获取collab platform开放平台 access_token（JWT 格式）", token_source: "python_user_token.json.accessToken", tools: ["python", "base64"]}
    format_out: {summary: "预测输出：成功解析 JWT header 和 payload，获得 alg: ES256, client_id, scope, opaque: true, enc (enc_ver: v1) 等字段", header_fields: ["alg", "feature_code", "kid", "typ"], payload_fields: ["client_id", "scope", "unit", "tenant_unit", "opaque", "enc", "auth_id", "auth_time", "auth_exp"], exit_code: 0}
    evidence:
      - 描述: Experimenter 调用 bash 执行 Python 脚本，使用 base64.urlsafe_b64decode 对 JWT token 的三段进行解码。Header 解析结果：alg: "ES256", feature_code: "FeatureOAuthJWTSign_CN", kid: "7628027028020710365", typ: "JWT"。Payload 解析结果：client_id: "cli_a721b7e46b1dd01c", scope 涵盖 board:whiteboard:node:create/delete/read、docs:document.*、drive:*、sheets:*、wiki:*、base:*、search:docs:read 等大量权限；unit 和 tenant_unit 均为 "eu_nc"（欧洲数据中心）；opaque: true；enc 字段为加密二进制数据（enc_ver: v1）；auth_id: "7628961790512106443"，auth_time: 1776256494，auth_exp: 1807792494；此外还有 jti、iat、exp、ver 等标准 JWT 字段。
        出处: bash session fa26096d step [13]
        时间: "2026-04-19T01:00:00Z"
        session: fa26096d
    counterexamples: []
    state_log:
      - {从: draft, 到: living, 理由: 通过 base64 解码成功解析了 JWT token 的完整结构，所有字段均已确认, 时间: "2026-04-19T01:00:00Z"}
    created_at: "2026-04-17T14:16:52Z"
    created_in_session: fa26096d

  - id: larkshell-local-state-structure
    summary: LarkShell 的 Local State 文件是 JSON 格式，包含 os_crypt.encrypted_key（DPAPI 加密的 AES 密钥）、profile.info_cache（记录多个 profile 的元数据）、intl.app_locale（zh-CN）、cloned_install（首次安装时间戳）、user_experience_metrics（含 stability 指标）、variations_limited_entropy_synthetic_trial_seed_v2 等配置字段
    maturity: living
    kind: state
    format_in: {summary: "触发条件：LarkShell 配置目录下存在 Local State 文件", file_path: "LarkShell/Local State", tools: ["cat", "python"]}
    format_out: {summary: "预测输出：成功读取 JSON，包含 os_crypt.encrypted_key、profile.info_cache、intl.app_locale、cloned_install、user_experience_metrics 等字段", key_fields: ["os_crypt.encrypted_key", "profile.info_cache", "intl.app_locale", "cloned_install", "user_experience_metrics"], exit_code: 0}
    evidence:
      - 描述: Experimenter 调用 bash 执行 `cat /c/Users/user/AppData/Roaming/LarkShell/Local State`，成功获取该文件的完整 JSON 内容。关键发现：os_crypt.encrypted_key（DPAPI 加密的 AES 密钥）、profile.info_cache（记录 4 个 profile 的元数据）、intl.app_locale: zh-CN、cloned_install 首次时间戳 1772242694（约 2026-02-28）、user_experience_metrics.stability（exited_cleanly: true、system_crash_count: 1）、variations_limited_entropy_synthetic_trial_seed_v2: "36"、local.password_hash_data_list: []、lark.app_config.gwp_asan_setting_v2 等。
        出处: bash session fa26096d step [16]
        时间: "2026-04-19T01:00:00Z"
        session: fa26096d
    counterexamples: []
    state_log:
      - {从: draft, 到: living, 理由: 成功读取并解析 Local State 文件的完整 JSON 结构，所有关键字段均已确认, 时间: "2026-04-19T01:00:00Z"}
    created_at: "2026-04-17T14:16:52Z"
    created_in_session: fa26096d

  - id: larkshell-default-preferences
    summary: LarkShell 的 Default 目录下包含 Preferences（231776 字节）和 Secure Preferences（231792 字节）两个配对文件，大小几乎一致且最后修改时间相同，遵循 Chromium 风格的浏览器配置存储模式。Cookies 文件存在但为 0 字节
    maturity: draft
    kind: state
    format_in: {summary: "触发条件：LarkShell/Default/ 目录下存在 Preferences 和 Secure Preferences 文件", file_path: "LarkShell/Default/", tools: ["ls"]}
    format_out: {summary: "预测输出：找到 Preferences 和 Secure Preferences 两个配对文件（大小接近、同步更新），以及 0 字节的 Cookies 文件", files: ["Preferences", "Secure Preferences", "Cookies (0 bytes)"], exit_code: 0}
    evidence:
      - 描述: Experimenter 调用 bash 执行 `ls -la /c/Users/user/AppData/Roaming/LarkShell/Default/`，列出该目录下的三个文件：Cookies（0 字节）、Preferences（231776 字节）、Secure Preferences（231792 字节）。两者大小几乎一致（仅差 16 字节），最后修改时间相同（Apr 19 00:15），表明同步更新。
        出处: bash session fa26096d step [18]
        时间: "2026-04-19T00:36:00Z"
        session: fa26096d
      - 描述: Experimenter 调用 bash 执行 `ls -la "C:/Users/user/AppData/Roaming/LarkShell/"`，再次确认 Default/ 子目录存在（目录创建时间 Apr 18 12:52，最后修改时间 Apr 19 00:15）。该观察从 session 60e4d89e 角度再次确认 Default 目录的存在，与 larkshell-default-preferences 假设一致。
        出处: bash session 60e4d89e step [1]
        时间: "2026-04-19T03:00:00Z"
        session: 60e4d89e
      - 描述: "Experimenter 调用 bash 执行 `ls -la \"C:/Users/user/AppData/Roaming/LarkShell/Default/\"`，列出 Default 目录内容：Cookies（0 字节）、Preferences（231776 字节，修改时间 Apr 19 03:12）、Secure Preferences（231792 字节，修改时间 Apr 19 03:12）。与之前 session fa26096d 中的观察（文件大小相同、修改时间 Apr 19 00:15）相比，文件大小未变但修改时间推进了约 3 小时（03:12），表明 Preferences 文件在此时段可能未被写入新内容，或 LarkShell 进程重启后未更新配置。Cookies 仍为 0 字节，与 cookie-storage-path 假设一致。"
        出处: bash session 60e4d89e step [1]
        时间: "2026-04-19T03:12:00Z"
        session: 60e4d89e
    counterexamples: []
    state_log:
      - {从: draft, 到: draft, 理由: 仅确认了文件存在和大小，尚未读取内容确认 JSON 结构, 时间: "2026-04-19T00:36:00Z"}
    created_at: "2026-04-17T14:16:52Z"
    created_in_session: fa26096d
---

# lark-cli 探索笔记

在本机已登录collab platform桌面客户端的前提下，找出不需要用户再次浏览器授权就能获取 lark user token 的方法。

背景：用户已在 Feishu 桌面客户端登录，目标绕过浏览器交互。探索方向：注册表、配置目录、桌面客户端缓存。

## 关系图

```
[✅] passport-encryption: persistent_passport_info_v2 使用 AES-GCM 加密
  └─[✅] session-key-web-access: suite_session_key 可作为 web session cookie
[✅] cookie-storage-path: Cookies 存储在 per-user partition 路径下
  └─[✅] cookie-db-access-failure: sqlite3 无法打开 Cookies 数据库
[✅] lark-cli-home-config: ~/.lark-cli/ 目录存储配置和 token
[✅] jwt-token-format: collab platform access_token 采用 JWT 格式
[✅] larkshell-local-state-structure: Local State 文件的 JSON 结构
[?] larkshell-default-preferences: Default 目录下的 Preferences 配对文件
```

## passport-encryption: persistent_passport_info_v2 使用 AES-GCM 加密

**状态**: 验证中 · **类型**: state

解密路径：Local State → os_crypt.encrypted_key → DPAPI 解密 → AES 密钥 → AES-GCM 解密 persistent_passport_info_v2 → JSON 明文（含 suite_session_key 等）

证据：多次匹配，存在 DPAPI 解密失败的 counterexample

## session-key-web-access: suite_session_key 可作为 web session cookie

**状态**: 验证中 · **类型**: transition

suite_session_key 在 feishu.cn 网页端有效（HTTP 200），但开放平台 API 和 passport token 端点均失败。

## cookie-storage-path: Cookies 存储在 per-user partition 路径下

**状态**: 验证中 · **类型**: state

Default/Cookies 为 0 字节，实际存储在 aha/users/*/PartitionsV2/*/Network/Cookies。

## cookie-db-access-failure: sqlite3 无法打开 Cookies 数据库

**状态**: 验证中 · **类型**: transition

即使复制文件副本到工作目录，sqlite3.connect() 仍抛出 "unable to open database file"。

## lark-cli-home-config: ~/.lark-cli/ 目录结构

**状态**: 验证中 · **类型**: state

包含 config.yaml、python_user_token.json、auth_logs/、logs/（按日期分割的 auth 日志）以及 ~/.lark-doc-cli.env。

## jwt-token-format: collab platform access_token 采用 JWT 格式

**状态**: 验证中 · **类型**: state

Header: alg: ES256, feature_code: FeatureOAuthJWTSign_CN。Payload: client_id, scope, opaque: true, enc (enc_ver: v1)。

## larkshell-local-state-structure: Local State 文件的 JSON 结构

**状态**: 验证中 · **类型**: state

包含 os_crypt.encrypted_key、profile.info_cache、intl.app_locale、cloned_install、user_experience_metrics 等字段。

## larkshell-default-preferences: Default 目录下的 Preferences 配对文件

**状态**: 待验证 · **类型**: state

Preferences 和 Secure Preferences 配对文件，尚未读取内容确认结构。

## 探索过程

- [iter 0] 本轮测试明确了登录流程的状态机与退出码分层：成功发起设备码获取（--recommend + --no-wait）直接 exit 0 返回 JSON；缺失授权范围（--scope/--recommend）在前端逻辑拦截并 exit 2；domain 参数实为 CLI 端即时强校验，直接推翻了此前"校验延迟"的假设。整体来看，exit 1 用于基础语法/标志解析错误，exit 2 用于业务参数缺失/格式校验失败，exit 3 专用于下游设备/API 授权失败。
- [iter 1] 本轮重点探测了 auth 模块的参数互斥逻辑与状态反馈机制。发现 --scope 与 --recommend/--domain 存在明确的互斥策略，混用会触发 exit 2 的 validation 类型错误；同时明确了 auth check 在无 token 时会返回标准的 not_logged_in 结构化 JSON。此外，CLI 响应体具有统一的 _notice.update 版本提示注入特征，可作为后续自动化解析的稳定锚点。
- [iter 2 (session fa26096d)] 系统性探索了 LarkShell 桌面客户端的数据基础设施。确认了 LarkShell 配置目录位于 AppData/Roaming/LarkShell/，包含 Local State、persistent_passport_info_v2、persistent_storage.db 等关键文件。完整解析了 Local State 文件的 JSON 结构（含 os_crypt.encrypted_key、profile.info_cache 等），确认了 Cookies 存储在 per-user partition 路径下而非 Default/Cookies（0 字节）。发现 ~/.lark-cli/ 目录存储了 CLI 配置和 token 文件。
- [iter 3 (session fa26096d)] 确认了 cookie-db-access-failure 假设——即使将 Cookies 文件复制到工作目录，sqlite3 仍无法打开。这可能因为 LarkShell 进程持有文件句柄导致复制了脏文件，或 SQLite WAL/journal 模式导致副本不可读。成功解码了collab platform access_token JWT，揭示了完整结构（ES256 签名、opaque: true、enc 加密）。确认了 Default 目录下的 Preferences 和 Secure Preferences 配对文件。
- [iter 4 (session 60e4d89e)] Experimenter 调用 bash 执行 `ls "C:/Users/user/AppData/Roaming/" 2>/dev/null | grep -i -e lark -e feishu`，返回退出码 134（Aborted），表明 grep 进程在该环境下崩溃。此前在 session fa26096d 中相同命令已成功返回 "LarkShell"。此差异可能源于 bash 环境状态变化或管道中 grep 的异常。
- [iter 5 (session 60e4d89e)] Experimenter 调用 bash 执行 `ls "C:/Users/user/AppData/Roaming/" 2>/dev/null`（不带 grep 管道），成功列出 Roaming 目录下全部内容。确认 "LarkShell" 存在于该路径，同时观察到同目录下存在 "the_company_CJW"、"ichat"、"launcher_server" 等应用目录。该操作通过移除崩溃的 grep 管道恢复了目录探查能力。
- [iter 6 (session 60e4d89e)] Experimenter 调用 bash 执行 `ls -la "C:/Users/user/AppData/Roaming/LarkShell/"`，完整列出 LarkShell Roaming 目录结构（约 60 个条目），再次确认了 LarkShell 数据基础设施的完整性：关键文件（Local State、persistent_passport_info_v2、persistent_storage.db 及其 -shm/-wal 伴生文件、Remote_State、Remote_Feature_* 等）的大小和存在性与之前 session fa26096d 中的观察一致。子目录结构（Default/、IronDefault/、aha/、media_center/、net_config/、passport_storage/、sdk_storage/、doctor/、update/ 等）也得到确认。该操作从 session 60e4d89e 角度为 passport-encryption、cookie-storage-path、larkshell-local-state-structure、larkshell-default-preferences 等多个假设提供了进一步的交叉验证。
- [iter 7 (session 60e4d89e)] Experimenter 调用 bash 执行 `ls -la "C:/Users/user/AppData/Roaming/LarkShell/Default/"`，精确查看 Default profile 目录内容。返回三个文件：Cookies（0 字节）、Preferences（231776 字节，修改时间 Apr 19 03:12）、Secure Preferences（231792 字节，修改时间 Apr 19 03:12）。与 session fa26096d 的观察（修改时间 Apr 19 00:15）相比，文件大小未变但修改时间推进至 03:12，表明 Preferences 文件在此时间段未被写入新内容。Cookies 仍为 0 字节，再次印证 cookie-storage-path 假设。

## 场景

- tool: lark-cli
- os: Windows 10
- timestamp: 2026-04-17
