"""项目落盘:每载体一份 pretty-JSON,文件夹组织,可 git diff(P0 取舍 4)。

原子写(temp+replace)防写一半损坏。读时缺文件按默认空处理(渐进补全友好)。
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict

from .models import Project

# 顶层单对象载体 → 各自一份文件
_SINGLE = {
    "meta": "meta",
    "premise": "premise",
    "arc": "arc",
    "meta_progress": "meta_progress",
    "audience": "audience",
    "background": "background",
}
# 列表载体 → 各自一份文件(文件内是数组)
_LISTS = [
    "reveal_layers", "world", "characters", "relationships",
    "variables", "stat_blocks", "pressures", "failure_levels",
    "beats", "storylines", "pacing",
    "nodes", "connections", "endings",
    "scenes", "prose_lines", "voices", "registers", "style_matrix",
    "tags", "notes",
    "game_texts", "rejected_archive", "comments",
]


def _dump(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    try:
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass


def _model_dump(project: Project) -> Dict[str, Any]:
    if hasattr(project, "model_dump"):
        return project.model_dump(by_alias=True, mode="json")
    return project.dict(by_alias=True)


def save_project(project: Project, root: str | os.PathLike) -> None:
    """把 project 拆成每载体一份 JSON 写到 root/。"""
    root = Path(root)
    data = _model_dump(project)
    for field, fname in _SINGLE.items():
        _atomic_write(root / f"{fname}.json", _dump(data.get(field, {})))
    for field in _LISTS:
        _atomic_write(root / f"{field}.json", _dump(data.get(field, [])))


def load_project(root: str | os.PathLike) -> Project:
    """从 root/ 读回 project;缺文件用默认空。"""
    root = Path(root)
    payload: Dict[str, Any] = {}
    for field, fname in _SINGLE.items():
        p = root / f"{fname}.json"
        if p.exists():
            payload[field] = json.loads(p.read_text(encoding="utf-8"))
    for field in _LISTS:
        p = root / f"{field}.json"
        if p.exists():
            payload[field] = json.loads(p.read_text(encoding="utf-8"))
    if "meta" not in payload:
        # 容错:无 meta 时从目录名兜底
        payload["meta"] = {"id": root.name, "name": root.name}
    if hasattr(Project, "model_validate"):
        return Project.model_validate(payload)
    return Project.parse_obj(payload)


def project_exists(root: str | os.PathLike) -> bool:
    return (Path(root) / "meta.json").exists()


# --------------------------------------------------------------------------- #
# 修订快照 / 还原(与"人工变体"正交的时间维度回溯)
# --------------------------------------------------------------------------- #
_HISTORY_DIR = ".history"
_HISTORY_CAP = 40


def snapshot(root: str | os.PathLike, ts: str) -> None:
    """把当前 root 下的载体 JSON 复制进 .history/<ts>/。ts 由调用方给(避免库内取时间)。"""
    root = Path(root)
    if not project_exists(root):
        return
    dst = root / _HISTORY_DIR / ts
    dst.mkdir(parents=True, exist_ok=True)
    for p in root.glob("*.json"):
        dst.joinpath(p.name).write_text(p.read_text(encoding="utf-8"), encoding="utf-8")
    _prune_history(root)


def list_history(root: str | os.PathLike) -> list[str]:
    """返回历史快照时间戳(新→旧)。"""
    h = Path(root) / _HISTORY_DIR
    if not h.exists():
        return []
    return sorted((d.name for d in h.iterdir() if d.is_dir()), reverse=True)


def restore(root: str | os.PathLike, ts: str) -> bool:
    """把 .history/<ts>/ 还原成当前(覆盖根下载体 JSON)。成功返回 True。"""
    src = Path(root) / _HISTORY_DIR / ts
    if not src.exists():
        return False
    for p in src.glob("*.json"):
        _atomic_write(Path(root) / p.name, p.read_text(encoding="utf-8"))
    return True


# --------------------------------------------------------------------------- #
# 具名版本(与自动 history 正交:人工保存的命名快照,可激活/对照)
# --------------------------------------------------------------------------- #
_VERSIONS_DIR = ".versions"


def save_version(root: str | os.PathLike, name: str) -> None:
    """把当前工作态另存为具名版本 .versions/<name>/。"""
    root = Path(root)
    dst = root / _VERSIONS_DIR / name
    dst.mkdir(parents=True, exist_ok=True)
    for p in root.glob("*.json"):
        dst.joinpath(p.name).write_text(p.read_text(encoding="utf-8"), encoding="utf-8")


def list_versions(root: str | os.PathLike) -> list[str]:
    d = Path(root) / _VERSIONS_DIR
    if not d.exists():
        return []
    return sorted(x.name for x in d.iterdir() if x.is_dir())


def load_version(root: str | os.PathLike, name: str) -> "Project | None":
    src = Path(root) / _VERSIONS_DIR / name
    if not src.exists():
        return None
    return load_project(src)


def activate_version(root: str | os.PathLike, name: str) -> bool:
    """把具名版本覆盖成当前工作态(覆盖前自有快照可经 snapshot 留底)。"""
    src = Path(root) / _VERSIONS_DIR / name
    if not src.exists():
        return False
    for p in src.glob("*.json"):
        _atomic_write(Path(root) / p.name, p.read_text(encoding="utf-8"))
    return True


def _prune_history(root: Path) -> None:
    snaps = list_history(root)
    for old in snaps[_HISTORY_CAP:]:
        d = root / _HISTORY_DIR / old
        for p in d.glob("*.json"):
            p.unlink(missing_ok=True)
        try:
            d.rmdir()
        except OSError:
            pass
