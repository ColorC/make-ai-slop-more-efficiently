# [OMNI] origin=claude-code domain=services/_governance ts=2026-07-03T23:50:00+08:00 type=router
# [OMNI] material_id="material:governance.testmap.feature_test_ledger.py"
# [OMNI] summary="功能点-测试台账契约层: 加载/校验各仓 testmap.yaml、在 omnicompany 仓内与外部挂载仓发现全部 testmap、核验测试锚有效性(gap/stale/covered 三态)。只做契约与只读校验, 不做巡检调度/material 注册表接线(见上位 plan.md 分批表, 均为后续批)。"
# [OMNI] why="用户 2026-07-03 原话: 缺乏统一的功能点和对应测试管理设施——今天任务窗口复制功能在受限环境静默坏了很久, 没有任何测试或台账为它站岗。本批只落契约层三件套(加载/发现/核验), 首份真 testmap 落 dashboard 前端。"
# [OMNI] tags=governance,testmap,test-ledger,contract
"""功能点-测试台账 · 契约层。

每个业务项目自带一份 ``testmap.yaml``(真源跟随项目仓), 本模块提供:

- :func:`load_testmap` —— 加载并校验单份 testmap 文件, 格式非法抛 :class:`TestmapError`。
- :func:`discover_testmaps` —— 在 omnicompany 仓内(``src`` 下 os.walk, 跳过重目录)与外部
  挂载仓(经 ``external_mounts.list_mounted_repo_roots`` + 各仓 ``.omni-mount.yaml`` 的
  ``testmaps`` 键)两条来源发现全部 testmap; 单份加载失败进 ``errors``, app 标识撞名的
  后到者进 ``rejected``(绝不静默覆盖同名 app)。
- :func:`verify_testmap` —— 对每个功能点核验测试锚有效性, 产出 gap/stale finding。
- :func:`feature_status` —— 结合 verify 产出算每个功能点的实测三态(stale 优先于 gap)。

只登记指针、只读校验; 不做 material 注册表接线、不做巡检管线、不做 LLM 评审(均为后续批,
见上位 ``plan.md`` 「分批与验收锚」一节)。

── 巡检批(2026-07-03)追加 ─────────────────────────────────────────────────
本批补齐三件事(均在本模块底部): :func:`sync_registry`(注册表接线) /
:func:`run_gates`(门禁真跑) / :func:`build_review_task`+``REVIEW_NODE_PROMPT``+
``REVIEW_RESULT_SCHEMA``(理论覆盖再评的任务拼装, 真正的 LLM 调用留给 CLI 层调
``run_json_agent``, 本模块不引入异步依赖)。

── 标准位置留痕批(2026-07-04)追加 ─────────────────────────────────────────
:func:`collect_reminders` —— OMNI-100 提醒读侧派生: 读近 7 天 ``logs/patrol/*.json``,
按 testmap 目录子树归属 violation, 并做确定性消化判定(违规发现时间早于该 app 的
"最近测试面变更时间"即视为已消化)。真源是 patrol 日志本身(标准位置), 本模块不写
patrol 侧, 只在消费点(sync_registry / CLI show/list)呈现。``sync_registry`` 随之
在 attrs 增写 ``reminders`` 键(保持既有 verify/gates/review 合并语义)。
"""
from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from omnicompany.packages.services._core.registry import get_registry
from omnicompany.packages.services._core.registry.instance import InstanceEntry
from omnicompany.packages.services._core.registry.external_mounts import (
    list_mounted_repo_roots,
)

# Windows 隐藏子进程窗口(禁止前台跳控制台窗口铁律)。非 Windows 取 0。
_BG_FLAGS = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

# ── 发现层剪枝: 这些目录名一旦出现在路径里就整枝跳过(dashboard 前端 node_modules
#    体量巨大, 不剪枝会让 os.walk 走到天荒地老)───────────────────────────────
_SKIP_DIR_NAMES = frozenset({
    "node_modules", ".git", "__pycache__", "static", "dist",
    "coverage", "test-results", "data", ".omni", "_archive",
})

