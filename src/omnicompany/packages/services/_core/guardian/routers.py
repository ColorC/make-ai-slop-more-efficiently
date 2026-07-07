# [OMNI] origin=claude-code domain=omnicompany/guardian ts=2026-04-20T00:00:00Z type=shim
# [OMNI] material_id="material:core.guardian.routers.compatibility_shim.py"
# [OMNI] migrated 2026-05-02: 旧 runtime.agent.agent_node_loop.AgentNodeLoop 已 deprecate
# [OMNI] 现在用 packages.services._core.agent.AgentNodeLoop (router 化新基础设施)
"""guardian/routers.py — 向后兼容 shim + HealthReporter AgentNodeLoop (Clean Migration 2026-04-20).

两部分:
  1. 旧名 alias (FsScannerRouter / ArchAuditorRouter 自动 → FsScannerWorker / ArchAuditorWorker)
  2. HealthReporterRouter 原样保留 (AgentNodeLoop 子类不迁, Phase 1 runtime 统一后处理)

真实 Worker 实现在 `workers/` 目录。本文件仅为:
  - 旧 `from ...guardian.routers import FsScannerRouter` 继续工作 (→ FsScannerWorker 别名)
  - HealthReporterRouter 仍在这里 (因为它是 AgentNodeLoop 而非 Worker, 不属于 workers/)

不要往本文件加新逻辑; 新增 Worker 请写 `workers/<name>.py`。
归档: `_archive/routers_legacy.py` 保留旧 3-class 单文件实现供历史追溯。
"""
from __future__ import annotations

import json
import logging
from typing import Any, ClassVar

from omnicompany.protocol.anchor import Verdict, VerdictKind
from omnicompany.runtime.agent.agent_loop_config import LoopConfig, CompactConfig, PermissionConfig
from omnicompany.packages.services._core.agent import (
    AgentNodeLoop,
    GrepRouter,
    GlobRouter,
    ListDirRouter,
    ReadFileRouter,
)
from omnicompany.packages.services._core.agent.routers.prompt_builder import PromptBuilderRouter
from omnicompany.packages.services._core.agent.routers.extract_result import ExtractResultRouter

from .workers import FsScannerWorker, ArchAuditorWorker

logger = logging.getLogger(__name__)


# ─── 旧名别名 (兼容) ────────────────────────────────────────────────────────
FsScannerRouter = FsScannerWorker
ArchAuditorRouter = ArchAuditorWorker


# ════════════════════════════════════════════════════════════
# HealthReporterRouter — AgentNodeLoop 子类 (已迁移至 router 化架构 2026-05-02)
# ════════════════════════════════════════════════════════════

_NODE_PROMPT = """\
你是一个项目架构健康度评估专家。你将收到自动扫描器产出的事实清单。

你可以使用工具探查具体文件内容来辅助判断：
- 用 read_file 查看 DEPRECATED 模块是否真的没人在用
- 用 grep 搜索某个模块是否被 import
- 用 list_dir 查看某个目录下的实际内容

**不打分** (铁律 2026-04-25): 绝不返回 health_score / quality_score 等数字. 分数没有统一尺度.
保留完整语义信号 — 每个问题都要给 severity + evidence + fix_hint.

评判原则:
- 散落的 .db/.json 文件在项目根目录 = 真正的架构污染, severity=major 或 critical
- DEPRECATED 模块如果仍被 import = 需迁移, severity=major
- 盘根目录 (C:/ E:/) 出现项目文件 = critical
- Router 缺少 DESCRIPTION = minor
- 空 __init__.py 完全正常, 不报告

## 严重度 (仅类别标签, 不加权求和)
- `critical`: 结构违规 / 编造 / 核心丢失 · **触发 FAIL**
- `major`: 命名/引用错 / 违反约定 / 缺升级路径
- `minor`: 措辞瑕疵 / 格式小问题

## 客观 evidence 铁律
每 issue 必须带 `evidence` · 是扫描事实或文件原文片段 · 不是你的判断.

完成评估后, 使用 finish 工具输出结果. finish 的 result 字段必须是严格 JSON:
{
    "verdict": "healthy" | "unhealthy" | "uncertain",
    "issues": [
        {
            "severity": "critical" | "major" | "minor",
            "category": "<如 root_contamination / omnimark_missing>",
            "field": "<具体路径或规则 id>",
            "message": "<具体问题描述>",
            "evidence": "<扫描事实 / 文件原文片段 · 客观可核验>",
            "fix_hint": "<具体怎么改>"
        }
    ],
    "summary": "<总体语义描述 · 非好坏判词 · 例 '根污染 3 处, 其余合规'>",
    "top_actions": ["<前 1-3 条改进建议>"],
    "report": "<完整的中文可读报告>"
}

issues 无即空数组 []. **不瞎编凑数**. 不返回 health_score 字段 (保留完整 issue 列表即可)."""


