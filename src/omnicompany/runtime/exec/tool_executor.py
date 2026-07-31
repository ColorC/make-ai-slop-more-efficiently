# [OMNI] origin=claude-code domain=omnicompany/runtime ts=2026-04-08T03:23:43Z
# [OMNI] material_id="material:runtime.exec.tool_dispatcher.execution_engine.py"
"""工具执行器 — OpenHands Runtime 执行层的 LAP 简化版

按 tool_name 分发执行，返回字符串结果。
每个执行器对应一个 Anthropic tool_use 工具。
"""

from __future__ import annotations

import logging
import os
import re
import subprocess
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_IS_WINDOWS = os.name == "nt"


class ToolExecutor:
    """统一工具执行器

    维护 undo 备份，按 tool_name 分发到具体实现。

    SWE-bench 模式（container_id 非 None）：
        bash 命令通过 `docker exec {container_id}` 转发到任务容器内执行。
        str_replace_editor 的路径映射到容器内路径（/repo/...）。
        register_tool 写入持久化 Format Registry（跨任务积累）。
    """

    MAX_TIMEOUT = 300  # 5 分钟硬上限, 任何 bash 命令不得超过此时长

    def __init__(
        self,
        timeout: int = 30,
        container_id: str | None = None,
        task_id: str | None = None,
        registry_path: str | None = None,
        route_graph: Any = None,
        origin: str = "claude-code",
        agent_name: str = "",
        domain: str = "",
    ):
        self.timeout = min(timeout, self.MAX_TIMEOUT)
        self._file_backups: dict[str, str] = {}  # path → last content (for undo)

        # SWE-bench 模式配置
        self.container_id = container_id          # Docker 容器 ID，非 None 时进入 SWE-bench 模式
        self.task_id = task_id                    # 当前任务 ID（用于 Format Registry 记录）
        self._route_graph = route_graph           # for CH2 semantic type registration

        # 写入身份（用于 guarded_write origin / domain / agent_name）
        # 默认 claude-code 适配外部 LLM agent；OmniCompany 内部代码（如 ReflectorRouter）
        # 应用"internal-engine"或本服务的正式身份，避免伪造 origin 绕 shield。
        self.origin = origin
        self.agent_name = agent_name
        self.domain = domain

        # Format Registry（跨任务持久化）
        if container_id is not None:
            from omnicompany.runtime.storage.tool_pattern_registry import PersistentFormatRegistry
            from pathlib import Path
            rp = Path(registry_path) if registry_path else None
            self._registry = PersistentFormatRegistry(rp or "data/format_registry.json")
        else:
            self._registry = None

    def execute(
        self,
        tool_name: str,
        tool_args: dict[str, Any],
        intent: dict[str, Any] | None = None,
    ) -> str:
        """按 tool_name 分发执行，返回结果字符串。

        Death Zone 前置拦截：命中禁区规则时直接返回阻断信息，不实际执行。
        intent 参数由调用方（ToolRouter）透传，用于 Death Zone 规则的上下文判断。
        """
        blocked = self._check_death_zones(tool_name, tool_args, intent)
        if blocked is not None:
            return blocked

        if tool_name in ("bash", "shell"):
            return self.execute_shell(tool_args.get("command", ""))
        elif tool_name == "str_replace_editor":
            return self.execute_editor(tool_args)
        elif tool_name == "think":
            return self.execute_think(tool_args.get("thought", ""))
        elif tool_name == "finish":
            return self.execute_finish(tool_args.get("message", ""))
        elif tool_name == "glob":
            return self.execute_glob(tool_args)
        elif tool_name == "grep":
            return self.execute_grep(tool_args)
        elif tool_name == "register_tool":
            return self.execute_register_tool(tool_args)
        elif tool_name == "register_semantic_types":
            return self.execute_register_semantic_types(tool_args)
        else:
            return f"Error: Unknown tool '{tool_name}'"

    def _check_death_zones(
        self,
        tool_name: str,
        tool_args: dict[str, Any],
        intent: dict[str, Any] | None = None,
    ) -> str | None:
        """Death Zone 前置拦截。命中时返回阻断消息，否则返回 None。"""
        from omnicompany.runtime.signals.pain_system import check_death_zones

        rule = check_death_zones(tool_name, tool_args, intent)
        if rule is None:
            return None

        import logging
        logging.getLogger(__name__).warning(
            "[DEATH ZONE] Blocked tool=%s rule=%s: %s",
            tool_name, rule.rule_id, rule.description,
        )

        if rule.action == "block":
            return (
                f"[DEATH ZONE BLOCKED] Operation denied by safety rule "
                f"'{rule.rule_id}': {rule.description}. "
                f"This action is permanently forbidden and cannot be overridden."
            )
        return None

    # -- shell (跨平台, Windows 上优先 Git Bash) --

    _shell_cmd: list[str] | None = None  # 缓存可用的 shell 命令

    @classmethod
    def _detect_shell(cls) -> tuple[list[str], str]:
        """检测 Windows 可用 shell, 优先 Git Bash (兼容 Linux 语法)"""
        if not _IS_WINDOWS:
            return ["/bin/bash", "-c"], "bash"

        # 1. Git Bash (完整 Linux 兼容: cat, heredoc, grep 等)
        git_bash_paths = [
            r"C:\Program Files\Git\bin\bash.exe",
            r"C:\Program Files (x86)\Git\bin\bash.exe",
            os.path.expandvars(r"%LOCALAPPDATA%\Programs\Git\bin\bash.exe"),
        ]
        for p in git_bash_paths:
            if os.path.exists(p):
                return [p, "-c"], "git-bash"

        # 2. PowerShell (比 cmd 好, 支持部分 Unix 别名)
        return ["powershell.exe", "-NoProfile", "-Command"], "powershell"

    @staticmethod
    def _kill_tree(pid: int) -> None:
        """杀掉进程树（Windows: taskkill /T, Unix: killpg）"""
        try:
            if _IS_WINDOWS:
                subprocess.run(
                    ["taskkill", "/PID", str(pid), "/T", "/F"],
                    capture_output=True, timeout=10,
                )
            else:
                import signal
                os.killpg(os.getpgid(pid), signal.SIGKILL)
        except Exception:
            pass

    def _get_shell(self) -> list[str]:
        """获取 shell 命令 (带缓存)"""
        if ToolExecutor._shell_cmd is None:
            ToolExecutor._shell_cmd, shell_name = self._detect_shell()
            import logging
            logging.getLogger(__name__).info("Shell detected: %s → %s", shell_name, ToolExecutor._shell_cmd[0])
        return ToolExecutor._shell_cmd

    def execute_register_tool(self, args: dict[str, Any]) -> str:
        """注册可复用工具到跨任务持久化 Format Registry（LAP Format 注册）

        这是与 Live-SWE-agent 的核心差异：
          Live-SWE-agent 的工具在任务结束后消失。
          register_tool 让工具跨任务存活，复用率成为进化信号。

        Args:
            tool_id:     唯一标识（snake_case）
            name:        人类可读名称
            description: 语义描述（适合什么场景，解决什么问题）
            script:      脚本内容或命令模板
        """
        if self._registry is None:
            return "register_tool is only available in SWE-bench mode (container_id required)."
        tool_id = args.get("tool_id", "").strip()
        name = args.get("name", tool_id).strip()
        description = args.get("description", "").strip()
        script = args.get("script", "").strip()
        if not tool_id or not description or not script:
            return "Error: register_tool requires tool_id, description, and script."
        result = self._registry.register(tool_id, name, description, script, self.task_id)
        summary = self._registry.summary()
        return f"{result}\n\n{summary}"

    def execute_register_semantic_types(self, args: dict[str, Any]) -> str:
        """CH2 梳理渠道：agent 主动批量注册领域语义类型。"""
        domain = args.get("domain", "").strip()
        types_list = args.get("types", [])
        if not domain or not types_list:
            return "Error: register_semantic_types requires 'domain' and 'types' (non-empty array)."

        if self._route_graph is None:
            return "Error: route_graph not available for semantic type registration."

        registered = []
        errors = []
        for item in types_list:
            type_id = item.get("type_id", "").strip()
            description = item.get("description", "").strip()
            if not type_id or not description:
                errors.append(f"Skipped item missing type_id or description: {item}")
                continue

            try:
                self._route_graph.upsert_semantic_type(
                    type_id=type_id,
                    description=description,
                    keywords=item.get("keywords", []),
                    handler_guidance=item.get("handler_guidance", ""),
                    exemplars=[f"Registered by agent for domain '{domain}'"],
                    source_channel="CH2_cataloging",
                )
                registered.append(type_id)
            except Exception as e:
                errors.append(f"Failed to register {type_id}: {e}")

        result_parts = [f"Domain: {domain}", f"Registered: {len(registered)} types"]
        if registered:
            result_parts.append("Types: " + ", ".join(registered))
        if errors:
            result_parts.append("Errors: " + "; ".join(errors))
        return "\n".join(result_parts)

    _FORBIDDEN_CMD_PATTERN = None

    @classmethod
    def _get_forbidden_pattern(cls):
        if cls._FORBIDDEN_CMD_PATTERN is None:
            import re
            cls._FORBIDDEN_CMD_PATTERN = re.compile(r'\bfind\b|\bhead\b', re.IGNORECASE)
        return cls._FORBIDDEN_CMD_PATTERN

    def execute_shell(self, command: str) -> str:
        """执行 shell 命令，返回 stdout+stderr

        普通模式: 在 host 上执行（Windows/Linux）
        SWE-bench 模式（container_id 非 None）: 通过 docker exec 转发到任务容器内

        Windows: 优先 Git Bash > PowerShell > cmd.exe
        Linux/WSL: /bin/bash
        """
        if not command:
            return "[returncode: 0]"

        if self._get_forbidden_pattern().search(command):
            logger.warning("BLOCKED forbidden command (find/head): %s", command[:80])
            return (
                "[returncode: -1]\n"
                "DEATH ZONE: find/head commands are FORBIDDEN. "
                "They spawn massive process trees on Windows. "
                "Use ls, cat, grep, sed, or python instead. "
                "This violation has been recorded as a pain signal."
            )

        # S3e.2 (2026-04-08): 软检测明显的文件写模式,经 archmap 判定后
        # 返回教学式错误,引导 LLM 换 str_replace_editor 工具而不是硬堵。
        # 本检测故意不求完备(shell 写文件的方式太多,完备等于自欺欺人),
        # 只抓 >/>>/tee/cp/mv/rm 等大头,命中才检查。
        gate_err = _bash_write_path_check(command)
        if gate_err:
            return gate_err

        # Wave 1: 安全分级（学自 Claude Code 的逻辑：分级比二元拦截更灵活）
        risk = BashSecurity.classify(command)
        if risk == "critical":
            logger.warning("CRITICAL command blocked: %s", command[:80])
            return (
                "[returncode: -1]\n"
                "BLOCKED: Critical-risk command detected. "
                "This command could cause irreversible damage and is permanently forbidden."
            )
        risk_prefix = ""
        if risk == "high":
            risk_prefix = "[WARNING: high-risk command] "

        # SWE-bench 模式：通过 docker exec 在任务容器内执行
        if self.container_id:
            return self._execute_in_container(command)

        # 普通模式：在 host 上执行（用 Popen 确保 Windows 进程树也能杀掉）
        try:
            shell_cmd = self._get_shell()
            kwargs: dict[str, Any] = dict(
                text=True, encoding="utf-8", errors="replace",
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            )
            if _IS_WINDOWS:
                kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
            proc = subprocess.Popen(shell_cmd + [command], **kwargs)
            try:
                stdout, stderr = proc.communicate(timeout=self.timeout)
            except subprocess.TimeoutExpired:
                self._kill_tree(proc.pid)
                proc.kill()
                try:
                    proc.communicate(timeout=5)
                except Exception:
                    pass
                return f"[returncode: -1]\nCommand timed out after {self.timeout}s"
            output = stdout or ""
            if stderr:
                output += f"\n{stderr}" if output else stderr

            # Wave 1: 输出截断（学自 Claude Code 的逻辑：保头保尾防止超长结果浪费 token）
            output = self._truncate_output(output)
            if risk_prefix:
                output = risk_prefix + output

            return f"[returncode: {proc.returncode}]\n{output}" if output else f"[returncode: {proc.returncode}]"
        except Exception as e:
            return f"[returncode: -1]\n{e}"

    def _execute_in_container(self, command: str) -> str:
        """在 SWE-bench 任务 Docker 容器内执行命令"""
        try:
            result = subprocess.run(
                ["docker", "exec", self.container_id, "/bin/bash", "-c", command],
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=self.timeout,
                capture_output=True,
            )
            output = result.stdout
            if result.stderr:
                output += f"\n{result.stderr}" if output else result.stderr
            return f"[returncode: {result.returncode}]\n{output}" if output else f"[returncode: {result.returncode}]"
        except subprocess.TimeoutExpired:
            return f"[returncode: -1]\nCommand timed out after {self.timeout}s (container: {self.container_id})"
        except Exception as e:
            return f"[returncode: -1]\n{e}"

    # -- str_replace_editor --

    def execute_editor(self, args: dict[str, Any]) -> str:
        """文件编辑器分发"""
        command = args.get("command", "")
        path = args.get("path", "")

        if not path:
            return "Error: 'path' is required."

        if command == "view":
            return self._editor_view(path, args.get("view_range"))
        elif command == "create":
            return self._editor_create(path, args.get("file_text", ""))
        elif command == "str_replace":
            return self._editor_str_replace(path, args.get("old_str", ""), args.get("new_str", ""))
        elif command == "insert":
            return self._editor_insert(path, args.get("insert_line"), args.get("new_str", ""))
        elif command == "undo_edit":
            return self._editor_undo(path)
        else:
            return f"Error: Unknown editor command '{command}'. Use: view, create, str_replace, insert, undo_edit."

    # -- 容器内文件操作辅助 --

    def _container_read(self, path: str) -> tuple[str | None, str]:
        """从容器内读取文件。返回 (content, error)"""
        r = subprocess.run(
            ["docker", "exec", self.container_id, "cat", path],
            capture_output=True, text=True, timeout=self.timeout,
        )
        if r.returncode != 0:
            return None, f"Error: File '{path}' does not exist."
        return r.stdout, ""

    def _container_write(self, path: str, content: str) -> str:
        """向容器内写入文件。返回错误信息或空串。

        使用 docker cp 而非 tee（通过 stdin pipe），避免 Windows 上
        subprocess stdin → docker exec -i 的编码/pipe 关闭时序问题。
        """
        import tempfile
        try:
            # 写入临时文件，docker cp 到容器
            with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", suffix=".tmp", delete=False
            ) as f:
                f.write(content)
                tmp_path = f.name
            try:
                r = subprocess.run(
                    ["docker", "cp", tmp_path, f"{self.container_id}:{path}"],
                    capture_output=True, text=True, timeout=self.timeout,
                )
                if r.returncode != 0:
                    return f"Error writing '{path}': {r.stderr}"
            finally:
                # OMNI-013 ALLOW: tempfile cleanup, 路径在 OS tmp 不在仓库内
                os.remove(tmp_path)

            # 写入后验证：从容器重新读取，确认内容一致
            verify_content, verify_err = self._container_read(path)
            if verify_content is None:
                return f"Error verifying write to '{path}': {verify_err}"
            if verify_content != content:
                # 诊断信息：帮助定位写入不一致的根因
                return (
                    f"Error: write verification failed for '{path}'. "
                    f"Expected {len(content)} chars, got {len(verify_content)} chars. "
                    f"First diff at char {next((i for i,(a,b) in enumerate(zip(content, verify_content)) if a!=b), min(len(content),len(verify_content)))}."
                )
            return ""
        except subprocess.TimeoutExpired:
            return f"Error writing '{path}': timed out after {self.timeout}s"
        except Exception as e:
            return f"Error writing '{path}': {e}"

    def _container_exists(self, path: str) -> bool:
        """检查容器内文件是否存在"""
        r = subprocess.run(
            ["docker", "exec", self.container_id, "test", "-e", path],
            capture_output=True, timeout=self.timeout,
        )
        return r.returncode == 0

    def _editor_view(self, path: str, view_range: list[int] | None = None) -> str:
        """查看文件 (带行号) 或目录"""
        if self.container_id:
            return self._editor_view_container(path, view_range)

        p = Path(path)

        if p.is_dir():
            return self._list_directory(p)

        if not p.exists():
            return f"Error: File '{path}' does not exist.{self._hint_parent_dir(path)}"

        try:
            content = p.read_text(encoding="utf-8", errors="replace")
        except Exception as e:
            return f"Error reading '{path}': {e}"

        return self._format_view(path, content, view_range)

    def _editor_view_container(self, path: str, view_range: list[int] | None = None) -> str:
        """容器内 view"""
        content, err = self._container_read(path)
        if content is None:
            return err
        return self._format_view(path, content, view_range)

    def _format_view(self, path: str, content: str, view_range: list[int] | None = None) -> str:
        """格式化文件内容为带行号的输出"""
        lines = content.split("\n")

        # Normalize view_range: LLM may pass it as a JSON string like "[1, 50]"
        if isinstance(view_range, str):
            import json as _json
            try:
                view_range = _json.loads(view_range)
            except Exception:
                view_range = None

        if view_range:
            start = max(1, int(view_range[0]))
            end = int(view_range[1]) if len(view_range) > 1 else len(lines)
            end = min(end, len(lines))
            selected = lines[start - 1 : end]
            numbered = [f"{i:6d}\t{line}" for i, line in enumerate(selected, start=start)]
        else:
            numbered = [f"{i:6d}\t{line}" for i, line in enumerate(lines, start=1)]

        header = f"Here's the content of {path}:\n"
        return header + "\n".join(numbered)

    def _list_directory(self, p: Path, max_depth: int = 2) -> str:
        """列出目录内容 (最多 2 层)"""
        result_lines = []

        def _walk(current: Path, depth: int, prefix: str = ""):
            if depth > max_depth:
                return
            try:
                entries = sorted(current.iterdir(), key=lambda x: (not x.is_dir(), x.name))
            except PermissionError:
                result_lines.append(f"{prefix}[Permission denied]")
                return

            for entry in entries:
                if entry.name.startswith("."):
                    continue
                if entry.is_dir():
                    result_lines.append(f"{prefix}{entry.name}/")
                    _walk(entry, depth + 1, prefix + "  ")
                else:
                    result_lines.append(f"{prefix}{entry.name}")

        result_lines.append(f"Contents of {p}:")
        _walk(p, 1)
        return "\n".join(result_lines)

    def _editor_create(self, path: str, file_text: str) -> str:
        """创建新文件"""
        if self.container_id:
            if self._container_exists(path):
                return f"Error: File '{path}' already exists. Use str_replace to edit existing files."
            err = self._container_write(path, file_text)
            if err:
                return err
            return f"File created successfully at: {path}"

        p = Path(path)
        if p.exists():
            return f"Error: File '{path}' already exists. Use str_replace to edit existing files."
        try:
            # OmniGuardian: 走统一写入入口，自动 Shield 审计 + OmniMark 贴头
            from omnicompany.core.guarded_write import write_file
            write_file(
                path, file_text,
                origin=self.origin, agent_name=self.agent_name, domain=self.domain,
                purpose="str_replace_editor create",
            )
            return f"File created successfully at: {path}"
        except Exception as e:
            return f"Error creating '{path}': {e}"

    def _editor_str_replace(self, path: str, old_str: str, new_str: str) -> str:
        """精确字符串替换"""
        if not old_str:
            return "Error: 'old_str' must not be empty."

        # 读取文件（容器或本地）
        if self.container_id:
            content, err = self._container_read(path)
            if content is None:
                return err
        else:
            p = Path(path)
            if not p.exists():
                return f"Error: File '{path}' does not exist.{self._hint_parent_dir(path)}"
            try:
                content = p.read_text(encoding="utf-8", errors="replace")
            except Exception as e:
                return f"Error reading '{path}': {e}"

        count = content.count(old_str)
        if count == 0:
            # Aider-inspired fuzzy hint: use difflib.SequenceMatcher to find the closest
            # matching block in the file, so the agent can see what to match against
            # without a separate file-read round trip.
            import difflib
            lines = content.splitlines()
            old_lines = old_str.splitlines()
            total_lines = len(lines)
            hint = ""

            if old_lines:
                # Slide a window the same size as old_str across the file;
                # find the window with the highest SequenceMatcher ratio.
                window = len(old_lines)
                best_ratio = 0.0
                best_idx = -1
                for i in range(max(1, total_lines - window + 1)):
                    chunk = lines[i : i + window]
                    ratio = difflib.SequenceMatcher(
                        None, "\n".join(old_lines), "\n".join(chunk), autojunk=False
                    ).ratio()
                    if ratio > best_ratio:
                        best_ratio = ratio
                        best_idx = i

                if best_idx >= 0 and best_ratio >= 0.4:
                    # Show ±3 lines of context around the best match
                    ctx_start = max(0, best_idx - 3)
                    ctx_end = min(total_lines, best_idx + window + 3)
                    snippet = "\n".join(
                        f"{ctx_start + j + 1}: {l}"
                        for j, l in enumerate(lines[ctx_start:ctx_end])
                    )
                    view_start = ctx_start + 1
                    view_end = ctx_end
                    hint = (
                        f"\n\nBest match (similarity={best_ratio:.0%}) around line {best_idx + 1}:\n"
                        f"```\n{snippet}\n```\n"
                        f"Tip: Read that region precisely with "
                        f'`{{"command": "view", "path": "{path}", "view_range": [{view_start}, {view_end}]}}`'
                        f", then retry str_replace with the exact text shown."
                    )

            return (
                f"Error: 'old_str' not found in '{path}' ({total_lines} lines). "
                f"Make sure it matches exactly, including whitespace and indentation.{hint}"
            )
        if count > 1:
            return f"Error: 'old_str' found {count} times in '{path}'. Include more context lines to make it unique."

        # 备份 & 替换
        self._file_backups[path] = content
        new_content = content.replace(old_str, new_str, 1)

        # 写入（容器或本地）
        if self.container_id:
            err = self._container_write(path, new_content)
            if err:
                return err
        else:
            try:
                # OmniGuardian: str_replace 也走统一写入入口
                from omnicompany.core.guarded_write import write_file, ShieldViolation
                write_file(
                    path, new_content,
                    origin=self.origin, agent_name=self.agent_name, domain=self.domain,
                    purpose="str_replace_editor str_replace",
                    overwrite_stamp=False,
                )
            except ShieldViolation as e:
                return f"Error: OmniGuardian blocked write to '{path}': {e}"
            except Exception as e:
                return f"Error writing '{path}': {e}"

        # 显示编辑结果
        replacement_line = new_content.find(new_str)
        if replacement_line >= 0:
            line_num = new_content[:replacement_line].count("\n") + 1
            return f"The file {path} has been edited. Here's the result of the edit around line {line_num}:\n{self._snippet(new_content, line_num)}"
        return f"The file {path} has been edited successfully."

    def _editor_insert(self, path: str, insert_line: int | None, new_str: str) -> str:
        """在指定行后插入"""
        if insert_line is None:
            return "Error: 'insert_line' is required for insert command."
        try:
            insert_line = int(insert_line)
        except (TypeError, ValueError):
            return f"Error: 'insert_line' must be an integer, got {type(insert_line).__name__}: {insert_line!r}"

        # 读取文件
        if self.container_id:
            content, err = self._container_read(path)
            if content is None:
                return err
        else:
            p = Path(path)
            if not p.exists():
                return f"Error: File '{path}' does not exist.{self._hint_parent_dir(path)}"
            try:
                content = p.read_text(encoding="utf-8", errors="replace")
            except Exception as e:
                return f"Error reading '{path}': {e}"

        lines = content.split("\n")

        if insert_line < 0 or insert_line > len(lines):
            return f"Error: insert_line {insert_line} is out of range [0, {len(lines)}]."

        # 备份 & 插入
        self._file_backups[path] = content
        new_lines = new_str.split("\n")
        lines[insert_line:insert_line] = new_lines
        new_content = "\n".join(lines)

        if self.container_id:
            err = self._container_write(path, new_content)
            if err:
                return err
        else:
            try:
                # OmniGuardian: insert 也走统一写入入口
                from omnicompany.core.guarded_write import write_file, ShieldViolation
                write_file(
                    path, new_content,
                    origin=self.origin, agent_name=self.agent_name, domain=self.domain,
                    purpose="str_replace_editor insert",
                    overwrite_stamp=False,
                )
            except ShieldViolation as e:
                return f"Error: OmniGuardian blocked write to '{path}': {e}"
            except Exception as e:
                return f"Error writing '{path}': {e}"

        return f"The file {path} has been edited. Here's the result around line {insert_line + 1}:\n{self._snippet(new_content, insert_line + 1)}"

    def _editor_undo(self, path: str) -> str:
        """恢复上次编辑"""
        if path not in self._file_backups:
            return f"Error: No edit history for '{path}'."

        backup = self._file_backups.pop(path)
        try:
            # OmniGuardian: undo 也走统一写入入口（等价于一次 edit）
            from omnicompany.core.guarded_write import write_file, ShieldViolation
            write_file(
                path, backup,
                origin=self.origin, agent_name=self.agent_name, domain=self.domain,
                purpose="str_replace_editor undo_edit",
                overwrite_stamp=False,
            )
            return f"Last edit to {path} undone successfully."
        except ShieldViolation as e:
            return f"Error: OmniGuardian blocked undo to '{path}': {e}"
        except Exception as e:
            return f"Error restoring '{path}': {e}"

    def _snippet(self, content: str, center_line: int, context: int = 4) -> str:
        """获取文件片段 (带行号)"""
        lines = content.split("\n")
        start = max(1, center_line - context)
        end = min(len(lines), center_line + context)
        numbered = [f"{i:6d}\t{lines[i - 1]}" for i in range(start, end + 1)]
        return "\n".join(numbered)

    def _hint_parent_dir(self, path: str) -> str:
        """当文件不存在时，列出父目录内容作为提示。"""
        try:
            parent = Path(path).parent
            if parent.is_dir():
                entries = sorted(parent.iterdir(), key=lambda x: x.name)[:20]
                names = [e.name + ("/" if e.is_dir() else "") for e in entries]
                return f" Parent directory '{parent}' contains: {', '.join(names) or '(empty)'}."
        except Exception:
            pass
        return ""

    # -- think --

    def execute_think(self, thought: str) -> str:
        """记录思考，返回确认"""
        return "[INTERNAL REASONING RECORDED — this is NOT task output, continue executing with real tools]"

    # -- finish --

    def execute_finish(self, message: str) -> str:
        """返回完成消息 (由 LLMRouter 特殊处理)"""
        return message

    # -- glob / grep (Wave 1, upgraded 2026-04-18 per agent_tools.md) --

    # 默认 head_limit（可被 args 覆盖）
    DEFAULT_GLOB_HEAD_LIMIT = 100
    DEFAULT_GREP_HEAD_LIMIT = 250
    MAX_GLOB_RESULTS = 100   # 向后兼容保留
    MAX_GREP_RESULTS = 50    # 向后兼容保留（被 head_limit 覆盖）

    # BD.6d 对齐 CC: ripgrep 相关常量
    # 来源: 参考项目/claude-code-analysis/src/utils/ripgrep.ts L80
    MAX_BUFFER_SIZE = 20_000_000   # 20MB，大 monorepos 有 200K+ 文件
    # CC 的 WSL 特例：60s，其他 20s。我们默认 30s 折中；env 可覆盖
    _DEFAULT_RG_TIMEOUT_SEC = 30

    # VCS 目录排除（ripgrep --glob '!xxx'）
    _VCS_EXCLUDE = (".git", ".svn", ".hg", ".bzr", ".jj", ".sl")

    # ripgrep 二进制查找缓存
    _RG_BINARY_CACHED: str | None = None

    @classmethod
    def _get_rg_timeout(cls) -> int:
        """从 env 读 ripgrep 超时（对齐 CC CLAUDE_CODE_GLOB_TIMEOUT_SECONDS）。"""
        env = os.environ.get("CLAUDE_CODE_GLOB_TIMEOUT_SECONDS", "")
        if env.isdigit():
            v = int(env)
            if v > 0:
                return v
        return cls._DEFAULT_RG_TIMEOUT_SEC

    @staticmethod
    def _is_eagain_stderr(stderr: str) -> bool:
        """CC isEagainError 对齐: ripgrep spawn 太多线程导致的 EAGAIN，
        应该用 -j 1 单线程 retry。Docker/CI 资源受限时常见。"""
        if not stderr:
            return False
        return "os error 11" in stderr or "Resource temporarily unavailable" in stderr

    @classmethod
    def _find_rg_binary(cls) -> str:
        """查找独立 rg.exe 二进制（跨 Windows / POSIX）。

        Windows 下 `which rg` 可能命中 Claude Code 注入的 shell function
        而非独立二进制，Python 子进程会 FileNotFound。所以必须显式查候选路径。
        """
        if cls._RG_BINARY_CACHED is not None:
            return cls._RG_BINARY_CACHED

        import shutil as _shutil

        env_path = os.environ.get("OMNI_RG_PATH", "").strip()
        if env_path and Path(env_path).is_file():
            cls._RG_BINARY_CACHED = env_path
            return env_path

        candidates = [
            _shutil.which("rg"),
            _shutil.which("rg.exe"),
            # @vscode/ripgrep (bundled with Antigravity / Cursor / VS Code)
            r"c:/Users/user/AppData/Local/Programs/Antigravity/resources/app/node_modules/@vscode/ripgrep/bin/rg.exe",
            r"c:/Users/user/AppData/Local/Programs/cursor/resources/app/node_modules/@vscode/ripgrep/bin/rg.exe",
            r"c:/Users/user/.gemini/tmp/bin/rg.exe",
            r"c:/Users/user/AppData/Local/Programs/Microsoft VS Code/560a9dba96/resources/app/extensions/copilot/node_modules/@github/copilot/sdk/ripgrep/bin/win32-x64/rg.exe",
        ]
        for c in candidates:
            if c and Path(c).is_file():
                cls._RG_BINARY_CACHED = str(c)
                return str(c)
        cls._RG_BINARY_CACHED = "rg"
        return "rg"

    def execute_glob(self, args: dict[str, Any]) -> str:
        """按 glob 模式搜索文件路径（优先用 ripgrep --files --glob，fallback Python rglob）。

        Args:
            pattern (required): glob 模式（`**/*.lua` / `pbui_activity_*.prefab`）
            path   (optional but strongly recommended): 搜索根，缺省 cwd
            head_limit (optional): 结果上限，默认 100
        """
        pattern = args.get("pattern", "")
        root = args.get("path", os.getcwd())
        head_limit = int(args.get("head_limit", self.DEFAULT_GLOB_HEAD_LIMIT))

        if not pattern:
            return "Error: 'pattern' is required."

        root_path = Path(root)
        if not root_path.is_dir():
            return f"Error: '{root}' is not a directory."

        # 优先 ripgrep
        rg_result = self._glob_ripgrep(pattern, root, head_limit)
        if rg_result is not None:
            return rg_result

        # Fallback: Python rglob
        try:
            matches: list[dict[str, Any]] = []
            for p in root_path.rglob(pattern):
                if p.is_file():
                    try:
                        stat = p.stat()
                        matches.append({
                            "path": str(p),
                            "size": stat.st_size,
                            "mtime": time.strftime("%Y-%m-%d %H:%M", time.localtime(stat.st_mtime)),
                        })
                    except OSError:
                        matches.append({"path": str(p), "size": -1, "mtime": "?"})
                if len(matches) >= head_limit:
                    break

            if not matches:
                return f"No files matching '{pattern}' in '{root}'."

            lines = [f"Found {len(matches)} file(s) matching '{pattern}':"]
            for m in matches:
                size_str = f"{m['size']:,}B" if m['size'] >= 0 else "?"
                lines.append(f"  {m['path']}  ({size_str}, {m['mtime']})")
            if len(matches) >= head_limit:
                lines.append(f"  ... (truncated at head_limit={head_limit})")
            return "\n".join(lines)
        except Exception as e:
            return f"Error during glob search: {e}"

    def _glob_ripgrep(self, pattern: str, root: str, head_limit: int) -> str | None:
        """用 ripgrep --files --glob 实现 glob。None 表示不可用需 fallback。"""
        rg = self._find_rg_binary()
        try:
            rg_args = [rg, "--files", "--hidden"]
            for vcs in self._VCS_EXCLUDE:
                rg_args.extend(["--glob", f"!{vcs}"])
            rg_args.extend(["--glob", pattern])
            rg_args.append(root)
            result = subprocess.run(
                rg_args, capture_output=True, text=True,
                encoding="utf-8", errors="replace", timeout=20,
            )
            if result.returncode not in (0, 1):
                return None  # rg error, fallback
            lines = [l for l in (result.stdout or "").splitlines() if l.strip()]
            total = len(lines)
            truncated = False
            if total > head_limit:
                lines = lines[:head_limit]
                truncated = True
            if total == 0:
                return f"No files matching '{pattern}' in '{root}'."
            header = f"Found {total} file(s) matching '{pattern}'"
            if truncated:
                header += f" (showing first {head_limit}, pass head_limit=N to adjust)"
            return header + "\n" + "\n".join(f"  {l}" for l in lines)
        except FileNotFoundError:
            return None
        except subprocess.TimeoutExpired:
            return f"Error: glob timed out after 20s in '{root}'. Narrow 'path' to a smaller subtree."
        except Exception:
            return None

    def execute_grep(self, args: dict[str, Any]) -> str:
        """在文件内容中搜索正则（优先 ripgrep，fallback Python re）。

        Args (1:1 对齐 CC GrepTool schema):
            pattern (required): 正则表达式
            path (optional): 搜索根，缺省 cwd
            glob / include (optional): 文件过滤 glob（'*.lua' / '*.prefab'）—— include 旧名兼容
            type (optional): rg --type （'py'/'js'/'rust' 等）
            output_mode (optional): 'content' / 'files_with_matches' (CC 默认) / 'count'
            -A (optional, int): 匹配行**后** N 行（rg -A）
            -B (optional, int): 匹配行**前** N 行（rg -B）
            -C (optional, int): 匹配行**前后** N 行（alias context）
            context (optional, int): alias for -C
            -n (optional, bool): 显示行号，默认 true
            -i / case_insensitive (optional, bool): 忽略大小写
            multiline (optional, bool): -U --multiline-dotall
            head_limit (optional, int): 默认 250; 0 = unlimited
            offset (optional, int): 跳过前 N 行（分页）; 默认 0
        """
        pattern = args.get("pattern", "")
        path = args.get("path", os.getcwd())
        glob_filter = args.get("glob", "") or args.get("include", "")
        file_type = args.get("type", "")
        output_mode = args.get("output_mode", "files_with_matches")  # CC 默认
        # -A/-B/-C/context：-C 和 context 等价；-A/-B 独立
        a_lines = args.get("-A")
        b_lines = args.get("-B")
        c_lines = args.get("-C", args.get("context"))
        show_line_nums = args.get("-n")  # default None → True at build time
        if show_line_nums is None:
            show_line_nums = True
        case_i = bool(args.get("case_insensitive") or args.get("-i"))
        multiline = bool(args.get("multiline", False))
        head_limit_raw = args.get("head_limit", self.DEFAULT_GREP_HEAD_LIMIT)
        head_limit = int(head_limit_raw)  # 0 = unlimited (CC escape hatch)
        offset = int(args.get("offset", 0))

        if not pattern:
            return "Error: 'pattern' is required."

        rg_result = self._grep_ripgrep(
            pattern=pattern, path=path, glob_filter=glob_filter,
            file_type=file_type, output_mode=output_mode,
            a_lines=a_lines, b_lines=b_lines, c_lines=c_lines,
            show_line_nums=bool(show_line_nums),
            case_i=case_i, multiline=multiline,
            head_limit=head_limit, offset=offset,
        )
        if rg_result is not None:
            return rg_result
        return self._grep_python(pattern, path, glob_filter, max(head_limit, 1) if head_limit else 1000)

    def _grep_ripgrep(
        self, *, pattern: str, path: str, glob_filter: str, file_type: str,
        output_mode: str,
        a_lines, b_lines, c_lines,
        show_line_nums: bool, case_i: bool, multiline: bool,
        head_limit: int, offset: int,
    ) -> str | None:
        """用 ripgrep 搜索。返回 None 表示不可用需 fallback。
        对齐 CC ripgrep.ts: EAGAIN 单线程 retry / TimeoutError 带 partial / MAX_BUFFER 截断标记。"""
        rg = self._find_rg_binary()
        try:
            base_args = [rg, "--hidden", "--max-columns", "500"]
            for vcs in self._VCS_EXCLUDE:
                base_args.extend(["--glob", f"!{vcs}"])
            if case_i:
                base_args.append("-i")
            if multiline:
                base_args.extend(["-U", "--multiline-dotall"])
            if output_mode == "files_with_matches":
                base_args.append("-l")
            elif output_mode == "count":
                base_args.append("-c")
            else:  # content
                base_args.append("--no-heading")
                if show_line_nums:
                    base_args.append("-n")
                # 独立 -A / -B 优先，否则 -C / context
                if a_lines is not None:
                    base_args.extend(["-A", str(int(a_lines))])
                if b_lines is not None:
                    base_args.extend(["-B", str(int(b_lines))])
                if (a_lines is None and b_lines is None) and c_lines is not None:
                    base_args.extend(["-C", str(int(c_lines))])
            if glob_filter:
                for g in glob_filter.split():
                    base_args.extend(["--glob", g])
            if file_type:
                base_args.extend(["--type", file_type])
            # pattern 以 - 开头用 -e 包
            if pattern.startswith("-"):
                base_args.extend(["-e", pattern])
            else:
                base_args.append(pattern)
            base_args.append(path)

            timeout_sec = self._get_rg_timeout()

            # BD.6d: 首次尝试；如遇 EAGAIN，用 -j 1 单线程 retry（对齐 CC L126）
            def _run_rg(args: list[str]) -> tuple[int | None, str, str, bool]:
                """返回 (returncode, stdout, stderr, timed_out)。stdout/stderr 截到 MAX_BUFFER"""
                try:
                    proc = subprocess.run(
                        args, capture_output=True, text=True,
                        encoding="utf-8", errors="replace", timeout=timeout_sec,
                    )
                    so = proc.stdout or ""
                    se = proc.stderr or ""
                    # BD.6d MAX_BUFFER 截断标记（CC L152-155）
                    if len(so) > self.MAX_BUFFER_SIZE:
                        so = so[:self.MAX_BUFFER_SIZE]
                    if len(se) > self.MAX_BUFFER_SIZE:
                        se = se[:self.MAX_BUFFER_SIZE]
                    return proc.returncode, so, se, False
                except subprocess.TimeoutExpired as te:
                    # 带 partial results（对齐 CC RipgrepTimeoutError L98-106）
                    partial = (te.stdout or b"")
                    if isinstance(partial, bytes):
                        partial = partial.decode("utf-8", errors="replace")
                    return None, partial, str(te), True

            rc, stdout, stderr, timed_out = _run_rg(base_args)

            # EAGAIN 检测 + 单线程 retry（对齐 CC isEagainError + -j 1）
            if rc is not None and rc != 0 and self._is_eagain_stderr(stderr):
                logger.warning(
                    "[ripgrep] EAGAIN detected in '%s', retrying with -j 1",
                    path,
                )
                rc, stdout, stderr, timed_out = _run_rg([base_args[0]] + ["-j", "1"] + base_args[1:])

            if timed_out:
                partial_lines = [l for l in stdout.splitlines() if l.strip()][:50]
                partial_preview = ""
                if partial_lines:
                    partial_preview = (
                        "\nPartial results before timeout (first 50):\n"
                        + "\n".join(f"  {l}" for l in partial_lines)
                    )
                return (
                    f"[TOOL_TIMEOUT] ripgrep timed out after {timeout_sec}s in '{path}'.\n"
                    f"Narrow 'path' to a smaller subtree, add 'type' filter, "
                    f"or set CLAUDE_CODE_GLOB_TIMEOUT_SECONDS env." + partial_preview
                )

            if rc == 2:
                # rg real error, fallback to Python impl
                return None
            # EAGAIN 即使 retry 后仍失败
            if rc is not None and rc != 0 and rc != 1 and self._is_eagain_stderr(stderr):
                return (
                    f"[TOOL_ERROR] ripgrep failed with EAGAIN after -j 1 retry in '{path}'. "
                    f"System resource constrained (too many threads). Try narrower 'path'."
                )

            lines = [l for l in stdout.splitlines() if l.strip()]
            total = len(lines)
            # offset + head_limit (0 = unlimited per CC escape hatch)
            if offset > 0:
                lines = lines[offset:]
            truncated = False
            if head_limit > 0 and len(lines) > head_limit:
                lines = lines[:head_limit]
                truncated = True
            if total == 0:
                return f"No matches for '{pattern}' in '{path}'" + (f" glob={glob_filter}" if glob_filter else "") + "."
            noun = "lines" if output_mode == "content" else ("files" if output_mode == "files_with_matches" else "entries")
            header = f"Found {total} {noun} for '{pattern}'"
            if offset > 0:
                header += f" (offset={offset})"
            if truncated:
                # CC-style: tell LLM it can paginate via offset/head_limit
                header += f" (showing {head_limit} after offset={offset}; pass offset+head_limit for next page or head_limit=0 for unlimited)"
            return header + ":\n" + "\n".join(f"  {l}" for l in lines)
        except FileNotFoundError:
            return None
        except Exception as exc:
            logger.warning("[ripgrep] unexpected error in '%s': %s", path, exc)
            return None

    def _grep_python(self, pattern: str, path: str, glob_filter: str, head_limit: int) -> str:
        """纯 Python 正则搜索 fallback"""
        try:
            regex = re.compile(pattern)
        except re.error as e:
            return f"Error: invalid regex '{pattern}': {e}"

        target = Path(path)
        matches = []

        # 单文件
        if target.is_file():
            matches = self._grep_file(target, regex)
        elif target.is_dir():
            # 遍历目录
            glob_pattern = glob_filter or "*"
            for fp in target.rglob(glob_pattern):
                if fp.is_file() and fp.stat().st_size < 1_000_000:  # 跳过 >1MB 的文件
                    matches.extend(self._grep_file(fp, regex))
                    if len(matches) >= head_limit:
                        break
        else:
            return f"Error: '{path}' is not a file or directory."

        if not matches:
            return f"No matches for '{pattern}' in '{path}'."

        total = len(matches)
        matches = matches[:head_limit]
        output = [f"Found {total} match(es) for '{pattern}'"]
        if total > head_limit:
            output[0] += f" (showing first {head_limit})"
        output[0] += ":"
        output.extend(f"  {m}" for m in matches)

        return "\n".join(output)

    @staticmethod
    def _grep_file(file_path: Path, regex: re.Pattern) -> list[str]:
        """在单个文件中搜索正则匹配"""
        results = []
        try:
            text = file_path.read_text(encoding="utf-8", errors="replace")
            for i, line in enumerate(text.split("\n"), 1):
                if regex.search(line):
                    results.append(f"{file_path}:{i}: {line.rstrip()[:200]}")
        except Exception:
            pass
        return results

    # -- output truncation (Wave 1) --

    MAX_OUTPUT_CHARS = 30_000

    def _truncate_output(self, output: str) -> str:
        """截断过长输出：保头保尾（学自 Claude Code 的逻辑）"""
        if len(output) <= self.MAX_OUTPUT_CHARS:
            return output
        half = self.MAX_OUTPUT_CHARS // 2
        return (
            output[:half]
            + f"\n\n[... {len(output) - self.MAX_OUTPUT_CHARS:,} chars truncated ...]\n\n"
            + output[-half:]
        )