TESTMAP_FILENAME = "testmap.yaml"


class TestmapError(Exception):
    """testmap.yaml 格式非法。带来源路径与原因, 供调用方定位。"""

    def __init__(self, path: Path, reason: str) -> None:
        self.path = path
        self.reason = reason
        super().__init__(f"{path}: {reason}")


# ── 数据形状 ──────────────────────────────────────────────────────────────

@dataclass
class TestGate:
    """一条可执行的测试入口(给 AI 和巡检跑的命令)。"""

    id: str
    cmd: str
    cwd: str = "."


@dataclass
class TestAnchor:
    """一个功能点声明的实有测试锚: 文件 + 用例名字面子串列表。"""

    file: str
    cases: list[str] = field(default_factory=list)


@dataclass
class Feature:
    """一个功能点: 理论上该有什么(should) + 实有测试锚(tests)。"""

    id: str
    what: str
    should: list[str]
    tests: list[TestAnchor] = field(default_factory=list)
    status: str | None = None  # 文件里写的 status 只是缓存, verify 以实测为准


@dataclass
class Testmap:
    """一份 testmap.yaml 的加载结果。"""

    app: str
    path: Path
    base_dir: Path  # 相对路径(doc/gates.cwd/tests.file)的解析基准 = 本文件所在目录
    doc: str | None
    gates: list[TestGate]
    features: list[Feature]


@dataclass
class DiscoverResult:
    """discover_testmaps 的产出: 可用 testmap + 加载失败 + app 撞名拒绝。"""

    testmaps: list[Testmap] = field(default_factory=list)
    errors: list[dict[str, str]] = field(default_factory=list)      # {path, reason}
    rejected: list[dict[str, str]] = field(default_factory=list)    # {path, reason}


# ── load_testmap ──────────────────────────────────────────────────────────

def load_testmap(path: Path) -> Testmap:
    """加载并校验一份 testmap.yaml。校验失败抛 :class:`TestmapError`。"""
    path = Path(path)
    try:
        raw_text = path.read_text(encoding="utf-8")
    except OSError as e:
        raise TestmapError(path, f"无法读取文件: {e}") from e

    try:
        data = yaml.safe_load(raw_text)
    except yaml.YAMLError as e:
        raise TestmapError(path, f"YAML 解析失败: {e}") from e

    if not isinstance(data, dict):
        raise TestmapError(path, "顶层必须是一个映射(dict)")

    app = data.get("app")
    if not isinstance(app, str) or not app.strip():
        raise TestmapError(path, "缺少必填字段 app, 或 app 非字符串/为空")

    raw_features = data.get("features")
    if raw_features is None or not isinstance(raw_features, list):
        raise TestmapError(path, "缺少必填字段 features, 或 features 非列表")

    seen_ids: set[str] = set()
    features: list[Feature] = []
    for i, item in enumerate(raw_features):
        if not isinstance(item, dict):
            raise TestmapError(path, f"features[{i}] 必须是映射(dict)")
        fid = item.get("id")
        what = item.get("what")
        if not isinstance(fid, str) or not fid.strip():
            raise TestmapError(path, f"features[{i}] 缺少必填字段 id")
        if not isinstance(what, str) or not what.strip():
            raise TestmapError(path, f"feature '{fid}' 缺少必填字段 what")
        if fid in seen_ids:
            raise TestmapError(path, f"feature id 在文件内重复: {fid}")
        seen_ids.add(fid)

        should = item.get("should")
        if not isinstance(should, list) or len(should) == 0:
            raise TestmapError(path, f"feature '{fid}' 的 should 缺失或为空列表")

        raw_tests = item.get("tests") or []
        if not isinstance(raw_tests, list):
            raise TestmapError(path, f"feature '{fid}' 的 tests 必须是列表")
        tests: list[TestAnchor] = []
        for j, t in enumerate(raw_tests):
            if not isinstance(t, dict) or not t.get("file"):
                raise TestmapError(path, f"feature '{fid}' 的 tests[{j}] 缺少必填字段 file")
            cases = t.get("cases") or []
            tests.append(TestAnchor(file=str(t["file"]), cases=[str(c) for c in cases]))

        features.append(Feature(
            id=fid, what=what, should=[str(s) for s in should],
            tests=tests, status=item.get("status"),
        ))

    raw_gates = data.get("gates") or []
    if not isinstance(raw_gates, list):
        raise TestmapError(path, "gates 必须是列表")
    gates: list[TestGate] = []
    for g in raw_gates:
        if not isinstance(g, dict) or not g.get("id") or not g.get("cmd"):
            raise TestmapError(path, f"gates 项缺少必填字段 id/cmd: {g!r}")
        gates.append(TestGate(id=str(g["id"]), cmd=str(g["cmd"]), cwd=str(g.get("cwd", "."))))

    doc = data.get("doc")
    if doc is not None and not isinstance(doc, str):
        raise TestmapError(path, "doc 若存在必须是字符串")

    return Testmap(
        app=app, path=path, base_dir=path.parent, doc=doc,
        gates=gates, features=features,
    )