class _HealthReporterPromptBuilder(PromptBuilderRouter):
    """HealthReporter 自定义首轮 user message — 按 category 聚合扫描问题."""

    DESCRIPTION = "Guardian health reporter prompt builder assembles scanner facts into a strict JSON-only health assessment prompt."
    FORMAT_IN: ClassVar[str] = "agent.prompt-request"
    FORMAT_OUT: ClassVar[str] = "agent.prompt-built"

    def build_initial_messages(self, input_data: dict) -> list[dict]:
        fs_issues = input_data.get("fs_issues", [])
        arch_issues = input_data.get("arch_issues", [])
        all_issues = fs_issues + arch_issues

        if not all_issues:
            return [{"role": "user", "content": (
                "自动扫描未发现问题。请直接用 finish 输出严格 JSON: "
                '{"verdict":"healthy","issues":[],"summary":"自动扫描未发现问题",'
                '"top_actions":[],"report":"自动扫描未发现问题"}'
            )}]

        # 按 category 聚合
        by_category: dict[str, list[dict]] = {}
        for issue in all_issues:
            cat = issue.get("category", "unknown")
            by_category.setdefault(cat, []).append(issue)

        lines = [f"自动扫描发现 {len(all_issues)} 个问题：\n"]
        for cat, items in sorted(by_category.items()):
            lines.append(f"## {cat} ({len(items)} 个)")
            for item in items[:8]:
                lines.append(f"- {item.get('detail', '')}  路径: {item.get('path', '')}")
            if len(items) > 8:
                lines.append(f"- ... 另外 {len(items) - 8} 个同类问题")
            lines.append("")

        lines.append(
            "请评估这些问题的真实严重性。你可以用工具探查具体文件来辅助判断。"
            "完成后用 finish 输出 JSON 结果。"
        )

        return [{"role": "user", "content": "\n".join(lines)}]


