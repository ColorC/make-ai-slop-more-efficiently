# [OMNI] origin=claude-code domain=project_atlas ts=2026-07-04 type=health status=active
# [OMNI] summary="技能库健康巡检:解析级体检(BOM/frontmatter/name+description)+ canonical→两生效目录导出漂移检测与自动修复(仅 atlas 管理的技能)+ 正文死绝对路径只报不修。"
# [OMNI] why="canonical 真源之外还有两个生效目录(~/.claude/skills、~/.codex/skills),此前只在 approve/export 手动触发时才可能对齐,坏半库事故(手改生效目录/漏导)无持续巡检;本模块补上无人值守闭环,且明确不动非 atlas 管理的技能(如 lark-*)。"
# [OMNI] tags=project_atlas,health,skills,atlas,cron
"""project_atlas 技能库健康巡检(`omni atlas health`)。

三类 finding:
  parse    —— BOM / frontmatter 开闭 / name·description 缺失(解析级体检,全部技能都查)
  drift    —— canonical 已批准集合 vs 两个生效目录(~/.claude/skills、~/.codex/skills)缺失或内容不一致
              (仅对 canonical 里存在的名字做漂移检查; 生效目录独有的名字视为非 atlas 管理, 跳过)
  dead_ref —— canonical 正文里的绝对路径(盘符打头)在磁盘上不存在(只报不修, 语义内容不动)

--apply 时只对 drift 类做自动修复(按 canonical 重新导出覆盖), parse/dead_ref 只报告。
报告落 data/domains/project_atlas/health/health-<ts>.json。
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ._paths import DATA_ROOT, SKILLS_ROOT

HEALTH_DIR = DATA_ROOT / "health"

_CLAUDE_SKILLS = Path.home() / ".claude" / "skills"
_CODEX_SKILLS = Path.home() / ".codex" / "skills"

# 生效目录布局是扁平的 <name>/SKILL.md(无 space 子层, 见 atlas.py export)
_EXPORT_TARGETS: tuple[tuple[str, Path], ...] = (("claude", _CLAUDE_SKILLS), ("codex", _CODEX_SKILLS))

_FRONTMATTER_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*\n?", re.DOTALL)
# Windows 绝对路径: 盘符打头, 如 E:\WindowsWorkspace\... 或 E:/WindowsWorkspace/...
# 反引号包裹的路径允许空格；裸路径仍在空白和常见标点处结束，避免吞入后续叙述。
_ABS_PATH_RE = re.compile(
    r"`([A-Za-z]:[\\/][^`\r\n]+)`|"
    r"(?<![A-Za-z0-9_])([A-Za-z]:[\\/][^\s`\"'<>|*?)\]]+)"
)
# 行锚后缀: `:123` 或 `:123-456`(单文件行号/行区间), 不是路径的一部分, 判存在性前先剥掉。
_LINE_ANCHOR_SUFFIX_RE = re.compile(r":\d+(?:-\d+)?$")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class Finding:
    category: str  # parse | drift | dead_ref
    detail: str
    path: str = ""
    space: str = ""
    name: str = ""
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        d = {"category": self.category, "detail": self.detail, "path": self.path,
             "space": self.space, "name": self.name}
        if self.extra:
            d.update(self.extra)
        return d


def _iter_canonical() -> list[tuple[str, str, Path]]:
    """canonical(SKILLS_ROOT)下 (space, name, SKILL.md 路径) 列表; 按 space/name 排序。"""
    out: list[tuple[str, str, Path]] = []
    if not SKILLS_ROOT.is_dir():
        return out
    for space_dir in sorted(p for p in SKILLS_ROOT.iterdir() if p.is_dir()):
        for obj_dir in sorted(p for p in space_dir.iterdir() if p.is_dir()):
            sk = obj_dir / "SKILL.md"
            if sk.is_file():
                out.append((space_dir.name, obj_dir.name, sk))
    return out


def _iter_flat_export(root: Path) -> list[tuple[str, Path]]:
    """生效目录(扁平 <name>/SKILL.md)下 (name, SKILL.md 路径) 列表。"""
    out: list[tuple[str, Path]] = []
    if not root.is_dir():
        return out
    for obj_dir in sorted(p for p in root.iterdir() if p.is_dir()):
        sk = obj_dir / "SKILL.md"
        if sk.is_file():
            out.append((obj_dir.name, sk))
    return out


def _has_bom(raw: bytes) -> bool:
    return raw.startswith(b"\xef\xbb\xbf")


def _check_parse(space: str, name: str, sk: Path) -> list[Finding]:
    """解析级体检: UTF-8 无 BOM、frontmatter 开闭齐(80 行内)、name/description 非空。"""
    findings: list[Finding] = []
    try:
        raw = sk.read_bytes()
    except OSError as e:
        findings.append(Finding("parse", f"读取失败: {e}", str(sk), space, name))
        return findings
    if _has_bom(raw):
        findings.append(Finding("parse", "含 UTF-8 BOM(需无 BOM)", str(sk), space, name))
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as e:
        findings.append(Finding("parse", f"非合法 UTF-8: {e}", str(sk), space, name))
        return findings

    lines = text.splitlines()
    head = lines[:80]
    open_idx = next((i for i, ln in enumerate(head) if ln.strip() == "---"), None)
    close_idx = None
    if open_idx is not None:
        close_idx = next((i for i in range(open_idx + 1, len(head)) if head[i].strip() == "---"), None)
    if open_idx is None or close_idx is None:
        findings.append(Finding("parse", "frontmatter 未在 80 行内开闭齐全(缺 `---` 起止)", str(sk), space, name))
        return findings

    fm_text = "\n".join(head[open_idx + 1:close_idx])
    m_name = re.search(r"^name:\s*(.*)$", fm_text, re.MULTILINE)
    m_desc = re.search(r"^description:\s*(.*)$", fm_text, re.MULTILINE)
    name_val = (m_name.group(1).strip() if m_name else "")
    if not name_val:
        findings.append(Finding("parse", "frontmatter name 缺失/空", str(sk), space, name))

    # description 常用 `>-`/`>`/`|`/`|-` 折行块标量, 真值在下一行起的缩进块里;
    # 单行捕获值若本身就是块标量指示符, 不算真内容, 一律再扫后续块。
    _BLOCK_SCALAR_INDICATORS = {"", ">-", ">", "|", "|-", "|+", ">+"}
    if not m_desc:
        findings.append(Finding("parse", "frontmatter description 缺失/空", str(sk), space, name))
    else:
        inline_val = m_desc.group(1).strip()
        if inline_val and inline_val not in _BLOCK_SCALAR_INDICATORS:
            desc_empty = False
        else:
            start = m_desc.end()
            rest = fm_text[start:]
            block_lines = []
            for ln in rest.splitlines():
                if re.match(r"^[A-Za-z_][\w-]*:\s*", ln):
                    break
                block_lines.append(ln.strip())
            desc_empty = not any(block_lines)
        if desc_empty:
            findings.append(Finding("parse", "frontmatter description 缺失/空", str(sk), space, name))
    return findings


def _content_hash(p: Path) -> str:
    try:
        return hashlib.sha256(p.read_bytes()).hexdigest()
    except OSError:
        return ""


def _check_drift(canonical: list[tuple[str, str, Path]]) -> list[Finding]:
    """canonical 已批准集合 vs 两个生效目录: 缺失或内容哈希不一致 → finding。

    仅对 canonical 里存在的名字做漂移检查; 生效目录里独有的名字(canonical 没有)
    视为非 atlas 管理, 绝不动、也不报漂移。
    """
    findings: list[Finding] = []
    canon_hash = {name: _content_hash(sk) for _sp, name, sk in canonical}
    canon_space = {name: sp for sp, name, sk in canonical}
    for target_name, root in _EXPORT_TARGETS:
        exported = dict(_iter_flat_export(root))
        for name, chash in canon_hash.items():
            exp_path = exported.get(name)
            sp = canon_space[name]
            if exp_path is None:
                findings.append(Finding(
                    "drift", f"{target_name} 生效目录缺失(canonical 已批准但未导出)",
                    str(root / name / "SKILL.md"), sp, name,
                    extra={"target": target_name}))
                continue
            if _content_hash(exp_path) != chash:
                findings.append(Finding(
                    "drift", f"{target_name} 生效目录内容与 canonical 不一致(导出漂移)",
                    str(exp_path), sp, name,
                    extra={"target": target_name}))
    return findings


def _path_exists_tolerant(raw_path: str) -> bool:
    """判存在性前先剥掉行锚后缀(`:123` / `:123-456`, 不是路径本身)。"""
    candidate = _LINE_ANCHOR_SUFFIX_RE.sub("", raw_path)
    try:
        return Path(candidate).exists()
    except OSError:
        return False


def _is_path_template(raw_path: str) -> bool:
    """通配符、占位符和省略路径是文档模板，不是应当存在的单个文件。"""
    if any(token in raw_path for token in ("<", ">", "{", "}", "*", "…")):
        return True
    return bool(re.search(r"(?:^|[\\/_.-])(?:YYYY|MM|DD|NNN)(?:$|[\\/_.-])", raw_path))


def _check_dead_ref(canonical: list[tuple[str, str, Path]]) -> list[Finding]:
    """canonical 正文里的绝对路径(盘符打头)存在性检查, 死路径只报不修。"""
    findings: list[Finding] = []
    for sp, name, sk in canonical:
        try:
            text = sk.read_text(encoding="utf-8-sig")
        except OSError:
            continue
        seen: set[str] = set()
        for m in _ABS_PATH_RE.finditer(text):
            raw_path = (m.group(1) or m.group(2)).rstrip(".,;:")
            # 裸路径的正则会在模板起始符前结束；这种前缀同样不是实体路径。
            following = text[m.end():m.end() + 1]
            if following in {"<", "{", "*"} or _is_path_template(raw_path):
                continue
            if raw_path in seen:
                continue
            seen.add(raw_path)
            if not _path_exists_tolerant(raw_path):
                findings.append(Finding(
                    "dead_ref", f"正文绝对路径不存在: {raw_path}", str(sk), sp, name,
                    extra={"dead_path": raw_path}))
    return findings


def _apply_drift_fixes(drift_findings: list[Finding]) -> dict[str, int]:
    """按 canonical 重新导出覆盖(复用 atlas export 的落盘逻辑: 直接覆盖拷贝 SKILL.md)。"""
    import shutil

    canon_by_name = {name: sk for _sp, name, sk in _iter_canonical()}
    n = 0
    for f in drift_findings:
        sk = canon_by_name.get(f.name)
        if sk is None:
            continue
        dst = Path(f.path)
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(sk, dst)
        n += 1
    return {"repaired": n}


def run_health(apply_fix: bool = False) -> dict[str, Any]:
    """跑一轮技能库健康巡检, 可选 --apply 自动修复导出漂移。返回报告 dict 并落盘。"""
    HEALTH_DIR.mkdir(parents=True, exist_ok=True)
    canonical = _iter_canonical()

    all_skills: list[tuple[str, str, Path]] = list(canonical)
    seen_paths = {str(sk) for _sp, _n, sk in canonical}
    for _target_name, root in _EXPORT_TARGETS:
        for name, sk in _iter_flat_export(root):
            if str(sk) in seen_paths:
                continue
            seen_paths.add(str(sk))
            all_skills.append(("", name, sk))

    parse_findings: list[Finding] = []
    for sp, name, sk in all_skills:
        parse_findings.extend(_check_parse(sp, name, sk))

    drift_findings = _check_drift(canonical)
    dead_ref_findings = _check_dead_ref(canonical)

    repair_result: dict[str, int] = {"repaired": 0}
    if apply_fix and drift_findings:
        repair_result = _apply_drift_fixes(drift_findings)

    all_findings = parse_findings + drift_findings + dead_ref_findings
    payload = {
        "kind": "atlas_health",
        "generated_at": _now(),
        "canonical_count": len(canonical),
        "scanned_skill_files": len(all_skills),
        "counts": {
            "parse": len(parse_findings),
            "drift": len(drift_findings),
            "dead_ref": len(dead_ref_findings),
        },
        "findings": [f.to_dict() for f in all_findings],
        "applied": apply_fix,
        "repair": repair_result,
    }
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out = HEALTH_DIR / f"health-{ts}.json"
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    payload["_written"] = str(out)
    return payload


def latest_health_report() -> dict[str, Any] | None:
    """给 `omni atlas list` 摘要行读: 最新一份健康巡检报告(按文件名时间戳取最大)。"""
    if not HEALTH_DIR.is_dir():
        return None
    reports = sorted(HEALTH_DIR.glob("health-*.json"))
    if not reports:
        return None
    try:
        return json.loads(reports[-1].read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


__all__ = ["run_health", "latest_health_report", "HEALTH_DIR"]
