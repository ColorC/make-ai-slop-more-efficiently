# [OMNI] origin=claude-code domain=services/absorption/routers.py ts=2026-04-08T12:00:00Z
# [OMNI] material_id="material:learning.absorption.legacy.v1_v2_business_logic.py"
#
# ⚠ 可能含 DEPRECATED (2026-04-18) — 继承旧 runtime.agent.agent_node_loop.AgentNodeLoop。阶段 C 会迁到 packages.services.agent.AgentNodeLoop
# 违规：LLMClient/ToolDefinition.call 直调 + 内存 list[dict] 传参（非 Format+bus）。
# 重构计划：omnifactory/docs/plans/[2026-04-18]AGENT-NODE-LOOP-ROUTERIZATION/plan.md
# 禁止基于 V1 AgentNodeLoop 新增实现；Guardian 会监控违规。
"""absorption.routers — Stage 1 Survey & Triage 真实实现 (Stage 3d 升级)。

7 节点管线, 本文件持有 6 个同步 Router:
  1. TargetIntakeRouter              — ANCHOR + HARD, 规整用户请求
  2. RepoFacadeFetcherRouter         — ANCHOR + HARD, 抓 GitHub 元数据 (L1 升级)
  3. OmnifactorySnapshotFetcherRouter — ANCHOR + HARD, 扫本仓自身能力 (L2 新增)
  4. (LandmarkPickerRouter 在 landmark_picker.py, AgentNodeLoop)
  5. CoverageAuditorRouter           — ANCHOR + HARD, 覆盖度审计 (L5 新增)
  6. TriageGateRouter                — ANCHOR + HARD, tier-1 过滤 + 落盘 pool (改写)
  7. ReportWriterRouter              — TRANSFORMER + RULE, markdown 报告 (L6 新增)

本次升级 (Stage 3d) 解决了前一轮的 7 个问题:
  L1. 数据抓取太薄 → RepoFacadeFetcher 加: 递归 tree / 全 README / 贡献者 / 近期 release
  L2. 无 OmniCompany 对照 → OmnifactorySnapshotFetcher 扫 packages/core/runtime
  L3. 无迭代读文件 → LandmarkPicker 升 AgentNodeLoop (独立文件)
  L4. 证据链薄弱 → 提交工具强制 file_path + snippet, 非读过的文件拒绝
  L5. 无完整性审计 → CoverageAuditor 比对总 tree vs 读过的文件
  L6. JSON 不可读 → ReportWriter 产 markdown 报告
  L7. confidence 不诚实 → 每个 landmark/gap/sketch 必须带 confidence + reason
"""

from __future__ import annotations

import base64
import datetime
import json
import logging
import re
import subprocess
import threading
from pathlib import Path
from typing import Any, ClassVar

from omnicompany.core.config import resolve_domain_data_dir
from omnicompany.protocol.anchor import Verdict, VerdictKind
from omnicompany.runtime.routing.router import Router

from omnicompany.packages.services._learning.absorption.snapshot import (
    build_snapshot,
    snapshot_stats,
)

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════
# 公共辅助
# ═══════════════════════════════════════════════════════════

_REPO_PATTERNS = [
    re.compile(r"^https?://github\.com/(?P<owner>[^/]+)/(?P<name>[^/]+?)(?:\.git)?(?:/.*)?$"),
    re.compile(r"^git@github\.com:(?P<owner>[^/]+)/(?P<name>[^/]+?)(?:\.git)?$"),
    re.compile(r"^(?P<owner>[^/\s]+)/(?P<name>[^/\s]+?)(?:\.git)?$"),
]


def _parse_repo(raw: str) -> tuple[str, str] | None:
    raw = raw.strip()
    for pat in _REPO_PATTERNS:
        m = pat.match(raw)
        if m:
            return m.group("owner"), m.group("name")
    return None


def _absorption_artifact_dir() -> Path:
    return resolve_domain_data_dir("absorption")


def _gh_api(path: str, timeout: int = 30) -> str:
    cmd = ["gh", "api", path]
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, encoding="utf-8", timeout=timeout,
        )
    except FileNotFoundError as e:
        raise RuntimeError("gh CLI 未安装或不在 PATH 中") from e
    except subprocess.TimeoutExpired as e:
        raise RuntimeError(f"gh api {path} 超时 ({timeout}s)") from e
    if result.returncode != 0:
        first_err = (result.stderr or "").strip().splitlines()[:1]
        raise RuntimeError(
            f"gh api {path} 失败 (rc={result.returncode}): {first_err[0] if first_err else 'unknown'}"
        )
    return result.stdout.strip()


def _gh_api_json(path: str, timeout: int = 30) -> Any:
    raw = _gh_api(path, timeout=timeout)
    if not raw:
        return None
    return json.loads(raw)


def _decode_base64_readme(b64: str) -> str:
    try:
        return base64.b64decode(b64).decode("utf-8", errors="replace")
    except Exception:
        return ""


# ═══════════════════════════════════════════════════════════
# Node 01 · TargetIntakeRouter (ANCHOR + HARD)
# ═══════════════════════════════════════════════════════════

class TargetIntakeRouter(Router):
    DESCRIPTION = (
        "解析 user_request 中的 repos (支持短名/HTTP URL/SSH URL)、"
        "校验 profile、为本次 absorption 分配全局唯一 absorption_id。"
    )
    FORMAT_IN = "absorption.user_request"
    FORMAT_OUT = "absorption.intake"

    _VALID_PROFILES = ("framework_absorption", "domain_absorption")

    def run(self, input_data: Any) -> Verdict:
        if not isinstance(input_data, dict):
            return Verdict(
                kind=VerdictKind.FAIL,
                output=input_data,
                diagnosis=f"user_request 必须是 dict，收到 {type(input_data).__name__}",
            )

        repos_raw = input_data.get("repos") or []
        profile = input_data.get("profile")
        notes = (input_data.get("notes") or "").strip()

        if not isinstance(repos_raw, list) or not repos_raw:
            return Verdict(
                kind=VerdictKind.FAIL,
                output=input_data,
                diagnosis="user_request.repos 必须是非空列表",
            )
        if profile not in self._VALID_PROFILES:
            return Verdict(
                kind=VerdictKind.FAIL,
                output=input_data,
                diagnosis=f"profile={profile!r} 不在合法枚举: {self._VALID_PROFILES}",
            )

        repos: list[dict[str, str]] = []
        invalid: list[str] = []
        for raw in repos_raw:
            if not isinstance(raw, str):
                invalid.append(repr(raw))
                continue
            parsed = _parse_repo(raw)
            if parsed is None:
                invalid.append(raw)
                continue
            owner, name = parsed
            repos.append({
                "owner": owner,
                "name": name,
                "url": f"https://github.com/{owner}/{name}",
            })

        if invalid:
            return Verdict(
                kind=VerdictKind.FAIL,
                output=input_data,
                diagnosis=f"无法解析的 repo 标识: {invalid}",
            )

        ts = datetime.datetime.utcnow().strftime("%Y%m%d-%H%M%S")
        repo_short = "_".join(r["name"][:12] for r in repos[:2])
        absorption_id = f"abs-{ts}-{repo_short}"

        state = {
            "absorption_id": absorption_id,
            "repos": repos,
            "profile": profile,
            "notes": notes,
        }
        logger.info(
            "[absorption.target_intake] id=%s repos=%d profile=%s",
            absorption_id, len(repos), profile,
        )
        return Verdict(
            kind=VerdictKind.PASS,
            output=state,
            confidence=1.0,
            diagnosis=f"intake 规整完成: {len(repos)} repo(s) profile={profile}",
            granted_tags=["domain.absorption", "stage.normalized"],
        )


# ═══════════════════════════════════════════════════════════
# Node 02 · RepoFacadeFetcherRouter (ANCHOR + HARD)
# Stage 3d L1 升级: 抓更多数据
# ═══════════════════════════════════════════════════════════

