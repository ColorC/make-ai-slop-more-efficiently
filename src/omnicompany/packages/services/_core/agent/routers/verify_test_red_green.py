# [OMNI] origin=claude-code domain=services/agent/routers ts=2026-04-25T00:00:00Z type=infrastructure
# [OMNI] material_id="material:core.agent.routers.test_redgreen.verifier.py"
"""VerifyTestRedGreenRouter · 测试红绿基础验证 SingleTool (Stage E 红绿升级).

**用途** (2026-04-25 用户 TDD 纠偏):
  测试要先过 TDD 红绿二元先验:
  - 源 stub 时 必红 (测能感知源缺失)
  - 源原样时 必绿 (测对正确源不误报)
  之前用的"反转字面量必失败"只证 vitest 在跑, 不证测能感知 bug.

**调用流程** (deterministic, 失败安全):
  1. 备份 source_file 当前内容
  2. 写 stub_source_text 到 source_file
  3. 跑 vitest test_file → 录红色检结果 (期望大量 fail)
  4. **无论上一步成败**, 还原 source_file
  5. 再跑 vitest → 录绿色检结果 (期望全 pass)
  6. 解析 vitest json, 给每条 test 一个 (can_red, can_green)
  7. 返报告

**安全网**:
  - source_file 必须在 ctx.allowed_red_green_roots 树下 (从严默认)
  - 备份+还原走 try/finally; 即便 KeyboardInterrupt 也尽力还原
  - 多次调用并发不安全 (写共享文件); 调用方自行串行

**典型用法** (TestGenAgent 写完测后自验):
  ```python
  ctx["allowed_red_green_roots"] = (str(workspace_root),)
  result = verify_test_red_green({
      "test_file": "tests/store/gameStore.test.js",
      "source_file": "src/store/gameStore.js",
      "stub_source_text": "// stub: 所有 exports throw NotImplementedError\n..."
  })
  # 检 result.per_test_analysis 找空壳测 + 假绿测
  ```
"""
from __future__ import annotations

import json
import logging
import subprocess
import time
from pathlib import Path
from typing import ClassVar

from omnicompany.packages.services._core.agent.routers.single_tool import (
    SingleToolRouter,
    ToolContext,
    ToolExecutionError,
)

logger = logging.getLogger(__name__)


def _run_vitest(workspace_root: Path, test_file_rel: str, timeout_s: int = 60) -> dict:
    """跑 npx vitest run --reporter=json. 返 {ok, passed, failed, total, tests, error?}."""
    cmd_str = f'npx --no-install vitest run --reporter=json "{test_file_rel}"'
    t0 = time.time()
    try:
        proc = subprocess.run(
            cmd_str,
            cwd=str(workspace_root),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_s,
            shell=True,
        )
    except subprocess.TimeoutExpired as e:
        return {
            "ok": False, "exit": -1, "passed": 0, "failed": 0, "total": 0,
            "tests": [], "error": f"timeout {timeout_s}s",
            "duration_ms": int((time.time() - t0) * 1000),
        }
    except Exception as e:
        return {
            "ok": False, "exit": -2, "passed": 0, "failed": 0, "total": 0,
            "tests": [], "error": f"exec error: {e}",
            "duration_ms": int((time.time() - t0) * 1000),
        }

    stdout = proc.stdout or ""
    raw: dict | None = None
    j_start = stdout.rfind("{\n")
    if j_start == -1:
        j_start = stdout.find("{")
    if j_start != -1:
        try:
            raw = json.loads(stdout[j_start:])
        except json.JSONDecodeError:
            try:
                end = stdout.rfind("}")
                raw = json.loads(stdout[j_start:end + 1])
            except Exception:
                raw = None

    passed = failed = total = 0
    tests: list[dict] = []
    if isinstance(raw, dict):
        total = raw.get("numTotalTests", 0)
        passed = raw.get("numPassedTests", 0)
        failed = raw.get("numFailedTests", 0)
        for tr in raw.get("testResults", []):
            for ar in tr.get("assertionResults", []) or []:
                tests.append({
                    "name": ar.get("fullName") or ar.get("title", ""),
                    "title": ar.get("title", ""),
                    "ancestorTitles": ar.get("ancestorTitles", []),
                    "status": ar.get("status"),
                })

    return {
        "ok": proc.returncode == 0 and failed == 0,
        "exit": proc.returncode,
        "passed": passed, "failed": failed, "total": total,
        "tests": tests,
        "stdout_tail": stdout[-1500:] if not raw else "",
        "stderr_tail": (proc.stderr or "")[-1500:],
        "duration_ms": int((time.time() - t0) * 1000),
    }


