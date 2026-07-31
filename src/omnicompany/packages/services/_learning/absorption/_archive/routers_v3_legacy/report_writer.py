# [OMNI] origin=claude-code domain=services/absorption ts=2026-04-13T00:00:00Z type=router
# [OMNI] material_id="material:learning.absorption.v3_legacy.report_writer.stage2_renderer.py"
"""report_writer — V3 Stage 2 报告管线（3 个 Router）

管线拓扑：
  learning_extractor → ReportWriterV3Router → HumanFeedbackGateV3Router
                            → FeedbackRouterV3 → EMIT（完成）
                                              → JUMP 回 module_explorer（补充学习）

设计文档：docs/plans/[2026-04-13]REPO-ABSORPTION-V3/DESIGN.md §七
"""
from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from omnicompany.core.config import resolve_domain_data_dir
from omnicompany.core.guarded_write import write_file as _guarded_write
from omnicompany.protocol.anchor import Verdict, VerdictKind
from omnicompany.runtime.routing.router import Router

_MODEL = "qwen3.6-plus"


def _absorption_repo_dir(repo_name: str) -> Path:
    """返回 data/domains/absorption/<repo_name>/ 目录路径（不自动创建）。"""
    return resolve_domain_data_dir("absorption") / repo_name


# ══════════════════════════════════════════════════════════════════════
# Router 1：ReportWriterV3Router（LLM 渲染综合报告）
# ══════════════════════════════════════════════════════════════════════

_REPORT_SYSTEM = """你是 OmniCompany 吸纳系统的报告撰写员。
你会收到外部 repo 的学习发现（findings）以及每个发现对应的实际代码片段，
需要生成一份面向工程师的深度吸纳报告。

## 报告结构

报告分两大部分，用 `---DETAIL---` 分隔：

---

### Part 1：精炼摘要（快速阅读层）

```
# Absorption Report: <repo_name>
> <一句话：这个 repo 对 OmniCompany 的核心价值>

## 一、Repo 概览
（2-3 段：定位 / 技术栈 / 规模 / 核心设计哲学）

## 二、架构
（ASCII 架构图 + 核心组件 1-2 句说明）

## 三、能力地图

| 功能域 | 描述 | 代表文件 |
|---|---|---|
...

## 四、发现速览

| # | 标题 | 缺口 | 优先级 | 可移植性 |
|---|---|---|---|---|
...（每条 1 行，不展开）

## 五、改进提案（优先级排序）

| 优先级 | 提案 | 在 OmniCompany 中的位置 | 为何重要 |
|---|---|---|---|
...

## 六、本次吸纳局限
（诚实说明：读了哪些文件，哪些方面没有深入，结论置信度）
```

---

### Part 2：详细展开（深度学习层，每个 finding 一章）

对每个 finding，生成如下章节：

```
## 发现 N：<标题> [`<gap_id>`][`<priority>`][`<portability>`]

### 理解
（3-5 段深入分析：
  - 这个模块解决了什么问题？为什么这样解决？
  - 设计决策背后的权衡是什么？
  - 与常见实现方式相比有何特别之处？
  - 生产环境中的关键细节（并发安全 / 错误处理 / 边界条件））

### 参考代码

```python
# <文件路径>（第 X-Y 行）
<直接引用你收到的代码，不要改动，完整展示关键片段>
```

（如有多处关键代码，分多个代码块展示，每块标注文件和行号）

### 学习点
（具体可迁移的知识，每条以「**[机制名]**」开头）
- **[机制名]**：具体描述这个模式/算法/设计，精确到实现细节

### 学习方向
（OmniCompany 中如何应用，以「在 `<具体文件路径>` 中」开头）
- 在 `<路径>` 中：具体要做什么，新增什么接口/类/方法
- 参考 `<原始文件:行号>` 的实现方式
- 注意：（迁移时需要关注的兼容性/依赖问题）
```

---

## 输出格式

```
<Part 1：精炼摘要 Markdown>

---DETAIL---

<Part 2：详细展开 Markdown>

---JSON---
{
  "repo_overview": "一段话",
  "architecture": "架构描述",
  "capability_map": {"功能域": "描述 [代表文件]"},
  "highlights": [{"gap_id": "G1", "title": "...", "portability": "directly_reusable", "action": "..."}],
  "proposals": [{"priority": "P0", "what": "...", "location": "core/xxx.py", "why": "..."}]
}
```

## 写作原则

- **代码引用必须直接来自你收到的代码**，不要凭记忆构造，不要改动原始代码
- **理解段必须深入**，说清楚设计决策的理由，不只是描述"做了什么"
- **学习方向必须精确**，给出具体文件路径和接口设计，不要废话
- **诚实声明局限**，没有深入的地方直接说
"""