# ── discover_testmaps ─────────────────────────────────────────────────────

def _iter_repo_testmaps(src_root: Path):
    """os.walk 在 src_root 下找 testmap.yaml, 按名剪枝跳过重目录。"""
    for dirpath, dirnames, filenames in os.walk(src_root):
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIR_NAMES]
        if TESTMAP_FILENAME in filenames:
            yield Path(dirpath) / TESTMAP_FILENAME


def _iter_external_mount_testmaps(workspace_root: Path):
    """外部挂载仓: 读 .omni-mount.yaml 的可选键 testmaps(相对仓根路径列表)。

    显式把 workspace_root 对应的登记表路径传给 list_mounted_repo_roots, 使调用方传入
    的 tmp 工作区(测试常态)天然读不到本机真实 config/external_mounts.yaml, 从而不会
    把真实挂载仓(quant-lab/walker-game)误发现进 tmp 场景。真实工作区(仓根就是
    omnicompany 自己)时 <workspace_root>/config/external_mounts.yaml 与模块默认路径
    等价, 行为不变。
    """
    registry_path = Path(workspace_root) / "config" / "external_mounts.yaml"
    for repo_root in list_mounted_repo_roots(registry_path=registry_path):
        manifest_path = Path(repo_root) / ".omni-mount.yaml"
        if not manifest_path.exists():
            continue
        try:
            manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001 — 清单坏了不影响其余来源
            continue
        if not isinstance(manifest, dict):
            continue
        rel_paths = manifest.get("testmaps")
        if not isinstance(rel_paths, list):
            continue
        for rel in rel_paths:
            yield Path(repo_root) / str(rel)


def discover_testmaps(workspace_root: Path) -> DiscoverResult:
    """发现全部 testmap: omnicompany 仓内(<workspace_root>/omnicompany/src 起 walk) +
    外部挂载仓(.omni-mount.yaml 的 testmaps 键)。

    单份加载失败进 errors, 不炸整体; app 标识撞名时按路径字符串排序, 后到者进 rejected
    (先到者仍可用, 绝不静默覆盖)。
    """
    workspace_root = Path(workspace_root)
    candidate_paths: list[Path] = []

    src_root = workspace_root / "omnicompany" / "src"
    if src_root.is_dir():
        candidate_paths.extend(_iter_repo_testmaps(src_root))
    else:
        # workspace_root 本身就是 omnicompany 仓根的情况(如测试直接传仓根)。
        alt_src = workspace_root / "src"
        if alt_src.is_dir():
            candidate_paths.extend(_iter_repo_testmaps(alt_src))

    candidate_paths.extend(_iter_external_mount_testmaps(workspace_root))

    result = DiscoverResult()
    by_app: dict[str, Testmap] = {}
    # 按路径字符串排序保证确定性(先到者=排序靠前者)。
    for p in sorted(set(candidate_paths), key=lambda x: str(x)):
        try:
            tm = load_testmap(p)
        except TestmapError as e:
            result.errors.append({"path": str(p), "reason": e.reason})
            continue

        existing = by_app.get(tm.app)
        if existing is not None:
            result.rejected.append({
                "path": str(p),
                "reason": (
                    f"app '{tm.app}' 与已注册的 {existing.path} 冲突, "
                    f"后到者({p})拒绝注册, 先到者仍可用"
                ),
            })
            continue
        by_app[tm.app] = tm
        result.testmaps.append(tm)

    return result


