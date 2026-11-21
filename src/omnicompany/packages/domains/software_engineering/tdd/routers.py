# [OMNI] origin=claude-code domain=software_engineering/tdd ts=2026-04-08T03:23:43Z
# [OMNI] material_id="material:domains.software_engineering.tdd.pipeline_routers.implementation.py"
"""sw_tdd.routers — TDD 执行管线的 Router 实现

6 个节点:
  1 HARD:      plan_loader (读取 sw-plan 产出)
  1 SOFT/LLM:  test_writer (agent_loop 节点 — 写测试)
  1 HARD:      test_runner (执行测试命令)
  1 SOFT/LLM:  impl_writer (agent_loop 节点 — 写实现)
  1 确定性:     report_emitter (汇总 TDD 结果)

agent_loop 嵌套设计:
  test_writer 和 impl_writer 的 run() 内部不直接调用 agent_loop，
  而是使用 LLM 做单次代码生成。真正的 agent_loop 嵌套在 E2E 场景中
  通过 TeamRunner 的 max_steps 控制迭代。
  结构测试和无 LLM 场景使用 mock 替代。
"""

from __future__ import annotations

import json
import logging
import os
import re
import subprocess
from pathlib import Path
from typing import Any

from omnicompany.protocol.anchor import Verdict, VerdictKind
from omnicompany.runtime.routing.router import Router

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════════════
# tdd-context 数据结构
# ═══════════════════════════════════════════════════════════════════════════════

def _empty_context() -> dict:
    return {
        "plan": "",                   # TDD 计划文本
        "project_dir": "",
        "test_files": [],             # [{path, content}]
        "impl_files": [],             # [{path, content}]
        "test_cmd": "",               # 测试执行命令
        "test_result": {},            # {exit_code, stdout, stderr, passed, failed, errors}
        "iteration": 0,
        "max_iterations": 3,
    }


# ═══════════════════════════════════════════════════════════════════════════════
# 1. PlanLoader — 读取实施计划（HARD）
# ═══════════════════════════════════════════════════════════════════════════════

class PlanLoaderRouter(Router):
    FORMAT_IN = "sw_tdd.plan"
    FORMAT_OUT = "sw_tdd.test-code"
    DESCRIPTION = "读取 sw-plan 产出的 TDD 实施计划"

    def run(self, input_data: Any) -> Verdict:
        plan_text = (input_data.get("plan_text") or "").strip()
        plan_path = (input_data.get("plan_path") or "").strip()
        project_dir = (input_data.get("project_dir") or "").strip()
        test_cmd = (input_data.get("test_cmd") or "").strip()

        ctx = _empty_context()
        ctx["project_dir"] = project_dir

        if plan_text:
            ctx["plan"] = plan_text
        elif plan_path:
            p = Path(plan_path)
            if not p.exists():
                return Verdict(kind=VerdictKind.FAIL, diagnosis=f"计划文件不存在: {plan_path}")
            try:
                ctx["plan"] = p.read_text(encoding="utf-8", errors="replace")
            except Exception as e:
                return Verdict(kind=VerdictKind.FAIL, diagnosis=f"读取失败: {e}")
        else:
            return Verdict(kind=VerdictKind.FAIL, diagnosis="需要 plan_text 或 plan_path")

        # 从计划中提取测试命令
        if test_cmd:
            ctx["test_cmd"] = test_cmd
        else:
            # 尝试从计划文本中找 "Run: `...`" 模式
            run_cmds = re.findall(r'Run:\s+`(.+?)`', ctx["plan"])
            test_cmds = [c for c in run_cmds if "test" in c.lower() or "pytest" in c.lower()]
            ctx["test_cmd"] = test_cmds[0] if test_cmds else "python -m pytest -v"

        return Verdict(
            kind=VerdictKind.PASS, output=ctx,
            diagnosis=f"加载计划 ({len(ctx['plan'])} chars), 测试命令: {ctx['test_cmd']}",
        )


