# [OMNI] origin=claude-code domain=omnicompany/runtime ts=2026-04-08T03:23:43Z
# [OMNI] material_id="material:runtime.nodes.guardian_convergence.health_auditor.py"
"""守护与收敛节点 — 系统健康检查 + Fisher 单调性审计

从 semantic.py 拆分。
"""

from __future__ import annotations

import logging
from typing import Any

from omnicompany.protocol.anchor import Verdict, VerdictKind
from omnicompany.runtime.routing.router import Router
from omnicompany.runtime.storage.db_access import open_db_rw

logger = logging.getLogger(__name__)


class ConvergenceAuditRouter(Router):
    """收敛审计节点 — 检查 Fisher 单调性是否被持续违反。

    理论依据：定论 3.1 Fisher 基本定理。
    在 Validator 固定窗口内，平均 pass_rate (reward_composite) 应单调不降。
    违反意味着变异/选择机制出了问题，需要元进化介入。

    设计原则（修正后）：
    - 窗口至少 8 轮数据才有意义——跨任务趋势，而非单任务波动
    - 单次违反不触发（Fisher 噪声）；需要 CONSECUTIVE_VIOLATIONS 次连续违反
    - 面向进化后立即重试的评估，而非每轮正常运作
    - 单个探索任务内频繁降奖励是正常的（任务难度不均匀），不应触发元进化
    """

    INPUT_KEYS = ["system_prompt", "messages", "tool_results"]
    WINDOW_SIZE = 8                 # 原 5 → 8：需要更多轮数据
    NOISE_TOLERANCE = 0.05          # 原 0.02 → 0.05：容忍更大噪声
    CONSECUTIVE_VIOLATIONS = 3      # 新增：需要连续 3 次违反才触发

    def __init__(self, param_registry: Any = None):
        self._reward_history: list[float] = []
        self._violation_count = 0
        self._consecutive_violations = 0
        self._param_registry = param_registry

    def run(self, input_data: Any) -> Verdict:
        if not isinstance(input_data, dict):
            return Verdict(kind=VerdictKind.PASS, output=input_data)

        window_size = self.WINDOW_SIZE
        if self._param_registry is not None:
            try:
                window_size = int(self._param_registry.get_or_default(
                    "convergence.window_size", self.WINDOW_SIZE))
            except Exception:
                pass

        reward = input_data.get("reward_composite", 0.5)
        self._reward_history.append(reward)

        # 数据不足时不触发
        if len(self._reward_history) < window_size:
            return Verdict(
                kind=VerdictKind.PASS,
                output={
                    **input_data,
                    "convergence_ok": True,
                    "fisher_violation": False,
                    "fisher_violation_count": self._violation_count,
                    "reward_history_len": len(self._reward_history),
                    "convergence_note": f"warming_up({len(self._reward_history)}/{window_size})",
                },
            )

        window = self._reward_history[-window_size:]
        # 统计窗口内违反次数（而非在第一次违反时立即触发）
        violations_in_window = sum(
            1 for i in range(1, len(window))
            if window[i] < window[i - 1] - self.NOISE_TOLERANCE
        )
        # 连续违反计数
        if violations_in_window >= 2:
            self._consecutive_violations += 1
        else:
            self._consecutive_violations = 0

        # 只有连续 CONSECUTIVE_VIOLATIONS 轮都检测到多次违反才触发
        violation = self._consecutive_violations >= self.CONSECUTIVE_VIOLATIONS
        if violation:
            self._violation_count += 1

        kind = VerdictKind.FAIL if violation else VerdictKind.PASS
        return Verdict(
            kind=kind,
            output={
                **input_data,
                "convergence_ok": not violation,
                "fisher_violation": violation,
                "fisher_violation_count": self._violation_count,
                "fisher_consecutive": self._consecutive_violations,
                "fisher_violations_in_window": violations_in_window,
                "reward_history_len": len(self._reward_history),
            },
        )


