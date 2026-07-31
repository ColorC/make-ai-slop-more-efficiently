# [OMNI] origin=claude-code domain=services/registry ts=2026-04-11T00:00:00Z
# [OMNI] material_id="material:core.registry.incremental_diagnosis.git_diff_driver.py"
"""
增量诊断 — 基于 git diff 识别变更文件，只对受影响 Router/Format 重跑诊断，追加病历本。

设计：
  1. git diff --name-only [base]  → 变更文件列表
  2. scan_file() 每个变更 .py 文件  → 更新 InstanceRegistry + 得到 entity_id 列表
  3. 按类型分组 → router targets / format targets
  4. 运行各自的 Doctor 5 节点链（Router）/ hard diagnosis（Format）
  5. 结果自动写入 HealthArchive（Doctor 内部已集成）

用法（命令行）：
    cd omnicompany
    python -m omnicompany.packages.services._core.registry.incremental
    python -m omnicompany.packages.services._core.registry.incremental --base HEAD~1
    python -m omnicompany.packages.services._core.registry.incremental --base main

用法（API）：
    from omnicompany.packages.services._core.registry.incremental import run_incremental_diagnosis
    results = run_incremental_diagnosis()
    results = run_incremental_diagnosis(base="HEAD~1")
"""
from __future__ import annotations

import logging
import subprocess
from pathlib import Path

log = logging.getLogger(__name__)

# ── 默认路径（与 __init__.py 对齐）──────────────────────────────────────────
_THIS_FILE = Path(__file__)
# scanner.py / incremental.py → registry → services → packages → omnicompany (src) → src → repo root
_DEFAULT_SOURCE_ROOT = _THIS_FILE.parents[3]       # src/omnicompany
_DEFAULT_REPO_ROOT   = _THIS_FILE.parents[5]        # omnicompany repo root


# ── git 工具 ─────────────────────────────────────────────────────────────────

def get_changed_files(
    base: str = "HEAD",
    repo_root: Path | None = None,
) -> list[Path]:
    """返回相对于 base 的变更文件列表（绝对路径，仅存在的 .py 文件）。

    base 可以是：
      "HEAD"        — 未提交的工作区改动（相对于最新 commit）
      "HEAD~1"      — 最近一次提交引入的改动
      "abc1234"     — 任意 commit hash
      "main"        — 相对于 main 分支
    """
    cwd = repo_root or _DEFAULT_REPO_ROOT
    try:
        # git diff --name-only base 获取工作区改动
        r = subprocess.run(
            ["git", "diff", "--name-only", base],
            capture_output=True, text=True, timeout=10,
            cwd=str(cwd),
        )
        lines = r.stdout.strip().splitlines() if r.returncode == 0 else []

        # 也获取 staged 改动（已 add 未 commit）
        r2 = subprocess.run(
            ["git", "diff", "--name-only", "--cached", base],
            capture_output=True, text=True, timeout=10,
            cwd=str(cwd),
        )
        lines2 = r2.stdout.strip().splitlines() if r2.returncode == 0 else []

        seen: set[str] = set()
        result: list[Path] = []
        for rel in lines + lines2:
            rel = rel.strip()
            if not rel or rel in seen:
                continue
            seen.add(rel)
            abs_path = cwd / rel
            if abs_path.exists() and abs_path.suffix == ".py":
                result.append(abs_path)

        return result
    except Exception as e:
        log.warning("get_changed_files failed: %s", e)
        return []


def get_changed_files_in_commit(
    commit: str = "HEAD",
    repo_root: Path | None = None,
) -> list[Path]:
    """返回某次 commit 引入的变更文件（用 git show --name-only）。

    比 get_changed_files(base="HEAD~1") 更精确：只看那次 commit，不含工作区改动。
    """
    cwd = repo_root or _DEFAULT_REPO_ROOT
    try:
        r = subprocess.run(
            ["git", "show", "--name-only", "--format=", commit],
            capture_output=True, text=True, timeout=10,
            cwd=str(cwd),
        )
        result: list[Path] = []
        for rel in r.stdout.strip().splitlines():
            rel = rel.strip()
            if not rel:
                continue
            abs_path = cwd / rel
            if abs_path.exists() and abs_path.suffix == ".py":
                result.append(abs_path)
        return result
    except Exception as e:
        log.warning("get_changed_files_in_commit failed: %s", e)
        return []