class RepoFacadeFetcherRouter(Router):
    """用 gh CLI 抓 GitHub 仓库门面元数据 + 递归 tree + 贡献者 + 近期 release。

    L1 升级后每个 repo 抓 8 个 endpoint:
      1. repos/<o>/<n>                          → 元数据 (stars/license/default_branch)
      2. repos/<o>/<n>/contents                 → 顶层 (quick overview)
      3. repos/<o>/<n>/git/trees/<sha>?recursive=1 → 递归全 tree (所有文件路径)
      4. repos/<o>/<n>/languages                → 语言占比
      5. repos/<o>/<n>/readme                   → 全量 README
      6. repos/<o>/<n>/commits?per_page=10      → 近 10 commit 推断频率
      7. repos/<o>/<n>/contributors?per_page=10 → 前 10 贡献者
      8. repos/<o>/<n>/releases?per_page=5      → 近 5 release

    缓存策略: 每 repo 一个 JSON 写到 facade_cache/<owner>__<name>.json, 命中即跳过 gh 调用。
    """

    DESCRIPTION = (
        "gh CLI 抓 GitHub 全量门面: 递归 tree + 全 README + 贡献者 + 近期 release "
        "+ 语言/commit 频率/元数据。HARD: 任一 repo 拉取失败即 FAIL → HALT。"
    )
    FORMAT_IN = "absorption.intake"
    FORMAT_OUT = "absorption.facade_card"

    def __init__(self, *, use_cache: bool = True) -> None:
        self._use_cache = use_cache

    def _cache_path(self, owner: str, name: str) -> Path:
        d = _absorption_artifact_dir() / "facade_cache"
        d.mkdir(parents=True, exist_ok=True)
        return d / f"{owner}__{name}.json"

    def _fetch_recursive_tree(self, owner: str, name: str, default_branch: str) -> list[dict[str, Any]]:
        """抓递归全 tree。最多 100k 文件 (GitHub API 上限)。"""
        try:
            data = _gh_api_json(f"repos/{owner}/{name}/git/trees/{default_branch}?recursive=1")
        except RuntimeError as e:
            logger.warning("[facade_fetcher] 递归 tree 拉取失败 %s/%s: %s", owner, name, e)
            return []
        if not data:
            return []
        tree = data.get("tree") or []
        simplified = [
            {
                "path": t.get("path"),
                "type": t.get("type"),  # blob | tree
                "size": t.get("size", 0),
            }
            for t in tree
            if isinstance(t, dict)
        ]
        if data.get("truncated"):
            logger.warning(
                "[facade_fetcher] %s/%s 的 tree 被 GitHub 截断 (超过 100k 项)", owner, name
            )
            simplified.append({"_truncated": True})
        return simplified

    def _fetch_contributors(self, owner: str, name: str) -> list[dict[str, Any]]:
        try:
            data = _gh_api_json(f"repos/{owner}/{name}/contributors?per_page=10") or []
        except RuntimeError:
            return []
        return [
            {"login": d.get("login"), "contributions": d.get("contributions", 0)}
            for d in data[:10]
            if isinstance(d, dict)
        ]

    def _fetch_releases(self, owner: str, name: str) -> list[dict[str, Any]]:
        try:
            data = _gh_api_json(f"repos/{owner}/{name}/releases?per_page=5") or []
        except RuntimeError:
            return []
        out: list[dict[str, Any]] = []
        for r in data[:5]:
            if not isinstance(r, dict):
                continue
            body = (r.get("body") or "")[:1200]
            out.append({
                "tag": r.get("tag_name"),
                "name": r.get("name"),
                "published_at": r.get("published_at"),
                "body_excerpt": body,
            })
        return out

    def _fetch_one(self, owner: str, name: str) -> dict[str, Any]:
        cache_file = self._cache_path(owner, name)
        if self._use_cache and cache_file.exists():
            try:
                cached = json.loads(cache_file.read_text(encoding="utf-8"))
                cached["_cache_hit"] = True
                logger.info("[facade_fetcher] cache hit %s/%s", owner, name)
                return cached
            except Exception:
                logger.warning("[facade_fetcher] cache 损坏, 回退到 API: %s", cache_file)

        logger.info("[facade_fetcher] fetching %s/%s (full)", owner, name)

        # 1. 元数据
        repo_meta = _gh_api_json(f"repos/{owner}/{name}")
        default_branch = repo_meta.get("default_branch") or "main"

        # 2. 顶层 contents
        contents = _gh_api_json(f"repos/{owner}/{name}/contents") or []
        top_level_dirs = [c["name"] for c in contents if isinstance(c, dict) and c.get("type") == "dir"]
        top_level_files = [c["name"] for c in contents if isinstance(c, dict) and c.get("type") == "file"][:50]

        # 3. 递归 tree
        tree_recursive = self._fetch_recursive_tree(owner, name, default_branch)
        file_count = sum(1 for t in tree_recursive if t.get("type") == "blob")
        dir_count = sum(1 for t in tree_recursive if t.get("type") == "tree")

        # 4. 语言
        languages = _gh_api_json(f"repos/{owner}/{name}/languages") or {}
        total = sum(languages.values()) or 1
        language_stats = {k: round(v / total, 4) for k, v in languages.items()}

        # 5. README
        try:
            readme_obj = _gh_api_json(f"repos/{owner}/{name}/readme")
            readme_full = _decode_base64_readme(readme_obj.get("content", "")) if readme_obj else ""
        except RuntimeError:
            readme_full = ""

        # 6. 近期 commits
        try:
            commits = _gh_api_json(f"repos/{owner}/{name}/commits?per_page=10") or []
        except RuntimeError:
            commits = []

        # 7. 贡献者
        contributors = self._fetch_contributors(owner, name)

        # 8. 近期 releases
        releases = self._fetch_releases(owner, name)

        # commit_frequency 推断
        if len(commits) >= 2:
            try:
                first = commits[0]["commit"]["author"]["date"]
                last = commits[-1]["commit"]["author"]["date"]
                d_first = datetime.datetime.fromisoformat(first.replace("Z", "+00:00"))
                d_last = datetime.datetime.fromisoformat(last.replace("Z", "+00:00"))
                span_days = max(1.0, (d_first - d_last).total_seconds() / 86400)
                rate = len(commits) / span_days
                if rate >= 1.0:
                    commit_frequency = "high"
                elif rate >= 0.2:
                    commit_frequency = "medium"
                else:
                    commit_frequency = "low"
            except Exception:
                commit_frequency = "unknown"
        else:
            commit_frequency = "unknown"

        card = {
            "owner": owner,
            "name": name,
            "url": repo_meta.get("html_url") or f"https://github.com/{owner}/{name}",
            "description": repo_meta.get("description") or "",
            "stars": repo_meta.get("stargazers_count", 0),
            "forks": repo_meta.get("forks_count", 0),
            "open_issues": repo_meta.get("open_issues_count", 0),
            "topics": repo_meta.get("topics") or [],
            "primary_language": repo_meta.get("language") or "Unknown",
            "default_branch": default_branch,
            "license": (repo_meta.get("license") or {}).get("spdx_id") or "UNKNOWN",
            "readme_full": readme_full,
            "readme_size": len(readme_full),
            "top_level_dirs": top_level_dirs,
            "top_level_files": top_level_files,
            "tree_recursive": tree_recursive,
            "file_count": file_count,
            "dir_count": dir_count,
            "language_stats": language_stats,
            "commit_frequency": commit_frequency,
            "recent_commits": [
                {
                    "sha": c.get("sha", "")[:7],
                    "author": (c.get("commit") or {}).get("author", {}).get("name"),
                    "date": (c.get("commit") or {}).get("author", {}).get("date"),
                    "message": ((c.get("commit") or {}).get("message") or "").split("\n")[0][:120],
                }
                for c in commits[:10]
            ],
            "contributors": contributors,
            "releases": releases,
            "fetched_at": datetime.datetime.utcnow().isoformat() + "Z",
            "_cache_hit": False,
        }

        try:
            # OMNI-013 ALLOW: business artifact write (audited 2026-04-08)
            cache_file.write_text(
                json.dumps(card, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except Exception as e:
            logger.warning("[facade_fetcher] 缓存写盘失败: %s", e)

        return card

    def run(self, input_data: Any) -> Verdict:
        if not isinstance(input_data, dict) or "repos" not in input_data:
            return Verdict(
                kind=VerdictKind.FAIL,
                output=input_data,
                diagnosis="intake 必须含 repos 列表",
            )

        facade_cards: list[dict[str, Any]] = []
        errors: list[str] = []
        for repo in input_data["repos"]:
            try:
                card = self._fetch_one(repo["owner"], repo["name"])
                facade_cards.append(card)
            except RuntimeError as e:
                errors.append(f"{repo['owner']}/{repo['name']}: {e}")

        if errors:
            return Verdict(
                kind=VerdictKind.FAIL,
                output=input_data,
                diagnosis="GitHub API 拉取失败:\n  " + "\n  ".join(errors),
            )

        state = dict(input_data)
        state["facade_cards"] = facade_cards
        cache_hits = sum(1 for c in facade_cards if c.get("_cache_hit"))
        total_files = sum(c.get("file_count", 0) for c in facade_cards)
        return Verdict(
            kind=VerdictKind.PASS,
            output=state,
            confidence=1.0,
            diagnosis=(
                f"抓取 {len(facade_cards)} 张 facade card "
                f"({cache_hits} 缓存命中, {len(facade_cards) - cache_hits} API 调用); "
                f"总文件数 {total_files}"
            ),
            granted_tags=["domain.absorption", "stage.fetched"],
        )


# ═══════════════════════════════════════════════════════════
# Node 03 · OmnifactorySnapshotFetcherRouter (ANCHOR + HARD)
# Stage 3d L2 新增: 扫本仓自身能力
# ═══════════════════════════════════════════════════════════

class OmnifactorySnapshotFetcherRouter(Router):
    """扫描 OmniCompany 本仓, 生成当前能力快照。

    无 LLM, 无网络, 纯文件系统扫描。由 snapshot.build_snapshot() 完成重活。
    产物供下游 LandmarkPicker 的 LLM 通过 omni_capabilities 工具查询。
    """

    DESCRIPTION = (
        "扫本仓 packages/core/runtime, 生成 OmniCompany 当前能力快照 "
        "(packages / registered_pipelines / routers / builtin_tools / core_modules)。"
        "无 LLM 无网络, 纯 FS 扫描。"
    )
    FORMAT_IN = "absorption.facade_card"
    FORMAT_OUT = "absorption.omnifactory_snapshot"

    def run(self, input_data: Any) -> Verdict:
        try:
            snapshot = build_snapshot()
        except Exception as e:
            return Verdict(
                kind=VerdictKind.FAIL,
                output=input_data,
                diagnosis=f"OmniCompany 快照扫描失败: {e}",
            )
        stats = snapshot_stats(snapshot)
        state = dict(input_data) if isinstance(input_data, dict) else {}
        state["omni_snapshot"] = snapshot
        return Verdict(
            kind=VerdictKind.PASS,
            output=state,
            confidence=1.0,
            diagnosis=(
                f"OmniCompany 快照: {stats['packages']} packages / "
                f"{stats['registered_pipelines']} pipelines / "
                f"{stats['routers']} routers / "
                f"{stats['builtin_tools']} tools / "
                f"{stats['core_modules']} core modules"
            ),
            granted_tags=["domain.absorption", "stage.self_introspected"],
        )


# ═══════════════════════════════════════════════════════════
# Node 05 · CoverageAuditorRouter (ANCHOR + HARD)
# Stage 3d L5 新增: 覆盖度审计
# ═══════════════════════════════════════════════════════════

class CoverageAuditorRouter(Router):
    """对比 LandmarkPicker 的探索轨迹 (读过哪些文件) vs 仓库总 tree, 产出覆盖度报告。

    读 state 中:
      - facade_cards[i].tree_recursive    (总 tree, 来自 L1 升级)
      - picker_read_files                 (来自 LandmarkPicker session state)
      - picker_listed_paths               (同上)

    输出:
      - coverage_by_repo[i] = {
          total_files, files_read, read_percent, top_dirs_scanned,
          top_dirs_unscanned, unscanned_reasons
        }
      - overall_coverage_percent
      - honest_limitations (markdown-ready)
    """

    DESCRIPTION = (
        "审计 LandmarkPicker 的探索覆盖度: 总 tree vs 实际读过的文件。"
        "产出覆盖百分比 + 未扫目录 + 诚实局限声明。"
    )
    FORMAT_IN = "absorption.landmark_list"
    FORMAT_OUT = "absorption.coverage_audit"

    def run(self, input_data: Any) -> Verdict:
        if not isinstance(input_data, dict):
            return Verdict(
                kind=VerdictKind.FAIL,
                output=input_data,
                diagnosis="input 必须是 dict",
            )

        facade_cards = input_data.get("facade_cards") or []
        read_files = input_data.get("picker_read_files") or []
        listed_paths = input_data.get("picker_listed_paths") or []

        coverage_by_repo: list[dict[str, Any]] = []
        for card in facade_cards:
            owner = card["owner"]
            name = card["name"]
            tree = card.get("tree_recursive") or []
            total_files = sum(1 for t in tree if t.get("type") == "blob")
            if total_files == 0:
                coverage_by_repo.append({
                    "owner": owner,
                    "name": name,
                    "total_files": 0,
                    "files_read": 0,
                    "read_percent": 0.0,
                    "top_dirs": {},
                    "unscanned_top_dirs": [],
                    "note": "tree_recursive 为空或被截断, 无法审计",
                })
                continue

            # 该 repo 在此 run 中读过的文件
            my_read = [
                r for r in read_files
                if r.get("owner") == owner and r.get("name") == name
            ]
            my_listed = [
                l for l in listed_paths
                if l.get("owner") == owner and l.get("name") == name
            ]

            # 按顶层目录分组统计
            top_dirs: dict[str, dict[str, Any]] = {}
            for d in card.get("top_level_dirs", []):
                top_dirs[d] = {
                    "total_files": 0,
                    "files_read": 0,
                    "was_listed": False,
                }
            for t in tree:
                if t.get("type") != "blob":
                    continue
                path = t.get("path", "")
                top = path.split("/", 1)[0]
                if top in top_dirs:
                    top_dirs[top]["total_files"] += 1
            for r in my_read:
                path = r.get("path", "")
                top = path.split("/", 1)[0]
                if top in top_dirs:
                    top_dirs[top]["files_read"] += 1
            for l in my_listed:
                lpath = l.get("path", "")
                if lpath == "<root>":
                    continue
                top = lpath.split("/", 1)[0]
                if top in top_dirs:
                    top_dirs[top]["was_listed"] = True

            unscanned = [
                d for d, info in top_dirs.items()
                if info["files_read"] == 0 and not info["was_listed"]
            ]

            coverage_by_repo.append({
                "owner": owner,
                "name": name,
                "total_files": total_files,
                "files_read": len(my_read),
                "read_percent": round(len(my_read) / total_files * 100, 2),
                "top_dirs": top_dirs,
                "unscanned_top_dirs": unscanned,
                "listed_dirs_count": len(my_listed),
            })

        overall_total = sum(c.get("total_files", 0) for c in coverage_by_repo)
        overall_read = sum(c.get("files_read", 0) for c in coverage_by_repo)
        overall_pct = round(overall_read / overall_total * 100, 2) if overall_total else 0.0

        state = dict(input_data)
        state["coverage_audit"] = {
            "coverage_by_repo": coverage_by_repo,
            "overall_total_files": overall_total,
            "overall_files_read": overall_read,
            "overall_coverage_percent": overall_pct,
        }
        return Verdict(
            kind=VerdictKind.PASS,
            output=state,
            confidence=1.0,
            diagnosis=(
                f"覆盖审计: {overall_read} / {overall_total} 文件 "
                f"({overall_pct}%) 跨 {len(coverage_by_repo)} repo"
            ),
            granted_tags=["domain.absorption", "stage.audited"],
        )


# ═══════════════════════════════════════════════════════════
# Node 06 · TriageGateRouter (ANCHOR + HARD)
# ═══════════════════════════════════════════════════════════

class TriageGateRouter(Router):
    """tier-1 过滤 + 持久化 landmark_pool (含全部 L1~L7 产物)。"""

    DESCRIPTION = (
        "tier-1 过滤 (≥1 才放行) + 落盘 landmark_pool 到 "
        "data/absorption/landmark_pool/<absorption_id>.json (全量证据 + 覆盖审计)。"
    )
    FORMAT_IN = "absorption.coverage_audit"
    FORMAT_OUT = "absorption.triaged_landmarks"

    def _pool_path(self, absorption_id: str) -> Path:
        d = _absorption_artifact_dir() / "landmark_pool"
        d.mkdir(parents=True, exist_ok=True)
        return d / f"{absorption_id}.json"

    def run(self, input_data: Any) -> Verdict:
        if not isinstance(input_data, dict) or "landmarks" not in input_data:
            return Verdict(
                kind=VerdictKind.FAIL,
                output=input_data,
                diagnosis="缺少 landmarks",
            )

        all_landmarks = input_data.get("landmarks") or []
        absorption_id = input_data.get("absorption_id", "abs-unknown")
        landscape_sketches = input_data.get("landscape_sketches") or []
        capability_gaps = input_data.get("capability_gaps") or []
        coverage_audit = input_data.get("coverage_audit") or {}

        tier_one = [lm for lm in all_landmarks if lm.get("tier") == 1]
        tier_two = [lm for lm in all_landmarks if lm.get("tier") == 2]
        tier_three = [lm for lm in all_landmarks if lm.get("tier") == 3]

        pool_payload = {
            "absorption_id": absorption_id,
            "profile": input_data.get("profile"),
            "fetched_at": datetime.datetime.utcnow().isoformat() + "Z",
            "tier_one": tier_one,
            "tier_two": tier_two,
            "tier_three": tier_three,
            "tier_one_count": len(tier_one),
            "total_candidates": len(all_landmarks),
            "landscape_sketches": landscape_sketches,
            "capability_gaps": capability_gaps,
            "coverage_audit": coverage_audit,
            "picker_read_files": input_data.get("picker_read_files") or [],
            "picker_listed_paths": input_data.get("picker_listed_paths") or [],
            "picker_finish_summary": input_data.get("picker_finish_summary"),
            "facade_cards_summary": [
                {
                    "owner": c["owner"],
                    "name": c["name"],
                    "stars": c.get("stars"),
                    "forks": c.get("forks"),
                    "open_issues": c.get("open_issues"),
                    "primary_language": c.get("primary_language"),
                    "license": c.get("license"),
                    "commit_frequency": c.get("commit_frequency"),
                    "file_count": c.get("file_count"),
                    "contributors_count": len(c.get("contributors", [])),
                    "releases_count": len(c.get("releases", [])),
                }
                for c in input_data.get("facade_cards", [])
            ],
        }
        pool_file = self._pool_path(absorption_id)
        try:
            # OMNI-013 ALLOW: business artifact write (audited 2026-04-08)
            pool_file.write_text(
                json.dumps(pool_payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            logger.info("[absorption.triage_gate] pool 已写盘: %s", pool_file)
        except Exception as e:
            return Verdict(
                kind=VerdictKind.FAIL,
                output=input_data,
                diagnosis=f"landmark_pool 写盘失败: {e}",
            )

        if not tier_one:
            return Verdict(
                kind=VerdictKind.FAIL,
                output=input_data,
                diagnosis=(
                    "无 tier-1 地标 — LLM 判定该仓与 OmniCompany 当前能力地图无显著差异, "
                    f"全部 {len(all_landmarks)} 个候选已留档到 {pool_file}"
                ),
            )

        state = dict(input_data)
        state["tier_one"] = tier_one
        state["tier_one_count"] = len(tier_one)
        state["pool_path"] = str(pool_file)
        return Verdict(
            kind=VerdictKind.PASS,
            output=state,
            confidence=1.0,
            diagnosis=(
                f"放行 {len(tier_one)} 个 tier-1; "
                f"全部 {len(all_landmarks)} 候选 + {len(landscape_sketches)} 速写 + "
                f"{len(capability_gaps)} gap 已留档到 {pool_file.name}"
            ),
            granted_tags=["domain.absorption", "stage.triaged", "ready_for_phase_b"],
        )


# ═══════════════════════════════════════════════════════════
# Node 07 · ReportWriterRouter (TRANSFORMER + RULE)
# Stage 3d L6 新增: markdown 报告生成
# ═══════════════════════════════════════════════════════════

class ReportWriterRouter(Router):
    """从完整 state 生成 human-readable markdown 报告。

    产物: data/absorption/reports/<absorption_id>.md
    内容: TL;DR / 每个 repo 的 Landscape / tier-1 详情 + 证据 / gap 分析 / 覆盖声明 / 诚实局限。
    """

    DESCRIPTION = (
        "从全 state 生成 markdown 报告到 data/absorption/reports/<absorption_id>.md。"
        "含证据引用、覆盖审计、诚实局限声明。"
    )
    FORMAT_IN = "absorption.triaged_landmarks"
    FORMAT_OUT = "absorption.report"

    def _report_path(self, absorption_id: str) -> Path:
        d = _absorption_artifact_dir() / "reports"
        d.mkdir(parents=True, exist_ok=True)
        return d / f"{absorption_id}.md"

    def _render_landmark(self, lm: dict[str, Any], read_files_index: dict) -> str:
        ev = lm.get("evidence") or {}
        snippet = ev.get("snippet", "")
        file_path = ev.get("file_path", "?")
        read_entry = read_files_index.get(file_path)
        read_note = ""
        if read_entry:
            total = read_entry.get("total_lines", 0)
            lines_read = read_entry.get("lines_read", 0)
            read_note = f" — read {lines_read} of {total} lines via gh_file_read"
        parts = [
            f"#### `{lm.get('path', '?')}` — tier {lm.get('tier')}",
            "",
            f"{lm.get('why_interesting', '')}",
            "",
            f"**Evidence file**: `{file_path}`{read_note}",
            "",
            "```",
            snippet.rstrip(),
            "```",
            "",
            f"**Why this evidence**: {ev.get('why_this_evidence', '')}",
            "",
            f"**vs OmniCompany**: {lm.get('compared_against_omnifactory', '')}",
            "",
        ]
        return "\n".join(parts)

    def _render_sketch(self, sk: dict[str, Any]) -> str:
        # New sketch schema: positioning (prose), core_abstractions (list of dicts),
        # diff_vs_omnifactory (prose), files_relied_on (list of str)
        abstractions = sk.get("core_abstractions") or []
        abs_rendered: list[str] = []
        for a in abstractions:
            if isinstance(a, dict):
                name = a.get("name", "?")
                what = a.get("what_it_does", "")
                ev_file = a.get("evidence_file")
                ev_suffix = f" _(seen in `{ev_file}`)_" if ev_file else ""
                abs_rendered.append(f"- **{name}** — {what}{ev_suffix}")
            else:
                # Legacy string form
                abs_rendered.append(f"- {a}")
        abs_list = "\n".join(abs_rendered) if abs_rendered else "_(no abstractions recorded)_"

        files = sk.get("files_relied_on") or []
        files_list = "\n".join(f"- `{f}`" for f in files) if files else "_(not specified)_"
        positioning = sk.get("positioning") or sk.get("one_liner") or "_(no positioning)_"
        return "\n".join([
            f"**Positioning**: {positioning}",
            "",
            "**Core abstractions**:",
            abs_list,
            "",
            f"**vs OmniCompany**: {sk.get('diff_vs_omnifactory', '')}",
            "",
            "**Files relied on**:",
            files_list,
            "",
        ])

    def _render_gap(self, gap: dict[str, Any]) -> str:
        ev = gap.get("external_evidence") or {}
        snippet = ev.get("snippet", "")
        queries = gap.get("omni_capabilities_queries_used") or []
        q_list = "\n".join(f"  - {q}" for q in queries) if queries else "_(none recorded)_"
        why_proves = ev.get("why_this_proves_gap", "")
        return "\n".join([
            f"#### {gap.get('gap_title', '?')}",
            "",
            f"{gap.get('gap_description', '')}",
            "",
            f"**External evidence** (`{ev.get('file_path', '?')}`):",
            "",
            "```",
            snippet.rstrip(),
            "```",
            "",
            f"**Why this proves the gap**: {why_proves}",
            "",
            f"**OmniCompany current state**: {gap.get('omnifactory_current_state', '')}",
            "",
            "**Omni capability queries used**:",
            q_list,
            "",
        ])

    def _render_coverage(self, card: dict[str, Any], cov_entry: dict[str, Any]) -> str:
        lines = [
            f"- **Total source files in repo**: {cov_entry.get('total_files', 0)}",
            f"- **Files actually read by picker**: {cov_entry.get('files_read', 0)}",
            f"- **Directories explored via gh_tree_list**: {cov_entry.get('listed_dirs_count', 0)}",
            "",
        ]
        td = cov_entry.get("top_dirs") or {}
        if td:
            lines.append("| Top dir | Total files | Read | Listed |")
            lines.append("|---|---|---|---|")
            for name, info in td.items():
                listed = "✓" if info.get("was_listed") else ""
                lines.append(
                    f"| `{name}` | {info.get('total_files', 0)} | {info.get('files_read', 0)} | {listed} |"
                )
            lines.append("")
        unscanned = cov_entry.get("unscanned_top_dirs") or []
        if unscanned:
            lines.append("**⚠️ Top-level dirs NOT opened at all**:")
            lines.extend(f"- `{d}`" for d in unscanned)
            lines.append("")
        return "\n".join(lines)

    def _render_facade_facts(self, card: dict[str, Any]) -> str:
        lang_pct = card.get("language_stats") or {}
        top_langs = sorted(lang_pct.items(), key=lambda x: x[1], reverse=True)[:4]
        lang_str = ", ".join(f"{k} {v * 100:.1f}%" for k, v in top_langs)
        contributors = card.get("contributors") or []
        contrib_count = len(contributors)
        releases = card.get("releases") or []
        lines = [
            f"- **URL**: {card.get('url')}",
            f"- **Stars**: {card.get('stars', 0)} · Forks {card.get('forks', 0)} · Open issues {card.get('open_issues', 0)}",
            f"- **License**: {card.get('license', '?')}",
            f"- **Default branch**: `{card.get('default_branch')}`",
            f"- **Languages**: {lang_str or '?'}",
            f"- **Commit frequency**: {card.get('commit_frequency')}",
            f"- **Top contributors**: {contrib_count} recorded",
            f"- **File count**: {card.get('file_count', 0)} files / {card.get('dir_count', 0)} dirs",
            f"- **Recent releases**: {len(releases)}",
            f"- **README size**: {card.get('readme_size', 0)} bytes",
            f"- **Topics**: {card.get('topics') or '(none)'}",
        ]
        return "\n".join(lines)

    def run(self, input_data: Any) -> Verdict:
        if not isinstance(input_data, dict):
            return Verdict(
                kind=VerdictKind.FAIL,
                output=input_data,
                diagnosis="input 必须是 dict",
            )

        absorption_id = input_data.get("absorption_id", "abs-unknown")
        profile = input_data.get("profile", "?")
        facade_cards = input_data.get("facade_cards") or []
        landmarks = input_data.get("landmarks") or []
        sketches = input_data.get("landscape_sketches") or []
        gaps = input_data.get("capability_gaps") or []
        coverage_audit = input_data.get("coverage_audit") or {}
        cov_by_repo = {
            (c.get("owner"), c.get("name")): c
            for c in coverage_audit.get("coverage_by_repo", [])
        }
        picker_read_files = input_data.get("picker_read_files") or []
        # Index read files by path for cross-reference in landmark rendering
        read_files_index = {
            r.get("path"): r
            for r in picker_read_files
            if r.get("path")
        }
        finish_summary = input_data.get("picker_finish_summary")

        tier_one = [lm for lm in landmarks if lm.get("tier") == 1]
        tier_two = [lm for lm in landmarks if lm.get("tier") == 2]
        tier_three = [lm for lm in landmarks if lm.get("tier") == 3]

        # ── 拼 markdown ──
        md = [
            f"# Repo Absorption Report — `{absorption_id}`",
            "",
            f"**Generated**: {datetime.datetime.utcnow().isoformat()}Z  ",
            f"**Profile**: `{profile}`  ",
            f"**Repos analyzed**: {len(facade_cards)}  ",
            f"**Files read by picker**: {len(picker_read_files)} unique files  ",
            f"**Total source files in scope**: {coverage_audit.get('overall_total_files', 0)}",
            "",
            "---",
            "",
            "## TL;DR",
            "",
        ]
        if tier_one:
            md.append(f"**{len(tier_one)} tier-1 landmark(s)** — must-absorb findings backed by direct reading of the evidence file:")
            md.append("")
            for lm in tier_one:
                why = (lm.get("why_interesting", "") or "").replace("\n", " ")
                md.append(
                    f"- [{lm.get('owner')}/{lm.get('name')}] `{lm.get('path')}` — {why[:160]}"
                )
        else:
            md.append("**No tier-1 landmarks** — picker concluded this repo has no significant gap vs OmniCompany. triage_gate halts here (honest negative verdict).")
        md.append("")
        md.append(f"**{len(gaps)} capability gap(s)** · **{len(sketches)} landscape sketch(es)**")
        if finish_summary:
            md.extend(["", "**Picker closing summary** (the LLM's own words at `finish` time):", f"> {finish_summary}"])
        md.append("")
        md.append("---")
        md.append("")

        # ── 每 repo 一节 ──
        for card in facade_cards:
            owner = card["owner"]
            name = card["name"]
            md.append(f"## `{owner}/{name}`")
            md.append("")
            md.append("### Repository facts")
            md.append("")
            md.append(self._render_facade_facts(card))
            md.append("")

            # Landscape sketch
            my_sketches = [s for s in sketches if s.get("owner") == owner and s.get("name") == name]
            if my_sketches:
                md.append("### Landscape sketch")
                md.append("")
                for s in my_sketches:
                    md.append(self._render_sketch(s))
            else:
                md.append("### Landscape sketch")
                md.append("")
                md.append("_(picker did not submit a sketch for this repo)_")
                md.append("")

            # Tier-1 landmarks
            my_t1 = [lm for lm in tier_one if lm.get("owner") == owner and lm.get("name") == name]
            if my_t1:
                md.append(f"### Tier-1 landmarks (must absorb) — {len(my_t1)}")
                md.append("")
                for lm in my_t1:
                    md.append(self._render_landmark(lm, read_files_index))
            # Tier-2
            my_t2 = [lm for lm in tier_two if lm.get("owner") == owner and lm.get("name") == name]
            if my_t2:
                md.append(f"### Tier-2 landmarks (worth examining) — {len(my_t2)}")
                md.append("")
                md.append("| Path | Why | Evidence file |")
                md.append("|---|---|---|")
                for lm in my_t2:
                    ev = lm.get("evidence") or {}
                    why = (lm.get("why_interesting", "") or "").replace("|", " ").replace("\n", " ")
                    md.append(
                        f"| `{lm.get('path')}` | {why[:140]} | `{ev.get('file_path', '?')}` |"
                    )
                md.append("")
            # Tier-3
            my_t3 = [lm for lm in tier_three if lm.get("owner") == owner and lm.get("name") == name]
            if my_t3:
                md.append(f"### Tier-3 landmarks (learn-only) — {len(my_t3)}")
                md.append("")
                md.append("| Path | Why |")
                md.append("|---|---|")
                for lm in my_t3:
                    why = (lm.get("why_interesting", "") or "").replace("|", " ").replace("\n", " ")
                    md.append(f"| `{lm.get('path')}` | {why[:140]} |")
                md.append("")

            # Capability gaps
            my_gaps = [g for g in gaps if g.get("owner") == owner and g.get("name") == name]
            if my_gaps:
                md.append(f"### Capability gaps — {len(my_gaps)}")
                md.append("")
                for g in my_gaps:
                    md.append(self._render_gap(g))
            else:
                md.append("### Capability gaps")
                md.append("")
                md.append("_(picker did not flag any gaps for this repo)_")
                md.append("")

            # Coverage honesty
            cov_entry = cov_by_repo.get((owner, name)) or {}
            md.append("### Coverage honesty")
            md.append("")
            md.append(self._render_coverage(card, cov_entry))

            md.append("---")
            md.append("")

        # ── Footer: global honest limitations ──
        md.append("## Global honest limitations")
        md.append("")
        md.append(
            "- This report is based on what the picker LLM **actually read** via the "
            "`gh_file_read` tool during this run. Any finding not directly backed by a "
            "read file is a risk — treat it as a lead to verify later, not a conclusion."
        )
        overall_total = coverage_audit.get("overall_total_files", 0)
        overall_read = coverage_audit.get("overall_files_read", 0)
        if overall_total:
            md.append(
                f"- Overall: the picker read **{overall_read} files** out of "
                f"**{overall_total} source files** in scope. The remaining "
                f"{overall_total - overall_read} files were not opened; landmarks in those "
                f"areas may exist but were not discovered in this run."
            )
        md.append(
            "- Each `capability_gap` was verified against `omni_capabilities` queries "
            "(see the queries listed under each gap). However OmniCompany's snapshot "
            "itself is generated by a filesystem scan of `packages/` / `core/` / `runtime/` "
            "and may miss capabilities declared only at runtime, or capabilities sitting "
            "under unfamiliar namespaces. If you suspect a gap is a false positive, "
            "re-run the picker with a broader omni_capabilities query filter."
        )
        md.append("")
        md.append(f"_Report generated by absorption-survey pipeline · absorption_id={absorption_id}_")
        md.append("")

        content = "\n".join(md)
        report_file = self._report_path(absorption_id)
        try:
            # OMNI-013 ALLOW: business artifact write (audited 2026-04-08)
            report_file.write_text(content, encoding="utf-8")
            logger.info("[absorption.report_writer] 报告已写盘: %s", report_file)
        except Exception as e:
            return Verdict(
                kind=VerdictKind.FAIL,
                output=input_data,
                diagnosis=f"报告写盘失败: {e}",
            )

        state = dict(input_data)
        state["report_path"] = str(report_file)
        state["report_size_bytes"] = len(content.encode("utf-8"))
        return Verdict(
            kind=VerdictKind.PASS,
            output=state,
            confidence=1.0,
            diagnosis=(
                f"markdown 报告 {state['report_size_bytes']} 字节已写到 {report_file.name}; "
                f"{len(tier_one)} t1 + {len(tier_two)} t2 + {len(tier_three)} t3 + "
                f"{len(gaps)} gaps 已渲染"
            ),
            granted_tags=["domain.absorption", "stage.reported"],
        )


# ═══════════════════════════════════════════════════════════
# V2 — 问题驱动定向深读管线 (Phase 2 实现)
# ReconScoutV2Router: AgentNodeLoop，本地文件工具，≤30 文件
# IntersectionPlannerV2Router: 单次 LLM 调用，生成 G1-G7 问题清单
# Phase 3/4 实现见 docs/plans/[2026-04-13]REPO-ABSORPTION-V2/plan.md
# ═══════════════════════════════════════════════════════════

# ── V2 ReconScout 会话状态 ──────────────────────────────

_RECON_SESSION_STATE: dict[str, dict] = {}
_RECON_SESS_LOCK = threading.Lock()
_RECON_SESS_COUNTER = 0


def _next_recon_sess_id(router: Any) -> str:
    global _RECON_SESS_COUNTER
    with _RECON_SESS_LOCK:
        _RECON_SESS_COUNTER += 1
        return f"recon-v2-{id(router)}-{_RECON_SESS_COUNTER}"


def _new_recon_session(
    sess_id: str,
    repo_local_path: str,
    repo_name: str,
    self_portrait: str,
    upstream_input: dict,
) -> dict:
    state: dict = {
        "repo_local_path": repo_local_path,
        "repo_name": repo_name,
        "self_portrait": self_portrait,
        "upstream_input": upstream_input,
        "listed_paths": [],
        "read_files": [],
        "recon_map": None,
    }
    _RECON_SESSION_STATE[sess_id] = state
    return state


def _make_recon_tools(sess_id: str) -> list:
    """为 ReconScout 会话构建本地文件工具列表（闭包绑定 sess_id）。"""
    from omnicompany.runtime.agent.agent_loop_tools import (
        FinishTool,
        ThinkTool,
        ToolDefinition,
    )

    def _state() -> dict:
        return _RECON_SESSION_STATE[sess_id]

    # ── local_list ───────────────────────────────────────
    LocalListTool = ToolDefinition(
        name="local_list",
        description=(
            "List files and subdirectories at a path within the local repo. "
            "Returns JSON with items array of {name, type, path, size}. "
            "type is 'file' or 'dir'. Use path='' for repo root, 'src/core' for subdir. "
            "Non-recursive — call multiple times to drill down. "
            "Start by listing the root to understand overall structure."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Relative path within repo (empty string for root)",
                    "default": "",
                },
            },
            "required": [],
        },
        is_concurrency_safe=True,
        is_readonly=True,
    )

    def _local_list_call(args: dict, executor: Any, ctx: Any) -> str:
        repo_root = Path(_state()["repo_local_path"])
        rel = (args.get("path") or "").strip("/\\").strip()
        target = repo_root / rel if rel else repo_root
        if not target.exists():
            return f"Error: path '{rel or '.'}' does not exist"
        if not target.is_dir():
            return f"Error: '{rel or '.'}' is not a directory"
        items = []
        try:
            for entry in sorted(target.iterdir()):
                rel_path = str(entry.relative_to(repo_root)).replace("\\", "/")
                items.append({
                    "name": entry.name,
                    "type": "dir" if entry.is_dir() else "file",
                    "path": rel_path,
                    "size": entry.stat().st_size if entry.is_file() else 0,
                })
        except PermissionError as e:
            return f"Error: permission denied listing {target}: {e}"
        _state()["listed_paths"].append(rel or ".")
        return json.dumps({"path": rel or ".", "items": items}, ensure_ascii=False)

    LocalListTool.call = _local_list_call  # type: ignore[assignment]

    # ── local_read ───────────────────────────────────────
    LocalReadTool = ToolDefinition(
        name="local_read",
        description=(
            "Read the content of a file from the local repo. Returns line-numbered content. "
            "Use path relative to repo root (e.g., 'README.md', 'src/core/agent.py'). "
            "Use offset and limit to read specific sections of large files. "
            "Default limit is 400 lines — for large files, read in segments with offset."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Relative path within repo, e.g. 'README.md' or 'src/core/agent.py'",
                },
                "offset": {
                    "type": "integer",
                    "default": 0,
                    "minimum": 0,
                    "description": "Line number to start reading from (0-based)",
                },
                "limit": {
                    "type": "integer",
                    "default": 400,
                    "minimum": 10,
                    "maximum": 1200,
                    "description": "Max lines to read (default 400, max 1200)",
                },
            },
            "required": ["path"],
        },
        is_concurrency_safe=True,
        is_readonly=True,
    )

    def _local_read_call(args: dict, executor: Any, ctx: Any) -> str:
        repo_root = Path(_state()["repo_local_path"])
        rel = (args.get("path") or "").strip("/\\")
        target = repo_root / rel
        if not target.exists():
            return f"Error: file '{rel}' not found in repo"
        if not target.is_file():
            return f"Error: '{rel}' is a directory, not a file"
        size = target.stat().st_size
        if size > 1024 * 1024:
            return f"Error: file too large ({size} bytes); max 1MB"
        try:
            content = target.read_text(encoding="utf-8", errors="replace")
        except Exception as e:
            return f"Error reading {rel}: {e}"
        lines = content.split("\n")
        total = len(lines)
        offset = int(args.get("offset") or 0)
        limit = int(args.get("limit") or 400)
        start = min(offset, total)
        end = min(start + limit, total)
        segment = lines[start:end]
        numbered = "\n".join(f"{i + 1:5d}\t{line}" for i, line in enumerate(segment, start=start))
        read_log = _state()["read_files"]
        if rel not in read_log:
            read_log.append(rel)
        header = f"=== {rel} (total {total} lines, showing {start + 1}-{end}) ===\n"
        return header + numbered

    LocalReadTool.call = _local_read_call  # type: ignore[assignment]

    # ── submit_recon_map ─────────────────────────────────
    SubmitReconMapTool = ToolDefinition(
        name="submit_recon_map",
        description=(
            "Submit the final reconnaissance result. Call this ONCE when you have read "
            "enough files to produce a solid capability map. Requirements:\n"
            "- capability_map: functional domains → 1-3 sentence description\n"
            "- key_modules: 5-10 most important files with one-sentence descriptions\n"
            "- architecture_summary: 2-3 paragraph architectural overview\n"
            "- entry_points: list of main entry file paths\n"
            "Must read at least 5 files before submitting."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "capability_map": {
                    "type": "object",
                    "description": "Functional domain → 1-3 sentence description",
                    "additionalProperties": {"type": "string"},
                },
                "key_modules": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "path": {"type": "string"},
                            "description": {"type": "string"},
                        },
                        "required": ["path", "description"],
                    },
                    "minItems": 3,
                    "maxItems": 10,
                },
                "architecture_summary": {
                    "type": "string",
                    "description": "2-3 paragraph architectural overview",
                },
                "entry_points": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Main entry file paths (relative to repo root)",
                },
            },
            "required": ["capability_map", "key_modules", "architecture_summary"],
        },
        is_concurrency_safe=False,
        is_readonly=True,
    )

    def _submit_recon_map_call(args: dict, executor: Any, ctx: Any) -> str:
        files_read = _state()["read_files"]
        if len(files_read) < 5:
            return (
                f"Error: must read at least 5 files before submitting. "
                f"Currently read: {files_read}"
            )
        _state()["recon_map"] = dict(args)
        n_domains = len(args.get("capability_map") or {})
        n_modules = len(args.get("key_modules") or [])
        return f"Recon map submitted: {n_domains} domains, {n_modules} key modules"

    SubmitReconMapTool.call = _submit_recon_map_call  # type: ignore[assignment]

    # ── local_grep ───────────────────────────────────────
    LocalGrepTool = ToolDefinition(
        name="local_grep",
        description=(
            "Search file contents across the entire local repo using a regex pattern. "
            "Returns matching lines with file path and line number. "
            "Use this to find things you don't know the filename for — e.g., "
            "'class.*Error', 'def run', 'import asyncio', '810' (line count in comments). "
            "glob_pattern filters which files to search (e.g. '*.py', '*.ts', '**/*.md'). "
            "max_results limits output lines (default 60, max 200). "
            "This is your primary tool for discovering important modules you didn't know existed."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "pattern": {
                    "type": "string",
                    "description": "Regex pattern to search for in file contents",
                },
                "glob_pattern": {
                    "type": "string",
                    "description": "File glob filter, e.g. '*.py', '*.ts', '**/*.md' (default: all files)",
                    "default": "",
                },
                "max_results": {
                    "type": "integer",
                    "default": 60,
                    "minimum": 1,
                    "maximum": 200,
                    "description": "Max result lines to return",
                },
            },
            "required": ["pattern"],
        },
        is_concurrency_safe=True,
        is_readonly=True,
    )

    def _local_grep_call(args: dict, executor: Any, ctx: Any) -> str:
        import subprocess as _sp
        repo_root = Path(_state()["repo_local_path"])
        pattern = args.get("pattern", "")
        if not pattern:
            return "Error: pattern is required"
        glob = (args.get("glob_pattern") or "").strip()
        max_results = int(args.get("max_results") or 60)

        cmd = ["rg", "--line-number", "--no-heading", "--color=never",
               "--max-count=5", pattern]
        if glob:
            cmd += ["--glob", glob]
        cmd.append(".")

        try:
            result = _sp.run(
                cmd, cwd=str(repo_root),
                capture_output=True, text=True, encoding="utf-8",
                errors="replace", timeout=30,
            )
            lines = result.stdout.splitlines()
        except FileNotFoundError:
            # rg not available, fall back to Python grep
            lines = []
            import re as _re
            try:
                rx = _re.compile(pattern)
            except _re.error as e:
                return f"Error: invalid regex '{pattern}': {e}"
            import fnmatch as _fn
            for fpath in sorted(repo_root.rglob("*")):
                if not fpath.is_file():
                    continue
                if glob and not _fn.fnmatch(fpath.name, glob.lstrip("**/").lstrip("**\\")):
                    continue
                try:
                    text = fpath.read_text(encoding="utf-8", errors="replace")
                    for i, line in enumerate(text.splitlines(), 1):
                        if rx.search(line):
                            rel = str(fpath.relative_to(repo_root)).replace("\\", "/")
                            lines.append(f"{rel}:{i}:{line}")
                            if len(lines) >= max_results:
                                break
                except Exception:
                    continue
                if len(lines) >= max_results:
                    break
        except Exception as e:
            return f"Error running grep: {e}"

        if not lines:
            return f"No matches for pattern '{pattern}'"
        truncated = lines[:max_results]
        note = f" (showing first {max_results})" if len(lines) > max_results else ""
        header = f"=== grep '{pattern}' — {len(truncated)} matches{note} ===\n"
        return header + "\n".join(truncated)

    LocalGrepTool.call = _local_grep_call  # type: ignore[assignment]

    return [LocalListTool, LocalReadTool, LocalGrepTool, SubmitReconMapTool, ThinkTool, FinishTool]