class GuardianCheckRouter(Router):
    """守护进程健康检查节点 — 心跳/超时检查 + 文件系统洁净度审计。

    理论依据：终点 2 防止抱死。
    接收 GuardianProcess 实例，调用 check_health()。
    异常时返回 FAIL + Tier1 痛觉信号。

    同时每 N 轮执行一次文件系统洁净度审计：
    - 根目录出现非预期文件/目录 → 痛觉
    - 任何位置出现类型命名的临时文件（bash.stdout.*, fs.path.*）→ 痛觉 + 自动删除
    - 追踪发现的混乱文件数量，超过阈值注入痛觉信号
    """

    INPUT_KEYS = ["system_prompt", "messages", "tool_results"]

    # 根目录允许存在的合法条目
    _ALLOWED_ROOT = frozenset({
        "src", "scripts", "data", "config", "tests", "docs", "tmp", "venv", ".venv",
        ".git", ".pytest_cache", "__pycache__",
        "pyproject.toml", ".gitignore", ".env", ".env.local", "README.md",
    })

    # data/autonomous/ 下允许存在的子目录（其他均为未分类文件）
    _ALLOWED_DATA_AUTONOMOUS_FILES = frozenset({
        "semantic_network.db", "events.db", "evolution_log.jsonl",
        "meta_heuristics.json", "gen0_bootstrap.json",
    })

    # 类型名前缀，agent 经常用这些作为文件名
    _TYPE_NAME_PREFIXES = (
        "bash.stdout.", "bash.stderr.", "bash.int.", "fs.path.", "fs.content.",
        "python.code.", "think.plan.", "exec.output.", "data.json.",
    )

    _FS_CHECK_INTERVAL = 5   # 每 5 步检查一次
    _fs_check_counter: int = 0

    def __init__(self, guardian: Any = None, project_root: str | None = None, db_path: str | None = None):
        self._guardian = guardian
        self._db_path = db_path
        import os
        self._project_root = project_root or os.getcwd()

    def run(self, input_data: Any) -> Verdict:
        if not isinstance(input_data, dict):
            return Verdict(kind=VerdictKind.PASS, output=input_data)

        issues: list[str] = []
        pain_intensity = 0.0

        # ── 守护进程心跳检查 ──────────────────────────────
        if self._guardian is not None:
            self._guardian.heartbeat()
            report = self._guardian.check_health()
            if not report.healthy:
                return Verdict(
                    kind=VerdictKind.FAIL,
                    output={
                        **input_data,
                        "guardian_ok": False,
                        "guardian_issues": report.issues,
                        "has_pain": True,
                        "pain_intensity": 1.0,
                        "pain_tier": 1,
                    },
                )

        # ── 文件系统洁净度审计（每 N 步执行一次）────────────
        GuardianCheckRouter._fs_check_counter += 1
        if GuardianCheckRouter._fs_check_counter % self._FS_CHECK_INTERVAL == 0:
            fs_pain, fs_issues = self._audit_filesystem()
            issues.extend(fs_issues)
            pain_intensity = max(pain_intensity, fs_pain)

        if issues:
            import logging
            logging.getLogger(__name__).warning(
                "GuardianCheck FS audit: %d issue(s), pain=%.2f | %s",
                len(issues), pain_intensity, "; ".join(issues[:3]),
            )
            # 持久化痛觉信号到 semantic_network.db（无论是否回 FAIL）
            self._write_pain_to_db(pain_intensity, issues)
            if pain_intensity >= 0.5:
                return Verdict(
                    kind=VerdictKind.FAIL,
                    output={
                        **input_data,
                        "guardian_ok": False,
                        "guardian_issues": issues,
                        "has_pain": True,
                        "pain_intensity": pain_intensity,
                        "pain_tier": 2,
                        "pain_source": "filesystem_entropy",
                    },
                )

        return Verdict(
            kind=VerdictKind.PASS,
            output={**input_data, "guardian_ok": True, "guardian_issues": issues},
        )

    def _audit_filesystem(self) -> tuple[float, list[str]]:
        """检查文件系统洁净度。返回 (pain_intensity, issues)。"""
        import os
        from pathlib import Path

        issues: list[str] = []
        root = Path(self._project_root)

        # 1. 根目录非法条目 — 只检测，不删除（让痛觉驱动系统自学习）
        try:
            for entry in root.iterdir():
                name = entry.name
                if name.startswith("."):
                    continue
                if name not in self._ALLOWED_ROOT:
                    kind = "file" if entry.is_file() else "dir"
                    issues.append(f"root_contamination: unexpected {kind} '{name}' in project root")
        except Exception:
            pass

        # 2. 全局扫描类型命名文件（只检测，不删除）
        type_named_files: list[Path] = []
        try:
            count = 0
            for dirpath, dirnames, filenames in os.walk(str(root)):
                dirnames[:] = [d for d in dirnames if d not in (
                    ".git", "venv", ".venv", "__pycache__", ".pytest_cache", "data",
                )]
                for fname in filenames:
                    if any(fname.startswith(p) for p in self._TYPE_NAME_PREFIXES):
                        type_named_files.append(Path(dirpath) / fname)
                count += len(filenames)
                if count > 500:
                    break
        except Exception:
            pass

        if type_named_files:
            issues.append(
                f"type_named_files: {len(type_named_files)} type-named temp files exist "
                f"({', '.join(f.name for f in type_named_files[:3])}{'...' if len(type_named_files) > 3 else ''})"
            )

        # 3. data/autonomous/ 非规范文件检查（agent 生成的报告/日志堆积）
        data_auto_extras: list[str] = []
        try:
            data_auto = root / "data" / "autonomous"
            if data_auto.exists():
                for entry in data_auto.iterdir():
                    if entry.is_file() and entry.name not in self._ALLOWED_DATA_AUTONOMOUS_FILES:
                        if entry.suffix in (".md", ".txt", ".py", ".json") and entry.name.endswith(
                            (".md", ".txt")
                        ):
                            data_auto_extras.append(entry.name)
        except Exception:
            pass
        if len(data_auto_extras) > 5:
            issues.append(
                f"data_auto_accumulation: {len(data_auto_extras)} unstructured files in data/autonomous/ "
                f"({', '.join(data_auto_extras[:3])}...)"
            )

        # pain 计算：每个非法条目 0.15，类型命名文件每个 0.10，data堆积 0.05/个
        root_contaminations = len([i for i in issues if "root_contamination" in i])
        root_pain = min(0.6, root_contaminations * 0.15)
        type_pain = min(0.4, len(type_named_files) * 0.10)
        data_accum_pain = min(0.3, len(data_auto_extras) * 0.05)
        pain = min(0.9, root_pain + type_pain + data_accum_pain)

        return pain, issues

    def _write_pain_to_db(self, pain_score: float, issues: list[str]) -> None:
        """将 FS 污染痛觉信号持久化到 semantic_network.db（pain_signals 表）。
        Phase 5: 已停止写入旧的 guardian_pain_events 表，统一写 pain_signals。
        """
        if not self._db_path:
            return
        import time as _time
        try:
            conn = open_db_rw(self._db_path)
            _severity = (
                "critical" if pain_score >= 0.7
                else ("high" if pain_score >= 0.5
                      else ("medium" if pain_score >= 0.3 else "low"))
            )
            _issues_text = "；".join(issues[:5]) if issues else "无具体问题"
            _signal_text = (
                f"Guardian健康检查发现系统问题（pain={pain_score:.2f}，"
                f"issue_count={len(issues)}）：{_issues_text}"
            )
            _now = _time.time()
            # Phase 7+: 写入前 expire 超过 1h 的旧 guardian pain（滑动窗口，防止无限积累）
            _expire_cutoff = _now - 3600
            conn.execute(
                "UPDATE pain_signals SET resolved_at=?, resolution=? "
                "WHERE node_id='system.guardian' AND resolved_at IS NULL AND created_at < ?",
                (_now, "guardian 健康信号已过期（TTL 1h，由新检查覆盖）", _expire_cutoff),
            )
            conn.execute(
                "INSERT INTO pain_signals (node_id, signal_text, severity, source, created_at) "
                "VALUES (?,?,?,?,?)",
                ("system.guardian", _signal_text, _severity, "guardian_check", _now),
            )
            conn.commit()
            conn.close()
        except Exception as e:
            import logging
            logging.getLogger(__name__).debug("GuardianCheck._write_pain_to_db failed: %s", e)
