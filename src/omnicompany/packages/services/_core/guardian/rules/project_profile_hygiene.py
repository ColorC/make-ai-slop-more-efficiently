# [OMNI] origin=codex domain=omnicompany/guardian ts=2026-06-18 type=rule
"""Project hygiene profile scanner.

This module lets non-Omnicompany repositories opt into Guardian hygiene checks
without hard-coding their directory shape in Guardian itself. A project provides
`.omni/hygiene-profile.yaml`; `HygieneScanWorker` reads it and emits warnings
next to the existing OMNI hygiene rules.
"""
from __future__ import annotations

import os
import re
from pathlib import Path, PurePosixPath
from typing import Any

try:
    import yaml
except Exception:  # pragma: no cover - dependency exists in omnicompany
    yaml = None  # type: ignore[assignment]


PROFILE_REL = ".omni/hygiene-profile.yaml"

_VERSION_RE = re.compile(
    r"(^|[-_.])v\d+([-_.]|$)|"
    r"(^|[-_.])V\d+([-_.]|$)|"
    r"\.bak(\.|$)|"
    r"(^|[-_.])backup([-_.]|$)|"
    r"(^|[-_.])retry([-_.]|$)|"
    r"(^|[-_.])copy([-_.]|$)|"
    r"(^|[-_.])old([-_.]|$)|"
    r"step\d+_\d+|phase\d+_\d+",
    re.IGNORECASE,
)


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def _as_str_set(value: Any) -> set[str]:
    return {str(item) for item in _as_list(value)}


# 文件系统大小写敏感性: Windows/NTFS 不区分大小写, 闭集成员判定必须同样不区分,
# 否则 profile 登记 `WindowsWorkspace` 而工具写 `windowsworkspace\...`(同一物理目录)
# 会把已登记核心目录误判 stray 并在 enforce 模式阻断写入 (对抗测试 path-tricks 实证)。
_CASE_INSENSITIVE_FS = os.name == "nt"


def _norm_name(name: str) -> str:
    return name.lower() if _CASE_INSENSITIVE_FS else name


def _norm_set(names) -> set[str]:
    return {_norm_name(str(n)) for n in names}


def _strip_ext_len_prefix(raw: str) -> str:
    r"""去掉 Windows 扩展长度前缀 \\?\ (含 \\?\UNC\)。

    pathlib(3.12)对 \\?\E:\... 解析异常(is_absolute 时真时假 / resolve 保留或追加该前缀),
    使 relative_to(scan_root='E:\\') 全失败 → 顶层 stray 漏判(对抗测试 extended-length 绕过实证)。
    含尾随空格的中间组件经 resolve() 也会被加上该前缀, 同源。统一剥掉按普通路径处理。
    """
    s = str(raw)
    if s.startswith("\\\\?\\UNC\\"):
        return "\\\\" + s[len("\\\\?\\UNC\\"):]
    if s.startswith("\\\\?\\"):
        return s[len("\\\\?\\"):]
    return s


def _resolve_profile_root(project_root: Path, root_spec: dict[str, Any]) -> Path:
    raw = str(root_spec.get("path", "."))
    p = Path(raw)
    if not p.is_absolute():
        p = project_root / p
    return p.resolve()


def _iter_entries(root: Path) -> list[Path]:
    try:
        return sorted(root.iterdir(), key=lambda p: p.name.lower())
    except (PermissionError, OSError):
        return []


def _iter_files_and_dirs(root: Path) -> list[Path]:
    out: list[Path] = []
    stack = [root]
    while stack:
        cur = stack.pop()
        try:
            children = list(cur.iterdir())
        except (PermissionError, OSError):
            continue
        for child in children:
            out.append(child)
            if child.is_dir() and child.name not in {".git", ".venv", "venv", "node_modules"}:
                stack.append(child)
    return out


def _rel(path: Path, root: Path) -> str:
    return path.relative_to(root).as_posix()