# ── V2 ReconScout 系统提示 ──────────────────────────────

_RECON_SYSTEM_PROMPT = """你是 OmniCompany 的代码侦察专家。你的工作方式是一个真正的 Agent：
搜索 → 发现 → 读取 → 更新认知 → 再搜索，循环直到你对这个 repo 有足够清晰的认知。

## 任务

产出一份**能力图谱**（capability map）：这个 repo 能做什么、核心模块是什么、架构取向如何。
目标是"我看到了什么"——客观陈述，不猜测。

## 工作方式

**你有三个工具：**
- `local_list`：列目录（了解结构）
- `local_grep`：在全 repo 搜索关键词/模式（发现你不知道的东西）
- `local_read`：读具体文件（深入理解）

**探索循环（每轮重复直到满足）：**

1. **看**：列目录或读一个文件
2. **想**（think）：这告诉了我什么？我还不知道什么？下一步该搜什么？
3. **搜**：用 grep 验证假设或发现新线索
4. **读**：读 grep 找到的值得深读的文件
5. **重复**：每次读完都重新评估——还有什么重要的我没看到？

**grep 是你的主动发现工具，不是备用工具：**
- 不要猜文件名，先搜：`local_grep "class Classifier"` 比猜 "classifier.py" 更可靠
- 读完 README 后，搜索里面提到的关键词，找到真正实现它的文件
- 如果一个功能域还模糊，grep 该域的关键术语
- 每个你感兴趣的功能，都值得搜一下看有没有独立模块在负责

## 约束

- 最多读 **30 个文件**（grep 不限）
- 每个文件读完后 **think 一次**，更新你的认知地图

## 产出

满足以下任一条件时调 `submit_recon_map`：
- 已读 ≥8 个文件 **且** 对主要功能域有清晰认知
- 或 已读 ≥15 个文件

产出内容：
- **capability_map**：功能域 → 1-3 句描述（只写你看到的，不确定的注明"根据文件名推测"）
- **key_modules**：5-10 个最重要文件，每个一句话
- **architecture_summary**：2-3 段，架构取向和核心设计决策
- **entry_points**：主要入口文件路径
"""


