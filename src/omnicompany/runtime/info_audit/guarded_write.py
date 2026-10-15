# [OMNI] origin=claude-code domain=runtime/info_audit/guarded_write ts=2026-04-09T00:00:00Z
# [OMNI] material_id="material:runtime.info_audit.write_gatekeeper.rule_engine.py"
"""Guarded Write — 规则化写入守门员。

设计决策 (2026-04-09 用户反馈):
  - **不走 LLM**, 规则引擎判定
  - 唯一必须检查: "会不会破坏自身" (omnicompany 框架核心代码)
  - 每次调用只允许写入 **一个** 唯一的 `allowed_output` 路径
  - 其他路径一律拒绝, 返回拒绝原因

使用方式:

    result = guarded_write(
        target_path=Path("data/scratch/fallback/trace_X/out.md"),
        content="...",
        allowed_output=Path("data/scratch/fallback/trace_X/out.md"),
        trace_id="trace_X",
    )
    if result.status == "ok":
        print("written")
    else:
        print(f"rejected: {result.reason}")

UniversalFallbackLoop 先让外置 AgentRuntime 返回结构化报告，再由 Omni 直接
调用 guarded_write。AgentRuntime 不获得任何写工具或输出路径选择权。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from omnicompany.core.config import _project_root


@dataclass
class GuardedWriteResult:
    status: str  # "ok" / "rejected"
    reason: str
    path: str
    bytes_written: int = 0


# 项目根 = omnicompany/ 目录
_REPO_ROOT = _project_root()

# "自身" 路径 — 写入这些目录下的任何文件一律拒绝
_SELF_PROTECT_PATHS = [
    _REPO_ROOT / "src" / "omnicompany",
    _REPO_ROOT / ".claude",
    _REPO_ROOT / "pyproject.toml",
    _REPO_ROOT / "setup.cfg",
    _REPO_ROOT / "setup.py",
    _REPO_ROOT / ".git",
]


def _is_under(path: Path, parent: Path) -> bool:
    try:
        p = path.resolve()
        par = parent.resolve()
        return p == par or par in p.parents
    except Exception:
        return False


def _would_damage_self(target: Path) -> tuple[bool, str]:
    """检查 target 路径是否会破坏 omnicompany 自身。

    Returns:
        (True, 原因) 如果会破坏
        (False, "") 如果安全
    """
    for protected in _SELF_PROTECT_PATHS:
        if _is_under(target, protected):
            return True, f"target 落在自身保护区: {protected}"
    return False, ""


def guarded_write(
    *,
    target_path: Path | str,
    content: str | bytes,
    allowed_output: Path | str,
    trace_id: str = "",
    max_bytes: int = 100 * 1024,
) -> GuardedWriteResult:
    """规则化写入守门员。

    规则:
      1. 写入路径必须**恰好等于** `allowed_output`
      2. `allowed_output` 本身不能落在自身保护区
      3. 内容字节数不能超过 `max_bytes`
      4. 父目录会自动创建

    Args:
        target_path: Agent 想写的路径 (通常来自工具调用)
        content: 要写的内容
        allowed_output: 本次 fallback 唯一允许的输出路径
        trace_id: 仅供日志
        max_bytes: 单次写入上限

    Returns:
        GuardedWriteResult
    """
    target = Path(target_path)
    allowed = Path(allowed_output)

    # 1. 路径必须严格等于 allowed_output
    try:
        if target.resolve() != allowed.resolve():
            return GuardedWriteResult(
                status="rejected",
                reason=(
                    f"只允许写入指定的 allowed_output: {allowed} (你写的是 {target}). "
                    f"本次 fallback 限定单一输出文件, 无法写其他位置。"
                ),
                path=str(target),
            )
    except Exception as e:
        return GuardedWriteResult(
            status="rejected",
            reason=f"路径解析失败: {e}",
            path=str(target),
        )

    # 2. allowed_output 不能落在自身保护区
    damage, why = _would_damage_self(allowed)
    if damage:
        return GuardedWriteResult(
            status="rejected",
            reason=f"allowed_output 本身违反自身保护: {why}",
            path=str(target),
        )

    # 3. 内容大小检查
    if isinstance(content, str):
        data = content.encode("utf-8")
    else:
        data = content
    if len(data) > max_bytes:
        return GuardedWriteResult(
            status="rejected",
            reason=f"内容超出上限 {max_bytes} 字节 (实际 {len(data)} 字节)",
            path=str(target),
        )

    # 4. 写入
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    except Exception as e:
        return GuardedWriteResult(
            status="rejected",
            reason=f"写入失败: {e}",
            path=str(target),
        )

    return GuardedWriteResult(
        status="ok",
        reason="",
        path=str(target),
        bytes_written=len(data),
    )


# ---------------------------------------------------------------------------
# Bash 命令白名单校验 (反之: 黑名单拒绝写入类命令)
# ---------------------------------------------------------------------------

# 允许的命令前缀 (只读/查找/检索)
_READONLY_BASH_PREFIXES = {
    "ls", "dir", "find", "grep", "rg", "fgrep", "egrep",
    "cat", "head", "tail", "less", "more", "wc", "file", "stat",
    "pwd", "echo", "which", "where", "whereis", "type",
    "git status", "git log", "git diff", "git show", "git blame",
    "git branch", "git tag", "git remote", "git ls-files", "git grep",
    "python -c", "python3 -c", "py -c",
    "tree",
}

# 绝对禁止的命令 (写入 / 删除 / 执行外部脚本)
_BLACKLISTED_BASH_PATTERNS = [
    "rm ", "rmdir ", "mv ", "cp ",
    "chmod ", "chown ", "touch ",
    " > ", " >> ", " | tee", " >>>",
    "mkdir ", "ln ", "dd ",
    "curl ", "wget ", "ssh ", "scp ",
    "pip install", "pip uninstall", "npm install", "npm uninstall",
    "apt ", "apt-get", "yum ", "brew ",
    "git add", "git commit", "git push", "git reset", "git checkout",
    "git merge", "git rebase", "git restore", "git rm", "git mv",
    "p4 edit", "p4 submit", "p4 revert", "p4 add",
    "sudo ", "su ",
]


def validate_readonly_bash(command: str) -> tuple[bool, str]:
    """判定一条 bash 命令是否只读安全。

    Returns:
        (True, "") 如果安全
        (False, 拒绝原因) 否则
    """
    if not command or not command.strip():
        return False, "空命令"
    cmd = command.strip()

    # 检查黑名单
    lowered = cmd.lower()
    for pat in _BLACKLISTED_BASH_PATTERNS:
        if pat in lowered:
            return False, f"命令包含禁止模式 '{pat.strip()}' (fallback 只读模式)"

    # 检查白名单前缀
    for prefix in _READONLY_BASH_PREFIXES:
        if cmd.startswith(prefix):
            return True, ""

    return False, (
        f"命令 '{cmd[:40]}...' 不在只读白名单。fallback 只读模式仅允许: "
        f"{', '.join(sorted(_READONLY_BASH_PREFIXES)[:8])}..."
    )