def _path_matches(rel_path: str, pattern: str) -> bool:
    """Path-aware glob match.

    `PurePosixPath.match()` treats patterns as suffix matches, so `tmp/**` can
    unexpectedly match `var/tmp/...`. For hygiene profiles most slash patterns
    are intended to be rooted at the configured scan root. Basename-only globs
    such as `*.json` still match by filename.
    """
    rel_path = rel_path.strip("/")
    pattern = pattern.strip("/")
    if not rel_path or not pattern:
        return False
    if "/" not in pattern:
        return PurePosixPath(rel_path).match(pattern)
    if pattern.endswith("/**"):
        prefix = pattern[:-3].rstrip("/")
        if prefix.startswith("**/"):
            marker = prefix[3:]
            return rel_path == marker or rel_path.endswith("/" + marker) or f"/{marker}/" in f"/{rel_path}/"
        return rel_path == prefix or rel_path.startswith(prefix + "/")
    if pattern.startswith("**/"):
        suffix_pattern = pattern[3:]
        parts = rel_path.split("/")
        return any(
            PurePosixPath("/".join(parts[idx:])).match(suffix_pattern)
            for idx in range(len(parts))
        )
    first = pattern.split("/", 1)[0]
    if first not in {"*", "**"} and rel_path != first and not rel_path.startswith(first + "/"):
        return False
    return PurePosixPath(rel_path).match(pattern)


def _matches_any(rel_path: str, patterns: list[str]) -> bool:
    return any(_path_matches(rel_path, pat) for pat in patterns)


def _issue(
    *,
    rule_id: str,
    severity: str,
    profile_root: str,
    rel_path: str,
    message: str,
) -> dict[str, str]:
    return {
        "rule_id": rule_id,
        "severity": severity.upper(),
        "path": f"{profile_root}:{rel_path}" if rel_path else profile_root,
        "message": message,
    }


# 像"相对路径片段"的裸目录名: 在盘根/工作区根冒出这些, 几乎一定是错误 CWD 下
# 跑了相对路径 (directory_cleanliness §6 的统一根因). 单字符同理 (例 `e`).
_PATH_FRAGMENT_NAMES = frozenset({
    "e", "root", "tmp", "temp", "bin", "data", "src", "lib", "out",
    "dist", "build", "node_modules", "var", "log", "logs", "cache", "obj",
})


def _classify_root_stray(entry: Path) -> str:
    """给闭集外的顶层条目分类根因 + 给改法 (满足"找到根因"诉求).

    返回一句中文提示, 直接拼进告警 message。分三类:
      手误/错误CWD相对写 · vendored 参考仓铺顶层 · 未登记游离条目。
    """
    name = entry.name
    is_dir = False
    try:
        is_dir = entry.is_dir()
    except (PermissionError, OSError):
        pass
    # 1) 单字符 / 像路径片段的裸名字 → 手误或错误 CWD 相对写。
    #    这是【名字】信号, 与目录是否已落盘无关 —— 实时 hook 在写入【前】判, 目标常还不
    #    存在 (正是要拦的"错误CWD相对写"); 故不 gate 在 is_dir 上 (否则 pre-write 退化成"未登记")。
    if len(name) == 1 or name.lower() in _PATH_FRAGMENT_NAMES:
        return (
            f"[根因·手误或错误CWD] 像是在此根下跑了相对路径 `{name}/...` "
            f"(本该用绝对路径或先 cd 进目标项目)。查内容+创建时间定位是哪条命令/哪个 agent 写的。"
        )
    # 2) 目录内含 .git → vendored 参考仓直接铺顶层
    try:
        if is_dir and (entry / ".git").exists():
            return (
                "[根因·参考仓铺顶层] 这是一个独立 git 仓 (含 .git): 收进 `参考项目/` "
                "或所属项目的 vendor 子目录, 别占顶层。"
            )
    except (PermissionError, OSError):
        pass
    # 3) 其他 → 未登记游离条目
    return (
        "[根因·未登记] 说清它哪来的/归谁: 是某项目的私有数据被相对路径写错位置, "
        "还是该登记的新项目? 定位后归位或登记进 hygiene-profile, 否则清理。"
    )