class ReconScoutV2Router(Router):
    """V2 侦察节点 (AgentNodeLoop) — grep 驱动自适应探索。

    工具集：local_list / local_grep / local_read。
    策略：搜索→发现→读取→更新认知→再搜索，循环直到形成清晰能力图谱。
    不依赖固定读取顺序，由 Agent 自主决定下一步搜什么、读什么。
    ≤30 文件，将 self_portrait 携带到输出供 IntersectionPlanner 使用。
    """

    DESCRIPTION = (
        "V2 侦察：AgentNodeLoop，local_grep 驱动自适应发现，"
        "搜索→读取→更新认知循环，≤30 文件，产出能力图谱"
    )
    FORMAT_IN = "absorption.request"
    FORMAT_OUT = "absorption.recon.map"

    def __init__(self, **kwargs: Any) -> None:
        self._recon_sess_id: str | None = None
        self._role = kwargs.get("role", "runtime_main")

    def _build_agentloop(self) -> Any:
        """延迟导入并构造 AgentNodeLoop。避免模块级循环 import。"""
        from omnicompany.runtime.agent.agent_node_loop import AgentNodeLoop
        from omnicompany.runtime.agent.agent_loop_config import (
            CompactConfig,
            LoopConfig,
            PermissionConfig,
        )

        class _ReconLoop(AgentNodeLoop):
            DESCRIPTION = ReconScoutV2Router.DESCRIPTION
            FORMAT_IN = "absorption.request"
            FORMAT_OUT = "absorption.recon.map"
            SYSTEM_PROMPT: ClassVar[str] = _RECON_SYSTEM_PROMPT
            LOOP_CONFIG: ClassVar[LoopConfig] = LoopConfig(
                max_turns=40,
                compact=CompactConfig(
                    auto_compact_enabled=True,
                    auto_compact_threshold=0.80,
                ),
                permission=PermissionConfig(mode="readonly"),
            )
            TOOLS: ClassVar[list] = []

            def __init__(self_inner, outer_router: "ReconScoutV2Router", **kw: Any) -> None:
                kw.setdefault("role", outer_router._role)
                super().__init__(**kw)
                self_inner._outer = outer_router

            def build_initial_messages(self_inner, input_data: dict) -> list[dict]:
                from omnicompany.runtime.llm.llm import LLMClient

                repo_name = input_data.get("repo_name", "unknown")
                repo_local_path = input_data.get("repo_local_path", "")
                self_portrait = input_data.get("self_portrait", "")

                if not repo_local_path:
                    raise ValueError("ReconScoutV2: input 缺少 repo_local_path")

                sess_id = _next_recon_sess_id(self_inner)
                self_inner._outer._recon_sess_id = sess_id
                _new_recon_session(
                    sess_id,
                    repo_local_path=repo_local_path,
                    repo_name=repo_name,
                    self_portrait=self_portrait,
                    upstream_input=dict(input_data),
                )

                # 绑定工具
                from omnicompany.runtime.agent.agent_loop_tools import FinishTool
                bound_tools = _make_recon_tools(sess_id)
                if not any(t.name == "finish" for t in bound_tools):
                    bound_tools.append(FinishTool)
                self_inner._tools = bound_tools
                self_inner._tool_map = {t.name: t for t in self_inner._tools}
                tools_spec = [t.to_api_spec() for t in self_inner._tools]
                role = self_inner._outer._role
                self_inner._llm = LLMClient(role=role, tools=tools_spec)
                self_inner._llm_no_tools = LLMClient(role=role, tools=[])

                # 根目录预览（供 LLM 快速参考）
                try:
                    root_entries = sorted(Path(repo_local_path).iterdir())
                    root_preview = "\n".join(
                        f"  {'[DIR] ' if e.is_dir() else '      '}{e.name}"
                        for e in root_entries[:60]
                    )
                except Exception:
                    root_preview = "(无法预览根目录)"

                user_msg = f"""# Repo 侦察任务

**Repo**: {repo_name}
**路径**: {repo_local_path}

## 根目录（快速参考）

```
{root_preview}
```

## OmniCompany 自画像（G1-G7 已知缺口）

{self_portrait}

---

开始侦察。先读 README.md，然后用 local_grep 和 local_list 主动探索——
每次读完都 think 一次，更新你对这个 repo 的认知，决定下一步搜什么或读什么。
满足条件后调 submit_recon_map，再调 finish。"""

                return [{"role": "user", "content": user_msg}]

            def extract_result(self_inner, final_text: str, messages: list[dict]) -> Verdict:
                sess_id = self_inner._outer._recon_sess_id
                if not sess_id:
                    return Verdict(
                        kind=VerdictKind.FAIL,
                        output={},
                        diagnosis="ReconScoutV2: no session id (init failed)",
                    )
                state = _RECON_SESSION_STATE.pop(sess_id, None)
                self_inner._outer._recon_sess_id = None
                if state is None:
                    return Verdict(
                        kind=VerdictKind.FAIL,
                        output={},
                        diagnosis="ReconScoutV2: session state lost",
                    )
                recon_map = state.get("recon_map")
                files_read = state.get("read_files", [])
                upstream = state.get("upstream_input", {})
                repo_name = state.get("repo_name", "unknown")
                self_portrait = state.get("self_portrait", "")

                base_output = {
                    **upstream,
                    "repo_name": repo_name,
                    "files_read": files_read,
                    "self_portrait": self_portrait,
                    "recon_finish_summary": final_text,
                }

                if not recon_map:
                    return Verdict(
                        kind=VerdictKind.PARTIAL,
                        output={
                            **base_output,
                            "capability_map": {},
                            "key_modules": [],
                            "architecture_summary": (
                                final_text[:500] if final_text else "[LLM未调用submit_recon_map]"
                            ),
                            "entry_points": [],
                        },
                        confidence=0.2,
                        diagnosis=(
                            f"ReconScoutV2 结束但未调 submit_recon_map，"
                            f"读取了 {len(files_read)} 个文件"
                        ),
                    )

                output = {
                    **base_output,
                    "capability_map": recon_map.get("capability_map", {}),
                    "key_modules": recon_map.get("key_modules", []),
                    "architecture_summary": recon_map.get("architecture_summary", ""),
                    "entry_points": recon_map.get("entry_points", []),
                }
                return Verdict(
                    kind=VerdictKind.PASS,
                    output=output,
                    confidence=0.85,
                    diagnosis=(
                        f"ReconScoutV2: {len(recon_map.get('capability_map', {}))} 功能域, "
                        f"{len(recon_map.get('key_modules', []))} 关键模块, "
                        f"读取 {len(files_read)} 个文件"
                    ),
                    granted_tags=["domain.absorption", "stage.v2.recon"],
                )

        return _ReconLoop(self)

    async def run(self, input_data: Any) -> Verdict:  # type: ignore[override]
        loop = self._build_agentloop()
        return await loop.run(input_data)


