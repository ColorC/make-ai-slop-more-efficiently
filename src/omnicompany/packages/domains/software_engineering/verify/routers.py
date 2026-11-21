# [OMNI] origin=claude-code domain=software_engineering/verify ts=2026-04-08T03:23:43Z
# [OMNI] material_id="material:domains.software_engineering.verify.pipeline_routers.implementation.py"
"""sw_verify.routers — 验证管线的 Router 实现

6 个节点:
  1 确定性 Transformer: claim_parser
  2 HARD 执行节点:      env_checker, cmd_executor
  2 SOFT/LLM 节点:      output_analyzer, supplemental_designer
  1 确定性 Transformer: report_emitter
"""

from __future__ import annotations

import json
import logging
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

from omnicompany.protocol.anchor import Verdict, VerdictKind
from omnicompany.runtime.routing.router import Router

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════════════
# verify-context 数据结构
# ═══════════════════════════════════════════════════════════════════════════════

def _empty_context() -> dict:
    return {
        "claim": "",
        "verify_cmd": "",
        "work_dir": "",
        "expect_pattern": "",
        "env_ok": False,
        "executions": [],       # [{cmd, stdout, stderr, exit_code, elapsed}]
        "analyses": [],         # [{verdict, reason, patterns_matched, patterns_missed}]
        "supplementals": [],    # [{cmd, expect_pattern, reason}]
        "iteration": 0,
    }


# ═══════════════════════════════════════════════════════════════════════════════
# 1. ClaimParser — 解析声称，推断预期模式（确定性）
# ═══════════════════════════════════════════════════════════════════════════════

class ClaimParserRouter(Router):
    FORMAT_IN = "sw_verify.claim"
    FORMAT_OUT = "sw_verify.env-check"
    DESCRIPTION = "解析声称文本，推断预期模式，初始化 verify-context"

    # 常见声称 → 预期输出模式映射
    _INFER_MAP = [
        (["pass", "passed", "通过", "成功"], r"(pass|passed|PASSED|0 failed|0 errors|OK|ok|success|SUCCESS)"),
        (["fail", "failed", "失败"], r"(FAIL|fail|failed|FAILED|error|ERROR)"),
        (["clean", "干净", "no error", "no warning"], r"(0 errors|0 warnings|no issues|clean|CLEAN)"),
        (["build", "compile", "编译"], r"(BUILD SUCCESS|compiled|build completed|exit\s*code.*0)"),
        (["lint", "格式"], r"(0 errors|no issues|All checks passed|clean)"),
    ]

    def run(self, input_data: Any) -> Verdict:
        claim = (input_data.get("claim") or "").strip()
        verify_cmd = (input_data.get("verify_cmd") or "").strip()
        work_dir = (input_data.get("work_dir") or "").strip()
        expect_pattern = (input_data.get("expect_pattern") or "").strip()

        if not claim:
            return Verdict(kind=VerdictKind.FAIL, diagnosis="缺少 claim（要验证的声称）")
        if not verify_cmd:
            return Verdict(kind=VerdictKind.FAIL, diagnosis="缺少 verify_cmd（验证命令）")

        # 自动推断预期模式
        if not expect_pattern:
            claim_lower = claim.lower()
            for keywords, pattern in self._INFER_MAP:
                if any(k in claim_lower for k in keywords):
                    expect_pattern = pattern
                    break
            else:
                expect_pattern = r"(pass|success|ok|exit\s*code.*0)"

        ctx = _empty_context()
        ctx.update({
            "claim": claim,
            "verify_cmd": verify_cmd,
            "work_dir": work_dir,
            "expect_pattern": expect_pattern,
        })

        return Verdict(
            kind=VerdictKind.PASS,
            output=ctx,
            diagnosis=f"声称: '{claim}' → cmd='{verify_cmd}' pattern='{expect_pattern}'",
            confidence=1.0,
        )


# ═══════════════════════════════════════════════════════════════════════════════
# 2. EnvChecker — 检查执行环境（HARD）
# ═══════════════════════════════════════════════════════════════════════════════