# ── verify_testmap ────────────────────────────────────────────────────────

def verify_testmap(tm: Testmap) -> list[dict[str, Any]]:
    """对每个功能点核验测试锚有效性, 产出 finding 字典列表。

    键: app / feature_id / kind / detail。kind 取值:
      - gap: 无 tests 或 tests 为空(理论有 should, 实际无测试)。
      - stale: 锚文件不存在, 或存在但某 case 子串在文件文本里找不到。
    全部命中的功能点不产出 finding。
    """
    findings: list[dict[str, Any]] = []
    for feat in tm.features:
        if not feat.tests:
            findings.append({
                "app": tm.app, "feature_id": feat.id, "kind": "gap",
                "detail": f"未覆盖 should 共 {len(feat.should)} 条, 无任何测试锚",
            })
            continue

        feature_stale_details: list[str] = []
        for anchor in feat.tests:
            anchor_path = (tm.base_dir / anchor.file).resolve()
            if not anchor_path.exists():
                feature_stale_details.append(f"测试文件不存在: {anchor.file}")
                continue
            try:
                text = anchor_path.read_text(encoding="utf-8", errors="replace")
            except OSError as e:
                feature_stale_details.append(f"测试文件读取失败: {anchor.file}({e})")
                continue
            for case in anchor.cases:
                if case not in text:
                    feature_stale_details.append(f"用例锚在 {anchor.file} 中找不到: {case!r}")

        if feature_stale_details:
            findings.append({
                "app": tm.app, "feature_id": feat.id, "kind": "stale",
                "detail": "; ".join(feature_stale_details),
            })

    return findings


def feature_status(tm: Testmap, findings: list[dict[str, Any]]) -> dict[str, str]:
    """结合 verify_testmap 的产出算每个功能点的实测三态(stale 优先于 gap)。"""
    by_feature: dict[str, str] = {}
    for f in findings:
        if f["app"] != tm.app:
            continue
        fid = f["feature_id"]
        kind = f["kind"]
        if by_feature.get(fid) == "stale":
            continue  # 已是 stale, 不降级
        by_feature[fid] = kind

    status: dict[str, str] = {}
    for feat in tm.features:
        status[feat.id] = by_feature.get(feat.id, "covered")
    return status


# ── sync_registry(注册表接线) ─────────────────────────────────────────────

def _testmap_entity_id(app: str) -> str:
    return f"testmap:{app}"


# ── collect_reminders(OMNI-100 提醒读侧派生) ─────────────────────────────

_REMINDER_WINDOW_DAYS = 7


def _to_posix(path: str) -> str:
    return str(path).replace("\\", "/")


def _parse_iso(ts: str) -> datetime | None:
    """宽松解析 patrol 日志里的 ISO 时间戳(可能带/不带时区、逗号/点分秒)。解析失败返回 None。"""
    if not ts or not isinstance(ts, str):
        return None
    s = ts.strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _iter_recent_patrol_logs(workspace_root: Path, window_days: int = _REMINDER_WINDOW_DAYS) -> list[Path]:
    """`logs/patrol/*.json`, 按 mtime 过滤到 window_days 天内(含), 排序无关(调用方去重/排序)。"""
    patrol_dir = Path(workspace_root) / "logs" / "patrol"
    if not patrol_dir.is_dir():
        return []
    cutoff = datetime.now(timezone.utc).timestamp() - window_days * 86400
    out: list[Path] = []
    for p in patrol_dir.glob("*.json"):
        try:
            if p.stat().st_mtime >= cutoff:
                out.append(p)
        except OSError:
            continue
    return out