def scan_project_profile_violations(project_root: Path) -> list[dict[str, str]]:
    """Scan `.omni/hygiene-profile.yaml` if present.

    The schema is intentionally small:
      roots.<name>.path
      roots.<name>.required_paths
      roots.<name>.allowed_root_dirs / allowed_root_files
      roots.<name>.forbidden_globs
      roots.<name>.versioned_name_scan.include/exclude
    """
    profile_path = project_root / PROFILE_REL
    if not profile_path.exists():
        return []
    if yaml is None:
        return [_issue(
            rule_id="PROJ-HYG-000",
            severity="HIGH",
            profile_root="profile",
            rel_path=PROFILE_REL,
            message="Cannot load hygiene profile because PyYAML is unavailable.",
        )]

    try:
        profile = yaml.safe_load(profile_path.read_text(encoding="utf-8")) or {}
    except Exception as exc:
        return [_issue(
            rule_id="PROJ-HYG-000",
            severity="HIGH",
            profile_root="profile",
            rel_path=PROFILE_REL,
            message=f"Cannot parse hygiene profile: {exc}",
        )]

    roots = profile.get("roots") or {}
    if not isinstance(roots, dict):
        return [_issue(
            rule_id="PROJ-HYG-000",
            severity="HIGH",
            profile_root="profile",
            rel_path=PROFILE_REL,
            message="hygiene profile field `roots` must be a mapping.",
        )]

    issues: list[dict[str, str]] = []
    for root_name, root_spec_any in roots.items():
        if not isinstance(root_spec_any, dict):
            continue
        root_spec: dict[str, Any] = root_spec_any
        scan_root = _resolve_profile_root(project_root, root_spec)
        root_label = str(root_name)
        if not scan_root.exists():
            issues.append(_issue(
                rule_id="PROJ-HYG-ROOT-MISSING",
                severity=str(root_spec.get("missing_severity", "HIGH")),
                profile_root=root_label,
                rel_path="",
                message=f"Configured hygiene root does not exist: {scan_root}",
            ))
            continue

        required_paths = [str(p) for p in _as_list(root_spec.get("required_paths"))]
        for required in required_paths:
            if not (scan_root / required).exists():
                issues.append(_issue(
                    rule_id="PROJ-HYG-REQUIRED-MISSING",
                    severity="HIGH",
                    profile_root=root_label,
                    rel_path=required,
                    message=f"Required path is missing under {root_label}: {required}",
                ))

        allowed_dirs = _norm_set(_as_str_set(root_spec.get("allowed_root_dirs")))
        allowed_files = _norm_set(_as_str_set(root_spec.get("allowed_root_files")))
        if allowed_dirs or allowed_files:
            for entry in _iter_entries(scan_root):
                if entry.is_dir():
                    if _norm_name(entry.name) not in allowed_dirs:
                        issues.append(_issue(
                            rule_id="PROJ-HYG-ROOT-CLOSED-SET",
                            severity="MEDIUM",
                            profile_root=root_label,
                            rel_path=entry.name,
                            message=(
                                f"顶层目录 `{entry.name}` 不在 {root_label} 闭集内。 "
                                + _classify_root_stray(entry)
                            ),
                        ))
                elif entry.is_file() and _norm_name(entry.name) not in allowed_files:
                    issues.append(_issue(
                        rule_id="PROJ-HYG-ROOT-CLOSED-SET",
                        severity="MEDIUM",
                        profile_root=root_label,
                        rel_path=entry.name,
                        message=(
                            f"顶层文件 `{entry.name}` 不在 {root_label} 闭集内。 "
                            + _classify_root_stray(entry)
                        ),
                    ))

        # closed_set_only: 巨大外部根 (workspace/drive) 只做闭集判定 (一层 iterdir),
        # 硬跳过下面递归全树的 forbidden_globs / versioned_name_scan, 防扫炸。
        if root_spec.get("closed_set_only"):
            continue

        forbidden = root_spec.get("forbidden_globs") or []
        for item_any in _as_list(forbidden):
            if isinstance(item_any, str):
                item = {"pattern": item_any}
            elif isinstance(item_any, dict):
                item = item_any
            else:
                continue
            pattern = str(item.get("pattern", ""))
            if not pattern:
                continue
            severity = str(item.get("severity", "HIGH"))
            reason = str(item.get("reason", "forbidden by hygiene profile"))
            exclude = [str(p) for p in _as_list(item.get("exclude"))]
            for path in _iter_files_and_dirs(scan_root):
                rel_path = _rel(path, scan_root)
                if _matches_any(rel_path, exclude):
                    continue
                if _path_matches(rel_path, pattern):
                    issues.append(_issue(
                        rule_id=str(item.get("rule_id", "PROJ-HYG-FORBIDDEN-PATH")),
                        severity=severity,
                        profile_root=root_label,
                        rel_path=rel_path,
                        message=f"{rel_path}: {reason}",
                    ))

        version_scan = root_spec.get("versioned_name_scan")
        if isinstance(version_scan, dict):
            include = [str(p) for p in _as_list(version_scan.get("include") or ["**/*"])]
            exclude = [str(p) for p in _as_list(version_scan.get("exclude"))]
            severity = str(version_scan.get("severity", "MEDIUM"))
            for path in _iter_files_and_dirs(scan_root):
                rel_path = _rel(path, scan_root)
                if not _matches_any(rel_path, include):
                    continue
                if _matches_any(rel_path, exclude):
                    continue
                if _VERSION_RE.search(path.name):
                    issues.append(_issue(
                        rule_id="PROJ-HYG-VERSIONED-NAME",
                        severity=severity,
                        profile_root=root_label,
                        rel_path=rel_path,
                        message=(
                            f"{rel_path}: version/copy/backup marker in active path. "
                            "Keep the stable name in place and move old variants to an archive."
                        ),
                    ))

    return issues


