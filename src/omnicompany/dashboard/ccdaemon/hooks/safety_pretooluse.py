"""Fast, fail-closed PreToolUse guard for destructive shell commands.

This file deliberately has no project imports, filesystem access, subprocess calls,
or process inspection.  It is suitable for direct execution with ``python -I -S``.
The guard examines the hook event only; it never executes the proposed command.
It is a conservative syntax heuristic: generated or opaque scripts cannot be
proven safe, so recognized encoded/script wrappers are denied rather than treated
as complete inspection of arbitrary code.

What it refuses (2026-10-12): removing whole directory trees (``rm -r``,
``Remove-Item -Recurse``, ``rd /s``, ``del /s`` without a file pattern, .NET
``Delete(..., $true)``, ``shutil.rmtree``, node recursive rm, ``robocopy /MIR|/PURGE``,
``find -delete`` or a recursive listing piped into a delete without a name filter);
discarding uncommitted work (``git clean -f``, ``reset --hard``, whole-tree
``checkout``/``restore``, ``checkout -f``, ``git rm -f``, ``worktree remove --force``;
``p4 clean``, wide ``p4 revert``, ``p4 obliterate -y``); and stopping processes by
name, tree or several PIDs.  The same checks run inside ``ssh`` and ``igo ps`` /
``igo spawn`` payloads.  Deleting named files, wildcards included, is left alone.
Script files (``python x.py``) are not inspected; whole-tree deletions are routed
to the verified cleanup tool, which is such a script and does its own disk checks.
"""
from __future__ import annotations

import ast
import base64
import json
import ntpath
import re
import shlex
import sys
from typing import Any, Iterable

# ``-I -S`` intentionally ignores user site configuration, including
# PYTHONIOENCODING.  Hook reasons must remain readable on native Windows stdio.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError):
        pass


SHELL_TOOLS = frozenset(
    {
        "bash",
        "Bash",
        "shell",
        "Shell",
        "exec",
        "exec_command",
        "functions.exec",
        "functions.exec_command",
        "execute_bash",
        "executeBash",
        "run_command",
        "run_shell_command",
        "shell_command",
        "powershell",
        "PowerShell",
    }
)
COMMAND_KEYS = frozenset(
    {"command", "cmd", "shell", "script", "code", "input", "command_line", "commandLine"}
)
CONTAINER_KEYS = frozenset({"tool_input", "toolInput", "arguments", "args", "payload"})

_AGENTS = r"C:/workspace/AGENTS.md"
_ARCHIVE = r"C:/workspace/omnicompany\docs\standards\_global\directory_semantic_archive.md"
SOURCES = {
    "process": f"{_AGENTS} §“破坏性命令拦截”",
    "delete": f"{_AGENTS} §“破坏性命令拦截”“正式与临时目录绝不混用”；{_ARCHIVE} §“删除依据”",
    "discard": f"{_AGENTS} §“破坏性命令拦截”“worktree 完成即回收”",
    "p4": f"{_AGENTS} §“破坏性命令拦截”“demogame 配置产物 checkout 硬约束”",
    "opaque": f"{_AGENTS} §“破坏性命令拦截”",
    "input": f"本地 PreToolUse 输入边界与 {_AGENTS} §“阻止必须说明具体理由”",
}
PROCEED = {
    "process": "先核实精确 PID 及其业务用途，一次只结束一个 PID，不带 /T 进程树。",
    "delete": (
        "整棵目录改用 `omni cleanup rmtree <绝对路径>`（逐项核验链接、硬链接、Git 跟踪、占用进程后才删并写回执；"
        "这是规则指定的清理工具，不算绕过），它拒绝时按给出的原因处理；worktree 用 `omni cleanup worktree <路径>`"
        "（先保全改动与独有文件，再不带 --force 地 `git worktree remove`）；单个或按名单、按文件模式的文件可以直接删。目标归属不明时用 "
        "`omni guardian dirs explain <绝对路径>` 查目录语义档案，查不到的保留并标 needs-owner，"
        "已确认可删的其它目录照常清理，不要因一次被拦就放弃。"
    ),
    "discard": "先确认未提交、未跟踪内容已保全（提交到分支或归档 ref），再改用精确路径或不带强制选项的原生命令；"
               "收尾 worktree 用 `omni cleanup worktree <路径>`，它会先保全再原生移除。",
    "p4": "先 shelve 并逐文件核对，再按精确文件或 changelist 操作；可先加 -n 预览。",
    "opaque": "改成可静态核实的写法（字面量命令、可解析的内联代码）后重试。",
    "input": "缩小单次命令或包装内容后重试。",
}
_TARGET_BASE = ""
MAX_COMMAND_CHARS = 131_072
MAX_WRAPPER_SCAN_CHARS = 262_144
MAX_REPORTED_TARGETS = 256
MAX_DEPTH = 4

