# [OMNI] origin=ai-ide domain=publish ts=2026-06-15T00:00:00Z type=domain status=active
# [OMNI] summary="publish 域: 对外发布 / 知识备份。首件: TeamWorkspace 明文知识快照 → gitee teamworkspace-snapshot。"
# [OMNI] why="用户'起一个公开发布项目+统一成 omni 命令';三远端发布(github脱敏/gitlab全量/gitee全量)+ 知识备份的统一家。"
# [OMNI] tags=publish,domain,backup,release
"""publish domain —— 对外发布与知识备份管线。

当前管线:
  - publish.teamworkspace_snapshot : TeamWorkspace 明文知识 → gitee 私有仓 teamworkspace-snapshot 分支
"""

from __future__ import annotations
