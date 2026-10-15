"""Provider-specific runtime-state markers for terminal agents.

Agent TUIs repaint while idle, so byte activity is not evidence of work.  This
module recognizes only provider-owned busy/idle markers and keeps a short
heartbeat for animated markers that disappear when a turn finishes.
"""

from __future__ import annotations

import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping

from .provider_identity import (
    PTY_AGENT_PROVIDER_IDS,
    second_account_env,
    registered_cli_provider,
)


PROVIDER_BUSY_HEARTBEAT_S = float(
    os.environ.get("OMNI_PROVIDER_BUSY_HEARTBEAT_S") or 3.0
)
# A latched (CLI-pushed) busy marker is only trustworthy while the TUI keeps
# producing *some* output: every supported provider repaints its status line or
# re-writes an animated title many times a minute during a live turn.  Total
# output silence far beyond any measured in-turn gap therefore means the
# provider froze or finished without its idle transition ever reaching us
# (2026-08-18: a 20h codex session deadlocked at turn end with the spinner
# title still latched — the send gate then refused every prompt forever).
# After this window the latch decays instead of pinning "working" for good.
PROVIDER_LATCHED_STALE_S = float(
    os.environ.get("OMNI_PROVIDER_LATCHED_STALE_S") or 900.0
)
# Kiro's native Thinking/tool animation can pause for several seconds between
# provider-owned frames during a live turn. Keep that screen-derived signal
# finite (so stale redraws still decay), but long enough to bridge normal gaps.
KIRO_BUSY_HEARTBEAT_S = float(
    os.environ.get("OMNI_KIRO_BUSY_HEARTBEAT_S") or 10.0
)
_TAIL_CHARS = 2048
_BRAILLE_FRAMES = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
# Claude Code normally uses the braille pair. Some current builds switch to
# half-circle title frames while a long tool/subagent task is active.
_CLAUDE_FRAMES = "⠂⠐◐◑"
_CLAUDE_IDLE_FRAMES = "✳"
_KIMI_MOON_FRAMES = "🌑🌒🌓🌔🌕🌖🌗🌘"