_DELETE_VERBS = frozenset({"rm", "remove-item", "ri", "rmdir", "rd", "del", "erase", "unlink"})
_PREFIX_WORDS = frozenset(
    {"&", "call", "do", "then", "else", "elif", "{", "(", "!", "time", "nohup", "exec", "command", "builtin", "noglob"}
)
_WIDE_PATHSPECS = frozenset({".", "./", ".\\", "*", ":/", ":/."})
_NAME_FILTERS = frozenset(
    {"-name", "-iname", "-path", "-ipath", "-regex", "-iregex", "-wholename", "-iwholename", "-filter", "-include"}
)
_DOTNET_TREE_DELETE = re.compile(
    r"\[(?:System\.)?IO\.Directory\]::Delete\s*\([^)]*,\s*\$true"
    r"|\.Delete\s*\(\s*\$true\s*\)"
    r"|FileSystem\]::DeleteDirectory\s*\(",
    re.I,
)
_NODE_RM_CALL = re.compile(r"\b(?:rm|rmdir|rmSync|rmdirSync)\s*\(")
_NODE_RECURSIVE = re.compile(r"recursive\s*:\s*(?:true|!0|1)\b")
_NODE_TREE_HELPERS = re.compile(r"\brimraf\b|\bremoveSync\s*\(|\bemptyDirSync\s*\(")
_NODE_CHILD = re.compile(r"\b(?:execSync|exec|spawnSync|spawn|execFileSync)\s*\(\s*(['\"`])(.*?)\1", re.S)
_PYTHON_EXE = re.compile(r"(?:python|pythonw)(?:\d+(?:\.\d+)?)?|py")


def _safe_text(value: Any) -> str:
    return value if isinstance(value, str) else ""


def _redact(value: str) -> str:
    """Return a complete target label without echoing credentials or a command."""
    value = value.strip().strip("'\"").replace("\r", " ").replace("\n", " ")
    value = re.sub(r"(?i)(https?://)([^/@\s]+):([^/@\s]+)@", r"\1<credentials>@", value)
    value = re.sub(r"(?i)([?&](?:token|password|passwd|secret|key)=)[^&\s]+", r"\1<redacted>", value)
    value = re.sub(r"(?i)(--?(?:token|password|passwd|secret|key)(?:=|\s+))\S+", r"\1<redacted>", value)
    return value or "<target not supplied>"


def _target_label(values: Iterable[str], *, paths: bool = False) -> str:
    targets = []
    for value in values:
        if not value:
            continue
        value = value.strip().strip("'\"")
        if (
            paths
            and _TARGET_BASE
            and value
            and not ntpath.isabs(value)
            and not value.startswith(("<", "/", "$", "%", "~", "{"))
        ):
            value = ntpath.normpath(ntpath.join(_TARGET_BASE, value))
        targets.append(_redact(value))
    if not targets:
        return "<dynamic or unknown target; absolute target was not supplied>"
    if len(targets) > MAX_REPORTED_TARGETS:
        return "<too many targets to report; input too large to determine a safe scope>"
    if len(targets) == 1:
        return targets[0]
    return ", ".join(targets)


def _deny(action: str, target: str, *, kind: str, detail: str = "") -> str:
    extra = f"{detail}。" if detail else ""
    return (
        f"PreToolUse 已拒绝：{action}；目标：{_redact(target)}。{extra}"
        f"规则来源：{SOURCES[kind]}。继续条件：{PROCEED[kind]}不得换工具或 shell 绕过此 hook。"
    )


def _split_shell(text: str) -> list[tuple[str, str]]:
    """Split shell statements/pipelines while respecting quotes and comments.

    Returns ``(operator_before, segment)`` pairs so a delete fed by a pipe can be
    judged together with the command that produced its input.
    """
    parts: list[tuple[str, str]] = []
    start = 0
    quote = ""
    escaped = False
    op = ""
    i = 0
    while i < len(text):
        char = text[i]
        if escaped:
            escaped = False
            i += 1
            continue
        if char == "\\" and quote != "'":
            escaped = True
            i += 1
            continue
        if quote:
            if char == quote:
                quote = ""
            i += 1
            continue
        if char in "'\"`":
            quote = char
            i += 1
            continue
        if char == "#" and (i == 0 or text[i - 1].isspace()):
            end = text.find("\n", i)
            if end < 0:
                end = len(text)
            parts.append((op, text[start:i]))
            op = ";"
            start = end + 1
            i = start
            continue
        if char in ";\n|&":
            parts.append((op, text[start:i]))
            if text[i : i + 2] in {"&&", "||"}:
                op = text[i : i + 2]
                i += 2
            else:
                op = char
                i += 1
            start = i
            continue
        i += 1
    parts.append((op, text[start:]))
    return [(o, part.strip()) for o, part in parts if part.strip()]


def _tokens(segment: str) -> list[str]:
    try:
        # posix=False preserves Windows backslashes in absolute targets.
        return [token.strip("'\"") for token in shlex.split(segment, posix=False, comments=False)]
    except ValueError:
        # An incomplete quote is itself ambiguous.  Keep enough tokens to detect
        # a dangerous executable, while avoiding a hook traceback.
        return re.findall(r"[^\s]+", segment)


