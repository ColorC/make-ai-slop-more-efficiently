"""OmniPatrol Runner — 可周期触发或手动 CLI 调用的巡逻入口

Phase 1: 只观测，所有违规以 warn 输出（log + 返回结构）。不修改任何文件。
Phase 2+: 接入 OmniTow，根据 disposition 执行 quarantine/stamp 等动作。

两种扫描模式：
  - diff 模式（默认）：只扫描 git diff 和工作树变更文件
  - full 模式（--full）：扫描整个 src/ 目录的所有 .py 文件
"""
# [OMNI] origin=omnifactory domain=omnifactory/guardian ts=2026-04-05T00:00:00Z
# [OMNI] material_id="material:services.guardian.patrol_runner_legacy.git_diff_scan.archive.py"

from __future__ import annotations

import logging
import subprocess
from pathlib import Path
from typing import Optional

# NOTE: 已归档 (2026-04-20), 不再被活代码 import。相对 import 改为 `.patrol_legacy`。
from .patrol_legacy import FileContext, RuleEngine, parse_omnimark

logger = logging.getLogger(__name__)

_DEFAULT_ROOT = Path("e:/WindowsWorkspace/omnifactory")


# ─── Git 变更文件收集 ──────────────────────────────────────────


def _git_committed_changes(root: Path, n_commits: int = 1) -> list[tuple[str, str]]:
    """返回最近 n_commits 个 commit 引入的文件变更 [(change_type, rel_path)]。"""
    try:
        out = subprocess.check_output(
            ["git", "diff", "--name-status", f"HEAD~{n_commits}", "HEAD"],
            cwd=str(root),
            text=True,
            stderr=subprocess.DEVNULL,
        )
        results = []
        for line in out.splitlines():
            parts = line.split("\t", 1)
            if len(parts) == 2:
                results.append((parts[0][:1], parts[1].strip()))
        return results
    except subprocess.CalledProcessError:
        return []
    except FileNotFoundError:
        logger.warning("git not found in PATH")
        return []


def _git_uncommitted_changes(root: Path) -> list[tuple[str, str]]:
    """返回工作树和暂存区的未 commit 变更 [(change_type, rel_path)]。"""
    try:
        out = subprocess.check_output(
            ["git", "status", "--porcelain"],
            cwd=str(root),
            text=True,
            stderr=subprocess.DEVNULL,
        )
        results = []
        for line in out.splitlines():
            if len(line) < 4:
                continue
            status = line[:2].strip()
            path = line[3:].strip()
            # 处理重命名（格式：old -> new）
            if " -> " in path:
                path = path.split(" -> ", 1)[1]
            change_type = status[0] if status else "M"
            if change_type == "?":
                change_type = "A"  # 未跟踪文件视为新增
            results.append((change_type, path))
        return results
    except subprocess.CalledProcessError:
        return []
    except FileNotFoundError:
        return []


def _git_staged_changes(root: Path) -> list[tuple[str, str]]:
    """返回 staged （index 中）的变更 [(change_type, rel_path)]。

    用于 pre-commit hook：只扫即将进入下一次 commit 的文件，不碰未暂存的脏文件。
    """
    try:
        out = subprocess.check_output(
            ["git", "diff", "--cached", "--name-status"],
            cwd=str(root),
            text=True,
            stderr=subprocess.DEVNULL,
        )
        results = []
        for line in out.splitlines():
            parts = line.split("\t")
            if len(parts) < 2:
                continue
            status = parts[0][0]   # A / M / D / R / C
            path = parts[-1]        # rename 时取目标路径
            results.append((status, path))
        return results
    except subprocess.CalledProcessError:
        return []
    except FileNotFoundError:
        return []


