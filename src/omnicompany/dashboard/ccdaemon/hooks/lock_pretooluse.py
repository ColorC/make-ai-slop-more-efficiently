# [OMNI] origin=ai-ide ts=2026-05-02 type=infra status=active agent=ai-ide-current
# [OMNI] summary="G4 内部实时拦截 hook - PreToolUse 拦 Edit/Write 等写入工具, 路径不合法时 warn 或 enforce"
# [OMNI] why="G4 离线 MVP 之上加实时层. mode=warn 默认 (打 stderr 引用规范但不阻断), mode=enforce 切真阻断, mode=off 关"
# [OMNI] tags=hook,lock,pretooluse,realtime,G4
# [OMNI] material_id="material:dashboard.cc_wrapper.hooks.pretooluse_lock_enforcer.implementation.py"
"""G4 实时拦截 PreToolUse hook.

拦写入工具 (Edit/Write/MultiEdit/NotebookEdit) 调用前判定 file_path 合法性:
  1. 锁未启用 → 放行
  2. 文件不在 watched_paths → 放行
  3. 文件在白名单 → 放行
  4. 文件在 baseline → 放行
  5. 文件在注册中心 → 放行
  6. 不命中任何豁免 → 按 runtime_mode 决定:
     - off: 放行
     - warn: stderr 给 OMNI-LOCK-VIOLATION 提示 + 引用规范, **放行** (exit 0)
     - enforce: stderr + exit 2 阻断 (claude code 会 deny 这次工具调用)

跟离线 G4 (services/_core/protection) 共用同一份策略文件 (.omni/protection_policy.json),
判定逻辑也走同一份函数 (is_watched / is_whitelisted / is_in_baseline). 只是触发时机
不同 (hook 是写入前, 离线 scan 是写入后).
"""
from __future__ import annotations

import os
import sys

from . import _shared as sh
from ..write_scope import planned_write_scope, resolve_candidate, ToolPathCandidate, denial_message, is_inside_or_equal


_WRITE_TOOLS = frozenset({"Edit", "Write", "MultiEdit", "NotebookEdit", "str_replace_editor"})


def _project_root_str() -> str:
    return str(sh.repo_root())


# ── 顶层 stray 实时守卫 (2026-06-26 用户"防垃圾"): 复用 hygiene-profile 闭集 ──
# 防"错误CWD下相对路径写"造顶层游离垃圾 (E:\e / E:\tmp / WS\data 等同一根因)。
# 写工具: 精确闭集判定(高置信, enforce 可阻断 = 用户要的"自己通道报错形")。
# Bash:   cwd 在危险大根(盘根/工作区根)+写命令 → 详细警告(启发式, 只 warn 不误杀)。
# 全程 fail-open: 守卫自身任何异常都不阻塞用户工作。规范见 directory_cleanliness §一·5/§6。

def _stray_guard_mode() -> str:
    """warn(默认) / enforce / off。读 protection_policy.stray_guard_mode。"""
    try:
        from omnicompany.packages.services._core.protection import load_policy
        return str((load_policy() or {}).get("stray_guard_mode", "warn")).lower()
    except Exception:
        return "warn"