def _basename(token: str) -> str:
    token = token.strip("'\"").replace("\\", "/")
    token = token.rsplit("/", 1)[-1].lower()
    for suffix in (".exe", ".cmd", ".bat"):
        if token.endswith(suffix):
            return token[: -len(suffix)]
    return token


def _strip_prefix(tokens: list[str]) -> list[str]:
    """Drop control keywords, env assignments and wrappers that only precede a command."""
    while tokens:
        head = tokens[0]
        stripped = head.lstrip("({")
        if stripped != head:
            tokens = ([stripped] if stripped else []) + tokens[1:]
            continue
        low = head.lower()
        if low in _PREFIX_WORDS:
            tokens = tokens[1:]
            continue
        if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*=\S*", head):
            tokens = tokens[1:]
            continue
        base = _basename(head)
        if base in {"timeout", "env", "nice", "stdbuf"}:
            tokens = tokens[1:]
            while tokens and tokens[0].startswith("-"):
                tokens = tokens[1:]
            if base == "timeout" and tokens and re.fullmatch(r"\d+(?:\.\d+)?[smhd]?", tokens[0]):
                tokens = tokens[1:]
            continue
        break
    return tokens


def _is_dynamic(value: str) -> bool:
    return bool(re.search(r"[$%{}*?]|\b(?:env|var|variable)\b", value, re.I))


def _option_values(args: list[str], names: frozenset[str]) -> tuple[list[str], bool]:
    values: list[str] = []
    malformed = False
    i = 0
    while i < len(args):
        raw = args[i]
        lower = raw.lower()
        matched = None
        for name in names:
            if lower == name or lower.startswith(name + ":") or lower.startswith(name + "="):
                matched = name
                break
        if matched is not None:
            suffix = raw[len(matched) :]
            if suffix.startswith(":") or suffix.startswith("="):
                values.append(suffix[1:])
            elif i + 1 < len(args) and not args[i + 1].startswith(("-", "/")):
                i += 1
                values.append(args[i])
            else:
                malformed = True
        i += 1
    return values, malformed


def _process_reason(tokens: list[str], executable: str) -> str | None:
    args = tokens[1:]
    if any(arg.lower() in {"-whatif", "-whatif:$true"} for arg in args):
        return None
    if executable in {"stop-process", "spps"}:
        names, missing_name = _option_values(args, frozenset({"-name", "-processname"}))
        if names or missing_name:
            return _deny("按进程名停止进程", _target_label(names), kind="process", detail="按名操作可能匹配多个进程")
        ids, missing_id = _option_values(args, frozenset({"-id", "-pid"}))
        if ids and len(ids) == 1 and not missing_id and ids[0].isdigit() and int(ids[0]) > 0:
            return None
        return _deny("停止进程", _target_label(ids), kind="process", detail="无法证明这是一个精确的单 PID 操作")

    if executable == "taskkill":
        images, _ = _option_values(args, frozenset({"/im", "-im"}))
        if images:
            return _deny("按映像名终止进程", _target_label(images), kind="process", detail="/IM 会按名称匹配一个或多个进程")
        pids, missing_pid = _option_values(args, frozenset({"/pid", "-pid"}))
        tree = any(arg.lower() in {"/t", "-t"} for arg in args)
        if pids and len(pids) == 1 and not missing_pid and pids[0].isdigit() and int(pids[0]) > 0 and not tree:
            return None
        return _deny("终止进程", _target_label(pids), kind="process", detail="只允许一个无进程树扩散的精确 PID")

    if executable in {"pkill", "killall"}:
        return _deny("按名称批量终止进程", _target_label(args), kind="process", detail="该命令的默认匹配范围不是单一 PID")

    # POSIX kill is allowed only for one literal positive PID.  Signals and
    # options are ignored; every remaining positional token must be that PID.
    positional = [arg for arg in args if not arg.startswith("-") and arg != "--"]
    numeric = [arg for arg in args if re.fullmatch(r"\d+", arg)]
    if len(numeric) == 1 and len(positional) == 1 and int(numeric[0]) > 0 and not _is_dynamic(numeric[0]):
        return None
    return _deny("终止进程", _target_label(numeric or args), kind="process", detail="只允许一个精确的正整数 PID")


def _rm_flag_is_recursive(arg: str) -> bool | None:
    """True/False for a recognised rm flag, None when ``arg`` is not a flag."""
    low = arg.lower()
    if low in {"--recursive", "-recurse", "-recursive"} or low.startswith("-recurse:"):
        return True
    if low.startswith("--"):
        return False
    if re.fullmatch(r"-[rRfivdIP]+", arg):
        # POSIX short flags such as -rf, -fr, -Rv.
        return "r" in arg.lower()
    if low.startswith("-"):
        # PowerShell parameter on the Remove-Item alias (-Force, -ErrorAction ...).
        return low in {"-r", "-rec"}
    return None


