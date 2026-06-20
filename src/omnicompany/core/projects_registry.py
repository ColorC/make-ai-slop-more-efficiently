# [OMNI] origin=ai-ide ts=2026-06-12 type=infra
# [OMNI] material_id="material:core.projects_registry.py"
"""项目注册表(projects) — omnicompany 的**唯一权威**项目模型。

用户原话 (2026-06-12 /goal + 复查): 工作时第一考虑的是"我要搞的是什么内容相关的东西";
项目是用户和总控 agent 的共同入口; **本体独立于 dashboard 存放, 有唯一权威, 任何其他
位置都应该被删除**(旧 workboard 三态 lane 模型已退役)。

设计:
- 本体在 core 层(本模块), 数据在 data/registry/projects.json(老位置 data/boss_sight/
  projects.json 首次读取时自动迁移)。dashboard / CLI / 总控全是消费方。
- 每个项目绑定一个 **index 文件** (PROJECT_INDEX.md, 在项目自己的仓库根):
  YAML frontmatter(强结构: roots/entry_points/latest/quick_actions/links) + 五节正文。
  快速工作选项(绑定 skill)注册在 index 里, 本模块只存指针并在 enrich 时浮出。
- 最后活跃时间 = max(关联 plan 的 mtime, progress 时间线条目, index 文件 mtime)。
- plan 关联: plan_categories 里既可写类目前缀(如 "demogame/figma-to-prefab")也可写完整
  plan id, 匹配规则 = 精确相等 或 前缀+"/"。
- 纯模块(不依赖 FastAPI): omni project CLI 与 dashboard controlplane/projects.py 共用。
  路由挂 dashboard 进程(8210, 可自由重启), 不挂 ccdaemon。
"""

from __future__ import annotations

import json
import os
import re
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from omnicompany.core.config import omni_workspace_root
from omnicompany.packages.services._core.omnicompany.formats import PROJECT
from omnicompany.packages.services._core.omnicompany.material_events import publish_material_event

# 主分组(可自由扩展, 这里是展示顺序的默认值; 用户 2026-06-12 给的常用组)
DEFAULT_GROUPS_ORDER: list[str] = ["demogame", "omnicompany", "indie-game", "other"]
GROUP_LABELS: dict[str, str] = {
    "demogame": "demogame",
    "omnicompany": "Omnicompany",
    "indie-game": "Indie Game",
    "other": "其他",
}

_OPTIONAL_FIELDS = (
    "name", "group", "tags", "desc", "roots", "index_path", "bg", "icon",
    "plan_categories", "links", "pinned",
)

_lock = threading.Lock()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _path() -> Path:
    """唯一权威数据文件; 老位置(boss_sight 时期)存在且新位置缺失时自动迁移。"""
    new = omni_workspace_root() / "data" / "registry" / "projects.json"
    if not new.is_file():
        legacy = omni_workspace_root() / "data" / "boss_sight" / "projects.json"
        if legacy.is_file():
            new.parent.mkdir(parents=True, exist_ok=True)
            legacy.replace(new)
    return new


def assets_dir() -> Path:
    """项目背景图等生成资产目录(由 controlplane/projects.py 的 /api/project-assets 路由 serve)。"""
    new = omni_workspace_root() / "data" / "registry" / "project_assets"
    if not new.is_dir():
        legacy = omni_workspace_root() / "data" / "boss_sight" / "project_assets"
        if legacy.is_dir():
            new.parent.mkdir(parents=True, exist_ok=True)
            legacy.replace(new)
    return new


def _read() -> dict[str, Any]:
    p = _path()
    if not p.is_file():
        return {"version": 1, "projects": [], "groups_order": list(DEFAULT_GROUPS_ORDER)}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"version": 1, "projects": [], "groups_order": list(DEFAULT_GROUPS_ORDER)}
    if not isinstance(data, dict):
        return {"version": 1, "projects": [], "groups_order": list(DEFAULT_GROUPS_ORDER)}
    data.setdefault("projects", [])
    data.setdefault("groups_order", list(DEFAULT_GROUPS_ORDER))
    return data


def _write(data: dict[str, Any]) -> None:
    p = _path()
    p.parent.mkdir(parents=True, exist_ok=True)
    data["updated_at"] = _now()
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(p)


def list_projects() -> list[dict[str, Any]]:
    return list(_read().get("projects", []))


