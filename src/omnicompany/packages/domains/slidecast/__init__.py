# [OMNI] origin=ai-ide domain=slidecast ts=2026-06-20T00:00:00Z type=package status=active
# [OMNI] summary="slidecast 域包根。类别 aigc-video-content:AI 自动生成'会动的 HTML 演示式讲解/说书 deck'(Slidev),可选导出带旁白视频。"
# [OMNI] why="用户 2026-06-20 立项。科普/说书走 HTML 动画演示路线(否掉 AI 生视频素材拼接)。引擎选型见 docs/reports 题目1 HTML动画演示路线报告。"
# [OMNI] tags=slidecast,aigc-video-content,slidev,domain,pipeline
"""slidecast domain —— AI 自动生成"会动的 HTML 演示式讲解/说书 deck"的家。

类别: aigc-video-content(演示 PPT 式视频内容生成)。
引擎: Slidev(选型证据见
docs/reports/题目1-科普说书讲解管线-选型对比-HTML动画演示路线-2026-06-20.md)。

边界: 管线(代码/prompt)在本 domain; 产物(decks/renders/videos)在
data/domains/slidecast(gitignore); 内容真源(选题/脚本素材)留外部;
苦力 worker 走统一 LLM 网关性价比模型。详见 DESIGN.md。
"""

from __future__ import annotations
