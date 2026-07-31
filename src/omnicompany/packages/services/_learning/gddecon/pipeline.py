# [OMNI] origin=claude-code domain=services/_learning/gddecon ts=2026-06-27T00:00:00Z type=pipeline status=active
# [OMNI] material_id="material:services.learning.gddecon.pipeline.deconstruction_orchestrator.py"
"""gddecon.pipeline —— 游戏设计拆解编排器。

真实执行入口 = run_deconstruction(config)。
  1. 注册 gddecon Formats (幂等)。
  2. 读 discovery_method.md 作 node_prompt (发现法本体)。
  3. 用统一 run_json_agent (只读 AgentNodeLoop) 跑拆解 agent: 读设计源 + 当前 build,
     应用「透镜 × 展开规则 × 完备性」, 出结构化方面树 JSON (按 gddecon.aspect-tree schema 校验)。
  4. 把 JSON 确定性渲染成可读 .md, 落到 data/knowledge/aspect_trees/<game>.md。
  5. (可选) 经 SQLiteBus 发 TASK_INTENT / TASK_FINISH 事件供审计。

agent 只读「发现」, 编排器确定性「落盘」—— 对齐 worker.md 的读/写分离, 用统一 agent 不 fork (R-26)。

team.py 风格的 build_team() / run.py 的 build_bindings() 是给 `omni describe` 的拓扑声明,
真实多步逻辑在本编排器 (同 hypothesis.run_session 的约定)。
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import pathlib
import uuid
from typing import Any

log = logging.getLogger(__name__)

_PKG_DIR = pathlib.Path(__file__).resolve().parent
_PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[6]  # 仓根 .../omnicompany (parents[5]=src)
_METHOD_PATH = _PKG_DIR / "discovery_method.md"
_GAP_METHOD_PATH = _PKG_DIR / "gap_method.md"
_UI_STD_METHOD_PATH = _PKG_DIR / "ui_standard_method.md"
_DEFAULT_PROJECT_ROOT = "C:/workspace/"
_OUT_DIR = _PROJECT_ROOT / "data" / "knowledge" / "aspect_trees"


def _load_method() -> str:
    return _METHOD_PATH.read_text(encoding="utf-8")


def _build_task(cfg: dict) -> str:
    game = cfg.get("game_name", "(未命名游戏)")
    sources = cfg.get("design_sources") or []
    build_root = cfg.get("build_root", "")
    evidence = cfg.get("build_evidence") or []
    focus = (cfg.get("focus") or "").strip()

    lines = [
        f"拆解这款游戏的设计为方面树：{game}",
        "",
        "设计源（一手「应然」，逐个读够再下判断）：",
    ]
    lines += [f"  - {s}" for s in sources] or ["  - (未提供，尽力从 build 推断)"]
    lines += ["", f"当前 build 根（「实然」，读代码/界面快照/截图取现态证据）：", f"  - {build_root or '(未提供)'}"]
    if evidence:
        lines += ["", "已知现态观察 / 失败现象（作为背离触发的线索）："]
        lines += [f"  - {e}" for e in evidence]
    if focus:
        lines += ["", f"本次只下钻这个子领域（其余只给到顶层即可）：{focus}"]
    lines += [
        "",
        "按发现法（透镜 × 展开规则 × 完备性）做完整拆解。顶层方面要覆盖「UI 只是其一」，"
        "把其余大方面也挖出来。每条方面都要有 verbatim 证据。最后只输出一个 JSON 对象。",
    ]
    return "\n".join(lines)


def _gather_context(cfg: dict) -> str:
    """确定性预载素材: 读 design_sources(文件 / 目录下 .md) 文本, 拼成上下文 (有上限)。

    比让 agent 自由用工具读再出 JSON 更可靠 (material.md: Format 字段预加载 > 动态探索),
    且不会漏文件。"""
    CAP_FILE = 30000
    CAP_TOTAL = 170000
    parts: list[str] = []
    total = 0
    for src in cfg.get("design_sources") or []:
        p = pathlib.Path(src)
        files: list[pathlib.Path] = []
        try:
            if p.is_dir():
                files = sorted(p.glob("*.md"))[:14]
            elif p.is_file():
                files = [p]
        except OSError:
            continue
        for f in files:
            try:
                txt = f.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            if len(txt) > CAP_FILE:
                txt = txt[:CAP_FILE] + f"\n…(截断, 原长 {len(txt)})"
            block = f"\n\n===== 文件: {f} =====\n{txt}"
            if total + len(block) > CAP_TOTAL:
                parts.append(f"\n\n(后续素材超总量上限 {CAP_TOTAL}, 略)")
                return "".join(parts)
            parts.append(block)
            total += len(block)
    return "".join(parts)


def _build_user(cfg: dict, context: str) -> str:
    return _build_task(cfg) + "\n\n──────── 素材（设计源 + 现态证据）────────\n" + context


def _slug(name: str) -> str:
    s = re.sub(r"[^0-9A-Za-z一-鿿_-]+", "-", name).strip("-")
    return s or "game"


def _render_md(data: dict, cfg: dict) -> str:
    game = data.get("game_name") or cfg.get("game_name", "")
    aspects = data.get("aspects") or []
    lenses = data.get("lenses_applied") or []
    by_parent: dict[Any, list[dict]] = {}
    for a in aspects:
        by_parent.setdefault(a.get("parent"), []).append(a)

    out: list[str] = [
        f"# {game} · 设计方面树",
        "",
        "> 由 gddecon 拆解管线产出（方面发现法：透镜 × 展开规则 × 完备性）。"
        "这是「决策树建构」的骨架——每个叶子方面后续挂评判标准与构建决策，取代散点式修复。",
        "",
        f"- 方面总数：{data.get('aspect_count', len(aspects))}",
        f"- 用到的透镜：{', '.join(lenses) if lenses else '(未列)'}",
        f"- 顶层方面数：{len(by_parent.get(None, []))}",
        "",
        "## 方面树",
        "",
    ]

    def walk(parent_id: Any, depth: int) -> None:
        for a in by_parent.get(parent_id, []):
            indent = "  " * depth
            flag = " ⚠当前背离" if a.get("live_concern") else ""
            out.append(f"{indent}- **{a.get('name','')}** `({a.get('id','')})`{flag}")
            if a.get("definition"):
                out.append(f"{indent}  - 关切：{a['definition']}")
            if a.get("lens"):
                out.append(f"{indent}  - 透镜：{a['lens']}")
            if a.get("rationale"):
                out.append(f"{indent}  - 为何独立：{a['rationale']}")
            for ev in a.get("evidence") or []:
                out.append(f"{indent}  - 证据：{ev}")
            walk(a.get("id"), depth + 1)

    walk(None, 0)

    notes = data.get("completeness_notes")
    if notes:
        out += ["", "## 完备性自评", "", notes]
    return "\n".join(out) + "\n"


def _as_list(v: Any) -> list[str]:
    if isinstance(v, list):
        return [str(x) for x in v if str(x).strip()]
    if isinstance(v, str):
        return [x.strip() for x in v.split(",") if x.strip()]
    return []


async def _run_async(cfg: dict) -> dict:
    from .formats import GDDECON_ASPECT_TREE, register_formats

    # CLI 把 design_sources/build_evidence 传成逗号串; 归一为 list
    cfg = dict(cfg)
    cfg["design_sources"] = _as_list(cfg.get("design_sources"))
    cfg["build_evidence"] = _as_list(cfg.get("build_evidence"))

    # 1. 注册 Formats（幂等，非致命）
    try:
        from omnicompany.protocol.format import create_builtin_registry
        register_formats(create_builtin_registry())
    except Exception as exc:  # noqa: BLE001
        log.warning("[gddecon] 注册 Formats 失败（非致命）: %s", exc)

    session_id = cfg.get("session_id") or f"gddecon-{uuid.uuid4().hex[:8]}"
    game = cfg.get("game_name", "(未命名游戏)")
    project_root = cfg.get("project_root") or _DEFAULT_PROJECT_ROOT
    model = cfg.get("model") or "qwen3.6-plus"
    max_turns = int(cfg.get("max_turns") or 60)

    # 2. 可选事件总线（审计）
    bus = None
    try:
        from omnicompany.bus.sqlite import SQLiteBus
        from omnicompany.protocol.events import FactoryEvent
        from omnicompany.protocol.registry import EventType
        bus = SQLiteBus()
        await bus.connect()
        await bus.publish(FactoryEvent(
            trace_id=session_id,
            event_type=EventType.TASK_INTENT.value,
            source="gddecon.pipeline",
            payload={"game_name": game, "session_id": session_id, "focus": cfg.get("focus", "")},
            tags=["gddecon", f"game.{_slug(game)}"],
        ))
    except Exception as exc:  # noqa: BLE001
        log.warning("[gddecon] EventBus 初始化失败（降级无事件）: %s", exc)
        bus = None

    # 3. 确定性预载素材 + 一次性结构化产出（call_json 比 agentic loop 出 JSON 可靠）
    out: dict = {
        "game_name": game,
        "session_id": session_id,
        "ok": False,
        "error": "",
        "doc_path": None,
        "aspect_count": 0,
    }
    data: Any = None
    try:
        from omnicompany.runtime.llm.structured import call_json
        context = _gather_context(cfg)
        out["context_chars"] = len(context)
        data = await asyncio.to_thread(
            call_json,
            system=_load_method(),
            user=_build_user(cfg, context),
            schema=GDDECON_ASPECT_TREE.json_schema,
            model=model,
            max_tokens=16000,
        )
        out["ok"] = isinstance(data, dict) and bool(data.get("aspects"))
    except Exception as exc:  # noqa: BLE001
        out["error"] = str(exc)[:400]
        log.warning("[gddecon] call_json 失败: %s", exc)

    if out["ok"] and isinstance(data, dict) and data.get("aspects"):
        # 4. 确定性落盘
        _OUT_DIR.mkdir(parents=True, exist_ok=True)
        doc_path = _OUT_DIR / f"{_slug(game)}.md"
        doc_path.write_text(_render_md(data, cfg), encoding="utf-8")
        (_OUT_DIR / f"{_slug(game)}.json").write_text(  # 结构化树, 供差距分析阶段复用
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        out["doc_path"] = str(doc_path)
        out["aspect_count"] = data.get("aspect_count") or len(data.get("aspects") or [])
        out["aspect_tree"] = data  # 供事件型 worker 作 gddecon.aspect-tree sink 产出
        log.info("[gddecon] %s: %d 方面 -> %s", game, out["aspect_count"], doc_path)
    else:
        log.warning("[gddecon] 未产出合法方面树: err=%s", out.get("error"))

    # 5. 收尾事件
    if bus is not None:
        try:
            from omnicompany.protocol.events import FactoryEvent
            from omnicompany.protocol.registry import EventType
            await bus.publish(FactoryEvent(
                trace_id=session_id,
                event_type=EventType.TASK_FINISH.value,
                source="gddecon.pipeline",
                payload={"result": out},
                tags=["gddecon", f"game.{_slug(game)}"],
            ))
            await bus.close()
        except Exception as exc:  # noqa: BLE001
            log.warning("[gddecon] EventBus 收尾失败（非致命）: %s", exc)

    return out


def run_deconstruction(config: dict) -> dict:
    """同步入口：拆一款游戏的设计为方面树。

    config 字段（= gddecon.deconstruction-request）：
      game_name(必填) / design_sources[] / build_root / build_evidence[] / focus / project_root
      + 编排选项 model(默认 qwen3.6-plus) / max_turns(默认 60)。
    返回：{game_name, ok, doc_path, aspect_count, turn_count, error, session_id}。
    """
    return asyncio.run(_run_async(config))


# ═══════════════════════════════════════════════════════════
# 差距分析阶段（应然↔实然↔差距全局盘点）
# ═══════════════════════════════════════════════════════════

def _load_gap_method() -> str:
    return _GAP_METHOD_PATH.read_text(encoding="utf-8")


def _load_tree(game: str) -> dict | None:
    p = _OUT_DIR / f"{_slug(game)}.json"
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
    return None


_SEV_ORDER = {"critical": 0, "major": 1, "minor": 2, "aligned": 3}


def _render_gap_md(game: str, tree: dict, gaps: list[dict]) -> str:
    aspects = tree.get("aspects") or []
    by_id = {a.get("id"): a for a in aspects}

    def top_of(aid: str) -> str:
        seen = set()
        cur = aid
        while cur in by_id and by_id[cur].get("parent") and cur not in seen:
            seen.add(cur)
            cur = by_id[cur]["parent"]
        return cur

    top_name = {a["id"]: a["name"] for a in aspects if not a.get("parent")}
    groups: dict[str, list[dict]] = {}
    for g in gaps:
        t = top_of(g.get("id", ""))
        groups.setdefault(top_name.get(t, t or "其他"), []).append(g)

    n_crit = sum(1 for g in gaps if g.get("severity") == "critical")
    n_major = sum(1 for g in gaps if g.get("severity") == "major")
    out = [
        f"# {game} · 设计差距整体盘点",
        "",
        "> 由 gddecon 差距分析产出（应然 ↔ 实然 ↔ 差距，逐方面）。配合方面树使用，是决策树「挂尺子/挂决策」前的全局差距底账。不打分，severity 为分类词。",
        "",
        f"- 差距条目：{len(gaps)}（critical {n_crit} · major {n_major}）",
        "",
    ]
    sev_mark = {"critical": "🔴", "major": "🟠", "minor": "🟡", "aligned": "✅"}
    for top, items in groups.items():
        items.sort(key=lambda g: _SEV_ORDER.get(g.get("severity", "minor"), 2))
        out.append(f"## {top}")
        out.append("")
        for g in items:
            mark = sev_mark.get(g.get("severity"), "·")
            out.append(f"### {mark} {g.get('name','')} `({g.get('id','')})` · {g.get('severity','')}")
            out.append(f"- **应然**：{g.get('intended','')}")
            out.append(f"- **实然**：{g.get('actual','')}")
            out.append(f"- **差距**：{g.get('gap','')}")
            for ev in g.get("evidence") or []:
                out.append(f"  - 证据：{ev}")
            out.append("")
    return "\n".join(out) + "\n"


async def _run_gap_async(cfg: dict) -> dict:
    from .formats import GDDECON_GAP_REPORT, register_formats
    try:
        from omnicompany.protocol.format import create_builtin_registry
        register_formats(create_builtin_registry())
    except Exception as exc:  # noqa: BLE001
        log.warning("[gddecon-gap] 注册 Formats 失败（非致命）: %s", exc)

    cfg = dict(cfg)
    cfg["design_sources"] = _as_list(cfg.get("design_sources"))
    cfg["build_evidence"] = _as_list(cfg.get("build_evidence"))
    game = cfg.get("game_name", "(未命名游戏)")
    model = cfg.get("model") or "qwen3.6-plus"

    # 1. 取方面树（无则先跑拆解）
    tree = _load_tree(game)
    if not tree or not tree.get("aspects"):
        log.info("[gddecon-gap] 无现成方面树, 先跑拆解 ...")
        deco = await _run_async(cfg)
        tree = deco.get("aspect_tree")
    out: dict = {"game_name": game, "ok": False, "error": "", "doc_path": None, "gap_count": 0}
    if not tree or not tree.get("aspects"):
        out["error"] = "无方面树可分析"
        return out

    aspects = tree["aspects"]
    aspect_list = "\n".join(
        f"- {a.get('id')} | {a.get('name')} | {a.get('definition','')}"
        f"{' [当前疑似背离]' if a.get('live_concern') else ''}"
        for a in aspects
    )

    # 2. 一次性结构化产出全部差距
    try:
        from omnicompany.runtime.llm.structured import call_json
        context = _gather_context(cfg)
        user = (
            f"游戏：{game}\n方面树（对清单里每个方面都给一条差距分析，用清单里的 id）：\n{aspect_list}\n\n"
            f"──────── 素材（设计源=应然 / 当前 build=实然）────────\n{context}"
        )
        data = await asyncio.to_thread(
            call_json,
            system=_load_gap_method(),
            user=user,
            schema=GDDECON_GAP_REPORT.json_schema,
            model=model,
            max_tokens=28000,
        )
        gaps = data.get("gaps") if isinstance(data, dict) else None
        if gaps:
            _OUT_DIR.mkdir(parents=True, exist_ok=True)
            doc_path = _OUT_DIR / f"{_slug(game)}-差距.md"
            doc_path.write_text(_render_gap_md(game, tree, gaps), encoding="utf-8")
            (_OUT_DIR / f"{_slug(game)}-差距.json").write_text(
                json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            out.update(ok=True, doc_path=str(doc_path), gap_count=len(gaps), gap_report=data)
            log.info("[gddecon-gap] %s: %d 差距 -> %s", game, len(gaps), doc_path)
        else:
            out["error"] = "call_json 未产出 gaps"
    except Exception as exc:  # noqa: BLE001
        out["error"] = str(exc)[:400]
        log.warning("[gddecon-gap] call_json 失败: %s", exc)
    return out


def run_gap_analysis(config: dict) -> dict:
    """同步入口：对一款游戏的方面树做应然↔实然↔差距全局盘点。

    config 同 deconstruction-request（game_name 必填；无现成方面树时会先跑拆解）。
    返回：{game_name, ok, doc_path, gap_count, error}。产出 <game>-差距.md。
    """
    return asyncio.run(_run_gap_async(config))


# ═══════════════════════════════════════════════════════════
# UI 设计生命周期 · 第一条：跟进 UI 标准
# ═══════════════════════════════════════════════════════════

def _load_ui_std_method() -> str:
    return _UI_STD_METHOD_PATH.read_text(encoding="utf-8")


def _ui_aspects_text(tree: dict | None) -> str:
    """从方面树里抽 UI 相关方面（顶层名或 id 含 ui/界面/信息/交互），给标准制定当锚。"""
    if not tree:
        return "(无方面树, 仅据规格制定)"
    aspects = tree.get("aspects") or []
    by_id = {a.get("id"): a for a in aspects}

    def top_of(aid: str) -> dict | None:
        cur = aid
        seen = set()
        while cur in by_id and by_id[cur].get("parent") and cur not in seen:
            seen.add(cur)
            cur = by_id[cur]["parent"]
        return by_id.get(cur)

    def is_ui(a: dict) -> bool:
        t = top_of(a.get("id", "")) or a
        blob = f"{t.get('id','')} {t.get('name','')}".lower()
        return ("ui" in blob) or ("界面" in blob)

    ui = [a for a in aspects if is_ui(a)]
    if not ui:
        return "(方面树无明显 UI 簇, 据规格制定)"
    return "\n".join(f"- {a.get('id')} | {a.get('name')} | {a.get('definition','')}" for a in ui)


def _render_ui_standard_md(game: str, data: dict) -> str:
    rules = data.get("rules") or []
    scope = data.get("scope") or ""
    out = [
        f"# {game} · UI 标准库{(' · ' + scope) if scope else ''}",
        "",
        "> 由 gddecon 跟进UI标准产出。每条=一句可检查的硬要求 + 怎么验 + 证据。"
        "是 评估UI / 建立UI / 调整UI 的依据；设计规格更新可重跑刷新。",
        "",
        f"- 规则数：{len(rules)}",
        "",
    ]
    for dim in ("信息", "交互"):
        items = [r for r in rules if r.get("dimension") == dim]
        out.append(f"## {dim}类（{len(items)} 条）")
        out.append("")
        for r in items:
            tag = "必须" if r.get("necessity") == "must" else "应当"
            out.append(f"### [{tag}] {r.get('name','')} `({r.get('id','')})`")
            out.append(f"- **要求**：{r.get('rule','')}")
            out.append(f"- **怎么验**：{r.get('check','')}")
            for ev in r.get("evidence") or []:
                out.append(f"  - 证据：{ev}")
            out.append("")
    other = [r for r in rules if r.get("dimension") not in ("信息", "交互")]
    if other:
        out.append("## 其他")
        out.append("")
        for r in other:
            out.append(f"### {r.get('name','')} `({r.get('id','')})` · {r.get('dimension','')}")
            out.append(f"- 要求：{r.get('rule','')}")
            out.append(f"- 怎么验：{r.get('check','')}")
            out.append("")
    return "\n".join(out) + "\n"


async def _run_ui_standard_async(cfg: dict) -> dict:
    from .formats import GDDECON_UI_STANDARD, register_formats
    try:
        from omnicompany.protocol.format import create_builtin_registry
        register_formats(create_builtin_registry())
    except Exception as exc:  # noqa: BLE001
        log.warning("[gddecon-ui-std] 注册 Formats 失败（非致命）: %s", exc)

    cfg = dict(cfg)
    cfg["design_sources"] = _as_list(cfg.get("design_sources"))
    cfg["build_evidence"] = _as_list(cfg.get("build_evidence"))
    game = cfg.get("game_name", "(未命名游戏)")
    model = cfg.get("model") or "qwen3.6-plus"

    tree = _load_tree(game)
    out: dict = {"game_name": game, "ok": False, "error": "", "doc_path": None, "rule_count": 0}
    try:
        from omnicompany.runtime.llm.structured import call_json
        context = _gather_context(cfg)
        user = (
            f"游戏：{game}\n方面树 UI 簇（标准要覆盖这些方面）：\n{_ui_aspects_text(tree)}\n\n"
            f"──────── UI 设计规格（应然）────────\n{context}"
        )
        data = await asyncio.to_thread(
            call_json,
            system=_load_ui_std_method(),
            user=user,
            schema=GDDECON_UI_STANDARD.json_schema,
            model=model,
            max_tokens=22000,
        )
        rules = data.get("rules") if isinstance(data, dict) else None
        if rules:
            _OUT_DIR.mkdir(parents=True, exist_ok=True)
            doc_path = _OUT_DIR / f"{_slug(game)}-UI标准.md"
            doc_path.write_text(_render_ui_standard_md(game, data), encoding="utf-8")
            (_OUT_DIR / f"{_slug(game)}-UI标准.json").write_text(
                json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            n_info = sum(1 for r in rules if r.get("dimension") == "信息")
            n_act = sum(1 for r in rules if r.get("dimension") == "交互")
            out.update(ok=True, doc_path=str(doc_path), rule_count=len(rules),
                       n_info=n_info, n_act=n_act, ui_standard=data)
            log.info("[gddecon-ui-std] %s: %d 规则(信息%d/交互%d) -> %s",
                     game, len(rules), n_info, n_act, doc_path)
        else:
            out["error"] = "call_json 未产出 rules"
    except Exception as exc:  # noqa: BLE001
        out["error"] = str(exc)[:400]
        log.warning("[gddecon-ui-std] call_json 失败: %s", exc)
    return out


def run_ui_standard(config: dict) -> dict:
    """同步入口：从 UI 设计规格 + 方面树 UI 簇制定可检查的 UI 标准库（信息/交互两类）。

    config 同 deconstruction-request（design_sources 给 UI 规格；game_name 必填）。
    返回：{game_name, ok, doc_path, rule_count, n_info, n_act, error}。产出 <game>-UI标准.md。
    """
    return asyncio.run(_run_ui_standard_async(config))


# ═══════════════════════════════════════════════════════════
# UI 设计生命周期 · 建立UI设计稿（按真后端逻辑·complete-expression）
# ═══════════════════════════════════════════════════════════

_UI_BUILD_METHOD_PATH = _PKG_DIR / "ui_build_method.md"
_BACKEND_FILES = (
    "src/game/core/game-state.ts",
    "src/game/core/game-command.ts",
    "src/game/core/battle-timeline-segment.ts",
    "src/game/core/battle-command-points.ts",
    "src/game/core/battle-card-zones.ts",
    "src/game/core/battle-v1-content.ts",
    "src/game/core/battle-auto-play.ts",
)

# 皮肤样板（class 词表的 CSS；在 Python 里，花括号无 format_map 之忧）。flex lane 让卡条首尾相接=塞满轨。
_UI_SKIN = """
*{box-sizing:border-box;margin:0;padding:0;font-family:"Segoe UI","Microsoft YaHei",sans-serif}
body{width:1440px;height:810px;background:radial-gradient(1300px 760px at 50% 38%,#16202b,#0b0e13 62%,#070a0e);color:#e6edf3;overflow:hidden}
.stage{position:relative;width:1440px;height:810px;padding:8px 10px;display:flex;flex-direction:column;gap:8px}
.tl{background:#0e141b;border:1px solid #28323e;border-radius:8px;padding:6px 8px;position:relative}
.tl-ruler{display:flex;gap:0;color:#5b6776;font-size:10px;border-bottom:1px solid #1c2733;padding-bottom:2px;margin-bottom:4px}
.tl-ruler span{flex:1;text-align:left}
.tl-lane{display:flex;align-items:stretch;height:24px;margin:3px 0;gap:1px}
.tl-lane-label{flex:0 0 96px;font-size:11px;color:#9fb4cc;display:flex;align-items:center;padding-right:6px}
.tl-lane.enemy .tl-lane-label{color:#ffb4b4}
.tl-bar{position:relative;display:flex;border:1px solid #2a3543;border-radius:3px;overflow:hidden;min-width:24px}
.tl-seg{display:block;height:100%}
.tl-seg.guard{background:#3d6bbf}.tl-seg.attack{background:#c2503f}.tl-seg.neutral{background:#3a4654}
.tl-seg.noop{background-image:repeating-linear-gradient(45deg,#0000,#0000 3px,#ffffff22 3px,#ffffff22 6px)}
.tl-name{position:absolute;left:3px;top:50%;transform:translateY(-50%);font-size:10px;color:#fff;white-space:nowrap;text-shadow:0 1px 2px #000}
.tl-playhead{position:absolute;top:18px;bottom:6px;width:2px;background:#e3b341;box-shadow:0 0 6px #e3b341;z-index:3}
.row{display:flex;gap:8px;flex:1;min-height:0}
.col{display:flex;flex-direction:column;gap:8px}
.panel{background:#0e141b;border:1px solid #28323e;border-radius:8px;padding:6px 8px;font-size:12px}
.panel h4{font-size:12px;color:#8b97a6;margin-bottom:4px;font-weight:600}
.actors{display:flex;flex-wrap:wrap;gap:6px}
.actor{border:1px solid #2a3543;border-radius:6px;padding:5px 7px;min-width:150px;font-size:11px;background:#121922}
.actor.enemy{border-color:#5a2630}
.actor b{font-size:12px}
.hpbar{height:6px;border-radius:3px;background:#0c1118;border:1px solid #26303c;overflow:hidden;margin:3px 0}
.hpfill{display:block;height:100%;background:#58a6ff}.actor.enemy .hpfill{background:#f85149}
.cp b{color:#e3b341}
.zone{font-size:11px;color:#cfe0f0}
.ops{display:flex;flex-wrap:wrap;gap:5px}
.opbtn{border:1px solid #2b3a49;background:#141c26;color:#cfe0f0;border-radius:5px;padding:4px 7px;font-size:11px}
.opbtn.danger{border-color:#7f1d1d;background:#2a1010;color:#ffb4b4}
.noop-tag{color:#e3b341;font-size:10px}
.feedback{color:#8b97a6;font-size:12px}
"""

_VIEWER_CSS = """
*{box-sizing:border-box;margin:0;padding:0;font-family:"Segoe UI","Microsoft YaHei",sans-serif}
html,body{height:100%;background:#05070a;color:#e6edf3;overflow:hidden}
.tool{height:46px;display:flex;align-items:center;gap:8px;padding:0 12px;background:#0d1218;border-bottom:1px solid #28323e;font-size:13px}
.tool b{font-size:14px}.tool .sub{color:#8b97a6;font-size:12px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.tool .sp{flex:1}
.btn{background:#16202b;border:1px solid #2b3a49;color:#cfe0f0;border-radius:6px;padding:5px 10px;cursor:pointer;font-size:13px}
.viewport{position:absolute;inset:46px 0 0 0;overflow:hidden;cursor:default;background:radial-gradient(1200px 600px at 50% 30%,#0c1219,#05070a)}
.viewport.space{cursor:grab}.viewport.panning{cursor:grabbing}
.canvas{position:absolute;left:0;top:0;width:1440px;height:810px;transform-origin:0 0}
.frame{width:1440px;height:810px;border:1px solid #28323e;border-radius:6px;background:#0b0e13}
"""

_VIEWER_JS = """
const vp=document.getElementById('vp'),cv=document.getElementById('cv');const SW=1440,SH=810;let s=1,tx=0,ty=0,pct=document.getElementById('pct');
function apply(){cv.style.transform='translate('+tx+'px,'+ty+'px) scale('+s+')';pct.textContent=Math.round(s*100)+'%';}
function fit(){const r=vp.getBoundingClientRect();s=Math.min(r.width/SW,r.height/SH)*0.97;tx=(r.width-SW*s)/2;ty=(r.height-SH*s)/2;apply();}
function reset100(){const r=vp.getBoundingClientRect();s=1;tx=(r.width-SW)/2;ty=20;apply();}
function z(k){const r=vp.getBoundingClientRect(),mx=r.width/2,my=r.height/2,ns=Math.max(.15,Math.min(6,s*k));tx=mx-(mx-tx)*(ns/s);ty=my-(my-ty)*(ns/s);s=ns;apply();}
let spaceDown=false,pan=false,sx,sy;
window.addEventListener('keydown',e=>{if(e.code==='Space'&&!e.repeat){spaceDown=true;vp.classList.add('space');e.preventDefault();}});
window.addEventListener('keyup',e=>{if(e.code==='Space'){spaceDown=false;vp.classList.remove('space');}});
vp.addEventListener('wheel',e=>{if(!e.ctrlKey)return;e.preventDefault();const r=vp.getBoundingClientRect(),mx=e.clientX-r.left,my=e.clientY-r.top,k=e.deltaY<0?1.1:1/1.1,ns=Math.max(.15,Math.min(6,s*k));tx=mx-(mx-tx)*(ns/s);ty=my-(my-ty)*(ns/s);s=ns;apply();},{passive:false});
vp.addEventListener('mousedown',e=>{if(!(e.button===1||spaceDown))return;pan=true;sx=e.clientX-tx;sy=e.clientY-ty;vp.classList.add('panning');e.preventDefault();});
window.addEventListener('mousemove',e=>{if(pan){tx=e.clientX-sx;ty=e.clientY-sy;apply();}});
window.addEventListener('mouseup',()=>{if(pan){pan=false;vp.classList.remove('panning');}});
vp.addEventListener('auxclick',e=>{if(e.button===1)e.preventDefault();});
fit();window.addEventListener('resize',fit);
"""


def _load_ui_build_method() -> str:
    return _UI_BUILD_METHOD_PATH.read_text(encoding="utf-8")


def _gather_backend_context(cfg: dict) -> str:
    root = pathlib.Path(cfg.get("build_root") or ".")
    # 清单模式：给了已人工核准的权威表达清单 → 只喂 清单 + 状态模型(精确字段形状)，
    # 不喂会诱发"未实装"幻觉的 reducer/auto-play 代码。
    inv = cfg.get("inventory")
    if inv:
        parts: list[str] = [
            "【权威表达清单（已人工核准 · 必须逐项完整体现；默认全部已实装，不要自行判断某项未实装）】\n" + str(inv)
        ]
        try:
            gs = (root / "src/game/core/game-state.ts").read_text(encoding="utf-8", errors="ignore")
            parts.append(f"\n\n===== 状态模型 game-state.ts（字段精确形状，供准确命名）=====\n{gs[:24000]}")
        except OSError:
            pass
        sample = cfg.get("runtime_sample")
        if sample:
            parts.append(f"\n\n===== 运行态采样 =====\n{sample}")
        return "".join(parts)

    CAP = 22000
    parts = []
    total = 0
    for rel in _BACKEND_FILES:
        try:
            txt = (root / rel).read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        if len(txt) > CAP:
            txt = txt[:CAP] + "\n…(截断)"
        block = f"\n\n===== {rel} =====\n{txt}"
        if total + len(block) > 160000:
            break
        parts.append(block)
        total += len(block)
    sample = cfg.get("runtime_sample")
    if sample:
        parts.append(f"\n\n===== 运行态采样（真跑探针）=====\n{sample}")
    return "".join(parts)


def _render_ui_design_viewer(game: str, scope: str, body_html: str) -> str:
    import html as _h
    design = ("<!DOCTYPE html><html lang='zh-CN'><head><meta charset='UTF-8'><style>"
              + _UI_SKIN + "</style></head><body>" + body_html + "</body></html>")
    srcdoc = _h.escape(design, quote=True)
    return (
        "<!DOCTYPE html><html lang='zh-CN'><head><meta charset='UTF-8'><title>"
        + _h.escape(game) + " · 战斗屏界面设计稿(按真后端)</title><style>" + _VIEWER_CSS + "</style></head><body>"
        "<div class='tool'><b>" + _h.escape(game) + " · 战斗屏界面设计稿</b>"
        "<span class='sub'>complete-expression · 按真后端逻辑(game-state/command/segment...) · " + _h.escape(scope) + "</span>"
        "<span class='sp'></span><button class='btn' onclick='z(1/1.2)'>－</button>"
        "<button class='btn' id='pct' onclick='reset100()'>100%</button>"
        "<button class='btn' onclick='z(1.2)'>＋</button><button class='btn' onclick='fit()'>适应</button></div>"
        "<div class='viewport' id='vp'><div class='canvas' id='cv'>"
        "<iframe class='frame' sandbox='allow-same-origin' srcdoc=\"" + srcdoc + "\"></iframe></div></div>"
        "<script>" + _VIEWER_JS + "</script></body></html>"
    )


async def _run_ui_build_async(cfg: dict) -> dict:
    from .formats import GDDECON_UI_DESIGN, register_formats
    try:
        from omnicompany.protocol.format import create_builtin_registry
        register_formats(create_builtin_registry())
    except Exception as exc:  # noqa: BLE001
        log.warning("[gddecon-ui-build] 注册 Formats 失败（非致命）: %s", exc)

    cfg = dict(cfg)
    game = cfg.get("game_name", "(未命名游戏)")
    scope = cfg.get("scope") or "战斗屏"
    model = cfg.get("model") or "qwen3.6-plus"
    out: dict = {"game_name": game, "ok": False, "error": "", "doc_path": None}
    try:
        from omnicompany.runtime.llm.structured import call_json
        context = _gather_backend_context(cfg)
        user = f"游戏：{game}　范围：{scope}\n\n──────── 真实后端代码 + 运行态 ────────\n{context}"
        data = await asyncio.to_thread(
            call_json, system=_load_ui_build_method(), user=user,
            schema=GDDECON_UI_DESIGN.json_schema, model=model, max_tokens=26000,
        )
        body = data.get("body_html") if isinstance(data, dict) else None
        if body:
            _OUT_DIR.mkdir(parents=True, exist_ok=True)
            doc_path = _OUT_DIR.parent / "ui_mockups" / f"{_slug(game)}-{_slug(scope)}-backend-design.html"
            doc_path.parent.mkdir(parents=True, exist_ok=True)
            doc_path.write_text(_render_ui_design_viewer(game, scope, body), encoding="utf-8")
            (_OUT_DIR.parent / "ui_mockups" / f"{_slug(game)}-{_slug(scope)}-backend-design.json").write_text(
                json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            out.update(ok=True, doc_path=str(doc_path), ui_design=data,
                       exposes=data.get("exposes", []), incomplete=data.get("incomplete", []))
            log.info("[gddecon-ui-build] %s: 设计稿 -> %s", game, doc_path)
        else:
            out["error"] = "call_json 未产出 body_html"
    except Exception as exc:  # noqa: BLE001
        out["error"] = str(exc)[:400]
        log.warning("[gddecon-ui-build] call_json 失败: %s", exc)
    return out


def run_ui_build(config: dict) -> dict:
    """同步入口：按真实后端逻辑产出一版 complete-expression 界面设计稿（HTML+缩放查看器）。

    config: game_name(必填) / build_root(walker-game 根，读真后端) / scope(默认战斗屏) / runtime_sample(可选)。
    返回：{game_name, ok, doc_path, exposes, incomplete, error}。产出 data/knowledge/ui_mockups/<game>-<scope>-backend-design.html。
    """
    return asyncio.run(_run_ui_build_async(config))


# ═══════════════════════════════════════════════════════════
# UI 设计生命周期 · 制定信息层级（界面信息维度·常驻/揭示 + 揭示即操作）
# ═══════════════════════════════════════════════════════════

_INFO_HIER_METHOD_PATH = _PKG_DIR / "info_hierarchy_method.md"
_TIER_BUCKETS = (
    ("T0", "T0 一眼必看（常驻）"),
    ("T1", "T1 常驻次级（常驻，可弱化）"),
    ("T2", "T2 按需揭示（展开才出现）"),
    ("T3", "T3 调试态（默认藏）"),
)


def _load_info_hier_method() -> str:
    return _INFO_HIER_METHOD_PATH.read_text(encoding="utf-8")


def _tier_bucket(tier: str) -> str:
    t = (tier or "").strip().upper()
    for pref, _label in _TIER_BUCKETS:
        if t.startswith(pref):
            return pref
    return "T2"


def _render_info_hierarchy_md(game: str, scope: str, data: dict) -> str:
    tiers = data.get("tiers") or []
    reveal_ops = data.get("reveal_ops") or []
    by_bucket: dict[str, list[dict]] = {}
    for row in tiers:
        by_bucket.setdefault(_tier_bucket(row.get("tier", "")), []).append(row)
    n_resident = sum(1 for r in tiers if (r.get("residency") or "").startswith("常"))
    n_reveal = len(tiers) - n_resident
    new_ops = [o for o in reveal_ops if "已有" not in (o.get("kind") or "")]

    out = [
        f"# {game} · {scope} · 信息层级表",
        "",
        "> 由 gddecon 信息层级管线产出（按玩家注意力 / 行为频次分层）。是把'完整体现'那版很密的设计稿"
        "按界面信息维度收拾的依据。不打分，tier 为分类词。",
        "",
        f"- 信息条目：{len(tiers)}（常驻 {n_resident} · 按需揭示 {n_reveal}）",
        f"- 揭示操作：{len(reveal_ops)}（其中纯 UI 新增操作 {len(new_ops)}）—— '展开信息'本身即操作，并入操作全集",
        "",
        "## 一、信息层级（常驻 vs 揭示）",
        "",
    ]
    for pref, label in _TIER_BUCKETS:
        rows = by_bucket.get(pref) or []
        if not rows:
            continue
        out.append(f"### {label}（{len(rows)} 条）")
        out.append("")
        for r in rows:
            head = f"- **{r.get('info','')}** · {r.get('residency','')}"
            if r.get("reveal_op"):
                head += f" · 揭示操作：{r['reveal_op']}"
            out.append(head)
            if r.get("rationale"):
                out.append(f"  - 为何此层：{r['rationale']}")
            drv = r.get("drivers") or []
            if drv:
                out.append(f"  - 驱动时刻：{', '.join(str(d) for d in drv)}")
        out.append("")

    out += ["## 二、揭示操作清单（'展开信息'即操作）", ""]
    existing = [o for o in reveal_ops if "已有" in (o.get("kind") or "")]
    for label, items in (("已有命令带出（已有操作的副产物）", existing), ("纯 UI 揭示 · 新增操作", new_ops)):
        if not items:
            continue
        out.append(f"### {label}（{len(items)} 条）")
        out.append("")
        for o in items:
            out.append(f"- **{o.get('name','')}**")
            if o.get("trigger"):
                out.append(f"  - 触发：{o['trigger']}")
            rv = o.get("reveals") or []
            if rv:
                out.append(f"  - 露出：{', '.join(str(x) for x in rv)}")
        out.append("")
    return "\n".join(out) + "\n"


def _write_info_hierarchy(game: str, scope: str, data: dict, out: dict) -> None:
    mdir = _OUT_DIR.parent / "ui_mockups"
    mdir.mkdir(parents=True, exist_ok=True)
    doc_path = mdir / f"{_slug(game)}-{_slug(scope)}-信息层级.md"
    doc_path.write_text(_render_info_hierarchy_md(game, scope, data), encoding="utf-8")
    (mdir / f"{_slug(game)}-{_slug(scope)}-信息层级.json").write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    out.update(ok=True, doc_path=str(doc_path), info_hierarchy=data,
               tier_count=len(data.get("tiers") or []),
               reveal_op_count=len(data.get("reveal_ops") or []))
    log.info("[gddecon-info-hier] %s: %d 信息 / %d 揭示操作 -> %s",
             game, len(data.get("tiers") or []), len(data.get("reveal_ops") or []), doc_path)


async def _run_info_hierarchy_async(cfg: dict) -> dict:
    from .formats import GDDECON_INFO_HIERARCHY, register_formats
    try:
        from omnicompany.protocol.format import create_builtin_registry
        register_formats(create_builtin_registry())
    except Exception as exc:  # noqa: BLE001
        log.warning("[gddecon-info-hier] 注册 Formats 失败（非致命）: %s", exc)

    cfg = dict(cfg)
    game = cfg.get("game_name", "(未命名游戏)")
    scope = cfg.get("scope") or "战斗屏"
    model = cfg.get("model") or "qwen3.6-plus"
    inv = cfg.get("inventory") or ""
    concept = cfg.get("concept") or ""
    out: dict = {"game_name": game, "ok": False, "error": "", "doc_path": None}
    try:
        from omnicompany.runtime.llm.structured import call_json
        user = (
            f"游戏：{game}　范围：{scope}\n\n核心循环：\n{concept}\n\n"
            f"──────── 完整信息清单（权威，每条都要在 tiers 里安置）────────\n{inv}"
        )
        data = await asyncio.to_thread(
            call_json, system=_load_info_hier_method(), user=user,
            schema=GDDECON_INFO_HIERARCHY.json_schema, model=model, max_tokens=24000,
        )
        tiers = data.get("tiers") if isinstance(data, dict) else None
        if tiers:
            data.setdefault("game_name", game)
            data.setdefault("scope", scope)
            _write_info_hierarchy(game, scope, data, out)
        else:
            out["error"] = "call_json 未产出 tiers"
    except Exception as exc:  # noqa: BLE001
        out["error"] = str(exc)[:400]
        log.warning("[gddecon-info-hier] call_json 失败: %s", exc)
    return out


def run_info_hierarchy(config: dict) -> dict:
    """同步入口：把一屏的完整信息清单按玩家注意力/行为频次排成信息层级表（常驻/揭示 + 揭示即操作）。

    config: game_name(必填) / scope(默认战斗屏) / inventory(完整表达清单文本) / concept(游戏核心循环)。
    返回：{game_name, ok, doc_path, tier_count, reveal_op_count, error}。产出 <game>-<scope>-信息层级.md。
    """
    return asyncio.run(_run_info_hierarchy_async(config))


# ═══════════════════════════════════════════════════════════
# UI 设计生命周期 · 操作交互模型（界面操作维度·信息层级的对偶）
# ═══════════════════════════════════════════════════════════

_INTERACTION_METHOD_PATH = _PKG_DIR / "interaction_model_method.md"
_OP_GROUPS = ("布阵", "重整", "时间控制", "揭示")


def _load_interaction_method() -> str:
    return _INTERACTION_METHOD_PATH.read_text(encoding="utf-8")


def _render_interaction_model_md(game: str, scope: str, data: dict) -> str:
    ops = data.get("operations") or []
    principles = data.get("principles") or []
    buckets: dict[str, list[dict]] = {g: [] for g in _OP_GROUPS}
    other: list[dict] = []
    for o in ops:
        g = o.get("group", "")
        placed = False
        for G in _OP_GROUPS:
            if G in g:
                buckets[G].append(o)
                placed = True
                break
        if not placed:
            other.append(o)

    out = [
        f"# {game} · {scope} · 操作交互模型",
        "",
        "> 由 gddecon 操作交互模型管线产出（界面操作维度，信息层级的对偶）。逐操作定：频次×手势 / 反馈 / "
        "确认安全 / 可用相位 / 选择模型。是把'完整体现'那版设计稿按界面操作维度收拾的依据。不打分。",
        "",
        f"- 操作数：{len(ops)}",
        "",
    ]
    if principles:
        out += ["## 贯穿性交互原则", ""]
        out += [f"- {p}" for p in principles]
        out += [""]

    proposed = [o for o in ops if "建议" in (o.get("backend", "") or "")]
    if proposed:
        out += [
            "## ⚠ 管线发现的后端结构缺口（建议新增 · 后端 game-command 里尚无）", "",
            "> 这些是交互分析里推断'应该有'、但当前真后端**不存在**对应指令的操作。"
            "**不要当现有操作直接体现**——是给后端的设计建议，需先在后端落地再表达。", "",
        ]
        for o in proposed:
            out.append(f"- **{o.get('op','')}**（{o.get('group','')}）：{o.get('rationale','')}")
        out += [""]

    out += ["## 逐操作交互规范", ""]

    def emit(o: dict) -> None:
        tag = f" · {o['backend']}" if o.get("backend") else ""
        out.append(f"- **{o.get('op','')}**{tag} · {o.get('frequency','')}频 · {o.get('gesture','')} · {o.get('directness','')}")
        out.append(f"  - 反馈：{o.get('feedback','')}")
        out.append(f"  - 安全：{o.get('safety','')} · 相位：{o.get('availability','')} · 选择：{o.get('selection_model','')}")
        if o.get("rationale"):
            out.append(f"  - 理由：{o['rationale']}")
        out.append("")

    for G in _OP_GROUPS:
        grp = buckets[G]
        if not grp:
            continue
        out.append(f"### {G}（{len(grp)} 个）")
        out.append("")
        for o in grp:
            emit(o)
    if other:
        out.append("### 其他")
        out.append("")
        for o in other:
            emit(o)
    return "\n".join(out) + "\n"


def _write_interaction_model(game: str, scope: str, data: dict, out: dict) -> None:
    mdir = _OUT_DIR.parent / "ui_mockups"
    mdir.mkdir(parents=True, exist_ok=True)
    doc_path = mdir / f"{_slug(game)}-{_slug(scope)}-操作交互模型.md"
    doc_path.write_text(_render_interaction_model_md(game, scope, data), encoding="utf-8")
    (mdir / f"{_slug(game)}-{_slug(scope)}-操作交互模型.json").write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    out.update(ok=True, doc_path=str(doc_path), interaction_model=data,
               op_count=len(data.get("operations") or []))
    log.info("[gddecon-interaction] %s: %d 操作 -> %s",
             game, len(data.get("operations") or []), doc_path)


async def _run_interaction_model_async(cfg: dict) -> dict:
    from .formats import GDDECON_INTERACTION_MODEL, register_formats
    try:
        from omnicompany.protocol.format import create_builtin_registry
        register_formats(create_builtin_registry())
    except Exception as exc:  # noqa: BLE001
        log.warning("[gddecon-interaction] 注册 Formats 失败（非致命）: %s", exc)

    cfg = dict(cfg)
    game = cfg.get("game_name", "(未命名游戏)")
    scope = cfg.get("scope") or "战斗屏"
    model = cfg.get("model") or "qwen3.6-plus"
    ops = cfg.get("ops") or cfg.get("operations") or ""
    concept = cfg.get("concept") or ""
    out: dict = {"game_name": game, "ok": False, "error": "", "doc_path": None}
    try:
        from omnicompany.runtime.llm.structured import call_json
        user = (
            f"游戏：{game}　范围：{scope}\n\n核心循环：\n{concept}\n\n"
            f"──────── 操作全集（每个操作都要在 operations 里一行）────────\n{ops}"
        )
        data = await asyncio.to_thread(
            call_json, system=_load_interaction_method(), user=user,
            schema=GDDECON_INTERACTION_MODEL.json_schema, model=model, max_tokens=24000,
        )
        operations = data.get("operations") if isinstance(data, dict) else None
        if operations:
            data.setdefault("game_name", game)
            data.setdefault("scope", scope)
            _write_interaction_model(game, scope, data, out)
        else:
            out["error"] = "call_json 未产出 operations"
    except Exception as exc:  # noqa: BLE001
        out["error"] = str(exc)[:400]
        log.warning("[gddecon-interaction] call_json 失败: %s", exc)
    return out


def run_interaction_model(config: dict) -> dict:
    """同步入口：把一屏的操作全集排成操作交互模型（频次×手势/反馈/确认/相位/选择，界面操作维度）。

    config: game_name(必填) / scope(默认战斗屏) / ops(操作全集文本) / concept(游戏核心循环)。
    返回：{game_name, ok, doc_path, op_count, error}。产出 <game>-<scope>-操作交互模型.md。
    """
    return asyncio.run(_run_interaction_model_async(config))


# ═══════════════════════════════════════════════════════════
# TeamSpec 拓扑声明（供 omni describe / register；真实执行走 run_deconstruction）
# ═══════════════════════════════════════════════════════════

from omnicompany.protocol.team import NodeKind, NodeMaturity, TeamNode, TeamSpec
from omnicompany.protocol.anchor import TransformerSpec, TransformMethod


def build_team() -> TeamSpec:
    """拓扑声明：deconstruction-request → aspect-tree（单 agent 节点）。

    真实多步执行入口是 gddecon.pipeline.run_deconstruction。
    """
    nodes = [
        TeamNode(
            id="deconstruct",
            kind=NodeKind.TRANSFORMER,
            transformer=TransformerSpec(
                id="gddecon-deconstructor",
                name="AspectTreeDeconstructor",
                description=(
                    "统一 AgentNodeLoop（只读）。读设计源 + 当前 build，应用方面发现法"
                    "（透镜 × 展开规则 × 完备性），产出该游戏的方面树。编排器确定性落盘 .md。"
                ),
                from_format="gddecon.deconstruction-request",
                to_format="gddecon.aspect-tree",
                method=TransformMethod.LLM,
            ),
            maturity=NodeMaturity.GROWING,
        ),
    ]
    return TeamSpec(
        id="gddecon-aspect-tree",
        name="游戏设计拆解管线",
        description=(
            "读一款游戏的设计源 + 当前 build，用方面发现法产出方面树（设计应被拆成哪些维度）。"
            "TeamSpec 是拓扑声明，真实执行由 gddecon.pipeline.run_deconstruction 驱动。"
        ),
        purpose="发现一款游戏的设计大概分多少方面、如何嵌套、每个怎么发现——作为决策树建构的骨架。",
        nodes=nodes,
        edges=[],
        entry="deconstruct",
        tags=["domain.gddecon", "workflow.knowledge-generation"],
    )