def _testmap_dir_rel(tm: "Testmap", workspace_root: Path) -> str:
    """该 testmap 所在目录相对 workspace_root 的 posix 路径(用作子树前缀匹配基准)。"""
    try:
        rel = tm.base_dir.resolve().relative_to(Path(workspace_root).resolve())
        return _to_posix(str(rel)) if str(rel) != "." else ""
    except ValueError:
        return _to_posix(str(tm.base_dir))


def _path_in_subtree(path: str, subtree_prefix: str) -> bool:
    """path(/ 分隔, 仓相对) 是否在 subtree_prefix 目录子树内(前缀匹配, 不误配同名兄弟目录)。"""
    p = _to_posix(path).lstrip("/")
    if not subtree_prefix:
        return True  # testmap 在仓根, 视为覆盖全仓
    prefix = subtree_prefix.rstrip("/") + "/"
    return p.startswith(prefix)


def _git_file_mtime(path: Path, workspace_root: Path) -> datetime | None:
    """该文件 git 最后提交时间(隐藏窗口子进程)。非 git 仓/无历史/子进程失败均返回 None(容错到只用 mtime)。"""
    try:
        proc = subprocess.run(
            ["git", "log", "-1", "--format=%cI", "--", str(path)],
            cwd=str(workspace_root), capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=20, creationflags=_BG_FLAGS,
        )
        if proc.returncode != 0:
            return None
        line = (proc.stdout or "").strip().splitlines()
        if not line:
            return None
        return _parse_iso(line[0].strip())
    except Exception:  # noqa: BLE001 — git 不可用时容错为只用 mtime
        return None


def _file_mtime(path: Path) -> datetime | None:
    try:
        return datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
    except OSError:
        return None


def _test_surface_changed_at(tm: "Testmap", workspace_root: Path) -> datetime | None:
    """该 app 的"最近测试面变更时间" = max(testmap.yaml 与全部登记 tests 锚文件的
    git 最后提交时间、工作区 mtime)。任一文件 git 取不到就退化为该文件 mtime;
    全部都取不到时返回 None(调用方视为"永远早", 即不过滤)。
    """
    candidate_paths: list[Path] = [tm.path]
    for feat in tm.features:
        for anchor in feat.tests:
            candidate_paths.append((tm.base_dir / anchor.file).resolve())

    times: list[datetime] = []
    for p in candidate_paths:
        git_t = _git_file_mtime(p, workspace_root)
        mtime_t = _file_mtime(p)
        for t in (git_t, mtime_t):
            if t is not None:
                times.append(t)

    if not times:
        return None
    return max(times)