def _pipe_feeds_tree(piped_from: str | None) -> str | None:
    """Return the upstream command when it lists a whole tree without a name filter."""
    if not piped_from:
        return None
    tokens = _strip_prefix(_tokens(piped_from))
    if not tokens:
        return None
    base = _basename(tokens[0])
    args = tokens[1:]
    lowered = [a.lower() for a in args]
    if base == "find":
        recursive = True
    elif base in {"get-childitem", "gci", "ls", "dir"}:
        recursive = any(a.startswith("-rec") or a in {"-r", "/s"} or a == "-R".lower() for a in lowered)
    else:
        return None
    if not recursive:
        return None
    filtered = any(a in _NAME_FILTERS or a.startswith(("-filter:", "-include:")) for a in lowered)
    filtered = filtered or (base != "find" and any(re.search(r"[*?]", a) for a in args if not a.startswith("-")))
    return None if filtered else piped_from


def _piped_delete_reason(targets: list[str], piped_from: str | None) -> str | None:
    pipe_fed = not targets or all(re.fullmatch(r"\$_(?:\.\w+)*|\$psitem(?:\.\w+)*|\{\}", t, re.I) for t in targets)
    if not pipe_fed:
        return None
    upstream = _pipe_feeds_tree(piped_from)
    if upstream is None:
        return None
    return _deny(
        "整树列举后批量删除",
        _target_label([upstream]),
        kind="delete",
        detail="上游递归列举整棵目录且没有按名称过滤，删除会扫掉整棵树",
    )


def _deletion_reason(tokens: list[str], executable: str, piped_from: str | None = None) -> str | None:
    args = tokens[1:]
    if executable == "rm":
        recursive = False
        targets: list[str] = []
        for arg in args:
            flag = _rm_flag_is_recursive(arg)
            if arg == "--":
                continue
            if flag is None:
                targets.append(arg)
            else:
                recursive = recursive or flag
        if recursive:
            return _deny("递归删除", _target_label(targets, paths=True), kind="delete", detail="会整棵删除目录，hook 无法确认范围可恢复")
        return _piped_delete_reason(targets, piped_from)

    if executable in {"remove-item", "ri", "rmdir", "rd", "del", "erase"}:
        cmd_style = executable in {"rmdir", "rd", "del", "erase"}
        slash_s = cmd_style and any(arg.lower() == "/s" for arg in args)
        ps_recurse = any(
            arg.lower() in {"-r", "-re", "-rec", "--recurse", "--recursive"}
            or (arg.lower().startswith("-rec") and arg.lower() not in {"-replace"})
            for arg in args
        )
        values, _ = _option_values(args, frozenset({"-path", "-literalpath"}))
        values += [arg for arg in args if not arg.startswith(("-", "/")) and arg not in values]
        if executable in {"del", "erase"} and slash_s and not ps_recurse:
            # ``del /s *.pyc`` removes matching files under a tree; ``del /s dir`` or ``*`` removes them all.
            patterns = [ntpath.basename(v.rstrip("\\/")) for v in values]
            filtered = bool(patterns) and all(
                re.search(r"[*?]", p) and p not in {"*", "*.*"} for p in patterns
            )
            if filtered:
                return None
            return _deny("del /s 整树删除文件", _target_label(values, paths=True), kind="delete", detail="没有按文件模式过滤，会删除整棵目录里的全部文件")
        if slash_s or ps_recurse:
            return _deny("递归删除", _target_label(values, paths=True), kind="delete", detail="递归范围、动态目标或正式数据边界必须先核实")
        return _piped_delete_reason(values, piped_from)
    return None


def _find_reason(tokens: list[str]) -> str | None:
    args = tokens[1:]
    lowered = [a.lower() for a in args]
    roots = [a for a in args if not a.startswith(("-", "(", ")", "!"))][:1]
    filtered = any(a in _NAME_FILTERS for a in lowered)
    if "-delete" in lowered and not filtered:
        return _deny("find 整树删除", _target_label(roots, paths=True), kind="delete", detail="-delete 没有按名称过滤，会删掉整棵目录")
    for index, low in enumerate(lowered):
        if low in {"-exec", "-execdir", "-ok", "-okdir"} and index + 1 < len(args):
            sub: list[str] = []
            for token in args[index + 1 :]:
                if token in {";", "\\;", "+"}:
                    break
                sub.append(token)
            if not sub or _basename(sub[0]) not in _DELETE_VERBS:
                continue
            if not filtered:
                return _deny("find 整树删除", _target_label(roots, paths=True), kind="delete", detail="-exec 删除没有按名称过滤，会删掉整棵目录")
            reason = _deletion_reason(sub, _basename(sub[0]))
            if reason:
                return reason
    return None


def _robocopy_reason(tokens: list[str]) -> str | None:
    args = tokens[1:]
    if not any(a.lower() in {"/mir", "/purge"} for a in args):
        return None
    positional = [a for a in args if not a.startswith("/")]
    dest = positional[1:2] or positional[:1]
    return _deny(
        "robocopy 镜像或清除",
        _target_label(dest, paths=True),
        kind="delete",
        detail="/MIR 或 /PURGE 会删除目标目录里源目录没有的文件和子目录",
    )


