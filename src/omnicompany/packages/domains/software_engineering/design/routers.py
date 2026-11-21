# [OMNI] origin=claude-code domain=software_engineering/design ts=2026-04-08T03:23:41Z
# [OMNI] material_id="material:domains.software_engineering.design.pipeline_routers.implementation.py"
"""sw_design.routers — 设计审查管线的 Router 实现

7 个节点:
  1 HARD:      spec_parser (解析设计文档)
  2 HARD:      arch_scanner (os.walk), file_reader (读关键文件)
  1 SOFT/LLM:  context_judge (上下文充分性)
  1 HARD:      pattern_analyzer (分析现有架构模式)
  1 SOFT/LLM:  design_reviewer (LLM 审查)
  1 确定性:     report_formatter (终版报告)
"""

from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path
from typing import Any

from omnicompany.protocol.anchor import Verdict, VerdictKind
from omnicompany.runtime.routing.router import Router
from omnicompany.packages.domains.software_engineering._shared.common_formats import (
    truncate_file_content, MAX_TREE_BYTES,
)

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════════════
# design-context 数据结构
# ═══════════════════════════════════════════════════════════════════════════════

def _empty_context() -> dict:
    return {
        # sw.task-input
        "task_text": "",
        "project_dir": "",
        "task_type": "design",
        "scope": "feature",
        "related_files": [],
        # pipeline 上下文
        "snapshot": {},
        "file_batch": [],
        "context": {"iteration": 0, "sufficient": False},
        "patterns": {},
    }


# ═══════════════════════════════════════════════════════════════════════════════
# 1. SpecParser — 解析设计文档（HARD）
# ═══════════════════════════════════════════════════════════════════════════════

class SpecParserRouter(Router):
    FORMAT_IN = "sw_design.task"
    FORMAT_OUT = "sw_design.snapshot"
    DESCRIPTION = "解析设计文档，提取目标、范围、关键技术决策"

    def run(self, input_data: Any) -> Verdict:
        spec_text = (input_data.get("spec_text")
                     or input_data.get("task_text") or "").strip()
        spec_path = (input_data.get("spec_path")
                     or input_data.get("task_path") or "").strip()
        project_dir = (input_data.get("project_dir") or "").strip()

        ctx = _empty_context()
        ctx["project_dir"] = project_dir

        if spec_text:
            ctx["task_text"] = spec_text
        elif spec_path:
            p = Path(spec_path)
            if not p.exists():
                return Verdict(kind=VerdictKind.FAIL, diagnosis=f"文件不存在: {spec_path}")
            try:
                ctx["task_text"] = p.read_text(encoding="utf-8", errors="replace")
            except Exception as e:
                return Verdict(kind=VerdictKind.FAIL, diagnosis=f"读取失败: {e}")
        else:
            return Verdict(kind=VerdictKind.FAIL, diagnosis="需要 spec_text 或 spec_path")

        # 提取目标
        goals = []
        for line in ctx["task_text"].splitlines():
            line_s = line.strip().lower()
            if any(kw in line_s for kw in ["目标", "goal", "objective", "requirement", "需求"]):
                goals.append(line.strip())
        ctx["spec_goals"] = goals[:10]

        return Verdict(
            kind=VerdictKind.PASS, output=ctx,
            diagnosis=f"解析设计文档 ({len(ctx['task_text'])} chars, {len(goals)} 目标)",
        )


# ═══════════════════════════════════════════════════════════════════════════════
# 2. ArchScanner — 扫描项目架构（HARD）
# ═══════════════════════════════════════════════════════════════════════════════

