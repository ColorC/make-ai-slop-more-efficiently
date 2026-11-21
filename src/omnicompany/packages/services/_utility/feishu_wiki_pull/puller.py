# [OMNI] origin=omnicompany domain=utility/feishu_wiki_pull ts=2026-07-22T14:09:05Z type=router status=active
# [OMNI] summary="把 Wiki 节点按原层级导出到本地并生成逐项溯源清单"
# [OMNI] why="下载结果必须能区分成功、跳过、部分失败和不支持类型，不能用一个绿色退出码掩盖漏文档"
# [OMNI] tags=feishu,wiki,manifest,download,provenance

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .client import DownloadPayload, FeishuApiError, FeishuClient, WikiNode, safe_component


@dataclass(frozen=True)
class PullResult:
    status: str
    output_dir: Path
    manifest_path: Path
    report_path: Path
    total: int
    succeeded: int
    partial: int
    failed: int
    unsupported: int


class WikiPuller:
    def __init__(
        self,
        client: FeishuClient,
        output_dir: Path,
        *,
        overwrite: bool = False,
        include_native_docx: bool = True,
        export_timeout: float = 180.0,
    ) -> None:
        self.client = client
        self.output_dir = output_dir.resolve()
        self.overwrite = overwrite
        self.include_native_docx = include_native_docx
        self.export_timeout = export_timeout

    def pull(
        self,
        space_id: str,
        *,
        source_url: str = "",
        auth_identity: str = "bot",
        app_id: str = "",
    ) -> PullResult:
        started = _now()
        self.output_dir.mkdir(parents=True, exist_ok=True)
        (self.output_dir / "pull-failure.json").unlink(missing_ok=True)
        documents_root = self.output_dir / "documents"
        documents_root.mkdir(parents=True, exist_ok=True)
        nodes = self.client.walk_nodes(space_id)
        manifest: dict[str, Any] = {
            "schema_version": 1,
            "source": {
                "space_id": space_id,
                "space_url": source_url,
                "auth_identity": auth_identity,
                "app_id": app_id,
            },
            "tool": {
                "name": "omnicompany.feishu_wiki_pull",
                "protocol_reference": "larksuite/cli v1.0.66 @ 4e2cbea94e33913378ce515f0fccfb9b1f2f2bb9",
                "touches_lark_cli_state": False,
            },
            "started_at": started,
            "completed_at": None,
            "status": "running",
            "summary": {},
            "nodes": [],
        }
        manifest_path = self.output_dir / "manifest.json"
        report_path = self.output_dir / "pull-report.md"
        self._write_json(manifest_path, manifest)

        for node in nodes:
            record = self._pull_node(documents_root, node)
            manifest["nodes"].append(record)
            self._write_json(manifest_path, manifest)

        counts = {
            "total": len(nodes),
            "succeeded": sum(item["status"] == "success" for item in manifest["nodes"]),
            "partial": sum(item["status"] == "partial" for item in manifest["nodes"]),
            "failed": sum(item["status"] == "failed" for item in manifest["nodes"]),
            "unsupported": sum(item["status"] == "unsupported" for item in manifest["nodes"]),
        }
        status = "success" if counts["total"] > 0 and counts["succeeded"] == counts["total"] else "partial"
        if counts["total"] == 0:
            status = "empty"
        manifest["completed_at"] = _now()
        manifest["status"] = status
        manifest["summary"] = counts
        self._write_json(manifest_path, manifest)
        self._write_report(report_path, manifest)
        return PullResult(
            status=status,
            output_dir=self.output_dir,
            manifest_path=manifest_path,
            report_path=report_path,
            total=counts["total"],
            succeeded=counts["succeeded"],
            partial=counts["partial"],
            failed=counts["failed"],
            unsupported=counts["unsupported"],
        )

    def _pull_node(self, documents_root: Path, node: WikiNode) -> dict[str, Any]:
        node_dir = documents_root.joinpath(*node.folder_parts)
        node_dir.mkdir(parents=True, exist_ok=True)
        metadata = node.as_dict()
        metadata["relative_dir"] = node_dir.relative_to(self.output_dir).as_posix()
        self._write_json(node_dir / "node.json", metadata)

        record: dict[str, Any] = {**metadata, "status": "running", "artifacts": [], "errors": []}
        plans = self._artifact_plans(node)
        if not plans:
            record["status"] = "unsupported"
            record["errors"].append(
                {
                    "message": f"暂不支持 obj_type={node.obj_type!r} 的内容导出；节点元数据已保存",
                    "code": "unsupported_obj_type",
                }
            )
            return record

        for plan in plans:
            try:
                artifact = self._execute_plan(node_dir, node, plan)
                record["artifacts"].append(artifact)
            except FeishuApiError as error:
                record["errors"].append({"artifact": plan[0], **error.as_dict()})
            except OSError as error:
                record["errors"].append(
                    {"artifact": plan[0], "message": str(error), "code": "local_file_io"}
                )

        if record["artifacts"] and not record["errors"]:
            record["status"] = "success"
        elif record["artifacts"]:
            record["status"] = "partial"
        else:
            record["status"] = "failed"
        return record

    def _artifact_plans(self, node: WikiNode) -> list[tuple[str, str]]:
        if node.obj_type == "docx":
            plans = [("markdown", "markdown")]
            if self.include_native_docx:
                plans.append(("export", "docx"))
            return plans
        if node.obj_type == "doc":
            return [("export", "docx")]
        if node.obj_type == "sheet":
            return [("export", "xlsx")]
        if node.obj_type == "bitable":
            return [("export", "base")]
        if node.obj_type == "slides":
            return [("export", "pptx")]
        if node.obj_type == "file":
            return [("file", "original")]
        return []

    def _execute_plan(
        self,
        node_dir: Path,
        node: WikiNode,
        plan: tuple[str, str],
    ) -> dict[str, Any]:
        kind, file_extension = plan
        if kind == "markdown":
            payload = DownloadPayload(
                body=self.client.fetch_markdown(node.obj_token).encode("utf-8"),
                file_name="content.md",
                content_type="text/markdown; charset=utf-8",
            )
            target = node_dir / "content.md"
        elif kind == "export":
            payload = self.client.export_document(
                node.obj_token,
                node.obj_type,
                file_extension,
                timeout=self.export_timeout,
            )
            target = node_dir / f"content.{file_extension}"
        else:
            payload = self.client.download_file(node.obj_token)
            suffix = Path(payload.file_name).suffix
            if not suffix:
                suffix = Path(node.title).suffix or ".bin"
            suffix = safe_component(suffix, ".bin", max_length=16)
            if not suffix.startswith("."):
                suffix = "." + suffix
            target = node_dir / f"content{suffix}"

        skipped = target.exists() and not self.overwrite
        if not skipped:
            self._write_bytes(target, payload.body)
        current = target.read_bytes()
        return {
            "kind": kind,
            "format": file_extension,
            "relative_path": target.relative_to(self.output_dir).as_posix(),
            "source_file_name": payload.file_name,
            "content_type": payload.content_type,
            "size_bytes": len(current),
            "sha256": hashlib.sha256(current).hexdigest(),
            "skipped_existing": skipped,
        }

    @staticmethod
    def _write_bytes(path: Path, content: bytes) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(path.name + ".tmp")
        temporary.write_bytes(content)
        os.replace(temporary, path)

    @staticmethod
    def _write_json(path: Path, value: Any) -> None:
        content = (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
        WikiPuller._write_bytes(path, content)

    @staticmethod
    def _write_report(path: Path, manifest: dict[str, Any]) -> None:
        source = manifest["source"]
        summary = manifest["summary"]
        lines = [
            "# collab platform知识库拉取报告",
            "",
            f"- 空间：`{source['space_id']}`",
            f"- 来源：{source['space_url'] or '未提供'}",
            f"- 身份：`{source['auth_identity']}`",
            f"- 状态：`{manifest['status']}`",
            f"- 节点：{summary['total']}（成功 {summary['succeeded']}、部分 {summary['partial']}、失败 {summary['failed']}、不支持 {summary['unsupported']}）",
            "- 本设施未读取或写入本机 lark-cli 配置、profile 或登录 token。",
            "",
            "## 节点清单",
            "",
            "| 状态 | 类型 | 标题 | 本地目录 |",
            "|---|---|---|---|",
        ]
        for node in manifest["nodes"]:
            title = str(node["title"]).replace("|", "\\|")
            lines.append(
                f"| {node['status']} | {node['obj_type']} | {title} | `{node['relative_dir']}` |"
            )
        WikiPuller._write_bytes(path, ("\n".join(lines) + "\n").encode("utf-8"))


def write_failure_report(
    output_dir: Path,
    *,
    space_id: str,
    source_url: str,
    auth_identity: str,
    app_id: str,
    error: FeishuApiError,
) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    target = output_dir / "pull-failure.json"
    payload = {
        "schema_version": 1,
        "source": {
            "space_id": space_id,
            "space_url": source_url,
            "auth_identity": auth_identity,
            "app_id": app_id,
        },
        "failed_at": _now(),
        "error": error.as_dict(),
        "contains_secret_or_token": False,
        "touches_lark_cli_state": False,
    }
    WikiPuller._write_json(target, payload)
    return target


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
