# [OMNI] origin=claude-code domain=omnicompany/doctor ts=2026-04-22T00:00:00Z type=worker status=deprecated
# [OMNI] summary="MaterialContextualAuditWorker — V3 LLM Format 全语境审计 worker. 软归档 2026-05-06: 判定点已反推为 H-2026-05-06-016..020 进新假设库, 用 HypothesisDiagnosticAgent 替代"
# [OMNI] why="诊断重制阶段 3 项 2: D 类老 LLM worker 软归档. 物理归档待 V3 管线整体替换 (plan §六 阶段 8)"
# [OMNI] tags=worker,doctor,deprecated,phase-3-soft-archive
# [OMNI] material_id="material:diagnosis.doctor.worker.material.llm_semantic_auditor.py"
"""MaterialContextualAuditWorker — LLM 语义审计 (SOFT, Stage 3 Clean Migration 2026-04-22).

## DEPRECATED (2026-05-06 软归档, V3 整体替换时物理归档)

本 worker 的判定维度已反推为新假设入 `data/services/doctor/hypotheses/`:
- H-2026-05-06-016: Format 健康审计应同时比对上下游 Router 实际产出/期望 (不孤立检查)
- H-2026-05-06-017: Format 描述应实质语义信息 (关键词匹配 ≠ 语义满足)
- H-2026-05-06-018: LLM 详细定性报告按 material_id + git hash 分目录存档
- H-2026-05-06-019: F-01 五要素 + F-06 schema 一致 + F-08 precondition 对称, 多维交叉验证
- H-2026-05-06-020: LLM 失败应返空 dict 让调用方降级 SKIP, 不抛异常阻断管线

新替代路径: 用 `HypothesisDiagnosticAgent` 跑这 5 条假设替代本 worker.

物理归档时机: plan §六 阶段 8 (闭环跑通 + 用户验收) 后.

---

Worker 协议:
  FORMAT_IN  = doctor.material.extracted
  FORMAT_OUT = doctor.material.check.llm-audit

诊断目标: LLM 驱动的全语境 Format 语义审计. 向 LLM 提供:
  ① Format 完整定义 (id/description/schema/examples/tags/parent)
  ② 上游 Router 源码 (FORMAT_OUT == material_id 的 Router 类)
  ③ 下游 Router 源码 (FORMAT_IN  == material_id 的 Router 类)
  ④ docs/standards/material.md 全文 (F-01~F-13 + 4 原则 + FA 反模式)

审计维度:
  - F-01 五要素 (字段语义 / 枚举 / 上游承诺 / 下游用途 / 最小样例)
  - F-06 schema ↔ description 一致性
  - F-08 semantic_preconditions ↔ required_tags 对称性
  - FA-01/04/05/06/07 反模式检测
  - 上游产出匹配度 (Format 是否精确描述上游 Router 的实际产出)
  - 下游期望匹配度 (Format 是否精确描述下游 Router 的实际期望)

LLM 产出详细定性报告, 存档到: data/doctor/audit/<format_id_safe>/<git_short_hash>.md
LLM 失败时降级为 SKIP (不阻断管线).
"""
from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path
from typing import Any

from omnicompany.packages.services._core.omnicompany import Worker
from omnicompany.protocol.anchor import Verdict, VerdictKind

from ._shared import DEFAULT_SOURCE_ROOT, logger


_MAX_SRC = 5000   # 单个 Router 源码最大字符数 (避免 prompt 过长)

