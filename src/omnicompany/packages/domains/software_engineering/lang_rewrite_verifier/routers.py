# [OMNI] origin=claude-code domain=software_engineering/lang_rewrite_verifier ts=2026-04-08T03:23:42Z
# [OMNI] migrated 2026-05-03: 旧 omnicompany.runtime.agent.agent_node_loop.AgentNodeLoop 已 deprecate, 现用 packages.services._core.agent.AgentNodeLoop (router 化新基础设施).
# [OMNI] material_id="material:domains.software_engineering.lang_rewrite_verifier.smoke_routers.implementation.py"
"""lang_rewrite_verifier.routers — 冒烟测试生成 + 执行

两个节点：

  SmokeTestGeneratorRouter (AgentNodeLoop)
    全局阅读翻译后的 Rust 项目，生成有针对性的冒烟测试套件。
    FORMAT_IN:  rewrite.verified-code
    FORMAT_OUT: smoke.test-suite

  SmokeRunnerRouter (HARD Router)
    顺序执行测试套件，第一个 fatal 失败即停并打包 debug.error-report。
    FORMAT_IN:  smoke.test-suite
    FORMAT_OUT: smoke.result (PASS) / debug.error-report (FAIL)
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
import subprocess
from pathlib import Path
from typing import Any

from omnicompany.protocol.anchor import Verdict, VerdictKind
from omnicompany.runtime.routing.router import Router
from omnicompany.packages.services._core.agent.routers.prompt_builder import PromptBuilderRouter
from omnicompany.packages.services._core.agent.routers.extract_result import ExtractResultRouter
from omnicompany.runtime.agent_runtime import run_readonly_structured_agent

DOMAIN = "smoke"


# ── SmokeTestGeneratorRouter ──────────────────────────────────────────────────


_NODE_PROMPT = """\
你是一个 Rust 项目测试专家。你收到的是一个刚从 Python 翻译到 Rust 的项目。
你的任务：分析项目结构和核心功能，生成一套从简到繁的冒烟测试用例。

【工作步骤】
1. 读取 Cargo.toml：确认 crate 类型（[[bin]] 还是 [lib]）、依赖、features
2. 读取 src/main.rs 或 src/lib.rs：了解入口逻辑和公开接口
3. 用 glob 浏览 src/ 下所有 .rs 文件，选择关键文件深入阅读
4. 理解核心数据流：输入是什么，经过什么处理，输出是什么
5. 思考：哪些路径是"能跑起来"的最小验证集？

【输出格式】
通过 finish 工具输出以下 JSON（message 字段）：

```json
{
  "work_dir": "<项目根目录绝对路径>",
  "compile_command": "cargo build",
  "test_cases": [
    {
      "id": "build",
      "description": "编译通过",
      "cmd": "cargo build",
      "success_pattern": "Finished",
      "timeout_secs": 120,
      "fatal": true
    },
    {
      "id": "smoke_run",
      "description": "基础运行：调用 finish",
      "cmd": "cargo run -- \"<符合项目实际用法的参数>\"",
      "success_pattern": "<期望出现在 stdout/stderr 中的字符串>",
      "timeout_secs": 60,
      "fatal": true
    }
  ]
}
```

