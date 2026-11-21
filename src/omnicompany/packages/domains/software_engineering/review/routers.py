# [OMNI] origin=claude-code domain=software_engineering/review ts=2026-04-08T03:23:42Z
# [OMNI] material_id="material:domains.software_engineering.review.pipeline_routers.implementation.py"
"""sw_review.routers — 代码审查管线的 Router 实现

7 个节点:
  1 HARD:      diff_collector (git/text 收集)
  2 HARD:      context_gatherer (读文件 imports/callers)
               test_searcher (搜索测试文件)
  1 SOFT/LLM:  sufficiency_judge (判断上下文是否充分)
  1 SOFT/LLM:  deep_reviewer (多维度审查)
  1 SOFT/LLM:  finding_validator (交叉验证)
  1 确定性:     report_formatter (汇总报告)
"""

from __future__ import annotations

import json
import logging
import re
import subprocess
from pathlib import Path
from typing import Any

from omnicompany.protocol.anchor import Verdict, VerdictKind
from omnicompany.runtime.routing.router import Router

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════════════
# review-context 数据结构
# ═══════════════════════════════════════════════════════════════════════════════

def _empty_context() -> dict:
    return {
        "diff": "",
        "description": "",
        "source": "",
        "work_dir": "",
        "changed_files": [],          # [{"path": ..., "added": N, "removed": N}]
        "file_contexts": {},          # {path: {"content": ..., "imports": ..., "callers": ...}}
        "test_files": {},             # {test_path: content_summary}
        "coverage_gaps": [],          # ["file X has no tests"]
        "explored_dirs": [],          # 已探索的目录
        "iteration": 0,
        "sufficient": False,
    }


# ═══════════════════════════════════════════════════════════════════════════════
# 1. DiffCollector — 收集 diff（HARD）
# ═══════════════════════════════════════════════════════════════════════════════

class DiffCollectorRouter(Router):
    FORMAT_IN = "sw_review.diff"
    FORMAT_OUT = "sw_review.review-context"
    DESCRIPTION = "收集 git diff 或直接 diff 文本，解析变更文件列表"

    def run(self, input_data: Any) -> Verdict:
        base_sha = (input_data.get("base_sha") or "").strip()
        head_sha = (input_data.get("head_sha") or "").strip()
        diff_text = (input_data.get("diff_text") or "").strip()
        work_dir = (input_data.get("work_dir") or "").strip()
        description = (input_data.get("description") or "").strip()

        ctx = _empty_context()
        ctx["work_dir"] = work_dir
        ctx["description"] = description

        if diff_text:
            ctx["diff"] = diff_text
            ctx["source"] = "provided"
        elif base_sha and head_sha:
            cwd = Path(work_dir) if work_dir else Path.cwd()
            try:
                r = subprocess.run(
                    ["git", "diff", f"{base_sha}..{head_sha}"],
                    capture_output=True, text=True, timeout=30, cwd=str(cwd),
                )
                if r.returncode != 0:
                    return Verdict(kind=VerdictKind.FAIL,
                                   diagnosis=f"git diff 失败: {r.stderr.strip()}")
                ctx["diff"] = r.stdout
                ctx["source"] = f"git:{base_sha[:8]}..{head_sha[:8]}"
            except Exception as e:
                return Verdict(kind=VerdictKind.FAIL, diagnosis=f"git 错误: {e}")
        else:
            return Verdict(kind=VerdictKind.FAIL,
                           diagnosis="需要 base_sha+head_sha 或 diff_text")

        if not ctx["diff"].strip():
            return Verdict(kind=VerdictKind.FAIL, diagnosis="diff 为空")

        # 截断
        if len(ctx["diff"]) > 60000:
            ctx["diff"] = ctx["diff"][:60000] + "\n... (truncated)"

        # 解析变更文件列表
        changed = []
        for match in re.finditer(r'^diff --git a/(.*?) b/', ctx["diff"], re.MULTILINE):
            fpath = match.group(1)
            if fpath not in [c["path"] for c in changed]:
                added = len(re.findall(r'^\+(?!\+\+)', ctx["diff"][match.start():], re.MULTILINE))
                removed = len(re.findall(r'^-(?!--)', ctx["diff"][match.start():], re.MULTILINE))
                changed.append({"path": fpath, "added": added, "removed": removed})
        ctx["changed_files"] = changed

        return Verdict(
            kind=VerdictKind.PASS, output=ctx,
            diagnosis=f"收集 diff: {len(changed)} 文件变更, {len(ctx['diff'])} chars",
        )


# ═══════════════════════════════════════════════════════════════════════════════
# 2. ContextGatherer — 读取修改文件的上下文（HARD）
# ═══════════════════════════════════════════════════════════════════════════════