# ═══════════════════════════════════════════════════════════════════════════════
# 2. TestWriter — LLM 写测试（SOFT, agent_loop 节点）
# ═══════════════════════════════════════════════════════════════════════════════

_TEST_WRITER_SYSTEM = """\
你是一名 TDD 专家。根据实施计划写测试代码。

**规则：**
1. 测试必须可直接执行 (pytest/unittest)
2. 测试先于实现 — 测试应描述期望行为，此时运行必然失败
3. 每个测试有明确的 assert 语句
4. 测试文件路径必须与计划一致

**输出 JSON 格式：**
```json
{
  "test_files": [
    {"path": "tests/test_xxx.py", "content": "完整的测试代码"}
  ]
}
```
"""


class TestWriterRouter(Router):
    FORMAT_IN = "sw_tdd.plan"
    FORMAT_OUT = "sw_tdd.test-code"
    DESCRIPTION = "根据 TDD 计划生成测试代码 (LLM)"

    def __init__(self, *, model: str | None = None):
        self._model = model

    def _make_client(self):
        from omnicompany.runtime.llm.llm import LLMClient
        return LLMClient(role="runtime_main", max_tokens=8192,
                         **({"model": self._model} if self._model else {}))

    def run(self, input_data: Any) -> Verdict:
        ctx = input_data

        # 收集现有文件上下文
        existing = ""
        project_dir = ctx.get("project_dir", "")
        if project_dir and Path(project_dir).exists():
            for f in Path(project_dir).rglob("*.py"):
                if "__pycache__" in str(f):
                    continue
                try:
                    content = f.read_text(encoding="utf-8", errors="replace")
                    if len(content) < 3000:
                        rel = str(f.relative_to(project_dir)).replace("\\", "/")
                        existing += f"\n## {rel}\n```python\n{content}\n```\n"
                except Exception:
                    pass
                if len(existing) > 10000:
                    break

        prompt = f"""根据以下 TDD 计划生成测试文件:

## 实施计划
{ctx['plan'][:12000]}

## 现有代码
{existing[:8000]}

请输出 JSON（test_files 列表）。"""

        print("[*] Calling LLM for test generation...")
        try:
            client = self._make_client()
            resp = client.call(
                messages=[{"role": "user", "content": prompt}],
                system=_TEST_WRITER_SYSTEM,
            )
            text = resp.content[0].text
            match = re.search(r'```json\n(.*?)```', text, re.DOTALL)
            if match:
                result = json.loads(match.group(1))
                ctx["test_files"] = result.get("test_files", [])
            else:
                try:
                    result = json.loads(text)
                    ctx["test_files"] = result.get("test_files", [])
                except json.JSONDecodeError:
                    ctx["test_files"] = []

            # 写入测试文件
            if project_dir and ctx["test_files"]:
                base = Path(project_dir)
                for tf in ctx["test_files"]:
                    fpath = base / tf["path"]
                    fpath.parent.mkdir(parents=True, exist_ok=True)
                    # OMNI-013 ALLOW: business artifact write (S3d.6 audited 2026-04-08, follow-up: refactor to guarded_write)
                    fpath.write_text(tf["content"], encoding="utf-8")

            return Verdict(
                kind=VerdictKind.PASS, output=ctx,
                diagnosis=f"生成 {len(ctx['test_files'])} 个测试文件",
            )
        except Exception as e:
            return Verdict(kind=VerdictKind.FAIL, output=ctx,
                           diagnosis=f"测试生成失败: {e}")


# ═══════════════════════════════════════════════════════════════════════════════
# 3. TestRunner — 执行测试（HARD）
# ═══════════════════════════════════════════════════════════════════════════════