class ArchScannerRouter(Router):
    FORMAT_IN = "sw_design.snapshot"
    FORMAT_OUT = "sw_design.context-state"
    DESCRIPTION = "扫描项目目录，识别架构分层和关键文件"

    _SKIP = {".git", "node_modules", "__pycache__", ".venv", "venv", ".mypy_cache",
             "dist", "build", ".tox", ".pytest_cache", ".egg-info", "_graveyard"}

    def run(self, input_data: Any) -> Verdict:
        ctx = input_data
        project_dir = ctx.get("project_dir", "")

        if not project_dir:
            return Verdict(kind=VerdictKind.PASS, output=ctx,
                           diagnosis="无项目目录，跳过扫描")

        base = Path(project_dir)
        if not base.exists():
            return Verdict(kind=VerdictKind.FAIL, diagnosis=f"目录不存在: {project_dir}")

        tree_lines = []
        key_files = []
        file_count = 0
        lang_counts: dict[str, int] = {}
        top_dirs: list[str] = []

        for root, dirs, files in os.walk(str(base)):
            depth = Path(root).relative_to(base).parts
            if len(depth) > 4:
                dirs.clear()
                continue

            dirs[:] = [d for d in sorted(dirs) if d not in self._SKIP and not d.startswith(".")]
            if len(depth) == 1:
                top_dirs.append(dirs[-1] if dirs else Path(root).name)

            indent = "  " * len(depth)
            tree_lines.append(f"{indent}{Path(root).name}/")

            for f in sorted(files)[:30]:
                fpath = Path(root) / f
                rel = str(fpath.relative_to(base)).replace("\\", "/")
                tree_lines.append(f"{indent}  {f}")
                file_count += 1

                # 语言统计
                ext = fpath.suffix.lower()
                if ext in (".py", ".js", ".ts", ".tsx", ".rs", ".go", ".java", ".c", ".cpp"):
                    lang_counts[ext] = lang_counts.get(ext, 0) + 1

                # 关键文件
                fl = f.lower()
                if fl in ("readme.md", "readme.rst", "pyproject.toml", "setup.py",
                          "package.json", "tsconfig.json", "cargo.toml", "go.mod",
                          "dockerfile", "docker-compose.yml", ".env.example"):
                    key_files.append(rel)
                elif fl in ("__init__.py", "main.py", "app.py", "index.ts", "index.js"):
                    key_files.append(rel)
                elif "config" in fl or fl.startswith("conftest"):
                    key_files.append(rel)

            if file_count > 500:
                tree_lines.append("... (truncated)")
                break

        primary_lang = max(lang_counts, key=lang_counts.get) if lang_counts else "unknown"
        ctx["snapshot"] = {
            "tree": "\n".join(tree_lines[:200])[:MAX_TREE_BYTES],
            "primary_language": primary_lang,
            "lang_distribution": dict(sorted(lang_counts.items(), key=lambda x: -x[1])[:5]),
            "file_count": file_count,
            "key_files": key_files[:25],
            "top_level_dirs": top_dirs[:10],
        }

        return Verdict(
            kind=VerdictKind.PASS, output=ctx,
            diagnosis=f"扫描 {file_count} 文件, 主语言 {primary_lang}, {len(key_files)} 关键文件",
        )


# ═══════════════════════════════════════════════════════════════════════════════
# 3. FileReader — 读取关键文件（HARD）
# ═══════════════════════════════════════════════════════════════════════════════

class FileReaderRouter(Router):
    FORMAT_IN = "sw_design.context-state"
    FORMAT_OUT = "sw_design.context-state"
    DESCRIPTION = "读取关键文件内容，提取接口签名和依赖信息"

    def run(self, input_data: Any) -> Verdict:
        ctx = input_data
        project_dir = ctx.get("project_dir", "")
        base = Path(project_dir) if project_dir else None
        file_batch = ctx.get("file_batch", [])
        read_paths = {f["path"] for f in file_batch}

        for fpath in ctx.get("snapshot", {}).get("key_files", []):
            if fpath in read_paths:
                continue

            full = (base / fpath) if base else Path(fpath)
            if not full.exists() or not full.is_file():
                continue

            try:
                raw = full.read_text(encoding="utf-8", errors="replace")
                content, truncated = truncate_file_content(raw)

                imports = [l.strip() for l in raw.splitlines()[:50]
                          if l.strip().startswith(("import ", "from ", "require(", "#include", "use "))]
                sigs = re.findall(
                    r'^(?:def |class |function |async function |export |pub fn |fn |struct |interface ).*',
                    raw, re.MULTILINE)

                file_batch.append({
                    "path": fpath,
                    "language": full.suffix.lstrip("."),
                    "size_bytes": len(raw),
                    "content": content,
                    "imports": imports[:20],
                    "signatures": sigs[:25],
                    "truncated": truncated,
                })
            except Exception:
                pass

        ctx["file_batch"] = file_batch
        return Verdict(
            kind=VerdictKind.PASS, output=ctx,
            diagnosis=f"读取 {len(file_batch)} 文件",
        )