def groups_order() -> list[str]:
    return list(_read().get("groups_order", DEFAULT_GROUPS_ORDER))


def set_project(project_id: str, *, by: str = "human", **fields: Any) -> dict[str, Any]:
    """新增/更新一个项目(字段不传则保留原值)。"""
    project_id = (project_id or "").strip()
    if not project_id or not re.fullmatch(r"[a-z0-9][a-z0-9_-]*", project_id):
        raise ValueError(f"项目 id 须为小写字母/数字/中划线, 收到 {project_id!r}")
    with _lock:
        data = _read()
        projects: list[dict] = data["projects"]
        existing = next((p for p in projects if p.get("id") == project_id), None)
        if existing is None:
            item: dict[str, Any] = {
                "id": project_id,
                "created_at": _now(),
                "updated_at": _now(),
                "updated_by": by,
            }
            projects.append(item)
        else:
            item = existing
            item["updated_at"] = _now()
            item["updated_by"] = by
        for f in _OPTIONAL_FIELDS:
            if f in fields and fields[f] is not None:
                item[f] = fields[f]
        item.setdefault("name", project_id)
        item.setdefault("group", "other")
        _write(data)
        publish_material_event(PROJECT.id, item, source="core.projects_registry")
        return dict(item)


def remove_project(project_id: str) -> bool:
    with _lock:
        data = _read()
        n0 = len(data["projects"])
        data["projects"] = [p for p in data["projects"] if p.get("id") != project_id]
        if len(data["projects"]) == n0:
            return False
        _write(data)
        publish_material_event(
            PROJECT.id,
            {"id": project_id, "deleted": True, "removed_at": _now()},
            source="core.projects_registry",
        )
        return True


# ── index 文件 (PROJECT_INDEX.md): frontmatter 解析 + 校验 ──────────────────

# index 文件 frontmatter 的必填键(强结构化契约; quick_actions/links 可为空列表)
INDEX_REQUIRED_KEYS = ("omni_project", "name", "group", "roots")

# index 解析 TTL 缓存 — index 文件常在外部盘(d:\P4 网络盘, 读一次几百 ms),
# 首页/侧栏/实体三处并发请求时若每次都现读, /api/projects 冷启动会拖到秒级(2026-06-12 实测)。
_INDEX_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}
_INDEX_TTL = 20.0


def parse_index_file_cached(index_path: str | Path) -> dict[str, Any]:
    import time as _time
    key = str(index_path)
    now = _time.time()
    hit = _INDEX_CACHE.get(key)
    if hit and hit[0] > now:
        return hit[1]
    parsed = parse_index_file(index_path)
    _INDEX_CACHE[key] = (now + _INDEX_TTL, parsed)
    return parsed


def parse_index_file(index_path: str | Path) -> dict[str, Any]:
    """解析 index 文件的 YAML frontmatter。返回 {ok, data?, error?, mtime?}。"""
    p = Path(index_path)
    if not p.is_file():
        return {"ok": False, "error": f"index 文件不存在: {p}"}
    try:
        text = p.read_text(encoding="utf-8")
    except OSError as e:
        return {"ok": False, "error": f"读失败: {e}"}
    m = re.match(r"\A---\s*\n(.*?)\n---\s*\n", text, re.DOTALL)
    if not m:
        return {"ok": False, "error": "缺少 YAML frontmatter (--- ... ---)"}
    try:
        import yaml
        fm = yaml.safe_load(m.group(1)) or {}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"frontmatter YAML 解析失败: {e}"}
    if not isinstance(fm, dict):
        return {"ok": False, "error": "frontmatter 不是映射"}
    missing = [k for k in INDEX_REQUIRED_KEYS if k not in fm]
    if missing:
        return {"ok": False, "error": f"frontmatter 缺必填键: {missing}", "data": fm}
    try:
        mtime = datetime.fromtimestamp(p.stat().st_mtime, tz=timezone.utc).isoformat()
    except OSError:
        mtime = None
    return {"ok": True, "data": fm, "mtime": mtime}


# ── 计划治理 (plan_steward 产物): plan→project 显式覆盖 + 中文标题 ──────────────
# 2026-06-12 用户: 人工/前缀分类不可靠("demogame-KB-INGEST 这种很明显放的位置不对"),
# 由治理部门(omni governance plans-run, deepseek-v4-pro)逐计划判定写覆盖表。
# 归属规则: 覆盖表里有这个计划 → 以它的 project 为准(null=不属于任何项目);
#           没有(新计划还没治理) → 退回 plan_categories 前缀规则。

