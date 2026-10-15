# [OMNI] origin=ai-ide ts=2026-06-06 type=service
"""N2d 确定性多-agent workflow 编排器 (fan-out → 收集 → 综合)。

背景: 总控目前只能 `omni worker spawn` 单发, 多-agent 编排靠总控 LLM 逐轮 ad-hoc
(不确定、费 token)。本模块给一个**确定性引擎**:

  1. fan-out: 一次性 spawn K 个 subagent (一个子任务一个), 复用现有 spawn 逻辑
     (_build_spawn_prompt 注入 plan/standards/deliverable§ + create_session 起会话)。
  2. 收集: 订阅 chat_manager 的 `subagent.completed` 事件, 子任务完成时记下它提交到
     审阅台的材料 (2026-08-20 融合后: review_store.list_materials 按 source_subagent_id 过滤)。
  3. 综合: fan-out 全部完成后, 若给了 synthesize 提示, 自动 spawn 一个综合 subagent,
     prompt 里带上 fan-out 各子任务的材料清单, 让它读取并汇总、再 `omni review submit`。

事件驱动、非阻塞 (subagent 跑数分钟, 不能让 CLI / 控制器 Bash 卡住, 故 run 立刻返回
wf_id, 后续靠事件推进)。实例落盘 `data/boss_sight/workflows/<wf_id>.json`。

不做 (留作后续, 见 N2 计划 §5): 任意 DAG / 条件边 / 失败重试 / 超时取消 /
verify 独立阶段 (当前 synth 即"综合", 想做 verify 把它当一个 synth 子任务即可)。
subagent 并发上限复用 chat.py 的 MAX_LIVE_SUBAGENTS (create 时会拒)。
"""
from __future__ import annotations

import asyncio
import dataclasses
import json
import logging
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class WfTask:
    """一个子任务 = 一个 subagent。role=fanout 是并行铺开的子任务, synthesize 是综合任务。"""

    prompt: str
    role: str = "fanout"  # fanout | synthesize
    subagent_id: str | None = None
    status: str = "pending"  # pending | running | done
    material_ids: list[str] = field(default_factory=list)
    preview: str = ""

    def to_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "WfTask":
        return cls(
            prompt=str(d.get("prompt") or ""),
            role=str(d.get("role") or "fanout"),
            subagent_id=d.get("subagent_id"),
            status=str(d.get("status") or "pending"),
            material_ids=list(d.get("material_ids") or []),
            preview=str(d.get("preview") or ""),
        )


@dataclass
class Workflow:
    id: str
    title: str
    plan_id: str
    cwd: str
    provider: str
    model: str | None
    synthesize_prompt: str | None
    tasks: list[WfTask]
    status: str = "running"  # running | synthesizing | done | failed
    synth_spawned: bool = False
    created_at: float = 0.0
    updated_at: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        d = dataclasses.asdict(self)
        d["tasks"] = [t.to_dict() for t in self.tasks]
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Workflow":
        return cls(
            id=str(d["id"]),
            title=str(d.get("title") or ""),
            plan_id=str(d.get("plan_id") or ""),
            cwd=str(d.get("cwd") or ""),
            provider=str(d.get("provider") or "claude_code"),
            model=d.get("model"),
            synthesize_prompt=d.get("synthesize_prompt"),
            tasks=[WfTask.from_dict(t) for t in (d.get("tasks") or [])],
            status=str(d.get("status") or "running"),
            synth_spawned=bool(d.get("synth_spawned")),
            created_at=float(d.get("created_at") or 0.0),
            updated_at=float(d.get("updated_at") or 0.0),
        )

    def fanout_tasks(self) -> list[WfTask]:
        return [t for t in self.tasks if t.role == "fanout"]

    def public_view(self) -> dict[str, Any]:
        ft = self.fanout_tasks()
        return {
            "id": self.id,
            "title": self.title,
            "plan_id": self.plan_id,
            "status": self.status,
            "fanout_total": len(ft),
            "fanout_done": sum(1 for t in ft if t.status == "done"),
            "has_synthesize": bool(self.synthesize_prompt),
            "synth_spawned": self.synth_spawned,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "tasks": [t.to_dict() for t in self.tasks],
        }