_OSC_TITLE_RE = re.compile(
    r"\x1b\](?:0|2);(?P<title>[^\x07\x1b]{0,512})(?:\x07|\x1b\\)"
)
_OSC_PROGRESS_RE = re.compile(
    r"\x1b\]9;4;(?P<state>\d+)(?:;(?P<progress>\d{0,3}))?(?:\x07|\x1b\\)"
)
_CODEX_WORKING_RE = re.compile(
    r"Working\s*(?:\([^\r\n]{0,160})?.{0,80}?esc\s+to\s+interrupt",
    re.IGNORECASE,
)
_INTERRUPT_RE = re.compile(
    r"esc\s+(?:(?:again\s+)?to\s+)?interrupt",
    re.IGNORECASE,
)
_QODER_BUSY_RE = re.compile(
    r"(?:thinking|working)\.{2,3}",
    re.IGNORECASE,
)
_KIMI_TEXT_RE = re.compile(
    rf"[{re.escape(_BRAILLE_FRAMES)}]\s*(?:thinking|working)\.\.\.",
    re.IGNORECASE,
)
_KIMI_MOON_RE = re.compile(f"[{_KIMI_MOON_FRAMES}]")
_ANSI_SGR = r"(?:\x1b\[[0-9;]*m)*"
_KIRO_BUSY_RE = re.compile(
    rf"(?:Thinking\.\.\.{_ANSI_SGR}\s*{_ANSI_SGR}\(esc to cancel\)"
    rf"|Kiro{_ANSI_SGR} is working.*Type to steer)",
    re.IGNORECASE,
)
# Newer Kiro CLI builds animate tool rows with partial-circle frames such as
# rotating Grep / Read / Bash rows instead of retaining the older
# ``Thinking...`` footer.  The final completed row uses a solid bullet; exclude it
# deliberately so an idle redraw of completed tools cannot refresh the busy
# heartbeat.
_KIRO_TOOL_SPINNER_RE = re.compile(
    r"[\u25d4\u25d1\u25d5](?:\x1b\[[0-9;]*m)*\s+"
    r"(?:\x1b\[[0-9;]*m)*[^\r\n\x1b]"
)
_KIRO_IDLE_RE = re.compile(
    r"ask a question or describe a task",
    re.IGNORECASE,
)
_OPENCODE_STATIC_RE = re.compile(r"\[⋯\]")
# opencode 1.18 实测 (2026-08 本机 PTY capture): footer 每帧全屏重绘, 字与字之间
# 随时插入 SGR 颜色序列与 CSI 光标定位, 例如
#   esc \x1b[0m\x1b[31;18H\x1b[38;2;128;128;128m\x1b[48;2;10;10;10minterrupt
# 普通 ``esc\s+interrupt`` 匹配不到, 导致 opencode 的 working 状态从未点亮。
# 块波动画同理: ■ + SGR/光标 + ⬝⬝⬝… 交替出现。
_OPENCODE_SEGMENT_GAP = r"(?:\x1b\[[0-9;?]*[a-zA-Z]|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\))*"
_OPENCODE_INTERRUPT_RE = re.compile(
    rf"esc{_OPENCODE_SEGMENT_GAP}\s+"
    rf"(?:(?:(?:again{_OPENCODE_SEGMENT_GAP})?\s+to|to{_OPENCODE_SEGMENT_GAP})\s+)?"
    rf"{_OPENCODE_SEGMENT_GAP}interrupt",
    re.IGNORECASE,
)
_OPENCODE_BLOCK_WAVE_RE = re.compile(
    rf"[■⬝](?:{_OPENCODE_SEGMENT_GAP}[■⬝]){{3,}}"
)
# opencode 空闲页脚 = 灰字(128;128;128)画出的当前目录路径; 忙碌时该页脚
# 被块波动画占据(实测 25s 忙碌流里 0 次出现路径页脚, 完成后立即出现)。
# 工具输出里的路径是白字(238;238;238), 不会误触发 idle 释放。
_OPENCODE_IDLE_CWD_RE = re.compile(
    r"\x1b\[38;2;128;128;128m"
    r"(?:\x1b\[[0-9;?]*[a-zA-Z])*"
    r"(?P<path>(?:[A-Za-z]:[\\/][^\s\x1b]{2,}|~?(?:/[^\s\x1b]{2,}){1,}))"
)
# zcode (zcode-app-cli, pi-tui): 忙碌时状态行每秒重绘动画计时器
#   working… ── [ 🕛 0s ]   /   thinking… ── [ 🕒 3s ]
# 字符之间穿插 SGR 序列(与 opencode 同类), 用同样的段间隙正则;
# 空闲状态行是 ◈ zai/<model> ─ ◉ <mode> ─ ⚡ <effort> ─ ctx…, 实测只在
# 回合结束后的最后重绘出现, 空闲其余时间完全静默 → 显式 busy/idle 转换对。
_ZCODE_SEGMENT_GAP = r"(?:\x1b\[[0-9;?]*[a-zA-Z]|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\))*"
# A live ZCode turn has a non-empty activity followed by its own animated
# elapsed-time footer.  The activity is deliberately not enumerated: current
# builds emit ``thinking…``, ``preparing Bash…``, ``running Read…``,
# ``waiting for model…``, and retry/recovery states.  More importantly, elapsed
# time becomes ``43m 09s`` / ``1h 02m 03s`` after a minute, whereas the old
# ``\d+s`` matcher silently stopped recognizing a still-running long turn.
# The clock + elapsed-time structure is the authority here, not arbitrary
# screen text, so this stays safe from transcript/repaint false positives.
_ZCODE_ELAPSED_RE = (
    rf"(?:\d+{_ZCODE_SEGMENT_GAP}h{_ZCODE_SEGMENT_GAP}\s+)?"
    rf"(?:\d+{_ZCODE_SEGMENT_GAP}m{_ZCODE_SEGMENT_GAP}\s+)?"
    rf"\d+{_ZCODE_SEGMENT_GAP}s"
)
# Unlike ``_ZCODE_SEGMENT_GAP`` this atom is never empty.  It is safe inside a
# quantified activity span; using the optional gap there would create an
# exponential backtracking path on a long terminal replay.
_ZCODE_ACTIVITY_ATOM = (
    r"(?:[^\r\n\x1b]|\x1b\[[0-9;?]*[a-zA-Z]|"
    r"\x1b\][^\x07\x1b]*(?:\x07|\x1b\\))"
)
_ZCODE_BUSY_RE = re.compile(
    rf"{_ZCODE_ACTIVITY_ATOM}{{1,480}}?"
    rf"{_ZCODE_SEGMENT_GAP}──{_ZCODE_SEGMENT_GAP}\s*\["
    rf"{_ZCODE_SEGMENT_GAP}\s*[🕐-🕛]{_ZCODE_SEGMENT_GAP}\s*"
    rf"{_ZCODE_ELAPSED_RE}{_ZCODE_SEGMENT_GAP}\s*\]",
    re.IGNORECASE,
)
# ZCode keeps the ``◈ zai/<model>`` header visible and repaints it while
# working. Only the final ``Worked for <elapsed>`` divider ends a turn.
_ZCODE_WORKED_RE = re.compile(
    rf"worked{_ZCODE_SEGMENT_GAP}\s+for{_ZCODE_SEGMENT_GAP}\s+{_ZCODE_ELAPSED_RE}",
    re.IGNORECASE,
)
# DeepSeek Harness PTY adapter (config/agents/dsh/dsh-pty.py) pushes its own
# OSC-0 titles: ⣾ prefix = a headless dsh task is running, ✓ = pane idle.
_DSH_BUSY_FRAMES = "\u28fe"
_DSH_IDLE_FRAMES = "\u2713"

