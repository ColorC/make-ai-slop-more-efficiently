# AI Player Facility

## 1. 目的

本包让 Game Observatory 拥有可跨会话延续的 AI 玩家。AI 玩家持续认识当前游戏状态、维护任务与前沿、选择有价值的下一步、保存完整证据，并把经过反复验证的操作沉淀为分层技能。

《剑与远征：启程》承担已知真值回归。《三国：谋定天下》承担真实业务运行：账号由 AI 独立经营，登录后的游戏内发展、战斗、同盟协作和正常交流由 AI 自主完成。

## 2. 真源边界

- `ObservatoryStore` 继续保存 run、artifact、trace、evidence run 与 evidence step。
- `AIPlayerStore` 使用同一 SQLite 文件中的独立版本表和 `ai_player_*` 表，保存环境、记忆、状态图、任务、技能和会话胶囊。
- 截图、视频、UI 树和运行状态保存在现有 ArtifactStore；AI 玩家只保存可解析引用。
- 公开设计案、社群反馈和网站发布使用各自管线。玩法候选在证据闭合与审定前不能进入已确认设计事实。

## 3. 运行主循环

1. 核对游戏、构建、账号、服务器、赛季、设备和存档身份。
2. 观察当前画面、UI 树和运行状态，形成带证据的语义状态。
3. 恢复会话胶囊，先核对 pending action 的实际效果。
4. 从用户目标、覆盖缺口、未闭合边、新入口、陈旧状态、失效技能和攻略触发器生成任务。
5. 在任务依赖、信息收益、动作成本、恢复成本和账号权限内选择下一项任务。
6. 已知流程优先使用验证技能；未知部分逐步规划，每一步都进行结果验证。
7. 动作前后证据闭合成功后，写入状态、转移、任务进度和攻略现场反馈。
8. 遇到循环、连续失败、环境漂移或证据缺失时恢复并切换前沿。
9. 安全停止时追加新的会话胶囊，保留未完成任务和全部前沿。

## 4. 七类长期记忆

- `identity_environment`：游戏、构建、账号、服务器、赛季、设备和存档。
- `working`：当前观察、子目标、预算与短期判断。
- `episodic`：带时间和证据的实际游玩经历。
- `semantic`：界面、资源、规则、玩法候选及相邻关系。
- `procedural`：候选技能、已验证技能、失败与恢复流程。
- `task`：活跃任务、依赖、优先级、冷却、阻断和重访条件。
- `failure_forbidden`：循环、危险动作、错误攻略、失效技能和禁止重试条件。

记忆采用追加式版本。修正通过 `supersedes_id` 或失效记录表达，旧结论和旧证据保留。

## 5. 环境隔离

环境身份至少包含：`game_id`、`build_id`、`account_id`、`channel`、`device_target_id`。服务器、赛季、语言、分辨率和存档在适用时参与 identity hash。任何关键字段变化都会阻止旧会话胶囊直接恢复；技能可以按自身适用域降级为待复验。

运行记录不能只凭相同设备归入环境。首次引用必须由 canonical 环境元数据、匹配的游戏与构建、同环境且 hash 有效的证据文件或既有同环境认领证明身份。技能恢复链、技能验证 run、边的恢复技能、会话胶囊中的 pending evidence 与 action run 都执行相同的嵌套引用检查。

MuMu 暴露的 `127.0.0.1:16384` 与 `emulator-5554` 已确认为同一实例，设备身份使用一个 canonical target，ADB serial 只保留为连接别名。

## 6. 账号行动权限

`AccountActionPolicyV1` 对动作语义进行分类，不能依靠屏幕坐标白名单表达账号权限。

三国纯 AI 账号允许自主执行：

- 使用游戏内虚拟资源；
- 招募、培养、建设和队伍调整；
- 地图行动、战斗、任务和活动；
- 加入或退出同盟、同盟协作；
- 以 AI 玩家自身身份进行正常游戏内交流。

真实货币支付和向游戏外服务提交个人身份资料需要单独授权。命中这两类动作时，任务进入 `awaiting_authorization`，设施保存当前画面、拟执行动作、理由和重新激活条件，不向设备下发动作。

## 7. 时效攻略

攻略知识必须保存 URL、平台、作者、发布时间或更新时间、检索时间、适用游戏版本、赛季、服务器阶段、摘要和原文定位。缺少适用域时，该知识只进入候选队列。

下列事件触发检索或刷新：