def _resolve_file_content(
    ev_file: str,
    module_readings: list[dict],
    repo_local_path: str,
) -> tuple[str, str]:
    """按优先级解析文件内容：module_readings 缓存 → 磁盘直读。

    Returns:
        (content, source) where source is "cache" | "disk" | ""
    """
    ev_file_norm = ev_file.strip("/\\").replace("\\", "/")

    # 1. 在 module_readings 里宽松匹配（双向 endswith）
    for mr in module_readings:
        mr_path = (mr.get("path") or "").strip("/\\").replace("\\", "/")
        if mr_path == ev_file_norm or mr_path.endswith(ev_file_norm) or ev_file_norm.endswith(mr_path):
            content = mr.get("content") or ""
            if content:
                return content, "cache"

    # 2. 直接从磁盘读（module_readings 可能路径格式不同，但文件一定在 repo 里）
    if repo_local_path:
        candidate = Path(repo_local_path) / ev_file_norm
        if candidate.exists() and candidate.is_file() and candidate.stat().st_size < 512 * 1024:
            try:
                content = candidate.read_text(encoding="utf-8", errors="replace")
                return content, "disk"
            except Exception:
                pass

    return "", ""


def _build_finding_with_code(
    finding: dict,
    module_readings: list[dict],
    repo_local_path: str = "",
) -> str:
    """为单个 finding 构建含实际代码的文本块，供 LLM 撰写详细章节。

    代码来源优先级：module_readings 缓存 → repo 磁盘直读。
    """
    gap_id = finding.get("gap_id", "?")
    priority = finding.get("priority", "?")
    title = finding.get("title", "?")
    what = finding.get("what_it_does", "")
    delta = finding.get("omnifactory_delta", "")
    action = finding.get("action", "")
    portability = finding.get("portability", "?")
    evidence = finding.get("evidence") or []

    lines = [
        f"### [{priority}][{gap_id}] {title} (portability={portability})",
        f"**what_it_does**: {what}",
        f"**omnifactory_delta**: {delta}",
        f"**action**: {action}",
        "",
    ]

    code_blocks: list[str] = []
    for ev in evidence:
        ev_file = (ev.get("file") or "").strip("/\\")
        ev_lines = ev.get("lines", "")
        ev_quote = ev.get("quote", "")

        content, source = _resolve_file_content(ev_file, module_readings, repo_local_path)

        if content and ev_lines:
            code_slice = _extract_lines(content, ev_lines, context=5)
            if code_slice:
                src_tag = " [disk]" if source == "disk" else ""
                code_blocks.append(
                    f"**代码参考{src_tag}** `{ev_file}` 第 {ev_lines} 行:\n```python\n{code_slice}\n```"
                )
        elif content and ev_quote:
            idx = content.find(ev_quote[:40])
            if idx >= 0:
                start_line = content[:idx].count("\n")
                code_slice = _extract_lines(content, f"{start_line}-{start_line+30}", context=3)
                src_tag = " [disk]" if source == "disk" else ""
                code_blocks.append(
                    f"**代码参考{src_tag}** `{ev_file}` (定位自 quote):\n```python\n{code_slice}\n```"
                )
            else:
                code_blocks.append(
                    f"**代码片段** (`{ev_file}`):\n```\n{ev_quote}\n```"
                )
        elif content:
            head = "\n".join(content.splitlines()[:60])
            src_tag = " [disk]" if source == "disk" else ""
            code_blocks.append(
                f"**代码参考{src_tag}** `{ev_file}` (前 60 行):\n```python\n{head}\n```"
            )
        elif ev_quote:
            # 最后兜底：只有 LLM 截取的 quote
            code_blocks.append(
                f"**代码片段** (`{ev_file}`):\n```\n{ev_quote}\n```"
            )

    lines.extend(code_blocks)
    return "\n".join(lines)