class IntersectionPlannerV2Router(Router):
    """V2 问题清单规划节点 (LLM) — Phase 2 实现。

    单次 LLM 调用，对比 OmniCompany 自画像缺口（G1-G7）vs 侦察图谱，
    输出绑定到 G1-G7 的优先化问题清单（最多 20 条）。
    """

    DESCRIPTION = (
        "V2 交集规划：LLM 对比自画像缺口(G1-G7) vs 侦察图谱，"
        "输出绑定 G1-G7 的优先化问题清单（最多20条，含 expected_location）"
    )
    FORMAT_IN = "absorption.recon.map"
    FORMAT_OUT = "absorption.question-list"

    _MODEL = "qwen3.6-plus"

    _SYSTEM = """你是 OmniCompany 的能力分析师，负责将外部 repo 的能力与 OmniCompany 的已知缺口对应。

你会收到：
1. OmniCompany 的**自画像**（G1-G7 已知缺口）
2. 对外部 repo 的**侦察图谱**（capability_map + key_modules + architecture_summary）

你的任务：生成一份**优先化问题清单**，每条问题必须：
- 绑定到 G1-G7 中的一个缺口（gap_id）
- 问具体实现细节（不是"有没有"，而是"怎么做的/机制是什么"）
- 指出在哪个文件/模块可能找到答案（expected_location）

优先级规则：
- P0: 该 repo 的特色功能，OmniCompany 完全缺失，直接可参考
- P1: 该 repo 有更好的实现方式，值得学习
- P2: 次要参考，或此 repo 与该缺口关联度低

若某个缺口在这个 repo 里完全不涉及，设 skip_reason 说明理由（仍需列出，优先级 P2）。

**输出格式**：纯 JSON 对象，无 markdown 代码块包装，无其他文字：
{
  "repo_name": "...",
  "questions": [
    {
      "id": "Q1",
      "text": "具体问题文本（30字以上）",
      "gap_id": "G1",
      "priority": "P0",
      "expected_location": "packages/core/scheduler.ts:198-411 或目录名",
      "skip_reason": ""
    }
  ]
}

每个 repo 最多 20 个问题，按优先级 P0→P1→P2 排序。"""

    def __init__(self, *, model: str | None = None, **kwargs: Any) -> None:
        self._model = model or self._MODEL

    def run(self, input_data: Any) -> Verdict:
        repo_name = input_data.get("repo_name", "unknown")
        self_portrait = input_data.get("self_portrait", "")
        capability_map = input_data.get("capability_map") or {}
        key_modules = input_data.get("key_modules") or []
        architecture_summary = input_data.get("architecture_summary", "")

        if not self_portrait:
            return Verdict(
                kind=VerdictKind.FAIL,
                output=input_data,
                diagnosis="IntersectionPlannerV2: self_portrait 为空，无法生成问题清单",
            )

        cap_map_str = "\n".join(
            f"  **{domain}**: {desc}"
            for domain, desc in capability_map.items()
        )
        modules_str = "\n".join(
            f"  - `{m.get('path', '?')}`: {m.get('description', '')}"
            for m in key_modules[:10]
        )

        user_msg = f"""# 问题清单规划

## Repo: {repo_name}

## OmniCompany 自画像（G1-G7 缺口）

{self_portrait}

## 侦察图谱

### 功能域（capability_map）
{cap_map_str if cap_map_str else "(侦察未产出功能域)"}

### 关键模块（key_modules）
{modules_str if modules_str else "(侦察未产出关键模块)"}

### 架构摘要
{architecture_summary or "(侦察未产出架构摘要)"}

---

请生成针对此 repo 的优先化问题清单（最多 20 条），每条绑定 G1-G7，JSON 格式输出，无其他文字。"""

        try:
            from omnicompany.runtime.llm.llm import LLMClient
            client = LLMClient(model=self._model)
            resp = client.call(
                messages=[{"role": "user", "content": user_msg}],
                system=self._SYSTEM,
            )
            raw = resp.content[0].text.strip()
            if raw.startswith("```"):
                raw = re.sub(r"^```[a-z]*\n?", "", raw)
                raw = re.sub(r"\n?```$", "", raw.strip())
            data = json.loads(raw)
            questions = data.get("questions") or []
            p0_count = sum(1 for q in questions if q.get("priority") == "P0")
            return Verdict(
                kind=VerdictKind.PASS,
                confidence=0.9,
                output={
                    **input_data,
                    "repo_name": repo_name,
                    "questions": questions,
                },
                diagnosis=(
                    f"IntersectionPlannerV2: {len(questions)} 个问题，"
                    f"{p0_count} 个 P0"
                ),
            )
        except Exception as e:
            return Verdict(
                kind=VerdictKind.FAIL,
                output=dict(input_data),
                diagnosis=f"IntersectionPlannerV2 LLM 调用失败: {type(e).__name__}: {e}",
            )