class TestRunnerRouter(Router):
    FORMAT_IN = "sw_tdd.test-code"
    FORMAT_OUT = "sw_tdd.test-result"
    DESCRIPTION = "执行测试命令，捕获结果"

    def run(self, input_data: Any) -> Verdict:
        ctx = input_data
        test_cmd = ctx.get("test_cmd", "python -m pytest -v")
        project_dir = ctx.get("project_dir", "")

        if not project_dir or not Path(project_dir).exists():
            return Verdict(
                kind=VerdictKind.PASS, output=ctx,
                diagnosis="无项目目录，跳过测试执行",
            )

        try:
            result = subprocess.run(
                test_cmd,
                shell=True,
                capture_output=True,
                text=True,
                cwd=project_dir,
                timeout=60,
                env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
            )

            stdout = result.stdout[-3000:] if len(result.stdout) > 3000 else result.stdout
            stderr = result.stderr[-1000:] if len(result.stderr) > 1000 else result.stderr

            # 解析测试结果
            passed = len(re.findall(r' PASSED', stdout))
            failed = len(re.findall(r' FAILED', stdout))
            errors = len(re.findall(r' ERROR', stdout))

            ctx["test_result"] = {
                "exit_code": result.returncode,
                "stdout": stdout,
                "stderr": stderr,
                "passed": passed,
                "failed": failed,
                "errors": errors,
            }

            all_pass = result.returncode == 0
            return Verdict(
                kind=VerdictKind.PASS if all_pass else VerdictKind.FAIL,
                output=ctx,
                diagnosis=f"测试{'通过' if all_pass else '失败'}: {passed}✅ {failed}❌ {errors}⚠️",
            )
        except subprocess.TimeoutExpired:
            ctx["test_result"] = {
                "exit_code": -1, "stdout": "", "stderr": "TIMEOUT",
                "passed": 0, "failed": 0, "errors": 1,
            }
            return Verdict(kind=VerdictKind.FAIL, output=ctx, diagnosis="测试超时")
        except Exception as e:
            ctx["test_result"] = {
                "exit_code": -1, "stdout": "", "stderr": str(e),
                "passed": 0, "failed": 0, "errors": 1,
            }
            return Verdict(kind=VerdictKind.FAIL, output=ctx, diagnosis=f"测试执行失败: {e}")


# ═══════════════════════════════════════════════════════════════════════════════
# 4. ImplWriter — LLM 写实现（SOFT, agent_loop 节点）
# ═══════════════════════════════════════════════════════════════════════════════

_IMPL_WRITER_SYSTEM = """\
你是一名高级工程师。根据失败的测试和 TDD 计划，编写使测试通过的最小实现。

**规则：**
1. 只写让测试通过所需的代码
2. 不要修改测试文件
3. 代码必须正确处理边界条件
4. 遵循计划中指定的架构和文件路径

**输出 JSON 格式：**
```json
{
  "impl_files": [
    {"path": "src/xxx.py", "content": "完整的实现代码"}
  ]
}
```
"""