class _HealthReporterExtractResult(ExtractResultRouter):
    """HealthReporter 自定义产物提取 — parse JSON health report, fallback 到 FAIL verdict."""

    DESCRIPTION = "Guardian health reporter result extractor parses strict JSON reports and separates reporter infrastructure failures."
    FORMAT_IN: ClassVar[str] = "agent.result-request"
    FORMAT_OUT: ClassVar[str] = "guardian.health-report"

    def extract(
        self,
        *,
        final_text: str,
        messages: list[dict],
        turn_count: int,
        stop_reason: str,
    ) -> Verdict:
        # 从原始 input 恢复 issues（用于传递给下游）
        # build_initial_messages 的 input_data 不在这里了，从 messages 推断
        fs_issues: list[dict] = []
        arch_issues: list[dict] = []

        try:
            text = final_text.strip()
            if "```" in text:
                for part in text.split("```"):
                    if part.startswith("json"):
                        text = part[4:].strip()
                        break
                    elif "{" in part:
                        text = part.strip()
                        break

            parsed = json.loads(text)
        except Exception as e:
            logger.error("[health_reporter] 结果解析失败: %s", e)
            # Reporter infra failure: the health result is unusable, but this is not
            # evidence of a critical repository-structure issue.
            parse_err_issue = {
                "severity": "major",
                "category": "llm_output_parse_error",
                "field": "extract_result",
                "message": f"LLM 输出 JSON 解析失败: {e}",
                "evidence": f"raw final_text (first 500 chars): {final_text[:500]!r}",
                "fix_hint": "重跑; 若持续, 检查 LLM system_prompt 输出格式约束",
            }
            return Verdict(
                kind=VerdictKind.FAIL,
                output={
                    "verdict": "uncertain",              # 非 "healthy"
                    "passed": False,
                    "issues": [parse_err_issue],
                    "counts": {"critical": 0, "major": 1, "minor": 0},
                    "total_issues": 1,
                    "top_actions": [],
                    "fs_issues": fs_issues,
                    "arch_issues": arch_issues,
                    "infrastructure_error": True,
                    "report": f"LLM 输出解析失败: {e}",
                    "summary": "parse_error · LLM 响应不合规 JSON",
                },
                diagnosis=f"LLM output parse failed: {e}",
            )

        # 新契约: 从 issues 数组按 severity 计类别 · 不算分数
        raw_issues = parsed.get("issues") or []
        issues: list[dict] = []
        for it in raw_issues:
            if not isinstance(it, dict):
                continue
            sev = (it.get("severity") or "minor").lower()
            if sev not in ("critical", "major", "minor"):
                sev = "minor"
            issues.append({
                "severity": sev,
                "category": str(it.get("category") or "unknown"),
                "field":    str(it.get("field") or ""),
                "message":  str(it.get("message") or ""),
                "evidence": str(it.get("evidence") or ""),
                "fix_hint": str(it.get("fix_hint") or ""),
            })
        counts = {
            "critical": sum(1 for i in issues if i["severity"] == "critical"),
            "major":    sum(1 for i in issues if i["severity"] == "major"),
            "minor":    sum(1 for i in issues if i["severity"] == "minor"),
        }
        # Gate: 有 critical 即 FAIL · 保留 verdict 字符串作语义标签
        passed = counts["critical"] == 0
        kind = VerdictKind.PASS if passed else VerdictKind.FAIL

        verdict_str = parsed.get("verdict") or ("healthy" if passed else "unhealthy")
        report = parsed.get("report") or parsed.get("summary") or ""
        summary = parsed.get("summary") or ""

        return Verdict(
            kind=kind,
            output={
                "verdict": verdict_str,                  # 语义标签
                "passed": passed,                        # binary gate
                "issues": issues,                        # 全量 issue
                "counts": counts,                        # 类别计数 · 非分数
                "total_issues": len(issues),             # 向后兼容 · 等于 len(issues)
                "top_actions": parsed.get("top_actions") or [],
                "fs_issues": fs_issues,
                "arch_issues": arch_issues,
                "report": report,
                "summary": summary,
            },
            diagnosis=report if not passed else None,
        )


class HealthReporterRouter(AgentNodeLoop):
    """LLM Agent 评估项目健康度。

    继承 AgentNodeLoop：可以自主探查文件、grep 搜索、列目录，
    基于扫描事实 + 实际文件内容做上下文判断，而非硬规则算分。

    注: 本类已迁移至 router 化架构 (2026-05-02)。
    不属于 Worker 架构, 不迁入 workers/ 子目录。
    """

    NODE_PROMPT: ClassVar[str] = _NODE_PROMPT
    TOOL_ROUTERS: ClassVar[list] = [ReadFileRouter, GrepRouter, GlobRouter, ListDirRouter]
    LOOP_CONFIG: ClassVar[LoopConfig] = LoopConfig(
        max_turns=15,
        compact=CompactConfig(auto_compact_enabled=False),  # 轮数少不需要压缩
        permission=PermissionConfig(mode="readonly"),  # 健康检查只读
    )

    DESCRIPTION = "AgentNodeLoop guardian health reporter evaluates repository health from scanner facts with read-only file probes."
    FORMAT_IN = "guardian.arch-report"
    FORMAT_OUT = "guardian.health-report"

    def __init__(
        self,
        *,
        model: str | None = None,
        bus: Any | None = None,
        config: LoopConfig | None = None,
    ):
        super().__init__(model=model, bus=bus, config=config or self.LOOP_CONFIG)

    # ── 子类钩子: 自定义 PromptBuilder + ExtractResult ──

    def build_prompt_builder(self, *, bus: Any) -> PromptBuilderRouter:
        return _HealthReporterPromptBuilder(template=self.NODE_PROMPT, bus=bus)

    def build_extract_result(self, *, bus: Any) -> ExtractResultRouter:
        return _HealthReporterExtractResult(bus=bus)


__all__ = [
    # 新名 Worker (推荐)
    "FsScannerWorker",
    "ArchAuditorWorker",
    # 旧名 alias (兼容)
    "FsScannerRouter",
    "ArchAuditorRouter",
    # AgentNodeLoop (已迁移至 router 化架构)
    "HealthReporterRouter",
]