def _git_reason(tokens: list[str]) -> str | None:
    if len(tokens) < 2 or _basename(tokens[0]) != "git":
        return None
    args = tokens[1:]
    sub_index = None
    i = 0
    while i < len(args):
        arg = args[i]
        # These global options consume a value before the subcommand.
        if arg in {"-C", "-c", "--git-dir", "--work-tree", "--namespace", "--exec-path", "--config-env"}:
            i += 2
            continue
        if arg.startswith("-"):
            i += 1
            continue
        sub_index = i
        break
    if sub_index is None:
        return None
    subcommand = args[sub_index].lower()
    subargs = args[sub_index + 1 :]
    flags = [a for a in subargs if a.startswith("-")]
    if subcommand == "clean" and not any("n" in arg.lower().lstrip("-") for arg in flags) and any("f" in arg.lower().lstrip("-") for arg in flags):
        return _deny("git clean 强制清理未跟踪内容", _target_label(subargs, paths=True), kind="discard", detail="未跟踪文件没有 Git 历史兜底")
    if subcommand == "reset" and any(arg.lower() == "--hard" for arg in subargs):
        return _deny("git reset --hard", _target_label(subargs), kind="discard", detail="会丢弃工作树或暂存区中的未提交改动")
    if subcommand == "checkout":
        if "--" in subargs:
            after = subargs[subargs.index("--") + 1 :]
            if not after or any(arg in _WIDE_PATHSPECS for arg in after):
                return _deny("git checkout -- 的宽范围恢复", _target_label(after, paths=True), kind="discard", detail="可能丢弃多个未提交文件")
        elif any(arg in _WIDE_PATHSPECS for arg in subargs):
            return _deny("git checkout 的宽范围恢复", _target_label(subargs, paths=True), kind="discard", detail="会用索引覆盖整棵工作树的未提交改动")
        if any(arg in {"-f", "--force"} for arg in subargs):
            return _deny("git checkout --force", _target_label(subargs), kind="discard", detail="切换时会丢弃工作树里的未提交改动")
    if subcommand == "restore":
        staged_only = any(a in {"--staged", "-S"} for a in subargs) and not any(a in {"--worktree", "-W"} for a in subargs)
        paths = subargs[subargs.index("--") + 1 :] if "--" in subargs else [a for a in subargs if not a.startswith("-")]
        if not staged_only and any(p in _WIDE_PATHSPECS for p in paths):
            return _deny("git restore 的宽范围恢复", _target_label(paths, paths=True), kind="discard", detail="会丢弃整棵工作树的未提交改动")
    if subcommand == "rm" and "--cached" not in subargs and any(
        a == "--force" or (a.startswith("-") and not a.startswith("--") and "f" in a[1:]) for a in subargs
    ):
        return _deny("git rm --force", _target_label([a for a in subargs if not a.startswith("-")], paths=True), kind="discard", detail="会连同未提交改动一起删除文件")
    if subcommand == "worktree" and subargs and subargs[0].lower() == "remove":
        if any(a in {"-f", "--force"} or a.lower() == "--force" for a in subargs[1:]):
            return _deny(
                "git worktree remove --force",
                _target_label([a for a in subargs[1:] if not a.startswith("-")], paths=True),
                kind="discard",
                detail="强制移除会丢弃 worktree 里未提交、未跟踪和忽略的内容",
            )
    return None


_P4_GLOBAL_WITH_VALUE = frozenset({"-c", "-u", "-p", "-P", "-H", "-d", "-C", "-Q", "-x", "-z", "-r", "-v", "-L", "-l"})


def _p4_reason(tokens: list[str]) -> str | None:
    args = tokens[1:]
    i = 0
    while i < len(args) and args[i].startswith("-"):
        i += 2 if args[i] in _P4_GLOBAL_WITH_VALUE else 1
    if i >= len(args):
        return None
    sub = args[i].lower()
    rest = args[i + 1 :]
    preview = "-n" in rest
    files = [a for a in rest if not a.startswith("-")]
    if sub == "obliterate" and "-y" in rest:
        return _deny("p4 obliterate -y", _target_label(files), kind="p4", detail="会永久删除服务器上的文件和历史，无法恢复")
    if sub == "clean" and not preview:
        return _deny("p4 clean", _target_label(files, paths=True), kind="p4", detail="会撤销本地修改并删除不在 depot 里的本地文件")
    if sub == "revert" and not preview and "-a" not in rest and "-k" not in rest:
        if any("..." in f or "*" in f for f in files):
            return _deny("p4 revert 宽范围", _target_label(files), kind="p4", detail="会丢弃匹配范围内所有已打开文件的本地修改")
    return None