def collect_reminders(tm: "Testmap", workspace_root: Path) -> list[dict[str, Any]]:
    """OMNI-100 提醒读侧派生: 近 7 天 patrol 日志里归属该 testmap 子树、且尚未被消化的
    OMNI-100 violation, 按时间倒序返回 `[{detected_at, path, message}]`。

    消化判定(确定性): violation.detected_at 早于该 app 的"最近测试面变更时间"
    (testmap.yaml 与全部登记 tests 锚文件的 git 最后提交时间/工作区 mtime 取最大值)
    → 视为已消化, 过滤掉。git 子进程失败容错为"只用 mtime"; 两者都取不到则不过滤
    (保守: 宁可多提醒不漏报)。

    去重: 同 path + detected_at(日期粒度) 只留一条。
    """
    workspace_root = Path(workspace_root)
    subtree_prefix = _testmap_dir_rel(tm, workspace_root)
    surface_changed_at = _test_surface_changed_at(tm, workspace_root)

    seen: set[tuple[str, str]] = set()
    reminders: list[dict[str, Any]] = []

    for log_path in _iter_recent_patrol_logs(workspace_root):
        try:
            payload = json.loads(log_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(payload, dict):
            continue
        scan_ts = payload.get("scan_ts")
        violations = payload.get("violations")
        if not isinstance(violations, list):
            continue

        for v in violations:
            if not isinstance(v, dict) or v.get("rule_id") != "OMNI-100":
                continue
            v_path = v.get("path")
            if not isinstance(v_path, str) or not v_path:
                continue
            if not _path_in_subtree(v_path, subtree_prefix):
                continue

            detected_at = v.get("detected_at") or scan_ts or ""
            detected_dt = _parse_iso(detected_at) if detected_at else None

            if surface_changed_at is not None and detected_dt is not None:
                if detected_dt < surface_changed_at:
                    continue  # 已消化: 台账测试面比这条提醒更晚变更过

            dedup_date = detected_dt.date().isoformat() if detected_dt is not None else str(detected_at)
            key = (_to_posix(v_path), dedup_date)
            if key in seen:
                continue
            seen.add(key)

            reminders.append({
                "detected_at": detected_at,
                "path": v_path,
                "message": v.get("message", ""),
            })

    reminders.sort(key=lambda r: r.get("detected_at") or "", reverse=True)
    return reminders


def sync_registry(workspace_root: Path) -> dict[str, Any]:
    """discover + verify 全量跑一遍, 逐份 testmap 写一条 InstanceEntry(type=testmap)。

    已有条目更新(InstanceRegistry.write 幂等覆盖语义); attrs 里既有的 gates/review
    键保留合并(不被本函数抹掉 —— 门禁真跑与理论再评结果各自写在同一条目的 attrs 下)。
    本批新增 attrs.reminders(collect_reminders 结果), 同样走保留合并语义。

    Returns: {"written": [app,...], "errors": [...], "rejected": [...], "counts": {app: {...}}}
    """
    workspace_root = Path(workspace_root)
    result = discover_testmaps(workspace_root)
    registry = get_registry(workspace_root / "data" / "services" / "registry")

    written: list[str] = []
    counts_by_app: dict[str, dict[str, int]] = {}
    ts = _now_iso()

    for tm in result.testmaps:
        findings = verify_testmap(tm)
        status = feature_status(tm, findings)
        counts = {"covered": 0, "gap": 0, "stale": 0}
        for s in status.values():
            counts[s] = counts.get(s, 0) + 1

        try:
            testmap_path_rel = str(tm.path.resolve().relative_to(workspace_root.resolve()))
        except ValueError:
            testmap_path_rel = str(tm.path)

        entity_id = _testmap_entity_id(tm.app)
        existing = registry.read(entity_id)
        existing_attrs = dict(existing.attrs) if existing is not None else {}

        reminders = collect_reminders(tm, workspace_root)

        attrs: dict[str, Any] = {
            **existing_attrs,  # 保留合并: 既有的 gates/review 键不被 sync 抹掉
            "app": tm.app,
            "repo_root": str(workspace_root),
            "testmap_path": testmap_path_rel,
            "verify": {
                "ts": ts,
                "counts": counts,
                "findings": findings,
            },
            "reminders": reminders,
        }

        entry = InstanceEntry(
            entity_id=entity_id,
            type="testmap",
            name=tm.app,
            package="",
            source_file=str(tm.path.resolve()),
            attrs=attrs,
        )
        registry.write(entry)
        written.append(tm.app)
        counts_by_app[tm.app] = counts

    return {
        "written": written,
        "errors": result.errors,
        "rejected": result.rejected,
        "counts": counts_by_app,
    }


# ── run_gates(门禁真跑) ──────────────────────────────────────────────────

def _gate_log_path(workspace_root: Path, app: str, gate_id: str, ts: str) -> Path:
    safe_ts = ts.replace(":", "").replace("-", "")
    d = Path(workspace_root) / "data" / "services" / "testmap" / "gates" / app
    d.mkdir(parents=True, exist_ok=True)
    return d / f"{gate_id}-{safe_ts}.log"


def run_one_gate(tm: Testmap, gate: TestGate, workspace_root: Path,
                  timeout_s: int = 2400) -> dict[str, Any]:
    """在 <tm.base_dir>/<gate.cwd> 下真跑 gate.cmd(隐藏窗口子进程), 红必须记红。

    非零退出码/超时一律 status=red 并保留输出, 绝不吞错记绿(计划错误样本②的靶心)。
    """
    cwd = (tm.base_dir / gate.cwd).resolve()
    ran_at = _now_iso()
    start = datetime.now(timezone.utc)
    timed_out = False
    try:
        proc = subprocess.run(
            gate.cmd, shell=True, cwd=str(cwd),
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=timeout_s, creationflags=_BG_FLAGS,
        )
        exit_code = proc.returncode
        output = (proc.stdout or "") + (proc.stderr or "")
    except subprocess.TimeoutExpired as e:
        timed_out = True
        exit_code = -1
        out = e.stdout if isinstance(e.stdout, str) else (e.stdout.decode("utf-8", "replace") if e.stdout else "")
        err = e.stderr if isinstance(e.stderr, str) else (e.stderr.decode("utf-8", "replace") if e.stderr else "")
        output = (out or "") + (err or "") + f"\n[timeout] 超过 {timeout_s}s 未完成, 记 red"
    except Exception as e:  # noqa: BLE001 — 任何异常都不吞, 记 red 并保留信息
        exit_code = -1
        output = f"[error] 子进程启动失败: {type(e).__name__}: {e}"

    duration_s = (datetime.now(timezone.utc) - start).total_seconds()
    status = "green" if (exit_code == 0 and not timed_out) else "red"

    log_path = _gate_log_path(workspace_root, tm.app, gate.id, ran_at)
    log_path.write_text(output, encoding="utf-8")
    try:
        log_rel = str(log_path.resolve().relative_to(Path(workspace_root).resolve()))
    except ValueError:
        log_rel = str(log_path)

    return {
        "status": status,
        "exit_code": exit_code,
        "duration_s": round(duration_s, 3),
        "ran_at": ran_at,
        "log_path": log_rel,
    }


def run_gates(app: str | None, gate_id: str | None, workspace_root: Path,
              timeout_s: int = 2400) -> dict[str, Any]:
    """跑目标 testmap(s) 的门禁真跑; app 省略则跑全部已发现 testmap; gate_id 只跑一个。

    结果写回注册表该 app 条目的 attrs.gates[gate_id](合并语义, 保留 verify/review 键)。
    """
    workspace_root = Path(workspace_root)
    result = discover_testmaps(workspace_root)
    targets = result.testmaps
    if app is not None:
        targets = [t for t in targets if t.app == app]
        if not targets:
            return {"error": f"无此 app: {app}", "results": {}}

    registry = get_registry(workspace_root / "data" / "services" / "registry")
    all_results: dict[str, dict[str, Any]] = {}

    for tm in targets:
        gates = tm.gates
        if gate_id is not None:
            gates = [g for g in gates if g.id == gate_id]
            if not gates:
                all_results[tm.app] = {"error": f"无此 gate: {gate_id}"}
                continue

        entity_id = _testmap_entity_id(tm.app)
        existing = registry.read(entity_id)
        existing_attrs = dict(existing.attrs) if existing is not None else {}
        gates_attrs = dict(existing_attrs.get("gates") or {})

        app_results: dict[str, Any] = {}
        for g in gates:
            gate_result = run_one_gate(tm, g, workspace_root, timeout_s=timeout_s)
            gates_attrs[g.id] = gate_result
            app_results[g.id] = gate_result

        attrs = {**existing_attrs, "gates": gates_attrs}
        entry = InstanceEntry(
            entity_id=entity_id,
            type="testmap",
            name=tm.app,
            package="",
            source_file=str(tm.path.resolve()),
            attrs=attrs,
        )
        registry.write(entry)
        all_results[tm.app] = app_results

    return {"results": all_results, "errors": result.errors, "rejected": result.rejected}


# ── 理论覆盖再评(任务拼装; 真正 LLM 调用在 CLI 层用 run_json_agent) ────────

REVIEW_RESULT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["findings"],
    "properties": {
        "findings": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["kind", "evidence", "detail"],
                "properties": {
                    "kind": {
                        "type": "string",
                        "enum": ["missing_feature", "unjudgeable_should", "suspect_anchor"],
                    },
                    "feature_id": {"type": ["string", "null"]},
                    "evidence": {"type": "string"},
                    "detail": {"type": "string"},
                },
            },
        },
    },
}