_SYSTEM = """\
你是 OmniCompany 的 Format 语义审计员。你将获得：
1. 一个 Format 的完整定义
2. 产出该 Format 的上游 Router 源码（可能为空，说明未找到）
3. 消费该 Format 的下游 Router 源码（可能为空，说明未找到）
4. 完整的 Format 健康标准文档

你的任务：对该 Format 的语义质量进行深度定性评估，产出结构化 JSON + 完整 Markdown 报告。

## 重要原则

- 评估基于**语义理解**，不是关键词检测
- 描述中包含某个词不等于满足对应标准；描述需要提供实质信息
- 上游 Router 的 run() 方法中产出的字段 = 该 Format 应描述的内容
- 下游 Router 的 run() 方法中访问的字段 = 该 Format 的消费者期望

## 输出格式

严格输出合法 JSON（不要 markdown 代码块），字段如下：

{
  "f01_field_semantics": true/false,
  "f01_enum_invariants": true/false,
  "f01_upstream_promises": true/false,
  "f01_downstream_usage": true/false,
  "f01_minimal_example": true/false,
  "f06_schema_coherent": true/false,
  "f08_preconditions_symmetric": true/false,
  "fa01_hollow": true/false,
  "fa04_semantic_overload": true/false,
  "fa05_clone_rename": true/false,
  "fa06_semantic_break": true/false,
  "fa07_heterogeneous_mix": true/false,
  "upstream_match": true/false,
  "upstream_match_notes": "上游产出与 Format 描述的差异（若无上游 Router 源码则注明）",
  "downstream_match": true/false,
  "downstream_match_notes": "下游期望与 Format 描述的差异（若无下游 Router 源码则注明）",
  "overall_grade": "A/B/C/D",
  "key_findings": "最关键的 1-3 条发现（具体，有针对性）",
  "improvement_suggestions": "具体改进建议",
  "detailed_report": "完整 Markdown 审计报告，包含各维度分析、上下游匹配分析、综合评级"
}

评级标准（overall_grade）：
- A: F-01 五要素全部满足 + 无高危反模式 + 上下游匹配良好
- B: F-01 部分满足（≥3/5）+ 上下游基本匹配
- C: F-01 不足一半 OR 存在重要不匹配
- D: 描述空洞无信息增益（FA-01）OR 严重误导消费者
"""