def _python_tree_reason(tree: ast.AST) -> str | None:
    class Visitor(ast.NodeVisitor):
        reason: str | None = None
        loop_depth = 0

        def visit_For(self, node: ast.For) -> None:
            self.loop_depth += 1
            self.generic_visit(node)
            self.loop_depth -= 1

        visit_AsyncFor = visit_For

        def visit_While(self, node: ast.While) -> None:
            self.loop_depth += 1
            self.generic_visit(node)
            self.loop_depth -= 1

        def visit_ListComp(self, node: ast.ListComp) -> None:
            self.loop_depth += 1
            self.generic_visit(node)
            self.loop_depth -= 1

        visit_SetComp = visit_GeneratorExp = visit_DictComp = visit_ListComp

        def visit_Call(self, node: ast.Call) -> None:
            if self.reason:
                return
            name = _call_name(node.func)
            if name in {"shutil.rmtree", "rmtree"}:
                self.reason = _deny("Python 封装的递归删除", "<embedded script>", kind="delete", detail="识别到真实 shutil.rmtree 调用")
                return
            if name == "os.kill":
                pid = None
                if len(node.args) >= 1:
                    try:
                        pid = ast.literal_eval(node.args[0])
                    except (ValueError, TypeError, SyntaxError):
                        pass
                if self.loop_depth or not isinstance(pid, int) or pid <= 0:
                    self.reason = _deny("Python 封装的进程终止", "<embedded script>", kind="process", detail="PID 动态、为零/负数或位于循环中")
                    return
            if name and (name.startswith("subprocess.") or name in {"os.system", "os.popen"}):
                command = None
                for arg in node.args:
                    try:
                        value = ast.literal_eval(arg)
                    except (ValueError, TypeError, SyntaxError):
                        continue
                    if isinstance(value, str):
                        command = value
                        break
                    if isinstance(value, (list, tuple)) and all(isinstance(item, str) for item in value):
                        command = " ".join(value)
                        break
                if command is None:
                    self.reason = _deny("无法静态核实的 subprocess 调用", "<embedded script>", kind="opaque", detail="命令参数不是字面量")
                    return
                self.reason = _analyze_text(command, 1)
                if self.reason:
                    return
            self.generic_visit(node)

    visitor = Visitor()
    visitor.visit(tree)
    return visitor.reason


def _python_code_reason(code: str) -> str | None:
    """Inspect real Python calls only; strings and unrelated subprocess tests pass."""
    candidates = [code]
    unescaped = code.replace('\\"', '"')
    if unescaped != code:
        candidates.append(unescaped)
    for candidate in candidates:
        try:
            tree = ast.parse(candidate)
        except SyntaxError:
            continue
        return _python_tree_reason(tree)
    return _deny("无法静态核实的 Python 脚本", "<embedded script>", kind="opaque", detail="脚本语法无法解析")


def _python_invocation(tokens: list[str]) -> tuple[str | None, Any]:
    """Classify ``python ...``: ('code', text) for -c, ('script', index) for a script, else (None, None).

    Interpreter options end at the first non-option token; everything after a
    script path belongs to the script, so a later ``-c`` is not inline code.
    """
    i = 1
    while i < len(tokens):
        token = tokens[i]
        if token == "-c" or re.fullmatch(r"-[A-Za-z]*c", token) and not token.startswith("--"):
            return ("code", tokens[i + 1] if i + 1 < len(tokens) else "")
        if token in {"-m", "-"}:
            return (None, None)
        if token in {"-W", "-X", "--check-hash-based-pycs"}:
            i += 2
            continue
        if token.startswith("-"):
            i += 1
            continue
        return ("script", i)
    return (None, None)


def _node_code_reason(code: str) -> str | None:
    if (_NODE_RM_CALL.search(code) and _NODE_RECURSIVE.search(code)) or _NODE_TREE_HELPERS.search(code):
        return _deny("Node 封装的递归删除", "<embedded script>", kind="delete", detail="识别到递归删除调用")
    for match in _NODE_CHILD.finditer(code):
        reason = _analyze_text(match.group(2), 1)
        if reason:
            return reason
    return None


def _igo_payload(tokens: list[str], start: int) -> str | None:
    """Return the remote command carried by ``igo ps -c`` / ``igo spawn <name> <command_line>``."""
    if start >= len(tokens):
        return None
    sub = tokens[start].lower()
    rest = tokens[start + 1 :]
    if sub == "ps":
        for index, token in enumerate(rest):
            if token in {"-c", "--command"} and index + 1 < len(rest):
                return rest[index + 1]
            if token.startswith("--command="):
                return token.split("=", 1)[1]
    if sub == "spawn":
        positional: list[str] = []
        skip = False
        for token in rest:
            if skip:
                skip = False
                continue
            if token == "--cwd":
                skip = True
                continue
            if not token.startswith("-"):
                positional.append(token)
        if len(positional) >= 2:
            return positional[1]
    return None