def _load_file_ctx(
    root: Path, change_type: str, rel_path: str
) -> Optional[FileContext]:
    """加载单个文件，构建 FileContext。删除的文件 content=None。"""
    # 统一路径分隔符
    rel_path_norm = rel_path.replace("\\", "/")
    abs_path = root / rel_path_norm

    content: Optional[str] = None
    if change_type != "D" and abs_path.exists() and abs_path.is_file():
        try:
            content = abs_path.read_text(encoding="utf-8", errors="replace")
        except Exception:
            pass

    omnimark = parse_omnimark(content) if content else None
    return FileContext(
        path=rel_path_norm,
        abs_path=str(abs_path),
        change_type=change_type,
        content=content,
        omnimark=omnimark,
    )


# ─── 全量扫描 ──────────────────────────────────────────────────


def _full_src_scan(root: Path) -> list[FileContext]:
    """扫描 src/ + scripts/ 下的所有 .py 文件及 data/ 下的 .db 文件。"""
    files: list[FileContext] = []

    # Python 文件（src/ + scripts/）
    for search_dir in [root / "src", root / "scripts"]:
        if not search_dir.exists():
            continue
        for p in search_dir.rglob("*"):
            if not p.is_file():
                continue
            if "__pycache__" in p.parts:
                continue
            # 全量扫描时非 .py 文件只检查 OMNI-005~007（走 change_type=M）
            rel = str(p.relative_to(root)).replace("\\", "/")
            try:
                content = p.read_text(encoding="utf-8", errors="replace") if p.suffix == ".py" else None
            except Exception:
                content = None
            omnimark = parse_omnimark(content) if content else None
            files.append(FileContext(
                path=rel,
                abs_path=str(p),
                change_type="M",
                content=content,
                omnimark=omnimark,
            ))

    # data/ 下的 .db 文件（用于 OMNI-005 全量检查 .db 散落）
    data_dir = root / "data"
    if data_dir.exists():
        for p in data_dir.rglob("*.db"):
            rel = str(p.relative_to(root)).replace("\\", "/")
            files.append(FileContext(
                path=rel,
                abs_path=str(p),
                change_type="M",
                content=None,
                omnimark=None,
            ))
        # data/ 的直接子目录(用于 OMNI-021 drawer-subdir-drift)
        # 每个子目录以 "data/<name>/" 形式送进去
        for p in data_dir.iterdir():
            if p.is_dir():
                rel = f"data/{p.name}/"
                files.append(FileContext(
                    path=rel,
                    abs_path=str(p),
                    change_type="M",
                    content=None,
                    omnimark=None,
                ))

    # 仓库根层的直接子文件（用于 OMNI-015 禁区检查）
    # 不递归，只看 root 下的非目录直接文件
    _SKIP_ROOT_FILES = frozenset({
        "pyproject.toml", "poetry.lock", "uv.lock", "requirements.txt",
        ".gitignore", ".gitattributes", "README.md", "LICENSE", "CHANGELOG.md",
        ".python-version", "pytest.ini", "mypy.ini", ".pre-commit-config.yaml",
    })
    try:
        for p in root.iterdir():
            # 直接子文件
            if p.is_file():
                if p.name in _SKIP_ROOT_FILES:
                    continue
                if p.name.startswith("."):   # .env / .envrc 等隐藏文件不扫
                    continue
                files.append(FileContext(
                    path=p.name,
                    abs_path=str(p),
                    change_type="M",
                    content=None,
                    omnimark=None,
                ))
            # 直接子目录 — 只用于 OMNI-015 的 forbidden_root_dirs 检查
            # path 以 '/' 结尾标识这是一个目录条目
            elif p.is_dir():
                if p.name.startswith("."):  # .git / .omni / .claude 等隐藏目录合法
                    continue
                files.append(FileContext(
                    path=p.name + "/",
                    abs_path=str(p),
                    change_type="M",
                    content=None,
                    omnimark=None,
                ))
    except OSError:
        pass

    return files


# ─── 主入口 ────────────────────────────────────────────────────


