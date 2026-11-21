# [OMNI] origin=claude-code domain=services/_focus ts=2026-06-23 type=service
# [OMNI] summary="本地目标/专注系统(whatnow)的 omni 侧 worker：自动推进本地(非 multica)任务，meego/multica 作接单+反馈渠道"
"""_focus 服务：whatnow 本地目标系统的 omni 侧工人。

用户 2026-06-23 /goal R7：把我们自己的本地任务也自动推进——作为非 multica issue,
不走 multica 包装,而走 Omnicompany 自建 team/worker 包装；meego 和 multica 作为
接单(intake)和反馈(feedback)渠道,两边都打通。

- 接单(intake): whatnow 的 /api/sync/meego + /api/sync/multica(已在 Rust 服务里,定时+打开时)。
- 推进(advance): 本模块 advance_local() —— 用 omni LLM 基础设施给本地 plan 类任务产"下一步+进展",
  写回 whatnow(进度历史 + 完成度 patch)。这是 omni 自建 worker,不经 multica。
- 反馈(feedback): feedback_channels() —— 把推进结果回写到外部渠道(multica 评论 / meego 评论)。
"""
