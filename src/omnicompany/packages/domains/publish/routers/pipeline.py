# [OMNI] origin=ai-ide domain=publish/routers ts=2026-06-15T00:00:00Z type=router status=active
# [OMNI] summary="TeamWorkspace 知识快照三节点: ScanSource(选明文) → StageMirror(镜像+diff) → CommitPush(提交/推送)。"
# [OMNI] why="全 RULE 确定性, 无 LLM。把'收明文→镜像→git'编排成管线节点, 对外推送默认显式 dry_run 可预览。"
# [OMNI] tags=publish,router,backup,snapshot
"""publish.teamworkspace_snapshot 三 RULE 节点。

scan(选明文+统计) → stage(镜像进 gitee 暂存克隆 + diff) → commit_push(提交 + 可选推送)
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from omnicompany.protocol.anchor import Verdict, VerdictKind
from omnicompany.runtime.routing.router import Router

from .. import snapshot
from .._paths import RUNS_ROOT, STAGING_ROOT, ensure_dirs


def _truthy(v: Any) -> bool:
    return v is True or (isinstance(v, str) and v.strip().lower() in ("1", "true", "yes", "on"))


# ── 节点 1: 扫源选明文 ────────────────────────────────────────────────────
class ScanSource(Router):
    """遍历 TeamWorkspace, 选明文(排图片/构建/二进制), 出清单 + 统计。建 run_dir。"""

    DESCRIPTION = "扫源: 选明文 + 统计(排图片/构建/二进制/超大)"
    FORMAT_IN = "publish.snapshot_request"
    FORMAT_OUT = "publish.snapshot_manifest"
    REQUIRED_CONTEXT: list[str] = []

    def run(self, input_data: Any) -> Verdict:
        req = input_data if isinstance(input_data, dict) else {}
        src = str(req.get("src") or "").strip() or snapshot.DEFAULT_SRC
        src_root = Path(src)
        if not src_root.is_dir():
            return Verdict(kind=VerdictKind.FAIL, output=req,
                           diagnosis=f"源目录不存在: {src_root}")
        try:
            max_mb = int(req.get("max_file_mb") or snapshot.DEFAULT_MAX_FILE_MB)
        except (TypeError, ValueError):
            max_mb = snapshot.DEFAULT_MAX_FILE_MB

        dry_run = _truthy(req.get("dry_run"))
        push = _truthy(req.get("push"))

        files, stats = snapshot.iter_text_files(src_root, max_file_mb=max_mb)
        if not files:
            return Verdict(kind=VerdictKind.FAIL, output=req,
                           diagnosis=f"{src_root} 下没扫到任何明文文件(检查路径/黑名单)")

        ensure_dirs()
        run_dir = RUNS_ROOT / ("run_" + datetime.now().strftime("%Y-%m-%dT%H-%M-%S"))
        run_dir.mkdir(parents=True, exist_ok=True)
        (run_dir / "manifest.json").write_text(
            json.dumps({"src_root": str(src_root), "stats": stats, "files": files},
                       ensure_ascii=False, indent=2), encoding="utf-8")

        by_top = ", ".join(f"{k}:{v}" for k, v in sorted(stats["by_top"].items(), key=lambda x: -x[1])[:8])
        return Verdict(
            kind=VerdictKind.PASS,
            output={
                "src_root": str(src_root), "files": files, "stats": stats,
                "branch": snapshot.SNAPSHOT_BRANCH, "remote": snapshot.GITEE_URL,
                "dry_run": dry_run, "push": push, "run_dir": str(run_dir),
            },
            diagnosis=(f"选中 {stats['included']} 个明文文件 "
                       f"(排: 扩展名 {stats['skipped_ext']} / 二进制 {stats['skipped_binary']} / 超大 {stats['skipped_large']}); "
                       f"顶层 {by_top}"),
            granted_tags=["domain.publish", "stage.scanned"],
        )


# ── 节点 2: 镜像进暂存克隆 + 算 diff ──────────────────────────────────────
class StageMirror(Router):
    """暂存克隆对齐 gitee 分支 → 清空铺入选中明文 → git add -A → 算增删改。"""

    DESCRIPTION = "暂存: 镜像进 gitee 暂存克隆 + 算 diff"
    FORMAT_IN = "publish.snapshot_manifest"
    FORMAT_OUT = "publish.snapshot_staged"
    REQUIRED_CONTEXT = ["src_root", "files"]

    def run(self, input_data: Any) -> Verdict:
        out = input_data if isinstance(input_data, dict) else {}
        src_root = Path(out["src_root"])
        files: list[str] = out.get("files") or []
        branch = out.get("branch") or snapshot.SNAPSHOT_BRANCH
        remote = out.get("remote") or snapshot.GITEE_URL

        ensure_dirs()
        staging_dir = STAGING_ROOT / "teamworkspace-snapshot"
        try:
            align = snapshot.ensure_staging_clone(staging_dir, remote, branch)
        except snapshot.GitError as e:
            return Verdict(kind=VerdictKind.FAIL, output=out,
                           diagnosis=f"暂存克隆对齐失败(网络/鉴权?): {e}")

        try:
            mirrored = snapshot.mirror_files(staging_dir, src_root, files)
            diff = snapshot.stage_and_diff(staging_dir)
        except snapshot.GitError as e:
            return Verdict(kind=VerdictKind.FAIL, output=out, diagnosis=f"git add/diff 失败: {e}")
        except Exception as e:  # 镜像/IO 异常也干净返回, 别抛进 format_check
            import traceback
            return Verdict(kind=VerdictKind.FAIL, output=out,
                           diagnosis=f"镜像/暂存异常: {type(e).__name__}: {e}\n{traceback.format_exc()[-600:]}")

        return Verdict(
            kind=VerdictKind.PASS,
            output={**out, "staging_dir": str(staging_dir), "mirrored": mirrored, "diff": diff},
            diagnosis=(f"{align}; 铺入 {mirrored} 文件; "
                       f"增 {diff['added']} / 改 {diff['modified']} / 删 {diff['removed']}"),
            granted_tags=["domain.publish", "stage.staged"],
        )


# ── 节点 3: 提交 + 可选推送(管线 sink)──────────────────────────────────
class CommitPush(Router):
    """dry_run: 只报 diff, 回滚暂存不留提交。否则提交; push=True 推到 gitee。"""

    DESCRIPTION = "提交/推送: dry_run 只预览 · 否则提交(可推 gitee)"
    FORMAT_IN = "publish.snapshot_staged"
    FORMAT_OUT = "publish.snapshot_result"
    REQUIRED_CONTEXT = ["staging_dir", "diff"]

    def run(self, input_data: Any) -> Verdict:
        out = input_data if isinstance(input_data, dict) else {}
        staging_dir = Path(out["staging_dir"])
        branch = out.get("branch") or snapshot.SNAPSHOT_BRANCH
        diff = out.get("diff") or {}
        dry_run = bool(out.get("dry_run"))
        push = bool(out.get("push"))
        src_root = Path(out["src_root"])
        files_total = len((out.get("files") or []))

        base = {"branch": branch, "remote": out.get("remote"), "dry_run": dry_run,
                "diff": diff, "files_total": files_total}

        if diff.get("total_changes", 0) == 0:
            return Verdict(kind=VerdictKind.PASS,
                           output={**base, "committed": False, "pushed": False},
                           diagnosis="无变更 —— 快照已是最新, 不提交。",
                           granted_tags=["domain.publish", "stage.result", "kind.sink"])

        if dry_run:
            # 预览模式: 不提交, 把暂存树 reset 回去(留 .git, 不污染)
            snapshot._git(["reset", "--hard", "HEAD"], cwd=staging_dir, check=False)
            return Verdict(
                kind=VerdictKind.PASS,
                output={**base, "committed": False, "pushed": False},
                diagnosis=(f"[预览] 将 增 {diff['added']} / 改 {diff['modified']} / 删 {diff['removed']} "
                           f"(共 {files_total} 明文)。加 --push 实际提交并推 gitee/{branch}。"),
                granted_tags=["domain.publish", "stage.result", "kind.sink"],
            )

        msg = snapshot.snapshot_message(src_root)
        try:
            res = snapshot.commit_and_push(staging_dir, branch, msg, push=push)
        except snapshot.GitError as e:
            return Verdict(kind=VerdictKind.FAIL, output={**base, "committed": False},
                           diagnosis=f"提交失败: {e}")

        diag = (f"已提交 {res.get('sha', '')} (增 {diff['added']}/改 {diff['modified']}/删 {diff['removed']})")
        if push:
            diag += " · 已推送 gitee" if res.get("pushed") else f" · 推送失败: {res.get('push_error', '?')}"
        else:
            diag += " · 仅本地暂存(未推, 加 --push 才推 gitee)"
        kind = VerdictKind.PASS
        if push and not res.get("pushed"):
            kind = VerdictKind.FAIL  # 推送失败要醒目

        return Verdict(
            kind=kind,
            output={**base, **res},
            diagnosis=diag,
            granted_tags=["domain.publish", "stage.result", "kind.sink"],
        )