class MaterialContextualAuditWorker(Worker):
    """LLM 驱动的全语境 Format 语义审计 + git 存档."""

    DESCRIPTION = "LLM 语义审计: format + 上下游 Router 源码 + 完整标准 → 定性报告 + git 存档"
    FORMAT_IN = "doctor.material.extracted"
    FORMAT_OUT = "doctor.material.check.llm-audit"

    def __init__(self, model: str | None = None):
        # 项目唯一 LLM (铁律): qwen3.6-plus. 保留 model 参数以便覆写测试.
        self._model = model or "qwen3.6-plus"

    def run(self, input_data: Any) -> Verdict:
        material_id: str = input_data["material_id"]
        extracted: dict = input_data.get("extracted", {})
        format_obj: dict = extracted.get("format_obj", {})
        usages: list = extracted.get("usages", [])
        source_root = Path(input_data.get("source_root", DEFAULT_SOURCE_ROOT))

        # 上下游 Router 源码
        upstream_entries = self._load_router_sources(source_root, usages, "OUTPUT")
        downstream_entries = self._load_router_sources(source_root, usages, "INPUT")

        # 标准文档
        standards = self._load_standards(source_root)

        # LLM 审计
        audit_data, raw_report = self._audit(
            material_id, format_obj,
            upstream_entries, downstream_entries, standards,
        )

        # 存档
        audit_path = self._archive_report(material_id, source_root, audit_data, raw_report)

        # 构造 check 结果
        f01_pass = all(audit_data.get(k, False) for k in (
            "f01_field_semantics", "f01_enum_invariants",
            "f01_upstream_promises", "f01_downstream_usage", "f01_minimal_example",
        ))
        has_antipattern = any(audit_data.get(k, False) for k in (
            "fa01_hollow", "fa04_semantic_overload",
            "fa05_clone_rename", "fa06_semantic_break", "fa07_heterogeneous_mix",
        ))
        grade = audit_data.get("overall_grade", "?")
        check_result = {
            "check": "contextual_audit",
            "passed": grade in ("A", "B"),
            "severity": "MEDIUM" if grade in ("C", "D") else "INFO",
            "detail": audit_data.get("key_findings", "LLM 审计跳过"),
            "grade": grade,
            "audit_path": str(audit_path) if audit_path else None,
            "sub_checks": [
                {"name": "f01_five_elements",       "passed": f01_pass},
                {"name": "f06_schema_coherent",     "passed": audit_data.get("f06_schema_coherent", True)},
                {"name": "f08_preconditions",       "passed": audit_data.get("f08_preconditions_symmetric", True)},
                {"name": "no_antipatterns",         "passed": not has_antipattern},
                {"name": "upstream_match",          "passed": audit_data.get("upstream_match", True)},
                {"name": "downstream_match",        "passed": audit_data.get("downstream_match", True)},
            ],
        }

        return Verdict(
            kind=VerdictKind.PASS,
            confidence=1.0,
            output={
                "material_id": material_id,
                "source_root": input_data.get("source_root", ""),
                "sig_diff_ok": input_data.get("sig_diff_ok", True),
                "extracted": extracted,
                "check_llm_audit": check_result,
            },
            diagnosis=f"ContextualAudit: {material_id} grade={grade}",
        )

    # ── 内部工具 ──────────────────────────────────────────────────

    def _load_standards(self, source_root: Path) -> str:
        """加载 docs/standards/material.md."""
        project_root = source_root.resolve().parents[1]
        standards_path = project_root / "docs" / "standards" / "material.md"
        try:
            return standards_path.read_text(encoding="utf-8")
        except Exception:
            return "(standards/material.md 未找到)"

    def _load_router_sources(
        self,
        source_root: Path,
        usages: list[dict],
        role: str,  # "INPUT" or "OUTPUT"
    ) -> list[dict]:
        """从 usages 列表中找到 INPUT/OUTPUT 角色的文件, 加载对应 Router 类源码."""
        entries: list[dict] = []
        seen_files: set[str] = set()
        for usage in usages:
            if role not in usage.get("role", ""):
                continue
            file_rel: str = usage["file"]
            if file_rel in seen_files:
                continue
            seen_files.add(file_rel)
            try:
                full_path = source_root.resolve().parent / file_rel
                content = full_path.read_text(encoding="utf-8", errors="ignore")
            except Exception:
                continue
            class_name = self._class_owning_line(content, usage.get("line", ""))
            src_excerpt = (
                self._extract_class_source(content, class_name)
                if class_name
                else content[:_MAX_SRC]
            )
            if len(src_excerpt) > _MAX_SRC:
                src_excerpt = src_excerpt[:_MAX_SRC] + "\n... [truncated]"
            entries.append({
                "file": file_rel,
                "class": class_name or "(unknown)",
                "source": src_excerpt,
            })
        return entries

    def _class_owning_line(self, content: str, target_line_stripped: str) -> str | None:
        """给定一行内容 (stripped), 找到该行所在的类名."""
        lines = content.splitlines()
        class_pat = re.compile(r"^class\s+(\w+)")
        current_class: str | None = None
        for line in lines:
            m = class_pat.match(line)
            if m:
                current_class = m.group(1)
            if target_line_stripped and target_line_stripped[:60] in line:
                return current_class
        return current_class

    def _extract_class_source(self, content: str, class_name: str) -> str:
        """提取指定类的全部源码 (从 class 行到下一个顶层 class/def)."""
        lines = content.splitlines()
        pat = re.compile(r"^class\s+" + re.escape(class_name) + r"\b")
        start: int | None = None
        for i, line in enumerate(lines):
            if pat.match(line):
                start = i
                break
        if start is None:
            return content[:_MAX_SRC]
        end = len(lines)
        for i in range(start + 1, len(lines)):
            if re.match(r"^class\s", lines[i]) or re.match(r"^def\s", lines[i]):
                end = i
                break
        return "\n".join(lines[start:end])

    def _build_user_msg(
        self,
        material_id: str,
        format_obj: dict,
        upstreams: list[dict],
        downstreams: list[dict],
        standards: str,
    ) -> str:
        """构造 LLM 用户消息."""
        parts: list[str] = []

        parts.append("# Format 定义")
        parts.append(f"**ID**: {material_id}")
        parts.append(f"**description**: {format_obj.get('description') or '(空)'}")
        if format_obj.get("json_schema"):
            parts.append(f"**json_schema**: {json.dumps(format_obj['json_schema'], ensure_ascii=False)}")
        if format_obj.get("examples"):
            parts.append(f"**examples**: {json.dumps(format_obj['examples'], ensure_ascii=False)}")
        parts.append(f"**tags**: {format_obj.get('tags', [])}")
        parts.append(f"**parent**: {format_obj.get('parent') or '(无)'}")

        if upstreams:
            for u in upstreams:
                parts.append("\n# 上游 Router (产出此 Format)")
                parts.append(f"**文件**: {u['file']}  **类**: {u['class']}")
                parts.append(f"```python\n{u['source']}\n```")
        else:
            parts.append("\n# 上游 Router\n(未找到 FORMAT_OUT 引用, 可能是管线起点或名称不一致)")

        if downstreams:
            for d in downstreams:
                parts.append("\n# 下游 Router (消费此 Format)")
                parts.append(f"**文件**: {d['file']}  **类**: {d['class']}")
                parts.append(f"```python\n{d['source']}\n```")
        else:
            parts.append("\n# 下游 Router\n(未找到 FORMAT_IN 引用, 可能是管线终点或名称不一致)")

        parts.append(f"\n# Format 健康标准文档\n{standards}")
        parts.append("\n请对以上 Format 进行语义审计, 输出 JSON 报告.")

        return "\n\n".join(parts)

    def _audit(
        self,
        material_id: str,
        format_obj: dict,
        upstreams: list[dict],
        downstreams: list[dict],
        standards: str,
    ) -> tuple[dict, str]:
        """调用 LLM; 返回 (audit_data_dict, raw_llm_text). 失败时返回空 dict."""
        if not format_obj.get("description"):
            return {}, "(description 缺失, 跳过 LLM 审计)"

        try:
            from omnicompany.runtime.llm.llm import LLMClient
            client = LLMClient(role="runtime_main")
            user_msg = self._build_user_msg(material_id, format_obj, upstreams, downstreams, standards)
            resp = client.call(
                messages=[{"role": "user", "content": user_msg}],
                system=_SYSTEM,
            )
            raw = resp.content[0].text.strip()
            if raw.startswith("```"):
                raw = re.sub(r"^```[a-z]*\n?", "", raw)
                raw = re.sub(r"\n?```$", "", raw.strip())
            data = json.loads(raw)
            return data, raw
        except Exception as e:
            logger.warning("FormatContextualAudit LLM call failed for %s: %s", material_id, e)
            return {}, f"(LLM 调用失败: {type(e).__name__}: {e})"

    def _get_git_hash(self, source_root: Path) -> str:
        try:
            result = subprocess.run(
                ["git", "rev-parse", "--short", "HEAD"],
                capture_output=True, text=True,
                cwd=str(source_root.resolve().parents[1]),
                timeout=5,
            )
            return result.stdout.strip() or "unknown"
        except Exception:
            return "unknown"

    def _archive_report(
        self,
        material_id: str,
        source_root: Path,
        audit_data: dict,
        raw_report: str,
    ) -> Path | None:
        """将 Markdown 报告存档到 data/doctor/audit/{safe_id}/{git_hash}.md."""
        if not audit_data:
            return None
        try:
            git_hash = self._get_git_hash(source_root)
            safe_id = material_id.replace(".", "_")
            audit_dir = source_root.resolve().parents[1] / "data" / "doctor" / "audit" / safe_id
            audit_dir.mkdir(parents=True, exist_ok=True)

            detailed = audit_data.get("detailed_report", raw_report)
            report_lines = [
                f"# Format 审计报告: {material_id}",
                "",
                f"**Commit**: `{git_hash}`  **Grade**: {audit_data.get('overall_grade', '?')}",
                "",
                detailed,
            ]
            report_path = audit_dir / f"{git_hash}.md"
            report_path.write_text("\n".join(report_lines), encoding="utf-8")
            return report_path
        except Exception as e:
            logger.warning("Failed to archive audit report for %s: %s", material_id, e)
            return None


# 向后兼容别名 (run.py 引用了旧名称时不会立刻崩溃)
DescriptionEvaluatorWorker = MaterialContextualAuditWorker
