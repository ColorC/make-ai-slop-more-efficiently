# [OMNI] origin=omnicompany domain=omnicompany/guardian ts=2026-04-05T00:00:00Z
# [OMNI] material_id="material:core.guardian.evolution_signal_processor.implementation.py"
"""OmniEvolve — 内部违规矫正进化信号（05-OMNIEVOLVE.md）

只对 **内部管线产生的** 违规文件触发（omnimark.origin 在内部管线枚举中
且 omnimark.trace 可追溯到具体节点）。

三级升级机制（类 fail2ban 渐进惩罚）：
  Level 0 — 警告（第 1 次）   : 只记录，不做任何修改
  Level 1 — 建议矫正（2-3次） : LLM 生成 prompt 补丁，存为 pending，人工审核后应用
  Level 2 — 写入限制（4次+）  : 发出 restriction_request，需人工确认 1h 后才生效

当前 Phase 4 实现范围：
  - Level 0/1/2 记录与建议全部实现
  - Level 1 LLM 建议生成（实际写入需 `omni guardian evolution-apply` 确认）
  - Level 2 写入限制发出（实际 BlockedPatternFilter 包装留 Phase 4+ 实施）
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

_DEFAULT_ROOT = Path("e:/WindowsWorkspace/omnicompany")

# 可被 OmniEvolve 追溯的内部管线来源
INTERNAL_PIPELINE_ORIGINS = frozenset({
    "workflow-factory",
    "sw-implement",
    "sw-tdd",
    "lang-rewrite",
    "skill-import",
    "omnicompany",   # 框架自身管线
})

# OMNI-003 违规时需要追加到 SYSTEM_PROMPT 的约束提示
_RULE_CONSTRAINT_HINTS = {
    "OMNI-003": (
        "\n\n[OmniGuardian 约束] 严禁直接 import anthropic / openai。"
        "所有 LLM 调用必须通过 omnicompany.runtime.llm.llm.LLMClient。"
        "违反此约束的代码将被自动隔离并记入违规历史。"
    ),
    "OMNI-002": (
        "\n\n[OmniGuardian 约束] 严禁在 src/omnicompany/runtime/ 框架层直接写业务代码。"
        "业务代码必须放在 src/omnicompany/packages/<namespace>/<domain>/ 下。"
    ),
    "OMNI-004": (
        "\n\n[OmniGuardian 约束] Router.run() 必须是同步方法（def run），"
        "不能是 async def run。TeamRunner 会自动处理异步调度。"
    ),
}


# ─── 数据结构 ────────────────────────────────────────────────────

@dataclass
class EvolutionSignal:
    """发给进化系统的矫正信号。"""

    signal_id: str
    source_ticket: str
    source_trace: str
    source_node: str
    source_pipeline: str
    rule_violated: str
    violation_path: str
    violation_evidence: list[str]
    suggested_correction: str
    repeat_count: int
    escalation_level: int        # 0=warn / 1=prompt-fix / 2=restriction
    detected_at: str = ""
    correction_applied: bool = False
    correction_ts: Optional[str] = None

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class NodeViolationHistory:
    """单个节点的违规 + 矫正历史。"""

    node_id: str
    pipeline: str
    violations: list[dict] = field(default_factory=list)
    current_restrictions: list[str] = field(default_factory=list)
    total_violations: int = 0
    consecutive_clean_runs: int = 0   # 连续合规次数（用于降级）

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "NodeViolationHistory":
        h = cls(
            node_id=d.get("node_id", ""),
            pipeline=d.get("pipeline", ""),
            violations=d.get("violations", []),
            current_restrictions=d.get("current_restrictions", []),
            total_violations=d.get("total_violations", 0),
            consecutive_clean_runs=d.get("consecutive_clean_runs", 0),
        )
        return h

    def escalation_level(self) -> int:
        """根据历史违规次数计算当前升级等级。"""
        n = self.total_violations
        if n <= 1:
            return 0
        elif n <= 3:
            return 1
        else:
            return 2


# ─── OmniEvolve ─────────────────────────────────────────────────

class OmniEvolve:
    """内部违规矫正进化处理器。

    只对携带可追溯 omnimark（origin + trace + node 都有值）的违规触发。
    外部写入（origin=human / claude-code）由 OmniTow 处理，不走 OmniEvolve。
    """

    def __init__(
        self,
        project_root: str | Path = _DEFAULT_ROOT,
        use_llm: bool = True,
    ):
        self._root = Path(project_root)
        self._evo_dir = self._root / ".omni" / "evolution"
        self._use_llm = use_llm   # False 时跳过 LLM 调用（测试 / 离线环境）

    # ── 主入口 ───────────────────────────────────────────────────

    def process(
        self,
        violation: dict,
        omnimark: Optional[dict] = None,
    ) -> Optional[EvolutionSignal]:
        """处置一条违规，若来自内部管线则生成 EvolutionSignal。

        Args:
            violation: run_patrol() violations 条目
            omnimark:  违规文件的 OmniMark 字段（可选，None 则自动读取）

        Returns:
            EvolutionSignal 或 None（非内部管线来源时）
        """
        # 1. 读取 omnimark
        if omnimark is None:
            omnimark = self._read_omnimark(violation.get("path", ""))
        if not self._is_internal(omnimark):
            return None

        node_id = omnimark.get("node", "unknown-node")
        pipeline = omnimark.get("origin", "unknown-pipeline")
        trace_id = omnimark.get("trace", "")

        # 2. 加载节点历史 + 更新计数
        history = self._load_history(node_id)
        history.total_violations += 1
        history.pipeline = pipeline
        history.consecutive_clean_runs = 0
        level = history.escalation_level()

        now = datetime.now(timezone.utc).isoformat()
        signal_id = f"EVS-{violation.get('ticket_id', now[:10])}"

        # 3. 提取违规证据（违规行）
        evidence = self._extract_evidence(violation)

        # 4. 生成矫正建议
        correction = self._build_correction_suggestion(violation, evidence, level)

        signal = EvolutionSignal(
            signal_id=signal_id,
            source_ticket=violation.get("ticket_id", ""),
            source_trace=trace_id,
            source_node=node_id,
            source_pipeline=pipeline,
            rule_violated=violation.get("rule_id", ""),
            violation_path=violation.get("path", ""),
            violation_evidence=evidence,
            suggested_correction=correction,
            repeat_count=history.total_violations,
            escalation_level=level,
            detected_at=now,
        )

        # 5. 按级别执行动作
        if level == 0:
            self._apply_level0(signal)
        elif level == 1:
            self._apply_level1(signal)
        else:
            self._apply_level2(signal)

        # 6. 持久化历史
        history.violations.append({
            "signal_id": signal.signal_id,
            "ticket_id": violation.get("ticket_id", ""),
            "rule": signal.rule_violated,
            "path": signal.violation_path,
            "detected_at": now,
            "escalation_level": level,
            "correction_applied": False,
        })
        self._save_history(history)
        self._update_index(signal)

        return signal

    def process_batch(
        self,
        violations: list[dict],
    ) -> list[EvolutionSignal]:
        """处置一批违规，返回生成的 EvolutionSignal 列表。"""
        signals = []
        for v in violations:
            s = self.process(v)
            if s is not None:
                signals.append(s)
        return signals

    # ── 级别动作 ─────────────────────────────────────────────────

    def _apply_level0(self, signal: EvolutionSignal) -> None:
        """Level 0：仅警告记录，不做实质修改。"""
        logger.warning(
            "[OmniEvolve L0] %s  节点=%s  规则=%s  路径=%s  (第 %d 次违规，仅记录)",
            signal.signal_id, signal.source_node,
            signal.rule_violated, signal.violation_path,
            signal.repeat_count,
        )

    def _apply_level1(self, signal: EvolutionSignal) -> None:
        """Level 1：LLM 生成 prompt 矫正建议，写为 pending，等待人工确认。"""
        logger.warning(
            "[OmniEvolve L1] %s  节点=%s  第 %d 次违规 → 生成 prompt 矫正建议",
            signal.signal_id, signal.source_node, signal.repeat_count,
        )
        # 尝试用 LLM 生成更精准的矫正建议（use_llm=False 时跳过，使用规则模板）
        llm_suggestion = self._llm_generate_correction(signal) if self._use_llm else None
        if llm_suggestion:
            signal.suggested_correction = llm_suggestion

        # 写入 pending correction 文件（人工审核后 `omni guardian evolution-apply` 应用）
        self._save_pending_correction(signal)
        logger.info(
            "[OmniEvolve L1] 矫正建议已写入 .omni/evolution/nodes/%s.pending_correction.json",
            signal.source_node,
        )

    def _apply_level2(self, signal: EvolutionSignal) -> None:
        """Level 2：发出写入限制请求（需人工确认后生效）。"""
        logger.error(
            "[OmniEvolve L2] %s  节点=%s  第 %d 次违规 → 发出 restriction_request（需 1h 内人工确认）",
            signal.signal_id, signal.source_node, signal.repeat_count,
        )
        self._save_restriction_request(signal)

    # ── LLM 矫正建议生成 ─────────────────────────────────────────

    _CORRECTION_SYSTEM = """\