- 首次进入新系统；
- 高价值且不易撤回的游戏内选择；
- 版本、赛季、服务器阶段变化；
- 同一目标连续两次失败；
- 当前知识超过自己的新鲜度期限。

攻略建议与实机结果分别保存。实机结果与攻略冲突时，攻略记录进入 `contradicted` 或 `stale`，后续决策使用新的现场证据。

## 8. 状态图与任务防循环

状态节点使用稳定区域、UI 语义、选中对象、弹窗层级和运行状态形成语义指纹。动画、倒计时和轻微像素变化作为变体；会改变操作结果的选中状态、弹窗和锁定状态保持独立。

每个状态—动作对记录尝试次数、最近结果、新证据增量和冷却时间。连续无变化或回到同一短循环达到阈值后，当前分支进入冷却，调度器恢复到已知节点并选择其他前沿。存在可达前沿时，任务表不得返回空闲。

## 9. 技能生命周期

技能层级为 L0 至 L6：单动作、视觉定位动作、短序列、业务步骤、完整玩法循环、跨玩法日程和长期经营策略。成功轨迹先形成候选；通过环境重置、视觉变体、前置不满足、执行中断、客观结果和恢复测试后才能成为首选技能。

客户端、赛季、布局或前置变化会触发技能降级。执行器完成脚本只代表步骤结束，技能成功仍由独立结果判定器确认。

## 10. 恢复与 pending action

会话胶囊保存最后确认状态、当前任务、预算、前沿、设备租约和 pending action。pending action 的效果状态只有 `unknown`、`confirmed`、`failed`。恢复流程必须重新观察并确认效果；`unknown` 状态禁止直接重放非幂等动作。

## 11. 阶段 0 边界

阶段 0 交付严格合同、环境基线、冻结验收清单和当前 ExplorationRunner 的可重复多步基线。该阶段不改变真实账号，不点击登录页，也不宣称已经完成语义状态、任务调度、技能结晶或七日经营。

唯一最终退出条件由计划目录中的 `acceptance.md` 定义：AP 11/11、P 13/13、E2E 10/10、G 12/12 全部通过，干净数据库重跑通过，独立审查为 PASS。

## 12. 阶段 0 可重复入口

- `python -m omnicompany.packages.domains.game_observatory.ai_player.baseline_cli` 运行冻结控制 fixture。命令会建立 `ObservatoryStore` 与 `AIPlayerStore`，保存环境和基准结果，重新打开数据库后逐项比对，再写结果文件。
- `python -m omnicompany.packages.domains.game_observatory.ai_player.sanguo_prelogin_seed` 读取三国环境基线、攻略 seed 和研究原文，核对三者来源 ID 与数量，保存 14 条登录前候选知识，重新打开数据库核对中文 canonical 来源 ID。该命令只处理既有文件，设备动作计数固定为 0。
- `afk_freeze_candidates.build_afk_freeze_candidates` 从既有 AFK canonical、证据数据库和原始 artifact 生成独立候选包。候选包可以通过结构验证，同时固定保持 `freeze_pass=false`，直到路线回放、中断注入、纯导航证据和人类真值签发全部完成。
- `python -m omnicompany.packages.domains.game_observatory.ai_player.route_replay` 从验收清单和 benchmark ID 自动读取候选清单及固定 SHA，再把一次 live EvidenceRoute 与候选路线精确关联。执行前写入整条路线定义哈希；评估时核对 route ID、起止状态、完整路线定义、步骤顺序、真实物理动作预算、EvidenceRun/Manifest/Step 快照、动作 run、artifact SHA 和最终 manifest 时间。评估只判定执行证据是否闭合，语义终点在独立审定前固定为 `unadjudicated`，不能直接冻结真值。
- `python -m omnicompany.packages.domains.game_observatory.ai_player.route_replay_suite` 读取一份 route ID 到 verification 路径的套件输入，要求候选清单中的路线恰好各出现一次，并拒绝跨路线复用 EvidenceRun/Step、路径逃逸以及评估过程中 acceptance 或 candidate 快照变化。套件结果同样只覆盖执行证据，固定不可冻结。
- AI-player artifact 必须在自身 metadata 中携带 `environment_id`，或已经由同一环境登记。首次登记的无环境 artifact 与任何跨环境状态、边、技能、任务、会话和攻略引用都会被拒绝。
- 可执行攻略必须同时匹配环境、构建、账号、渠道、客户端版本、赛季、服务器阶段和新鲜度窗口。缺少任一项的资料只能参与发现；已过期或与现场冲突的资料不能驱动动作。
