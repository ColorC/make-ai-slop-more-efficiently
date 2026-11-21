# [OMNI] origin=arch-ratchet-surgery ts=2026-08-18 type=infra
# 本文件是 ccdaemon/pty_routes.py 的 exec 装载分片：无 import、不可独立 import，
# 名字在主模块 globals 中解析（与拆分前单文件语义等价）。
# 语义来源: PTY 投递确认 —— 正文+回车分写后, 按 provider 语义信号确认真开了一轮;
# 终端字节数不可信(256KB 重放上限+TUI 重绘乱长)。
# 2026-08-18 实证补丁: codex 忙碌(回合进行中)时回车不入队 —— 正文滞留 composer,
# 直到某次回车落在空闲 TUI 上才随后续消息拼团提交(观测: 三条消息拼成一个大回合,
# 送达延迟不可控)。旧实现此时直接返回 already_working, 把滞留伪装成成功。
# 修法: 忙碌即挂 deliver-on-idle 守望者, 回合一结束立刻补回车并确认新回合启动;
# 空 composer 上的回车是无操作, 已送达的消息不会被二次提交。

_PROMPT_CONFIRM_WAIT_S = 4.0
_DELIVERY_MAX_WAIT_S = 1800.0
_DELIVERY_IDLE_ENTER_MAX = 3
_PENDING_DELIVERY_TASKS: dict[str, Any] = {}
_CODEX_PLAN_OVERLAY_MARKERS = ("Create a plan?", "use Plan mode", "esc dismiss")


def _turn_working(sess: Any) -> bool:
    try:
        return bool(sess.provider_runtime.working(now=time.time()))
    except Exception:  # noqa: BLE001
        return False


async def _wait_turn_working(sid: str, seconds: float) -> str:
    manager = get_manager()
    deadline = time.monotonic() + max(0.0, seconds)
    while True:
        sess = manager.get(sid)
        if sess is None or getattr(sess, "closed", False):
            return "gone"
        if _turn_working(sess):
            return "started"
        if time.monotonic() >= deadline:
            return "idle"
        await asyncio.sleep(_SEED_POLL_S)


async def _dismiss_codex_plan_overlay(sid: str, sess: Any, manager: Any) -> bool:
    """Dismiss Codex's plan suggestion only when it is on the current screen.

    The suggestion consumes Enter while an injected prompt remains in the
    composer.  The host screen model lets us distinguish the live overlay from
    the same words in historical PTY output, so Escape is never sent on a mere
    ring-buffer match.
    """
    if not str(getattr(sess, "provider", "")).startswith("codex"):
        return False
    request_screen = getattr(manager, "_request_host_screen", None)
    if request_screen is None:
        return False
    try:
        frame = await request_screen(sess, timeout=0.75)
    except Exception:  # noqa: BLE001
        return False
    if not frame:
        return False
    rows = max(1, int(getattr(sess, "rows", 36) or 36))
    current_tail = "\n".join(frame.replace("\r", "\n").splitlines()[-(rows * 2 + 4) :])
    if not all(marker in current_tail for marker in _CODEX_PLAN_OVERLAY_MARKERS):
        return False
    await manager.write(sid, "\x1b")
    await asyncio.sleep(_SEED_POLL_S)
    await manager.write(sid, "\r")
    return True


def _ensure_delivery_watcher(sid: str) -> None:
    """One watcher per session; concurrent sends piggyback on the same Enter."""
    task = _PENDING_DELIVERY_TASKS.get(sid)
    if task is not None and not task.done():
        return
    _PENDING_DELIVERY_TASKS[sid] = asyncio.create_task(
        _deliver_on_idle(sid), name=f"pty-deliver-on-idle-{sid}"
    )


async def _deliver_on_idle(sid: str) -> None:
    """Re-press Enter the moment the target turn ends, so composer-stranded
    text submits immediately instead of waiting for the next lucky Enter.
    Bounded: gives up after _DELIVERY_MAX_WAIT_S or a few no-op Enters on an
    idle TUI (empty composer / dead process — see the turn-end deadlock case)."""
    manager = get_manager()
    deadline = time.monotonic() + _DELIVERY_MAX_WAIT_S
    idle_enters = 0
    try:
        while time.monotonic() < deadline and idle_enters < _DELIVERY_IDLE_ENTER_MAX:
            sess = manager.get(sid)
            if sess is None or getattr(sess, "closed", False):
                return
            if not _turn_working(sess):
                try:
                    if not await _dismiss_codex_plan_overlay(sid, sess, manager):
                        await manager.write(sid, "\r")
                except KeyError:
                    return
                idle_enters += 1
                if await _wait_turn_working(sid, _PROMPT_CONFIRM_WAIT_S) == "started":
                    return
            await asyncio.sleep(_SEED_POLL_S)
    finally:
        _PENDING_DELIVERY_TASKS.pop(sid, None)


async def _confirm_submitted_prompt(sid: str, sess: Any) -> str:
    """Confirm an idle observable TUI consumed Enter; busy targets hand off to
    the deliver-on-idle watcher instead of reporting a fake success."""
    if getattr(sess, "provider_runtime", None) is None:
        return "unobservable"
    if _turn_working(sess):
        _ensure_delivery_watcher(sid)
        return "queued_behind_turn"
    status = await _wait_turn_working(sid, _PROMPT_CONFIRM_WAIT_S)
    if status != "idle":
        return "submitted" if status == "started" else status
    manager = get_manager()
    overlay_dismissed = await _dismiss_codex_plan_overlay(sid, sess, manager)
    if not overlay_dismissed:
        await manager.write(sid, "\r")
    status = await _wait_turn_working(sid, _PROMPT_CONFIRM_WAIT_S)
    if status == "started":
        return "recovered_overlay" if overlay_dismissed else "recovered"
    if status == "gone":
        return "gone"
    _ensure_delivery_watcher(sid)
    return "unconfirmed"