_SUPPORTED_PROVIDERS = PTY_AGENT_PROVIDER_IDS
# These providers publish an explicit terminal-title transition when a turn
# ends. Once their busy marker is observed, keep it latched until that idle
# marker arrives; a spinner pause can mean a long tool call, not completion.
# ``codex_the_company`` runs the same Codex CLI (process-local the_company provider
# overrides), so its title-spinner markers are identical.
# ``dsh`` (DeepSeek Harness PTY adapter, config/agents/dsh/dsh-pty.py) emits
# OSC-0 titles itself: braille-frame prefix while a headless task runs, ✓ when
# the pane is waiting for the next task line.
# ``opencode`` (1.18+): 忙碌时块波动画每帧重绘(锁存), 空闲页脚稳定画当前
# 目录路径(灰字) —— 实测捕捉: 空闲态完全静默、忙碌流 25s 内路径页脚 0 次,
# 完成后立即出现。两者构成可靠的显式 busy/idle 转换对。
# ``zcode`` (zcode-app-cli): 忙碌时动画计时器状态行每秒重绘(锁存),
# 空闲状态行 ◈ zai/<model> 只在回合结束重绘一次, 其余空闲时间静默 ——
# 与 opencode 同一类转换对。
# ``qoder`` (qodercli): 实机 PTY 帧为 ``✦ <topic> | Working``，结束帧为
# ``◇ <topic> | Ready``。这两个 OSC-0 标题是同一 TUI 写出的严格转换对；
# 旧的盲文标题仍只作有限心跳兼容，不能锁存。
# ``grok`` (Grok Build TUI): OSC-0 标题每帧重绘。忙碌以 braille spinner
# 开头 (``⠋ - Waiting for response… - {name} - grok``), 空闲去掉 spinner
# (``{name} - grok``)。2026-08-14 本机 PTY snapshot 实测: 工作流 250k
# 窗口 169 条忙碌标题、0 条 OSC 9;4; 另一条已结束回合的会话最后一条是
# 无 spinner 的 idle 标题。
_EXPLICIT_IDLE_PROVIDERS = frozenset(
    {
        "codex",
        "codex_the_company",
        "codex_glm",
        "codex_2",
        "claude_code",
        "claude_2",
        "codebuddy",
        "kimi",
        "kiro",
        "dsh",
        "opencode",
        "zcode",
        "qoder",
        "grok",
    }
)


#: 绝不能从 daemon 继承给子会话的"我是谁"变量。
#:
#: identity.resolve_active_trace_id 的优先级是
#: ``OMNI_CC_TRACE_ID`` > ``CODEX_THREAD_ID`` > ``OMNI_CC_PTY_ID``。ccdaemon 常常
#: 是从某条 CLI 会话里启动的(``omni cc daemon restart``), 于是它的进程环境带着那条
#: 会话的身份; 一路继承下去, **每个**被它拉起的子会话都会自称是启动它的那条会话
#: —— `omni who` 报错人、`omni handoff adopt` / `note` / `progress` 记错归属、
#: 子会话还会去改父会话的 plan 绑定。子会话的身份只能来自本进程自己的
#: ``OMNI_CC_PTY_ID``(创建时按 sid 显式注入), 所以这里一律先剥掉继承来的那份。
_INHERITED_IDENTITY_ENV = (
    "OMNI_CC_TRACE_ID",
    "CODEX_THREAD_ID",
    "OMNI_CC_PTY_ID",
    "OMNI_AGENT_PROVIDER",
    "OMNI_HOOK_PROVIDER",
)


def _dsh_machine_settings_template() -> Path | None:
    """Repo template for ~/.dsh/settings.yaml (DeepSeek Harness providers)."""
    try:
        from omnicompany.core.config import omni_workspace_root

        template = omni_workspace_root() / "config" / "agents" / "dsh" / "settings.yaml"
    except Exception:  # noqa: BLE001 - provisioning is best-effort
        return None
    return template if template.is_file() else None


def _provision_dsh_machine_settings() -> None:
    """Write ~/.dsh/settings.yaml from the repo template when missing.

    dsh keeps providers under its machine home (~/.dsh by default, DSH_HOME
    overridable). A fresh machine (or a wiped home) must still offer the
    opencode-go / the_company provider routes the CLI Hub entries reference, so
    the first dsh PTY launch self-heals the file. Existing user edits are
    never touched.
    """
    template = _dsh_machine_settings_template()
    if template is None:
        return
    try:
        import os as _os

        home = _os.environ.get("DSH_HOME", "").strip() or str(Path.home() / ".dsh")
        target = Path(home) / "settings.yaml"
        if target.is_file():
            return
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(template.read_bytes())
    except Exception as exc:  # noqa: BLE001 - pane still boots; dsh reports config errors itself
        import logging

        logging.getLogger(__name__).warning(
            "dsh: failed to provision ~/.dsh/settings.yaml: %s", exc
        )


def _opencode_go_api_key() -> str | None:
    """OpenCode Go key from the local opencode auth store (never logged)."""
    try:
        import json

        auth_path = Path.home() / ".local" / "share" / "opencode" / "auth.json"
        if not auth_path.is_file():
            return None
        entry = json.loads(auth_path.read_text(encoding="utf-8")).get("opencode-go")
        if not isinstance(entry, dict):
            return None
        key = entry.get("key")
        return str(key).strip() if isinstance(key, str) and key.strip() else None
    except Exception:  # noqa: BLE001 - absence just means the opencode-go route reports missing credential
        return None


