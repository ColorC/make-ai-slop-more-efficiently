# [OMNI] origin=claude-code domain=services/absorption ts=2026-04-14T00:00:00Z type=router
# [OMNI] material_id="material:learning.absorption.v3_legacy.report_incremental_updater.router.py"
"""report_updater — ReportUpdaterV3Router（LLM 增量更新）

与 ReportWriterV3Router 的区别：
  ReportWriterV3  = 起草：从 findings 从零生成完整报告
  ReportUpdaterV3 = 增量更新：将补充探索的新发现融入已有报告

输入来自补充路径：
  - report_md / report_path：已有报告（Iteration 1 写出的）
  - findings：本轮 supplement_extractor 产出的新发现
  - previous_findings：上一轮已有的发现（避免重复、了解已有内容）
  - supplement_guidance：人工指定的补充方向
  - iteration：当前轮次

输出：更新后的 absorption.report.v3（覆盖写 report.md，不是追加）

FORMAT_IN:  absorption.learning  （来自 supplement_extractor）
FORMAT_OUT: absorption.report.v3
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from omnicompany.core.config import resolve_domain_data_dir
from omnicompany.core.guarded_write import write_file as _guarded_write
from omnicompany.protocol.anchor import Verdict, VerdictKind
from omnicompany.runtime.routing.router import Router
from omnicompany.packages.services._learning.absorption._archive.routers_v3_legacy.report_writer import (
    _build_finding_with_code,
    _split_report_parts,
)

_MODEL = "qwen3.6-plus"

_UPDATER_SYSTEM = """你是 OmniCompany 吸纳系统的报告更新编辑。

你会收到：
1. 已有的完整吸纳报告（上一轮写成的）
2. 本轮基于人工反馈补充探索的新发现
3. 人工指定的补充方向

你的任务是**增量更新**，而不是重新起草：

## 更新规则

**Part 1（精炼摘要）更新**：
- 在"四、发现速览"表格末尾追加新发现行
- 在"五、改进提案"表格末尾追加新提案行（仅 P0/P1）
- 不改动其他章节（概览、架构、能力地图保持不变，除非新发现明显修正了原有描述）

**Part 2（详细展开）更新**：
- 在现有发现章节之后追加新发现的详细章节（格式与原报告一致）
- 新章节标题格式：`## 发现 N：<标题> [gap_id][priority][portability]`

**末尾增加迭代说明**：
```
## Iteration <N> 补充记录（<日期>）
**人工反馈方向**：
- 方向1
- 方向2

**新增发现**：<X> 个（补充探索 <Y> 个文件）
```

## 输出格式

与原报告相同：
```
<更新后的完整 Part 1>

---DETAIL---

<更新后的完整 Part 2（含新追加章节）>

---JSON---
{"repo_overview": "...", "architecture": "...", "capability_map": {...}, "highlights": [...], "proposals": [...]}
```

**重要**：输出的是整份报告的完整文本（包含原有内容 + 新增内容），不是只输出新增部分。
"""


class ReportUpdaterV3Router(Router):
    """V3 报告增量更新节点（LLM）。

    基于人工反馈的补充探索发现，将新内容融入已有报告，而不是重新起草。
    与 ReportWriterV3Router 的分工：Writer=起草，Updater=增量融合。

    来自 supplement_extractor 的 absorption.learning → 更新后的 absorption.report.v3
    """

    DESCRIPTION = (
        "V3 报告增量更新：将 supplement_extractor 的新发现融入已有报告，"
        "追加发现速览行、改进提案行和 Part 2 详细章节，覆盖写 report.md，不重新起草"
    )
    FORMAT_IN = "absorption.learning"
    FORMAT_OUT = "absorption.report.v3"

    _MODEL = _MODEL

    def __init__(self, *, model: str | None = None, **kwargs: Any) -> None:
        self._model = model or self._MODEL

    def run(self, input_data: Any) -> Verdict:
        repo_name = input_data.get("repo_name", "unknown")
        new_findings: list[dict] = input_data.get("findings") or []
        new_overall: dict = input_data.get("overall_assessment") or {}
        new_module_readings: list[dict] = input_data.get("module_readings") or []
        new_files_read: list[str] = input_data.get("files_read") or []
        repo_local_path: str = input_data.get("repo_local_path", "")

        # 上一轮产出（通过 FeedbackRouterV3 的 supplement_request 携带）
        previous_findings: list[dict] = list(input_data.get("previous_findings") or [])
        previous_report_md: str = input_data.get("report_md", "")
        supplement_guidance: str = input_data.get("supplement_guidance", "")
        iteration: int = int(input_data.get("iteration", 2))
        feedback_incorporated: list[str] = list(input_data.get("feedback_incorporated") or [])

        if not new_findings:
            return Verdict(
                kind=VerdictKind.FAIL,
                output=dict(input_data),
                diagnosis="ReportUpdaterV3: 补充探索无新发现，跳过更新",
            )

        if not previous_report_md:
            # 兜底：如果没有拿到已有报告文本，从磁盘读
            repo_dir = resolve_domain_data_dir("absorption") / repo_name
            report_path = repo_dir / "report.md"
            if report_path.exists():
                previous_report_md = report_path.read_text(encoding="utf-8")

        # 为新发现构建带实际代码的文本块
        finding_blocks: list[str] = []
        for i, f in enumerate(new_findings, 1):
            block = _build_finding_with_code(f, new_module_readings, repo_local_path)
            finding_blocks.append(f"## 新发现 {i}\n\n{block}")
        new_findings_with_code = "\n\n---\n\n".join(finding_blocks)

        # 解析补充方向（去掉前缀文本）
        guidance_lines = [
            ln.strip() for ln in supplement_guidance.splitlines()
            if ln.strip() and not ln.strip().startswith("本次补充")
        ]

        user_msg = f"""# 报告增量更新任务

