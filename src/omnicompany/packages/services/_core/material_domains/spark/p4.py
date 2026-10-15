"""Spark P4 artifact inspection through the existing ``demogame-p4`` wrapper."""
from __future__ import annotations

import json
import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Protocol, Sequence

from ..governance import ArtifactInspection


P4_ARTIFACT_KINDS = frozenset(
    {
        "spark-p4-code",
        "spark-p4-config-source",
        "spark-p4-config-export",
        "spark-p4-prefab",
        "spark-p4-resource",
        "spark-p4-asset-meta",
        "spark-p4-interface",
        "spark-p4-copy",
    }
)
_CL_PATTERN = re.compile(r"^CL\s+(\d+)$")


class IgameP4Runner(Protocol):
    def __call__(
        self,
        args: Sequence[str],
        *,
        cwd: Path,
    ) -> Mapping[str, Any]: ...


class SubprocessIgameP4Runner:
    """Run only ``demogame-p4`` and return its JSON envelope.

    Wrapper absence, timeouts, invalid JSON, and exit/envelope disagreement are
    converted to explicit error envelopes. No raw ``p4`` fallback exists here.

    Spark has its own P4 server in the workspace ``.p4config``.  The machine's
    global submit-gate broker belongs to a different server, so these read-only
    inspection commands deliberately suppress only that broker discovery.  A
    write command is rejected before process creation and therefore cannot use
    this runner to bypass the submit gate.
    """

    def __init__(self, *, timeout_seconds: int = 60, direct_workspace: bool = True) -> None:
        self.timeout_seconds = timeout_seconds
        self.direct_workspace = direct_workspace

    def __call__(
        self,
        args: Sequence[str],
        *,
        cwd: Path,
    ) -> Mapping[str, Any]:
        command = ["demogame-p4", *args]
        environment = os.environ.copy()
        if self.direct_workspace:
            if not _is_read_only_inspection(args):
                return _error_envelope(
                    "direct_workspace_write_forbidden",
                    "Spark 工作区直连仅允许 status、sync -n 与 changelist history",
                )
            # demogame-p4 discovers the machine-wide broker through LOCALAPPDATA.
            # Removing it for this child process keeps P4CONFIG/P4TICKETS from
            # the Spark workspace while avoiding an unrelated upstream server.
            environment.pop("LOCALAPPDATA", None)
        try:
            completed = subprocess.run(
                command,
                cwd=cwd,
                env=environment,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="strict",
                timeout=self.timeout_seconds,
                check=False,
            )
        except FileNotFoundError:
            return _error_envelope(
                "wrapper_unavailable",
                "demogame-p4 不在 PATH 中；禁止回退到裸 p4",
            )
        except subprocess.TimeoutExpired:
            return _error_envelope(
                "wrapper_timeout",
                f"demogame-p4 在 {self.timeout_seconds} 秒内没有返回",
            )
        except OSError as exc:
            return _error_envelope("wrapper_unavailable", f"无法运行 demogame-p4：{exc}")

        try:
            payload = json.loads(completed.stdout)
        except (TypeError, ValueError):
            return _error_envelope(
                "wrapper_invalid_json",
                "demogame-p4 没有返回可解析的 JSON envelope",
            )
        if not isinstance(payload, dict) or not isinstance(payload.get("ok"), bool):
            return _error_envelope(
                "wrapper_invalid_envelope",
                "demogame-p4 返回值缺少布尔字段 ok",
            )
        if completed.returncode != 0 and payload.get("ok") is True:
            return _error_envelope(
                "wrapper_exit_mismatch",
                "demogame-p4 退出码失败但 envelope 声称成功",
            )
        return payload


def _is_read_only_inspection(args: Sequence[str]) -> bool:
    values = tuple(args)
    if not values:
        return False
    if values[0] == "status":
        return True
    if values[0] == "sync":
        return "-n" in values or "--preview" in values
    return len(values) >= 2 and values[:2] == ("changelist", "history")


