# [OMNI] origin=ai-ide domain=publish ts=2026-06-15T00:00:00Z type=format status=active
# [OMNI] summary="publish domain 的 Format 契约: 快照请求→明文清单→暂存镜像→提交结果。"
# [OMNI] why="框架级统一:节点间流动的数据声明成 Format, FORMAT_IN/OUT 才有契约。"
# [OMNI] tags=publish,format,material,backup
"""publish domain Materials。

链路: publish.snapshot_request → publish.snapshot_manifest → publish.snapshot_staged → publish.snapshot_result
"""

from __future__ import annotations

from omnicompany.protocol.format import Format, FormatRegistry


SNAPSHOT_REQUEST = Format(
    id="publish.snapshot_request",
    name="SnapshotRequest",
    description="一次 TeamWorkspace 知识快照请求。src(源根)、dry_run(只预览不提交不推)、push(提交后是否推 gitee)、max_file_mb。",
    tags=["domain.publish", "stage.request", "kind.source"],
    json_schema={
        "type": "object",
        "properties": {
            "src": {"type": "string"},
            "dry_run": {"type": ["boolean", "string"]},
            "push": {"type": ["boolean", "string"]},
            "max_file_mb": {"type": "integer"},
        },
    },
)

SNAPSHOT_MANIFEST = Format(
    id="publish.snapshot_manifest",
    name="SnapshotManifest",
    description="扫描态:选中的明文相对路径清单 + 统计(各顶层目录计数 / 排掉的图片/二进制/超大)。",
    tags=["domain.publish", "stage.scanned", "kind.internal"],
    json_schema={
        "type": "object",
        "properties": {
            "src_root": {"type": "string"},
            "files": {"type": "array", "items": {"type": "string"}},
            "stats": {"type": "object"},
            "branch": {"type": "string"},
            "remote": {"type": "string"},
            "dry_run": {"type": "boolean"},
            "push": {"type": "boolean"},
            "run_dir": {"type": "string"},
        },
        "required": ["src_root", "files", "stats"],
    },
)

SNAPSHOT_STAGED = Format(
    id="publish.snapshot_staged",
    name="SnapshotStaged",
    description="暂存态:明文已镜像进 gitee 暂存克隆并 git add -A, 算出相对远端分支的增删改 diff。",
    tags=["domain.publish", "stage.staged", "kind.internal"],
    json_schema={
        "type": "object",
        "properties": {
            "src_root": {"type": "string"},
            "staging_dir": {"type": "string"},
            "branch": {"type": "string"},
            "remote": {"type": "string"},
            "diff": {"type": "object"},
            "mirrored": {"type": "integer"},
            "dry_run": {"type": "boolean"},
            "push": {"type": "boolean"},
            "stats": {"type": "object"},
            "run_dir": {"type": "string"},
        },
        "required": ["staging_dir", "diff"],
    },
)

SNAPSHOT_RESULT = Format(
    id="publish.snapshot_result",
    name="SnapshotResult",
    description="管线 sink:快照结果。committed/pushed/sha/diff + 是 dry_run 则只是预览。",
    tags=["domain.publish", "stage.result", "kind.sink"],
    json_schema={
        "type": "object",
        "properties": {
            "committed": {"type": "boolean"},
            "pushed": {"type": "boolean"},
            "sha": {"type": "string"},
            "diff": {"type": "object"},
            "dry_run": {"type": "boolean"},
            "branch": {"type": "string"},
            "remote": {"type": "string"},
            "files_total": {"type": "integer"},
        },
    },
)


ALL_FORMATS = [SNAPSHOT_REQUEST, SNAPSHOT_MANIFEST, SNAPSHOT_STAGED, SNAPSHOT_RESULT]


def register_formats(registry: FormatRegistry) -> None:
    for fmt in ALL_FORMATS:
        if not registry.is_registered(fmt.id):
            registry.register(fmt)