class WorkflowOrchestrator:
    """编排器单例。在 ccdaemon lifespan 里 attach 到 chat_manager 事件。

    注入点:
      - spawn_fn(prompt, plan_id, cwd, provider, model) -> awaitable[str(subagent_id)]
        默认走 _default_spawn (in-process 复用 create_session)。测试可注入假 spawn。
      - materials_fn(subagent_id) -> list[str(material_id)]  收集某 subagent 提交的材料。
    """

    def __init__(
        self,
        *,
        store_root: Path,
        spawn_fn: Any = None,
        materials_fn: Any = None,
    ) -> None:
        self._root = Path(store_root) / "boss_sight" / "workflows"
        self._root.mkdir(parents=True, exist_ok=True)
        self._workflows: dict[str, Workflow] = {}
        self._subagent_index: dict[str, str] = {}  # subagent_id -> wf_id (快速判属于哪个工作流)
        self._lock = asyncio.Lock()
        self._spawn_fn = spawn_fn or _default_spawn
        self._materials_fn = materials_fn or _default_materials
        self._load_persisted()

    # ── 持久化 ──────────────────────────────────────────────────────────────
    def _path(self, wf_id: str) -> Path:
        return self._root / f"{wf_id}.json"

    def _persist(self, wf: Workflow) -> None:
        wf.updated_at = _now()
        try:
            self._path(wf.id).write_text(
                json.dumps(wf.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except Exception:  # noqa: BLE001
            logger.exception("workflow persist failed for %s", wf.id)

    def _load_persisted(self) -> None:
        for f in sorted(self._root.glob("*.json")):
            try:
                wf = Workflow.from_dict(json.loads(f.read_text(encoding="utf-8")))
            except Exception:  # noqa: BLE001
                logger.exception("workflow load failed: %s", f)
                continue
            self._workflows[wf.id] = wf
            for t in wf.tasks:
                if t.subagent_id:
                    self._subagent_index[t.subagent_id] = wf.id

    # ── 创建 + fan-out ──────────────────────────────────────────────────────
    async def create_and_run(self, spec: dict[str, Any]) -> dict[str, Any]:
        title = str(spec.get("title") or "").strip() or "未命名 workflow"
        plan_id = str(spec.get("plan_id") or "").strip()
        if not plan_id:
            raise ValueError("workflow 需要 plan_id (工作流是 plan 范围的)")
        raw_tasks = spec.get("tasks") or []
        tasks = [str(t).strip() for t in raw_tasks if str(t).strip()]
        if not tasks:
            raise ValueError("workflow 至少要 1 个 fan-out 子任务 (tasks)")
        provider = str(spec.get("provider") or "claude_code")
        model = spec.get("model")
        cwd = str(spec.get("cwd") or "")
        synth = spec.get("synthesize")
        synth = str(synth).strip() if synth else None

        wf = Workflow(
            id=f"wf-{uuid.uuid4().hex[:10]}",
            title=title,
            plan_id=plan_id,
            cwd=cwd,
            provider=provider,
            model=model,
            synthesize_prompt=synth,
            tasks=[WfTask(prompt=t, role="fanout") for t in tasks],
            created_at=_now(),
            updated_at=_now(),
        )
        async with self._lock:
            self._workflows[wf.id] = wf
            self._persist(wf)
        # fan-out: 逐个 spawn (create 串行避免并发上限竞态; subagent 本身并行跑)。
        for t in wf.fanout_tasks():
            try:
                sid = await self._spawn_fn(
                    prompt=t.prompt, plan_id=plan_id, cwd=cwd, provider=provider, model=model,
                )
                t.subagent_id = sid
                t.status = "running"
                self._subagent_index[sid] = wf.id
            except Exception as e:  # noqa: BLE001
                logger.exception("workflow %s fan-out spawn failed", wf.id)
                t.status = "done"  # 起不来当作终态, 不卡住整体推进
                t.preview = f"spawn failed: {type(e).__name__}: {e}"
        async with self._lock:
            self._persist(wf)
        logger.info("workflow %s started: %d fan-out tasks, synth=%s", wf.id, len(tasks), bool(synth))
        return wf.public_view()

    # ── 事件推进 (订阅 chat_manager) ────────────────────────────────────────
    def on_event(self, sess: Any, event_type: str, payload: dict[str, Any], tags: Any) -> None:
        """同步回调 (chat_manager._emit_session_event 调)。必须快 + 不抛。

        只关心 subagent.completed, 且该 subagent 属于某个 running workflow。命中则丢给
        async task 推进 (收集材料 / 判断是否综合)。"""
        try:
            if event_type != "subagent.completed":
                return
            sid = (payload or {}).get("subagent_id") or getattr(sess, "id", None)
            if not sid or sid not in self._subagent_index:
                return
            preview = (payload or {}).get("last_assistant_preview") or ""
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                logger.warning("workflow on_event: no running loop, skip advance for %s", sid)
                return
            loop.create_task(self._advance(sid, preview))
        except Exception:  # noqa: BLE001
            logger.exception("workflow on_event failed")

    async def _advance(self, subagent_id: str, preview: str) -> None:
        async with self._lock:
            wf_id = self._subagent_index.get(subagent_id)
            wf = self._workflows.get(wf_id) if wf_id else None
            if wf is None or wf.status not in ("running", "synthesizing"):
                return
            # 标记完成的子任务 + 收集它的材料。
            task = next((t for t in wf.tasks if t.subagent_id == subagent_id), None)
            if task is None:
                return
            task.status = "done"
            task.preview = (preview or task.preview or "")[:300]
            try:
                task.material_ids = list(self._materials_fn(subagent_id))
            except Exception:  # noqa: BLE001
                logger.exception("workflow collect materials failed for %s", subagent_id)
            # synth 子任务完成 → 整个工作流完成。
            if task.role == "synthesize":
                wf.status = "done"
                self._persist(wf)
                logger.info("workflow %s done (synth complete)", wf.id)
                return
            # fan-out 子任务: 检查是否全部完成。
            fanout = wf.fanout_tasks()
            if not all(t.status == "done" for t in fanout):
                self._persist(wf)
                return
            # 全部 fan-out 完成。
            if not wf.synthesize_prompt:
                wf.status = "done"
                self._persist(wf)
                logger.info("workflow %s done (no synth phase)", wf.id)
                return
            if wf.synth_spawned:
                self._persist(wf)
                return
            wf.synth_spawned = True
            wf.status = "synthesizing"
            self._persist(wf)
            spawn_args = dict(plan_id=wf.plan_id, cwd=wf.cwd, provider=wf.provider, model=wf.model)
            synth_prompt = self._compose_synth_prompt(wf)
        # 锁外 spawn synth (spawn 内部还会拿别的锁, 避免嵌套)。
        try:
            sid = await self._spawn_fn(prompt=synth_prompt, **spawn_args)
        except Exception as e:  # noqa: BLE001
            logger.exception("workflow %s synth spawn failed", wf.id)
            async with self._lock:
                wf.status = "failed"
                self._persist(wf)
            return
        async with self._lock:
            synth_task = WfTask(prompt=synth_prompt, role="synthesize", subagent_id=sid, status="running")
            wf.tasks.append(synth_task)
            self._subagent_index[sid] = wf.id
            self._persist(wf)
        logger.info("workflow %s fan-out complete → synth subagent %s spawned", wf.id, sid)

    def _compose_synth_prompt(self, wf: Workflow) -> str:
        lines = [
            wf.synthesize_prompt or "综合上述 fan-out 子任务的产物。",
            "",
            "## fan-out 阶段产物 (来自审阅台)",
        ]
        for i, t in enumerate(wf.fanout_tasks(), 1):
            mats = ", ".join(t.material_ids) if t.material_ids else "(未提交材料)"
            lines.append(f"- 子任务 {i}: subagent={t.subagent_id} 材料={mats}")
            if t.preview:
                lines.append(f"  预览: {t.preview[:200]}")
            lines.append(f"  原始指令: {t.prompt[:200]}")
        lines += [
            "",
            f"请用 `omni review list --plan-id {wf.plan_id}` 查看上述材料(或直接读材料文件), "
            "综合成一份最终结论, 然后用 `omni review submit` 提交综合产物。",
        ]
        return "\n".join(lines)

    # ── 查询 ────────────────────────────────────────────────────────────────
    def get(self, wf_id: str) -> dict[str, Any] | None:
        wf = self._workflows.get(wf_id)
        return wf.public_view() if wf else None

    def list_all(self) -> list[dict[str, Any]]:
        return [
            wf.public_view()
            for wf in sorted(self._workflows.values(), key=lambda w: w.created_at, reverse=True)
        ]


def _now() -> float:
    return time.time()


async def _default_spawn(*, prompt: str, plan_id: str, cwd: str, provider: str, model: str | None) -> str:
    """in-process 复用 create_session + _build_spawn_prompt 起一个 subagent, 返回 subagent_id。"""
    from omnicompany.dashboard.ccdaemon.api import CreateChatSessionBody, create_session
    from omnicompany.packages.services.boss_sight.controller.tools import _build_spawn_prompt
    from omnicompany.core.config import omni_workspace_root

    ws = omni_workspace_root()
    composed, _audit = _build_spawn_prompt(
        plan_id=plan_id,
        initial_prompt=prompt,
        extra_standards=[],
        extra_templates=[],
        skip_plan_inject=False,
        ws=ws,
    )
    body = CreateChatSessionBody(
        cwd=cwd or str(ws),
        model=model,
        provider=provider,
        initial_prompt=composed,
        from_controller=True,
        active_plan=plan_id,
    )
    meta = await create_session(body)
    return str(meta.get("id"))


def _default_materials(subagent_id: str) -> list[str]:
    """收集某 subagent 提交到审阅台的材料 id。

    2026-08-20 审阅库融合换源: 读共享核心 review_store 的画布投影（旧实现读
    reviewstage MaterialStore）。对齐旧口径 list(subagent_id=..., include_archived=True)：
    含归档、排除内部治理件（旧 list() 默认 include_internal=False）。
    """
    from omnicompany.packages.services._core import review_store

    from omnicompany.packages.services.boss_sight.cockpit import _row_is_internal

    return [
        str(m.get("id") or "")
        for m in review_store.list_materials(include_archived=True)
        if str(m.get("source_subagent_id") or "") == subagent_id and not _row_is_internal(m)
    ]


_orchestrator: WorkflowOrchestrator | None = None


def get_orchestrator() -> WorkflowOrchestrator:
    global _orchestrator
    if _orchestrator is None:
        from omnicompany.core.config import omni_workspace_root

        _orchestrator = WorkflowOrchestrator(store_root=omni_workspace_root() / "data")
    return _orchestrator


__all__ = ["Workflow", "WfTask", "WorkflowOrchestrator", "get_orchestrator"]