你是 OmniGuardian 的矫正顾问。
一个内部管线节点的代码重复违反架构规则，你需要生成一段精准的 SYSTEM_PROMPT 追加片段，
使该节点在未来生成代码时不再违反该规则。

要求：
- 追加片段简洁（不超过 5 句话）
- 明确指出禁止的行为和正确的替代方案
- 不替换原有 SYSTEM_PROMPT，只追加约束
- 输出纯文本，无 markdown，以 \\n\\n[OmniGuardian 约束] 开头
"""

    def _llm_generate_correction(self, signal: EvolutionSignal) -> Optional[str]:
        """调用 LLMClient 生成精准的 prompt 矫正建议。失败返回 None（降级用规则模板）。"""
        try:
            from omnicompany.runtime.llm.llm import LLMClient
            client = LLMClient()

            evidence_text = "\n".join(signal.violation_evidence[:10]) or "(无具体证据行)"
            user_msg = (
                f"节点 ID: {signal.source_node}\n"
                f"管线: {signal.source_pipeline}\n"
                f"违规规则: {signal.rule_violated}\n"
                f"违规文件: {signal.violation_path}\n"
                f"违规次数: {signal.repeat_count}\n"
                f"当前矫正建议（规则模板）: {signal.suggested_correction}\n\n"
                f"具体违规代码行:\n{evidence_text}\n\n"
                "请生成一段 SYSTEM_PROMPT 追加片段，使该节点未来不再产生此类违规。"
            )
            response = client.call(
                messages=[{"role": "user", "content": user_msg}],
                system=self._CORRECTION_SYSTEM,
            )
            if hasattr(response, "content"):
                text = "".join(b.text for b in response.content if hasattr(b, "text")).strip()
            else:
                text = str(response).strip()
            return text if text else None
        except Exception as e:
            logger.debug("[OmniEvolve] LLM 矫正建议生成失败: %s", e)
            return None

    # ── 辅助 ─────────────────────────────────────────────────────

    def _is_internal(self, omnimark: Optional[dict]) -> bool:
        """判断是否是内部管线产生的违规（需 origin + trace 都有值）。"""
        if not omnimark:
            return False
        origin = omnimark.get("origin", "")
        trace = omnimark.get("trace", "") or omnimark.get("intent", "")  # 兼容旧格式
        node = omnimark.get("node", "") or omnimark.get("created_by", "")
        return origin in INTERNAL_PIPELINE_ORIGINS and bool(trace) and bool(node)

    def _read_omnimark(self, rel_path: str) -> Optional[dict]:
        """从文件读取 OmniMark 字段。"""
        if not rel_path:
            return None
        abs_path = self._root / rel_path
        if not abs_path.exists():
            return None
        try:
            from omnicompany.core.omnimark import parse_omnimark
            m = parse_omnimark(abs_path)
            return asdict(m) if m else None
        except Exception:
            return None

    def _extract_evidence(self, violation: dict) -> list[str]:
        """从违规信息中提取具体违规代码行。"""
        message = violation.get("message", "")
        path = violation.get("path", "")
        rule_id = violation.get("rule_id", "")

        # 尝试从文件中找出实际违规行
        lines: list[str] = []
        abs_path = self._root / path
        if abs_path.exists() and abs_path.suffix == ".py":
            try:
                content = abs_path.read_text(encoding="utf-8", errors="replace")
                # 根据规则找关键行
                triggers = {
                    "OMNI-003": ["import anthropic", "import openai", "from anthropic", "from openai"],
                    "OMNI-004": ["async def run(self"],
                    "OMNI-002": [],  # 整个文件位置就是证据
                }
                keywords = triggers.get(rule_id, [])
                for i, line in enumerate(content.splitlines(), 1):
                    if any(kw in line for kw in keywords):
                        lines.append(f"  L{i}: {line.rstrip()}")
                        if len(lines) >= 5:
                            break
            except Exception:
                pass

        if not lines:
            lines = [message[:120]] if message else []
        return lines

    def _build_correction_suggestion(
        self, violation: dict, evidence: list[str], level: int
    ) -> str:
        """基于规则模板构建矫正建议（LLM 成功时会覆盖）。"""
        rule_id = violation.get("rule_id", "")
        hint = _RULE_CONSTRAINT_HINTS.get(rule_id, "")
        if hint:
            return hint.strip()
        return f"请检查 {rule_id} 规则要求并修正对应代码。"

    # ── 持久化 ───────────────────────────────────────────────────

    def _ensure_dirs(self) -> None:
        (self._evo_dir / "nodes").mkdir(parents=True, exist_ok=True)

    def _load_history(self, node_id: str) -> NodeViolationHistory:
        self._ensure_dirs()
        hist_file = self._evo_dir / "nodes" / f"{node_id}.history.json"
        if hist_file.exists():
            try:
                data = json.loads(hist_file.read_text(encoding="utf-8"))
                return NodeViolationHistory.from_dict(data)
            except Exception:
                pass
        return NodeViolationHistory(node_id=node_id, pipeline="")

    def _save_history(self, history: NodeViolationHistory) -> None:
        self._ensure_dirs()
        hist_file = self._evo_dir / "nodes" / f"{history.node_id}.history.json"
        hist_file.write_text(
            json.dumps(history.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def _save_pending_correction(self, signal: EvolutionSignal) -> None:
        """写入待确认的 prompt 矫正建议。"""
        self._ensure_dirs()
        pc_file = self._evo_dir / "nodes" / f"{signal.source_node}.pending_correction.json"
        data = {
            "node_id": signal.source_node,
            "pipeline": signal.source_pipeline,
            "signal_id": signal.signal_id,
            "rule_violated": signal.rule_violated,
            "repeat_count": signal.repeat_count,
            "suggested_correction": signal.suggested_correction,
            "created_at": signal.detected_at,
            "status": "pending",   # pending / applied / rejected
            "applied_at": None,
            "applied_by": None,
        }
        pc_file.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def _save_restriction_request(self, signal: EvolutionSignal) -> None:
        """写入 Level 2 写入限制请求（等待人工确认）。"""
        self._ensure_dirs()
        rr_file = self._evo_dir / "nodes" / f"{signal.source_node}.restriction_request.json"
        data = {
            "node_id": signal.source_node,
            "signal_id": signal.signal_id,
            "rule_violated": signal.rule_violated,
            "repeat_count": signal.repeat_count,
            "requested_at": signal.detected_at,
            "confirm_by": None,   # 人工填写确认时间
            "status": "pending_confirmation",
            "blocked_patterns": _BLOCKED_PATTERNS.get(signal.rule_violated, []),
        }
        rr_file.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        logger.warning(
            "[OmniEvolve L2] restriction_request 已写入，"
            "确认命令: omni guardian evolution-confirm-restriction %s",
            signal.source_node,
        )

    def _update_index(self, signal: EvolutionSignal) -> None:
        """更新 .omni/evolution/index.json。"""
        self._ensure_dirs()
        index_file = self._evo_dir / "index.json"
        try:
            index = json.loads(index_file.read_text(encoding="utf-8")) if index_file.exists() else []
            entry = {
                "signal_id": signal.signal_id,
                "node_id": signal.source_node,
                "pipeline": signal.source_pipeline,
                "rule": signal.rule_violated,
                "level": signal.escalation_level,
                "repeat_count": signal.repeat_count,
                "detected_at": signal.detected_at,
            }
            index = [e for e in index if e.get("signal_id") != signal.signal_id]
            index.append(entry)
            index_file.write_text(
                json.dumps(index, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception as e:
            logger.debug("[OmniEvolve] index 更新失败: %s", e)

    # ── 查询接口 ─────────────────────────────────────────────────

    def get_node_history(self, node_id: str) -> Optional[NodeViolationHistory]:
        hist_file = self._evo_dir / "nodes" / f"{node_id}.history.json"
        if not hist_file.exists():
            return None
        try:
            data = json.loads(hist_file.read_text(encoding="utf-8"))
            return NodeViolationHistory.from_dict(data)
        except Exception:
            return None

    def get_pending_correction(self, node_id: str) -> Optional[dict]:
        pc_file = self._evo_dir / "nodes" / f"{node_id}.pending_correction.json"
        if not pc_file.exists():
            return None
        try:
            return json.loads(pc_file.read_text(encoding="utf-8"))
        except Exception:
            return None

    def apply_correction(self, node_id: str, applied_by: str = "human") -> bool:
        """将 pending_correction 标记为已应用（实际 prompt 修改由人工完成）。"""
        pc_file = self._evo_dir / "nodes" / f"{node_id}.pending_correction.json"
        if not pc_file.exists():
            return False
        try:
            data = json.loads(pc_file.read_text(encoding="utf-8"))
            data["status"] = "applied"
            data["applied_at"] = datetime.now(timezone.utc).isoformat()
            data["applied_by"] = applied_by
            pc_file.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            # 更新节点历史中最后一条的 correction_applied
            history = self._load_history(node_id)
            if history.violations:
                history.violations[-1]["correction_applied"] = True
                history.violations[-1]["correction_ts"] = data["applied_at"]
                self._save_history(history)
            return True
        except Exception as e:
            logger.debug("[OmniEvolve] apply_correction 失败: %s", e)
            return False

    def list_all(self) -> list[dict]:
        """返回所有进化信号索引。"""
        index_file = self._evo_dir / "index.json"
        if not index_file.exists():
            return []
        try:
            return json.loads(index_file.read_text(encoding="utf-8"))
        except Exception:
            return []

    def record_clean_run(self, node_id: str) -> None:
        """记录一次合规运行（连续合规后可降级 escalation_level）。"""
        history = self._load_history(node_id)
        if not history.total_violations:
            return
        history.consecutive_clean_runs += 1
        # 连续 5 次合规 → 降回 Level 0（重置计数）
        if history.consecutive_clean_runs >= 5:
            old_level = history.escalation_level()
            history.total_violations = max(0, history.total_violations - 2)
            history.consecutive_clean_runs = 0
            logger.info(
                "[OmniEvolve] 节点 %s 连续 5 次合规，escalation_level %d → %d",
                node_id, old_level, history.escalation_level(),
            )
        self._save_history(history)


# BlockedPatternFilter 的封锁模式（Level 2 使用）
_BLOCKED_PATTERNS: dict[str, list[str]] = {
    "OMNI-003": ["import anthropic", "from anthropic", "import openai", "from openai"],
    "OMNI-002": ["src/omnicompany/runtime/", "src/omnicompany/protocol/"],
    "OMNI-004": ["async def run(self"],
}