# ═══════════════════════════════════════════════════════════════════════════════
# 4. ContextJudge — 判断上下文充分性（SOFT）
# ═══════════════════════════════════════════════════════════════════════════════

class ContextJudgeRouter(Router):
    FORMAT_IN = "sw_design.context-state"
    FORMAT_OUT = "sw_design.context-state"
    DESCRIPTION = "判断是否已收集足够信息进行设计审查"

    def run(self, input_data: Any) -> Verdict:
        ctx = input_data
        context = ctx.get("context", {})
        iteration = context.get("iteration", 0)
        file_batch = ctx.get("file_batch", [])
        spec = ctx.get("task_text", "")
        project_dir = ctx.get("project_dir", "")

        if not project_dir:
            context["sufficient"] = True
            ctx["context"] = context
            return Verdict(kind=VerdictKind.PASS, output=ctx,
                           diagnosis="无项目目录，直接进入审查")

        issues = []
        if not file_batch:
            issues.append("未读取任何文件")
        if not ctx.get("snapshot", {}).get("tree"):
            issues.append("未扫描目录结构")

        # 检查 spec 中提到的文件/模块是否已读取
        mentioned = re.findall(r'[\w/\\]+\.\w{1,5}', spec)
        read_paths = {f["path"] for f in file_batch}
        for mf in mentioned[:10]:
            basename = Path(mf).name
            if not any(basename in rp for rp in read_paths):
                if iteration < 2:
                    key_files = ctx.get("snapshot", {}).get("key_files", [])
                    if mf not in key_files:
                        ctx.setdefault("snapshot", {}).setdefault("key_files", []).append(mf)
                    issues.append(f"设计文档提到 {basename} 但未读取")

        if issues and iteration < 2:
            context["iteration"] = iteration + 1
            ctx["context"] = context
            return Verdict(
                kind=VerdictKind.PARTIAL, output=ctx,
                diagnosis=f"上下文不充分 ({len(issues)} 问题), 继续探索",
            )

        context["sufficient"] = True
        ctx["context"] = context
        return Verdict(
            kind=VerdictKind.PASS, output=ctx,
            diagnosis=f"上下文充分: {len(file_batch)} 文件",
        )


# ═══════════════════════════════════════════════════════════════════════════════
# 5. PatternAnalyzer — 分析架构模式（HARD）
# ═══════════════════════════════════════════════════════════════════════════════