# ── Wave 1: Bash 安全分级（学自 Claude Code 的逻辑：4级分类比二元死区灵活）────

class BashSecurity:
    """命令安全分级器 — critical/high/medium/low 四级分类"""

    _CRITICAL = [
        r"rm\s+-rf\s+/(?!tmp)",          # rm -rf / (但允许 /tmp)
        r"mkfs\.",                         # 格式化磁盘
        r"dd\s+if=.+of=/dev/",            # 磁盘覆写
        r":\s*\(\s*\)\s*\{.*:\s*\|\s*:\s*&\s*\}\s*;\s*:",  # fork bomb (各种空格变体)
        r">(?: |)\s*/dev/sd[a-z]",        # 覆写磁盘设备
    ]

    _HIGH = [
        r"rm\s+-rf",                       # 递归删除
        r"chmod\s+-R\s+777",              # 全开权限
        r"curl\s+.+\|\s*(?:ba)?sh",       # 管道执行远程脚本
        r"wget\s+.+\|\s*(?:ba)?sh",
        r"eval\s+\$\(",                  # eval 注入
        r"git\s+push\s+--force",          # 强制推送
        r"git\s+reset\s+--hard",          # 硬重置
    ]

    _MEDIUM = [
        r"(?:apt|yum|brew)\s+(?:install|remove)",
        r"pip\s+install(?:\s+--user)?",
        r"npm\s+install\s+-g",
    ]

    _compiled: dict[str, list[re.Pattern]] | None = None

    @classmethod
    def _compile(cls):
        if cls._compiled is None:
            cls._compiled = {
                "critical": [re.compile(p) for p in cls._CRITICAL],
                "high":     [re.compile(p) for p in cls._HIGH],
                "medium":   [re.compile(p) for p in cls._MEDIUM],
            }

    @classmethod
    def classify(cls, command: str) -> str:
        """返回 'critical' | 'high' | 'medium' | 'low'"""
        cls._compile()
        assert cls._compiled is not None
        for level in ("critical", "high", "medium"):
            for pattern in cls._compiled[level]:
                if pattern.search(command):
                    return level
        return "low"