_GOV_CACHE: dict[str, Any] = {"mtime": None, "data": {}}


def plan_governance_path() -> Path:
    return omni_workspace_root() / "data" / "registry" / "plan_governance.json"


def plan_governance() -> dict[str, dict[str, Any]]:
    """plan_id → {project, title_zh, ...} 覆盖表(mtime 缓存)。文件缺失返回空。"""
    p = plan_governance_path()
    try:
        mt = p.stat().st_mtime
    except OSError:
        return {}
    if _GOV_CACHE["mtime"] == mt:
        return _GOV_CACHE["data"]
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
        plans = raw.get("plans") if isinstance(raw, dict) else None
        data = plans if isinstance(plans, dict) else {}
    except (OSError, json.JSONDecodeError):
        data = {}
    _GOV_CACHE.update(mtime=mt, data=data)
    return data


def resolve_project_plans(project_id: str, cats: list[str] | None,
                          catalogue: list[dict[str, Any]],
                          gov: dict[str, dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    """项目关联计划的**唯一**归属入口(enrich 与 /api/projects/{id}/plans 共用)。"""
    g = plan_governance() if gov is None else gov
    cs = [c.strip().rstrip("/") for c in (cats or []) if c]
    out: list[dict[str, Any]] = []
    for it in catalogue:
        entry = g.get(it["id"])
        if entry is not None:
            if entry.get("project") == project_id:
                out.append(it)
            continue
        if any(it["id"] == c or it["id"].startswith(c + "/") for c in cs):
            out.append(it)
    return out


# ── enrich: 补最后活跃时间 / index 浮出 (quick_actions, links) ────────────────


def _plan_catalogue() -> list[dict[str, Any]]:
    """plan 目录全量(含嵌套, 如 demogame/figma-to-prefab/plans/*)。

    与前端 /api/plans **同一个扫描源**(controlplane/plans._scan, 自带 mtime-token 缓存) —
    2026-06-12 复查教训: 之前计数走 boss_sight 聚合器(只扫顶层)而列表走这里, 双源不一致。
    扫描器不可用时优雅退化(plan_count=0, last_active 退回 progress/index mtime)。
    """
    try:
        from omnicompany.core.plans_catalogue import _scan

        return _scan()
    except Exception:  # noqa: BLE001
        return []


def _plan_mtime_iso(folder_path: str) -> str | None:
    """单个 plan 目录的最后改动时间(目录或 plan.md 较新者)。只对匹配到的 plan 调用, 量小。"""
    try:
        d = omni_workspace_root() / folder_path
        ts = d.stat().st_mtime
        pm = d / "plan.md"
        if pm.is_file():
            ts = max(ts, pm.stat().st_mtime)
        return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()
    except OSError:
        return None


def _progress_ts_all(ref_ids: set[str]) -> list[str]:
    """progress.json 里这些 plan/project ref 的全部条目时间(近一周活跃格 + 最后活跃共用)。"""
    try:
        from omnicompany.dashboard.boss_sight.progress import list_entries
        entries = list_entries(None, None)
    except Exception:  # noqa: BLE001
        return []
    out: list[str] = []
    for e in entries:
        rid = str(e.get("ref_id") or "")
        if not any(rid == r or rid.startswith(r.rstrip("/") + "/") for r in ref_ids):
            continue
        ts = e.get("created_at") or ""
        if ts:
            out.append(ts)
    return out


# 活跃信号: 除 plan/progress/index 外, 再补「项目文件那天有改动」+「git 记录」两个信号
# (用户 2026-06-19: 只看 plan/progress 太稀疏, 真在干活的天显示成不活跃)。遍历较重, 90s 缓存。
_SKIP_DIRS = {"node_modules", ".git", "__pycache__", ".venv", "venv", "dist", "build",
              ".pytest_cache", ".mypy_cache", "caches", "snapshots"}
_ACTIVITY_CACHE: dict[str, tuple[float, list[str]]] = {}
# serve-stale-while-revalidate: 活跃度遍历(os.walk + git, 44 个项目冷算可达 ~7s)绝不阻塞请求。
# 过期/未命中时立刻返回旧值(没有就空), 后台线程刷新写回缓存。_ACTIVITY_REFRESHING 防同项目并发重刷。
_ACTIVITY_REFRESHING: set[str] = set()
_ACTIVITY_REFRESH_LOCK = threading.Lock()
# 上限 2 个后台线程: 22 个项目一次性开 22 线程会 GIL 抢占、拖慢事件循环。封顶后排队跑, 一样最终补齐。
_ACTIVITY_EXECUTOR = ThreadPoolExecutor(max_workers=2, thread_name_prefix="activity-refresh")


def _refresh_activity_async(item: dict[str, Any], pid: str) -> None:
    import time
    with _ACTIVITY_REFRESH_LOCK:
        if pid in _ACTIVITY_REFRESHING:
            return
        _ACTIVITY_REFRESHING.add(pid)

    def _work() -> None:
        try:
            roots = _project_roots(item)
            ts = _recent_file_ts(roots) + _git_commit_ts(roots)
            _ACTIVITY_CACHE[pid] = (time.time() + 90.0, ts)
        except Exception:  # noqa: BLE001 — 后台刷新失败不该影响请求; 下轮再试
            pass
        finally:
            with _ACTIVITY_REFRESH_LOCK:
                _ACTIVITY_REFRESHING.discard(pid)

    _ACTIVITY_EXECUTOR.submit(_work)


def _project_roots(item: dict[str, Any]) -> list[Path]:
    out: list[Path] = []
    for r in (item.get("roots") or []):
        try:
            p = Path(r)
            if p.exists():
                out.append(p)
        except (OSError, ValueError):
            continue
    return out


def _recent_file_ts(roots: list[Path], days: int = 8, cap: int = 3000) -> list[str]:
    """roots 下近 days 天内创建/修改过的文件 mtime(ISO)。跳过 node_modules/缓存等; 文件数封顶防大目录拖慢看板。"""
    import os
    from datetime import timedelta
    cutoff = (datetime.now(tz=timezone.utc) - timedelta(days=days)).timestamp()
    out: list[str] = []
    n = 0
    for root in roots:
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS and not d.startswith(".")]
            for fn in filenames:
                n += 1
                if n > cap:
                    return out
                try:
                    m = os.stat(os.path.join(dirpath, fn)).st_mtime
                except OSError:
                    continue
                if m >= cutoff:
                    out.append(datetime.fromtimestamp(m, tz=timezone.utc).isoformat())
    return out


_GIT_BREAKER_UNTIL = 0.0  # git 超时/失败后熔断到此刻(EDR 间歇拦子进程时, 别让看板被一串超时拖死)
# CREATE_NO_WINDOW: 禁止子进程分配新 console 窗口。8210 进程被 DETACHED_PROCESS 启动(无 console),
# 故每个 git.exe 子进程会被 Windows 分配一个新前台窗口 → 抢键盘焦点(用户硬规则: 禁止前台跳窗)。
_NO_WINDOW = 0x08000000 if os.name == "nt" else 0


def _git_commit_ts(roots: list[Path], days: int = 8) -> list[str]:
    """近 days 天内触及这些 roots 的 git 提交时间(ISO)。roots 可能跨多仓/不在仓里;
    EDR 可能拦子进程 → 短超时 + 吞异常优雅退化;一旦超时即熔断 5 分钟(退回只靠文件信号)。"""
    import subprocess
    import time
    global _GIT_BREAKER_UNTIL
    if time.time() < _GIT_BREAKER_UNTIL:
        return []
    out: list[str] = []
    seen: set[str] = set()
    for root in roots:
        try:
            top = subprocess.run(["git", "-C", str(root), "rev-parse", "--show-toplevel"],
                                 capture_output=True, text=True, timeout=3,
                                 creationflags=_NO_WINDOW)
            if top.returncode != 0:
                continue
            key = top.stdout.strip() + "::" + str(root)
            if key in seen:
                continue
            seen.add(key)
            r = subprocess.run(["git", "-C", top.stdout.strip(), "log",
                                f"--since={days} days ago", "--format=%cI", "--", str(root)],
                               capture_output=True, text=True, timeout=4,
                               creationflags=_NO_WINDOW)
            if r.returncode == 0:
                out.extend(ln.strip() for ln in r.stdout.splitlines() if ln.strip())
        except subprocess.TimeoutExpired:
            _GIT_BREAKER_UNTIL = time.time() + 300.0  # 熔断 5 分钟, 余下项目本轮也跳过 git
            return out
        except (subprocess.SubprocessError, OSError, ValueError):
            continue
    return out


def _activity_signals(item: dict[str, Any]) -> list[str]:
    """文件改动 + git 记录信号。serve-stale-while-revalidate: 请求永不阻塞在遍历上。
    新鲜命中直接返回; 过期/未命中返回旧值(没有就空)并后台刷新 —— 首屏(冷)秒开, 活跃度几秒内补齐。"""
    import time
    pid = str(item.get("id") or "")
    now = time.time()
    cached = _ACTIVITY_CACHE.get(pid)
    if cached and cached[0] > now:
        return cached[1]
    _refresh_activity_async(item, pid)
    return cached[1] if cached else []


def _activity_7d(ts_list: list[str]) -> list[bool]:
    """近 7 天逐日活跃布尔条(本地时区, 旧→新, 末位=今天)。用户 2026-06-12: 比内容数重要。"""
    from datetime import timedelta
    days: set[Any] = set()
    for ts in ts_list:
        try:
            days.add(datetime.fromisoformat(ts).astimezone().date())
        except (ValueError, TypeError):
            continue
    today = datetime.now().astimezone().date()
    return [(today - timedelta(days=6 - i)) in days for i in range(7)]


def enrich_projects(fresh: bool = False) -> dict[str, Any]:
    """注册表 + 计算字段(last_active / activity_7d / 关联 plan 数 / index 浮出)。前端与总控共用。

    fresh=True 时穿透 index 解析缓存(顶栏/面板的"刷新"按钮用 — 2026-06-12 用户点刷新无感)。
    """
    if fresh:
        _INDEX_CACHE.clear()
    data = _read()
    catalogue = _plan_catalogue()
    out: list[dict[str, Any]] = []
    for p in data.get("projects", []):
        item = dict(p)
        # 防御: bg 若被 shell 路径改写污染过(MSYS 把 /api/... 改成 C:/Program Files/Git/api/...),
        # 浮出时自愈为站内路径(2026-06-12 实际事故)。
        bg = str(item.get("bg") or "")
        if "/api/project-assets/" in bg and not bg.startswith("/api/"):
            item["bg"] = "/api/project-assets/" + bg.split("/api/project-assets/")[-1]
        cats: list[str] = [c.strip().rstrip("/") for c in (p.get("plan_categories") or []) if c]
        # 关联 plan: 治理覆盖表优先, 未治理的退回前缀规则(见 resolve_project_plans)
        related = resolve_project_plans(p["id"], cats, catalogue)
        item["plan_count"] = len(related)
        candidates: list[str] = []
        for it in related:
            ts = _plan_mtime_iso(it.get("folder_path") or "")
            if ts:
                candidates.append(ts)
        candidates.extend(_progress_ts_all(set(cats) | {item["id"]}))
        candidates.extend(_activity_signals(item))  # + 文件改动 + git 记录(2026-06-19)
        idx_info: dict[str, Any] | None = None
        if p.get("index_path"):
            idx_info = parse_index_file_cached(p["index_path"])
            if idx_info.get("mtime"):
                candidates.append(idx_info["mtime"])
        item["last_active"] = max(candidates) if candidates else p.get("updated_at")
        item["activity_7d"] = _activity_7d(candidates)
        item["index_ok"] = bool(idx_info and idx_info.get("ok")) if p.get("index_path") else None
        if idx_info and idx_info.get("ok"):
            fm = idx_info["data"]
            item["quick_actions"] = fm.get("quick_actions") or []
            # links: 注册表与 index 合并(index 优先在前)
            item["links"] = (fm.get("links") or []) + (p.get("links") or [])
            item["index_latest"] = fm.get("latest") or []
        else:
            item["quick_actions"] = []
            item["index_error"] = (idx_info or {}).get("error") if p.get("index_path") else None
        out.append(item)
    # 排序: pinned 优先, 然后按 last_active 降序
    out.sort(key=lambda x: (not x.get("pinned"), x.get("last_active") or ""), reverse=False)
    out.sort(key=lambda x: x.get("last_active") or "", reverse=True)
    out.sort(key=lambda x: not x.get("pinned"))
    order = data.get("groups_order", DEFAULT_GROUPS_ORDER)
    return {
        "projects": out,
        "groups_order": order,
        "group_labels": GROUP_LABELS,
        "updated_at": data.get("updated_at"),
    }