# ── 变更文件 → 诊断目标 ──────────────────────────────────────────────────────

def _classify_changed_files(
    changed_files: list[Path],
    source_root: Path,
) -> tuple[list[dict], list[str]]:
    """将变更文件分类为 router targets 和 format_ids。

    Returns:
        router_targets: [{"router_class": str, "source_file": str, "entity_id": str}]
        format_ids:     [format_id_str, ...]  (e.g. "demogame.table_schema")
    """
    from .scanner import scan_file
    from . import get_registry

    registry = get_registry()

    router_targets: list[dict] = []
    format_ids: list[str] = []
    seen_entities: set[str] = set()

    for py_file in changed_files:
        # 只处理 source_root 内的文件
        try:
            py_file.relative_to(source_root)
        except ValueError:
            log.debug("skip (outside source_root): %s", py_file)
            continue

        try:
            updated_eids = scan_file(py_file, source_root, registry)
        except Exception as e:
            log.warning("scan_file failed for %s: %s", py_file, e)
            updated_eids = []

        for eid in updated_eids:
            if eid in seen_entities:
                continue
            seen_entities.add(eid)

            entry = registry.read(eid)
            if entry is None:
                continue

            if entry.type == "router":
                # source_file stored relative to source_root.parent (= src/)
                sf = entry.source_file
                abs_sf = (
                    sf if Path(sf).is_absolute()
                    else str((source_root.parent / sf).resolve())
                )
                router_targets.append({
                    "entity_id": eid,
                    "router_class": entry.name,
                    "source_file": abs_sf,
                })
            elif entry.type == "format":
                format_ids.append(entry.name)  # format_id = entry.name for formats

    return router_targets, format_ids


# ── Router 诊断 ──────────────────────────────────────────────────────────────

def _diagnose_router(
    router_class: str,
    source_file: str,
    source_root: str,
    run_llm: bool = False,
) -> dict:
    """对单个 Router 运行诊断链，返回 health_record。

    通过 TeamRunner + SQLiteBus 执行，所有节点 I/O 均记录到事件总线。
    run_llm=False：确定性 5 节点链（快速，结构合规性）
    run_llm=True ：+ RouterContextualAuditRouter（语义质量，需 LLM API key）
    """
    from omnicompany.packages.services._diagnosis.doctor.run import run_router_diagnosis
    return run_router_diagnosis(router_class, source_file, source_root, run_llm=run_llm)


# ── Format 诊断 ──────────────────────────────────────────────────────────────

def _diagnose_format(format_id: str, source_root: str) -> dict:
    """对单个 Format 运行确定性诊断链，返回 health_record。

    通过 TeamRunner + SQLiteBus 执行，所有节点 I/O 均记录到事件总线。
    """
    from omnicompany.packages.services._diagnosis.doctor.run import _run_hard_diagnosis
    return _run_hard_diagnosis(format_id, source_root)


# ── 主入口 ────────────────────────────────────────────────────────────────────

def _already_diagnosed(entity_id: str, commit_hash: str, archive_dir: Path,
                        require_llm: bool = False) -> bool:
    """如果该 entity 在当前 commit 已有完整快照，返回 True（跳过重复诊断）。

    跳过条件：
    - 最新 JSONL 快照的 commit_hash == 当前 HEAD
    - require_llm=True 时，还需快照含 llm_audit 字段（说明已跑 LLM 语义审计）
    """
    if not commit_hash:
        return False
    import json
    import re
    safe = re.sub(r"[:/\\]", "_", entity_id) + ".jsonl"
    etype = entity_id.split(":")[0]
    path = archive_dir / etype / safe
    if not path.exists():
        return False
    lines = [l for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]
    if not lines:
        return False
    snap = json.loads(lines[-1])
    if snap.get("commit_hash") != commit_hash:
        return False
    if require_llm and not snap.get("llm_audit"):
        return False
    return True