def run_patrol(
    project_root: str | Path = _DEFAULT_ROOT,
    full_scan: bool = False,
    committed: bool = True,
    uncommitted: bool = True,
    n_commits: int = 1,
    use_llm: bool = False,
    llm_new_only: bool = True,
    llm_pilot_paths: tuple[str, ...] | None = None,
    use_agent: bool = False,
    auto_tow: bool = True,
    tow_phase2: bool = False,
    staged_only: bool = False,
    since_ts: Optional[str] = None,
) -> dict:
    """运行一次 OmniPatrol 巡逻。

    Args:
        project_root:     项目根目录
        full_scan:        True → 扫描整个 src/，忽略 git diff
        committed:        是否扫描最近已 commit 的变更
        uncommitted:      是否扫描未 commit 的工作树变更
        n_commits:        committed=True 时，回溯 commit 数量
        use_llm:          是否启用 LLM Judge（旧接口，默认 False）
        llm_new_only:     LLM 只检查新增文件（change_type=A）
        llm_pilot_paths:  LLM Judge 的试点路径
        use_agent:        是否启用 GuardianAgent 对疑似违规做智能复核（默认 False）
        auto_tow:         是否自动将违规交给 OmniTow 处置
        tow_phase2:       OmniTow 是否启用 Phase 2 动作

    Returns:
        {
            "scan_ts": ISO 时间戳,
            "scan_mode": "full" | "diff",
            "files_scanned": int,
            "violations_found": int,
            "agent_reviewed": int,          # Agent 复核的候选数
            "violations": [...],
            "by_severity": {...},
        }
    """
    from datetime import datetime, timezone

    root = Path(project_root)
    now = datetime.now(timezone.utc).isoformat()

    # ─── 文件收集 ──────────────────────────────────────────────
    if full_scan:
        files = _full_src_scan(root)
        scan_mode = "full"
    elif staged_only:
        # pre-commit hook 专用：只看 git index 里的文件
        changed = _git_staged_changes(root)
        scan_mode = "staged"
        files = [
            ctx
            for ct, path in changed
            if (ctx := _load_file_ctx(root, ct, path)) is not None
        ]
    else:
        changed: list[tuple[str, str]] = []
        if committed:
            changed.extend(_git_committed_changes(root, n_commits))
        if uncommitted:
            changed.extend(_git_uncommitted_changes(root))
        # 去重
        seen: set[str] = set()
        unique: list[tuple[str, str]] = []
        for ct, path in changed:
            if path not in seen:
                seen.add(path)
                unique.append((ct, path))
        files = [
            ctx
            for ct, path in unique
            if (ctx := _load_file_ctx(root, ct, path)) is not None
        ]
        scan_mode = "diff"

    # ─── 增量过滤 (since_ts): 只保留 mtime > since_ts 的文件 ────
    # Sentinel daemon 每次触发 patrol 传入 since_ts = 上次 patrol 时间,
    # 这样只扫增量内容. 目录条目 (path 以 / 结尾) 和 stat 失败的永远保留.
    if since_ts:
        try:
            from datetime import datetime as _dt
            cutoff = _dt.fromisoformat(str(since_ts).replace("Z", "+00:00"))
            cutoff_epoch = cutoff.timestamp()
            files_before = len(files)
            kept: list[FileContext] = []
            for ctx in files:
                p = Path(ctx.abs_path)
                if not p.is_file():
                    kept.append(ctx)   # 目录条目保留
                    continue
                try:
                    if p.stat().st_mtime > cutoff_epoch:
                        kept.append(ctx)
                except OSError:
                    kept.append(ctx)   # stat 失败保留 (保守)
            files = kept
            scan_mode = f"{scan_mode}+incr"
            logger.info(
                "patrol: incremental filter %d → %d files (since %s)",
                files_before, len(files), since_ts,
            )
        except (ValueError, TypeError) as e:
            logger.warning("patrol: since_ts parse failed, no filter: %s", e)

    # ─── Layer 1: 规则引擎（分流 absolute / needs_judgment）────
    engine = RuleEngine()
    split = engine.evaluate_split(files)
    confirmed = split["confirmed"]         # 绝对违规，直接报告
    candidates = split["needs_judgment"]    # 疑似违规，可送 Agent 复核
    rule_count = engine._counter

    # ─── Layer 2: GuardianAgent 智能复核（显式开启）─────────────
    # Agent 用工具读文件、搜索依赖，对疑似违规做语义判断
    # 确认的加入 violations（带具体建议），排除的不报告
    agent_reviewed = 0
    if use_agent and candidates:
        try:
            import asyncio
            from .judge_agent import GuardianAgent
            agent = GuardianAgent()
            agent_input = {
                "project_root": str(root),
                "candidates": [
                    {
                        "path": v.path,
                        "rule_id": v.rule_id,
                        "message": v.message,
                        "ticket_id": v.ticket_id,
                    }
                    for v in candidates
                ],
            }
            verdict = asyncio.run(agent.run(agent_input))
            agent_reviewed = len(candidates)

            # 用 Agent 判断结果替换候选：confirmed → 加入，dismissed → 丢弃
            if verdict.output and "judgments" in verdict.output:
                for j in verdict.output["judgments"]:
                    if j.get("verdict") == "confirmed":
                        # 找到对应的原始 Violation，更新消息和建议
                        original = next(
                            (c for c in candidates if c.path == j.get("path")),
                            None,
                        )
                        if original:
                            original.message = j.get("reasoning", original.message)
                            original.confidence = float(j.get("confidence", 0.9))
                            # 把 suggestion 附加到 violation dict（供 Tow 使用）
                            original._agent_suggestion = j.get("suggestion", "")
                            confirmed.append(original)
                    elif j.get("verdict") == "uncertain":
                        # 不确定的也报告，但降低置信度
                        original = next(
                            (c for c in candidates if c.path == j.get("path")),
                            None,
                        )
                        if original:
                            original.confidence = float(j.get("confidence", 0.5))
                            confirmed.append(original)
                    # dismissed → 不加入 confirmed（排除）
            else:
                # Agent 解析失败，全部候选按原样报告
                confirmed.extend(candidates)
        except Exception as e:
            logger.warning("GuardianAgent 复核失败，回退到规则结果: %s", e)
            confirmed.extend(candidates)
    else:
        # 未启用 Agent 或无候选 → 候选按原样报告（向后兼容）
        confirmed.extend(candidates)

    violations = confirmed

    # ─── Layer 2b: 旧 LLM Judge（兼容接口，逐步弃用）────────────
    llm_files_judged = 0
    if use_llm:
        from .patrol import LLMJudge
        judge = LLMJudge(pilot_paths=llm_pilot_paths)
        llm_violations = judge.judge(
            files=files,
            new_files_only=llm_new_only,
            counter_start=rule_count,
            rule_violations=violations,
        )
        llm_files_judged = len([
            f for f in files if judge._should_judge(f, llm_new_only)
        ])
        violations.extend(llm_violations)

    # ─── 统计 ─────────────────────────────────────────────────
    by_severity: dict[str, int] = {
        "CRITICAL": 0, "HIGH": 0, "MEDIUM": 0, "LOW": 0, "INFO": 0,
    }
    for v in violations:
        by_severity[v.severity] = by_severity.get(v.severity, 0) + 1

    result = {
        "scan_ts": now,
        "scan_mode": scan_mode,
        "files_scanned": len(files),
        "violations_found": len(violations),
        "agent_reviewed": agent_reviewed,
        "llm_files_judged": llm_files_judged,
        "violations": [
            {
                "ticket_id": v.ticket_id,
                "rule_id": v.rule_id,
                "severity": v.severity,
                "path": v.path,
                "message": v.message,
                "disposition": v.disposition,
                "confidence": v.confidence,
                "suggestion": getattr(v, "_agent_suggestion", ""),
            }
            for v in violations
        ],
        "by_severity": by_severity,
    }

    _try_emit(result, root)

    # ─── OmniTow：自动处置（Phase 1 warn-only，生成罚单）────────
    if auto_tow and violations:
        try:
            from .tow_truck import OmniTow
            tow = OmniTow(project_root=root, phase2=tow_phase2)
            tickets = tow.process_all(result["violations"])
            result["tickets_issued"] = len(tickets)
        except Exception as e:
            logger.debug("OmniTow 处置失败: %s", e)
            result["tickets_issued"] = 0
    else:
        result["tickets_issued"] = 0

    # ─── auto_comment 软修复（S3c.3）──────────────────────────
    # 只对 archmap.auto_comment_pilot_rules 列出的规则生效。
    # external-agent / unknown 来源 → 立即原地备注化
    # internal-pipeline 来源 → 写 fix-queue 等 apply-fixes
    # human 来源 → 只警告不动文件
    if auto_tow and violations:
        try:
            from .auto_comment import process_for_auto_comment
            ac_result = process_for_auto_comment(result["violations"], root)
            result["auto_comment"] = ac_result.to_dict()
        except Exception as e:
            logger.debug("auto_comment 处置失败: %s", e)
            result["auto_comment"] = {"error": str(e)}
    else:
        result["auto_comment"] = None

    # ─── tech_debt/REGISTRY.md 同步（Phase A2）─────────────────
    # 把 OMNI-NNN 违规增量 append 到 docs/tech_debt/REGISTRY.md §活跃违规
    # + docs/ARCH-CHANGES.jsonl 记录 violation-found 事件
    try:
        from .registry_updater import sync_patrol_result_to_registry
        result["registry_sync"] = sync_patrol_result_to_registry(result, root)
    except Exception as e:
        logger.debug("registry 同步失败: %s", e)
        result["registry_sync"] = {"added": 0, "bumped": 0, "skipped": 0, "arch_events": 0}

    return result