def _extract_lines(content: str, line_range: str, context: int = 5) -> str:
    """从文件内容中按行范围提取代码，含上下文行。"""
    all_lines = content.splitlines()
    total = len(all_lines)
    try:
        if "-" in str(line_range):
            parts = str(line_range).split("-", 1)
            start = max(0, int(parts[0]) - 1 - context)
            end = min(total, int(parts[1]) + context)
        else:
            mid = max(0, int(line_range) - 1)
            start = max(0, mid - context)
            end = min(total, mid + context + 1)
    except (ValueError, IndexError):
        start, end = 0, min(60, total)

    selected = all_lines[start:end]
    # 加行号
    numbered = [f"{start + i + 1:4d} | {ln}" for i, ln in enumerate(selected)]
    return "\n".join(numbered)


class ReportWriterV3Router(Router):
    """V3 综合报告写出节点（LLM 单次调用）。

    报告分两大部分：
    - Part 1 精炼摘要：概览/架构/能力地图/发现速览/改进提案/局限声明
    - Part 2 详细展开：每个 finding 独立章节（理解/参考代码/学习点/学习方向）

    输入：absorption.learning（含 findings / module_readings 等完整状态）
    输出：absorption.report.v3（两段 Markdown + 结构化摘要 + 落盘路径）
    """

    DESCRIPTION = (
        "V3 综合报告：两段式（精炼摘要 + 每 finding 详细展开），"
        "传入实际代码上下文，LLM 直接引用代码，写盘 data/domains/absorption/<repo>/report.md"
    )
    FORMAT_IN = "absorption.learning"
    FORMAT_OUT = "absorption.report.v3"

    _MODEL = _MODEL

    def __init__(self, *, model: str | None = None, **kwargs: Any) -> None:
        self._model = model or self._MODEL

    def run(self, input_data: Any) -> Verdict:
        repo_name = input_data.get("repo_name", "unknown")
        findings: list[dict] = input_data.get("findings") or []
        overall: dict = input_data.get("overall_assessment") or {}
        module_readings: list[dict] = input_data.get("module_readings") or []
        files_read: list[str] = input_data.get("files_read") or []
        repo_local_path: str = input_data.get("repo_local_path", "")
        iteration: int = int(input_data.get("iteration", 1))
        feedback_incorporated: list[str] = list(input_data.get("feedback_incorporated") or [])

        if not findings:
            return Verdict(
                kind=VerdictKind.FAIL,
                output=dict(input_data),
                diagnosis="ReportWriterV3: findings 为空，无法生成报告",
            )

        # 为每个 finding 构建含实际代码的文本块
        # _build_finding_with_code 按优先级：module_readings 缓存 → 磁盘直读
        finding_blocks: list[str] = []
        for i, f in enumerate(findings, 1):
            block = _build_finding_with_code(f, module_readings, repo_local_path)
            finding_blocks.append(f"## Finding {i}\n\n{block}")
        findings_with_code = "\n\n---\n\n".join(finding_blocks)

        user_msg = f"""# 报告生成任务

**Repo**: {repo_name}
**已探索文件数**: {len(files_read)}
**发现数量**: {len(findings)} 个
**吸纳价值**: {overall.get("absorption_value", "?")}
**总结**: {overall.get("summary", "")}

## 已读文件列表
{chr(10).join(f"- {f}" for f in files_read)}

---

## 发现列表（含对应代码）

以下是每个发现的结构化信息和从源码中提取的实际代码片段。
写 Part 2 时请直接引用这些代码，不要凭记忆构造。

{findings_with_code}

---

请生成完整报告：Part1（精炼摘要）+ ---DETAIL--- + Part2（每发现详细展开）+ ---JSON--- + 结构化摘要。"""

        try:
            from omnicompany.runtime.llm.llm import LLMClient
            client = LLMClient(model=self._model)
            resp = client.call(
                messages=[{"role": "user", "content": user_msg}],
                system=_REPORT_SYSTEM,
                info_audit=False,  # 长结构化输出节点 opt-out piggyback; 由 post_hoc 兜底
            )
            raw = resp.content[0].text.strip()
        except Exception as e:
            return Verdict(
                kind=VerdictKind.FAIL,
                output=dict(input_data),
                diagnosis=f"ReportWriterV3 LLM 失败: {type(e).__name__}: {e}",
            )

        # 拆分三段：Part1 + Part2 + JSON
        report_md, detail_md, structured = _split_report_parts(raw)

        # C3 结构性修复：用 findings 里的真实路径替换 LLM 编造路径
        # LLM 幻觉率低，出现的编造 90%+ 是信息缺失导致的——
        # 这里不靠 prompt 修，直接从 findings 数据硬替换
        real_paths = set()
        for f in findings:
            for ev in f.get("evidence") or []:
                p = ev.get("file", "")
                if p:
                    real_paths.add(p)
        report_md = _fix_fabricated_paths(report_md, real_paths)
        detail_md = _fix_fabricated_paths(detail_md, real_paths)

        # 落盘报告（Part1 + Part2 合并为完整 report）
        full_md = report_md + "\n\n---DETAIL---\n\n" + detail_md if detail_md else report_md
        repo_dir = _absorption_repo_dir(repo_name)
        repo_dir.mkdir(parents=True, exist_ok=True)
        report_path = repo_dir / "report.md"

        if report_path.exists() and iteration > 1:
            existing = report_path.read_text(encoding="utf-8")
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
            appended = (
                f"\n\n---\n\n# 补充吸纳 Iteration {iteration}（{timestamp}）\n\n"
                + full_md
            )
            _guarded_write(report_path, existing + appended, writer="internal-engine",
                           domain="absorption", purpose="absorption report iteration append")
        else:
            header = (
                f"<!-- absorption-module-driven | repo={repo_name} | iteration={iteration} | "
                f"{datetime.now().strftime('%Y-%m-%d')} -->\n\n"
            )
            _guarded_write(report_path, header + full_md, writer="internal-engine",
                           domain="absorption", purpose="absorption report initial write")

        print(f"\n[ReportWriterV3] 报告已写入: {report_path}")
        print(f"[ReportWriterV3] 可写入反馈: {repo_dir / 'feedback.md'}\n")

        return Verdict(
            kind=VerdictKind.PASS,
            output={
                **input_data,
                "report_path": str(report_path),
                "report_md": full_md,
                "structured": structured,
                "iteration": iteration,
                "feedback_incorporated": feedback_incorporated,
            },
            confidence=0.9,
            diagnosis=(
                f"ReportWriterV3: 报告生成完成 "
                f"(摘要={len(report_md)}字节, 详细={len(detail_md)}字节), "
                f"路径={report_path}"
            ),
            granted_tags=["domain.absorption", "stage.v3.report"],
        )


