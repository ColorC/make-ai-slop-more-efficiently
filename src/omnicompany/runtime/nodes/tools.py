# [OMNI] origin=claude-code domain=omnicompany/runtime ts=2026-04-08T03:23:44Z
# [OMNI] material_id="material:runtime.nodes.bash.editor.think.finish.tool_dispatch.execution.py"
"""工具节点 — bash / editor / think / finish

每个工具是图中的独立节点（Router）。
底座（ToolExecutor）只提供物理执行能力，不含语义分发。

bash 是 super node：可连接几乎一切外部真相。
"""

from __future__ import annotations

from typing import Any

from omnicompany.protocol.anchor import Verdict, VerdictKind
from omnicompany.runtime.routing.router import Router


class BashRouter(Router):
    """bash — super node，系统与外部世界的通用接口。

    可执行任意 shell 命令 → 连接几乎一切真相。
    """

    INPUT_KEYS = ["command"]

    def __init__(self, executor: "ToolExecutor"):
        self.executor = executor

    def run(self, input_data: Any) -> Verdict:
        command = ""
        if isinstance(input_data, dict):
            command = input_data.get("command", "")
        elif isinstance(input_data, str):
            command = input_data

        if not command:
            return Verdict(
                kind=VerdictKind.PASS,
                output={"result": "[no command]", "exit_code": 0},
            )

        result = self.executor.execute_shell(command)
        exit_code = 0
        if "[returncode:" in result:
            import re
            m = re.search(r"\[returncode:\s*(\d+)\]", result)
            if m:
                exit_code = int(m.group(1))

        return Verdict(
            kind=VerdictKind.PASS if exit_code == 0 else VerdictKind.FAIL,
            output={"result": result, "exit_code": exit_code, "command": command},
        )


class EditorRouter(Router):
    """str_replace_editor — 文件系统交互节点。"""

    INPUT_KEYS = ["command", "path"]

    def __init__(self, executor: "ToolExecutor"):
        self.executor = executor

    def run(self, input_data: Any) -> Verdict:
        if not isinstance(input_data, dict):
            return Verdict(kind=VerdictKind.FAIL, diagnosis="EditorRouter expects dict input")
        result = self.executor.execute_editor(input_data)
        is_error = result.startswith("Error") or result.startswith("[Error")
        return Verdict(
            kind=VerdictKind.FAIL if is_error else VerdictKind.PASS,
            output={"result": result},
        )


class ThinkRouter(Router):
    """think — 纯推理节点，无副作用但改变语义上下文。"""

    INPUT_KEYS = ["thought"]

    def run(self, input_data: Any) -> Verdict:
        thought = ""
        if isinstance(input_data, dict):
            thought = input_data.get("thought", "")
        elif isinstance(input_data, str):
            thought = input_data
        return Verdict(
            kind=VerdictKind.PASS,
            output={"thought": thought, "result": "[INTERNAL REASONING RECORDED — this is NOT task output, continue executing with real tools]"},
        )


class FinishRouter(Router):
    """finish — 终止信号节点。标记任务完成。"""

    INPUT_KEYS = ["message"]

    def run(self, input_data: Any) -> Verdict:
        message = ""
        if isinstance(input_data, dict):
            message = input_data.get("message", "")
        elif isinstance(input_data, str):
            message = input_data
        return Verdict(
            kind=VerdictKind.PASS,
            output={"message": message, "finished": True},
        )


class ToolDispatchRouter(Router):
    """工具分发节点 — 根据 tool_name 将请求路由到具体工具节点。

    这是从 ToolExecutor.execute() 的 if-else 中拆出的语义。
    在完全节点化的图中，此节点可被替代为图的分支边。
    但在过渡期仍然有用。
    """

    INPUT_KEYS = ["tool_name", "tool_args"]

    def __init__(self, executor: "ToolExecutor"):
        self.bash = BashRouter(executor)
        self.editor = EditorRouter(executor)
        self.think = ThinkRouter()
        self.finish = FinishRouter()
        self.executor = executor

    def run(self, input_data: Any) -> Verdict:
        if not isinstance(input_data, dict):
            return Verdict(kind=VerdictKind.FAIL, diagnosis="Expected dict with tool_name")

        tool_name = input_data.get("tool_name", "")
        tool_args = input_data.get("tool_args", {})

        if tool_name in ("bash", "shell"):
            return self.bash.run(tool_args)
        elif tool_name == "str_replace_editor":
            return self.editor.run(tool_args)
        elif tool_name == "think":
            return self.think.run(tool_args)
        elif tool_name == "finish":
            return self.finish.run(tool_args)
        else:
            return Verdict(
                kind=VerdictKind.FAIL,
                diagnosis=f"Unknown tool: {tool_name}",
                output={"result": f"Error: Unknown tool '{tool_name}'"},
            )


if __name__ != "__main__":
    from omnicompany.runtime.exec.tool_executor import ToolExecutor
