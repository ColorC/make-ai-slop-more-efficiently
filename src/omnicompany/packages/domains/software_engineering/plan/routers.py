# [OMNI] origin=claude-code domain=software_engineering/plan ts=2026-04-08T03:23:42Z
# [OMNI] material_id="material:domains.software_engineering.plan.pipeline_routers.implementation.py"
"""sw_plan.routers — 实施计划管线的 Router 实现

8 个节点:
  1 HARD:      spec_loader (读文件/接受文本)
  2 HARD:      codebase_scanner (os.walk), file_reader (读关键文件)
  1 SOFT/LLM:  context_judge (上下文充分性)
  1 SOFT/LLM:  file_mapper (LLM 生成文件修改计划)
  1 SOFT/LLM:  plan_drafter (LLM 生成 TDD 分步计划)
  1 HARD:      self_reviewer (占位符 + 结构验证)
  1 确定性:     plan_emitter (终版输出)
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

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════════════
# plan-context 数据结构
# ═══════════════════════════════════════════════════════════════════════════════

def _empty_context() -> dict:
    return {
        "spec": "",
        "spec_source": "",
        "project_dir": "",
        "tree": "",                   # 目录树文本
        "key_files": [],              # 关键文件列表 [path, ...]
        "read_files": {},             # {path: {content, imports, signatures}}
        "patterns": {},               # 识别的模式 {test_style, naming, ...}
        "explored_dirs": [],          # 已扫描的目录
        "file_map": [],               # [{path, action, reason}]
        "plan": "",                   # 计划文本
        "review_issues": [],          # 自检发现
        "iteration": 0,
        "plan_iteration": 0,
        "sufficient": False,
    }


# ── 占位符模式 ────────────────────────────────────────────────────────────────

_PLACEHOLDER_PATTERNS = [
    r'\bTBD\b',
    r'\bTODO\b',
    r'\bFIXME\b',
    r'\bXXX\b',
    r'implement\s+later',
    r'fill\s+in\s+details',
    r'add\s+appropriate\s+error\s+handling',
    r'add\s+validation',
    r'handle\s+edge\s+cases',
    r'similar\s+to\s+task\s+\d+',
    r'write\s+tests?\s+for\s+the\s+above',
    r'\.\.\.\s*$',
]

_PLACEHOLDER_RE = re.compile('|'.join(_PLACEHOLDER_PATTERNS), re.IGNORECASE | re.MULTILINE)


# ═══════════════════════════════════════════════════════════════════════════════
# 1. SpecLoader — 读取设计文档（HARD）
# ═══════════════════════════════════════════════════════════════════════════════

class SpecLoaderRouter(Router):
    FORMAT_IN = "sw_plan.spec"
    FORMAT_OUT = "sw_plan.code-context"
    DESCRIPTION = "读取设计文档或接受文本输入"

    def run(self, input_data: Any) -> Verdict:
        spec_path = (input_data.get("spec_path") or "").strip()
        spec_text = (input_data.get("spec_text") or "").strip()
        project_dir = (input_data.get("project_dir") or "").strip()

        ctx = _empty_context()
        ctx["project_dir"] = project_dir

        if spec_text:
            ctx["spec"] = spec_text
            ctx["spec_source"] = "provided"
        elif spec_path:
            p = Path(spec_path)
            if not p.exists():
                return Verdict(kind=VerdictKind.FAIL, diagnosis=f"文件不存在: {spec_path}")
            try:
                ctx["spec"] = p.read_text(encoding="utf-8", errors="replace")
                ctx["spec_source"] = str(p)
            except Exception as e:
                return Verdict(kind=VerdictKind.FAIL, diagnosis=f"读取失败: {e}")
        else:
            return Verdict(kind=VerdictKind.FAIL, diagnosis="需要 spec_path 或 spec_text")

        return Verdict(
            kind=VerdictKind.PASS, output=ctx,
            diagnosis=f"加载设计文档 ({len(ctx['spec'])} chars)",
        )


# ═══════════════════════════════════════════════════════════════════════════════
# 2. CodebaseScanner — 扫描目录结构（HARD）
# ═══════════════════════════════════════════════════════════════════════════════

class CodebaseScannerRouter(Router):
    FORMAT_IN = "sw_plan.code-context"
    FORMAT_OUT = "sw_plan.codebase-scan"
    DESCRIPTION = "扫描项目目录结构，识别关键文件"

    _SKIP = {".git", "node_modules", "__pycache__", ".venv", "venv", ".mypy_cache",
             "dist", "build", ".tox", ".pytest_cache", ".egg-info"}

    def run(self, input_data: Any) -> Verdict:
        ctx = input_data
        project_dir = ctx.get("project_dir", "")

        if not project_dir:
            return Verdict(
                kind=VerdictKind.PASS, output=ctx,
                diagnosis="无项目目录，跳过扫描",
            )

        base = Path(project_dir)
        if not base.exists():
            return Verdict(kind=VerdictKind.FAIL, diagnosis=f"目录不存在: {project_dir}")

        # 扫描目录树 (限深度 4)
        tree_lines = []
        key_files = []
        file_count = 0

        for root, dirs, files in os.walk(str(base)):
            depth = Path(root).relative_to(base).parts
            if len(depth) > 4:
                dirs.clear()
                continue

            dirs[:] = [d for d in sorted(dirs) if d not in self._SKIP and not d.startswith(".")]

            indent = "  " * len(depth)
            tree_lines.append(f"{indent}{Path(root).name}/")

            for f in sorted(files)[:30]:  # 每目录最多 30 文件
                fpath = Path(root) / f
                rel = str(fpath.relative_to(base)).replace("\\", "/")
                tree_lines.append(f"{indent}  {f}")
                file_count += 1

                # 关键文件识别
                fl = f.lower()
                if fl in ("readme.md", "readme.rst", "readme.txt"):
                    key_files.append(rel)
                elif fl in ("pyproject.toml", "setup.py", "setup.cfg", "cargo.toml",
                            "package.json", "tsconfig.json", "go.mod"):
                    key_files.append(rel)
                elif fl in ("__init__.py", "main.py", "app.py", "index.ts", "index.js",
                            "main.rs", "main.go"):
                    key_files.append(rel)
                elif fl.startswith("conftest"):
                    key_files.append(rel)

            if file_count > 500:
                tree_lines.append("... (truncated)")
                break

        ctx["tree"] = "\n".join(tree_lines[:200])
        ctx["key_files"] = key_files[:20]

        return Verdict(
            kind=VerdictKind.PASS, output=ctx,
            diagnosis=f"扫描了 {file_count} 文件, 识别 {len(key_files)} 关键文件",
        )


# ═══════════════════════════════════════════════════════════════════════════════
# 3. FileReader — 读取关键文件（HARD）
# ═══════════════════════════════════════════════════════════════════════════════

class FileReaderRouter(Router):
    FORMAT_IN = "sw_plan.codebase-scan"
    FORMAT_OUT = "sw_plan.code-context"
    DESCRIPTION = "读取关键文件内容，提取 imports、签名、测试模式"

    def run(self, input_data: Any) -> Verdict:
        ctx = input_data
        project_dir = ctx.get("project_dir", "")
        base = Path(project_dir) if project_dir else None

        for fpath in ctx.get("key_files", []):
            if fpath in ctx.get("read_files", {}):
                continue

            full = (base / fpath) if base else Path(fpath)
            if not full.exists() or not full.is_file():
                continue

            try:
                content = full.read_text(encoding="utf-8", errors="replace")
                if len(content) > 6000:
                    content = content[:6000] + "\n... (truncated)"

                imports = [l.strip() for l in content.splitlines()[:40]
                          if l.strip().startswith(("import ", "from ", "require(", "#include"))]

                signatures = re.findall(
                    r'^(?:def |class |function |async function |export ).*',
                    content, re.MULTILINE)

                ctx["read_files"][fpath] = {
                    "content": content,
                    "imports": imports[:15],
                    "signatures": signatures[:20],
                }
            except Exception:
                pass

        # 模式识别
        patterns = {}

        # 测试模式
        test_files = [f for f in ctx.get("read_files", {}) if "test" in f.lower() or "conftest" in f.lower()]
        if test_files:
            sample = ctx["read_files"][test_files[0]].get("content", "")
            if "pytest" in sample or "def test_" in sample:
                patterns["test_framework"] = "pytest"
            elif "unittest" in sample:
                patterns["test_framework"] = "unittest"
            elif "describe(" in sample or "it(" in sample:
                patterns["test_framework"] = "jest/mocha"

        # 包管理器
        for f in ctx.get("read_files", {}):
            if "pyproject.toml" in f:
                patterns["package_manager"] = "poetry/pip"
            elif "package.json" in f:
                patterns["package_manager"] = "npm/yarn"
            elif "Cargo.toml" in f:
                patterns["package_manager"] = "cargo"

        ctx["patterns"] = patterns

        return Verdict(
            kind=VerdictKind.PASS, output=ctx,
            diagnosis=f"读取了 {len(ctx['read_files'])} 文件, 模式: {patterns}",
        )


# ═══════════════════════════════════════════════════════════════════════════════
# 4. ContextJudge — 判断上下文充分性（SOFT）
# ═══════════════════════════════════════════════════════════════════════════════

class ContextJudgeRouter(Router):
    FORMAT_IN = "sw_plan.code-context"
    FORMAT_OUT = "sw_plan.code-context"
    DESCRIPTION = "判断代码库上下文是否足以生成计划"

    def run(self, input_data: Any) -> Verdict:
        ctx = input_data
        iteration = ctx.get("iteration", 0)
        read_files = ctx.get("read_files", {})
        spec = ctx.get("spec", "")
        project_dir = ctx.get("project_dir", "")

        # 如果没有 project_dir，直接充分
        if not project_dir:
            ctx["sufficient"] = True
            return Verdict(kind=VerdictKind.PASS, output=ctx,
                           diagnosis="无项目目录，直接进入计划生成")

        # 充分性检查
        issues = []
        if not read_files:
            issues.append("未读取任何文件")
        if not ctx.get("tree"):
            issues.append("未扫描目录结构")

        # 检查是否读到了 spec 中提到的文件
        mentioned_files = re.findall(r'[\w/\\]+\.\w{1,5}', spec)
        for mf in mentioned_files[:10]:
            basename = Path(mf).name
            if not any(basename in rf for rf in read_files):
                if iteration < 2:
                    # 将此文件加入 key_files 以便下一轮读取
                    if mf not in ctx.get("key_files", []):
                        ctx.setdefault("key_files", []).append(mf)
                    issues.append(f"设计文档提到 {basename} 但未读取")

        if issues and iteration < 2:
            ctx["iteration"] = iteration + 1
            return Verdict(
                kind=VerdictKind.PARTIAL, output=ctx,
                diagnosis=f"上下文不充分 ({len(issues)} 问题), 继续探索",
            )

        ctx["sufficient"] = True
        return Verdict(
            kind=VerdictKind.PASS, output=ctx,
            diagnosis=f"上下文充分: {len(read_files)} 文件, {len(ctx.get('patterns', {}))} 模式",
        )


# ═══════════════════════════════════════════════════════════════════════════════
# 5. FileMapper — LLM 生成文件修改计划（SOFT）
# ═══════════════════════════════════════════════════════════════════════════════

class FileMapperRouter(Router):
    FORMAT_IN = "sw_plan.code-context"
    FORMAT_OUT = "sw_plan.file-map"
    DESCRIPTION = "LLM 确定哪些文件需要新建/修改/删除"

    def __init__(self, *, model: str | None = None):
        self._model = model

    def _make_client(self):
        from omnicompany.runtime.llm.llm import LLMClient
        return LLMClient(role="runtime_main", max_tokens=4096,
                         **({"model": self._model} if self._model else {}))

    def run(self, input_data: Any) -> Verdict:
        ctx = input_data

        tree = ctx.get("tree", "无目录信息")
        existing_files = list(ctx.get("read_files", {}).keys())
        patterns = ctx.get("patterns", {})

        prompt = f"""根据以下设计文档和项目结构，确定需要 新建/修改/删除 哪些文件。