class PatternAnalyzerRouter(Router):
    FORMAT_IN = "sw_design.context-state"
    FORMAT_OUT = "sw_design.patterns"
    DESCRIPTION = "分析现有代码的架构模式、命名规范、分层结构"

    def run(self, input_data: Any) -> Verdict:
        ctx = input_data
        patterns: dict[str, Any] = {}
        file_batch = ctx.get("file_batch", [])

        # 测试框架检测
        test_files = [f for f in file_batch if "test" in f["path"].lower() or "conftest" in f["path"].lower()]
        if test_files:
            sample = test_files[0].get("content", "")
            if "pytest" in sample or "def test_" in sample:
                patterns["test_framework"] = "pytest"
            elif "unittest" in sample:
                patterns["test_framework"] = "unittest"
            elif "describe(" in sample or "it(" in sample:
                patterns["test_framework"] = "jest/mocha"

        # 包管理
        for f in file_batch:
            fl = Path(f["path"]).name.lower()
            if fl == "pyproject.toml":
                patterns["package_manager"] = "poetry/pip"
            elif fl == "package.json":
                patterns["package_manager"] = "npm/yarn"
            elif fl == "cargo.toml":
                patterns["package_manager"] = "cargo"
            elif fl == "go.mod":
                patterns["package_manager"] = "go modules"

        # 分层结构识别
        top_dirs = ctx.get("snapshot", {}).get("top_level_dirs", [])
        layers = []
        for d in top_dirs:
            dl = d.lower()
            if dl in ("src", "lib", "core"):
                layers.append("source")
            elif dl in ("tests", "test", "spec"):
                layers.append("tests")
            elif dl in ("docs", "doc"):
                layers.append("documentation")
            elif dl in ("scripts", "bin", "tools"):
                layers.append("tooling")
            elif dl in ("config", "conf", "settings"):
                layers.append("configuration")
        patterns["layers"] = layers

        # 命名规范 (从签名提取)
        all_sigs = []
        for fc in file_batch:
            all_sigs.extend(fc.get("signatures", []))
        if all_sigs:
            snake_count = sum(1 for s in all_sigs if "_" in s)
            camel_count = sum(1 for s in all_sigs if re.search(r'[a-z][A-Z]', s))
            patterns["naming_convention"] = "snake_case" if snake_count > camel_count else "camelCase"

        # 依赖注入方式
        all_imports = []
        for fc in file_batch:
            all_imports.extend(fc.get("imports", []))
        if any("inject" in i.lower() or "dependency" in i.lower() for i in all_imports):
            patterns["di_style"] = "framework_di"
        elif any("__init__" in i for i in all_imports):
            patterns["di_style"] = "constructor_injection"
        else:
            patterns["di_style"] = "direct_import"

        # 错误处理
        all_content = " ".join(fc.get("content", "")[:1000] for fc in file_batch)
        if "Result<" in all_content or "Result[" in all_content:
            patterns["error_handling"] = "result_type"
        elif "try:" in all_content or "try {" in all_content:
            patterns["error_handling"] = "try_catch"
        elif "if err" in all_content:
            patterns["error_handling"] = "error_return"

        ctx["patterns"] = patterns

        return Verdict(
            kind=VerdictKind.PASS, output=ctx,
            diagnosis=f"识别 {len(patterns)} 种模式: {list(patterns.keys())}",
        )


# ═══════════════════════════════════════════════════════════════════════════════
# 6. DesignReviewer — LLM 审查设计方案（SOFT）
# ═══════════════════════════════════════════════════════════════════════════════

_REVIEW_SYSTEM = """\
你是一名高级架构师，负责审查设计方案与现有代码库的一致性。

**审查维度：**
1. **一致性** — 设计是否遵循现有架构模式（命名、分层、测试风格）？
2. **可行性** — 设计在当前技术栈上是否可实现？有无技术障碍？
3. **风险** — 有哪些潜在风险（性能、安全、维护成本）？
4. **完整性** — 设计是否覆盖了所有必要方面（错误处理、测试、文档）？
5. **简洁性** — 是否存在过度设计？能否简化？

**输出 JSON 格式（必须精确）：**
```json
{
  "findings": [
    {
      "severity": "Critical|Important|Minor",
      "title": "简短标题",
      "detail": "详细说明",
      "suggestion": "改进建议"
    }
  ],
  "conclusion": "APPROVE|NEEDS_REVISION|REJECT",
  "summary": "一段话总结"
}
```
"""