def _stray_guard(payload: dict) -> int | None:
    """返回 2 = 阻断(仅 enforce 命中写工具 stray); 否则 None(可能已打印 warning)。永不抛。"""
    try:
        mode = _stray_guard_mode()
        if mode == "off":
            return None
        tool_name = payload.get("tool_name") or payload.get("toolName") or ""
        tool_input = payload.get("tool_input") or payload.get("toolInput") or {}
        if not isinstance(tool_input, dict):
            return None
        cwd = str(payload.get("cwd") or os.getcwd())
        from omnicompany.packages.services._core.guardian.rules.project_profile_hygiene import (
            check_top_level_stray, dangerous_bash_roots,
        )
        from pathlib import Path

        # 写工具: 精确解析 file_path → 闭集判定
        if tool_name in _WRITE_TOOLS:
            fp = (tool_input.get("file_path") or tool_input.get("path")
                  or tool_input.get("notebook_path") or "")
            if not fp:
                return None
            try:
                p = Path(str(fp))
                target = p if p.is_absolute() else (Path(cwd) / p)
            except Exception:
                return None
            stray = check_top_level_stray(target)
            if not stray:
                return None
            label = "BLOCKED" if mode == "enforce" else "WARN"
            msg = (
                f"\n[OMNI-STRAY-{label}] 这次写入会在顶层制造游离垃圾:\n"
                f"  目标: {target}\n"
                f"  {stray['message']}\n\n"
                f"正确做法: 用绝对路径写进具体项目, 或先 cd 进该项目再用项目内相对路径。\n"
                f"若是合法新顶层(需审批): 往 {{项目}}/.omni/hygiene-profile.yaml 对应 root 的\n"
                f"allowed_root_dirs/allowed_root_files 登记一行再重试。规范: directory_cleanliness §一·5/§6。\n"
            )
            sh.append_audit("stray_guard_write", {
                "target": str(target), "tool_name": tool_name,
                "mode": mode, "root": stray.get("root_label"),
            })
            try:
                print(msg, file=sys.stderr)
            except OSError:
                pass
            return 2 if mode == "enforce" else None

        # Bash 两道警告(启发式, 不阻断):
        #   (1) 命令里出现【绝对】顶层 stray 路径 → 精确判闭集(任何 cwd 都抓, 如 mkdir E:\junk)。
        #   (2) cwd 在危险大根 + 写命令(相对路径)→ 启发式(补 SHELL_WRITE_RE 漏的 cp/mv/clone/pnpm)。
        if tool_name in ("Bash", "Shell"):
            import re as _re
            command = str(tool_input.get("command") or "")
            if not command:
                return None
            # (1) 抽命令里的绝对路径 token(含 \\?\ 前缀), 逐个精确判顶层 stray
            for tok in _re.findall(r"(?:\\\\\?\\)?[A-Za-z]:[\\/][^\s\"'|&;<>]*", command):
                s = check_top_level_stray(tok.rstrip("\\/"))
                if s:
                    msg = (
                        f"\n[OMNI-STRAY-WARN] Bash 命令会在顶层制造游离垃圾:\n"
                        f"  命令含绝对路径: {tok}\n  {s['message']}\n\n"
                        f"正确做法: 写进具体项目(绝对路径)或先 cd 进项目。规范: directory_cleanliness §一·5/§6。\n"
                    )
                    sh.append_audit("stray_guard_bash_abs", {
                        "token": tok, "command": command[:200], "root": s.get("root_label"),
                    })
                    try:
                        print(msg, file=sys.stderr)
                    except OSError:
                        pass
                    return None
            # (2) 写命令检测: SHELL_WRITE_RE + 补 cp/mv/clone/pnpm/xcopy 等它漏的
            is_write = False
            try:
                from ..write_scope import SHELL_WRITE_RE
                is_write = bool(SHELL_WRITE_RE.search(command))
            except Exception:
                pass
            if not is_write:
                is_write = bool(_re.search(
                    r"\b(cp|mv|move|copy|xcopy|robocopy|pnpm|npm|yarn|wget|tar)\b|git\s+clone",
                    command, _re.IGNORECASE))
            if not is_write:
                return None
            bcwd = str(tool_input.get("cwd") or cwd)
            try:
                bcwd_resolved = Path(bcwd).resolve()
            except Exception:
                return None
            if not any(r == bcwd_resolved for r in dangerous_bash_roots()):
                return None
            msg = (
                f"\n[OMNI-STRAY-WARN] 在大根目录下用写命令, 相对路径极易制造顶层垃圾:\n"
                f"  cwd: {bcwd_resolved}  (盘根/工作区根, 不该在此直接写)\n\n"
                f"根因铁律(directory_cleanliness §6): 写文件/建目录一律【绝对路径】, 或先 cd 进具体项目。\n"
                f"别在此用 `data/...` `tmp/...` `e/...` 这类相对路径 —— 会长成顶层游离垃圾。\n"
            )
            sh.append_audit("stray_guard_bash", {
                "cwd": str(bcwd_resolved), "command": command[:200],
            })
            try:
                print(msg, file=sys.stderr)
            except OSError:
                pass
            return None
        return None
    except Exception:
        return None


def _build_warn_message(file_path: str, mode: str) -> str:
    label = "WARN" if mode == "warn" else "BLOCKED"
    return (
        f"\n[OMNI-LOCK-{label}] 写入路径不在合法白名单 / baseline / 注册中心:\n"
        f"  file_path: {file_path}\n\n"
        f"如果这是合法新内容, 走 omnicompany 流程:\n"
        f"  1. omni new --kind=<kind> --name=<name>           # 立沙盒草稿\n"
        f"  2. 在沙盒里编辑\n"
        f"  3. omni sandbox check --content=<草稿>             # 自检\n"
        f"  4. omni sandbox promote --content=<草稿> --target={file_path} --kind=<kind>\n"
        f"     # 走完 promote 流程会自动注册到中心, 之后写入就不再 warn\n\n"
        f"如果是误判 (合法的工程文件), 改 .omni/protection_policy.json 加 whitelist_patterns,\n"
        f"或 omni lock baseline --snapshot 把当前现状全 grandfather.\n"
        f"如要临时关 warn 跑工作: omni lock mode --set=off (不推荐长期关).\n"
    )