def _fix_fabricated_paths(text: str, real_paths: set[str]) -> str:
    """用 findings 里的真实路径替换 LLM 可能编造的路径。

    策略：扫描 report 中所有 .py 路径引用，若不在 real_paths 里，
    尝试按文件名匹配最接近的真实路径替换。
    """
    if not real_paths or not text:
        return text

    # 建文件名→真实路径索引
    basename_map: dict[str, str] = {}
    for rp in real_paths:
        bn = rp.rsplit("/", 1)[-1] if "/" in rp else rp
        basename_map[bn] = rp

    def _replacer(match: re.Match) -> str:
        path = match.group(0)
        if path in real_paths:
            return path  # 已经是真实路径
        # 按文件名匹配
        bn = path.rsplit("/", 1)[-1] if "/" in path else path
        if bn in basename_map:
            return basename_map[bn]
        return path  # 无法匹配，保留原样

    # 匹配 report 中的 .py 路径（`path/to/file.py` 或 path/to/file.py）
    return re.sub(r'[\w./]+\.py', _replacer, text)


def _split_report_parts(raw: str) -> tuple[str, str, dict]:
    """从 LLM 原始输出拆分出 Part1 / Part2 / JSON。"""
    summary_md = raw
    detail_md = ""
    structured: dict = {}

    if "---JSON---" in raw:
        main, json_part = raw.rsplit("---JSON---", 1)
        try:
            json_part = json_part.strip()
            if json_part.startswith("```"):
                json_part = re.sub(r"^```[a-z]*\n?", "", json_part)
                json_part = re.sub(r"\n?```$", "", json_part.strip())
            structured = json.loads(json_part)
        except Exception:
            structured = {}
    else:
        main = raw

    if "---DETAIL---" in main:
        parts = main.split("---DETAIL---", 1)
        summary_md = parts[0].strip()
        detail_md = parts[1].strip()
    else:
        summary_md = main.strip()

    return summary_md, detail_md, structured