# ─── S3e.2 bash 写路径软门禁 ──────────────────────────────
#
# 设计原则(用户明说): 堵不如疏, 不追求完备。
#   - 只抓常见写路径大头(>, >>, tee, cp, mv, rm, touch)
#   - 抓到后调 archmap is_writable 判断
#   - 命中禁区 → 返回教学式 tool error,告诉 LLM 换 str_replace_editor
#   - 不抓的 shell 变体(Python -c, xxd, printf, dd, 管道 base64 解码...) 不管
#   - 故意保留其他通道,避免 LLM 被逼到"绕过"情绪而爆炸式尝试

# 识别"命令 + 目标路径"对的 regex 列表
# 每条正则必须有一个命名组 (?P<target>...) 指向真实目标路径
_BASH_WRITE_PATTERNS = [
    # 重定向写: cmd > file / cmd >> file / cmd 1> file / cmd 2> file
    # 注意 >= 和 >& 不是重定向写,要先排除
    (
        "redirect-write",
        re.compile(r"""(?<![>&])>>?\s*(?P<target>[^\s&|;<>]+)"""),
    ),
    # tee file (tee -a file 也命中)
    (
        "tee",
        re.compile(r"""\btee\s+(?:-a\s+)?(?P<target>[^\s&|;<>]+)"""),
    ),
    # cp / mv / install 的 dst 通常是最后一个非 flag 参数,简化成 dst 在末尾
    (
        "cp-mv",
        re.compile(r"""\b(?:cp|mv|install)\s+(?:-[a-zA-Z]+\s+)*[^\s]+\s+(?P<target>[^\s&|;<>]+)"""),
    ),
    # rm / rmdir
    (
        "rm",
        re.compile(r"""\brm\s+(?:-[a-zA-Z]+\s+)*(?P<target>[^\s&|;<>]+)"""),
    ),
    # touch file
    (
        "touch",
        re.compile(r"""\btouch\s+(?P<target>[^\s&|;<>]+)"""),
    ),
    # curl -o file / wget -O file
    (
        "download",
        re.compile(r"""\b(?:curl|wget)\b[^|;&]*?\s-[oO]\s+(?P<target>[^\s&|;<>]+)"""),
    ),
]