**Repo**: {repo_name}
**当前 Iteration**: {iteration}
**补充探索方向**:
{chr(10).join(f"- {g}" for g in guidance_lines)}

**本轮新发现**: {len(new_findings)} 个（读取 {len(new_files_read)} 个文件）
**已有发现**: {len(previous_findings)} 个（来自上一轮）

---

## 已有报告全文（请在此基础上增量更新）

{previous_report_md}

---

## 本轮新发现（含实际代码）

{new_findings_with_code}

---

请按规则对报告进行增量更新，输出整份更新后的完整报告。"""

        try:
            from omnicompany.runtime.llm.llm import LLMClient
            client = LLMClient(model=self._model)
            resp = client.call(
                messages=[{"role": "user", "content": user_msg}],
                system=_UPDATER_SYSTEM,
            )
            raw = resp.content[0].text.strip()
        except Exception as e:
            return Verdict(
                kind=VerdictKind.FAIL,
                output=dict(input_data),
                diagnosis=f"ReportUpdaterV3 LLM 失败: {type(e).__name__}: {e}",
            )

        summary_md, detail_md, structured = _split_report_parts(raw)
        full_md = summary_md + "\n\n---DETAIL---\n\n" + detail_md if detail_md else summary_md

        # 覆盖写 report.md
        repo_dir = resolve_domain_data_dir("absorption") / repo_name
        repo_dir.mkdir(parents=True, exist_ok=True)
        report_path = repo_dir / "report.md"

        timestamp = datetime.now().strftime("%Y-%m-%d")
        header = (
            f"<!-- absorption-module-driven | repo={repo_name} | iteration={iteration} | "
            f"{timestamp} | updated -->\n\n"
        )
        _guarded_write(report_path, header + full_md, writer="internal-engine",
                       domain="absorption", purpose="absorption report incremental update")

        # 记录已融合的反馈
        feedback_incorporated = feedback_incorporated + [
            f"Iteration {iteration}: {g}" for g in guidance_lines[:3]
        ]

        # 合并所有发现（上一轮 + 本轮，供下游使用）
        all_findings = previous_findings + new_findings

        print(f"\n[ReportUpdaterV3] 报告已更新 (iteration={iteration}): {report_path}")
        print(f"[ReportUpdaterV3] 原有 {len(previous_findings)} 发现 + 新增 {len(new_findings)} 发现 = {len(all_findings)} 合并")

        return Verdict(
            kind=VerdictKind.PASS,
            output={
                **input_data,
                "report_path": str(report_path),
                "report_md": full_md,
                "structured": structured,
                "findings": all_findings,       # 合并后供下游 feedback_gate 判断
                "files_read": list(input_data.get("previous_files_read") or []) + new_files_read,
                "iteration": iteration,
                "feedback_incorporated": feedback_incorporated,
            },
            confidence=0.9,
            diagnosis=(
                f"ReportUpdaterV3: 已融合 {len(new_findings)} 新发现，"
                f"报告 iteration={iteration}，路径={report_path}"
            ),
            granted_tags=["domain.absorption", "stage.v3.report"],
        )