def _active_plan_id(payload: dict, cwd: str) -> str | None:
    pty_id = os.environ.get("OMNI_CC_PTY_ID")
    store = sh._read_cc_sessions_store(sh.repo_root())
    if pty_id and pty_id in store:
        return store.get(pty_id, {}).get("active_plan")
    session_id = payload.get("session_id") or payload.get("sessionId") or ""
    plan = sh.detect_active_plan(
        hint_cwd=cwd,
        provider=sh.binding_provider(payload),
        session_id=str(session_id) if session_id else None,
    )
    return sh.plan_id_of(plan) if plan else None


def _handle_payload(payload: dict) -> int:
    """Evaluate one Claude-style per-file tool payload."""
    tool_name = payload.get("tool_name") or payload.get("toolName") or ""

    # 顶层 stray 实时守卫 (覆盖 Bash + 写工具)。返回 2 = enforce 阻断; None = 放行/已 warn。
    _sg = _stray_guard(payload)
    if _sg is not None:
        return _sg

    # 不是写入工具 → 直接放行
    if tool_name not in _WRITE_TOOLS:
        return 0

    tool_input = payload.get("tool_input") or payload.get("toolInput") or {}
    file_path = (
        tool_input.get("file_path")
        or tool_input.get("path")
        or tool_input.get("notebook_path")
        or ""
    )
    if not file_path:
        return 0

    cwd = payload.get("cwd") or os.getcwd()
    active_plan = _active_plan_id(payload, str(cwd))
    scope = planned_write_scope(cwd=str(cwd), active_plan=active_plan)
    candidate = resolve_candidate(ToolPathCandidate(key="file_path", raw=str(file_path)), cwd=str(cwd))
    root = sh.repo_root().resolve()
    if candidate.resolved is not None and not is_inside_or_equal(candidate.resolved, root):
        if is_inside_or_equal(candidate.resolved, scope.workspace_root) or any(
            is_inside_or_equal(candidate.resolved, allowed_root) for allowed_root in scope.roots[1:]
        ) or any(candidate.resolved == allowed_path for allowed_path in scope.paths):
            return 0
        mode = "enforce"
        msg = denial_message(tool_name, candidate, scope) or _build_warn_message(file_path, mode)
        sh.append_audit("lock_pretooluse_external_scope", {
            "file_path": file_path,
            "tool_name": tool_name,
            "mode": mode,
            "active_plan": active_plan,
            "session_id": payload.get("session_id"),
        })
        try:
            print(msg, file=sys.stderr)
        except OSError:
            pass
        return 2

    # 跑判定 (复用 G4 离线层的策略函数)
    try:
        from omnicompany.packages.services._core.protection import (
            load_policy, is_watched, is_whitelisted, is_in_baseline,
        )
    except ImportError:
        return 0  # 防御: protection 模块不可用就放行 (不阻塞用户工作)

    policy = load_policy()
    if not policy.get("enabled", False):
        return 0  # 锁没开 → 放行

    # 元 IO 规则检查 — watched_meta_io_per_path 真消费 (2026-05-02 加)
    meta_io_rules = policy.get("meta_io_rules", {})
    per_path_rules = meta_io_rules.get("watched_meta_io_per_path", [])
    if per_path_rules:
        # 把 file_path 标准化跟规则 path_prefix 比
        try:
            from pathlib import Path as _P
            proj = sh.repo_root()
            try:
                rel_for_match = _P(file_path).resolve().relative_to(proj).as_posix()
            except (ValueError, OSError):
                rel_for_match = file_path.replace("\\", "/")
        except Exception:
            rel_for_match = file_path

        # tool_name 推断它产生的元 IO (按 cc_wrapper hook 接到的 tool 名)
        # Edit / Write / MultiEdit 都是写 → 推 create_file / overwrite_file
        produced_meta_io = set()
        if tool_name in ("Edit", "MultiEdit", "str_replace_editor"):
            produced_meta_io = {"meta_io.fs.overwrite_file"}
        elif tool_name == "Write":
            produced_meta_io = {"meta_io.fs.create_file", "meta_io.fs.overwrite_file"}
        elif tool_name == "NotebookEdit":
            produced_meta_io = {"meta_io.fs.overwrite_file"}

        for rule in per_path_rules:
            prefix = rule.get("path_prefix", "")
            if not prefix or not rel_for_match.startswith(prefix):
                continue
            allowed = set(rule.get("allowed_meta_io", []))
            mode = rule.get("mode", "warn")
            if produced_meta_io and not (produced_meta_io & allowed):
                # 此 tool 产生的元 IO 不在 allowed 里 → 违规
                sh.append_audit("lock_meta_io_per_path", {
                    "tool_name": tool_name, "file_path": file_path,
                    "rule_path_prefix": prefix, "rule_mode": mode,
                    "tool_produced": list(produced_meta_io),
                    "rule_allowed": list(allowed),
                })
                msg = (
                    f"\n[OMNI-LOCK-{mode.upper()}] watched_meta_io_per_path 规则命中:\n"
                    f"  file_path: {file_path}\n"
                    f"  path_prefix: {prefix}\n"
                    f"  tool {tool_name} 产生 {produced_meta_io}, 不在 allowed {allowed} 里\n"
                    f"修法:\n"
                    f"  - 调允许的 meta_io 工具 (用 omni meta-io list 查)\n"
                    f"  - 或改 .omni/protection_policy.json 的 watched_meta_io_per_path 加白\n"
                )
                try:
                    print(msg, file=sys.stderr)
                except OSError:
                    pass
                if mode == "enforce":
                    return 2

    if meta_io_rules.get("enforce_unregistered_tools"):
        sh.append_audit("lock_meta_io_check", {
            "tool_name": tool_name, "file_path": file_path,
            "rule": "enforce_unregistered_tools",
        })

    if not is_watched(file_path, policy):
        return 0
    if is_whitelisted(file_path, policy):
        return 0
    if is_in_baseline(file_path):
        return 0

    # 在 registry 里 → 放行
    try:
        from omnicompany.packages.services._core.registry import get_registry
        reg = get_registry()
        proj = sh.repo_root()
        try:
            from pathlib import Path
            rel = Path(file_path).resolve().relative_to(proj).as_posix()
        except (ValueError, OSError):
            rel = file_path.replace("\\", "/")
        for entry in reg.list_all():
            if (entry.source_file or "").replace("\\", "/").endswith(rel) or \
               (entry.source_file or "").replace("\\", "/") == rel:
                return 0
    except Exception:
        pass  # 注册中心失效不阻塞

    # 命中违规 — 按 runtime_mode 决定
    mode = policy.get("runtime_mode", "warn")
    msg = _build_warn_message(file_path, mode)

    # 审计
    sh.append_audit("lock_pretooluse", {
        "file_path": file_path, "tool_name": tool_name, "mode": mode,
        "session_id": payload.get("session_id"),
    })

    # 可选: 发 event 到 bus 让 dashboard 看到
    try:
        sh.emit_event(
            trace_id=sh.trace_id_for(payload),
            event_type="agent.lock.violation",
            payload={
                "file_path": file_path, "tool_name": tool_name,
                "mode": mode, "classification": "internal_misplace",
            },
            tags=["lock", f"mode:{mode}"],
        )
    except Exception:
        pass

    if mode == "off":
        return 0
    if mode == "warn":
        # stderr 提示但不阻断
        try:
            print(msg, file=sys.stderr)
        except OSError:
            pass
        return 0
    if mode == "enforce":
        # exit 2 阻断 (claude code 把 stderr 给 LLM 让其知道为什么被拒)
        try:
            print(msg, file=sys.stderr)
        except OSError:
            pass
        return 2

    # 未知 mode 默认 warn
    try:
        print(msg, file=sys.stderr)
    except OSError:
        pass
    return 0


def main() -> int:
    """PreToolUse entry, including Codex multi-file ``apply_patch`` calls."""

    payload = sh.read_stdin_json()
    tool_name = payload.get("tool_name") or payload.get("toolName") or ""
    if tool_name != "apply_patch":
        return _handle_payload(payload)

    tool_input = payload.get("tool_input") or payload.get("toolInput") or {}
    command = tool_input.get("command") if isinstance(tool_input, dict) else ""
    targets = sh.apply_patch_targets(str(command or ""))
    if not targets:
        return 0

    for normalized_tool, file_path in targets:
        per_file = dict(payload)
        per_file["tool_name"] = normalized_tool
        per_file["tool_input"] = {"file_path": file_path}
        result = _handle_payload(per_file)
        if result == 2:
            return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