class HumanApprovalGateV2Router(Router):
    """V2 人工审核门 (RULE passthrough) — Phase 1 auto passthrough。

    将 question-list 写出到 data/absorption/<repo>/pending_questions.md，
    等待人工编辑后读回（Phase 1 直接 passthrough）。
    """

    DESCRIPTION = "V2 人工门：将问题清单写出等待人工审核，Phase 1 为 auto passthrough，不阻塞"
    FORMAT_IN = "absorption.question-list"
    FORMAT_OUT = "absorption.question-list.approved"

    def run(self, input_data):
        import datetime as _dt
        repo_name = input_data.get("repo_name", "unknown")
        questions = input_data.get("questions", [])
        return Verdict(
            kind=VerdictKind.PASS,
            confidence=1.0,
            output={
                **input_data,  # 透传所有上游数据（repo_local_path 等）
                "repo_name": repo_name,
                "questions": questions,
                "approved_at": _dt.datetime.now().isoformat(),
                "reviewer": "auto-passthrough-phase1",
            },
            diagnosis=f"HumanApprovalGate passthrough: {len(questions)} 个问题直接通过",
        )


# ── V2 DirectedReader 会话状态 ──────────────────────────────

_DIRECTED_SESSION_STATE: dict[str, dict] = {}
_DIRECTED_SESS_LOCK = threading.Lock()
_DIRECTED_SESS_COUNTER = 0


def _next_directed_sess_id(router: Any) -> str:
    global _DIRECTED_SESS_COUNTER
    with _DIRECTED_SESS_LOCK:
        _DIRECTED_SESS_COUNTER += 1
        return f"directed-v2-{id(router)}-{_DIRECTED_SESS_COUNTER}"


def _new_directed_session(
    sess_id: str,
    repo_local_path: str,
    repo_name: str,
    questions: list,
    upstream_input: dict,
) -> dict:
    state: dict = {
        "repo_local_path": repo_local_path,
        "repo_name": repo_name,
        "questions": questions,
        "upstream_input": upstream_input,
        "read_files": [],         # 已读的不重复文件路径列表
        "submitted_answers": {},  # question_id → answer dict
    }
    _DIRECTED_SESSION_STATE[sess_id] = state
    return state


def _make_directed_tools(sess_id: str) -> list:
    """为 DirectedReader 会话构建工具列表（闭包绑定 sess_id）。"""
    from omnicompany.runtime.agent.agent_loop_tools import (
        FinishTool,
        ThinkTool,
        ToolDefinition,
    )

    _MAX_FILES = 15

    def _state() -> dict:
        return _DIRECTED_SESSION_STATE[sess_id]

    # ── local_list ────────────────────────────────────────
    LocalListTool = ToolDefinition(
        name="local_list",
        description=(
            "List files and subdirectories at a path within the local repo. "
            "Returns JSON with items array of {name, type, path, size}. "
            "type is 'file' or 'dir'. Use path='' for repo root, 'src/core' for subdir. "
            "Non-recursive — call multiple times to drill down."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Relative path within repo (empty string for root)",
                    "default": "",
                },
            },
            "required": [],
        },
        is_concurrency_safe=True,
        is_readonly=True,
    )

    def _local_list_call(args: dict, executor: Any, ctx: Any) -> str:
        repo_root = Path(_state()["repo_local_path"])
        rel = (args.get("path") or "").strip("/\\").strip()
        target = repo_root / rel if rel else repo_root
        if not target.exists():
            return f"Error: path '{rel or '.'}' does not exist"
        if not target.is_dir():
            return f"Error: '{rel or '.'}' is not a directory"
        items = []
        try:
            for entry in sorted(target.iterdir()):
                rel_path = str(entry.relative_to(repo_root)).replace("\\", "/")
                items.append({
                    "name": entry.name,
                    "type": "dir" if entry.is_dir() else "file",
                    "path": rel_path,
                    "size": entry.stat().st_size if entry.is_file() else 0,
                })
        except PermissionError as e:
            return f"Error: permission denied listing {target}: {e}"
        return json.dumps({"path": rel or ".", "items": items}, ensure_ascii=False)

    LocalListTool.call = _local_list_call  # type: ignore[assignment]

    # ── local_read ────────────────────────────────────────
    LocalReadTool = ToolDefinition(
        name="local_read",
        description=(
            "Read the content of a file from the local repo. Returns line-numbered content. "
            "Use path relative to repo root (e.g., 'README.md', 'src/core/agent.py'). "
            "Use offset and limit to read specific sections of large files. "
            f"BUDGET: at most {_MAX_FILES} distinct files total across all questions. "
            "Current budget is shown as [FILES READ: N/15] in every response. "
            "When budget is nearly exhausted, submit remaining answers before reading more."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Relative path within repo",
                },
                "offset": {
                    "type": "integer",
                    "default": 0,
                    "minimum": 0,
                    "description": "Line number to start reading from (0-based)",
                },
                "limit": {
                    "type": "integer",
                    "default": 400,
                    "minimum": 10,
                    "maximum": 1000,
                    "description": "Max lines to read (default 400, max 1000)",
                },
            },
            "required": ["path"],
        },
        is_concurrency_safe=True,
        is_readonly=True,
    )

    def _local_read_call(args: dict, executor: Any, ctx: Any) -> str:
        state = _state()
        repo_root = Path(state["repo_local_path"])
        rel = (args.get("path") or "").strip("/\\")
        read_log = state["read_files"]
        is_new = rel not in read_log
        if is_new and len(read_log) >= _MAX_FILES:
            answered = len(state["submitted_answers"])
            total_q = len(state["questions"])
            pending = [
                q.get("id") for q in state["questions"]
                if q.get("id") not in state["submitted_answers"]
            ]
            return (
                f"ERROR: file budget exhausted ({_MAX_FILES}/{_MAX_FILES} distinct files read). "
                f"Submit answers for remaining {total_q - answered} questions "
                f"({pending}) based on what you already read, or mark as not_found. "
                f"Call submit_answer for each, then finish."
            )
        target = repo_root / rel
        if not target.exists():
            return f"Error: file '{rel}' not found in repo"
        if not target.is_file():
            return f"Error: '{rel}' is a directory, not a file"
        size = target.stat().st_size
        if size > 1024 * 1024:
            return f"Error: file too large ({size} bytes); max 1MB"
        try:
            content = target.read_text(encoding="utf-8", errors="replace")
        except Exception as e:
            return f"Error reading {rel}: {e}"
        lines = content.split("\n")
        total = len(lines)
        offset = int(args.get("offset") or 0)
        limit = int(args.get("limit") or 400)
        start = min(offset, total)
        end = min(start + limit, total)
        segment = lines[start:end]
        numbered = "\n".join(f"{i + 1:5d}\t{line}" for i, line in enumerate(segment, start=start))
        if is_new:
            read_log.append(rel)
        budget_note = f"[FILES READ: {len(read_log)}/{_MAX_FILES}]"
        header = f"=== {rel} (total {total} lines, showing {start + 1}-{end}) {budget_note} ===\n"
        return header + numbered

    LocalReadTool.call = _local_read_call  # type: ignore[assignment]

    # ── submit_answer ─────────────────────────────────────
    SubmitAnswerTool = ToolDefinition(
        name="submit_answer",
        description=(
            "Submit your answer for one question. Call once per question ID. "
            "status='answered': clear evidence found in code. "
            "status='partial': partial evidence, picture incomplete. "
            "status='not_found': searched relevant files, no evidence found. "
            "Evidence must cite files you actually read in this session. "
            "You MUST call submit_answer for EVERY question before calling finish."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "question_id": {
                    "type": "string",
                    "description": "The question ID, e.g. 'Q1' or 'Q3'",
                },
                "status": {
                    "type": "string",
                    "enum": ["answered", "partial", "not_found"],
                },
                "answer": {
                    "type": "string",
                    "description": "Answer text (required for answered/partial; omit for not_found)",
                    "default": "",
                },
                "evidence": {
                    "type": "array",
                    "description": "Citations from files you actually read",
                    "default": [],
                    "items": {
                        "type": "object",
                        "properties": {
                            "file": {"type": "string", "description": "Relative file path"},
                            "lines": {"type": "string", "description": "Line range e.g. '45-67'"},
                            "quote": {"type": "string", "description": "Short relevant quote"},
                        },
                        "required": ["file"],
                    },
                },
                "confidence": {
                    "type": "number",
                    "minimum": 0.0,
                    "maximum": 1.0,
                    "default": 0.5,
                },
            },
            "required": ["question_id", "status"],
        },
        is_concurrency_safe=False,
        is_readonly=True,
    )

    def _submit_answer_call(args: dict, executor: Any, ctx: Any) -> str:
        state = _state()
        q_id = (args.get("question_id") or "").strip()
        valid_ids = {q.get("id", "") for q in state["questions"]}
        if q_id not in valid_ids:
            return f"Error: unknown question_id '{q_id}'. Valid IDs: {sorted(valid_ids)}"
        status = args.get("status", "not_found")
        if status not in ("answered", "partial", "not_found"):
            return f"Error: invalid status '{status}'. Must be answered/partial/not_found"
        # Validate evidence cites only files we've actually read
        read_set = set(state["read_files"])
        for ev in (args.get("evidence") or []):
            ev_file = ev.get("file", "")
            if ev_file and ev_file not in read_set:
                return (
                    f"Error: evidence cites '{ev_file}' which was not read in this session. "
                    f"Only cite from: {sorted(read_set)}"
                )
        state["submitted_answers"][q_id] = {
            "question_id": q_id,
            "status": status,
            "answer": args.get("answer") or "",
            "evidence": args.get("evidence") or [],
            "confidence": float(args.get("confidence") or 0.5),
        }
        pending = [
            q.get("id") for q in state["questions"]
            if q.get("id") not in state["submitted_answers"]
        ]
        if pending:
            return f"Answer recorded for {q_id} (status={status}). Still pending: {pending}"
        return f"Answer recorded for {q_id}. ALL {len(state['questions'])} questions answered — call finish."

    SubmitAnswerTool.call = _submit_answer_call  # type: ignore[assignment]

    # ── local_grep（共享实现，闭包绑定 DirectedReader state）────────
    DirectedGrepTool = ToolDefinition(
        name="local_grep",
        description=(
            "Search file contents across the repo using a regex pattern. "
            "Returns matching lines with file:line format. "
            "Use before local_read to find the right file/line without guessing. "
            "glob_pattern filters files (e.g. '*.py', '*.ts'). max_results default 80."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "pattern": {"type": "string", "description": "Regex pattern"},
                "glob_pattern": {"type": "string", "default": "",
                                 "description": "File glob filter, e.g. '*.py'"},
                "max_results": {"type": "integer", "default": 80,
                                "minimum": 1, "maximum": 200},
            },
            "required": ["pattern"],
        },
        is_concurrency_safe=True,
        is_readonly=True,
    )

    def _directed_grep_call(args: dict, executor: Any, ctx: Any) -> str:
        import subprocess as _sp
        repo_root = Path(_state()["repo_local_path"])
        pattern = args.get("pattern", "")
        if not pattern:
            return "Error: pattern is required"
        glob = (args.get("glob_pattern") or "").strip()
        max_results = int(args.get("max_results") or 80)

        cmd = ["rg", "--line-number", "--no-heading", "--color=never",
               "--max-count=5", pattern]
        if glob:
            cmd += ["--glob", glob]
        cmd.append(".")

        try:
            result = _sp.run(
                cmd, cwd=str(repo_root),
                capture_output=True, text=True, encoding="utf-8",
                errors="replace", timeout=30,
            )
            lines = result.stdout.splitlines()
        except FileNotFoundError:
            import re as _re
            import fnmatch as _fn
            lines = []
            try:
                rx = _re.compile(pattern)
            except _re.error as e:
                return f"Error: invalid regex: {e}"
            for fpath in sorted(repo_root.rglob("*")):
                if not fpath.is_file():
                    continue
                if glob and not _fn.fnmatch(fpath.name, glob.lstrip("**/").lstrip("**\\")):
                    continue
                try:
                    for i, line in enumerate(
                        fpath.read_text(encoding="utf-8", errors="replace").splitlines(), 1
                    ):
                        if rx.search(line):
                            rel = str(fpath.relative_to(repo_root)).replace("\\", "/")
                            lines.append(f"{rel}:{i}:{line}")
                            if len(lines) >= max_results:
                                break
                except Exception:
                    continue
                if len(lines) >= max_results:
                    break
        except Exception as e:
            return f"Error: {e}"

        if not lines:
            return f"No matches for '{pattern}'"
        truncated = lines[:max_results]
        note = f" (first {max_results})" if len(lines) > max_results else ""
        return f"=== grep '{pattern}' — {len(truncated)} matches{note} ===\n" + "\n".join(truncated)

    DirectedGrepTool.call = _directed_grep_call  # type: ignore[assignment]

    return [LocalListTool, LocalReadTool, DirectedGrepTool, SubmitAnswerTool, ThinkTool, FinishTool]


# ── V2 DirectedReader 系统提示 ──────────────────────────────

_DIRECTED_READER_SYSTEM_PROMPT = """你是 OmniCompany 的定向深读专家，负责通过阅读源码回答关于外部 repo 的具体技术问题。

## 你的任务

你会收到一份**优先化问题清单**（P0/P1/P2），每条问题：
- 绑定了 OmniCompany 的一个能力缺口（G1-G7）
- 有预测的代码位置（expected_location）

你需要通过读 repo 源码，逐一提交答案和证据。

## 工作流程

1. **先 think**：对前几个 P0 问题，规划搜索和读取路径
2. **用 local_grep 定位**：不要猜文件名，先搜索关键词找到正确文件和行号
   - 例：`local_grep "def _calculate_summary"` 比猜 compressor.py 第几行更快
3. **按 P0→P1→P2 顺序**：优先回答高优先级问题
4. **找到即提交**：每回答完一个问题立即 submit_answer
5. **所有问题必须提交**：找不到也要 submit_answer(status='not_found')
6. **最后调 finish**

## 文件预算硬限制

- 整个会话（所有问题合计）最多读 **15 个不同文件**
- local_read 每次都会显示 `[FILES READ: N/15]` 提醒进度
- 预算耗尽后 local_read 会报错，此时必须为剩余问题提交 not_found 再 finish
- 每个问题控制在 2-4 个文件内，不要为一个问题耗尽全部预算

## 答案质量

- **answered**：必须引用具体文件路径 + 行范围 + 关键代码片段
- **partial**：说清楚找到什么、缺什么
- **not_found**：说明找过了哪里、为何没找到

禁止猜测或编造。答案必须来自实际读到的代码。
"""