def run_incremental_diagnosis(
    base: str = "HEAD",
    source_root: Path | str | None = None,
    repo_root: Path | str | None = None,
    run_llm: bool = False,
    skip_existing: bool = True,
    verbose: bool = False,
) -> dict:
    """增量诊断主入口。

    Args:
        base:        git diff 基准（默认 "HEAD"，即诊断未提交的工作区改动）
        source_root: omnicompany 源码根（默认 src/omnicompany）
        repo_root:   git repo 根（默认自动检测）
        run_llm:     是否运行 LLM 语义审计（默认关闭，走纯确定性节点）
        verbose:     是否输出详细日志

    Returns:
        {
            "changed_files": [str, ...],
            "router_results": [{"entity_id", "router_class", "grade", "score", "error"}, ...],
            "format_results": [{"format_id", "grade", "score", "error"}, ...],
            "summary": {"router_count": N, "format_count": N, "grades": {...}},
        }
    """
    src_root = Path(source_root) if source_root else _DEFAULT_SOURCE_ROOT
    repo = Path(repo_root) if repo_root else _DEFAULT_REPO_ROOT

    # ── 1. 获取变更文件 ──────────────────────────────────────────────────────
    changed = get_changed_files(base, repo)
    if verbose:
        print(f"[incremental] git diff --name-only {base}: {len(changed)} .py files")
        for f in changed:
            print(f"  {f}")

    if not changed:
        return {
            "changed_files": [],
            "router_results": [],
            "format_results": [],
            "summary": {"router_count": 0, "format_count": 0, "grades": {}},
        }

    # ── 2. 分类变更文件 → 诊断目标 ───────────────────────────────────────────
    router_targets, format_ids = _classify_changed_files(changed, src_root)
    if verbose:
        print(f"[incremental] targets: {len(router_targets)} routers, {len(format_ids)} formats")

    # ── 2.5 获取当前 commit hash（用于跳过已诊断实体）───────────────────────
    _archive_dir = _DEFAULT_REPO_ROOT / "data" / "registry" / "health"
    _current_commit = ""
    if skip_existing:
        try:
            import subprocess as _sp
            _r = _sp.run(["git", "rev-parse", "--short", "HEAD"],
                         capture_output=True, text=True, timeout=5, cwd=str(repo))
            _current_commit = _r.stdout.strip() if _r.returncode == 0 else ""
        except Exception:
            pass

    # ── 3. 逐个诊断 ──────────────────────────────────────────────────────────
    router_results: list[dict] = []
    format_results: list[dict] = []
    # 契约变更 #02 (2026-04-25): 废 grade_counts, 改 verdict_counts (healthy/unhealthy/uncertain)
    verdict_counts: dict[str, int] = {}

    for rt in router_targets:
        cls = rt["router_class"]
        eid = rt["entity_id"]
        # 跳过：当前 commit 已有快照（且含 LLM 审计）
        if skip_existing and _already_diagnosed(eid, _current_commit, _archive_dir,
                                                   require_llm=run_llm):
            if verbose:
                print(f"  [skip] {cls:40s} 已在 commit {_current_commit} 诊断过")
            continue
        try:
            health = _diagnose_router(cls, rt["source_file"], str(src_root), run_llm=run_llm)
            # 契约变更 #02 (2026-04-25): 读 v2 字段 verdict + counts, 不兼容 v1
            verdict = health.get("verdict", "uncertain")
            counts = health.get("counts", {})
            if verbose:
                print(f"  router {cls:40s} {verdict}  "
                      f"crit={counts.get('critical',0)} major={counts.get('major',0)} minor={counts.get('minor',0)}")
            router_results.append({
                "entity_id": rt["entity_id"],
                "router_class": cls,
                "verdict": verdict,
                "counts": counts,
                "error": None,
            })
            verdict_counts[verdict] = verdict_counts.get(verdict, 0) + 1
        except Exception as e:
            log.warning("Router diagnosis failed for %s: %s", cls, e)
            router_results.append({
                "entity_id": rt["entity_id"],
                "router_class": cls,
                "verdict": "uncertain",
                "counts": {},
                "error": str(e),
            })

    for fmt_id in format_ids:
        fmt_eid = f"format:{fmt_id}"
        if skip_existing and _already_diagnosed(fmt_eid, _current_commit, _archive_dir,
                                                   require_llm=run_llm):
            if verbose:
                print(f"  [skip] {fmt_id:40s} 已在 commit {_current_commit} 诊断过")
            continue
        try:
            health = _diagnose_format(fmt_id, str(src_root))
            verdict = health.get("verdict", "uncertain")
            counts = health.get("counts", {})
            if verbose:
                print(f"  format {fmt_id:40s} {verdict}  "
                      f"crit={counts.get('critical',0)} major={counts.get('major',0)} minor={counts.get('minor',0)}")
            format_results.append({
                "format_id": fmt_id,
                "verdict": verdict,
                "counts": counts,
                "error": None,
            })
            verdict_counts[verdict] = verdict_counts.get(verdict, 0) + 1
        except Exception as e:
            log.warning("Format diagnosis failed for %s: %s", fmt_id, e)
            format_results.append({
                "format_id": fmt_id,
                "verdict": "uncertain",
                "counts": {},
                "error": str(e),
            })

    return {
        "changed_files": [str(f) for f in changed],
        "router_results": router_results,
        "format_results": format_results,
        "summary": {
            "router_count": len(router_results),
            "format_count": len(format_results),
            "verdicts": verdict_counts,
        },
    }