def _call_name(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        prefix = _call_name(node.value)
        return f"{prefix}.{node.attr}" if prefix else node.attr
    return ""


def _block_body(segment: str) -> str:
    """Text inside a ``{ ... }`` script block.

    Statement splitting happens before this, so a block containing ``;`` arrives
    without its closing brace; the remainder after ``{`` is still the body.
    """
    start = segment.find("{")
    if start < 0:
        return ""
    body = segment[start + 1 :]
    end = body.rfind("}")
    return body[:end] if end >= 0 else body


def _analyze_segment(segment: str, depth: int = 0, piped_from: str | None = None) -> str | None:
    if depth > MAX_DEPTH:
        return None
    tokens = _strip_prefix(_tokens(segment))
    if not tokens:
        return None
    first = _basename(tokens[0])
    raw = segment.lstrip()

    if raw.startswith(("[", "$", "(")) or tokens[0].startswith(("[", "$")):
        if _DOTNET_TREE_DELETE.search(segment):
            return _deny(".NET 递归删除目录", "<expression>", kind="delete", detail="Delete(..., $true) 会整棵删除目录")

    if first == "sudo":
        return _analyze_segment(" ".join(tokens[1:]), depth + 1, piped_from)
    if first == "xargs":
        # xargs may add options before the command; the first non-option token
        # is the command whose per-item execution must be inspected.
        nested = next((i for i, token in enumerate(tokens[1:], 1) if not token.startswith("-")), None)
        return _analyze_segment(" ".join(tokens[nested:]), depth + 1, piped_from) if nested is not None else None

    if _PYTHON_EXE.fullmatch(first):
        mode, value = _python_invocation(tokens)
        if mode == "code":
            return _python_code_reason(value)
        if mode == "script" and _basename(tokens[value]) == "igo.py":
            payload = _igo_payload(tokens, value + 1)
            return _analyze_text(payload, depth + 1) if payload else None
        return None
    if first == "igo":
        payload = _igo_payload(tokens, 1)
        return _analyze_text(payload, depth + 1) if payload else None
    if first == "node":
        for index, token in enumerate(tokens[1:], 1):
            if token in {"-e", "--eval", "-p", "--print"} and index + 1 < len(tokens):
                return _node_code_reason(tokens[index + 1])
        return None
    if first == "ssh":
        args = tokens[1:]
        i = 0
        while i < len(args) and args[i].startswith("-"):
            i += 2 if args[i] in {"-p", "-i", "-o", "-l", "-J", "-F", "-b", "-c", "-D", "-E", "-e", "-I", "-L", "-m", "-O", "-Q", "-R", "-S", "-W", "-w", "-B"} else 1
        remote = args[i + 1 :]
        return _analyze_text(" ".join(remote), depth + 1) if remote else None

    if first in {"powershell", "pwsh", "cmd", "bash", "sh", "zsh"}:
        if any(token.lower() in {"-encodedcommand", "-enc", "/encodedcommand"} for token in tokens[1:]):
            for index, token in enumerate(tokens[1:], 1):
                if token.lower() in {"-encodedcommand", "-enc", "/encodedcommand"} and index + 1 < len(tokens):
                    try:
                        decoded = base64.b64decode(tokens[index + 1], validate=True)
                        text = decoded.decode("utf-16le") if len(decoded) % 2 == 0 else decoded.decode("utf-8")
                    except (ValueError, UnicodeError):
                        return _deny("无法核实的编码 shell 脚本", "<encoded script>", kind="opaque", detail="编码内容无法静态解析")
                    return _analyze_text(text, depth + 1) or None
            return _deny("无法核实的编码 shell 脚本", "<encoded script>", kind="opaque", detail="编码内容缺失")
        posix_shell = first in {"bash", "sh", "zsh"}
        for index, token in enumerate(tokens[1:], 1):
            low = token.lower()
            command_flag = low in {"-command", "-c", "/c", "/command"} or (
                posix_shell and re.fullmatch(r"-[a-z]*c[a-z]*", low) is not None
            )
            if command_flag and index + 1 < len(tokens):
                reason = _analyze_text(" ".join(tokens[index + 1 :]), depth + 1)
                if reason:
                    return reason
        return None

    # PowerShell/Windows control constructs can contain a destructive command
    # after ``do``/``then`` or inside a ForEach-Object script block.  Inspect
    # only those syntactic bodies; do not search arbitrary arguments (which
    # would make rg/echo examples false positives).
    if first in {"for", "foreach", "while", "until", "if"}:
        nested = re.search(r"\b(?:do|then)\b(.+)$", segment, re.I | re.S)
        if nested:
            reason = _analyze_text(nested.group(1), depth + 1)
            if reason:
                return reason
        body = _block_body(segment)
        if body:
            reason = _analyze_text(body, depth + 1)
            if reason:
                return reason
    if first in {"%", "foreach-object", "for-each-object"}:
        body = _block_body(segment)
        if body:
            reason = _analyze_text(body, depth + 1, piped_from)
            if reason:
                return reason

    if first in {"stop-process", "spps", "taskkill", "pkill", "killall", "kill"}:
        return _process_reason(tokens, first)
    if first == "find":
        return _find_reason(tokens)
    if first == "robocopy":
        return _robocopy_reason(tokens)
    if first == "p4":
        return _p4_reason(tokens)
    deletion = _deletion_reason(tokens, first, piped_from)
    if deletion:
        return deletion
    return _git_reason(tokens)


def _analyze_text(text: str, depth: int = 0, piped_from: str | None = None) -> str | None:
    previous = piped_from
    for op, segment in _split_shell(text):
        upstream = previous if op == "|" or (op == "" and piped_from is not None) else None
        reason = _analyze_segment(segment, depth, upstream)
        if reason:
            return reason
        previous = segment
    return None


_WRAPPED_VALUE = re.compile(r"(?i)[\"']?(?:cmd|command|shell|script|code|input)[\"']?\s*:\s*([\"'`])")


def _wrapped_values(text: str) -> list[str]:
    if len(text) > MAX_WRAPPER_SCAN_CHARS:
        return []
    values: list[str] = []
    for match in _WRAPPED_VALUE.finditer(text):
        quote = match.group(1)
        i = match.end()
        start = i
        escaped = False
        while i < len(text):
            char = text[i]
            if escaped:
                escaped = False
            elif char == "\\" and quote != "'":
                escaped = True
            elif char == quote:
                raw = text[start:i]
                if quote == '"':
                    try:
                        raw = json.loads('"' + raw + '"')
                    except (ValueError, TypeError):
                        pass
                else:
                    raw = raw.replace("\\'", "'").replace("\\`", "`")
                values.append(raw)
                break
            i += 1
        # An unterminated quote consumed the rest of the bounded input.  Do not
        # start another suffix scan from a later apparent property.
        if i >= len(text):
            break
    return values


def _commands(tool_name: str, tool_input: Any) -> list[str]:
    """Collect command strings from direct and nested Claude/Codex shapes."""
    found: list[str] = []
    total_scanned = 0
    too_large = False

    def visit(value: Any, direct: bool = False, depth: int = 0) -> None:
        if depth > 5:
            return
        if isinstance(value, str):
            nonlocal total_scanned, too_large
            if len(value) > MAX_COMMAND_CHARS or total_scanned + len(value) > MAX_WRAPPER_SCAN_CHARS:
                too_large = True
                return
            total_scanned += len(value)
            if direct:
                found.append(value)
            found.extend(_wrapped_values(value))
            return
        if isinstance(value, dict):
            for key, child in value.items():
                if key in COMMAND_KEYS:
                    if isinstance(child, list):
                        strings = [item for item in child if isinstance(item, str)]
                        if strings:
                            visit(" ".join(strings), direct=True, depth=depth + 1)
                    else:
                        visit(child, direct=True, depth=depth + 1)
                elif key in CONTAINER_KEYS:
                    visit(child, direct=direct, depth=depth + 1)
        elif isinstance(value, list):
            for child in value:
                visit(child, direct=direct, depth=depth + 1)

    # A string tool input for functions.exec is wrapper code, while a string for
    # an ordinary shell tool is itself the command.
    tool_leaf = tool_name.rsplit(".", 1)[-1].lower()
    direct_shell = tool_name in SHELL_TOOLS or tool_leaf in {name.lower() for name in SHELL_TOOLS}
    visit(tool_input, direct=direct_shell and not isinstance(tool_input, dict))
    if too_large:
        found.append("__SAFETY_HOOK_INPUT_TOO_LONG__")
    return found


def inspect_event(payload: Any) -> str | None:
    if not isinstance(payload, dict):
        return "PreToolUse denied: malformed hook input; target: <event>. Rule source: hook input contract. Provide one JSON object with tool_name and tool_input; no command was executed."
    tool_name = payload.get("tool_name", payload.get("toolName", ""))
    if not isinstance(tool_name, str):
        return "PreToolUse denied: malformed hook input; target: <tool_name>. Rule source: hook input contract. Provide a string tool_name and retry the same task without changing shells."
    tool_leaf = tool_name.rsplit(".", 1)[-1]
    if tool_name not in SHELL_TOOLS and tool_leaf.lower() not in {name.lower() for name in SHELL_TOOLS}:
        return None
    if "tool_input" in payload:
        tool_input = payload.get("tool_input")
    else:
        tool_input = payload.get("toolInput", {})
    if tool_input is None:
        return None
    global _TARGET_BASE
    base = payload.get("cwd") or payload.get("workdir") or ""
    if isinstance(tool_input, dict):
        base = tool_input.get("cwd") or tool_input.get("workdir") or base
    _TARGET_BASE = base if isinstance(base, str) and (ntpath.isabs(base) or base.startswith("/")) else ""
    for command in _commands(tool_name, tool_input):
        if command == "__SAFETY_HOOK_INPUT_TOO_LONG__":
            return _deny("输入过长无法确定检查", "<command input>", kind="input")
        reason = _analyze_text(command)
        if reason:
            return reason
    return None


def _emit_denial(reason: str) -> int:
    result = {
        "permissionDecision": "deny",
        "permissionDecisionReason": reason,
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        },
    }
    sys.stdout.write(json.dumps(result, ensure_ascii=False, separators=(",", ":")) + "\n")
    sys.stderr.write(reason + "\n")
    return 2


def main() -> int:
    try:
        raw = sys.stdin.buffer.read(1_048_576)
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        return _emit_denial(
            f"PreToolUse denied: malformed hook JSON ({type(exc).__name__}); target: <event>. "
            "Rule source: hook input contract. Send one valid JSON object with tool_name and tool_input; no command was executed."
        )
    reason = inspect_event(payload)
    return _emit_denial(reason) if reason else 0


if __name__ == "__main__":
    raise SystemExit(main())
