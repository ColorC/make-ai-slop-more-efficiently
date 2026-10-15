# [OMNI] origin=claude-code domain=dashboard/boss_sight/services ts=2026-06-21T00:00:00Z type=service
# [OMNI] material_id="material:dashboard.boss_sight.services.agent_registry.py"
"""机器级 agent 注册表 — 给本机每个在跑的对话一个统一身份。

用户(2026-06-21): 所有本机在跑的 Omnicompany agent / claude code / codex 对话, 都该在
Omnicompany 注册, 带:
  - 身份标识: 项目-角色-名字 (如 "omnidashboard-os-核心开发-约翰"), 隔段更新
  - 运行位置: vscode / codex桌面 / vscode-powershell / 桌面powershell / managed-shell / omni-web ...
  - 当前在做(主) / 最初要做(次)
被动收集为主: 检测到对话在跑但没注册就主动补; agent 也能主动查/改自己身份。

数据源(全复用, 不另扫一遍):
  - import_routes.scan_claude/scan_codex  → 本机所有 claude/codex 对话 {provider,session_id,cwd,mtime,preview}
  - agent_digest.get_digest                 → 便宜模型维护的 {project,plan,title,last_step} = 当前在做
  - data/cc_sessions.json                   → dashboard 托管窗格 {active_plan, pty_id, caller_identity, alive, kind}

身份各段派生策略(确定性, 列表不烧 token):
  - 名字: 按 session_id 稳定散列从名字池取, 一经分配写库, 之后不变。
  - 角色/项目: 项目只接受会话绑定或内容摘要，随摘要更新而漂移；cwd 仅是位置元数据。
  - 位置: 从 cc_sessions 托管态 + provider 启发式判, 可被 `omni agents update` 纠正(用户原话: 位置可被动收集/纠正)。
名字一经 override / 派生即固定; 其余每次 rebuild 重算, 但 _pin 字段(用户/agent 手动改过的)永不被覆盖。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from omnicompany.core.config import omni_workspace_root
from omnicompany.dashboard.ccdaemon.api import ProviderRuntimeState
from omnicompany.packages.services.boss_sight.services.session_lineage import (
    attach_parent_titles,
    infer_session_lineage,
)

_RESIDENT_SCAN_CAP = max(
    80,
    int(os.environ.get("OMNI_RESIDENT_SCAN_CAP") or 160),
)

_PTY_RUNTIME_FIELDS = (
    "alive",
    "last_output_at",
    "provider_runtime_phase",
    "provider_runtime_marker_at",
    "provider_runtime_source",
    "provider_runtime_armed",
    "provider_runtime_latched",
)
_CCDAEMON_RUNTIME_FIELDS = (
    "alive",
    "process_alive",
    "processAlive",
    "last_output_at",
    "last_activity_at",
    "working",
    "working_known",
    "workingKnown",
    "working_source",
    "state",
    "state_evidence_at",
    "stateEvidenceAt",
    "provider_runtime_state",
    "provider_runtime_marker_at",
    "provider_runtime_known",
    "observed_at",
    "observedAt",
    "source",
)

# 名字池 — 朴素人名风(用户示例"约翰"), 中西混搭, 稳定散列分配。
_NAMES = [
    "约翰", "阿尔法", "老张", "小满", "墨菲", "图灵", "香农", "阿甘", "老李", "卡门",
    "诺亚", "林克", "奥古", "毕方", "青鸟", "子规", "罗恩", "汉娜", "马可", "薇拉",
    "高斯", "费曼", "艾达", "瓦特", "焦耳", "居里", "牛顿", "开普", "布尔", "莱布",
    "老王", "小柯", "阿喵", "大雄", "三毛", "九章", "洛书", "河图", "鲁班", "墨子",
    # 大幅扩展(用户 2026-06-22: 建议中文名, 短又好组合)。两字中文名。
    "子轩", "浩然", "宇航", "梓涵", "思远", "雨桐", "一鸣", "沐宸", "清欢", "知秋",
    "见微", "长卿", "子默", "望舒", "怀瑾", "流景", "南风", "西洲", "归舟", "听澜",
    "砚青", "墨白", "时砚", "司南", "北辰", "凌霄", "扶苏", "青砚", "霁月", "明轩",
    "白术", "苍术", "云深", "雾隐", "松烟", "竹影", "梅雪", "兰舟", "桂枝", "柳眠",
    "星河", "辰晞", "暮野", "晓川", "初霁", "未央", "长歌", "短笛", "拾光", "枕书",
    "停云", "落霞", "孤鹜", "秋水", "长天", "霜降", "白露", "惊蛰", "谷雨", "立春",
    "立秋", "冬至", "夏至", "春分", "清明", "芒种", "处暑", "寒露", "大雪", "雨水",
    "阿木", "阿火", "阿金", "阿楠", "阿土", "小寒", "小雪", "小川", "小野", "小舟",
    "明远", "致远", "行远", "笃行", "守拙", "守一", "抱朴", "葆真", "归真", "返璞",
    "观澜", "听雨", "煮茶", "种竹", "焙山", "钓雪", "看山", "数星", "补天", "炼石",
    "子期", "伯牙", "钟仪", "庄周", "惠施", "列御", "杨朱", "公输", "扁鹊", "华佗",
    "张衡", "祖冲", "沈括", "毕昇", "蔡伦", "李冰", "都江", "灵渠", "敦煌", "楼兰",
    "燕然", "瀚海", "天山", "昆仑", "蓬莱", "方丈", "瀛洲", "归墟", "若木", "扶桑",
    "玄武", "朱雀", "白虎", "青龙", "腾蛇", "勾陈", "应龙", "烛龙", "穷奇", "梼杌",
    "阿罗", "阿七", "阿九", "阿十", "小六", "小八", "老幺", "石头", "栓柱", "铁蛋",
    "丫丫", "团团", "圆圆", "豆豆", "毛豆", "青禾", "稻香", "南瓜", "冬瓜", "瓜瓜",
    "玻色", "费米", "夸克", "缪子", "中微", "希格", "傅立", "拉普", "欧拉", "黎曼",
    "子衿", "青衿", "苏堤", "白堤", "断桥", "雷峰", "灵隐", "孤山", "西泠", "平湖",
]

# 角色关键词 → 角色名(命中即取, 顺序即优先级)。
_ROLE_RULES = [
    (("qa", "测试", "跑测", "test"), "测试"),
    (("调研", "research", "report", "选型", "report"), "调研"),
    (("简历", "resume", "作品集", "portfolio", "约稿", "文案", "写作"), "内容"),
    (("dashboard", "驾驶舱", "canvas", "omnidashboard", "waiela", "看板", "overlay"), "核心开发"),
    (("配表", "config", "live", "数值", "quant", "策划"), "数值"),
    (("治理", "governance", "guardian", "注册", "registry"), "治理"),
]

def _store_path() -> Path:
    return omni_workspace_root() / "data" / "boss_sight" / "agent_registry.json"


def load_registry() -> dict[str, dict[str, Any]]:
    p = _store_path()
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
        return raw if isinstance(raw, dict) else {}
    except (OSError, ValueError):
        # ValueError 同时覆盖 JSONDecodeError 与 UnicodeDecodeError: 缓存文件损坏
        # (如非原子写入遗留垃圾尾, 2026-08-02 active-bindings 500 事故) 时回落空表,
        # 由后续 rebuild 再生, 不向 API 层传播 500。
        return {}


def _save_registry(store: dict[str, dict[str, Any]]) -> None:
    p = _store_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(store, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(p)


def _key(provider: str | None, session_id: str | None) -> str:
    return f"{provider}:{session_id}"


def _stable_name(key: str, taken: set[str]) -> str:
    """按 key 稳定散列取名; 撞名就线性探测下一个, 保证同一会话永远同名。"""
    h = 0
    for ch in key:
        h = (h * 131 + ord(ch)) & 0xFFFFFFFF
    n = len(_NAMES)
    for i in range(n):
        cand = _NAMES[(h + i) % n]
        if cand not in taken:
            return cand
    # 名字池用尽: 带序号
    return f"{_NAMES[h % n]}{h % 97}"


def _derive_role(blob: str) -> str:
    low = blob.lower()
    for keys, role in _ROLE_RULES:
        if any(k in low for k in keys):
            return role
    return "开发"


def _derive_project(digest_project: str, cwd: str = "") -> str:
    """Return only a content-derived project label.

    ``cwd`` remains in the signature for callers and historical fixtures, but
    it is deliberately ignored. A directory is where a runtime happens to
    execute; it is not evidence that the conversation belongs to that project.
    Unresolved conversations stay unbound until the companion has enough
    user/assistant content to propose a registered project.
    """
    value = str(digest_project or "").strip()
    return "" if value in {"", "无", "信息不足", "未归属", "未绑定", "未知项目"} else value


def _classify_location(item: dict[str, Any], managed: dict[str, Any] | None) -> str:
    """位置启发式; 可被 update 覆盖。managed = cc_sessions.json 里同 key 的托管记录。"""
    if managed:
        kind = (managed.get("kind") or "").lower()
        caller = (managed.get("caller_identity") or "").lower()
        if kind == "controller" or caller == "controller":
            return "omni-web"
        # ccdaemon 托管且有 pty → dashboard managed pane
        if managed.get("pty_id") or managed.get("id"):
            return "managed-shell"
    return "codex桌面" if item.get("provider") == "codex" else "vscode"


def _running(item: dict[str, Any], managed: dict[str, Any] | None, now: float) -> bool:
    if managed is not None:
        if "process_alive" in managed:
            return managed.get("process_alive") is True
        if managed.get("alive"):
            return True
    # transcript mtime 在最近 5 分钟内算"在跑"
    mt = item.get("mtime")
    try:
        return bool(mt) and (now - float(mt)) < 300
    except (TypeError, ValueError):
        return False


def _scan_convos() -> list[dict[str, Any]]:
    """复用 convos 的扫描: 本机所有 claude/codex/kiro 对话。"""
    try:
        from omnicompany.dashboard.ccdaemon.api import (
            scan_claude,
            scan_codex,
            scan_kiro,
            scan_qoder,
        )
        return list(scan_claude(cap=_RESIDENT_SCAN_CAP)) + list(
            scan_codex(cap=_RESIDENT_SCAN_CAP)
        ) + list(scan_kiro(cap=_RESIDENT_SCAN_CAP)) + list(
            scan_qoder(cap=_RESIDENT_SCAN_CAP)
        )
    except Exception:  # noqa: BLE001
        return []


def _load_ccdaemon_pty_runtime() -> dict[str, dict[str, Any]]:
    """Read the live ccdaemon PTY projection without host credentials."""
    port_path = omni_workspace_root() / "data" / "cc_daemon.port"
    try:
        port = int(port_path.read_text(encoding="utf-8").strip())
        if not 1 <= port <= 65535:
            return {}
        url = f"http://127.0.0.1:{port}/cc/tab-states"
        with urllib.request.urlopen(url, timeout=0.75) as response:  # noqa: S310
            payload = json.load(response)
    except (OSError, ValueError, json.JSONDecodeError, urllib.error.URLError):
        return {}

    items = payload.get("pty") if isinstance(payload, dict) else None
    if not isinstance(items, list):
        return {}
    out: dict[str, dict[str, Any]] = {}
    for item in items:
        if not isinstance(item, dict) or not item.get("id"):
            continue
        out[str(item["id"])] = {
            field: item[field]
            for field in _CCDAEMON_RUNTIME_FIELDS
            if field in item
        }
    return out


def _load_pty_host_runtime(pty_id: str) -> dict[str, Any]:
    """Read the newest persisted host runtime using a safe field allowlist."""
    host_dir = omni_workspace_root() / "data" / "pty_hosts"
    generations = sorted(
        host_dir.glob(f"{pty_id}.state.*.json"),
        reverse=True,
    )
    candidates = [*generations, host_dir / f"{pty_id}.json"]
    for path in candidates:
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(raw, dict):
            continue
        return {field: raw[field] for field in _PTY_RUNTIME_FIELDS if field in raw}
    return {}


def _load_cc_sessions() -> dict[str, dict[str, Any]]:
    """Index managed sessions by native provider id and overlay runtime state."""
    p = omni_workspace_root() / "data" / "cc_sessions.json"
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}

    if isinstance(raw, dict) and isinstance(raw.get("sessions"), dict):
        session_items = list(raw["sessions"].items())
    elif isinstance(raw, dict):
        session_items = list(raw.items())
    elif isinstance(raw, list):
        session_items = [(None, item) for item in raw]
    else:
        return {}

    live_runtime = _load_ccdaemon_pty_runtime()
    out: dict[str, dict[str, Any]] = {}
    for storage_id, session in session_items:
        if not isinstance(session, dict):
            continue
        managed = dict(session)
        # Registry consumers do not need detached-host connection credentials.
        managed.pop("host_token", None)
        managed.pop("host_port", None)
        pty_id = str(
            managed.get("pty_id")
            or managed.get("id")
            or storage_id
            or ""
        )
        if pty_id:
            managed["id"] = pty_id
            managed["pty_id"] = pty_id
            managed.update(_load_pty_host_runtime(pty_id))
            # Live ccdaemon memory overrides a potentially stale host snapshot.
            managed.update(live_runtime.get(pty_id, {}))
        native_id = (
            managed.get("provider_session_id")
            or managed.get("claude_session_id")
            or managed.get("id")
        )
        if native_id:
            out[_key(managed.get("provider"), native_id)] = managed
    return out


def _managed_run_status(
    managed: dict[str, Any] | None,
    now: float,
) -> str | None:
    """Project provider-owned busy/idle markers into Multiagent status."""
    if not managed or not managed.get("alive"):
        return None
    if managed.get("provider_runtime_known") is True:
        return "working" if managed.get("working") else "idle"

    state = ProviderRuntimeState(str(managed.get("provider") or "shell"))
    if not state.restore(managed):
        return "unknown"
    return state.status(now=now) or "unknown"


def _load_bindings_idx() -> dict[str, dict[str, Any]]:
    """会话权威绑定台账(identity 模块 cc_session_bindings.json),按 provider:session_id 索引。

    这是"会话自己声明"的绑定(plan/project/task),权威优先于 digest/cwd 推测。失败静默。
    见 docs/plans/dashboard/[2026-07-09]SESSION-SELF-BINDING/plan.md (4.1/4.5)。
    """
    try:
        from omnicompany.packages.services._core.identity import bindings_by_session_key
        return bindings_by_session_key() or {}
    except Exception:  # noqa: BLE001
        return {}


def _binding_for(
    bindings: dict[str, dict[str, Any]],
    *,
    provider: str,
    native_id: str,
    pty_id: str | None = None,
) -> dict[str, Any]:
    """Join one resident to the neutral identity ledger without PTY leakage."""

    candidates = [
        f"pty:{pty_id}" if pty_id else "",
        f"session:{native_id}" if native_id else "",
        f"{provider}:{native_id}" if provider and native_id else "",
    ]
    for key in candidates:
        if not key or key not in bindings:
            continue
        candidate = bindings[key]
        if key.startswith("pty:") and native_id:
            candidate_ids = {
                str(candidate.get(field) or "").strip()
                for field in ("session_id", "claude_session_id")
                if str(candidate.get(field) or "").strip()
            }
            # A PTY can host a new native conversation after being reused.
            if candidate_ids and native_id not in candidate_ids:
                continue
        return candidate
    return {}


def _clean(text: str, n: int) -> str:
    return " ".join(str(text or "").split())[:n]


def rebuild(*, now: float | None = None) -> list[dict[str, Any]]:
    """从所有数据源重建注册表, 落库, 返回记录列表(最近活跃在前)。

    确定性派生, 不调任何模型 → 可高频调用。_pin 过的字段保留。
    """
    from omnicompany.packages.services.boss_sight.services import agent_digest

    now = now if now is not None else time.time()
    store = load_registry()
    items = _scan_convos()
    cc = _load_cc_sessions()
    binds = _load_bindings_idx()  # 会话自己声明的权威绑定 (plan/project/task)
    # The digest store is several megabytes on a real workstation.  Reuse one
    # immutable snapshot for this rebuild instead of reparsing it for every
    # resident session.
    digest_store = agent_digest.load_digests()
    taken = {r.get("name") for r in store.values() if r.get("name")}

    seen: set[str] = set()
    for item in items:
        key = _key(item.get("provider"), item.get("session_id"))
        if not item.get("session_id"):
            continue
        seen.add(key)
        prev = store.get(key, {})
        managed = cc.get(key)
        pty_id = (managed or {}).get("pty_id") or (managed or {}).get("id") or item.get("pty_id")
        # Scope is conversation-owned.  Provider-specific aliases are kept
        # only for compatibility; PTY and neutral session joins come first.
        bd = _binding_for(
            binds,
            provider=str(item.get("provider") or ""),
            native_id=str(item.get("session_id") or ""),
            pty_id=str(pty_id or "") or None,
        )
        dg = agent_digest.get_digest(
            item.get("provider", ""),
            item.get("session_id", ""),
            digest_store,
        ) or {}
        pins: set[str] = set(prev.get("_pinned", []))

        # 名字: 一经分配不变
        name = prev.get("name") or _stable_name(key, taken)
        taken.add(name)

        # project 优先级: 用户 pin > 会话权威绑定 > 内容摘要；cwd 不能产生项目。
        project = (prev["project"] if "project" in pins
                   else (bd.get("project")
                         or _derive_project(str(dg.get("project") or ""))))
        blob = f"{project} {dg.get('title','')} {dg.get('last_step','')} {item.get('cwd','')} {item.get('preview','')}"
        role = prev["role"] if "role" in pins else _derive_role(blob)
        location = prev["location"] if "location" in pins else _classify_location(item, managed)

        current = dg.get("last_step") or dg.get("title") or ""
        if dg.get("title") and dg.get("last_step"):
            current = f"{dg['title']} · {dg['last_step']}"
        initial = prev.get("initial_task") or _clean(item.get("preview", ""), 140)

        identity = f"{project}-{role}-{name}"
        process_alive = (
            managed.get("process_alive")
            if managed is not None and "process_alive" in managed
            else managed.get("alive") if managed is not None else None
        )
        working = managed.get("working") if managed is not None and "working" in managed else None
        state = (
            str(managed.get("state") or "")
            if managed is not None else ""
        ) or ("working" if working is True else "idle" if working is False else "unknown")
        rec = {
            **prev,
            "key": key,
            "provider": item.get("provider"),
            "session_id": item.get("session_id"),
            "cwd": item.get("cwd"),
            "file": item.get("file"),  # transcript 路径, 给 tail/悬浮预览读
            "name": name,
            "project": project,
            "role": role,
            "identity": identity,
            "location": location,
            # 没数字摘要时回退到首条消息(initial), 别再显示"(待补)"(用户 2026-06-22)。
            "current_task": _clean(current, 160) or initial or "进行中…",
            "initial_task": initial or "(无)",
            "running": _running(item, managed, now),
            "run_status": _managed_run_status(managed, now),
            "process_alive": process_alive,
            "working": working,
            "working_known": managed.get("working_known") if managed is not None else None,
            "state": state,
            "state_evidence_at": managed.get("state_evidence_at") if managed is not None else None,
            "observed_at": (
                managed.get("observed_at") or managed.get("observedAt")
                if managed is not None else None
            ),
            "observation_source": managed.get("source") if managed is not None else "transcript",
            "pty_id": (managed or {}).get("pty_id") or (managed or {}).get("id"),
            # active_plan / task_id: 会话权威绑定优先, 回落托管态
            "active_plan": bd.get("active_plan") or (managed or {}).get("active_plan"),
            "workstream": bd.get("workstream"),
            "task_id": bd.get("task_id"),
            # topic is a durable self-description, not a digest guess.  Keep
            # it beside project/plan so the unified agent list does not lose
            # the launch-time responsibility after a rebuild.
            "topic": bd.get("topic") or (managed or {}).get("topic") or item.get("topic"),
            "authoritative": bool(bd),  # 是否有会话自己声明的绑定(vs 纯推测)
            "mtime": item.get("mtime"),
            "_updated_ts": now,
            "updated_at": datetime.fromtimestamp(now, timezone.utc).isoformat(),
        }
        rec["origin"] = (managed or {}).get("origin") or bd.get("session_origin")
        rec["lineage"] = infer_session_lineage(rec, binding=bd, managed=managed)
        store[key] = rec

    # Some native CLI providers do not yet expose a transcript scanner here. A
    # live Dashboard PTY is still an authoritative resident and must not vanish
    # from Multiagent View merely because Kimi/CodeBuddy/OpenCode/Qoder store
    # history in a different format. Synthesize only live sessions that already
    # have a stable provider-native id; transcript-backed records above continue
    # to win whenever they exist.
    managed_providers = {
        "claude_code",
        "codex",
        "codebuddy",
        "kimi",
        "kiro",
        "opencode",
        "qoder",
    }
    for managed_key, managed in cc.items():
        provider = str(managed.get("provider") or "")
        native_id = managed.get("provider_session_id") or managed.get("claude_session_id")
        if (
            managed_key in seen
            or provider not in managed_providers
            or not managed.get("alive")
            or not native_id
        ):
            continue

        key = _key(provider, native_id)
        if key in seen:
            continue
        seen.add(key)
        prev = store.get(key, {})
        pty_id = managed.get("pty_id") or managed.get("id")
        bd = _binding_for(
            binds,
            provider=provider,
            native_id=str(native_id or ""),
            pty_id=str(pty_id or "") or None,
        )
        pins: set[str] = set(prev.get("_pinned", []))
        name = prev.get("name") or _stable_name(key, taken)
        taken.add(name)

        cwd = str(managed.get("cwd") or "")
        title = _clean(
            managed.get("display_title") or managed.get("provider_title") or "",
            160,
        )
        project = (
            prev["project"]
            if "project" in pins
            else bd.get("project") or _derive_project("")
        )
        role = (
            prev["role"]
            if "role" in pins
            else _derive_role(f"{project} {title} {cwd}")
        )
        location = (
            prev["location"]
            if "location" in pins
            else _classify_location({"provider": provider}, managed)
        )
        initial = prev.get("initial_task") or title
        mtime = (
            managed.get("last_output_at")
            or managed.get("last_submit_at")
            or managed.get("started_at")
            or now
        )
        rec = {
            **prev,
            "key": key,
            "provider": provider,
            "session_id": str(native_id),
            "cwd": cwd,
            "file": None,
            "name": name,
            "project": project,
            "role": role,
            "identity": f"{project}-{role}-{name}",
            "location": location,
            "current_task": title or initial or "\u8fdb\u884c\u4e2d\u2026",
            "initial_task": initial or "(\u65e0)",
            "running": True,
            "run_status": _managed_run_status(managed, now),
            "process_alive": managed.get("process_alive", managed.get("alive")),
            "working": managed.get("working"),
            "working_known": managed.get("working_known"),
            "state": managed.get("state") or "unknown",
            "state_evidence_at": managed.get("state_evidence_at"),
            "observed_at": managed.get("observed_at") or managed.get("observedAt") or now,
            "observation_source": managed.get("source") or "ccdaemon.tab-states",
            "pty_id": managed.get("pty_id") or managed.get("id"),
            "active_plan": bd.get("active_plan") or managed.get("active_plan"),
            "workstream": bd.get("workstream"),
            "task_id": bd.get("task_id"),
            "topic": bd.get("topic") or managed.get("topic"),
            "authoritative": bool(bd),
            "mtime": mtime,
            "_updated_ts": now,
            "updated_at": datetime.fromtimestamp(now, timezone.utc).isoformat(),
        }
        rec["origin"] = managed.get("origin") or bd.get("session_origin")
        rec["lineage"] = infer_session_lineage(rec, binding=bd, managed=managed)
        store[key] = rec

    # 这轮没扫到的旧记录 = 不在跑了, 标 running=False(否则 --running 还显示一堆陈旧"待补")。
    for k, r in store.items():
        if k not in seen:
            r["running"] = False
            r["run_status"] = "idle"

    attach_parent_titles(list(store.values()))
    _save_registry(store)
    records = list(store.values())
    records.sort(key=lambda r: (0 if r.get("running") else 1, -float(r.get("mtime") or 0)))
    return records


# ---- 被动收集: 懒触发的后台 rebuild(单飞 + 节流) ----
_rebuild_lock = threading.Lock()
_rebuild_running = False
_rebuild_process: subprocess.Popen[bytes] | None = None
_last_rebuild_ts = 0.0
_MIN_REBUILD_GAP = max(
    30.0,
    float(os.environ.get("OMNI_AGENT_REGISTRY_REFRESH_S") or 90.0),
)


def schedule_rebuild() -> None:
    """被动收集: /active 检测到对话在跑时懒触发一次后台 rebuild, 不堵调用方(失败静默)。

    用户(2026-06-21): Omnicompany 检测到对话在进行、缺注册信息时应主动收集并更新。
    rebuild 是确定性的(不调模型), 只做文件扫描, 节流到 _MIN_REBUILD_GAP 秒一次。
    """
    global _rebuild_process, _rebuild_running, _last_rebuild_ts
    now = time.time()
    with _rebuild_lock:
        if _rebuild_process is not None:
            if _rebuild_process.poll() is None:
                return
            _rebuild_process = None
            _rebuild_running = False
        if _rebuild_running or (now - _last_rebuild_ts) < _MIN_REBUILD_GAP:
            return
        try:
            # A daemon restart must not immediately repeat a fresh full scan.
            if now - _store_path().stat().st_mtime < _MIN_REBUILD_GAP:
                return
        except OSError:
            pass
        try:
            # The scan walks thousands of transcript files and parses their
            # headers.  A Python thread still competes for the dashboard
            # process GIL, so a "background" rebuild can pause the PTY event
            # loop for several seconds.  Keep the work in a separate process;
            # it writes the same atomically replaced registry snapshot.
            _rebuild_process = subprocess.Popen(
                [
                    sys.executable,
                    "-c",
                    (
                        "from omnicompany.packages.services.boss_sight.services "
                        "import agent_registry; agent_registry.rebuild()"
                    ),
                ],
                cwd=str(omni_workspace_root()),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=(
                    getattr(subprocess, "CREATE_NO_WINDOW", 0)
                    | getattr(subprocess, "DETACHED_PROCESS", 0)
                ),
            )
        except OSError:
            _rebuild_process = None
            _rebuild_running = False
            return
        _rebuild_running = True
        _last_rebuild_ts = now


def list_records(*, running_only: bool = False, limit: int | None = None,
                 rebuild_first: bool = False) -> list[dict[str, Any]]:
    records = rebuild() if rebuild_first else sorted(
        load_registry().values(),
        key=lambda r: (0 if r.get("running") else 1, -float(r.get("mtime") or 0)))
    if running_only:
        records = [r for r in records if r.get("running")]
    return records[:limit] if limit else records


def find_record(session_id: str, provider: str | None = None) -> dict[str, Any] | None:
    """自查身份: 按 session_id(可选 provider) 查快照，缺失时后台刷新。"""
    for r in list_records(rebuild_first=False):
        if r.get("session_id") == session_id and (provider is None or r.get("provider") == provider):
            return r
    # 也允许用 key 直接命中
    record = load_registry().get(session_id)
    if record is None:
        schedule_rebuild()
    return record


def update_record(key: str, fields: dict[str, Any]) -> dict[str, Any]:
    """自更新身份: 改某记录的字段并钉住(rebuild 不再覆盖这些段)。"""
    store = load_registry()
    rec = store.get(key)
    if rec is None:
        raise KeyError(key)
    pins = set(rec.get("_pinned", []))
    for f, v in fields.items():
        rec[f] = v
        if f in ("name", "project", "role", "location", "identity", "initial_task"):
            pins.add(f)
    # 重算 identity(除非被显式钉)
    if "identity" not in fields:
        rec["identity"] = f"{rec.get('project')}-{rec.get('role')}-{rec.get('name')}"
    rec["_pinned"] = sorted(pins)
    rec["_updated_ts"] = time.time()
    store[key] = rec
    _save_registry(store)
    return rec


def _extract_msg_text(obj: dict[str, Any]) -> str:
    """从一条 transcript jsonl 记录里抠出 [角色] 文本(claude/codex 格式都尽量兼容)。"""
    msg = obj.get("message") if isinstance(obj.get("message"), dict) else obj
    role = msg.get("role") or obj.get("role") or obj.get("type") or ""
    content = msg.get("content")
    text = ""
    if isinstance(content, str):
        text = content
    elif isinstance(content, list):
        parts = []
        for c in content:
            if isinstance(c, dict) and c.get("text"):
                parts.append(str(c["text"]))
            elif isinstance(c, str):
                parts.append(c)
        text = " ".join(parts)
    elif isinstance(obj.get("text"), str):
        text = obj["text"]
    text = " ".join(str(text).split())
    if not text:
        return ""
    tag = {"user": "你", "assistant": "AI", "human": "你"}.get(str(role).lower(), str(role)[:6] or "·")
    return f"[{tag}] {text[:220]}"


def recent_content(key: str, n: int = 6) -> str:
    """某对话最近 n 条消息文本(悬浮预览用)。读 transcript 文件尾部。"""
    from collections import deque

    rec = load_registry().get(key)
    if not rec or not rec.get("file"):
        return ""
    try:
        with Path(rec["file"]).open("r", encoding="utf-8", errors="replace") as fh:
            lines = deque(fh, maxlen=80)
    except OSError:
        return ""
    out: list[str] = []
    for ln in lines:
        try:
            t = _extract_msg_text(json.loads(ln))
        except (json.JSONDecodeError, ValueError):
            continue
        if t:
            out.append(t)
    return "\n".join(out[-n:])[:1400]


__all__ = ["rebuild", "schedule_rebuild", "list_records", "find_record",
           "update_record", "load_registry", "recent_content"]
