# [OMNI] origin=claude-code type=dashboard summary="真正接入 ccusage 的各软件 token 用量引擎: shell out `ccusage daily/session/blocks --json` 拿 按天×软件×模型 token+成本(内置 LiteLLM 定价, 缓存写/读分类计价)+ 5h 计费块 + 按项目(会话cwd归属); 供 设置>Token统计 页签" why="用户选路线B: 直接拿 ccusage 当数据引擎不重复造轮子" tags=ccusage,token-stats,dashboard,boss-sight
"""ccusage_stats —— 真正接入 ccusage 的各软件 token 用量引擎(带秒开缓存)。

约束:
  - 性能: ccusage 每次扫 ~/.claude(GB 级)偏慢(~12s), 子进程阻塞。**网页请求绝不现场等**:
    走 stale-while-revalidate —— 有缓存(内存或落盘)立即返回, 后台线程刷新; 无缓存返回
    {computing:true}, 前端轮询。落盘缓存持久, ccdaemon 重启 / 局域网他机都能秒开。
  - ccusage 入口: 必须能用全局装的 ccusage(`npm i -g ccusage`)。ccdaemon 进程 PATH 常不含
    npm 全局 bin, 所以 _ccusage_cmd 显式探测 %APPDATA%/npm; 回退 npx 会每次联网查 registry
    (冷启 ~409s 超时), 只作最后兜底。
  - 定价: 不加 --offline, 用 ccusage 的 LiteLLM 在线定价(自缓存), 覆盖很新的模型。
  - Windows: 子进程 CREATE_NO_WINDOW(禁前台跳控制台窗口)。
  - 精度: 读本地日志有已知的 input/思考 token 欠计, 绝对数与成本为估算, 趋势/占比可用。
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from datetime import date, timedelta
from pathlib import Path
from typing import Any

try:  # 落盘缓存目录; 独立测试导入失败则只用内存缓存
    from omnicompany.core.config import omni_workspace_root as _omni_ws_root
except Exception:  # noqa: BLE001
    _omni_ws_root = None  # type: ignore

_TTL = 180.0  # 秒; 缓存新鲜期, 过期仍先返回旧值再后台刷新
_RUN_TIMEOUT = 150  # 单次 ccusage 子进程上限
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform == "win32" else 0
_DATE_RE = re.compile(r"^\d{4}-?\d{2}-?\d{2}$")

# 秒开缓存: 内存 + 落盘 + 后台刷新(stale-while-revalidate)
_MEM: dict[str, dict[str, Any]] = {}
_COMPUTING: set[str] = set()
_LOCK = threading.Lock()


# ── ccusage 定位与调用 ─────────────────────────────────────────────────
def _ccusage_cmd() -> list[str] | None:
    """定位 ccusage 入口。优先全局 ccusage(含显式探测 %APPDATA%/npm), 最后才回退 npx。

    ⚠ 生产必须全局装(`npm i -g ccusage`): npx 每次联网查 registry, 冷启 ~409s(超 proxy);
    全局装直接调 ~12s。ccdaemon 进程 PATH 常不含 npm 全局 bin, 故显式探测 %APPDATA%/npm。
    """
    direct = shutil.which("ccusage")
    if direct:
        return [direct]
    appdata = os.environ.get("APPDATA") or ""
    if appdata:
        for name in ("ccusage.cmd", "ccusage.CMD", "ccusage"):
            p = os.path.join(appdata, "npm", name)
            if os.path.isfile(p):
                return [p]
    npx = shutil.which("npx")
    if npx:
        return [npx, "-y", "ccusage"]
    return None


def _run_ccusage(args: list[str]) -> tuple[dict[str, Any] | None, str | None]:
    """跑 `ccusage <args> --json`, 解析 stdout JSON。返回 (data, error)。"""
    base = _ccusage_cmd()
    if base is None:
        return None, "未找到 ccusage / npx(需要 Node.js; 建议 npm i -g ccusage)"
    cmd = [*base, *args, "--json"]
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=_RUN_TIMEOUT,
            creationflags=_NO_WINDOW,
        )
    except subprocess.TimeoutExpired:
        return None, f"ccusage 超时(>{_RUN_TIMEOUT}s; 是否未全局装 ccusage 走了 npx?)"
    except OSError as e:
        return None, f"ccusage 启动失败: {type(e).__name__}: {e}"
    if proc.returncode != 0:
        return None, f"ccusage 退出码 {proc.returncode}: {(proc.stderr or '').strip()[:200]}"
    out = (proc.stdout or "").strip()
    if not out:
        return None, "ccusage 无输出"
    start = out.find("{")
    try:
        return json.loads(out[start:] if start > 0 else out), None
    except json.JSONDecodeError as e:
        return None, f"ccusage JSON 解析失败: {e}"


def _clean_date(v: str | None) -> str | None:
    return v if v and _DATE_RE.match(v) else None


def _daily_args(since: str | None, until: str | None) -> list[str]:
    args = ["daily"]
    if since:
        args += ["--since", since]
    if until:
        args += ["--until", until]
    return args


def _session_args(since: str | None, until: str | None) -> list[str]:
    args = ["session"]
    if since:
        args += ["--since", since]
    if until:
        args += ["--until", until]
    return args


# ── 按项目归属(会话 cwd) ───────────────────────────────────────────────
_ENC_RE = re.compile(r"[/\\:]")


def _enc_path(p: str) -> str:
    """把绝对路径 encode 成 claude 目录名格式(/ \\ : → -), 便于前缀匹配会话目录名。"""
    return _ENC_RE.sub("-", str(p)).strip("-").lower()


def _omni_project_index() -> dict[str, str]:
    """{encoded_root: project_id} —— omni 项目本体(projects_registry)的目录 roots, encode 后供前缀匹配。"""
    try:
        from omnicompany.core.projects_registry import list_projects
    except Exception:  # noqa: BLE001
        return {}
    idx: dict[str, str] = {}
    try:
        for proj in list_projects():
            pid = proj.get("id")
            if not pid:
                continue
            for r in (proj.get("roots") or []):
                e = _enc_path(r)
                if e:
                    idx[e] = pid
    except Exception:  # noqa: BLE001
        pass
    return idx


def _cwd_to_omni_project(dirname: str, idx: dict[str, str]) -> str | None:
    """会话编码目录名 → omni 项目 id(最长前缀匹配 encoded root); 匹配不到 = None。"""
    low = dirname.lower()
    best: str | None = None
    best_len = 0
    for e, pid in idx.items():
        if len(e) > best_len and low.startswith(e):
            best, best_len = pid, len(e)
    return best


# ── LLM 后台标识: 给 cwd 匹配不到的会话读内容判断 omni 项目 ────────────
_LABELS_FILE = "session_project_labels.json"
_LABELER_STARTED = False


def _read_session_summary(fpath: Path) -> tuple[str, str]:
    """读会话 jsonl(claude 或 codex rollout) → (cwd, 前几条用户消息摘要<=600字)。

    兼容两种格式: claude 顶层 {cwd, message:{role,content:[{type:text}]}};
    codex rollout {payload:{cwd}} + {payload:{type:message,role:user,content:[{type:input_text}]}}。
    以 '<' 开头的用户帧是系统注入(plugins/permissions 说明), 跳过。多取几条提高可判性。
    """
    cwd = ""
    texts: list[str] = []
    try:
        with fpath.open("r", encoding="utf-8", errors="replace") as fh:
            for i, line in enumerate(fh):
                if i > 1200 or len(texts) >= 8:
                    break
                if not cwd and '"cwd"' in line:
                    try:
                        j = json.loads(line)
                        cwd = j.get("cwd") or (j.get("payload") or {}).get("cwd") or ""
                    except Exception:  # noqa: BLE001
                        pass
                if '"user"' in line and '"role"' in line:
                    try:
                        j = json.loads(line)
                    except Exception:  # noqa: BLE001
                        continue
                    msg = j.get("message") or j.get("payload") or {}
                    if msg.get("role") != "user":
                        continue
                    content = msg.get("content")
                    t = ""
                    if isinstance(content, list):
                        for c in content:
                            if isinstance(c, dict) and c.get("type") in ("text", "input_text"):
                                t = c.get("text", "")
                                break
                    elif isinstance(content, str):
                        t = content
                    t = (t or "").strip()
                    if t and not t.startswith("<"):
                        texts.append(t[:300].replace("\n", " "))
    except OSError:
        pass
    return cwd, " | ".join(texts)[:1400]


def _project_candidates() -> str:
    try:
        from omnicompany.core.projects_registry import list_projects
        return "\n".join(f"- {p.get('id')}: {p.get('name') or ''}" for p in list_projects() if p.get("id"))
    except Exception:  # noqa: BLE001
        return ""


def _load_labels() -> dict[str, str | None]:
    d = _cache_dir()
    if d is None:
        return {}
    try:
        p = d / _LABELS_FILE
        if p.is_file():
            data = json.loads(p.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}
    return {}


def _save_labels(labels: dict[str, str | None]) -> None:
    d = _cache_dir()
    if d is None:
        return
    try:
        (d / _LABELS_FILE).write_text(json.dumps(labels, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass


def _llm_label_batch(batch: list[tuple[str, str, str]], candidates: str) -> dict[str, str | None]:
    """一批会话 [(sid, cwd, 首句)] → {sid: 项目id 或 None}, 调 qwen 判断。"""
    from omnicompany.packages.services._core.omnicompany.llm_client import call_llm_json
    system = (
        "你是项目归类助手。给你一批 AI 编程会话(工作目录 cwd + 用户首句), 判断每个属于下面哪个 omni 项目。"
        "只能从项目 id 列表里选; 判断不出、或属于通用/临时/测试(smoke、autonomy-test 之类)性质、无法明确归到某业务项目的, 一律填 null。"
        "严格输出 JSON, 不要多余解释。"
    )
    lines = [f"{i}. sid={sid} | cwd={cwd or '?'} | 首句={summ or '?'}" for i, (sid, cwd, summ) in enumerate(batch)]
    user = (
        f"omni 项目列表(id: 名称):\n{candidates}\n\n会话:\n" + "\n".join(lines)
        + '\n\n输出 JSON: {"结果": [{"sid": "...", "project": "项目id 或 null"}, ...]}'
    )
    try:
        res = call_llm_json(system, user, web_bus=None, caller="ccusage.project_labeler")
    except Exception:  # noqa: BLE001
        return {}
    out: dict[str, str | None] = {}
    for row in (res.get("结果") or res.get("results") or []):
        if isinstance(row, dict) and row.get("sid"):
            proj = row.get("project")
            out[str(row["sid"])] = proj if (proj and proj != "null") else None
    return out


def _labeler_status(**kw: Any) -> None:
    """labeler 心跳/状态落盘(诊断用): ccusage_cache/labeler_status.json。"""
    d = _cache_dir()
    if d is None:
        return
    try:
        kw["ts"] = time.time()
        (d / "labeler_status.json").write_text(json.dumps(kw, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass


def _labeler_loop() -> None:
    """后台增量: 给现有设施(绑定/cwd/digest)都归不了的会话打 omni 项目标签(claude + codex)。

    v2: 摘要升级为前 3 条用户消息(600字); 老版 null 标签作废重标一次(__v 迁移)。
    """
    try:
        root = Path.home() / ".claude" / "projects"
        codex_root = Path.home() / ".codex" / "sessions"
        idx = _omni_project_index()
        candidates = _project_candidates()
        if not candidates:
            _labeler_status(state="dead", error="project candidates 为空(list_projects 失败?)")
            return
    except Exception as e:  # noqa: BLE001
        import traceback
        _labeler_status(state="dead", error=f"{type(e).__name__}: {e}", tb=traceback.format_exc()[-800:])
        return
    # v3 迁移: 深读(前8条用户消息+digest标题线索)重判全部 null(标到项目的保留)
    labels = _load_labels()
    if labels.get("__v") != 3:
        labels = {k: v for k, v in labels.items() if v and k != "__v"}
        labels["__v"] = 3  # type: ignore[assignment]
        _save_labels(labels)
    while True:
        labels = _load_labels()
        digests = _digest_map()
        vocab = _ensure_vocab(digests)

        def _covered(uid: str) -> bool:
            if uid in labels:
                return True
            dp = digests.get(uid, (None, None))[0]
            return bool(dp and vocab.get(dp))

        pending: list[tuple[str, Path]] = []
        try:
            dirs = list(root.iterdir())
        except OSError:
            dirs = []
        for d in dirs:
            # 异常按目录隔离(同 _project_map); 且只标 uuid 形态文件 —— agent-*.jsonl 是
            # 子代理转写, 不对应 ccusage 会话行, 标了纯浪费 LLM 调用
            try:
                if not d.is_dir() or _pre_bucket(d.name) or _cwd_to_omni_project(d.name, idx) is not None:
                    continue
                for f in _safe_walk_jsonl(d):
                    if _UUID_RE.fullmatch(f.stem) and not _covered(f.stem.lower()):
                        pending.append((f.stem.lower(), f))
            except OSError:
                continue
        for f in _safe_walk_jsonl(codex_root, prefix="rollout-"):
            m = _UUID_RE.search(f.stem)
            if m and not _covered(m.group(0).lower()):
                pending.append((m.group(0).lower(), f))
        _labeler_status(state="running", labeled=len(labels), pending=len(pending))
        if not pending:
            _labeler_status(state="idle", labeled=len(labels), pending=0)
            time.sleep(1800)
            continue
        # 4 批×20 并发调 qwen(深读摘要更长, 批调小; 带超时防网关卡死); 失败批标 None 继续
        import concurrent.futures as _cf

        def _prep(sid: str, fp: Path) -> tuple[str, str, str]:
            cwd, summ = _read_session_summary(fp)
            dproj, _dp, dtitle = digests.get(sid, (None, None, None))
            if dproj or dtitle:  # 摘要设施(digest)已有的线索一并给 LLM, 不浪费现有信息
                summ = (summ + f" ‖ 摘要设施记载: 项目描述={dproj or '?'} 标题={dtitle or '?'}")[:1500]
            return (sid, cwd, summ)

        take = pending[:80]
        batches = [take[i:i + 20] for i in range(0, len(take), 20)]
        prepared = [[_prep(sid, fp) for sid, fp in b] for b in batches]
        _ex = _cf.ThreadPoolExecutor(max_workers=4)
        futs = [_ex.submit(_llm_label_batch, b, candidates) for b in prepared]
        for fut, b in zip(futs, prepared):
            try:
                labeled = fut.result(timeout=120)
            except Exception as e:  # noqa: BLE001
                labeled = {}
                _labeler_status(state="batch_error", error=f"{type(e).__name__}: {e}", labeled=len(labels))
            for sid, _cwd, _summ in b:
                labels[sid] = labeled.get(sid)
        _ex.shutdown(wait=False)
        _save_labels(labels)
        time.sleep(1)


def _ensure_labeler() -> None:
    global _LABELER_STARTED
    with _LOCK:
        if _LABELER_STARTED:
            return
        _LABELER_STARTED = True
    threading.Thread(target=_labeler_loop, daemon=True, name="ccusage-labeler").start()


def _safe_walk_jsonl(root: Path, prefix: str = "") -> Any:
    """递归找 <prefix>*.jsonl, 异常按目录隔离 —— rglob 生成器中途抛 OSError 会静默丢掉
    其余全部目录(曾两度造成大批会话假'未归属'), 用显式栈逐目录 try 彻底规避。"""
    stack = [root]
    while stack:
        d = stack.pop()
        try:
            for p in d.iterdir():
                if p.is_dir():
                    stack.append(p)
                elif p.name.endswith(".jsonl") and p.name.startswith(prefix):
                    yield p
        except OSError:
            continue


def _pre_bucket(dirname: str) -> str | None:
    """临时/测试会话预判(cwd 在 temp/scratchpad 下) → 直接归桶, 不进 omni 项目匹配/LLM。"""
    low = dirname.lower()
    if "temp" in low or "scratchpad" in low or "internal-tracker" in low or "-tmp" in low:
        return "临时/测试会话"
    return None


# ── agent_digests 复用(现成会话摘要设施, 已含 project/plan; 不重复分类) ──
_UUID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.I)


def _digest_map() -> dict[str, tuple[str | None, str | None]]:
    """{session uuid: (digest的project原文, plan原文)} —— 读 boss_sight/agent_digests.json。

    digests 是现成的会话摘要设施(claude_code:<uuid> / codex:<uuid> 键控, 2400+条,
    project/plan 为自由中文文本), 按用户要求复用它, 不逐会话重复分类; 自由文本 →
    omni 项目 id 的对齐走 _vocab_map(词表级一次映射)。
    """
    out: dict[str, tuple[str | None, str | None]] = {}
    if _omni_ws_root is None:
        return out
    try:
        p = _omni_ws_root() / "data" / "boss_sight" / "agent_digests.json"
        d = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return out
    for k, v in (d.items() if isinstance(d, dict) else []):
        if not isinstance(v, dict):
            continue
        m = _UUID_RE.search(k)
        if not m:
            continue
        proj = (v.get("project") or "").strip() or None
        plan = (v.get("plan") or "").strip() or None
        if plan == "无":
            plan = None
        title = (v.get("title") or "").strip() or None
        out[m.group(0).lower()] = (proj, plan, title)
    return out


_VOCAB_FILE = "project_vocab_map.json"


def _load_vocab() -> dict[str, str | None]:
    d = _cache_dir()
    if d is None:
        return {}
    try:
        p = d / _VOCAB_FILE
        if p.is_file():
            data = json.loads(p.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}
    return {}


def _save_vocab(v: dict[str, str | None]) -> None:
    d = _cache_dir()
    if d is None:
        return
    try:
        (d / _VOCAB_FILE).write_text(json.dumps(v, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass


def _llm_map_vocab(words: list[str], candidates: str) -> dict[str, str | None]:
    """把 digest 的自由文本项目名(词表级)映射到 omni 项目 id。一次映射一批词, 不逐会话调。"""
    from omnicompany.packages.services._core.omnicompany.llm_client import call_llm_json
    system = (
        "把下面这些自由写法的项目名映射到 omni 项目 id。只能从项目列表选; 显然对应不上"
        "(如 WindowsWorkspace、临时、通用杂务)填 null。同义/大小写/中英文变体要归并"
        "(如 'omnicompany 驾驶舱'/'Omnicompany驾驶舱' 都是 omnidashboard)。严格输出 JSON。"
    )
    user = (
        f"omni 项目列表(id: 名称):\n{candidates}\n\n自由写法:\n"
        + "\n".join(f"- {w}" for w in words)
        + '\n\n输出 JSON: {"映射": {"<自由写法>": "项目id 或 null", ...}}'
    )
    try:
        res = call_llm_json(system, user, web_bus=None, caller="ccusage.vocab_mapper")
    except Exception:  # noqa: BLE001
        return {}
    out: dict[str, str | None] = {}
    for k, v in ((res.get("映射") or res.get("mapping") or {}).items()):
        out[str(k)] = v if (v and v != "null") else None
    return out


def _ensure_vocab(digests: dict[str, tuple[str | None, str | None]]) -> dict[str, str | None]:
    """确保 digest 里出现过的项目写法都有词表映射(缺的批量补, 每批 200 词)。"""
    vocab = _load_vocab()
    words = sorted({t[0] for t in digests.values() if t[0]} - set(vocab))
    if not words:
        return vocab
    candidates = _project_candidates()
    if not candidates:
        return vocab
    for i in range(0, len(words), 200):
        mapped = _llm_map_vocab(words[i:i + 200], candidates)
        for w in words[i:i + 200]:
            vocab[w] = mapped.get(w)
    _save_vocab(vocab)
    return vocab


def _binding_map() -> dict[str, tuple[str | None, str | None]]:
    """{claude_session_id: (project, plan)} —— omni 会话绑定(cc_session_bindings + cc_sessions)。

    用户显式绑的最权威; project 常为 None 但 active_plan(plan)有。session uuid 可对上 ccusage。
    """
    out: dict[str, tuple[str | None, str | None]] = {}
    if _omni_ws_root is None:
        return out
    for fn in ("cc_session_bindings.json", "cc_sessions.json"):
        try:
            p = _omni_ws_root() / "data" / fn
            if not p.is_file():
                continue
            d = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        for v in (d.values() if isinstance(d, dict) else []):
            if not isinstance(v, dict):
                continue
            sid = v.get("claude_session_id") or v.get("session_id")
            if not sid:
                continue
            proj = v.get("project")
            plan = v.get("active_plan") or v.get("plan")
            prev = out.get(str(sid))
            if prev is None or (proj or plan):
                out[str(sid)] = (proj, plan)
    return out


def _project_map() -> dict[str, str | None]:
    """{claude session uuid: 归属} —— 只做确定性三级: 绑定 project > 临时预判 > cwd roots 匹配。

    digest 词表映射与 LLM 标签在 _make_resolver 里作为后续回退层。
    """
    root = Path.home() / ".claude" / "projects"
    idx = _omni_project_index()
    bindings = _binding_map()
    m: dict[str, str | None] = {}
    try:
        dirs = list(root.iterdir())
    except OSError:
        return m
    for d in dirs:
        # 异常按目录隔离: 单个目录并发写/长路径出错不能静默丢掉其余目录(曾致大批会话假"未归属")
        try:
            if not d.is_dir():
                continue
            pre = _pre_bucket(d.name)
            proj = None if pre else _cwd_to_omni_project(d.name, idx)
            # 子代理/工作流会话在 subagents/ 等子目录, 跟随所在目录归属(安全遍历)
            for f in _safe_walk_jsonl(d):
                uuid = f.stem
                bproj = bindings.get(uuid, (None, None))[0]
                m[uuid] = bproj or pre or proj
        except OSError:
            continue
    return m


def _make_resolver() -> Any:
    """统一归属解析: resolve(sid, agent) → (项目, 计划)。

    优先级(用户定): 会话绑定 > 确定性(临时预判/cwd匹配) > agent_digests(现成摘要设施,
    经词表映射对齐 omni 项目 id) > LLM 标签 > 未归属。plan = 绑定 plan > digest plan。
    codex 的 sid 是 ccusage period(含路径), 统一提取 uuid 归一。
    """
    pmap = _project_map()
    digests = _digest_map()
    vocab = _load_vocab()
    labels = _load_labels()
    bindings = _binding_map()

    def resolve(sid: str, agent: str) -> tuple[str, str]:
        m = _UUID_RE.search(sid)
        uid = m.group(0).lower() if m else sid.lower()
        b = bindings.get(uid) or bindings.get(sid) or (None, None)
        dproj, dplan, _dtitle = digests.get(uid, (None, None, None))
        plan = b[1] or dplan or "未绑定计划"
        if b[0]:
            return b[0], plan
        if agent == "claude":
            p = pmap.get(uid) or pmap.get(sid)
            if p:
                return p, plan
        if dproj:
            mapped = vocab.get(dproj)
            if mapped:
                return mapped, plan
        lab = labels.get(uid) or labels.get(sid)
        if isinstance(lab, str) and lab:
            return lab, plan
        return "未归属", plan

    return resolve


def _accum_session(dst: dict[str, dict[str, Any]], key: str, s: dict[str, Any]) -> None:
    slot = dst.setdefault(key, {
        "inputTokens": 0, "outputTokens": 0,
        "cacheCreationTokens": 0, "cacheReadTokens": 0,
        "totalTokens": 0, "totalCost": 0.0, "sessions": 0,
    })
    it = int(s.get("inputTokens") or 0)
    ot = int(s.get("outputTokens") or 0)
    cc = int(s.get("cacheCreationTokens") or 0)
    cr = int(s.get("cacheReadTokens") or 0)
    slot["inputTokens"] += it
    slot["outputTokens"] += ot
    slot["cacheCreationTokens"] += cc
    slot["cacheReadTokens"] += cr
    slot["totalTokens"] += int(s.get("totalTokens") or (it + ot + cc + cr))
    slot["totalCost"] += float(s.get("totalCost") or 0.0)
    slot["sessions"] += 1


def _by_project(sessions_list: list[dict[str, Any]], resolve: Any) -> dict[str, dict[str, Any]]:
    by_project: dict[str, dict[str, Any]] = {}
    for s in sessions_list:
        proj, _plan = resolve(str(s.get("period") or ""), str(s.get("agent") or "").lower())
        _accum_session(by_project, proj, s)
    return by_project


def _session_details(sessions_list: list[dict[str, Any]], resolve: Any) -> list[dict[str, Any]]:
    """会话明细(供前端按 软件/项目/计划 多选实时聚合全部图表)。一条会话一行。"""
    out: list[dict[str, Any]] = []
    for s in sessions_list:
        sid = str(s.get("period") or "")
        agent = str(s.get("agent") or "").lower()
        proj, plan = resolve(sid, agent)
        day = str((s.get("metadata") or {}).get("lastActivity") or "")[:10]
        out.append({
            "sid": sid,
            "project": proj,
            "plan": plan,
            "agent": agent,
            "day": day,
            "totalCost": s.get("totalCost", 0),
            "totalTokens": s.get("totalTokens", 0),
            "inputTokens": s.get("inputTokens", 0),
            "outputTokens": s.get("outputTokens", 0),
            "cacheReadTokens": s.get("cacheReadTokens", 0),
            "cacheCreationTokens": s.get("cacheCreationTokens", 0),
            "models": [
                {
                    "model": mb.get("modelName") or "unknown",
                    "cost": mb.get("cost", 0),
                    "tokens": int(mb.get("inputTokens", 0) or 0) + int(mb.get("outputTokens", 0) or 0)
                    + int(mb.get("cacheCreationTokens", 0) or 0) + int(mb.get("cacheReadTokens", 0) or 0),
                }
                for mb in (s.get("modelBreakdowns") or [])
            ],
        })
    return out


# ── 按软件 / 按模型派生 ────────────────────────────────────────────────
def _agent_of_model(name: str) -> str:
    n = (name or "").lower()
    if "claude" in n:
        return "claude"
    if n.startswith("gpt") or "codex" in n or n.startswith(("o1", "o3", "o4")):
        return "codex"
    if "gemini" in n:
        return "gemini"
    if "qwen" in n:
        return "qwen"
    if "kimi" in n:
        return "kimi"
    if "glm" in n:
        return "glm"
    return "other"


def _accum_breakdown(dst: dict[str, dict[str, Any]], key: str, mb: dict[str, Any]) -> None:
    slot = dst.setdefault(key, {
        "inputTokens": 0, "outputTokens": 0,
        "cacheCreationTokens": 0, "cacheReadTokens": 0,
        "totalTokens": 0, "totalCost": 0.0,
    })
    it = int(mb.get("inputTokens") or 0)
    ot = int(mb.get("outputTokens") or 0)
    cc = int(mb.get("cacheCreationTokens") or 0)
    cr = int(mb.get("cacheReadTokens") or 0)
    slot["inputTokens"] += it
    slot["outputTokens"] += ot
    slot["cacheCreationTokens"] += cc
    slot["cacheReadTokens"] += cr
    slot["totalTokens"] += it + ot + cc + cr
    slot["totalCost"] += float(mb.get("cost") or 0.0)


def _derive_by_agent_and_model(daily: list[dict[str, Any]]) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    by_agent: dict[str, dict[str, Any]] = {}
    by_model: dict[str, dict[str, Any]] = {}
    for row in daily:
        for mb in (row.get("modelBreakdowns") or []):
            name = mb.get("modelName") or "unknown"
            _accum_breakdown(by_model, name, mb)
            _accum_breakdown(by_agent, _agent_of_model(name), mb)
    return by_agent, by_model


def _active_block() -> dict[str, Any] | None:
    """当前 5h 计费块 + 燃烧率 + 预测(ccusage blocks --active)。取不到返回 None。"""
    data, _err = _run_ccusage(["blocks", "--active"])
    if not data:
        return None
    for b in (data.get("blocks") or []):
        if b.get("isActive"):
            return {
                "id": b.get("id"),
                "startTime": b.get("startTime"),
                "endTime": b.get("endTime"),
                "models": b.get("models") or [],
                "totalTokens": b.get("totalTokens"),
                "costUSD": b.get("costUSD"),
                "tokenCounts": b.get("tokenCounts") or {},
                "burnRate": b.get("burnRate") or {},
                "projection": b.get("projection") or {},
            }
    return None


# ── 实际计算(后台线程里跑, 不在网页请求内) ────────────────────────────
def _compute_stats(s: str | None, u: str | None) -> dict[str, Any]:
    daily_data, daily_err = _run_ccusage(_daily_args(s, u))
    if daily_data is None:
        return {
            "available": False, "generated_at": time.time(), "error": daily_err,
            "daily": [], "totals": {}, "agents": [], "by_agent": {}, "by_model": {},
            "by_project": {}, "active_block": None,
        }
    daily = daily_data.get("daily") or []
    totals = daily_data.get("totals") or {}
    agents: set[str] = set()
    for row in daily:
        for a in ((row.get("metadata") or {}).get("agents") or []):
            if a:
                agents.add(str(a))
    by_agent, by_model = _derive_by_agent_and_model(daily)
    sess_data, _sess_err = _run_ccusage(_session_args(s, u))
    sessions_raw = (sess_data or {}).get("session") or []
    resolve = _make_resolver()
    by_project = _by_project(sessions_raw, resolve)
    sessions_detail = _session_details(sessions_raw, resolve)
    payload: dict[str, Any] = {
        "available": True,
        "generated_at": time.time(),
        "source": "ccusage (开源事实标准; LiteLLM 定价, 缓存写/读分类计价)",
        "note": "读 ~/.claude、~/.codex 等本地日志; 有已知的 input/思考 token 欠计, 绝对数与成本为估算, 趋势与占比可用。",
        "daily": daily,
        "totals": totals,
        "agents": sorted(agents),
        "by_agent": by_agent,
        "by_model": by_model,
        "by_project": by_project,
        "sessions": sessions_detail,
        "active_block": _active_block(),
    }
    if s:
        payload["since"] = s
    if u:
        payload["until"] = u
    return payload


# ── 落盘缓存(持久; ccdaemon 重启 / 局域网他机都能读) ──────────────────
def _cache_dir() -> Path | None:
    if _omni_ws_root is None:
        return None
    try:
        d = _omni_ws_root() / "data" / "boss_sight" / "ccusage_cache"
        d.mkdir(parents=True, exist_ok=True)
        return d
    except OSError:
        return None


def _load_disk(key: str) -> dict[str, Any] | None:
    d = _cache_dir()
    if d is None:
        return None
    p = d / f"{key}.json"
    try:
        if p.is_file():
            data = json.loads(p.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else None
    except (OSError, json.JSONDecodeError):
        return None
    return None


def _save_disk(key: str, payload: dict[str, Any]) -> None:
    d = _cache_dir()
    if d is None:
        return
    try:
        (d / f"{key}.json").write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass


def _ensure_bg(key: str, s: str | None, u: str | None) -> None:
    """确保有一个后台线程在算 key(不重复启动)。算完写内存 + 落盘。"""
    with _LOCK:
        if key in _COMPUTING:
            return
        _COMPUTING.add(key)

    def _run() -> None:
        try:
            payload = _compute_stats(s, u)
            _MEM[key] = payload
            _save_disk(key, payload)
        except Exception:  # noqa: BLE001
            pass
        finally:
            with _LOCK:
                _COMPUTING.discard(key)

    threading.Thread(target=_run, daemon=True, name=f"ccusage-stats-{key}").start()


_PREWARM_STARTED = False
_PREWARM_RANGES = (7, 30, 90, 0)  # 天数; 0=全部, 对应前端 4 个时间范围


def _since_for(days: int) -> str | None:
    if days <= 0:
        return None
    return (date.today() - timedelta(days=days - 1)).strftime("%Y%m%d")


def _prewarm_loop() -> None:
    """后台守护: 每 10 分钟把前端 4 个时间范围的当天数据预算好落盘, 用户打开即命中(秒开)。

    since 用本地日期(date.today()), 与前端浏览器本地时区算的 since 对齐 → key 命中。
    """
    while True:
        for days in _PREWARM_RANGES:
            since = _since_for(days)
            key = f"{since or 'all'}__all"
            cached = _MEM.get(key) or _load_disk(key)
            fresh = cached is not None and (time.time() - float(cached.get("generated_at") or 0)) < _TTL
            if not fresh:
                try:
                    p = _compute_stats(since, None)
                    _MEM[key] = p
                    _save_disk(key, p)
                except Exception:  # noqa: BLE001
                    pass
        time.sleep(600)


def _ensure_prewarm() -> None:
    global _PREWARM_STARTED
    with _LOCK:
        if _PREWARM_STARTED:
            return
        _PREWARM_STARTED = True
    threading.Thread(target=_prewarm_loop, daemon=True, name="ccusage-prewarm").start()


def build_ccusage_stats(
    since: str | None = None,
    until: str | None = None,
    force: bool = False,
) -> dict[str, Any]:
    """秒开入口: 有缓存(内存/落盘)立即返回, 过期则后台刷新; 无缓存返回 computing。

    网页请求永不现场等 ccusage(那要十几秒~几分钟)。前端见 computing/stale 自行轮询/标注。
    """
    _ensure_prewarm()
    _ensure_labeler()
    s = _clean_date(since)
    u = _clean_date(until)
    key = f"{s or 'all'}__{u or 'all'}"

    cached = _MEM.get(key)
    if cached is None:
        cached = _load_disk(key)
        if cached is not None:
            _MEM[key] = cached

    now = time.time()
    fresh = cached is not None and (now - float(cached.get("generated_at") or 0)) < _TTL
    if cached is not None and fresh and not force:
        return cached

    # 过期或没有 → 后台刷新
    _ensure_bg(key, s, u)
    if cached is not None:
        return {**cached, "stale": True}
    return {
        "available": False,
        "computing": True,
        "generated_at": now,
        "note": "首次计算中(约 15 秒扫本地日志), 页面会自动刷新。",
        "daily": [], "totals": {}, "agents": [], "by_agent": {}, "by_model": {},
        "by_project": {}, "active_block": None,
    }


__all__ = ["build_ccusage_stats"]