class ContextGathererRouter(Router):
    FORMAT_IN = "sw_review.review-context"
    FORMAT_OUT = "sw_review.context"
    DESCRIPTION = "读取每个修改文件的 imports、函数签名、调用关系"

    def run(self, input_data: Any) -> Verdict:
        ctx = input_data
        work_dir = ctx.get("work_dir", "")
        base = Path(work_dir) if work_dir else Path.cwd()

        for cf in ctx.get("changed_files", []):
            fpath = cf["path"]
            if fpath in ctx["file_contexts"]:
                continue  # 已读过

            full_path = base / fpath
            if not full_path.exists():
                ctx["file_contexts"][fpath] = {"error": "文件不存在", "content": ""}
                continue

            try:
                content = full_path.read_text(encoding="utf-8", errors="replace")
                # 截断单文件
                if len(content) > 8000:
                    content = content[:8000] + "\n... (truncated)"

                # 提取 imports
                imports = []
                for line in content.splitlines()[:50]:
                    line = line.strip()
                    if line.startswith(("import ", "from ")) or \
                       line.startswith(("const ", "let ", "var ")) and "require(" in line or \
                       line.startswith("#include"):
                        imports.append(line)

                # 提取函数/类签名
                signatures = re.findall(
                    r'^(?:def |class |function |async function |export (?:default )?(?:function |class )).*',
                    content, re.MULTILINE,
                )

                ctx["file_contexts"][fpath] = {
                    "content": content,
                    "imports": imports[:20],
                    "signatures": signatures[:30],
                    "line_count": content.count("\n"),
                }
            except Exception as e:
                ctx["file_contexts"][fpath] = {"error": str(e), "content": ""}

        return Verdict(
            kind=VerdictKind.PASS, output=ctx,
            diagnosis=f"收集了 {len(ctx['file_contexts'])} 个文件的上下文",
        )


# ═══════════════════════════════════════════════════════════════════════════════
# 3. TestSearcher — 搜索对应测试（HARD）
# ═══════════════════════════════════════════════════════════════════════════════

class TestSearcherRouter(Router):
    FORMAT_IN = "sw_review.context"
    FORMAT_OUT = "sw_review.test-coverage"
    DESCRIPTION = "搜索变更文件的对应测试文件"

    _TEST_PATTERNS = [
        "test_{name}", "test_{name}.py", "{name}_test.py", "{name}.test.ts",
        "{name}.test.js", "{name}.spec.ts", "{name}.spec.js",
    ]

    def run(self, input_data: Any) -> Verdict:
        ctx = input_data
        work_dir = ctx.get("work_dir", "")
        base = Path(work_dir) if work_dir else Path.cwd()
        gaps = []

        for cf in ctx.get("changed_files", []):
            fpath = cf["path"]
            stem = Path(fpath).stem
            ext = Path(fpath).suffix

            # 跳过测试文件本身
            if "test" in stem.lower() or "spec" in stem.lower():
                continue

            found = False
            # 策略 1: 同目录下的 test_ 文件
            parent = (base / fpath).parent
            for pattern in ["test_*", "*_test*", "*.test.*", "*.spec.*"]:
                for test_file in parent.glob(pattern):
                    if stem.lower() in test_file.stem.lower():
                        try:
                            content = test_file.read_text(encoding="utf-8", errors="replace")
                            ctx["test_files"][str(test_file.relative_to(base))] = content[:3000]
                            found = True
                        except Exception:
                            pass

            # 策略 2: tests/ 目录
            for tests_dir in [base / "tests", base / "test", base / "__tests__"]:
                if tests_dir.exists():
                    for test_file in tests_dir.rglob(f"*{stem}*"):
                        if test_file.is_file():
                            try:
                                content = test_file.read_text(encoding="utf-8", errors="replace")
                                ctx["test_files"][str(test_file.relative_to(base))] = content[:3000]
                                found = True
                            except Exception:
                                pass

            if not found:
                gaps.append(f"{fpath} 没有找到对应测试")

        ctx["coverage_gaps"] = gaps
        return Verdict(
            kind=VerdictKind.PASS, output=ctx,
            diagnosis=f"找到 {len(ctx['test_files'])} 个测试, {len(gaps)} 个覆盖缺口",
        )


# ═══════════════════════════════════════════════════════════════════════════════
# 4. SufficiencyJudge — 判断信息是否充分（SOFT）
# ═══════════════════════════════════════════════════════════════════════════════