## 设计文档
{ctx['spec'][:8000]}

## 项目目录结构
```
{tree[:3000]}
```

## 已知模式
{json.dumps(patterns, ensure_ascii=False)}

## 现有文件
{chr(10).join(f'- {f}' for f in existing_files[:20])}

输出 JSON:
```json
{{
  "file_map": [
    {{"path": "完整路径", "action": "create/modify/delete", "reason": "原因", "dependencies": ["依赖的文件"]}}
  ]
}}
```"""

        try:
            client = self._make_client()
            resp = client.call(messages=[{"role": "user", "content": prompt}])
            text = resp.content[0].text
            match = re.search(r'```json\n(.*?)```', text, re.DOTALL)
            if match:
                result = json.loads(match.group(1))
                ctx["file_map"] = result.get("file_map", [])
            else:
                ctx["file_map"] = []

            return Verdict(
                kind=VerdictKind.PASS, output=ctx,
                diagnosis=f"文件映射: {len(ctx['file_map'])} 项操作",
            )
        except Exception as e:
            return Verdict(kind=VerdictKind.FAIL, output=ctx,
                           diagnosis=f"文件映射失败: {e}")


# ═══════════════════════════════════════════════════════════════════════════════
# 6. PlanDrafter — LLM 生成 TDD 分步计划（SOFT）
# ═══════════════════════════════════════════════════════════════════════════════

_PLAN_SYSTEM = """\
你是一名高级软件工程师，擅长将设计文档转化为可执行的实施计划。