def _dotenv_value(name: str) -> str | None:
    """Read one key from the omnicompany repo .env (never logged)."""
    try:
        from omnicompany.core.config import omni_workspace_root

        env_file = omni_workspace_root() / ".env"
        if not env_file.is_file():
            return None
        for line in env_file.read_text(encoding="utf-8", errors="replace").splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or "=" not in stripped:
                continue
            key_name, _, value = stripped.partition("=")
            if key_name.strip() == name:
                value = value.strip().strip("\"'")
                return value or None
        return None
    except Exception:  # noqa: BLE001
        return None


def _the_company_api_key_fallback() -> str | None:
    """THE_COMPANY_API_KEY from the omnicompany repo .env when not in the environment."""
    return _dotenv_value("THE_COMPANY_API_KEY")


def zai_api_key() -> str | None:
    """Resolve the Z.ai GLM Coding Plan key.

    Order: process env → ``omnicompany/.env``. The key originally lived in
    ``~/.zcode/cli/config.json``; it was migrated into ``.env`` on 2026-08-17
    when zcode was removed as a launchable provider, so nothing here depends on
    a third-party client's config any more. The value is never logged.
    """
    from_env = os.environ.get("ZAI_API_KEY", "").strip()
    if from_env:
        return from_env
    return _dotenv_value("ZAI_API_KEY")


def provider_terminal_environment(
    provider: str,
    environ: Mapping[str, str],
) -> dict[str, str]:
    """Return the child environment needed for provider-owned runtime signals.

    Kimi Code 0.29+ publishes its activity lifecycle through the ConEmu-style
    ``OSC 9;4`` protocol, but only when terminal capability detection succeeds.
    Dashboard's PTY adapter consumes that protocol directly, so advertise the
    narrow capability to Kimi without pretending to be Windows Terminal or a
    different terminal emulator.

    ``dsh`` (DeepSeek Harness) needs its machine config present and both
    backend credentials resolvable from env names referenced by
    ``~/.dsh/settings.yaml``: OPENCODE_API_KEY (local opencode auth store) and
    THE_COMPANY_API_KEY (omnicompany .env). Keys are injected only when the
    environment does not already carry them and are never logged.

    Session identity inherited from the daemon's own environment is stripped
    here (see ``_INHERITED_IDENTITY_ENV``); callers inject the per-session
    ``OMNI_CC_PTY_ID`` right after.
    """
    result = dict(environ)
    for key in _INHERITED_IDENTITY_ENV:
        result.pop(key, None)
    # 2026-08-20: 守护进程自己的环境里 TERM 可能缺失或是 "dumb"(被上游 harness 设成
    # 非交互值)。codex 见到 dumb 会打印 "Codex's interactive TUI may not work in this
    # terminal. Continue anyway? [y/N]" 并阻塞等输入 —— spawn 出来的会话因此在没人按
    # y 的情况下直接退出(实测连起三次、每次 20 秒内 PTY 消失)。我们这条链路的确是真
    # PTY(winpty + xterm 消费端), 所以显式声明一个真实终端类型, 而不是让每个 provider
    # 各自去处理这个交互提示。
    if str(result.get("TERM", "")).strip().lower() in {"", "dumb", "unknown"}:
        result["TERM"] = "xterm-256color"
    # 2026-09-03: Claude Code 2.1 默认用 alt-screen "fullscreen renderer"(实测
    # v2.1.258 一启动就是 \x1b[?1049h + ?1000/1002/1003/1006h): 对话历史全部留在
    # 备用屏里, 终端 scrollback 是空的, 滚动改由 CLI 自己解析鼠标上报实现。浏览器
    # 端因此只有滚轮能滚 —— 平板只有触摸没有滚轮, 于是彻底滚不动(codex/kimi/
    # codebuddy 都走正常缓冲区, 所以照旧能滚)。官方开关切回经典渲染器, 历史重新
    # 进正常缓冲区, xterm 自己的 scrollback 与触摸滚动即可工作。
    # 对所有 PTY 会话设置而不只是 claude 家族: shell 会话里手敲 `claude` 起的进程
    # 只能继承 spawn 时的环境, 那时 provider 还是 shell。显式给了值的环境不覆盖。
    result.setdefault("CLAUDE_CODE_DISABLE_ALTERNATE_SCREEN", "1")
    runtime_provider = registered_cli_provider(provider)
    if runtime_provider:
        result["OMNI_AGENT_PROVIDER"] = runtime_provider
    if provider == "kimi":
        result.setdefault("ConEmuANSI", "ON")
    # A second official account is only a different home directory (CODEX_HOME /
    # CLAUDE_CONFIG_DIR), and the whole credential lives there. Set it
    # unconditionally: an inherited value from the daemon's own environment
    # would otherwise point this session at the primary account.
    result.update(second_account_env(provider))
    if provider == "codex_glm" and not result.get("ZAI_API_KEY"):
        # codex reads the key from the env name pinned by the process-local
        # ``model_providers.zai.env_key`` override, so it has to be present in
        # the child env. Resolved late (env → .env → zcode config) and never logged.
        glm_key = zai_api_key()
        if glm_key:
            result["ZAI_API_KEY"] = glm_key
    if provider in ("dsh", "dsh_web"):
        _provision_dsh_machine_settings()
        if not result.get("OPENCODE_API_KEY"):
            opencode_key = _opencode_go_api_key()
            if opencode_key:
                result["OPENCODE_API_KEY"] = opencode_key
        if not result.get("THE_COMPANY_API_KEY"):
            the_company_key = _the_company_api_key_fallback()
            if the_company_key:
                result["THE_COMPANY_API_KEY"] = the_company_key
    return result