class SufficiencyJudgeRouter(Router):
    FORMAT_IN = "sw_review.test-coverage"
    FORMAT_OUT = "sw_review.review-context"
    DESCRIPTION = "判断收集的上下文是否足以进行深度审查"

    def run(self, input_data: Any) -> Verdict:
        ctx = input_data
        changed_files = ctx.get("changed_files", [])
        file_contexts = ctx.get("file_contexts", {})
        iteration = ctx.get("iteration", 0)

        # 充分性检查
        missing_context = []
        for cf in changed_files:
            fpath = cf["path"]
            fc = file_contexts.get(fpath, {})
            if fc.get("error") and not fc.get("content"):
                missing_context.append(fpath)

        # 允许最多 2 轮补充收集
        if missing_context and iteration < 2:
            ctx["iteration"] = iteration + 1
            return Verdict(
                kind=VerdictKind.PARTIAL, output=ctx,
                diagnosis=f"上下文不充分: {len(missing_context)} 个文件缺少内容",
            )

        ctx["sufficient"] = True
        return Verdict(
            kind=VerdictKind.PASS, output=ctx,
            diagnosis=f"上下文充分: {len(file_contexts)} 文件, {len(ctx.get('test_files', {}))} 测试",
        )


# ═══════════════════════════════════════════════════════════════════════════════
# 5. DeepReviewer — LLM 多维度审查（SOFT）
# ═══════════════════════════════════════════════════════════════════════════════

_REVIEW_SYSTEM = """\
你是一名资深代码审查员。请基于以下已收集的上下文对 diff 进行审查。

审查维度:
1. 正确性 — 逻辑是否正确？边界条件？
2. 架构 — 是否符合项目模式？不必要的耦合？
3. 测试 — 变更是否有对应测试？测试覆盖关键路径？
4. 安全 — 注入、泄露、权限绕过？
5. 可维护性 — 命名清晰？TODO/FIXME？

严重程度:
- 🔴 Critical — 必须修（逻辑错、安全漏洞）
- 🟡 Important — 应修（测试缺失、架构问题）
- 🔵 Minor — 建议改进（命名、注释）

输出 JSON:
```json
{
  "findings": [
    {"severity": "critical/important/minor", "file": "path", "line": 0, "description": "问题描述", "suggestion": "修复建议"}
  ],
  "summary": "1-2 句总结",
  "verdict": "APPROVE/REQUEST_CHANGES/NEEDS_DISCUSSION"
}
```"""


class DeepReviewerRouter(Router):
    FORMAT_IN = "sw_review.review-context"
    FORMAT_OUT = "sw_review.findings"
    DESCRIPTION = "LLM 多维度代码审查"
    REFLECTION_ENABLED = True

    def __init__(self, *, model: str | None = None):
        self._model = model

    def _make_client(self):
        from omnicompany.runtime.llm.llm import LLMClient
        return LLMClient(role="runtime_main", max_tokens=4096,
                         **({"model": self._model} if self._model else {}))

    def run(self, input_data: Any) -> Verdict:
        ctx = input_data

        # 构建审查 prompt：包含 diff + 上下文 + 测试覆盖
        context_parts = []
        for fpath, fc in ctx.get("file_contexts", {}).items():
            if fc.get("content"):
                context_parts.append(
                    f"## {fpath}\nImports: {fc.get('imports', [])}\n"
                    f"Signatures: {fc.get('signatures', [])[:10]}\n"
                    f"```\n{fc['content'][:3000]}\n```"
                )

        test_parts = []
        for tpath, content in ctx.get("test_files", {}).items():
            test_parts.append(f"## {tpath}\n```\n{content[:2000]}\n```")

        prompt = f"""请审查以下代码变更:

描述: {ctx.get('description', 'N/A')}

## Diff
```diff
{ctx.get('diff', '')[:20000]}
```

## 文件上下文
{chr(10).join(context_parts[:10]) if context_parts else '无额外上下文'}

## 测试文件
{chr(10).join(test_parts[:5]) if test_parts else '无测试文件'}

## 覆盖缺口
{chr(10).join(ctx.get('coverage_gaps', [])) if ctx.get('coverage_gaps') else '无缺口'}
"""

        print("[*] Calling LLM for deep review...")
        try:
            client = self._make_client()
            resp = client.call(messages=[{"role": "user", "content": prompt}],
                              system=_REVIEW_SYSTEM)
            text = resp.content[0].text
            match = re.search(r'```json\n(.*?)```', text, re.DOTALL)
            if match:
                review = json.loads(match.group(1))
            else:
                review = {"findings": [], "summary": text[:500], "verdict": "NEEDS_DISCUSSION"}

            ctx["review_result"] = review
            return Verdict(
                kind=VerdictKind.PASS, output=ctx,
                diagnosis=f"审查完成: {len(review.get('findings', []))} 个发现, "
                          f"verdict={review.get('verdict')}",
            )
        except Exception as e:
            return Verdict(kind=VerdictKind.FAIL, output=ctx,
                           diagnosis=f"LLM 审查失败: {e}")


# ═══════════════════════════════════════════════════════════════════════════════
# 6. FindingValidator — 交叉验证（HARD + 规则）
# ═══════════════════════════════════════════════════════════════════════════════

