# [OMNI] origin=claude-code domain=omnicompany/guardian ts=2026-04-23T00:00:00Z type=util
# [OMNI] material_id="material:core.guardian.git_hook_installer.implementation.py"
"""guardian.hook_installer — git hook 幂等安装器 (I-19, 2026-04-23).

背景:
    DESIGN.md D7 记录 auto_check 触发路径已写好但 git hook 注册"不全自动".
    历史上 .git/hooks/ 里的 pre-commit / post-commit 是散落编辑进去的,
    session 切换后可能丢失/漂移, 各 agent 的"本机"副本各不相同.

本模块作用:
    - 把权威 hook 模板固化在代码里 (PRE_COMMIT_TEMPLATE / POST_COMMIT_TEMPLATE)
    - 提供 install_hooks(force=False) 幂等安装
    - CLI: `omni guardian hook-install [--force] [--dry-run]`
    - Marker 机制: hook 里含 `# OMNI-GUARDIAN-MANAGED` 标记, 只覆盖自管的版本

设计 (plan §十 分布式规定):
    hook 模板属 Guardian 本模块 (就近) 的规定, 不走 archmap. 未来若 Guardian
    以外的 service (doctor/sentinel) 也想挂 hook, 走同样 pattern 各自管各自.
"""
from __future__ import annotations

import logging
import stat
from pathlib import Path
from typing import Literal

logger = logging.getLogger(__name__)


# hook 内含此 marker 标识 "由 Guardian 管理". 没 marker 的 hook 当作用户自定义, 不覆盖.
MANAGED_MARKER = "# OMNI-GUARDIAN-MANAGED"


PRE_COMMIT_TEMPLATE = f"""#!/bin/sh
{MANAGED_MARKER} (auto-installed by omni guardian hook-install, 2026-04-23 I-19; 2026-06-13 加 093a~d)
# OmniGuardian pre-commit hook
#
# 拦截集合 (零误报 absolute 规则, 直接阻塞 commit):
#   OMNI-014 illegal-drawer           (src/omnicompany/ 下非法子目录)
#   OMNI-015 forbidden-root-file      (仓库根禁区文件)
#   OMNI-016 packages-direct-child    (packages/ 下非 layer 子目录)
#   OMNI-017 format-not-observable    (package 没注册到 core/pipelines.py)
#   OMNI-018 router-not-observable    (Router 子类没被任何 pipeline 引用)
#   OMNI-035f docs/plans/<topic>/ 子项不在闭集    (2026-04-28 加)
#   OMNI-035g docs/ 子目录禁 .py / .pyc / __pycache__
#   OMNI-035h docs/ 子目录禁 .json / .jsonl 数据产物
#   OMNI-035i docs/ 禁运行时残留 (.log/.prefab/...)
#   OMNI-093a~c 设施统一唯一权威收束防漂移 (093d 语义判断已下沉 doc_steward)
#
# 其他违规 (含 OMNI-040~051 / 035j MEDIUM) 只 warn, 由 post-commit 汇总.
# 受 hygiene_whitelist 豁免的存量违规自动跳过 (figma agent 提交不被阻断).
# 紧急绕过: git commit --no-verify

if ! command -v omni >/dev/null 2>&1; then
    echo "[OmniGuardian pre-commit] omni 命令未安装, 跳过"
    exit 0
fi

PATROL_OUT=$(omni guardian patrol --staged-only --json-out 2>/dev/null)
if [ -z "$PATROL_OUT" ]; then
    exit 0
fi

BLOCKING=$(python -c "
import json, sys
BLOCK_RULES = {{
    'OMNI-014', 'OMNI-015', 'OMNI-016', 'OMNI-017', 'OMNI-018',
    'OMNI-035f', 'OMNI-035g', 'OMNI-035h', 'OMNI-035i',
    'OMNI-093a', 'OMNI-093b', 'OMNI-093c',
}}
try:
    r = json.loads(sys.stdin.read())
    blockers = [v for v in r.get('violations', []) if v.get('rule_id') in BLOCK_RULES]
    if blockers:
        print('BLOCK')
        for b in blockers:
            print(f\\\"  {{b.get('rule_id')}} {{b.get('path')}}: {{b.get('message','')[:200]}}\\\", file=sys.stderr)
except Exception:
    pass
" <<< "$PATROL_OUT" 2>&1)

if echo "$BLOCKING" | head -1 | grep -q "^BLOCK"; then
    echo "$BLOCKING" | tail -n +2 >&2
    echo "" >&2
    echo "[OmniGuardian] commit 被阻止 (零误报 absolute 规则命中)." >&2
    echo "  拦截集合: OMNI-014~018 + OMNI-035f~i + OMNI-093a~c" >&2
    echo "  存量豁免文件不被拦截 (.omni/guardian/hygiene-whitelist.json)" >&2
    echo "  紧急绕过: git commit --no-verify" >&2
    exit 1
fi

exit 0
"""