@dataclass(frozen=True)
class P4ArtifactCheck:
    status: str
    message: str
    inspection: ArtifactInspection
    evidence: tuple[dict[str, Any], ...]

    @property
    def verifiable(self) -> bool:
        return self.status == "verified"


class IgameP4ArtifactChecker:
    """Check a declared Spark P4 artifact against structured wrapper history."""

    def __init__(
        self,
        workspace_root: Path,
        *,
        runner: IgameP4Runner | None = None,
    ) -> None:
        self.workspace_root = Path(workspace_root).resolve()
        self.runner = runner or SubprocessIgameP4Runner()

    def check(self, artifact: Mapping[str, Any]) -> P4ArtifactCheck:
        kind = artifact.get("kind")
        if kind not in P4_ARTIFACT_KINDS:
            return self._failed("not_p4_artifact", "产物类型不属于 Spark P4 正式域")

        path, path_error = self._local_path(artifact.get("ref"))
        if path_error:
            return self._failed("invalid_local_ref", path_error)
        if path is None or not path.is_file():
            return self._failed("artifact_missing", f"本地物理文件不存在：{artifact.get('ref')}")

        revision_ref = artifact.get("revision_ref")
        if not isinstance(revision_ref, Mapping):
            return self._failed("invalid_revision_ref", "P4 产物缺少 revision_ref")
        if revision_ref.get("provider") != "perforce":
            return self._failed(
                "invalid_revision_provider",
                "Spark P4 产物的 revision_ref.provider 必须为 perforce",
            )
        depot_file = revision_ref.get("object")
        if not isinstance(depot_file, str) or not depot_file.startswith("//"):
            return self._failed(
                "invalid_depot_ref",
                "P4 revision_ref.object 必须是 // 开头的 depot 文件路径",
            )
        declared_revision = revision_ref.get("revision")
        match = _CL_PATTERN.fullmatch(declared_revision) if isinstance(declared_revision, str) else None
        if match is None:
            return self._failed(
                "invalid_p4_revision",
                "P4 revision_ref.revision 必须使用 `CL <number>`",
                depot_file=depot_file,
            )

        status_envelope = self.runner(("status", "--format", "json"), cwd=self.workspace_root)
        failure = self._runner_failure(status_envelope, depot_file)
        if failure is not None:
            return failure
        status_data = status_envelope.get("data")
        opened = status_data.get("records") if isinstance(status_data, Mapping) else None
        opened_rows = opened if isinstance(opened, list) else []
        if any(
            isinstance(item, Mapping)
            and str(item.get("depot_file") or "").casefold() == depot_file.casefold()
            for item in opened_rows
        ):
            return self._failed(
                "locally_opened",
                "目标文件在当前 P4 工作区中已打开；不能把本地未提交状态当正式产物",
                depot_file=depot_file,
            )

        sync_envelope = self.runner(
            ("sync", "-n", depot_file, "--format", "json"),
            cwd=self.workspace_root,
        )
        failure = self._runner_failure(sync_envelope, depot_file)
        if failure is not None:
            return failure
        sync_data = sync_envelope.get("data")
        changed = sync_data.get("changed") if isinstance(sync_data, Mapping) else None
        if not isinstance(changed, int):
            return self._failed(
                "sync_preview_invalid",
                "demogame-p4 sync -n 没有返回整数 changed",
                depot_file=depot_file,
            )
        if changed != 0:
            return self._failed(
                "workspace_not_at_head",
                "demogame-p4 sync -n 显示目标文件仍需同步；本地物理产物不是当前 P4 头",
                depot_file=depot_file,
            )

        envelope = self.runner(
            (
                "changelist",
                "history",
                depot_file,
                "-m",
                "1",
                "--include-files",
                "--format",
                "json",
            ),
            cwd=self.workspace_root,
        )
        failure = self._runner_failure(envelope, depot_file)
        if failure is not None:
            return failure

        data = envelope.get("data")
        changes = data.get("changes") if isinstance(data, Mapping) else None
        if not isinstance(changes, list) or not changes:
            return self._failed(
                "history_unavailable",
                "demogame-p4 没有返回该 depot 文件的已提交历史",
                depot_file=depot_file,
            )
        latest = changes[0]
        if not isinstance(latest, Mapping) or not str(latest.get("change") or "").isdigit():
            return self._failed(
                "history_invalid",
                "demogame-p4 返回的最新 changelist 结构不完整",
                depot_file=depot_file,
            )
        current_revision = f"CL {latest['change']}"
        files = latest.get("files")
        file_rows = files if isinstance(files, list) else []
        matched_file = next(
            (
                item
                for item in file_rows
                if isinstance(item, Mapping)
                and str(item.get("depot_file") or "").casefold() == depot_file.casefold()
            ),
            None,
        )
        if not isinstance(matched_file, Mapping) or not str(matched_file.get("revision") or ""):
            return self._failed(
                "file_revision_unavailable",
                "最新 changelist 没有返回目标 depot 文件及其文件修订号",
                depot_file=depot_file,
            )
        action = str(matched_file.get("action") or "").casefold()
        if action in {"delete", "move/delete", "purge"}:
            return self._failed(
                "depot_artifact_deleted",
                f"目标 depot 文件的最新动作是 {action}，不能作为当前正式产物",
                depot_file=depot_file,
            )

        evidence = (
            {
                "kind": "demogame-p4",
                "ref": (
                    f"changelist-history:{depot_file}@{current_revision}"
                    f"#file-revision={matched_file['revision']}"
                ),
            },
        )
        if current_revision != declared_revision:
            return P4ArtifactCheck(
                "stale",
                f"声明版本 {declared_revision} 已过期；P4 当前为 {current_revision}",
                ArtifactInspection(True, current_revision, True, True),
                evidence,
            )
        return P4ArtifactCheck(
            "verified",
            f"demogame-p4 已确认 {depot_file} 位于 {current_revision}",
            ArtifactInspection(True, current_revision, True, True),
            evidence,
        )

    def _local_path(self, ref: object) -> tuple[Path | None, str | None]:
        if not isinstance(ref, str) or not ref.strip():
            return None, "P4 产物 ref 必须是非空本地文件路径"
        raw = Path(ref)
        path = (raw if raw.is_absolute() else self.workspace_root / raw).resolve()
        try:
            path.relative_to(self.workspace_root)
        except ValueError:
            return None, f"P4 产物路径越出工作区：{ref}"
        return path, None

    def _runner_failure(
        self,
        envelope: Mapping[str, Any],
        depot_file: str,
    ) -> P4ArtifactCheck | None:
        if envelope.get("ok") is True:
            return None
        error = envelope.get("error") if isinstance(envelope.get("error"), Mapping) else {}
        code = str(error.get("code") or "p4_unavailable")
        raw_message = str(error.get("message") or "demogame-p4 查询失败")
        lowered = raw_message.casefold()
        status = (
            "identity_error"
            if any(marker in lowered for marker in ("doesn't exist", "unknown user", "用户不存在"))
            else code
        )
        return self._failed(
            status,
            f"P4 不可检：{code}：{raw_message}",
            depot_file=depot_file,
        )

    @staticmethod
    def _failed(
        status: str,
        message: str,
        *,
        depot_file: str | None = None,
    ) -> P4ArtifactCheck:
        evidence = (
            {
                "kind": "demogame-p4",
                "ref": f"error:{status}:{depot_file or 'unresolved'}",
            },
        )
        return P4ArtifactCheck(
            status,
            message,
            ArtifactInspection(False),
            evidence,
        )


class IgameP4ArtifactInspector:
    def __init__(self, checker: IgameP4ArtifactChecker) -> None:
        self.checker = checker

    def inspect(self, artifact: dict[str, Any]) -> ArtifactInspection:
        return self.checker.check(artifact).inspection


def _error_envelope(code: str, message: str) -> dict[str, Any]:
    return {
        "ok": False,
        "error": {"type": "demogame-p4", "code": code, "message": message},
    }
