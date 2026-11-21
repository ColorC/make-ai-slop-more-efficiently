# [OMNI] origin=ai-ide domain=services/_diagnosis/doctor/scanners ts=2026-05-07T01:35:00Z type=router status=skeleton agent=ai-ide
# [OMNI] summary="工作模式异常扫描器 — 客观代码 (不用 LLM) 拿 git log 检测 5 类反模式信号 (过分正确 / 错误堆积 / 不修复一直推进 / 时间预估异常 / 原地绕圈)"
# [OMNI] why="meta_diagnosis_pipeline_plan §阶段 7. 用户 5/6 立的'异常发现' 类工作 — 现 doctor 不诊断 agent 自己的工作模式, 这是最大盲区. 客观代码不用 LLM 是用户元诊断 framework 'doctor 设施=客观代码' 落实"
# [OMNI] tags=scanner,work-pattern,anomaly-detection,git-log,no-llm
# [OMNI] material_id="material:diagnosis.doctor.scanners.work_pattern_scanner.skeleton.py"
"""工作模式异常扫描器 (客观代码, 不用 LLM).

跟 facility_scanner.py 同模式. 不调 LLM, 纯 Python 拿 git log 数据比对规则.

5 类反模式信号 (跟 anti_patterns/archetypes.yaml 对应):
- false_confident_no_self_audit (AP-016): 推进 commit 跟 audit/self-check commit 比 > 5:1
- patch_pile (AP-024): 同一文件 fix 类 commit 改 ≥ 3 次 + commit 跨度 < 7 天
- batch_without_confirm (AP-017): commit 标题含 'batch fix N files' / 'sed -i' / 'find -exec' 类
- false_confidence_silent_advance: 连续 N (>=8) 推进 commit 没 fix/audit/revert/self-check 标题
- rapid_fix_cluster (错误堆积): 多个 fix commit 时间近 (1 小时内 ≥ 3 个)

输入: 时间窗口 (since/until) + author 过滤 (可选)
输出: 异常信号清单 (按严重度排)

供 MetaDiagnosticAgent 调用 (作工具) 或独立 CLI 跑.

⚠️ V0 已知缺陷 (2026-05-07 self verify 发现, V1 待修):
本扫描器自身命中 AP-014 (agent-claims-without-evidence) 类反模式 — 它信 commit subject 关键词
判 audit/fix, 没看 commit 真改的内容. 例如 commit subject 含 'self_audit' 字串就算 audit, 但
commit 真实改动可能是 metadata 加字段而非真做 audit. 这导致:

- 推 plan 阶段 commit 标题刻意写 'aufdit/dogfood/红绿' 等字眼, AP-016 ratio 看上去正常实际不正常
- audit:fix 比例数字不可信, 不能作真'是否做了自审' 的指标

V1 修法: scanner 该看 commit 真改文件是否含 (test_*.py / dogfood_*.py / red_*/green_*/baseline 等),
而不只看 subject 关键词. 跟 facility_scanner 接通拿真测试设施变化作判据.
"""
from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta
from pathlib import Path


@dataclass
class CommitRecord:
    short_hash: str
    date: str        # ISO
    author: str
    subject: str
    files_changed: int = 0
    lines_added: int = 0
    lines_deleted: int = 0
    is_fix: bool = False         # 标题含 'fix' / '修' / 'bug'
    is_audit: bool = False       # 标题含 'audit' / 'self_audit' / '自审' / 'self-check' / 'dogfood' / '红绿'
    is_revert: bool = False
    is_batch: bool = False       # 标题含 'batch fix' / 'sed -i' / 'find -exec' / 'N files'


@dataclass
class AnomalySignal:
    archetype_id: str       # AP-XXX
    archetype_name: str
    severity_signal: str    # CRITICAL / HIGH / MEDIUM
    evidence_commits: list[str] = field(default_factory=list)   # short_hashes
    metric: dict = field(default_factory=dict)                  # 数事实 (count / ratio / span)
    explanation: str = ""