REVIEW_NODE_PROMPT = """你是功能点-测试台账的评审团(只读)。给你一份 testmap.yaml 的绝对路径、
它的文档指针绝对路径(可能不存在)、以及该 testmap 目录近 30 天的 git log 摘要。
用 read_file/grep/glob/list_dir 只读工具自己去读文件核实, 别凭空猜测。

对照功能文档与近期变更审三件事(每条 finding 必须引用具体文件/行/提交作证据, 禁止输出置信度数字):
1. missing_feature —— 有没有用户可感知的新功能(从近期 git log/文档能看出来)没有登记进
   testmap 的 features 列表。
2. unjudgeable_should —— features[].should 里有没有写成不可判定句的条目(例如"应该正常工作"
   "表现良好"这类, 无法翻译成一条可执行断言的自然语言)。
3. suspect_anchor —— 登记的测试锚(tests[].file / cases)跟被指向文件的实际内容对不上
   (读了文件发现根本不是那么回事, 有幻觉疑点)。

只用 finish 返回 JSON, 形如 {"findings": [{"kind": "...", "feature_id": "...", "evidence": "...", "detail": "..."}]}。
没有 finding 就返回 {"findings": []}, 不要为了有产出而编造问题。"""


def _git_log_summary(target_dir: Path, workspace_root: Path, max_lines: int = 100) -> str:
    """近 30 天 git log 摘要(隐藏窗口, 截断到 max_lines 行以内)。仓外/无历史不报错, 返空串。"""
    try:
        proc = subprocess.run(
            ["git", "log", "--since=30 days ago", "--oneline", "--", str(target_dir)],
            cwd=str(workspace_root), capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=20, creationflags=_BG_FLAGS,
        )
        if proc.returncode != 0:
            return ""
        lines = (proc.stdout or "").splitlines()
        return "\n".join(lines[:max_lines])
    except Exception:  # noqa: BLE001 — git log 只是辅助上下文, 失败不阻断 review
        return ""


def build_review_task(tm: Testmap, workspace_root: Path) -> str:
    """拼装喂给 run_json_agent 的 task 字符串(testmap 绝对路径 + doc 指针 + git log 摘要)。"""
    doc_path = (tm.base_dir / tm.doc).resolve() if tm.doc else None
    log_summary = _git_log_summary(tm.base_dir, workspace_root)
    lines = [
        f"testmap 文件绝对路径: {tm.path.resolve()}",
        f"doc 指针绝对路径: {doc_path if doc_path else '(此 testmap 未声明 doc)'}",
        "该 testmap 目录近 30 天 git log 摘要:",
        log_summary or "(无提交记录或不在 git 仓内)",
    ]
    return "\n".join(lines)


__all__ = [
    "TESTMAP_FILENAME",
    "TestmapError",
    "TestGate",
    "TestAnchor",
    "Feature",
    "Testmap",
    "DiscoverResult",
    "load_testmap",
    "discover_testmaps",
    "verify_testmap",
    "feature_status",
    "collect_reminders",
    "sync_registry",
    "run_one_gate",
    "run_gates",
    "REVIEW_RESULT_SCHEMA",
    "REVIEW_NODE_PROMPT",
    "build_review_task",
]
