# [OMNI] origin=claude-code domain=dashboard/boss_sight/services ts=2026-06-21T00:00:00Z type=service
# [OMNI] material_id="material:dashboard.boss_sight.services.agent_registry.py"
"""机器级 agent 注册表 — 给本机每个在跑的对话一个统一身份。

用户(2026-06-21): 所有本机在跑的 Omnicompany agent / claude code / codex 对话, 都该在
Omnicompany 注册, 带:
  - 身份标识: 项目-角色-名字 (如 "omnidashboard-os-poof核心开发-约翰"), 隔段更新
  - 运行位置: vscode / codex桌面 / vscode-powershell / 桌面powershell / poof-powershell / omni-web ...
  - 当前在做(主) / 最初要做(次)
被动收集为主: 检测到对话在跑但没注册就主动补; agent 也能主动查/改自己身份。

数据源(全复用, 不另扫一遍):
  - import_routes._scan_claude/_scan_codex  → 本机所有 claude/codex 对话 {provider,session_id,cwd,mtime,preview}
  - agent_digest.get_digest                 → 便宜模型维护的 {project,plan,title,last_step} = 当前在做
  - data/cc_sessions.json                   → omni/poof 托管窗格 {active_plan, pty_id, caller_identity, alive, kind}

身份各段派生策略(确定性, 列表不烧 token):
  - 名字: 按 session_id 稳定散列从名字池取, 一经分配写库, 之后不变。
  - 角色/项目: 从 digest + cwd 关键词派生, 随 digest 更新而漂移 (满足"隔段更新")。
  - 位置: 从 cc_sessions 托管态 + provider 启发式判, 可被 `omni agents update` 纠正(用户原话: 位置可被动收集/纠正)。
名字一经 override / 派生即固定; 其余每次 rebuild 重算, 但 _pin 字段(用户/agent 手动改过的)永不被覆盖。
"""
from __future__ import annotations

import json
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from omnicompany.core.config import omni_workspace_root

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
    (("dashboard", "驾驶舱", "poof", "omnidashboard", "waiela", "看板", "overlay"), "核心开发"),
    (("配表", "config", "live", "数值", "quant", "策划"), "数值"),
    (("治理", "governance", "guardian", "注册", "registry"), "治理"),
]

# cwd 关键词 → 项目名(digest 没给项目时的兜底)。
_PROJECT_RULES = [
    ("poof", "omnidashboard-os"),
    ("omnidashboard", "omnidashboard-os"),
    ("waiela", "waiela"),
    ("omnicompany", "omnicompany"),
    ("quant-lab", "quant-lab"),
    ("walker", "行者无乡"),
    ("webworks", "webworks"),
    ("aiworkspace", "AIWorkSpace"),
]


def _store_path() -> Path:
    return omni_workspace_root() / "data" / "boss_sight" / "agent_registry.json"


def load_registry() -> dict[str, dict[str, Any]]:
    p = _store_path()
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
        return raw if isinstance(raw, dict) else {}
    except (OSError, json.JSONDecodeError):
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


def _derive_project(digest_project: str, cwd: str) -> str:
    if digest_project and digest_project not in ("无", "信息不足", ""):
        return digest_project
    low = (cwd or "").lower()
    for frag, proj in _PROJECT_RULES:
        if frag in low:
            return proj
    # 兜底: cwd 末段
    tail = (cwd or "").replace("\\", "/").rstrip("/").split("/")[-1]
    return tail or "未知项目"


def _classify_location(item: dict[str, Any], managed: dict[str, Any] | None) -> str:
    """位置启发式; 可被 update 覆盖。managed = cc_sessions.json 里同 key 的托管记录。"""
    if managed:
        kind = (managed.get("kind") or "").lower()
        caller = (managed.get("caller_identity") or "").lower()
        if kind == "controller" or caller == "controller":
            return "omni-web"
        # ccdaemon 托管且有 pty → 多半是 poof / omni-web 里起的窗格
        if managed.get("pty_id") or managed.get("id"):
            return "poof-powershell"
    return "codex桌面" if item.get("provider") == "codex" else "vscode"


def _running(item: dict[str, Any], managed: dict[str, Any] | None, now: float) -> bool:
    if managed is not None and managed.get("alive"):
        return True
    # transcript mtime 在最近 5 分钟内算"在跑"
    mt = item.get("mtime")
    try:
        return bool(mt) and (now - float(mt)) < 300
    except (TypeError, ValueError):
        return False


def _scan_convos() -> list[dict[str, Any]]:
    """复用 convos 的扫描: 本机所有 claude/codex 对话。"""
    try:
        from omnicompany.dashboard.ccdaemon.import_routes import _scan_claude, _scan_codex
        return list(_scan_claude()) + list(_scan_codex())
    except Exception:  # noqa: BLE001
        return []