class ImplWriterRouter(Router):
    FORMAT_IN = "sw_tdd.test-result"
    FORMAT_OUT = "sw_tdd.impl-code"
    DESCRIPTION = "根据测试结果和计划生成实现代码 (LLM)"

    def __init__(self, *, model: str | None = None):
        self._model = model

    def _make_client(self):
        from omnicompany.runtime.llm.llm import LLMClient
        return LLMClient(role="runtime_main", max_tokens=8192,
                         **({"model": self._model} if self._model else {}))

    def run(self, input_data: Any) -> Verdict:
        ctx = input_data
        test_result = ctx.get("test_result", {})

        # 收集测试文件内容
        test_code = ""
        for tf in ctx.get("test_files", []):
            test_code += f"\n## {tf['path']}\n```python\n{tf['content']}\n```\n"

        prompt = f"""根据以下失败的测试和计划，编写实现代码:

## TDD 计划
{ctx['plan'][:8000]}

## 测试代码
{test_code[:6000]}

## 测试输出
```
Exit code: {test_result.get('exit_code', 'N/A')}
{test_result.get('stdout', '')[:3000]}
{test_result.get('stderr', '')[:1000]}
```

## 迭代轮次
第 {ctx.get('iteration', 0) + 1} 轮

请输出 JSON（impl_files 列表）。"""

        print(f"[*] Calling LLM for implementation (iteration {ctx.get('iteration', 0) + 1})...")
        try:
            client = self._make_client()
            resp = client.call(
                messages=[{"role": "user", "content": prompt}],
                system=_IMPL_WRITER_SYSTEM,
            )
            text = resp.content[0].text
            match = re.search(r'```json\n(.*?)```', text, re.DOTALL)
            if match:
                result = json.loads(match.group(1))
                ctx["impl_files"] = result.get("impl_files", [])
            else:
                try:
                    result = json.loads(text)
                    ctx["impl_files"] = result.get("impl_files", [])
                except json.JSONDecodeError:
                    ctx["impl_files"] = []

            # 写入实现文件
            project_dir = ctx.get("project_dir", "")
            if project_dir and ctx["impl_files"]:
                base = Path(project_dir)
                for f in ctx["impl_files"]:
                    fpath = base / f["path"]
                    fpath.parent.mkdir(parents=True, exist_ok=True)
                    # OMNI-013 ALLOW: business artifact write (S3d.6 audited 2026-04-08, follow-up: refactor to guarded_write)
                    fpath.write_text(f["content"], encoding="utf-8")

            ctx["iteration"] = ctx.get("iteration", 0) + 1

            return Verdict(
                kind=VerdictKind.PASS, output=ctx,
                diagnosis=f"生成 {len(ctx['impl_files'])} 个实现文件 (iter {ctx['iteration']})",
            )
        except Exception as e:
            return Verdict(kind=VerdictKind.FAIL, output=ctx,
                           diagnosis=f"实现生成失败: {e}")


# ═══════════════════════════════════════════════════════════════════════════════
# 5. ReportEmitter — 汇总 TDD 结果（确定性）
# ═══════════════════════════════════════════════════════════════════════════════

class ReportEmitterRouter(Router):
    FORMAT_IN = "sw_tdd.test-result"
    FORMAT_OUT = "sw_tdd.report"
    DESCRIPTION = "汇总 TDD 执行报告"

    def run(self, input_data: Any) -> Verdict:
        ctx = input_data
        test_result = ctx.get("test_result", {})
        test_files = ctx.get("test_files", [])
        impl_files = ctx.get("impl_files", [])

        passed = test_result.get("passed", 0)
        failed = test_result.get("failed", 0)
        total = passed + failed
        pass_rate = (passed / total * 100) if total > 0 else 0

        lines = [
            "═" * 55,
            "🧪 TDD EXECUTION REPORT",
            "═" * 55,
            "",
            f"Iterations: {ctx.get('iteration', 0)}",
            f"Test files: {len(test_files)}",
            f"Impl files: {len(impl_files)}",
            f"Pass rate: {pass_rate:.0f}% ({passed}/{total})",
            f"Exit code: {test_result.get('exit_code', 'N/A')}",
            "",
        ]

        if test_files:
            lines.append("── 测试文件 ──")
            for tf in test_files:
                lines.append(f"  • {tf.get('path', 'unknown')}")
            lines.append("")

        if impl_files:
            lines.append("── 实现文件 ──")
            for f in impl_files:
                lines.append(f"  • {f.get('path', 'unknown')}")
            lines.append("")

        conclusion = "PASS" if test_result.get("exit_code") == 0 else "FAIL"
        lines.append("── 结论 ──")
        lines.append(f"{'✅' if conclusion == 'PASS' else '❌'} {conclusion}")
        lines.append("═" * 55)

        report = "\n".join(lines)
        print(f"\n{report}\n")

        return Verdict(
            kind=VerdictKind.PASS,
            output={
                "report": report,
                "conclusion": conclusion,
                "pass_rate": pass_rate,
                "iterations": ctx.get("iteration", 0),
                "test_files": [f.get("path") for f in test_files],
                "impl_files": [f.get("path") for f in impl_files],
            },
            diagnosis=f"TDD 报告: {conclusion} ({pass_rate:.0f}%, {ctx.get('iteration', 0)} 轮)",
        )