【规则】
- build 用例永远第一，fatal=true
- 如果是 lib crate（Cargo.toml 无 [[bin]]），不生成 cargo run 用例，改用 cargo test
- cmd 必须是可在 work_dir 下直接执行的完整 shell 命令
- success_pattern 要基于你对代码的实际理解，不要用"OK"这种通用词
- 用例数 3-7 个，宁少勿多，聚焦核心路径
- fatal=false 表示此用例失败不阻断后续用例（用于非关键路径）
"""


class _SmokeTestPromptBuilder(PromptBuilderRouter):
    """冒烟测试生成 agent 自定义首条 user 消息 (拼 work_dir / source_file / target_lang)."""

    def build_initial_messages(self, input_data: dict) -> list[dict]:
        work_dir = input_data.get("work_dir", "")
        rs_dir = input_data.get("rs_dir", work_dir)
        source_file = input_data.get("source_file", "")
        target_lang = input_data.get("target_lang", "rust")

        lines = []
        project_path = rs_dir or work_dir
        if project_path:
            lines.append(f"翻译完成的 Rust 项目位于: {project_path}")
        if source_file:
            lines.append(f"原始 Python 源文件: {source_file}")
        if target_lang and target_lang != "rust":
            lines.append(f"目标语言: {target_lang}")
        lines.append("")
        lines.append("请探查该项目，理解其核心功能，生成冒烟测试套件。")
        lines.append("完成后用 finish 工具输出 JSON 格式的测试套件。")

        return [{"role": "user", "content": "\n".join(lines)}]


class _SmokeTestExtractResult(ExtractResultRouter):
    """冒烟测试 agent 自定义产物提取 — 剥 markdown fence + parse JSON, 校验 test_cases 非空."""

    def extract(
        self,
        *,
        final_text: str,
        messages: list[dict],
        turn_count: int,
        stop_reason: str,
    ) -> Verdict:
        text = (final_text or "").strip()

        # 剥离 markdown 代码围栏
        if "```" in text:
            for part in text.split("```"):
                stripped = part.strip()
                if stripped.startswith("json"):
                    text = stripped[4:].strip()
                    break
                elif stripped.startswith("{"):
                    text = stripped
                    break

        try:
            suite = json.loads(text)
        except Exception as e:
            return Verdict(
                kind=VerdictKind.FAIL,
                output={"error": f"测试套件 JSON 解析失败: {e}", "raw": text[:500]},
                diagnosis=f"SmokeTestGenerator 输出无法解析为 JSON: {e}",
            )

        test_cases = suite.get("test_cases", [])
        if not test_cases:
            return Verdict(
                kind=VerdictKind.FAIL,
                output={"error": "test_cases 为空"},
                diagnosis="SmokeTestGenerator 未生成任何测试用例",
            )

        return Verdict(
            kind=VerdictKind.PASS,
            output={
                "work_dir": suite.get("work_dir", ""),
                "compile_command": suite.get("compile_command", "cargo build"),
                "test_cases": test_cases,
            },
            diagnosis=f"生成 {len(test_cases)} 个冒烟测试用例",
        )


StructuredSmokeRunner = Callable[..., Awaitable[dict[str, Any]]]
_SMOKE_RESULT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["work_dir", "compile_command", "test_cases"],
    "properties": {
        "work_dir": {"type": "string", "minLength": 1},
        "compile_command": {"type": "string", "const": "cargo build"},
        "test_cases": {
            "type": "array",
            "minItems": 3,
            "maxItems": 7,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["id", "description", "cmd", "success_pattern", "timeout_secs", "fatal"],
                "properties": {
                    "id": {"type": "string", "minLength": 1, "maxLength": 80},
                    "description": {"type": "string", "minLength": 5, "maxLength": 300},
                    "cmd": {
                        "type": "string",
                        "minLength": 5,
                        "maxLength": 500,
                        "pattern": "^cargo [^;&|<>]+$",
                    },
                    "success_pattern": {"type": "string", "minLength": 1, "maxLength": 300},
                    "timeout_secs": {"type": "integer", "minimum": 1, "maximum": 300},
                    "fatal": {"type": "boolean"},
                },
            },
        },
    },
}


def _rust_project_snapshot(work_dir: Path) -> str:
    paths = [work_dir / "Cargo.toml"]
    paths.extend(sorted((work_dir / "src").rglob("*.rs"))[:40])
    sections: list[str] = []
    total = 0
    for path in paths:
        if not path.is_file():
            continue
        raw = path.read_text(encoding="utf-8", errors="replace")
        remaining = 20000 - total
        if remaining <= 0:
            break
        raw = raw[:remaining]
        total += len(raw)
        sections.extend([f"## FILE: {path.relative_to(work_dir).as_posix()}", raw])
    return "\n\n".join(sections)


class SmokeTestGeneratorRouter(Router):
    """Read a translated Rust project through AgentRuntime and return a test plan."""

    FORMAT_IN = "rewrite.verified-code"
    FORMAT_OUT = f"{DOMAIN}.test-suite"
    DESCRIPTION = "外置 AgentRuntime 只读分析 Rust 项目，Omni 校验 cargo 冒烟测试计划"

    def __init__(
        self,
        *,
        model: str | None = None,
        bus: Any | None = None,
        report_runner: StructuredSmokeRunner = run_readonly_structured_agent,
    ) -> None:
        self._bus = bus
        self._model = model or "deepseek-v4-flash"
        self._report_runner = report_runner

    async def run(self, input_data: Any) -> Verdict:
        if not isinstance(input_data, dict):
            return Verdict(kind=VerdictKind.FAIL, output={}, diagnosis="input_data must be an object")
        work_dir = Path(str(input_data.get("rs_dir") or input_data.get("work_dir") or "")).resolve()
        if not work_dir.is_dir() or not (work_dir / "Cargo.toml").is_file():
            return Verdict(kind=VerdictKind.FAIL, output={}, diagnosis="Rust work_dir or Cargo.toml is missing")
        task = (
            f"Rust project root: {work_dir}\n"
            f"Original source: {input_data.get('source_file') or '(not supplied)'}\n"
            "Return 3-7 ordered cargo smoke tests from the authoritative snapshot below. "
            "The first case must be id=build, cmd='cargo build', fatal=true. Do not execute commands.\n\n"
            "Every cmd must be one foreground cargo command and must not contain redirects, pipes, "
            "shell chaining, backgrounding, curl, sleep, pkill, or environment assignments.\n\n"
            + _rust_project_snapshot(work_dir)
        )
        try:
            response = await self._report_runner(
                task=task,
                instructions=_NODE_PROMPT,
                result_schema=_SMOKE_RESULT_SCHEMA,
                workspace_cwd=work_dir,
                caller="lang-rewrite-verifier.smoke-test-generator",
                model=self._model,
                allowed_tools=(),
                max_steps=8,
                max_output_tokens=4096,
                timeout_seconds=180,
            )
        except Exception as error:  # noqa: BLE001
            return Verdict(kind=VerdictKind.FAIL, output={}, diagnosis=f"external AgentRuntime failed: {type(error).__name__}: {error}")
        runtime = response.get("runtime", {})
        suite = response.get("final")
        if not response.get("ok") or not isinstance(suite, dict):
            return Verdict(kind=VerdictKind.FAIL, output={"agent_runtime": runtime}, diagnosis=str(response.get("error") or "missing smoke test suite"))
        cases = suite.get("test_cases")
        issues: list[str] = []
        if Path(str(suite.get("work_dir") or "")).resolve() != work_dir:
            issues.append("work_dir changed")
        if not isinstance(cases, list) or not 3 <= len(cases) <= 7:
            issues.append("test_cases count must be 3-7")
            cases = []
        ids: set[str] = set()
        for index, case in enumerate(cases):
            if not isinstance(case, dict):
                issues.append(f"case {index} is not an object")
                continue
            case_id = str(case.get("id") or "")
            if not case_id or case_id in ids:
                issues.append(f"case {index} id is blank or duplicated")
            ids.add(case_id)
            command = str(case.get("cmd") or "")
            if not command.startswith("cargo ") or any(token in command for token in (";", "&&", "||", "|", ">", "<")):
                issues.append(
                    f"case {case_id or index} has unsafe/non-cargo command: {command!r}"
                )
        if cases and (cases[0].get("id") != "build" or cases[0].get("cmd") != "cargo build" or cases[0].get("fatal") is not True):
            issues.append("first case must be fatal cargo build")
        if issues:
            return Verdict(kind=VerdictKind.FAIL, output={"issues": issues, "agent_runtime": runtime}, diagnosis=f"smoke suite rejected: {issues[0]}")
        return Verdict(
            kind=VerdictKind.PASS,
            output={"work_dir": str(work_dir), "compile_command": "cargo build", "test_cases": cases, "agent_runtime": runtime},
            diagnosis=f"generated {len(cases)} validated smoke tests",
        )


# ── SmokeRunnerRouter ─────────────────────────────────────────────────────────


class SmokeRunnerRouter(Router):
    """HARD：顺序执行冒烟测试用例。

    遇到第一个 fatal=true 的失败用例即停止，将失败信息打包成
    debug.error-report 格式（含 language="rust"、source_files、
    compile_command）交给 debugger 管线处理。

    全部通过 → PASS（管线 EMIT）。
    """

    FORMAT_IN = f"{DOMAIN}.test-suite"
    FORMAT_OUT = f"{DOMAIN}.result"
    DESCRIPTION = "顺序运行冒烟测试，失败时打包 debug.error-report 进入 debugger"

    def run(self, input_data: dict) -> Verdict:
        work_dir: str = input_data.get("work_dir", "")
        test_cases: list[dict] = input_data.get("test_cases", [])
        compile_command: str = input_data.get("compile_command", "cargo build")

        if not test_cases:
            return Verdict(
                kind=VerdictKind.FAIL,
                output={**input_data, "error": "test_cases 为空"},
                diagnosis="没有测试用例可运行",
            )

        cwd = Path(work_dir) if work_dir else None

        # 预先读取所有源文件，供 debug.error-report 使用
        source_files: dict[str, str] = {}
        if cwd and cwd.is_dir():
            for rs_file in sorted(cwd.glob("src/**/*.rs")):
                try:
                    rel = str(rs_file.relative_to(cwd))
                    source_files[rel] = rs_file.read_text(encoding="utf-8", errors="replace")
                except Exception:
                    pass
            for extra in ("Cargo.toml", "Cargo.lock"):
                p = cwd / extra
                if p.exists():
                    try:
                        source_files[extra] = p.read_text(encoding="utf-8", errors="replace")
                    except Exception:
                        pass

        passed_cases: list[str] = []

        for case in test_cases:
            case_id: str = case.get("id", "unknown")
            cmd: str = case.get("cmd", "")
            success_pattern: str = case.get("success_pattern", "")
            timeout_secs: int = int(case.get("timeout_secs", 60))
            fatal: bool = bool(case.get("fatal", True))
            description: str = case.get("description", cmd)

            if not cmd:
                continue

            output = ""
            success = False
            try:
                result = subprocess.run(
                    cmd,
                    shell=True,
                    capture_output=True,
                    text=True,
                    timeout=timeout_secs,
                    cwd=str(cwd) if cwd else None,
                    encoding="utf-8",
                    errors="replace",
                )
                output = (result.stdout or "") + (result.stderr or "")
                success = result.returncode == 0
                if success and success_pattern:
                    success = success_pattern in output
            except subprocess.TimeoutExpired:
                output = f"[TIMEOUT after {timeout_secs}s]"
            except Exception as e:
                output = str(e)

            if success:
                passed_cases.append(case_id)
            elif fatal:
                # 打包 debug.error-report，透传 work_dir / compile_command
                # 让 TesterRouter 用同一条命令来验证修复是否生效
                return Verdict(
                    kind=VerdictKind.FAIL,
                    output={
                        # 透传上游字段（去掉 test_cases，避免混淆）
                        **{k: v for k, v in input_data.items() if k != "test_cases"},
                        # debug.error-report 必要字段
                        "language": "rust",
                        "error_text": output[:4000],
                        "compile_command": cmd,          # TesterRouter 用此命令验证
                        "work_dir": str(cwd) if cwd else "",
                        "source_files": source_files,
                        # 额外上下文
                        "failed_case_id": case_id,
                        "failed_case_description": description,
                        "passed_cases": passed_cases,
                        "all_test_cases": test_cases,
                    },
                    diagnosis=f"冒烟测试 [{case_id}] {description} 失败: {output[:200]}",
                )
            # fatal=False：记录失败但继续

        return Verdict(
            kind=VerdictKind.PASS,
            output={
                **input_data,
                "passed_cases": [c.get("id") for c in test_cases],
                "smoke_passed": True,
            },
            diagnosis=f"全部 {len(test_cases)} 个冒烟测试通过",
        )