def _bash_write_path_check(command: str) -> str:
    """扫一条 bash 命令的写路径大头,命中禁区返回教学式错误串。

    返回空字符串 '' 表示"软门禁未拦截,继续跑 command"。
    返回非空字符串表示"拦截,把这个串当 tool 输出返回给 LLM"。
    """
    # 先找所有可能的写目标
    hits: list[tuple[str, str]] = []   # [(pattern_name, target), ...]
    for name, pat in _BASH_WRITE_PATTERNS:
        for m in pat.finditer(command):
            tgt = m.group("target").strip("'\"")
            # 过滤掉管道符号 / 设备文件
            if not tgt or tgt.startswith("/dev/") or tgt in ("/dev/null",):
                continue
            hits.append((name, tgt))

    if not hits:
        return ""

    # 把每个目标走 archmap is_writable,任一不通过就拦
    try:
        from omnicompany.core.archmap import load_archmap
        archmap = load_archmap()
    except Exception:
        # archmap 不可用时不做这层软门禁,避免 fail-closed 打断正常 bash
        return ""

    for name, tgt in hits:
        # 相对路径按 CWD 算 — CWD 就是仓库根(omnicompany 默认启动 CWD)
        # 如果 tgt 是绝对路径 / 纯文件名,normalize 会处理
        try:
            from omnicompany.core.guarded_write import _normalize_to_repo_relative
            rel = _normalize_to_repo_relative(tgt)
        except Exception:
            continue
        # 不在仓库根下的目标(绝对路径到别的盘)不管
        if rel.startswith(("/", "C:", "D:", "E:", "F:", "c:", "d:", "e:", "f:")):
            continue
        check = archmap.is_writable(rel, "claude-code")
        if check.allowed:
            continue
        # 命中 — 返回教学式错误
        return (
            f"[returncode: -1]\n"
            f"OmniGuardian blocked a bash write to '{tgt}' (resolved to '{rel}').\n"
            f"Reason: {check.reason}\n"
            f"\n"
            f"不要用 bash 的 {name!r} 模式写入受保护路径。\n"
            f"建议换工具:\n"
            f"  - 写新文件 / 编辑文件 → 用 str_replace_editor (create/str_replace/insert)\n"
            f"  - 如果路径是 key file / human-only drawer, 你改不了, 请描述需求让 human 审阅\n"
            f"  - 如果是临时/缓存/调试输出,写到 .omni/tmp/ 或 logs/\n"
            f"\n"
            f"这条命令被拦是因为 OmniGuardian 的 drawer 权限, 不是 bash shell 本身的问题。\n"
            f"你不需要 '绕过'——换 str_replace_editor 就直接有权写。\n"
        )

    return ""