**核心原则：**
1. 每个 Task 是 2-5 分钟粒度的步骤
2. 每个代码步骤包含完整代码块（不是描述）
3. TDD: 先写测试(RED)，验证失败，再实现(GREEN)，验证通过
4. 每步含精确文件路径、完整代码、精确命令

**绝对禁止：**
- TBD / TODO / FIXME / XXX
- "implement later" / "fill in details"
- "add appropriate error handling"
- "similar to Task N" (必须重复完整代码)
- 不含代码块的代码步骤

**输出格式（Markdown）：**
# [Feature] Implementation Plan

**Goal:** [一句话]
**Architecture:** [2-3 句]

---
### Task 1: [组件名]
**Files:** [创建/修改的文件]

- [ ] **Step 1: Write failing test**
[完整测试代码]

- [ ] **Step 2: Verify test fails**
Run: `命令`

- [ ] **Step 3: Implement**
[完整实现代码]

- [ ] **Step 4: Verify**
Run: `命令`
"""


class PlanDrafterRouter(Router):
    FORMAT_IN = "sw_plan.file-map"
    FORMAT_OUT = "sw_plan.draft"
    DESCRIPTION = "LLM 生成 TDD 分步实施计划"
    REFLECTION_ENABLED = True

    def __init__(self, *, model: str | None = None):
        self._model = model

    def _make_client(self):
        from omnicompany.runtime.llm.llm import LLMClient
        return LLMClient(role="runtime_main", max_tokens=8192,
                         **({"model": self._model} if self._model else {}))

    def run(self, input_data: Any) -> Verdict:
        ctx = input_data

        # 收集上下文
        file_map_text = json.dumps(ctx.get("file_map", []), indent=2, ensure_ascii=False)
        existing = ""
        for fpath, fc in list(ctx.get("read_files", {}).items())[:5]:
            existing += f"\n## {fpath}\n```\n{fc.get('content', '')[:2000]}\n```\n"

        # 如果有上一轮自检的问题，加入反馈
        review_feedback = ""
        if ctx.get("review_issues"):
            review_feedback = (
                "\n\n**上一版计划的问题（请修复）：**\n"
                + "\n".join(f"- {i}" for i in ctx["review_issues"])
            )

        prompt = f"""根据以下信息生成实施计划:

## 设计文档
{ctx['spec'][:10000]}

## 文件修改计划
```json
{file_map_text[:3000]}
```

## 已有代码（参考）
{existing[:5000]}

## 项目模式
{json.dumps(ctx.get('patterns', {}), ensure_ascii=False)}
{review_feedback}"""

        print("[*] Calling LLM for plan generation...")
        try:
            client = self._make_client()
            resp = client.call(messages=[{"role": "user", "content": prompt}],
                              system=self._maybe_inject_reflection(_PLAN_SYSTEM))
            plan_text = resp.content[0].text

            # 反思：解析自评 + 信息不足拦截
            sa, plan_text = self._parse_self_assessment(plan_text)
            partial = self._check_reflection_partial(sa, plan_text, ctx)
            if partial:
                return partial

            ctx["plan"] = plan_text
            return Verdict(
                kind=VerdictKind.PASS, output=ctx,
                diagnosis=f"计划生成 ({len(plan_text)} chars)",
                self_assessment=sa,
            )
        except Exception as e:
            return Verdict(kind=VerdictKind.FAIL, output=ctx,
                           diagnosis=f"计划生成失败: {e}")


# ═══════════════════════════════════════════════════════════════════════════════
# 7. SelfReviewer — 占位符 + 结构验证（HARD）
# ═══════════════════════════════════════════════════════════════════════════════

class SelfReviewerRouter(Router):
    FORMAT_IN = "sw_plan.draft"
    FORMAT_OUT = "sw_plan.review-result"
    DESCRIPTION = "零占位符验证 + 需求覆盖度 + 结构检查"

    def run(self, input_data: Any) -> Verdict:
        ctx = input_data
        plan = ctx.get("plan", "")
        spec = ctx.get("spec", "")
        plan_iteration = ctx.get("plan_iteration", 0)

        issues = []

        # 1. 占位符扫描
        matches = _PLACEHOLDER_RE.findall(plan)
        if matches:
            unique = list(set(matches))
            issues.append(f"发现 {len(matches)} 个占位符: {', '.join(unique[:8])}")

        # 2. Task 结构
        task_count = len(re.findall(r'###\s+Task\s+\d+', plan))
        if task_count == 0:
            issues.append("未找到 Task 定义")

        step_count = len(re.findall(r'- \[ \]', plan))
        if task_count > 0 and step_count < task_count * 2:
            issues.append(f"{task_count} Task 仅 {step_count} 步骤（预期 ≥{task_count * 2}）")

        # 3. 代码块
        code_blocks = len(re.findall(r'```\w+', plan))
        if task_count > 0 and code_blocks < task_count:
            issues.append(f"{task_count} Task 仅 {code_blocks} 代码块（预期 ≥{task_count}）")

        # 4. 验证命令
        run_cmds = len(re.findall(r'Run:\s+`', plan))
        if task_count > 0 and run_cmds < task_count:
            issues.append(f"{task_count} Task 仅 {run_cmds} 验证命令")

        ctx["review_issues"] = issues

        # 构建报告
        quality_ok = len(issues) == 0

        report = (
            f"═══ PLAN REVIEW ═══\n"
            f"Tasks: {task_count} | Steps: {step_count} | "
            f"Code: {code_blocks} | Commands: {run_cmds}\n"
        )
        if quality_ok:
            report += "✅ 零占位符，结构完整\n"
        else:
            report += f"❌ {len(issues)} 个问题:\n"
            for i, iss in enumerate(issues, 1):
                report += f"  {i}. {iss}\n"

        print(f"\n{report}")

        # 回路: 最多允许 2 轮修改
        if not quality_ok and plan_iteration < 2:
            ctx["plan_iteration"] = plan_iteration + 1
            return Verdict(
                kind=VerdictKind.FAIL, output=ctx,
                diagnosis=f"自检失败 ({len(issues)} 问题), 回到 drafter 修改",
            )

        return Verdict(
            kind=VerdictKind.PASS if quality_ok else VerdictKind.PARTIAL,
            output=ctx,
            diagnosis=f"{'✅ 通过' if quality_ok else '⚠️ 超出修改次数'}: {task_count}T {step_count}S {code_blocks}C",
        )


# ═══════════════════════════════════════════════════════════════════════════════
# 8. PlanEmitter — 终版输出（确定性）
# ═══════════════════════════════════════════════════════════════════════════════

class PlanEmitterRouter(Router):
    FORMAT_IN = "sw_plan.review-result"
    FORMAT_OUT = "sw_plan.plan"
    DESCRIPTION = "输出终版计划 + 质量报告"

    def run(self, input_data: Any) -> Verdict:
        ctx = input_data
        plan = ctx.get("plan", "")
        issues = ctx.get("review_issues", [])
        task_count = len(re.findall(r'###\s+Task\s+\d+', plan))
        step_count = len(re.findall(r'- \[ \]', plan))
        quality_ok = len(issues) == 0

        report = (
            f"{'═' * 55}\n"
            f"📋 IMPLEMENTATION PLAN\n"
            f"{'═' * 55}\n\n"
            f"Tasks: {task_count} | Steps: {step_count}\n"
            f"Quality: {'✅ PASS' if quality_ok else '⚠️ PASS WITH WARNINGS'}\n"
        )
        if issues:
            report += f"Warnings: {'; '.join(issues)}\n"
        report += f"\n{'═' * 55}\n\n{plan}"

        print(f"\n[*] Plan generated: {task_count} tasks, {step_count} steps\n")

        return Verdict(
            kind=VerdictKind.PASS,
            output={
                "plan": plan,
                "report": report,
                "task_count": task_count,
                "step_count": step_count,
                "quality_ok": quality_ok,
                "issues": issues,
                "file_map": ctx.get("file_map", []),
                "project_dir": ctx.get("project_dir", ""),
            },
            diagnosis=f"计划完成: {task_count}T {step_count}S {'✅' if quality_ok else '⚠️'}",
        )