POST_COMMIT_TEMPLATE = f"""#!/bin/sh
{MANAGED_MARKER} (auto-installed by omni guardian hook-install, 2026-04-23 I-19)
# OmniGuardian post-commit hook
# 每次 commit 后扫最新 commit + 跑 hygiene scan. 永远 exit 0, 不阻塞.

if command -v omni >/dev/null 2>&1; then
    # 1. patrol 扫最近一个 commit
    omni guardian patrol --commits 1 --no-uncommitted 2>/dev/null || true

    # 2. hygiene 扫运行空间 (空目录/临时文件/老化/体积告警)
    echo ""
    echo "─────────── Runtime Hygiene (软告警) ───────────"
    omni run guardian-hygiene 2>/dev/null | tail -20 || true
fi

exit 0
"""


_HOOK_TEMPLATES: dict[str, str] = {
    "pre-commit": PRE_COMMIT_TEMPLATE,
    "post-commit": POST_COMMIT_TEMPLATE,
}


class HookInstallError(RuntimeError):
    """安装失败 (非幂等跳过情况)."""


def _find_git_dir(project_root: Path) -> Path | None:
    """找 .git 目录. 不是 git 工作树返回 None."""
    candidate = project_root / ".git"
    if candidate.is_dir():
        return candidate
    # worktree 情况: .git 是一个文件指向真实 gitdir
    if candidate.is_file():
        try:
            content = candidate.read_text(encoding="utf-8").strip()
            if content.startswith("gitdir:"):
                path = content.split(":", 1)[1].strip()
                p = Path(path)
                if not p.is_absolute():
                    p = (project_root / p).resolve()
                return p
        except Exception:
            pass
    return None


def _current_hook_status(
    hook_path: Path,
    template: str,
) -> Literal["absent", "managed-current", "managed-stale", "foreign"]:
    """判当前 hook 文件状态:

    absent          — 文件不存在
    managed-current — Guardian 管理且与模板一致
    managed-stale   — Guardian 管理但内容漂移
    foreign         — 文件存在但非 Guardian 管理 (用户自定义 hook, 不能覆盖)
    """
    if not hook_path.exists():
        return "absent"
    try:
        content = hook_path.read_text(encoding="utf-8")
    except Exception:
        return "foreign"
    if MANAGED_MARKER not in content:
        return "foreign"
    return "managed-current" if content == template else "managed-stale"


def install_hooks(
    project_root: Path,
    force: bool = False,
    dry_run: bool = False,
) -> dict[str, str]:
    """幂等安装 Guardian git hook.

    行为:
      - absent          → 装模板
      - managed-current → skip
      - managed-stale   → 装模板 (等同刷新)
      - foreign + force=False → skip 并返回 "skipped-foreign"
      - foreign + force=True  → 备份 (`<name>.bak-omni-<ts>`) 再覆盖

    Args:
        project_root: repo 根目录 (含 .git 目录)
        force: True 时覆盖 foreign hook (先备份)
        dry_run: True 时只汇报不写盘

    Returns:
        dict[hook_name -> action], action ∈
          {"installed", "refreshed", "skipped-current", "skipped-foreign",
           "replaced-foreign"}.
    """
    git_dir = _find_git_dir(project_root)
    if git_dir is None:
        raise HookInstallError(f"未找到 .git 目录: {project_root}")
    hooks_dir = git_dir / "hooks"
    if not hooks_dir.exists():
        if dry_run:
            return {name: "would-create-hooks-dir" for name in _HOOK_TEMPLATES}
        hooks_dir.mkdir(parents=True, exist_ok=True)

    result: dict[str, str] = {}
    for name, template in _HOOK_TEMPLATES.items():
        hook_path = hooks_dir / name
        status = _current_hook_status(hook_path, template)

        if status == "managed-current":
            result[name] = "skipped-current"
            continue

        if status == "absent":
            action = "installed"
        elif status == "managed-stale":
            action = "refreshed"
        elif status == "foreign":
            if not force:
                result[name] = "skipped-foreign"
                continue
            action = "replaced-foreign"
            if not dry_run:
                import time
                backup = hook_path.with_suffix(f".bak-omni-{int(time.time())}")
                try:
                    backup.write_text(hook_path.read_text(encoding="utf-8"), encoding="utf-8")
                except Exception as e:
                    raise HookInstallError(f"备份 {hook_path} 失败: {e}") from e
        else:
            # 理论上不会走到
            result[name] = f"unknown-status:{status}"
            continue

        if not dry_run:
            hook_path.write_text(template, encoding="utf-8")
            try:
                mode = hook_path.stat().st_mode
                hook_path.chmod(mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
            except Exception:
                pass  # Windows 上 chmod 可能无效, 但 git for windows 的 sh 能执行
        result[name] = action

    return result


def check_hooks(project_root: Path) -> dict[str, str]:
    """只检查, 不写盘. 返回 hook_name → status."""
    git_dir = _find_git_dir(project_root)
    if git_dir is None:
        return {name: "no-git" for name in _HOOK_TEMPLATES}
    hooks_dir = git_dir / "hooks"
    return {
        name: _current_hook_status(hooks_dir / name, template)
        for name, template in _HOOK_TEMPLATES.items()
    }


__all__ = [
    "MANAGED_MARKER",
    "PRE_COMMIT_TEMPLATE",
    "POST_COMMIT_TEMPLATE",
    "HookInstallError",
    "install_hooks",
    "check_hooks",
]