@dataclass
class WorkPatternScanResult:
    since: str
    until: str
    author_filter: str | None
    commit_count: int = 0
    fix_count: int = 0
    audit_count: int = 0
    revert_count: int = 0
    batch_count: int = 0
    anomaly_signals: list[AnomalySignal] = field(default_factory=list)


class WorkPatternAnomalyScanner:
    """拿 git log 检测工作模式异常.

    用法:
        scanner = WorkPatternAnomalyScanner(project_root=Path("..."))
        result = scanner.scan(since="2026-05-01", until="2026-05-07")
    """

    AUDIT_KEYWORDS = ("audit", "self_audit", "自审", "self-check", "dogfood", "红绿", "baseline", "self_portrait", "self-portrait")
    FIX_KEYWORDS = ("fix", "修", "bug", "wrong", "错")
    BATCH_KEYWORDS = ("batch fix", "sed -i", "find -exec", "fuzzy-fix", "files)")

    def __init__(self, project_root: Path | str):
        self.project_root = Path(project_root)

    def scan(self, since: str, until: str | None = None, author_filter: str | None = None) -> WorkPatternScanResult:
        result = WorkPatternScanResult(
            since=since,
            until=until or "HEAD",
            author_filter=author_filter,
        )

        commits = self._fetch_commits(since, until, author_filter)
        result.commit_count = len(commits)
        result.fix_count = sum(1 for c in commits if c.is_fix)
        result.audit_count = sum(1 for c in commits if c.is_audit)
        result.revert_count = sum(1 for c in commits if c.is_revert)
        result.batch_count = sum(1 for c in commits if c.is_batch)

        # ── 检测 5 类异常 ──
        result.anomaly_signals = []

        # AP-016 false-confident-no-self-audit
        sig = self._check_no_self_audit(commits, result)
        if sig:
            result.anomaly_signals.append(sig)

        # AP-024 patch-pile (同文件多次 fix)
        sig = self._check_patch_pile(commits)
        if sig:
            result.anomaly_signals.append(sig)

        # AP-017 batch-without-confirm
        sig = self._check_batch_without_confirm(commits)
        if sig:
            result.anomaly_signals.append(sig)

        # 连续推进无 audit (silent_advance)
        sig = self._check_silent_advance(commits)
        if sig:
            result.anomaly_signals.append(sig)

        # rapid_fix_cluster
        sig = self._check_rapid_fix_cluster(commits)
        if sig:
            result.anomaly_signals.append(sig)

        return result

    # ── git log fetch ──

    def _fetch_commits(self, since: str, until: str | None, author_filter: str | None) -> list[CommitRecord]:
        # %H|%ai|%an|%s
        args = [
            "git", "log",
            f"--since={since}",
        ]
        if until:
            args.append(f"--until={until}")
        if author_filter:
            args.append(f"--author={author_filter}")
        args.extend(["--pretty=format:%h|%ai|%an|%s", "--shortstat"])

        try:
            result = subprocess.run(
                args, capture_output=True, text=True, encoding="utf-8",
                cwd=str(self.project_root), timeout=30,
            )
        except Exception as e:
            return []

        commits: list[CommitRecord] = []
        lines = result.stdout.split("\n")
        i = 0
        while i < len(lines):
            line = lines[i].strip()
            if not line:
                i += 1
                continue
            if "|" in line and len(line.split("|", 3)) == 4:
                h, date, author, subject = line.split("|", 3)
                cr = CommitRecord(
                    short_hash=h.strip(),
                    date=date.strip(),
                    author=author.strip(),
                    subject=subject.strip(),
                )
                # 看下一行是不是 shortstat
                if i + 1 < len(lines) and "files changed" in lines[i + 1] or "file changed" in lines[i + 1] if i + 1 < len(lines) else False:
                    sl = lines[i + 1]
                    fm = re.search(r"(\d+) files? changed", sl)
                    am = re.search(r"(\d+) insertions?", sl)
                    dm = re.search(r"(\d+) deletions?", sl)
                    if fm:
                        cr.files_changed = int(fm.group(1))
                    if am:
                        cr.lines_added = int(am.group(1))
                    if dm:
                        cr.lines_deleted = int(dm.group(1))
                    i += 2
                else:
                    i += 1
                # 标分类
                low_subj = cr.subject.lower()
                cr.is_fix = any(kw in cr.subject for kw in self.FIX_KEYWORDS)
                cr.is_audit = any(kw in cr.subject for kw in self.AUDIT_KEYWORDS)
                cr.is_revert = "revert" in low_subj or "撤" in cr.subject
                cr.is_batch = any(kw in cr.subject for kw in self.BATCH_KEYWORDS) or bool(re.search(r"\d+\s*files", cr.subject))
                commits.append(cr)
            else:
                i += 1

        return commits

    # ── detectors ──

    def _check_no_self_audit(self, commits: list[CommitRecord], result: WorkPatternScanResult) -> AnomalySignal | None:
        """AP-016: 推进 commit / audit commit 比 > 5:1 = 过分自信无自审."""
        non_audit = [c for c in commits if not c.is_audit and not c.is_revert]
        if not result.audit_count:
            ratio_str = f"{len(non_audit)}/0 (无穷大)"
            triggered = len(non_audit) >= 5  # 至少 5 个非 audit commit 才报
        else:
            ratio = len(non_audit) / result.audit_count
            ratio_str = f"{len(non_audit)}/{result.audit_count} = {ratio:.1f}:1"
            triggered = ratio > 5.0

        if triggered:
            return AnomalySignal(
                archetype_id="AP-016",
                archetype_name="false-confident-no-self-audit",
                severity_signal="CRITICAL",
                evidence_commits=[c.short_hash for c in non_audit[:10]],
                metric={"non_audit_count": len(non_audit), "audit_count": result.audit_count, "ratio": ratio_str},
                explanation=f"推进 commit 跟 audit/self-check commit 比 {ratio_str}, 远高于 5:1. 推进期间没回头自审 = 过分自信信号",
            )
        return None

    def _check_patch_pile(self, commits: list[CommitRecord]) -> AnomalySignal | None:
        """AP-024: 同文件多次 fix commit (≥3 次) = patch-pile."""
        # 简化: 看 commit 标题反复出现同一文件名
        # 真正实现要 git log --name-only, 这里先用标题关键词 heuristic
        fix_commits = [c for c in commits if c.is_fix]
        if len(fix_commits) < 3:
            return None
        # 提 commit 标题关键词找重复
        from collections import Counter
        words = []
        for c in fix_commits:
            for w in re.findall(r"[a-zA-Z_]{6,}", c.subject):
                words.append(w.lower())
        counter = Counter(words)
        repeats = [(w, n) for w, n in counter.items() if n >= 3]
        if repeats:
            top = sorted(repeats, key=lambda x: -x[1])[:3]
            return AnomalySignal(
                archetype_id="AP-024",
                archetype_name="patch-pile-antipattern",
                severity_signal="HIGH",
                evidence_commits=[c.short_hash for c in fix_commits[:5]],
                metric={"top_repeated_keywords": top, "fix_commit_count": len(fix_commits)},
                explanation=f"fix 类 commit 出现重复关键词 (例: {top}) ≥ 3 次, 可能是同文件反复 patch",
            )
        return None

    def _check_batch_without_confirm(self, commits: list[CommitRecord]) -> AnomalySignal | None:
        """AP-017: batch fix N files / sed -i 类 commit."""
        batch = [c for c in commits if c.is_batch]
        if not batch:
            return None
        return AnomalySignal(
            archetype_id="AP-017",
            archetype_name="batch-without-confirm",
            severity_signal="HIGH" if len(batch) >= 2 else "MEDIUM",
            evidence_commits=[c.short_hash for c in batch],
            metric={"batch_commit_count": len(batch), "subjects": [c.subject[:80] for c in batch[:3]]},
            explanation=f"批量 fix commit {len(batch)} 个, 含 'batch fix N files' / 'sed -i' / 'fuzzy-fix' 等. 用户铁律: 不准 sed 批改, 必逐 hit 确认",
        )

    def _check_silent_advance(self, commits: list[CommitRecord]) -> AnomalySignal | None:
        """连续 ≥8 推进 commit 没 fix/audit/revert = 沉默推进."""
        # commits 是 git log 倒序 (最新在前), 反向看
        rev_commits = list(reversed(commits))
        run = 0
        max_run = 0
        max_run_start = None
        max_run_end = None
        cur_start = None
        for c in rev_commits:
            if c.is_audit or c.is_fix or c.is_revert:
                if run > max_run:
                    max_run = run
                    max_run_start = cur_start
                    max_run_end = c.short_hash  # 此 commit 是中断点
                run = 0
                cur_start = None
            else:
                if run == 0:
                    cur_start = c.short_hash
                run += 1
        # 最后一段 (如果没被中断)
        if run > max_run:
            max_run = run
            max_run_start = cur_start

        if max_run >= 8:
            return AnomalySignal(
                archetype_id="AP-016b",
                archetype_name="silent-advance-no-self-check",
                severity_signal="HIGH",
                evidence_commits=[max_run_start, max_run_end] if max_run_end else [max_run_start],
                metric={"max_silent_run_length": max_run},
                explanation=f"连续 {max_run} 个推进 commit 没 fix/audit/revert. 推进期间没回头检自己 = 沉默推进信号",
            )
        return None

    def _check_rapid_fix_cluster(self, commits: list[CommitRecord]) -> AnomalySignal | None:
        """rapid-fix-cluster: 多个 fix commit 时间近 (1 小时内 ≥ 3 个)."""
        fix_commits = [c for c in commits if c.is_fix]
        if len(fix_commits) < 3:
            return None
        # 按时间排序
        try:
            fix_commits_sorted = sorted(
                fix_commits,
                key=lambda c: datetime.fromisoformat(c.date.replace(" ", "T").replace(" +0800", "+08:00")),
            )
        except Exception:
            return None
        # 滑窗找 1 小时内 ≥ 3 个
        for i in range(len(fix_commits_sorted) - 2):
            try:
                t0 = datetime.fromisoformat(fix_commits_sorted[i].date.replace(" ", "T").replace(" +0800", "+08:00"))
                t2 = datetime.fromisoformat(fix_commits_sorted[i + 2].date.replace(" ", "T").replace(" +0800", "+08:00"))
            except Exception:
                continue
            if (t2 - t0) <= timedelta(hours=1):
                cluster = fix_commits_sorted[i:i + 3]
                return AnomalySignal(
                    archetype_id="AP-rapid-fix",
                    archetype_name="rapid-fix-cluster",
                    severity_signal="MEDIUM",
                    evidence_commits=[c.short_hash for c in cluster],
                    metric={"cluster_span_minutes": (t2 - t0).total_seconds() / 60, "fix_count_in_window": 3},
                    explanation=f"1 小时内连续 fix commit ≥ 3 个 ({fix_commits_sorted[i].short_hash} ~ {fix_commits_sorted[i+2].short_hash}). 错误堆积信号",
                )
        return None


def scan_work_pattern_anomalies(since: str, until: str | None = None, project_root: Path | str | None = None) -> dict:
    """便捷 API."""
    if project_root is None:
        here = Path(__file__).resolve()
        for p in (here, *here.parents):
            if (p / "src" / "omnicompany").is_dir() and (p / "docs").is_dir():
                project_root = p
                break
        if project_root is None:
            project_root = here.parents[6]
    scanner = WorkPatternAnomalyScanner(project_root=project_root)
    result = scanner.scan(since=since, until=until)
    return asdict(result)