class EnvCheckerRouter(Router):
    FORMAT_IN = "sw_verify.env-check"
    FORMAT_OUT = "sw_verify.verify-context"
    DESCRIPTION = "检查验证环境: 工作目录存在、命令可执行"

    def run(self, input_data: Any) -> Verdict:
        ctx = input_data
        work_dir = ctx.get("work_dir", "")
        verify_cmd = ctx.get("verify_cmd", "")

        issues = []

        # 检查工作目录
        if work_dir:
            p = Path(work_dir)
            if not p.exists():
                issues.append(f"工作目录不存在: {work_dir}")
            elif not p.is_dir():
                issues.append(f"工作路径不是目录: {work_dir}")

        # 检查命令是否可执行（取第一个 token）
        cmd_parts = verify_cmd.split()
        if cmd_parts:
            cmd_name = cmd_parts[0]
            # 跳过 shell 内置命令
            builtins = {"echo", "exit", "cd", "set", "type", "cmd", "powershell", "python", "pytest", "npm", "node", "git"}
            if cmd_name.lower() not in builtins and not shutil.which(cmd_name):
                issues.append(f"命令不在 PATH 中: {cmd_name}")

        if issues:
            ctx["env_ok"] = False
            return Verdict(
                kind=VerdictKind.FAIL,
                output=ctx,
                diagnosis=f"环境检查失败: {'; '.join(issues)}",
                confidence=1.0,
            )

        ctx["env_ok"] = True
        return Verdict(
            kind=VerdictKind.PASS,
            output=ctx,
            diagnosis="环境就绪",
            confidence=1.0,
        )


# ═══════════════════════════════════════════════════════════════════════════════
# 3. CmdExecutor — 执行验证命令（HARD）
# ═══════════════════════════════════════════════════════════════════════════════

class CmdExecutorRouter(Router):
    FORMAT_IN = "sw_verify.verify-context"
    FORMAT_OUT = "sw_verify.execution"
    DESCRIPTION = "执行验证命令，捕获 stdout/stderr/exit_code"

    def run(self, input_data: Any) -> Verdict:
        ctx = input_data
        iteration = ctx.get("iteration", 0)

        # 使用补充命令（如果有）
        if ctx.get("supplementals") and iteration > 0:
            latest = ctx["supplementals"][-1]
            cmd = latest["cmd"]
        else:
            cmd = ctx["verify_cmd"]

        work_dir = ctx.get("work_dir", "") or None
        cwd = Path(work_dir) if work_dir else None

        logger.info("[sw-verify] Executing: %s (cwd=%s, iteration=%d)", cmd, cwd, iteration)
        print(f"[*] Running verification (round {iteration + 1}): {cmd}")

        import time
        t0 = time.time()
        try:
            result = subprocess.run(
                cmd, shell=True, capture_output=True, text=True,
                timeout=300, cwd=str(cwd) if cwd else None,
                encoding="utf-8", errors="replace",
            )
            elapsed = time.time() - t0
            stdout = result.stdout or ""
            stderr = result.stderr or ""
            exit_code = result.returncode

            # 截断过长输出
            max_len = 10000
            if len(stdout) > max_len:
                stdout = stdout[:max_len] + f"\n... (truncated, {len(result.stdout)} chars total)"
            if len(stderr) > max_len:
                stderr = stderr[:max_len] + f"\n... (truncated, {len(result.stderr)} chars total)"

            exec_record = {
                "cmd": cmd,
                "stdout": stdout,
                "stderr": stderr,
                "exit_code": exit_code,
                "elapsed": round(elapsed, 2),
                "iteration": iteration,
            }
            ctx["executions"].append(exec_record)

            print(f"[*] Exit code: {exit_code} ({elapsed:.1f}s)")

            return Verdict(
                kind=VerdictKind.PASS,
                output=ctx,
                diagnosis=f"命令执行完成, exit_code={exit_code}, {elapsed:.1f}s",
                confidence=1.0,
            )

        except subprocess.TimeoutExpired:
            ctx["executions"].append({"cmd": cmd, "error": "timeout", "iteration": iteration})
            return Verdict(kind=VerdictKind.FAIL, output=ctx,
                           diagnosis=f"命令超时 (300s): {cmd}", confidence=1.0)
        except Exception as e:
            ctx["executions"].append({"cmd": cmd, "error": str(e), "iteration": iteration})
            return Verdict(kind=VerdictKind.FAIL, output=ctx,
                           diagnosis=f"命令执行异常: {e}", confidence=1.0)


# ═══════════════════════════════════════════════════════════════════════════════
# 4. OutputAnalyzer — 分析输出是否支持声称（SOFT/LLM）
# ═══════════════════════════════════════════════════════════════════════════════

