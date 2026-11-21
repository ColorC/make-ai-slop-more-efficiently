# [OMNI] origin=claude-code domain=omnicompany/guardian ts=2026-04-23T00:00:00Z type=util
# [OMNI] material_id="material:core.guardian.hygiene_whitelist_manager.implementation.py"
"""guardian.hygiene_whitelist — hygiene scan 白名单管理 (2026-04-23 第二波 §十一).

背景:
    第一波实扫 30 条真违规 + 4 候选, 其中部分是合法占位/归档 (如 `_archive/`
    下的大 db 是用户主动归档的事故快照, 要保留). 走 "一条一条 whitelist" 批量
    处置, 让 Guardian 告警清零到 <5 活跃.

设计原则:
    - 只加"合法性豁免" (permanent whitelist). 不混入"暂时忽略".
    - 支持 glob 路径, 一次白名单一整类 (如 `data/_archive/**/*.db`)
    - 白名单存 `.omni/guardian/hygiene-whitelist.json`, 跟代码一起进 git
    - 白名单产出带 OmniMark sidecar, 自己合规

文件 schema:
    [
      {
        "rule_id": "OMNI-047",
        "path_pattern": "data/services/registry/*",
        "reason": "registry 类型槽合法占位",
        "added_at": "2026-04-23T10:00:00Z",
        "added_by": "human"  # 或 "claude-code" / pipeline id
      },
      ...
    ]
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


_WHITELIST_REL = ".omni/guardian/hygiene-whitelist.json"


@dataclass
class WhitelistEntry:
    rule_id: str
    path_pattern: str
    reason: str = ""
    added_at: str = ""
    added_by: str = ""
    # 2026-04-24 新增 (plan §十二 豁免必带到期日):
    # ISO 日期 "YYYY-MM-DD". 空字符串 = 未设到期日 (会触发审计告警).
    # 架构永久豁免 (如 _archive/ 内部) 可填特殊值 "permanent" 表达永久意图.
    expires: str = ""
    # 到期时的行为建议. 默认 "re-review" (重新审查).
    # 其他值: "remove" (到期自动去豁免, 告警重现) / "extend" (通常不建议)
    on_expire: str = "re-review"


def _whitelist_path(project_root: Path | str) -> Path:
    return Path(project_root) / _WHITELIST_REL


def load_whitelist(project_root: Path | str) -> list[WhitelistEntry]:
    """加载白名单, 文件不存在返回空 list."""
    p = _whitelist_path(project_root)
    if not p.exists():
        return []
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
        if not isinstance(raw, list):
            return []
        entries: list[WhitelistEntry] = []
        for item in raw:
            if not isinstance(item, dict):
                continue
            if not item.get("rule_id") or not item.get("path_pattern"):
                continue
            entries.append(WhitelistEntry(
                rule_id=str(item["rule_id"]),
                path_pattern=str(item["path_pattern"]),
                reason=str(item.get("reason") or ""),
                added_at=str(item.get("added_at") or ""),
                added_by=str(item.get("added_by") or ""),
                expires=str(item.get("expires") or ""),
                on_expire=str(item.get("on_expire") or "re-review"),
            ))
        return entries
    except Exception as e:
        logger.warning("hygiene-whitelist 加载失败: %s", e)
        return []


def _save_whitelist(project_root: Path | str, entries: list[WhitelistEntry]) -> Path:
    """保存白名单 + 写 sidecar."""
    p = _whitelist_path(project_root)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        json.dumps([asdict(e) for e in entries], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    # sidecar — 白名单文件自己合规
    try:
        from omnicompany.core.omnimark import write_data_sidecar
        write_data_sidecar(
            p,
            written_by=f"{__name__}.{_save_whitelist.__name__}",
            source_path=__file__,
        )
    except Exception as e:
        logger.debug("hygiene-whitelist sidecar 写入失败: %s", e)
    return p


def add_whitelist_entry(
    project_root: Path | str,
    rule_id: str,
    path_pattern: str,
    reason: str = "",
    added_by: str = "human",
    expires: str = "",
    on_expire: str = "re-review",
) -> tuple[bool, str]:
    """添加一条白名单. 重复 (rule_id + path_pattern) 则更新 reason/ts.

    Returns:
        (added: bool, message: str) · added=True 表示新增, False 表示覆盖.
    """
    entries = load_whitelist(project_root)
    now = datetime.now(timezone.utc).isoformat()
    for e in entries:
        if e.rule_id == rule_id and e.path_pattern == path_pattern:
            e.reason = reason or e.reason
            e.added_at = now
            e.added_by = added_by
            if expires:
                e.expires = expires
            if on_expire:
                e.on_expire = on_expire
            _save_whitelist(project_root, entries)
            return False, f"updated: {rule_id} {path_pattern}"
    entries.append(WhitelistEntry(
        rule_id=rule_id,
        path_pattern=path_pattern,
        reason=reason,
        added_at=now,
        added_by=added_by,
        expires=expires,
        on_expire=on_expire,
    ))
    _save_whitelist(project_root, entries)
    return True, f"added: {rule_id} {path_pattern}"


def remove_whitelist_entry(
    project_root: Path | str,
    rule_id: str,
    path_pattern: str,
) -> bool:
    """删除一条白名单. 不存在返回 False."""
    entries = load_whitelist(project_root)
    filtered = [
        e for e in entries
        if not (e.rule_id == rule_id and e.path_pattern == path_pattern)
    ]
    if len(filtered) == len(entries):
        return False
    _save_whitelist(project_root, filtered)
    return True


def _compile_pattern(pattern: str) -> re.Pattern:
    """自制 glob → regex: 只把 * (任意, 跨 /) 和 ? (单字符, 跨 /) 当特殊,
    其余 (含 [ ]) 视为字面量. 避免 fnmatch 把 `[2026-04-23]` 当字符类误伤.
    """
    parts = []
    for ch in pattern:
        if ch == "*":
            parts.append(".*")
        elif ch == "?":
            parts.append(".")
        else:
            parts.append(re.escape(ch))
    return re.compile("^" + "".join(parts) + "$")


def _is_expired(entry: WhitelistEntry, today: str | None = None) -> bool:
    """判条目是否过期. expires="" 或 "permanent" 视为未过期."""
    from datetime import date
    if not entry.expires or entry.expires == "permanent":
        return False
    if today is None:
        today = date.today().isoformat()
    try:
        # ISO 字符串比较即可 (YYYY-MM-DD)
        return entry.expires < today
    except Exception:
        return False


def is_whitelisted(
    rule_id: str,
    path: str,
    whitelist: list[WhitelistEntry],
) -> Optional[WhitelistEntry]:
    """检查 (rule_id, path) 是否命中白名单且未过期. 返回第一条命中条目, 否则 None.

    过期条目 (expires < today) **不再生效**, 相当于不豁免 — 对应条目继续触发告警
    以迫使人类重审 (plan §十二 豁免到期日政策).

    path_pattern 支持 glob 风格: `*` 跨路径任意 · `?` 单字符任意.
    `[` `]` 作字面量 (不是字符类), 因实际路径常含 `[yyyy-mm-dd]` 日期戳.
    """
    # 归一化 Windows 反斜杠路径
    path = str(path).replace("\\", "/")
    for e in whitelist:
        if e.rule_id != rule_id:
            continue
        if _is_expired(e):
            continue  # 过期条目不再豁免, 强制重审
        pat = e.path_pattern.replace("\\", "/")
        if _compile_pattern(pat).match(path):
            return e
    return None


def audit_whitelist(project_root: Path | str) -> dict:
    """审计 whitelist 状态, 返回 dict {total, no_expires, expired, active}.

    用途: `omni guardian hygiene status` 汇报豁免健康度.
    无 expires 字段 = 审计警告 (应补到期日或标 permanent).
    """
    from datetime import date
    today = date.today().isoformat()
    entries = load_whitelist(project_root)
    no_expires = [e for e in entries if not e.expires]
    expired = [e for e in entries if e.expires and e.expires != "permanent" and e.expires < today]
    active = [e for e in entries if e not in no_expires and e not in expired]
    return {
        "total": len(entries),
        "no_expires": no_expires,
        "no_expires_count": len(no_expires),
        "expired": expired,
        "expired_count": len(expired),
        "active_count": len(active),
    }


__all__ = [
    "WhitelistEntry",
    "load_whitelist",
    "add_whitelist_entry",
    "remove_whitelist_entry",
    "is_whitelisted",
]