# ══════════════════════════════════════════════════════════════════════
# Router 2：HumanFeedbackGateV3Router（RULE：读 feedback.md）
# ══════════════════════════════════════════════════════════════════════

class HumanFeedbackGateV3Router(Router):
    """V3 人工反馈门（RULE）。

    检查 data/absorption/<repo>/feedback.md 是否存在：
    - 若存在：读取原文，解析方向列表，重命名为 feedback_<iteration>.md.done
    - 若不存在：auto-pass（视为本轮完成），directions=[]

    输入：absorption.report.v3
    输出：absorption.feedback
    """

    DESCRIPTION = (
        "V3 人工反馈门（RULE）：读 data/absorption/<repo>/feedback.md，"
        "解析补充方向，重命名 .done；无文件则 auto-pass"
    )
    FORMAT_IN = "absorption.report.v3"
    FORMAT_OUT = "absorption.feedback"

    def run(self, input_data: Any) -> Verdict:
        repo_name = input_data.get("repo_name", "unknown")
        iteration: int = int(input_data.get("iteration", 1))

        repo_dir = _absorption_repo_dir(repo_name)
        feedback_path = repo_dir / "feedback.md"

        if not feedback_path.exists():
            # auto-pass
            print("[HumanFeedbackGate] 未发现 feedback.md，本轮完成（auto-pass）")
            return Verdict(
                kind=VerdictKind.PASS,
                output={
                    **input_data,
                    "feedback_text": "",
                    "directions": [],
                    "iteration": iteration,
                    "reviewer": "auto-pass",
                    "has_feedback": False,
                },
                confidence=1.0,
                diagnosis=f"HumanFeedbackGate: no feedback.md, auto-pass (iteration={iteration})",
            )

        # 读取并解析
        feedback_text = feedback_path.read_text(encoding="utf-8").strip()
        print(f"[HumanFeedbackGate] 发现 feedback.md ({len(feedback_text)} 字节)")

        directions = _parse_directions(feedback_text)

        # 重命名为 .done，避免重复读（replace 在 Windows 上也能覆盖已有文件）
        done_path = repo_dir / f"feedback_{iteration}.md.done"
        feedback_path.replace(done_path)
        print(f"[HumanFeedbackGate] feedback.md → {done_path.name}")
        print(f"[HumanFeedbackGate] 解析到 {len(directions)} 个补充方向: {directions}")

        has_feedback = bool(directions or feedback_text)

        return Verdict(
            kind=VerdictKind.PASS,
            output={
                **input_data,
                "feedback_text": feedback_text,
                "directions": directions,
                "iteration": iteration,
                "reviewer": "human",
                "has_feedback": has_feedback,
            },
            confidence=1.0,
            diagnosis=(
                f"HumanFeedbackGate: {len(directions)} 方向 (iteration={iteration})"
                if has_feedback else
                "HumanFeedbackGate: 空反馈，本轮完成"
            ),
        )


def _parse_directions(feedback_text: str) -> list[str]:
    """从 feedback.md 原文解析补充学习方向列表。

    支持格式：
    - 列表项（- / * / 1.）
    - 或者整段文本视为一个方向
    """
    lines = feedback_text.splitlines()
    directions: list[str] = []
    for line in lines:
        line = line.strip()
        # 跳过标题和空行
        if not line or line.startswith("#"):
            continue
        # 提取列表项
        m = re.match(r"^[-*\d.]+\s+(.+)$", line)
        if m:
            directions.append(m.group(1).strip())
    # 若没有列表项，把整段文字视为一个方向
    if not directions and feedback_text.strip():
        directions = [feedback_text.strip()[:200]]
    return directions