@dataclass(frozen=True)
class _RuntimeEvent:
    offset: int
    working: bool
    source: str
    #: Whether this marker may hold "working" open with no further evidence.
    #:
    #: Only markers the CLI *pushes* qualify — an OSC title is written
    #: deliberately on a state change and is re-emitted for as long as the
    #: spinner runs. Screen text does not qualify: a resize, a TUI redraw or a
    #: replay of the scrollback re-delivers whatever the transcript already
    #: contained, so latching on it means an "esc to interrupt" from a finished
    #: turn pins the session to "working" with nothing able to release it.
    #: Text markers therefore fall back to the busy heartbeat.
    #:
    #: opencode's block wave is the one deliberate exception: the TUI repaints
    #: it every frame while busy, and the idle transition (gray cwd footer)
    #: arrives exactly once after the wave stops — the pair behaves like a
    #: pushed busy/idle protocol (verified against a real captured stream:
    #: 25s busy run with 0 cwd-footer hits, idle footer immediately after).
    #:
    #: Being weaker also means a text marker may not *replace* a pushed one
    #: (see ``_holds_pushed_busy``). Claude Code writes its OSC title only when
    #: the title string changes — measured silent for 41 s on a running turn —
    #: so one repainted footer used to demote the latch and report "idle" three
    #: seconds later, for as long as the next title write took to arrive.
    latching: bool = True


def _new_matches(pattern: re.Pattern[str], value: str, boundary: int) -> Iterable[re.Match[str]]:
    """Yield matches completed by the new chunk, including split markers."""
    return (match for match in pattern.finditer(value) if match.end() > boundary)