# ── 实时 hook 复用接口 (2026-06-26): 让 PreToolUse 守卫复用同一份 hygiene-profile 闭集 ──
# 这两个函数供 ccdaemon lock_pretooluse hook 在【写入前】实时拦顶层 stray。永不抛(hook fail-open)。

def _load_closed_set_roots(project_root: Path) -> list[tuple[str, Path, set[str]]]:
    """读 profile, 返回 closed_set_only 根的 (label, scan_root, allowed_names)。判不了返回 []。"""
    if yaml is None:
        return []
    profile_path = project_root / PROFILE_REL
    if not profile_path.exists():
        return []
    try:
        profile = yaml.safe_load(profile_path.read_text(encoding="utf-8")) or {}
    except Exception:
        return []
    roots = profile.get("roots") or {}
    if not isinstance(roots, dict):
        return []
    out: list[tuple[str, Path, set[str]]] = []
    for name, spec in roots.items():
        if not isinstance(spec, dict) or not spec.get("closed_set_only"):
            continue
        allowed = _as_str_set(spec.get("allowed_root_dirs")) | _as_str_set(spec.get("allowed_root_files"))
        if not allowed:
            continue
        try:
            out.append((str(name), _resolve_profile_root(project_root, spec), allowed))
        except Exception:
            continue
    return out


def check_top_level_stray(target: "Path | str", project_root: "Path | str | None" = None) -> dict | None:
    """判一个【绝对路径】会不会在某 closed_set_only 根下制造顶层 stray。

    供实时 hook 在写入前复用 hygiene-profile 闭集(单一真源)。永不抛异常。
    返回 {root_label, top_name, scan_root, message} 或 None(不是 stray / 判不了)。
    """
    try:
        # 先剥 \\?\ 扩展长度前缀(剥前 + resolve 后再剥), 防 pathlib 解析异常造成漏判。
        target = Path(_strip_ext_len_prefix(str(target)))
        if not target.is_absolute():
            return None
        try:
            target = Path(_strip_ext_len_prefix(str(target.resolve())))
        except OSError:
            pass
        if project_root is None:
            from omnicompany.core.config import omni_workspace_root
            project_root = omni_workspace_root()
        project_root = Path(project_root)
        for root_label, scan_root, allowed in _load_closed_set_roots(project_root):
            try:
                rel = target.relative_to(scan_root)
            except ValueError:
                continue
            parts = rel.parts
            if not parts:
                continue
            top = parts[0]
            if _norm_name(top) in _norm_set(allowed):  # 大小写不敏感(NTFS)
                continue
            cause = _classify_root_stray(scan_root / top)
            return {
                "root_label": root_label,
                "top_name": top,
                "scan_root": str(scan_root),
                "message": f"顶层 `{top}` 不在 {root_label} 闭集内({scan_root})。 {cause}",
            }
        return None
    except Exception:
        return None


def dangerous_bash_roots(project_root: "Path | str | None" = None) -> list[Path]:
    """返回"绝不该在其下用相对路径写"的大根 = closed_set_only 根中 project_root 的【严格祖先】。

    project_root 自身(仓根)是合法工作目录, 不算危险; 其父(工作区根)、祖父(盘根)算。
    供 hook 判 Bash 的 cwd 是否危险。永不抛。
    """
    try:
        if project_root is None:
            from omnicompany.core.config import omni_workspace_root
            project_root = omni_workspace_root()
        project_root = Path(project_root).resolve()
        out: list[Path] = []
        for _label, scan_root, _allowed in _load_closed_set_roots(project_root):
            if scan_root == project_root:
                continue  # 仓根是合法工作目录
            try:
                project_root.relative_to(scan_root)  # scan_root 是 project_root 的祖先?
                out.append(scan_root)
            except ValueError:
                continue
        return out
    except Exception:
        return []