class VerifyTestRedGreenRouter(SingleToolRouter):
    # 跑 bash 命令测试, 通用 IO
    CONSUMED_META_IO = ("*",)
    PRODUCED_META_IO = ()

    """TDD 红绿二元先验验证 (Phase A): 源 stub 必红 + 源原样必绿.

    调用方写好测后用此工具自验. 不通过的测必修.
    """

    TOOL_NAME: ClassVar[str] = "verify_test_red_green"
    DESCRIPTION: ClassVar[str] = (
        "Verify a test file's TDD red-green basics: stub the source under test, "
        "the test must FAIL ('red'); restore source, the test must PASS ('green'). "
        "Both must hold for the test to be considered alive (not a no-op).\n\n"
        "Returns a per-test analysis: {test_name → {can_red, can_green, issue}}.\n"
        "issue values: 'NOOP' (passes even when source stubbed = empty assertion); "
        "'WRONG_EXPECT' (fails on real source = test expectation broken); "
        "'OK' (red and green both confirmed).\n\n"
        "Use this AFTER writing tests, BEFORE finish. Fix any NOOP/WRONG_EXPECT tests "
        "and re-verify until all are OK.\n\n"
        "The stub_source_text replaces source_file entirely during the red check. "
        "Suggested stub: keep the same `export` declarations but make each export "
        "throw `new Error('NotImplemented')` or return obviously-wrong sentinel. "
        "The source is auto-restored after the call (try/finally), even on errors."
    )
    INPUT_SCHEMA: ClassVar[dict] = {
        "type": "object",
        "properties": {
            "test_file": {
                "type": "string",
                "description": "Path to the .test.js file (relative to workspace_root, or absolute under workspace_root).",
            },
            "source_file": {
                "type": "string",
                "description": "Path to the source file being tested (relative to workspace_root, or absolute).",
            },
            "stub_source_text": {
                "type": "string",
                "description": "Full replacement content for source_file during the red check. Should keep export shapes but make functions return null / throw / etc.",
            },
            "timeout_s": {
                "type": "integer",
                "description": "Per-vitest-run timeout in seconds. Default 60.",
            },
        },
        "required": ["test_file", "source_file", "stub_source_text"],
    }
    IS_CONCURRENCY_SAFE: ClassVar[bool] = False  # 写共享文件
    IS_READONLY: ClassVar[bool] = False

    def _execute(self, args: dict, ctx: ToolContext) -> str:
        test_file_arg = (args.get("test_file") or "").strip()
        source_file_arg = (args.get("source_file") or "").strip()
        stub_text = args.get("stub_source_text", "")
        timeout_s = int(args.get("timeout_s") or 60)
        if not test_file_arg or not source_file_arg:
            raise ToolExecutionError("test_file and source_file both required")
        if not isinstance(stub_text, str) or len(stub_text.strip()) == 0:
            raise ToolExecutionError("stub_source_text must be non-empty string")

        # 安全: source_file 必须在 ctx.allowed_red_green_roots 某根树下 (从严)
        allowed_roots = getattr(ctx, "allowed_red_green_roots", None) or ()
        if not allowed_roots:
            raise ToolExecutionError(
                "verify_test_red_green requires ctx.allowed_red_green_roots declared "
                "(workspace_root list). Refusing to mutate arbitrary file."
            )

        # 路径解析: 相对路径就把 allowed_roots[0] 当 workspace_root
        try:
            primary_root = Path(allowed_roots[0]).resolve()
        except Exception as e:
            raise ToolExecutionError(f"invalid allowed_red_green_roots[0]: {e}")

        def _resolve(p: str) -> Path:
            pp = Path(p)
            if not pp.is_absolute():
                pp = primary_root / p
            return pp.resolve()

        source_path = _resolve(source_file_arg)
        test_path = _resolve(test_file_arg)

        # 严格检 source_path 在某 allowed root 下
        in_allowed = False
        for r in allowed_roots:
            try:
                source_path.relative_to(Path(r).resolve())
                in_allowed = True
                break
            except ValueError:
                continue
        if not in_allowed:
            raise ToolExecutionError(
                f"source_file {source_path} outside allowed_red_green_roots {list(allowed_roots)}"
            )

        if not source_path.exists():
            raise ToolExecutionError(f"source_file does not exist: {source_path}")
        if not test_path.exists():
            raise ToolExecutionError(f"test_file does not exist: {test_path}")

        # 算相对路径给 vitest 用
        try:
            test_rel = str(test_path.relative_to(primary_root)).replace("\\", "/")
        except ValueError:
            test_rel = str(test_path)

        # 备份
        try:
            original_bytes = source_path.read_bytes()
        except Exception as e:
            raise ToolExecutionError(f"failed to read source_file: {e}")

        red_check: dict = {}
        green_check: dict = {}
        try:
            # 写 stub
            try:
                source_path.write_text(stub_text, encoding="utf-8")
            except Exception as e:
                raise ToolExecutionError(f"failed to write stub: {e}")
            # 跑红
            red_check = _run_vitest(primary_root, test_rel, timeout_s=timeout_s)
        finally:
            # 还原 — 极重要, 不论上面崩没崩
            try:
                source_path.write_bytes(original_bytes)
            except Exception as restore_err:
                # 致命: 还原失败. 给 LLM 明确告警.
                logger.error("verify_test_red_green: source restore FAILED: %s", restore_err)
                # 仍 raise 原错让调用方知道
                raise ToolExecutionError(
                    f"CRITICAL: source restore failed after stub. Manual restore needed. err: {restore_err}"
                )

        # 跑绿 (源已还原)
        green_check = _run_vitest(primary_root, test_rel, timeout_s=timeout_s)

        # per-test 分析
        red_status_map = {t["name"]: t["status"] for t in red_check.get("tests", [])}
        green_status_map = {t["name"]: t["status"] for t in green_check.get("tests", [])}
        all_test_names = sorted(set(red_status_map.keys()) | set(green_status_map.keys()))
        per_test: list[dict] = []
        ok_count = noop_count = wrong_expect_count = both_fail_count = unknown_count = 0
        for name in all_test_names:
            r = red_status_map.get(name)
            g = green_status_map.get(name)
            can_red = r == "failed"
            can_green = g == "passed"
            if can_red and can_green:
                issue = "OK"
                ok_count += 1
            elif not can_red and can_green:
                issue = "NOOP"  # 源 stub 还绿 = 测没真断言
                noop_count += 1
            elif can_red and not can_green:
                issue = "WRONG_EXPECT"  # 源原样还红 = 测期望错
                wrong_expect_count += 1
            elif not can_red and not can_green:
                issue = "BROKEN"  # 两次都没 fail/pass 表 (可能 setup 错)
                both_fail_count += 1
            else:
                issue = "UNKNOWN"
                unknown_count += 1
            per_test.append({
                "name": name,
                "red_status": r,
                "green_status": g,
                "can_red": can_red,
                "can_green": can_green,
                "issue": issue,
            })

        report = {
            "test_file": test_rel,
            "source_file": str(source_path.relative_to(primary_root)).replace("\\", "/")
                          if source_path.is_relative_to(primary_root) else str(source_path),
            "red_check": {
                "ok_overall": red_check.get("ok"),
                "passed": red_check.get("passed", 0),
                "failed": red_check.get("failed", 0),
                "total": red_check.get("total", 0),
                "exit": red_check.get("exit"),
                "error": red_check.get("error"),
            },
            "green_check": {
                "ok_overall": green_check.get("ok"),
                "passed": green_check.get("passed", 0),
                "failed": green_check.get("failed", 0),
                "total": green_check.get("total", 0),
                "exit": green_check.get("exit"),
                "error": green_check.get("error"),
            },
            "summary": {
                "OK": ok_count,
                "NOOP": noop_count,
                "WRONG_EXPECT": wrong_expect_count,
                "BROKEN": both_fail_count,
                "UNKNOWN": unknown_count,
                "total": len(per_test),
            },
            "per_test": per_test,
        }
        return json.dumps(report, ensure_ascii=False, indent=2)