# ── CLI ──────────────────────────────────────────────────────────────────────

def _print_table(results: dict) -> None:
    router_results = results["router_results"]
    format_results = results["format_results"]
    summary = results["summary"]

    if not router_results and not format_results:
        print("没有检测到需要重诊断的 Router 或 Format（变更范围外或无变更）。")
        return

    # 契约变更 #02 (2026-04-25): 显示 verdict + counts, 不显示 grade/score
    def _fmt_counts(c: dict) -> str:
        return f"crit={c.get('critical',0)} major={c.get('major',0)} minor={c.get('minor',0)}"

    verdict_sym = {
        "healthy": "[OK ]", "unhealthy": "[BAD]", "uncertain": "[~~ ]",
    }

    if router_results:
        print(f"\n{'Router':45s} {'verdict':^8}  {'counts'}")
        print("-" * 80)
        for r in sorted(router_results, key=lambda x: x.get("verdict", "uncertain")):
            v = r.get("verdict", "uncertain")
            err = f"  ERROR: {r['error'][:50]}" if r.get("error") else ""
            print(f"{r['router_class']:<45} {verdict_sym.get(v, v):^8}  {_fmt_counts(r.get('counts', {}))}{err}")

    if format_results:
        print(f"\n{'Format':45s} {'verdict':^8}  {'counts'}")
        print("-" * 80)
        for r in sorted(format_results, key=lambda x: x.get("verdict", "uncertain")):
            v = r.get("verdict", "uncertain")
            err = f"  ERROR: {r['error'][:50]}" if r.get("error") else ""
            print(f"{r['format_id']:<45} {verdict_sym.get(v, v):^8}  {_fmt_counts(r.get('counts', {}))}{err}")

    total = summary["router_count"] + summary["format_count"]
    verdict_str = "  ".join(f"{v}×{n}" for v, n in sorted(summary.get("verdicts", {}).items()))
    print(f"\n总计 {total} 个实体重诊断  |  {verdict_str}")
    print("健康快照已追加到 data/registry/health/")


if __name__ == "__main__":
    import argparse
    import sys

    sys.path.insert(0, str(_THIS_FILE.parents[4]))  # ensure src/ on path

    # 加载 .env（THE_COMPANY_API_KEY 等），避免 LLM 调用因 key 缺失回落到 "no-key"
    try:
        from dotenv import load_dotenv as _load_dotenv
        _load_dotenv()
    except ImportError:
        pass

    parser = argparse.ArgumentParser(description="增量诊断：只对 git diff 涉及的 Router/Format 重跑")
    parser.add_argument(
        "--base", default="HEAD",
        help="git diff 基准（默认 HEAD，即未提交改动）",
    )
    parser.add_argument(
        "--source-root", default=None,
        help="omnicompany 源码根目录（默认 src/omnicompany）",
    )
    parser.add_argument(
        "--llm", action="store_true",
        help="开启 LLM 语义审计（RouterContextualAuditRouter），需 THE_COMPANY_API_KEY",
    )
    parser.add_argument(
        "--no-skip", action="store_true",
        help="强制重新诊断（忽略已有快照），默认跳过当前 commit 已诊断的实体",
    )
    parser.add_argument(
        "--verbose", "-v", action="store_true",
        help="显示详细输出",
    )
    args = parser.parse_args()

    results = run_incremental_diagnosis(
        base=args.base,
        source_root=args.source_root,
        run_llm=args.llm,
        skip_existing=not args.no_skip,
        verbose=args.verbose,
    )
    _print_table(results)