def _try_emit(report: dict, root: Path) -> None:
    """尝试将巡逻报告写入本地 JSON 日志（不依赖 EventBus）。"""
    try:
        import json
        log_dir = root / "logs" / "patrol"
        log_dir.mkdir(parents=True, exist_ok=True)
        ts = report["scan_ts"].replace(":", "-").replace(".", "-")
        log_file = log_dir / f"patrol-{ts}.json"
        log_file.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as e:
        logger.debug("patrol: 无法写入日志文件: %s", e)


# ─── 格式化输出（供 CLI 使用）─────────────────────────────────


_SEVERITY_COLOR = {
    "CRITICAL": "\033[91m",   # bright red
    "HIGH": "\033[93m",       # yellow
    "MEDIUM": "\033[94m",     # blue
    "LOW": "\033[96m",        # cyan
    "INFO": "\033[37m",       # grey
}
_RESET = "\033[0m"


def format_patrol_report(result: dict, color: bool = True) -> str:
    """将 run_patrol 结果格式化为终端可读字符串。"""
    lines: list[str] = []
    mode_label = "全量扫描" if result.get("scan_mode") == "full" else "差量扫描(git diff)"
    lines.append(f"OmniPatrol 巡逻报告 — {result.get('scan_ts', '')}")
    lines.append(f"  模式: {mode_label}  |  扫描文件: {result['files_scanned']}  |  发现违规: {result['violations_found']}")
    lines.append("")

    if not result["violations"]:
        lines.append("  [OK] 未发现违规")
        return "\n".join(lines)

    # 按严重度分组输出
    by_sev: dict[str, list[dict]] = {}
    for v in result["violations"]:
        by_sev.setdefault(v["severity"], []).append(v)

    for sev in ["CRITICAL", "HIGH", "MEDIUM", "LOW", "INFO"]:
        group = by_sev.get(sev, [])
        if not group:
            continue
        prefix = _SEVERITY_COLOR.get(sev, "") if color else ""
        suffix = _RESET if color else ""
        lines.append(f"  {prefix}[{sev}]{suffix}  {len(group)} 条")
        for v in group:
            lines.append(f"    {v['ticket_id']}  {v['rule_id']}  {v['path']}")
            lines.append(f"      {v['message']}")
        lines.append("")

    bsev = result["by_severity"]
    summary_parts = []
    for sev in ["CRITICAL", "HIGH", "MEDIUM", "LOW"]:
        n = bsev.get(sev, 0)
        if n:
            summary_parts.append(f"{sev}:{n}")
    lines.append(f"  汇总: {' | '.join(summary_parts) or '无违规'}")
    return "\n".join(lines)