class DirectedReaderV2Router(Router):
    """V2 定向深读节点 (AgentNodeLoop) — Phase 3 实现。

    接收 IntersectionPlanner 输出的优先化问题清单（P0/P1/P2），
    用本地文件工具按 expected_location 提示逐一回答，
    ≤15 文件预算，所有问题必须提交答案（answered/partial/not_found）。
    """

    DESCRIPTION = (
        "V2 定向深读：AgentNodeLoop 按 P0→P1→P2 读 ≤15 文件，"
        "每问题用 expected_location 提示定向搜索，逐一提交带证据的答案"
    )
    FORMAT_IN = "absorption.question-list.approved"
    FORMAT_OUT = "absorption.question.answer"

    def __init__(self, **kwargs: Any) -> None:
        self._directed_sess_id: str | None = None
        self._role = kwargs.get("role", "runtime_main")

    def _build_agentloop(self) -> Any:
        from omnicompany.runtime.agent.agent_node_loop import AgentNodeLoop
        from omnicompany.runtime.agent.agent_loop_config import (
            CompactConfig,
            LoopConfig,
            PermissionConfig,
        )

        class _DirectedReaderLoop(AgentNodeLoop):
            DESCRIPTION = DirectedReaderV2Router.DESCRIPTION
            FORMAT_IN = "absorption.question-list.approved"
            FORMAT_OUT = "absorption.question.answer"
            SYSTEM_PROMPT: ClassVar[str] = _DIRECTED_READER_SYSTEM_PROMPT
            LOOP_CONFIG: ClassVar[LoopConfig] = LoopConfig(
                max_turns=100,
                compact=CompactConfig(
                    auto_compact_enabled=True,
                    auto_compact_threshold=0.80,
                ),
                permission=PermissionConfig(mode="readonly"),
            )
            TOOLS: ClassVar[list] = []

            def __init__(self_inner, outer_router: "DirectedReaderV2Router", **kw: Any) -> None:
                kw.setdefault("role", outer_router._role)
                super().__init__(**kw)
                self_inner._outer = outer_router

            def build_initial_messages(self_inner, input_data: dict) -> list[dict]:
                from omnicompany.runtime.llm.llm import LLMClient

                repo_name = input_data.get("repo_name", "unknown")
                repo_local_path = input_data.get("repo_local_path", "")
                questions = input_data.get("questions") or []
                capability_map = input_data.get("capability_map") or {}
                key_modules = input_data.get("key_modules") or []

                if not repo_local_path:
                    raise ValueError("DirectedReaderV2: input 缺少 repo_local_path")
                if not questions:
                    raise ValueError("DirectedReaderV2: input 缺少 questions")

                sess_id = _next_directed_sess_id(self_inner)
                self_inner._outer._directed_sess_id = sess_id
                _new_directed_session(
                    sess_id,
                    repo_local_path=repo_local_path,
                    repo_name=repo_name,
                    questions=questions,
                    upstream_input=dict(input_data),
                )

                # 绑定工具
                from omnicompany.runtime.agent.agent_loop_tools import FinishTool
                bound_tools = _make_directed_tools(sess_id)
                if not any(t.name == "finish" for t in bound_tools):
                    bound_tools.append(FinishTool)
                self_inner._tools = bound_tools
                self_inner._tool_map = {t.name: t for t in self_inner._tools}
                tools_spec = [t.to_api_spec() for t in self_inner._tools]
                role = self_inner._outer._role
                self_inner._llm = LLMClient(role=role, tools=tools_spec)
                self_inner._llm_no_tools = LLMClient(role=role, tools=[])

                # 格式化问题清单，P0→P1→P2 排序
                sorted_qs = sorted(
                    questions,
                    key=lambda q: {"P0": 0, "P1": 1, "P2": 2}.get(q.get("priority", "P2"), 9),
                )
                q_lines = []
                for q in sorted_qs:
                    q_lines.append(
                        f"**{q.get('id', '?')}** [{q.get('priority', '?')}] "
                        f"[缺口: {q.get('gap_id', '?')}]\n"
                        f"{q.get('text', '')}\n"
                        f"  → 预测位置: {q.get('expected_location', '(未指定)')}"
                    )
                q_block = "\n\n".join(q_lines)

                # 侦察图谱摘要
                cap_lines = "\n".join(
                    f"  {domain}: {desc}"
                    for domain, desc in list(capability_map.items())[:8]
                )
                mod_lines = "\n".join(
                    f"  {m.get('path', '?')}: {m.get('description', '')}"
                    for m in key_modules[:8]
                )

                # 根目录预览
                try:
                    root_entries = sorted(Path(repo_local_path).iterdir())
                    root_preview = "\n".join(
                        f"  {'[DIR] ' if e.is_dir() else '      '}{e.name}"
                        for e in root_entries[:40]
                    )
                except Exception:
                    root_preview = "(无法预览根目录)"

                user_msg = f"""# 定向深读任务

**Repo**: {repo_name}
**本地路径**: {repo_local_path}
**问题总数**: {len(questions)} 个
**文件预算**: 最多 15 个不同文件（跨所有问题合计）

## 问题清单（P0→P1→P2）

{q_block}

## 侦察图谱（来自 ReconScout，供参考）

### 功能域
{cap_lines or "(无)"}

### 关键模块
{mod_lines or "(无)"}

### 根目录结构
```
{root_preview}
```

---

## 指令

1. **先 think**：看完问题清单，规划 P0 问题的读取路径（每问题 2-4 个文件）
2. 按顺序处理每个问题：读文件 → 找证据 → 立即 submit_answer
3. 文件预算耗尽后，对剩余问题提交 not_found
4. 所有 {len(questions)} 个问题都提交完毕后，调 finish

开始吧，先处理 P0 问题。"""

                return [{"role": "user", "content": user_msg}]

            def extract_result(self_inner, final_text: str, messages: list[dict]) -> Verdict:
                sess_id = self_inner._outer._directed_sess_id
                if not sess_id:
                    return Verdict(
                        kind=VerdictKind.FAIL,
                        output={},
                        diagnosis="DirectedReaderV2: no session id (init failed)",
                    )
                state = _DIRECTED_SESSION_STATE.pop(sess_id, None)
                self_inner._outer._directed_sess_id = None
                if state is None:
                    return Verdict(
                        kind=VerdictKind.FAIL,
                        output={},
                        diagnosis="DirectedReaderV2: session state lost",
                    )

                questions = state.get("questions", [])
                submitted = state.get("submitted_answers", {})
                read_files = state.get("read_files", [])
                upstream = state.get("upstream_input", {})
                repo_name = state.get("repo_name", "unknown")

                # 为未提交的问题补充 not_found
                answers = []
                for q in questions:
                    q_id = q.get("id", "?")
                    if q_id in submitted:
                        rec = submitted[q_id]
                        answers.append({
                            "repo_name": repo_name,
                            "question_id": q_id,
                            "question_text": q.get("text", ""),
                            "gap_id": q.get("gap_id", ""),
                            "priority": q.get("priority", "P2"),
                            "status": rec["status"],
                            "answer": rec.get("answer", ""),
                            "evidence": rec.get("evidence", []),
                            "confidence": rec.get("confidence", 0.0),
                        })
                    else:
                        answers.append({
                            "repo_name": repo_name,
                            "question_id": q_id,
                            "question_text": q.get("text", ""),
                            "gap_id": q.get("gap_id", ""),
                            "priority": q.get("priority", "P2"),
                            "status": "not_found",
                            "answer": "[session ended without submitting this question]",
                            "evidence": [],
                            "confidence": 0.0,
                        })

                n_answered = sum(1 for a in answers if a["status"] == "answered")
                n_partial = sum(1 for a in answers if a["status"] == "partial")
                n_not_found = sum(1 for a in answers if a["status"] == "not_found")
                p0_total = sum(1 for a in answers if a["priority"] == "P0")
                p0_covered = sum(
                    1 for a in answers
                    if a["priority"] == "P0" and a["status"] in ("answered", "partial")
                )

                kind = VerdictKind.PASS
                if n_answered + n_partial == 0:
                    kind = VerdictKind.PARTIAL
                elif p0_total > 0 and p0_covered == 0:
                    kind = VerdictKind.PARTIAL

                return Verdict(
                    kind=kind,
                    output={
                        **upstream,
                        "repo_name": repo_name,
                        "answers": answers,
                        "total_questions": len(questions),
                        "n_answered": n_answered,
                        "n_partial": n_partial,
                        "n_not_found": n_not_found,
                        "files_read": read_files,
                    },
                    confidence=round(
                        (n_answered + n_partial * 0.5) / max(len(questions), 1), 2
                    ),
                    diagnosis=(
                        f"DirectedReaderV2: {len(questions)} 问 → "
                        f"{n_answered} answered / {n_partial} partial / {n_not_found} not_found, "
                        f"P0: {p0_covered}/{p0_total}, "
                        f"读取 {len(read_files)} 个文件"
                    ),
                    granted_tags=["domain.absorption", "stage.v2.directed"],
                )

        return _DirectedReaderLoop(self)

    async def run(self, input_data: Any) -> Verdict:  # type: ignore[override]
        loop = self._build_agentloop()
        return await loop.run(input_data)


class CoverageAuditorV2Router(Router):
    """V2 覆盖审计节点 (RULE) — Phase 4 实现。

    聚合 DirectedReader 的答案，按 G1-G7 和优先级统计覆盖情况，
    计算 coverage_score 供 Synthesis 和 ReportWriter 使用。
    """

    DESCRIPTION = (
        "V2 覆盖审计：RULE 节点，按 gap_id(G1-G7) 和优先级聚合答案，"
        "计算 coverage_score = (answered + partial×0.5) / total"
    )
    FORMAT_IN = "absorption.question.answer"
    FORMAT_OUT = "absorption.audit"

    def run(self, input_data):
        repo_name = input_data.get("repo_name", "unknown")
        answers = input_data.get("answers", [])

        # ── 按 gap_id 聚合 ──────────────────────────────────
        by_gap: dict[str, dict] = {}
        for a in answers:
            g = a.get("gap_id") or "unknown"
            if g not in by_gap:
                by_gap[g] = {
                    "gap_id": g,
                    "answered": 0,
                    "partial": 0,
                    "not_found": 0,
                    "questions": [],
                }
            status = a.get("status", "not_found")
            by_gap[g][status] = by_gap[g].get(status, 0) + 1
            by_gap[g]["questions"].append({
                "id": a.get("question_id", "?"),
                "priority": a.get("priority", "P2"),
                "status": status,
            })

        # ── 全局统计 ────────────────────────────────────────
        n_total = len(answers)
        n_answered = sum(1 for a in answers if a.get("status") == "answered")
        n_partial = sum(1 for a in answers if a.get("status") == "partial")
        n_not_found = sum(1 for a in answers if a.get("status") == "not_found")
        coverage_score = round((n_answered + n_partial * 0.5) / max(n_total, 1), 2)

        # ── P0 专项 ────────────────────────────────────────
        p0_answers = [a for a in answers if a.get("priority") == "P0"]
        p0_covered = sum(
            1 for a in p0_answers if a.get("status") in ("answered", "partial")
        )

        covered_gaps = sorted(
            g for g, v in by_gap.items() if v["answered"] + v["partial"] > 0
        )
        uncovered_gaps = sorted(
            g for g, v in by_gap.items() if v["answered"] + v["partial"] == 0
        )

        return Verdict(
            kind=VerdictKind.PASS,
            confidence=coverage_score,
            output={
                **input_data,
                "repo_name": repo_name,
                "answered_count": n_answered,
                "partial_count": n_partial,
                "not_found_count": n_not_found,
                "total_count": n_total,
                "coverage_score": coverage_score,
                "p0_covered": p0_covered,
                "p0_total": len(p0_answers),
                "by_gap": by_gap,
                "covered_gaps": covered_gaps,
                "uncovered_gaps": uncovered_gaps,
            },
            diagnosis=(
                f"CoverageAuditorV2: score={coverage_score:.2f} "
                f"({n_answered} answered / {n_partial} partial / {n_not_found} not_found), "
                f"P0: {p0_covered}/{len(p0_answers)}, "
                f"covered gaps: {covered_gaps}"
            ),
        )