class OutputAnalyzerRouter(Router):
    FORMAT_IN = "sw_verify.execution"
    FORMAT_OUT = "sw_verify.analysis"
    DESCRIPTION = "分析命令输出 vs 声称: 返回 CONFIRMED / REFUTED / UNCERTAIN"

    def __init__(self, *, model: str | None = None):
        self._model = model

    def _make_client(self):
        from omnicompany.runtime.llm.llm import LLMClient
        return LLMClient(role="runtime_main", max_tokens=2048,
                         **({"model": self._model} if self._model else {}))

    def run(self, input_data: Any) -> Verdict:
        ctx = input_data
        claim = ctx.get("claim", "")
        expect_pattern = ctx.get("expect_pattern", "")

        # 获取最新一次执行结果
        if not ctx.get("executions"):
            return Verdict(kind=VerdictKind.FAIL, output=ctx, diagnosis="无执行记录")

        latest = ctx["executions"][-1]
        exit_code = latest.get("exit_code", -1)
        stdout = latest.get("stdout", "")
        stderr = latest.get("stderr", "")
        combined = f"{stdout}\n{stderr}".strip()

        # HARD 判定层：exit_code + pattern 匹配
        exit_ok = (exit_code == 0)
        pattern_match = False
        if expect_pattern:
            try:
                pattern_match = bool(re.search(expect_pattern, combined, re.IGNORECASE))
            except re.error:
                pattern_match = expect_pattern.lower() in combined.lower()

        # 三态判定
        if exit_ok and pattern_match:
            verdict_str = "CONFIRMED"
            confidence = 0.95
        elif not exit_ok and not pattern_match:
            verdict_str = "REFUTED"
            confidence = 0.95
        else:
            # 混合信号：exit_code 与 pattern 不一致 → LLM 判定
            verdict_str = "UNCERTAIN"
            confidence = 0.5

            # 如果有 LLM 可用，进行深度分析
            try:
                client = self._make_client()
                prompt = f"""分析以下验证结果：

声称: {claim}
预期模式: {expect_pattern}
退出码: {exit_code}
输出:
```
{combined[:5000]}
```

矛盾点: exit_code={'0 (成功)' if exit_ok else f'{exit_code} (失败)'}，但模式匹配{'成功' if pattern_match else '失败'}。

请判断声称是否成立。输出 JSON:
```json
{{"verdict": "CONFIRMED 或 REFUTED", "reason": "判定理由"}}
```"""
                resp = client.call(messages=[{"role": "user", "content": prompt}])
                text = resp.content[0].text
                match = re.search(r'```json\n(.*?)```', text, re.DOTALL)
                if match:
                    result = json.loads(match.group(1))
                    verdict_str = result.get("verdict", "UNCERTAIN")
                    confidence = 0.8
            except Exception as e:
                logger.debug("LLM 判定失败，保留 UNCERTAIN: %s", e)

        analysis = {
            "verdict": verdict_str,
            "exit_code": exit_code,
            "exit_ok": exit_ok,
            "pattern_match": pattern_match,
            "patterns_checked": expect_pattern,
            "confidence": confidence,
        }
        ctx["analyses"].append(analysis)

        # 判定结果映射到 Verdict
        if verdict_str == "CONFIRMED":
            kind = VerdictKind.PASS
        elif verdict_str == "REFUTED":
            kind = VerdictKind.FAIL
        else:
            kind = VerdictKind.PARTIAL

        return Verdict(
            kind=kind,
            output=ctx,
            diagnosis=f"{verdict_str}: exit={exit_code}, pattern={pattern_match}",
            confidence=confidence,
        )


# ═══════════════════════════════════════════════════════════════════════════════
# 5. SupplementalDesigner — 设计补充验证（SOFT/LLM）
# ═══════════════════════════════════════════════════════════════════════════════