@dataclass
class ProviderRuntimeState:
    """Incrementally classify one CLI from its explicit terminal markers."""

    provider: str = "shell"
    phase: str = "idle"
    marker_at: float = 0.0
    source: str | None = None
    armed: bool = False
    #: Set when the marker that produced the current phase was CLI-pushed and
    #: may therefore hold "working" open without further evidence.
    latched: bool = False
    #: When any terminal output was last fed, marker or not. A live turn keeps
    #: repainting, so this is the liveness floor that lets a latched busy
    #: marker decay after total output silence (see PROVIDER_LATCHED_STALE_S).
    fed_at: float = 0.0
    _tail: str = ""

    def set_provider(self, provider: str) -> None:
        provider = provider or "shell"
        if provider == self.provider:
            return
        self.provider = provider
        self.phase = "idle"
        self.marker_at = 0.0
        self.source = None
        self.armed = False
        self.latched = False
        self.fed_at = 0.0
        self._tail = ""

    def mark_idle(self, source: str, *, now: float | None = None) -> None:
        self.phase = "idle"
        self.marker_at = time.time() if now is None else now
        self.source = source
        self.armed = False
        self.latched = False

    def feed(self, chunk: str, *, now: float | None = None) -> bool:
        """Consume live output and return whether an explicit state changed."""
        if not chunk:
            return False
        observed_at = time.time() if now is None else now
        self.fed_at = observed_at
        tail = self._tail
        combined = tail + chunk
        boundary = len(tail)
        self._tail = combined[-_TAIL_CHARS:]

        if self.provider not in _SUPPORTED_PROVIDERS:
            return False

        events = self._events(combined, boundary)
        if events and self._holds_pushed_busy():
            # Only the provider's own idle transition may release a pushed busy
            # marker. A weaker screen-text marker must not replace it: dropping
            # it here keeps ``latched``/``source``/``marker_at`` authoritative,
            # instead of silently demoting a live turn to the 3s heartbeat.
            events = [event for event in events if event.latching or not event.working]
        if not events:
            return False
        changed = False
        for event in sorted(events, key=lambda item: item.offset):
            next_phase = "working" if event.working else "idle"
            changed = changed or next_phase != self.phase or event.source != self.source
            self.phase = next_phase
            self.marker_at = observed_at
            self.source = event.source
            self.latched = event.working and event.latching
        return changed

    def _holds_pushed_busy(self) -> bool:
        """Whether a CLI-pushed busy marker currently owns the state.

        Only meaningful where the provider also publishes an explicit idle
        transition — that is what makes it safe to ignore everything else until
        the turn actually ends. Providers without one (Qoder today) still need
        every marker, screen text included, to refresh their heartbeat.
        """
        return (
            self.latched
            and self.phase == "working"
            and self.provider in _EXPLICIT_IDLE_PROVIDERS
        )

    def working(self, *, now: float | None = None) -> bool:
        if self.phase != "working" or not self.marker_at:
            return False
        # Latching is a property of the marker, not of the provider. A CLI that
        # publishes an idle title still emits plain "esc to interrupt" text that
        # a redraw can resurrect long after the turn ended, and holding that
        # open forever is how a finished session reports itself as running.
        current = time.time() if now is None else now
        if self.latched and self.provider in _EXPLICIT_IDLE_PROVIDERS:
            live_at = max(self.marker_at, self.fed_at)
            return max(0.0, current - live_at) <= PROVIDER_LATCHED_STALE_S
        heartbeat = (
            KIRO_BUSY_HEARTBEAT_S
            if self.provider == "kiro"
            and self.source in {"kiro:thinking-line", "kiro:tool-spinner"}
            else PROVIDER_BUSY_HEARTBEAT_S
        )
        return max(0.0, current - self.marker_at) <= heartbeat

    def status(self, *, now: float | None = None) -> str:
        return "working" if self.working(now=now) else "idle"

    def known(self) -> bool:
        """Whether this adapter has observed an authoritative runtime marker."""
        return self.provider not in _SUPPORTED_PROVIDERS or bool(self.marker_at)

    def export(self) -> dict[str, Any]:
        return {
            "provider_runtime_phase": self.phase,
            "provider_runtime_marker_at": self.marker_at or None,
            "provider_runtime_source": self.source,
            "provider_runtime_armed": self.armed,
            "provider_runtime_latched": self.latched,
            "provider_runtime_fed_at": self.fed_at or None,
        }

    def restore(self, meta: dict[str, Any]) -> bool:
        """Merge a newer marker persisted by the detached PTY host."""
        try:
            marker_at = float(meta.get("provider_runtime_marker_at") or 0.0)
        except (TypeError, ValueError):
            return False
        if not marker_at or marker_at < self.marker_at:
            return False
        phase = str(meta.get("provider_runtime_phase") or "")
        if phase not in {"working", "idle"}:
            return False
        self.phase = phase
        self.marker_at = marker_at
        source = meta.get("provider_runtime_source")
        self.source = str(source) if source else None
        self.armed = bool(meta.get("provider_runtime_armed"))
        # Absent on state written before latching became a marker property.
        # Defaulting to False makes a stale record decay to idle rather than
        # resurrect as permanent work.
        self.latched = bool(meta.get("provider_runtime_latched"))
        try:
            fed_at = float(meta.get("provider_runtime_fed_at") or 0.0)
        except (TypeError, ValueError):
            fed_at = 0.0
        # State written before fed_at existed: the marker itself is the best
        # available liveness floor, so an old frozen latch decays on restore.
        self.fed_at = max(fed_at, marker_at)
        return True

    def restore_from_replay(
        self,
        chunks: Iterable[str],
        *,
        observed_at: float,
    ) -> bool:
        """Recover old hosted explicit-idle provider state from terminal replay.

        Hosts launched before runtime markers were persisted expose only the
        bounded ANSI replay during a daemon reconnect. Providers in
        ``_EXPLICIT_IDLE_PROVIDERS`` emit durable busy and idle transitions, so
        the last marker in replay order is authoritative. Providers without a
        durable idle marker do not use this fallback: an old spinner in a
        transcript must never be promoted into current work.
        """
        if self.provider not in _EXPLICIT_IDLE_PROVIDERS:
            return False
        replay = "".join(chunks)
        if not replay:
            return False
        recovered = ProviderRuntimeState(self.provider)
        recovered.feed(replay, now=observed_at)
        # A provider may retain finite compatibility markers beside its durable
        # busy/idle protocol (Qoder's legacy braille title is one example).
        # A bounded reconnect replay must never promote one of those historical
        # heartbeats into current work; recover only a durable idle transition
        # or a latching provider-pushed busy transition.
        if not recovered.marker_at or (
            recovered.phase == "working" and not recovered.latched
        ):
            return False
        self.phase = recovered.phase
        self.marker_at = recovered.marker_at
        self.source = recovered.source
        self.armed = recovered.armed
        self.latched = recovered.latched
        self.fed_at = recovered.fed_at
        self._tail = recovered._tail
        return True

    def _events(self, value: str, boundary: int) -> list[_RuntimeEvent]:
        if self.provider in ("codex", "codex_the_company", "codex_glm", "codex_2"):
            return self._codex_events(value, boundary)
        if self.provider in ("claude_code", "claude_2"):
            return self._claude_events(value, boundary)
        if self.provider == "codebuddy":
            return self._codebuddy_events(value, boundary)
        if self.provider == "kimi":
            return self._kimi_events(value, boundary)
        if self.provider == "kiro":
            return self._kiro_events(value, boundary)
        if self.provider == "qoder":
            return self._qoder_events(value, boundary)
        if self.provider == "opencode":
            return self._opencode_events(value, boundary)
        if self.provider == "dsh":
            return self._dsh_events(value, boundary)
        if self.provider == "zcode":
            return self._zcode_events(value, boundary)
        if self.provider == "grok":
            return self._grok_events(value, boundary)
        return []

    @staticmethod
    def _title_matches(value: str, boundary: int) -> Iterable[re.Match[str]]:
        return _new_matches(_OSC_TITLE_RE, value, boundary)

    def _codex_events(self, value: str, boundary: int) -> list[_RuntimeEvent]:
        events = [
            _RuntimeEvent(match.start(), True, "codex:working-line", latching=False)
            for match in _new_matches(_CODEX_WORKING_RE, value, boundary)
        ]
        for match in self._title_matches(value, boundary):
            title = match.group("title").lstrip()
            events.append(
                _RuntimeEvent(
                    match.start(),
                    bool(title and title[0] in _BRAILLE_FRAMES),
                    "codex:title-spinner" if title and title[0] in _BRAILLE_FRAMES else "codex:title-idle",
                )
            )
        return events

    def _claude_events(self, value: str, boundary: int) -> list[_RuntimeEvent]:
        events = [
            _RuntimeEvent(match.start(), True, "claude:interrupt-line", latching=False)
            for match in _new_matches(_INTERRUPT_RE, value, boundary)
        ]
        for match in self._title_matches(value, boundary):
            title = match.group("title").lstrip()
            if title and title[0] in _CLAUDE_IDLE_FRAMES:
                events.append(_RuntimeEvent(match.start(), False, "claude:title-idle"))
            elif title and title[0] in _CLAUDE_FRAMES:
                events.append(_RuntimeEvent(match.start(), True, "claude:title-spinner"))
        return events

    def _codebuddy_events(self, value: str, boundary: int) -> list[_RuntimeEvent]:
        events = [
            _RuntimeEvent(match.start(), True, "codebuddy:interrupt-line", latching=False)
            for match in _new_matches(_INTERRUPT_RE, value, boundary)
        ]
        for match in self._title_matches(value, boundary):
            title = match.group("title").lstrip()
            if title.startswith("✳"):
                events.append(_RuntimeEvent(match.start(), False, "codebuddy:title-idle"))
            elif title and title[0] in _BRAILLE_FRAMES:
                events.append(_RuntimeEvent(match.start(), True, "codebuddy:title-spinner"))
        return events

    def _kimi_events(self, value: str, boundary: int) -> list[_RuntimeEvent]:
        progress_events: list[_RuntimeEvent] = []
        for match in _new_matches(_OSC_PROGRESS_RE, value, boundary):
            state = match.group("state")
            if state == "3":
                progress_events.append(
                    _RuntimeEvent(match.start(), True, "kimi:progress-active")
                )
            elif state == "0":
                progress_events.append(
                    _RuntimeEvent(match.start(), False, "kimi:progress-idle")
                )
        if progress_events:
            # The CLI publishes these transitions from its actual activity
            # state. Ignore screen-level spinners in the same terminal chunk so
            # a final redraw cannot overwrite an authoritative idle transition.
            return progress_events

        if self.latched and self.source == "kimi:progress-active":
            # OSC 9;4 is Kimi's provider-owned lifecycle. Once it says active,
            # later TUI redraws must not replace that authoritative source or
            # turn the latch back into a heartbeat. Only the matching official
            # idle transition may release it.
            return []

        events = [
            _RuntimeEvent(match.start(), True, "kimi:thinking-spinner", latching=False)
            for match in _new_matches(_KIMI_TEXT_RE, value, boundary)
        ]
        events.extend(
            _RuntimeEvent(match.start(), True, "kimi:tool-spinner", latching=False)
            for match in _new_matches(_KIMI_MOON_RE, value, boundary)
        )
        return events

    @staticmethod
    def _kiro_events(value: str, boundary: int) -> list[_RuntimeEvent]:
        events = [
            _RuntimeEvent(match.start(), True, "kiro:thinking-line", latching=False)
            for match in _new_matches(_KIRO_BUSY_RE, value, boundary)
        ]
        events.extend(
            _RuntimeEvent(match.start(), True, "kiro:tool-spinner", latching=False)
            for match in _new_matches(_KIRO_TOOL_SPINNER_RE, value, boundary)
        )
        events.extend(
            _RuntimeEvent(match.start(), False, "kiro:prompt-idle")
            for match in _new_matches(_KIRO_IDLE_RE, value, boundary)
        )
        return events

    def _qoder_events(self, value: str, boundary: int) -> list[_RuntimeEvent]:
        """Qoder's observed OSC title busy/idle transition, plus old fallbacks.

        qodercli's current TUI writes ``✦ <topic> | Working`` while a turn is
        live and ``◇ <topic> | Ready`` once it returns to its prompt.  These are
        provider-pushed state transitions, hence safe to latch.  Older braille
        titles and screen text have no demonstrated idle counterpart, so retain
        them only as finite heartbeat markers for backwards compatibility.
        """
        events = [
            _RuntimeEvent(match.start(), True, "qoder:interrupt-line", latching=False)
            for match in _new_matches(_INTERRUPT_RE, value, boundary)
        ]
        events.extend(
            _RuntimeEvent(match.start(), True, "qoder:thinking-line", latching=False)
            for match in _new_matches(_QODER_BUSY_RE, value, boundary)
        )
        for match in self._title_matches(value, boundary):
            title = match.group("title").lstrip()
            if title.startswith("✦") and re.search(r"\|\s*working\s*$", title, re.IGNORECASE):
                events.append(
                    _RuntimeEvent(match.start(), True, "qoder:title-working", latching=True)
                )
            elif title.startswith("◇") and re.search(r"\|\s*ready\s*$", title, re.IGNORECASE):
                events.append(_RuntimeEvent(match.start(), False, "qoder:title-ready"))
            elif title and title[0] in _BRAILLE_FRAMES:
                events.append(
                    _RuntimeEvent(match.start(), True, "qoder:title-spinner", latching=False)
                )
        return events

    def _opencode_events(self, value: str, boundary: int) -> list[_RuntimeEvent]:
        events = [
            _RuntimeEvent(match.start(), True, "opencode:interrupt-line", latching=False)
            for match in _new_matches(_OPENCODE_INTERRUPT_RE, value, boundary)
        ]
        events.extend(
            _RuntimeEvent(match.start(), True, "opencode:static-spinner", latching=False)
            for match in _new_matches(_OPENCODE_STATIC_RE, value, boundary)
        )
        # 块波动画是 opencode 忙碌的权威信号: 每帧重绘、锁存到空闲页脚出现。
        # 锁存解决"长工具调用里动画暂停 >3s 被心跳误判 idle"(用户: 离开页面
        # 回来看到不在运行, 其实还在跑)。
        events.extend(
            _RuntimeEvent(match.start(), True, "opencode:block-spinner", latching=True)
            for match in _new_matches(_OPENCODE_BLOCK_WAVE_RE, value, boundary)
        )
        # 空闲页脚: 灰字画出的 cwd 路径。忙碌流里不出现(实测), 是唯一的 idle 释放。
        events.extend(
            _RuntimeEvent(match.start(), False, "opencode:cwd-idle")
            for match in _new_matches(_OPENCODE_IDLE_CWD_RE, value, boundary)
        )
        return events

    def _dsh_events(self, value: str, boundary: int) -> list[_RuntimeEvent]:
        """DeepSeek Harness PTY adapter markers.

        The adapter (config/agents/dsh/dsh-pty.py) pushes an OSC-0 title
        before and after every headless dsh task: ⣾ prefix = working,
        ✓ = idle. Both are provider-pushed state transitions, so the busy
        marker latches (``dsh`` is in ``_EXPLICIT_IDLE_PROVIDERS``) and only
        the adapter's own idle title releases it — a replayed working title
        cannot pin a finished pane as running, and a redrawn answer cannot
        demote a live task.
        """
        events: list[_RuntimeEvent] = []
        for match in self._title_matches(value, boundary):
            title = match.group("title").lstrip()
            if not title:
                continue
            if title[0] in _DSH_BUSY_FRAMES:
                events.append(
                    _RuntimeEvent(match.start(), True, "dsh:title-working", latching=True)
                )
            elif title[0] in _DSH_IDLE_FRAMES:
                events.append(
                    _RuntimeEvent(match.start(), False, "dsh:title-idle")
                )
        return events

    def _zcode_events(self, value: str, boundary: int) -> list[_RuntimeEvent]:
        """zcode (zcode-app-cli) markers — 与 opencode 同族。

        忙碌: 动画计时器状态行 (``working… ── [ 🕛 0s ]`` /
        ``thinking… ── [ 🕒 3s ]``) 每秒重绘, 锁存;
        空闲: ``◈ zai/<model> ─ …`` 状态行只在回合结束后的最后重绘出现,
        其余空闲时间完全静默(实测捕捉)。二者构成显式 busy/idle 转换对。
        """
        events: list[_RuntimeEvent] = [
            _RuntimeEvent(match.start(), True, "zcode:timer-line", latching=True)
            for match in _new_matches(_ZCODE_BUSY_RE, value, boundary)
        ]
        events.extend(
            _RuntimeEvent(match.start(), False, "zcode:worked-idle")
            for match in _new_matches(_ZCODE_WORKED_RE, value, boundary)
        )
        return events

    def _grok_events(self, value: str, boundary: int) -> list[_RuntimeEvent]:
        """Grok Build OSC-0 titles captured from a live dashboard PTY.

        Default ``[ui.notifications.title].items`` is spinner + activity +
        session-name + grok. A working turn pushes a braille-frame prefix on
        every title write; the idle title is the same string with that
        prefix removed. Both are CLI-pushed, so the busy side latches and
        only the idle title releases it.

        OSC 9;4 is documented (``progress_bar = true``) but was not emitted
        in ConPTY (0 hits beside 169 OSC-0 titles). Do not depend on it.
        """
        events: list[_RuntimeEvent] = []
        for match in self._title_matches(value, boundary):
            title = match.group("title").lstrip()
            if not title:
                continue
            if title[0] in _BRAILLE_FRAMES:
                events.append(
                    _RuntimeEvent(
                        match.start(), True, "grok:title-spinner", latching=True
                    )
                )
            else:
                events.append(_RuntimeEvent(match.start(), False, "grok:title-idle"))
        return events