class SynthesisV2Router(Router):
    """V2 综合分析节点 (LLM) — Phase 4 实现。

    单次 LLM 调用，将所有 Q&A 答案按 G1-G7 组织，产出：
    - gap_analysis: 每个缺口的发现摘要
    - highlights: 对 OmniCompany 最有价值的 5-10 条行动项
    - architecture_overview: 目标 repo 架构概述
    - overall_assessment: 整体评估（值得吸纳的程度 + 优先方向）
    """

    DESCRIPTION = (
        "V2 综合分析：单次 LLM 调用，将所有 Q&A 按 G1-G7 聚合，"
        "产出 gap_analysis + highlights + architecture_overview + overall_assessment"
    )
    FORMAT_IN = "absorption.audit"
    FORMAT_OUT = "absorption.synthesis"

    _MODEL = "qwen3.6-plus"

    _SYSTEM = """你是 OmniCompany 的知识综合专家。你会收到对一个外部 repo 的定向问答记录，
按照 OmniCompany 的七个已知缺口（G1-G7）组织。

你的任务：将这些问答合成为一份结构化的洞察报告，供工程师直接参考并决策"要从这个 repo 学什么"。

**输出格式**：纯 JSON，无 markdown 代码块，无其他文字：
{
  "gap_analysis": {
    "G1": {
      "gap_name": "G1 缺口的名称",
      "finding": "这个 repo 在该缺口方面做了什么（1-3句话）",
      "actionability": "high/medium/low/none",
      "key_files": ["最相关的文件路径"],
      "verdict": "directly_reusable / worth_learning / reference_only / not_applicable"
    }
  },
  "highlights": [
    {
      "gap_id": "G1",
      "title": "简短标题（≤20字）",
      "finding": "具体发现（引用证据）",
      "action": "OmniCompany 应该做什么（具体行动，不是模糊建议）",
      "priority": "P0/P1/P2"
    }
  ],
  "architecture_overview": "2-3段，描述这个 repo 的整体架构和设计理念",
  "overall_assessment": {
    "absorption_value": "high/medium/low",
    "top_priority_gaps": ["G1", "G2"],
    "summary": "1-2句话总结：值不值得深入吸纳，最重要的收获是什么"
  }
}

highlights 最多 10 条，按优先级 P0→P1→P2 排序。
只包含有实际证据支持的发现，没有证据就不写进 highlights。"""

    def __init__(self, *, model: str | None = None, **kwargs: Any) -> None:
        self._model = model or self._MODEL

    def run(self, input_data: Any) -> Verdict:
        repo_name = input_data.get("repo_name", "unknown")
        answers = input_data.get("answers") or []
        by_gap = input_data.get("by_gap") or {}
        coverage_score = input_data.get("coverage_score", 0.0)
        capability_map = input_data.get("capability_map") or {}
        architecture_summary = input_data.get("architecture_summary", "")

        if not answers:
            return Verdict(
                kind=VerdictKind.FAIL,
                output=dict(input_data),
                diagnosis="SynthesisV2: answers 为空，无法综合",
            )

        # ── 按 gap_id 组织答案文本 ──────────────────────────
        gap_sections: list[str] = []
        # Collect all unique gap_ids from answers, sort G1-G7 first
        all_gaps = sorted(set(a.get("gap_id", "?") for a in answers))

        for gap_id in all_gaps:
            gap_answers = [a for a in answers if a.get("gap_id") == gap_id]
            gap_sections.append(f"\n### {gap_id}")
            for a in gap_answers:
                q_id = a.get("question_id", "?")
                priority = a.get("priority", "P2")
                status = a.get("status", "not_found")
                text = a.get("question_text", "")
                answer = a.get("answer", "")
                evidence = a.get("evidence") or []
                ev_str = ""
                if evidence:
                    ev_parts = [
                        f"`{ev.get('file', '?')}` L{ev.get('lines', '?')}: {ev.get('quote', '')[:80]}"
                        for ev in evidence[:3]
                    ]
                    ev_str = "\n  证据: " + " | ".join(ev_parts)
                gap_sections.append(
                    f"**{q_id}** [{priority}] [{status}]\n"
                    f"问: {text}\n"
                    f"答: {answer[:400] if answer else '(未找到)'}{ev_str}"
                )

        cap_str = "\n".join(f"  {k}: {v}" for k, v in list(capability_map.items())[:6])

        user_msg = f"""# 综合分析任务

**Repo**: {repo_name}
**覆盖率**: {coverage_score:.0%} ({input_data.get('answered_count', 0)} answered / {input_data.get('partial_count', 0)} partial / {input_data.get('not_found_count', 0)} not_found)

## 侦察图谱（架构背景）

{architecture_summary or "(无)"}

### 功能域
{cap_str or "(无)"}

## Q&A 记录（按 G1-G7 分组）
{"".join(gap_sections)}

---

请基于以上内容，生成综合分析 JSON（gap_analysis + highlights + architecture_overview + overall_assessment）。
只引用有答案支撑的内容，not_found 的问题不计入 highlights。"""

        try:
            from omnicompany.runtime.llm.llm import LLMClient
            client = LLMClient(model=self._model)
            resp = client.call(
                messages=[{"role": "user", "content": user_msg}],
                system=self._SYSTEM,
            )
            raw = resp.content[0].text.strip()
            if raw.startswith("```"):
                raw = re.sub(r"^```[a-z]*\n?", "", raw)
                raw = re.sub(r"\n?```$", "", raw.strip())
            data = json.loads(raw)
            highlights = data.get("highlights") or []
            n_p0 = sum(1 for h in highlights if h.get("priority") == "P0")
            return Verdict(
                kind=VerdictKind.PASS,
                confidence=0.85,
                output={
                    **input_data,
                    "repo_name": repo_name,
                    "gap_analysis": data.get("gap_analysis") or {},
                    "highlights": highlights,
                    "architecture_overview": data.get("architecture_overview", ""),
                    "overall_assessment": data.get("overall_assessment") or {},
                },
                diagnosis=(
                    f"SynthesisV2: {len(highlights)} 亮点（{n_p0} P0），"
                    f"absorption_value={data.get('overall_assessment', {}).get('absorption_value', '?')}"
                ),
            )
        except Exception as e:
            return Verdict(
                kind=VerdictKind.FAIL,
                output=dict(input_data),
                diagnosis=f"SynthesisV2 LLM 调用失败: {type(e).__name__}: {e}",
            )


class ReportWriterV2Router(Router):
    """V2 报告写入节点 (RULE) — Phase 4 实现。

    将 Synthesis 产出渲染为人类可读的 markdown 报告，
    写入 data/absorption/<repo_name>/<date>/report.md，
    并更新 data/absorption/<repo_name>/.omni/manifest.yaml。
    """

    DESCRIPTION = (
        "V2 报告写入：RULE 节点，将 Synthesis 产物渲染为 markdown report.md，"
        "按 G1-G7 分节，并更新 manifest.yaml 的 absorption_status"
    )
    FORMAT_IN = "absorption.synthesis"
    FORMAT_OUT = "absorption.report.v2"

    def _manifest_path(self, repo_name: str) -> Path:
        return _absorption_artifact_dir() / repo_name / ".omni" / "manifest.yaml"

    def _update_manifest(self, repo_name: str, report_path: Path, coverage_score: float) -> str:
        """更新 manifest.yaml 中的 absorption_status 和最后分析日期。"""
        mpath = self._manifest_path(repo_name)
        if not mpath.exists():
            return f"(manifest not found at {mpath})"
        try:
            text = mpath.read_text(encoding="utf-8")
            # 替换或追加 absorption_status
            if "absorption_status:" in text:
                text = re.sub(r"absorption_status:.*", "absorption_status: analyzed", text)
            else:
                text += "\nabsorption_status: analyzed\n"
            # 替换或追加 last_analysis_date
            today = datetime.date.today().isoformat()
            if "last_analysis_date:" in text:
                text = re.sub(r"last_analysis_date:.*", f"last_analysis_date: {today}", text)
            else:
                text += f"last_analysis_date: {today}\n"
            # 写回
            mpath.write_text(text, encoding="utf-8")
            return str(mpath)
        except Exception as e:
            return f"(manifest update failed: {e})"

    def _render_highlights(self, highlights: list) -> str:
        if not highlights:
            return "_（无有效亮点 — 所有问题均未找到答案）_\n"
        lines = []
        for h in highlights:
            gap = h.get("gap_id", "?")
            priority = h.get("priority", "P2")
            title = h.get("title", "")
            finding = h.get("finding", "")
            action = h.get("action", "")
            lines.append(f"### [{priority}] {title} `{gap}`\n")
            lines.append(f"{finding}\n")
            if action:
                lines.append(f"**→ 行动**: {action}\n")
            lines.append("")
        return "\n".join(lines)

    def _render_gap_analysis(self, gap_analysis: dict, by_gap: dict) -> str:
        lines = []
        gap_order = ["G1", "G2", "G3", "G4", "G5", "G6", "G7"]
        all_gaps = sorted(
            gap_analysis.keys(),
            key=lambda g: gap_order.index(g) if g in gap_order else 99,
        )
        for gap_id in all_gaps:
            info = gap_analysis[gap_id]
            verdict = info.get("verdict", "?")
            actionability = info.get("actionability", "?")
            lines.append(f"### {gap_id} — {info.get('gap_name', gap_id)}")
            lines.append(f"**可操作性**: {actionability} · **评定**: `{verdict}`\n")
            lines.append(f"{info.get('finding', '（无发现）')}\n")
            key_files = info.get("key_files") or []
            if key_files:
                lines.append("**关键文件**:")
                for f in key_files:
                    lines.append(f"- `{f}`")
                lines.append("")
            # Q&A 明细
            gap_qs = by_gap.get(gap_id, {}).get("questions", [])
            if gap_qs:
                summary = ", ".join(
                    f"{q['id']}={q['status']}" for q in gap_qs
                )
                lines.append(f"_问题: {summary}_\n")
            lines.append("")
        return "\n".join(lines)

    def _render_qa_details(self, answers: list) -> str:
        if not answers:
            return "_（无答案记录）_\n"
        lines = []
        for a in sorted(answers, key=lambda x: x.get("question_id", "")):
            status = a.get("status", "not_found")
            icon = {"answered": "✅", "partial": "🔶", "not_found": "❌"}.get(status, "?")
            lines.append(
                f"**{a.get('question_id', '?')}** {icon} "
                f"[{a.get('priority', '?')}] [{a.get('gap_id', '?')}]  "
                f"_{a.get('question_text', '')}_\n"
            )
            if a.get("answer"):
                lines.append(f"> {a['answer'][:500]}\n")
            for ev in (a.get("evidence") or [])[:2]:
                lines.append(
                    f"  📁 `{ev.get('file', '?')}` L{ev.get('lines', '?')} "
                    f"— _{ev.get('quote', '')[:80]}_\n"
                )
            lines.append("")
        return "\n".join(lines)

    def run(self, input_data: Any) -> Verdict:
        import datetime as _dt

        repo_name = input_data.get("repo_name", "unknown")
        gap_analysis = input_data.get("gap_analysis") or {}
        highlights = input_data.get("highlights") or []
        architecture_overview = input_data.get("architecture_overview", "")
        overall_assessment = input_data.get("overall_assessment") or {}
        answers = input_data.get("answers") or []
        by_gap = input_data.get("by_gap") or {}
        coverage_score = input_data.get("coverage_score", 0.0)
        n_answered = input_data.get("answered_count", 0)
        n_partial = input_data.get("partial_count", 0)
        n_not_found = input_data.get("not_found_count", 0)
        files_read = input_data.get("files_read") or []

        date_str = _dt.date.today().isoformat()
        report_dir = _absorption_artifact_dir() / repo_name / date_str
        report_dir.mkdir(parents=True, exist_ok=True)
        report_path = report_dir / "report.md"

        absorption_value = overall_assessment.get("absorption_value", "?")
        top_gaps = overall_assessment.get("top_priority_gaps") or []
        summary = overall_assessment.get("summary", "")

        # ── 构建 markdown ────────────────────────────────
        md: list[str] = [
            f"# Absorption V2 Report: {repo_name}",
            "",
            f"**生成时间**: {_dt.datetime.utcnow().isoformat()}Z  ",
            f"**覆盖率**: {coverage_score:.0%} "
            f"({n_answered} answered / {n_partial} partial / {n_not_found} not_found)  ",
            f"**读取文件数**: {len(files_read)}  ",
            f"**吸纳价值**: `{absorption_value}`  ",
            f"**优先缺口**: {', '.join(top_gaps) or '(无)'}",
            "",
            "---",
            "",
            "## TL;DR",
            "",
            summary or "_(Synthesis 未产出总结)_",
            "",
            "---",
            "",
            "## 架构概述",
            "",
            architecture_overview or "_(侦察未产出架构摘要)_",
            "",
            "---",
            "",
            "## 行动亮点",
            "",
            self._render_highlights(highlights),
            "---",
            "",
            "## 缺口分析（G1-G7）",
            "",
            self._render_gap_analysis(gap_analysis, by_gap),
            "---",
            "",
            "## Q&A 明细",
            "",
            self._render_qa_details(answers),
            "---",
            "",
            "## 读取文件清单",
            "",
        ]
        for f in files_read:
            md.append(f"- `{f}`")
        if not files_read:
            md.append("_(无)_")
        md.append("")
        md.append("---")
        md.append("")
        md.append("_此报告由 OmniCompany Absorption V2 管线自动生成。_")

        report_path.write_text("\n".join(md), encoding="utf-8")

        # ── 更新 manifest ────────────────────────────────
        manifest_path = self._update_manifest(repo_name, report_path, coverage_score)

        logger.info("[absorption.v2.report_writer] 报告已写盘: %s", report_path)

        return Verdict(
            kind=VerdictKind.PASS,
            confidence=coverage_score,
            output={
                "repo_name": repo_name,
                "report_path": str(report_path),
                "manifest_path": manifest_path,
                "coverage_score": coverage_score,
                "answered_count": n_answered,
                "total_count": n_answered + n_partial + n_not_found,
                "absorption_value": absorption_value,
            },
            diagnosis=(
                f"ReportWriterV2: report 写到 {report_path}, "
                f"manifest={manifest_path}, "
                f"coverage={coverage_score:.0%}"
            ),
            granted_tags=["domain.absorption", "stage.v2.reported"],
        )