class SupplementalDesignerRouter(Router):
    FORMAT_IN = "sw_verify.analysis"
    FORMAT_OUT = "sw_verify.verify-context"
    DESCRIPTION = "UNCERTAIN 时设计补充验证命令"

    def __init__(self, *, model: str | None = None):
        self._model = model

    def _make_client(self):
        from omnicompany.runtime.llm.llm import LLMClient
        return LLMClient(role="runtime_main", max_tokens=2048,
                         **({"model": self._model} if self._model else {}))

    def run(self, input_data: Any) -> Verdict:
        ctx = input_data
        claim = ctx.get("claim", "")
        original_cmd = ctx.get("verify_cmd", "")
        executions = ctx.get("executions", [])

        # 避免无限循环
        if ctx.get("iteration", 0) >= 3:
            return Verdict(
                kind=VerdictKind.FAIL,
                output=ctx,
                diagnosis="已超过最大补充验证轮次 (3), 判定为无法确认",
            )

        # 用 LLM 设计补充验证
        exec_summary = json.dumps(executions[-2:], indent=2, ensure_ascii=False) if executions else "无"

        try:
            client = self._make_client()
            prompt = f"""验证声称 "{claim}" 的命令 "{original_cmd}" 结果不确定。

已有执行记录:
{exec_summary}

请设计一个**补充验证命令**来确定声称是否成立。
这个命令应该从不同角度验证同一事实。

输出 JSON:
```json
{{"cmd": "补充验证命令", "expect_pattern": "预期模式", "reason": "为什么用这个命令"}}
```"""
            resp = client.call(messages=[{"role": "user", "content": prompt}])
            text = resp.content[0].text
            match = re.search(r'```json\n(.*?)```', text, re.DOTALL)

            if match:
                supp = json.loads(match.group(1))
                ctx["supplementals"].append(supp)
                ctx["iteration"] = ctx.get("iteration", 0) + 1

                return Verdict(
                    kind=VerdictKind.PASS,
                    output=ctx,
                    diagnosis=f"设计补充验证: {supp['cmd']}",
                    confidence=0.7,
                )
        except Exception as e:
            logger.debug("补充验证设计失败: %s", e)

        # Fallback: 简单的重运行
        ctx["supplementals"].append({
            "cmd": original_cmd,
            "expect_pattern": ctx.get("expect_pattern", ""),
            "reason": "LLM设计失败，重新执行原命令",
        })
        ctx["iteration"] = ctx.get("iteration", 0) + 1

        return Verdict(
            kind=VerdictKind.PASS,
            output=ctx,
            diagnosis="Fallback: 重运行原命令",
            confidence=0.5,
        )


# ═══════════════════════════════════════════════════════════════════════════════
# 6. ReportEmitter — 汇总最终报告（确定性）
# ═══════════════════════════════════════════════════════════════════════════════

class ReportEmitterRouter(Router):
    FORMAT_IN = "sw_verify.analysis"
    FORMAT_OUT = "sw_verify.report"
    DESCRIPTION = "汇总所有证据，生成最终验证报告"

    def run(self, input_data: Any) -> Verdict:
        ctx = input_data
        claim = ctx.get("claim", "")
        verify_cmd = ctx.get("verify_cmd", "")
        executions = ctx.get("executions", [])
        analyses = ctx.get("analyses", [])

        # 最终判定 = 最后一次 analysis 的 verdict
        final_analysis = analyses[-1] if analyses else {}
        verified = final_analysis.get("verdict") == "CONFIRMED"
        final_verdict = final_analysis.get("verdict", "UNKNOWN")

        report_lines = [
            f"{'═' * 55}",
            "🔍 VERIFICATION REPORT",
            f"{'═' * 55}",
            "",
            f"声称 (Claim): {claim}",
            f"验证命令: {verify_cmd}",
            f"验证轮次: {len(executions)}",
            "",
        ]

        for i, (exec_rec, analysis) in enumerate(zip(executions, analyses)):
            report_lines.extend([
                f"── 轮次 {i + 1} ──",
                f"命令: {exec_rec.get('cmd', '?')}",
                f"Exit Code: {exec_rec.get('exit_code', '?')} ({'✅' if exec_rec.get('exit_code') == 0 else '❌'})",
                f"Pattern Match: {'✅' if analysis.get('pattern_match') else '❌'}",
                f"判定: {analysis.get('verdict', '?')}",
                "",
            ])

        report_lines.extend([
            "── 最终结论 ──",
            f"{'✅' if verified else '❌'} {final_verdict}: \"{claim}\"",
        ])

        if verified:
            report_lines.append("   声称得到 evidence-based 证实")
        else:
            report_lines.append("   声称未通过验证")
            for a in analyses:
                if a.get("verdict") != "CONFIRMED":
                    report_lines.append(f"   理由: exit_code={a.get('exit_code')}, pattern={a.get('pattern_match')}")
                    break

        # 输出摘要
        latest_exec = executions[-1] if executions else {}
        output_preview = (latest_exec.get("stdout", "") + "\n" + latest_exec.get("stderr", "")).strip()
        if output_preview:
            report_lines.extend([
                "",
                "── 最后输出摘要 ──",
                output_preview[:2000],
            ])

        report_lines.append(f"{'═' * 55}")
        report = "\n".join(report_lines)

        print(f"\n\n{report}\n\n")

        return Verdict(
            kind=VerdictKind.PASS if verified else VerdictKind.FAIL,
            output={
                "verified": verified,
                "verdict": final_verdict,
                "claim": claim,
                "rounds": len(executions),
                "report": report,
            },
            diagnosis=f"{'✅ VERIFIED' if verified else '❌ NOT VERIFIED'}: {claim}",
            confidence=final_analysis.get("confidence", 0.5),
        )