class DesignReviewerRouter(Router):
    FORMAT_IN = "sw_design.patterns"
    FORMAT_OUT = "sw_design.review"
    DESCRIPTION = "LLM 审查设计方案，评估一致性、可行性、风险"
    REFLECTION_ENABLED = True

    def __init__(self, *, model: str | None = None):
        self._model = model

    def _make_client(self):
        from omnicompany.runtime.llm.llm import LLMClient
        return LLMClient(role="runtime_main", max_tokens=4096,
                         **({"model": self._model} if self._model else {}))

    def run(self, input_data: Any) -> Verdict:
        ctx = input_data

        # 准备审查上下文
        existing_code = ""
        for fc in list(ctx.get("file_batch", []))[:5]:
            sigs = fc.get("signatures", [])
            sig_text = "\n".join(f"  {s}" for s in sigs[:10])
            existing_code += f"\n### {fc['path']}\nSignatures:\n{sig_text}\n"

        prompt = f"""审查以下设计方案:

## 设计文档
{ctx['task_text'][:10000]}

## 项目架构信息
- 主语言: {ctx.get('snapshot', {}).get('primary_language', 'unknown')}
- 文件数: {ctx.get('snapshot', {}).get('file_count', 0)}
- 目录结构:
```
{ctx.get('snapshot', {}).get('tree', '无')[:2000]}
```

## 现有架构模式
{json.dumps(ctx.get('patterns', {}), indent=2, ensure_ascii=False)}

## 关键接口签名
{existing_code[:4000]}

请按审查维度分析并输出 JSON。"""

        print("[*] Calling LLM for design review...")
        try:
            client = self._make_client()
            resp = client.call(
                messages=[{"role": "user", "content": prompt}],
                system=_REVIEW_SYSTEM,
            )
            text = resp.content[0].text

            # 解析 JSON
            match = re.search(r'```json\n(.*?)```', text, re.DOTALL)
            if match:
                result = json.loads(match.group(1))
            else:
                # 尝试直接解析
                result = json.loads(text)

            ctx["review_findings"] = result.get("findings", [])
            ctx["conclusion"] = result.get("conclusion", "NEEDS_REVISION")
            ctx["review_summary"] = result.get("summary", "")

            return Verdict(
                kind=VerdictKind.PASS, output=ctx,
                diagnosis=f"审查完成: {ctx['conclusion']}, {len(ctx['review_findings'])} findings",
            )
        except Exception as e:
            return Verdict(kind=VerdictKind.FAIL, output=ctx,
                           diagnosis=f"审查失败: {e}")


# ═══════════════════════════════════════════════════════════════════════════════
# 7. ReportFormatter — 格式化报告（确定性）
# ═══════════════════════════════════════════════════════════════════════════════

class ReportFormatterRouter(Router):
    FORMAT_IN = "sw_design.review"
    FORMAT_OUT = "sw_design.report"
    DESCRIPTION = "格式化设计审查报告"

    _SEVERITY_ICONS = {"Critical": "🔴", "Important": "🟡", "Minor": "🔵"}

    def run(self, input_data: Any) -> Verdict:
        ctx = input_data
        findings = ctx.get("review_findings", [])
        conclusion = ctx.get("conclusion", "UNKNOWN")

        # 按 severity 分组
        by_severity: dict[str, list] = {"Critical": [], "Important": [], "Minor": []}
        for f in findings:
            sev = f.get("severity", "Minor")
            by_severity.setdefault(sev, []).append(f)

        critical_count = len(by_severity.get("Critical", []))
        important_count = len(by_severity.get("Important", []))
        minor_count = len(by_severity.get("Minor", []))

        # 构建报告
        lines = [
            "═" * 55,
            "📐 DESIGN REVIEW REPORT",
            "═" * 55,
            "",
            f"结论: {conclusion}",
            f"Findings: {critical_count}🔴 {important_count}🟡 {minor_count}🔵",
            "",
        ]

        if ctx.get("review_summary"):
            lines.append("── 摘要 ──")
            lines.append(ctx["review_summary"])
            lines.append("")

        for sev in ["Critical", "Important", "Minor"]:
            items = by_severity.get(sev, [])
            if not items:
                continue
            icon = self._SEVERITY_ICONS.get(sev, "")
            lines.append(f"── {icon} {sev} ──")
            for f in items:
                lines.append(f"  • {f.get('title', 'Untitled')}")
                if f.get("detail"):
                    lines.append(f"    {f['detail']}")
                if f.get("suggestion"):
                    lines.append(f"    → {f['suggestion']}")
            lines.append("")

        lines.append("── 结论 ──")
        conclusion_icons = {"APPROVE": "✅", "NEEDS_REVISION": "💬", "REJECT": "❌"}
        lines.append(f"{conclusion_icons.get(conclusion, '❓')} {conclusion}")
        lines.append("═" * 55)

        report = "\n".join(lines)
        print(f"\n{report}\n")

        return Verdict(
            kind=VerdictKind.PASS,
            output={
                "report_text": report,
                "report": report,       # 兼容
                "conclusion": conclusion,
                "metrics": {
                    "critical_count": critical_count,
                    "important_count": important_count,
                    "minor_count": minor_count,
                },
                "findings": findings,
            },
            diagnosis=f"报告生成: {conclusion} ({critical_count}🔴 {important_count}🟡 {minor_count}🔵)",
        )