def _load_cc_sessions() -> dict[str, dict[str, Any]]:
    """cc_sessions.json 按 provider:session_id 索引(用 claude_session_id 优先, 退回 id)。"""
    p = omni_workspace_root() / "data" / "cc_sessions.json"
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    sessions = raw.get("sessions") if isinstance(raw, dict) else raw
    if isinstance(sessions, dict):
        sessions = list(sessions.values())
    out: dict[str, dict[str, Any]] = {}
    for s in sessions or []:
        if not isinstance(s, dict):
            continue
        sid = s.get("claude_session_id") or s.get("id")
        out[_key(s.get("provider"), sid)] = s
    return out


def _clean(text: str, n: int) -> str:
    return " ".join(str(text or "").split())[:n]


def rebuild(*, now: float | None = None) -> list[dict[str, Any]]:
    """从所有数据源重建注册表, 落库, 返回记录列表(最近活跃在前)。

    确定性派生, 不调任何模型 → 可高频调用。_pin 过的字段保留。
    """
    from omnicompany.dashboard.boss_sight.services.agent_digest import get_digest

    now = now if now is not None else time.time()
    store = load_registry()
    items = _scan_convos()
    cc = _load_cc_sessions()
    taken = {r.get("name") for r in store.values() if r.get("name")}

    seen: set[str] = set()
    for item in items:
        key = _key(item.get("provider"), item.get("session_id"))
        if not item.get("session_id"):
            continue
        seen.add(key)
        prev = store.get(key, {})
        managed = cc.get(key)
        dg = get_digest(item.get("provider", ""), item.get("session_id", "")) or {}
        pins: set[str] = set(prev.get("_pinned", []))

        # 名字: 一经分配不变
        name = prev.get("name") or _stable_name(key, taken)
        taken.add(name)

        project = prev["project"] if "project" in pins else _derive_project(
            str(dg.get("project") or ""), str(item.get("cwd") or ""))
        blob = f"{project} {dg.get('title','')} {dg.get('last_step','')} {item.get('cwd','')} {item.get('preview','')}"
        role = prev["role"] if "role" in pins else _derive_role(blob)
        location = prev["location"] if "location" in pins else _classify_location(item, managed)

        current = dg.get("last_step") or dg.get("title") or ""
        if dg.get("title") and dg.get("last_step"):
            current = f"{dg['title']} · {dg['last_step']}"
        initial = prev.get("initial_task") or _clean(item.get("preview", ""), 140)

        identity = f"{project}-{role}-{name}"
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
            "pty_id": (managed or {}).get("pty_id") or (managed or {}).get("id"),
            "active_plan": (managed or {}).get("active_plan"),
            "mtime": item.get("mtime"),
            "_updated_ts": now,
            "updated_at": datetime.fromtimestamp(now, timezone.utc).isoformat(),
        }
        store[key] = rec

    # 这轮没扫到的旧记录 = 不在跑了, 标 running=False(否则 --running 还显示一堆陈旧"待补")。
    for k, r in store.items():
        if k not in seen:
            r["running"] = False

    _save_registry(store)
    records = list(store.values())
    records.sort(key=lambda r: (0 if r.get("running") else 1, -float(r.get("mtime") or 0)))
    return records


# ---- 被动收集: 懒触发的后台 rebuild(单飞 + 节流) ----
_rebuild_lock = threading.Lock()
_rebuild_running = False
_last_rebuild_ts = 0.0
_MIN_REBUILD_GAP = 20.0  # 两次后台 rebuild 最小间隔


def schedule_rebuild() -> None:
    """被动收集: /active 检测到对话在跑时懒触发一次后台 rebuild, 不堵调用方(失败静默)。

    用户(2026-06-21): Omnicompany 检测到对话在进行、缺注册信息时应主动收集并更新。
    rebuild 是确定性的(不调模型), 只做文件扫描, 节流到 _MIN_REBUILD_GAP 秒一次。
    """
    global _rebuild_running, _last_rebuild_ts
    now = time.time()
    with _rebuild_lock:
        if _rebuild_running or (now - _last_rebuild_ts) < _MIN_REBUILD_GAP:
            return
        _rebuild_running = True
        _last_rebuild_ts = now

    def _run() -> None:
        global _rebuild_running
        try:
            rebuild()
        except Exception:  # noqa: BLE001
            pass
        finally:
            with _rebuild_lock:
                _rebuild_running = False

    threading.Thread(target=_run, name="agent-registry-rebuild", daemon=True).start()


def list_records(*, running_only: bool = False, limit: int | None = None,
                 rebuild_first: bool = True) -> list[dict[str, Any]]:
    records = rebuild() if rebuild_first else sorted(
        load_registry().values(),
        key=lambda r: (0 if r.get("running") else 1, -float(r.get("mtime") or 0)))
    if running_only:
        records = [r for r in records if r.get("running")]
    return records[:limit] if limit else records


def find_record(session_id: str, provider: str | None = None) -> dict[str, Any] | None:
    """自查身份: 按 session_id(可选 provider) 找记录。先确保最新。"""
    for r in list_records(rebuild_first=True):
        if r.get("session_id") == session_id and (provider is None or r.get("provider") == provider):
            return r
    # 也允许用 key 直接命中
    return load_registry().get(session_id)


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
