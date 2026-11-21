# [OMNI] origin=ai-ide domain=publish ts=2026-06-15T00:00:00Z type=team status=active
# [OMNI] summary="publish domain 的 Team。TeamWorkspace 知识快照声明成 3 节点确定性图。"
# [OMNI] why="框架级统一:管线只能是 Team。scan(选明文)→stage(镜像+diff)→commit_push(提交/推送)。"
# [OMNI] tags=publish,team,pipeline,backup
"""publish domain Teams。"""

from __future__ import annotations

from omnicompany.protocol.anchor import TransformerSpec, TransformMethod
from omnicompany.protocol.team import (
    NodeKind,
    NodeMaturity,
    TeamEdge,
    TeamNode,
    TeamSpec,
)


def _node(nid: str, name: str, fmt_in: str, fmt_out: str, method: TransformMethod, desc: str) -> TeamNode:
    return TeamNode(
        id=nid,
        kind=NodeKind.TRANSFORMER,
        transformer=TransformerSpec(
            id=f"publish-{nid}", name=name, from_format=fmt_in, to_format=fmt_out,
            method=method, description=desc,
        ),
        maturity=NodeMaturity.GROWING,
    )


def build_teamworkspace_snapshot_pipeline() -> TeamSpec:
    """TeamWorkspace 知识快照管线: 选明文 → 镜像进 gitee 暂存克隆 → 提交并(可选)推送。"""
    nodes = [
        _node("scan", "ScanSource", "publish.snapshot_request", "publish.snapshot_manifest",
              TransformMethod.RULE, "遍历 TeamWorkspace, 选明文(排图片/构建/二进制/超大), 出清单+统计。"),
        _node("stage", "StageMirror", "publish.snapshot_manifest", "publish.snapshot_staged",
              TransformMethod.RULE, "暂存克隆对齐 gitee 分支 → 清空铺入明文 → git add -A → 算增删改。"),
        _node("commit_push", "CommitPush", "publish.snapshot_staged", "publish.snapshot_result",
              TransformMethod.RULE, "dry_run 只预览; 否则提交, push=True 推到 gitee/teamworkspace-snapshot。"),
    ]
    edges = [
        TeamEdge(source="scan", target="stage"),
        TeamEdge(source="stage", target="commit_push"),
    ]
    return TeamSpec(
        id="publish.teamworkspace_snapshot",
        name="TeamWorkspace 知识快照管线",
        description="把 TeamWorkspace 的明文知识(排图片/构建/二进制)镜像刷新到 gitee 私有仓的 teamworkspace-snapshot 分支。",
        nodes=nodes,
        edges=edges,
        entry="scan",
        tags=["domain.publish", "stage.backup"],
    )