class FindingValidatorRouter(Router):
    FORMAT_IN = "sw_review.findings"
    FORMAT_OUT = "sw_review.validated-findings"
    DESCRIPTION = "交叉验证: 检查 Critical/Important 发现是否有代码证据"

    def run(self, input_data: Any) -> Verdict:
        ctx = input_data
        review = ctx.get("review_result", {})
        findings = review.get("findings", [])
        file_contexts = ctx.get("file_contexts", {})

        validated = []
        for f in findings:
            f_path = f.get("file", "")
            f_line = f.get("line", 0)
            has_evidence = False

            # 检查该文件是否在 diff 中
            for cf in ctx.get("changed_files", []):
                if cf["path"] == f_path or f_path in cf["path"]:
                    has_evidence = True
                    break

            # 对 Critical 发现额外验证：文件内容中是否存在相关代码
            if f.get("severity") == "critical" and f_path in file_contexts:
                content = file_contexts[f_path].get("content", "")
                # 简单的关键词匹配（非详尽）
                desc_keywords = re.findall(r'[a-zA-Z_]+', f.get("description", ""))
                keyword_match = any(kw in content for kw in desc_keywords[:5] if len(kw) > 3)
                has_evidence = has_evidence and keyword_match

            validated.append({**f, "has_evidence": has_evidence})

        # 过滤无证据的 Critical（可能是误报）
        filtered = [v for v in validated
                    if v.get("severity") != "critical" or v.get("has_evidence")]

        ctx["validated_findings"] = filtered
        ctx["filtered_count"] = len(validated) - len(filtered)

        return Verdict(
            kind=VerdictKind.PASS, output=ctx,
            diagnosis=f"验证 {len(validated)} 发现, 过滤 {len(validated) - len(filtered)} 个无证据误报",
        )


# ═══════════════════════════════════════════════════════════════════════════════
# 7. ReportFormatter — 格式化报告（确定性）
# ═══════════════════════════════════════════════════════════════════════════════

class ReportFormatterRouter(Router):
    FORMAT_IN = "sw_review.validated-findings"
    FORMAT_OUT = "sw_review.report"
    DESCRIPTION = "汇总输出审查报告"

    def run(self, input_data: Any) -> Verdict:
        ctx = input_data
        review = ctx.get("review_result", {})
        findings = ctx.get("validated_findings", [])
        description = ctx.get("description", "N/A")

        critical = [f for f in findings if f.get("severity") == "critical"]
        important = [f for f in findings if f.get("severity") == "important"]
        minor = [f for f in findings if f.get("severity") == "minor"]

        # 确定结论
        if critical:
            conclusion = "REQUEST_CHANGES"
        elif important:
            conclusion = "NEEDS_DISCUSSION"
        else:
            conclusion = review.get("verdict", "APPROVE")

        lines = [
            f"{'═' * 55}",
            "📝 CODE REVIEW REPORT",
            f"{'═' * 55}",
            "",
            f"变更: {description}",
            f"文件: {len(ctx.get('changed_files', []))}",
            f"测试: {len(ctx.get('test_files', {}))} 找到, {len(ctx.get('coverage_gaps', []))} 缺口",
            "",
            "── 摘要 ──",
            review.get("summary", "N/A"),
            "",
        ]

        if critical:
            lines.append("── 🔴 Critical ──")
            for f in critical:
                lines.append(f"  • {f['description']} ({f.get('file', '?')}:{f.get('line', '?')})")
            lines.append("")

        if important:
            lines.append("── 🟡 Important ──")
            for f in important:
                lines.append(f"  • {f['description']} ({f.get('file', '?')}:{f.get('line', '?')})")
            lines.append("")

        if minor:
            lines.append("── 🔵 Minor ──")
            for f in minor:
                lines.append(f"  • {f['description']} ({f.get('file', '?')}:{f.get('line', '?')})")
            lines.append("")

        filtered = ctx.get("filtered_count", 0)
        if filtered:
            lines.append(f"(已过滤 {filtered} 个无证据误报)")
            lines.append("")

        lines.extend([
            "── 结论 ──",
            f"{'✅' if conclusion == 'APPROVE' else '❌' if conclusion == 'REQUEST_CHANGES' else '💬'} {conclusion}",
            f"{'═' * 55}",
        ])

        report = "\n".join(lines)
        print(f"\n\n{report}\n\n")

        return Verdict(
            kind=VerdictKind.PASS if conclusion == "APPROVE" else VerdictKind.FAIL,
            output={
                "report": report,
                "conclusion": conclusion,
                "critical_count": len(critical),
                "important_count": len(important),
                "minor_count": len(minor),
                "filtered_count": filtered,
            },
            diagnosis=f"{conclusion}: {len(critical)}🔴 {len(important)}🟡 {len(minor)}🔵",
        )