# ══════════════════════════════════════════════════════════════════════
# Router 3：FeedbackRouterV3（RULE + 判断：EMIT 或 JUMP）
# ══════════════════════════════════════════════════════════════════════

class FeedbackRouterV3(Router):
    """V3 反馈路由节点（RULE）。

    判断逻辑：
    - has_feedback=False 或 directions=[] → VerdictKind.PASS → 管线 EMIT（最终报告）
    - has_feedback=True                  → VerdictKind.PARTIAL → 管线 JUMP 回 module_explorer

    PARTIAL 时，输出 absorption.supplement_request，携带：
    - 原 repomap 字段（coarse_view / detail_views / repo_local_path / self_portrait）
    - supplement_guidance（来自 directions）
    - previous_findings / previous_files_read（避免重复）
    - iteration+1

    输入：absorption.feedback
    输出：absorption.report.v3（EMIT）或 absorption.supplement_request（JUMP）
    """

    DESCRIPTION = (
        "V3 反馈路由（RULE）：无反馈 → EMIT 完成；有方向 → 构建 supplement_request，"
        "JUMP 回 module_explorer 开始补充学习迭代"
    )
    FORMAT_IN = "absorption.feedback"
    FORMAT_OUT = "absorption.supplement_request"

    def run(self, input_data: Any) -> Verdict:
        repo_name = input_data.get("repo_name", "unknown")
        has_feedback: bool = bool(input_data.get("has_feedback"))
        directions: list[str] = list(input_data.get("directions") or [])
        iteration: int = int(input_data.get("iteration", 1))

        if not has_feedback or not directions:
            # 无反馈 → EMIT
            print(f"[FeedbackRouterV3] 无补充方向，报告锁定 (iteration={iteration})")
            return Verdict(
                kind=VerdictKind.PASS,
                output=dict(input_data),
                confidence=1.0,
                diagnosis=f"FeedbackRouterV3: EMIT，最终报告 (iteration={iteration})",
            )

        # 有反馈 → 构建 supplement_request，JUMP 回 module_explorer
        supplement_guidance = _build_supplement_guidance(directions)
        previous_findings = list(input_data.get("findings") or [])
        previous_files_read = list(input_data.get("files_read") or [])
        next_iter = iteration + 1

        # 汇总已有发现的标题（给 LLM 上下文，避免重复）
        found_titles = [f.get("title", "") for f in previous_findings if f.get("title")]

        supplement_request: dict = {
            "repo_name": repo_name,
            "repo_local_path": input_data.get("repo_local_path", ""),
            "self_portrait": input_data.get("self_portrait", ""),
            "coarse_view": input_data.get("coarse_view", ""),
            "detail_views": input_data.get("detail_views") or {},
            "total_files": input_data.get("total_files", 0),
            "supplement_guidance": supplement_guidance,
            "previous_findings": previous_findings,
            "previous_files_read": previous_files_read,
            "found_titles": found_titles,
            "iteration": next_iter,
            # 保留完整上游状态供下游合并
            **{k: v for k, v in input_data.items() if k not in (
                "supplement_guidance", "previous_findings", "previous_files_read",
                "iteration", "found_titles",
            )},
        }
        supplement_request["iteration"] = next_iter

        print(
            f"[FeedbackRouterV3] {len(directions)} 个补充方向 → JUMP 至 supplement_explorer "
            f"(iteration {iteration} → {next_iter})"
        )
        for d in directions:
            print(f"  - {d}")

        return Verdict(
            kind=VerdictKind.PARTIAL,
            output=supplement_request,
            confidence=0.8,
            diagnosis=(
                f"FeedbackRouterV3: JUMP to module_explorer, "
                f"{len(directions)} 方向 (iteration={next_iter})"
            ),
        )


def _build_supplement_guidance(directions: list[str]) -> str:
    """把方向列表组合成 ModuleExplorer 能读的 supplement_guidance 字符串。"""
    lines = ["本次补充探索方向（请优先重点关注）："]
    for i, d in enumerate(directions, 1):
        lines.append(f"{i}. {d}")
    return "\n".join(lines)
