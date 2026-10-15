# [OMNI] origin=ai-ide ts=2026-06-06 type=cli
"""已封存的 provider 历史检索/迁移兼容命令。

用户 2026-06-06: 总控能用 omni cli 列出/搜索/排序现有 codex & claude code 对话历史, 采纳(resume)成 subagent;
也能按 plan 查关联会话并 resume。这是"给 AI 用的 resume 模式"。

- list   : 列本机 provider-native 历史；--plan 查询关联该 plan 的 CLI Agent Worker。
- search : 在历史对话里搜(匹配预览/目录/会话id)。
- adopt  : resume 某条历史记录，采纳成真实 CLI Agent Worker。
- resume : resume 某条对话到真实 pty CLI 页签(走 ccdaemon /cc/sessions/resume-provider), 人直接接着敲, 不是 subagent。
"""
from __future__ import annotations

import json
import os
import re
import urllib.request
from datetime import datetime
from datetime import time as dtime

import click

from .._access import any_caller


#: 有本机历史存储、能被扫出来的 provider(与 import_routes.scan_all_now 同源)。
_SCANNED_PROVIDERS = ["claude_code", "codex", "kiro"]


def _scan() -> list[dict]:
    # 走 scan_all_now 而不是自己拼 claude+codex: 之前漏了 kiro, 历史里根本看不到 kiro 会话。
    from omnicompany.dashboard.ccdaemon.api import scan_all_now
    return list(scan_all_now())


_TS_RE = re.compile(rb'"timestamp":"(20\d\d-\d\d-\d\dT[0-9:.\-+Z]+)"')
#: 尾部读多少字节找最后一条真实时间戳(够覆盖若干条消息, 又不至于把 100MB transcript 读穿)。
_TAIL_BYTES = 262144


def _last_activity(item: dict) -> tuple[float, str]:
    """会话最后一次"真的说话"的时间, 返回 (epoch, 来源)。

    文件 mtime 不可信: claude code / dashboard 会重写 transcript 里的 last-prompt /
    ai-title / permission-mode 这类元数据行, 于是早已死掉的祖先分叉看起来"今天还活着"
    ——按 mtime 挑会话去 resume, 会把用户扔回几天前的旧版本, 再敲字就又分叉一次、
    把后续几千行甩掉。所以这里读 transcript 尾部, 取最后一条真实消息时间戳。

    kiro 的 transcript 不带时间戳字段, 只能退回文件 mtime(kiro 不重写已落盘的行,
    它的 mtime 就是真实追加时间; 注意别用 scan_kiro 那个跟 .json 元数据取 max 的值)。
    """
    path = item.get("file")
    if path:
        try:
            with open(path, "rb") as fh:
                fh.seek(0, os.SEEK_END)
                fh.seek(max(0, fh.tell() - _TAIL_BYTES))
                found = _TS_RE.findall(fh.read())
            if found:
                raw = found[-1].decode("ascii", "replace").replace("Z", "+00:00")
                return datetime.fromisoformat(raw).timestamp(), "content"
        except (OSError, ValueError):
            pass
        try:
            return os.path.getmtime(path), "file"
        except OSError:
            pass
    return float(item.get("mtime") or 0.0), "scan"


def _parse_since(since: str) -> float:
    """--since 取值: today | YYYY-MM-DD。返回当地时区当天 00:00 的 epoch。"""
    s = since.strip().lower()
    day = datetime.now().date() if s == "today" else datetime.strptime(s, "%Y-%m-%d").date()
    return datetime.combine(day, dtime.min).timestamp()


def _daemon_base() -> str:
    from omnicompany.dashboard.ccdaemon.api import lifecycle
    s = lifecycle.read_status()
    if not (getattr(s, "alive", False) and getattr(s, "port", None)):
        raise click.ClickException("ccdaemon 未运行(先 `omni cc daemon start`)")
    return f"http://127.0.0.1:{s.port}"


def _http(method: str, url: str, body: dict | None = None, timeout: int = 60) -> dict:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310 - 本机 daemon
        return json.loads(r.read().decode())


def _cc_sessions_by_plan(plan: str) -> list[dict]:
    base = _daemon_base()
    d = _http("GET", f"{base}/cc/sessions?include_recoverable=true&active_plan={plan}")
    items = d.get("items") or d.get("sessions") or []
    return [
        {"provider": s.get("provider"), "session_id": s.get("claude_session_id") or s.get("id"),
         "cwd": s.get("cwd"), "mtime": s.get("started_at"), "preview": s.get("name") or s.get("last_message") or "",
         "active_plan": s.get("active_plan"), "id": s.get("id"), "alive": s.get("alive")}
        for s in items if s.get("active_plan") == plan
    ]


def _print(items: list[dict], as_json: bool) -> None:
    if as_json:
        click.echo(json.dumps(items, ensure_ascii=False, indent=2))
        return
    if not items:
        click.echo("(无)")
        return
    prov_label = {"codex": "Codex", "kiro": "Kiro"}
    for s in items:
        prov = prov_label.get(s.get("provider") or "", "Claude")
        sid = (s.get("session_id") or "")[:14]
        cwd = s.get("cwd") or "?"
        prev = (s.get("preview") or "").replace("\n", " ")[:60]
        act = ""
        if s.get("last_activity"):
            act = "  " + datetime.fromtimestamp(s["last_activity"]).strftime("%m-%d %H:%M")
        click.echo(f"  [{prov:6}] {sid}{act}  {cwd}")
        if prev:
            click.echo(f"           {prev}")


@click.group("convos")
def cmd_convos() -> None:
    """列/搜/采纳(resume)已有 claude/codex 对话历史(给总控 AI 用)。"""


@cmd_convos.command("list")
@click.option("--provider", type=click.Choice(_SCANNED_PROVIDERS), default=None)
@click.option("--cwd", default=None, help="按 cwd 子串过滤")
@click.option("--plan", default=None, help="改为查 ccdaemon 里关联该 plan 的 CLI Agent Worker")
@click.option("--since", default=None,
              help="只留这天之后真的说过话的会话(today | YYYY-MM-DD); 按内容时间戳判, 不认 mtime")
@click.option("--limit", default=30, help="最多列几条")
@click.option("--json", "as_json", is_flag=True)
@any_caller
def cmd_convos_list(
    provider: str | None, cwd: str | None, plan: str | None, since: str | None, limit: int, as_json: bool
) -> None:
    """列出现有对话(默认本机 ~/.claude/~/.codex/~/.kiro, 按更新时间倒序)。"""
    if plan:
        _print(_cc_sessions_by_plan(plan)[:limit], as_json)
        return
    items = _scan()
    if provider:
        items = [s for s in items if s.get("provider") == provider]
    if cwd:
        items = [s for s in items if cwd.lower() in (s.get("cwd") or "").lower()]
    if since:
        floor = _parse_since(since)
        # mtime 是真实活跃时间的上界(元数据重写只会让它变新), 先拿它粗筛掉绝大多数,
        # 再对少数幸存者读尾部核实 —— 免得为一次筛选去 tail 几千个 transcript。
        kept = []
        for s in items:
            if (s.get("mtime") or 0) < floor:
                continue
            ts, src = _last_activity(s)
            if ts >= floor:
                kept.append({**s, "last_activity": ts, "activity_source": src})
        items = kept
    items.sort(key=lambda s: s.get("last_activity") or s.get("mtime", 0) or 0, reverse=True)
    _print(items[:limit], as_json)


@cmd_convos.command("search")
@click.argument("query")
@click.option("--provider", type=click.Choice(_SCANNED_PROVIDERS), default=None)
@click.option("--limit", default=30)
@click.option("--json", "as_json", is_flag=True)
@any_caller
def cmd_convos_search(query: str, provider: str | None, limit: int, as_json: bool) -> None:
    """在历史对话里搜(匹配预览/目录/会话id, 不区分大小写)。"""
    q = query.lower()
    items = _scan()
    if provider:
        items = [s for s in items if s.get("provider") == provider]
    items = [s for s in items if q in f"{s.get('preview', '')} {s.get('cwd', '')} {s.get('session_id', '')}".lower()]
    items.sort(key=lambda s: s.get("mtime", 0) or 0, reverse=True)
    _print(items[:limit], as_json)


@cmd_convos.command("adopt")
@click.argument("provider", type=click.Choice(["claude_code", "codex"]))
@click.argument("session_id")
@click.option("--plan", default=None, help="关联 plan id")
@click.option("--cwd", default=None, help="工作目录(默认沿用原会话)")
@click.option("--effort", type=click.Choice(["low", "medium", "high", "xhigh", "max"]), default=None,
              help="N2b 推理强度档(仅 claude_code 生效), 省略=用模型默认")
@any_caller
def cmd_convos_adopt(provider: str, session_id: str, plan: str | None, cwd: str | None, effort: str | None) -> None:
    """兼容入口：把已有 provider 历史恢复成真实 CLI Agent Worker。"""
    base = _daemon_base()
    body: dict = {
        "resume_provider_session_id": session_id,
        "provider": provider,
        "from_controller": True,
        "visibility": "hidden",
    }
    if cwd:
        body["cwd"] = cwd
    if plan:
        body["active_plan"] = plan
    if effort:
        body["topic"] = f"legacy adopt; requested effort={effort}"
    try:
        data = _http("POST", f"{base}/cc/agent-workers", body, timeout=120)
    except Exception as e:  # noqa: BLE001
        raise click.ClickException(f"采纳失败: {e}") from e
    click.echo(json.dumps(
        {"ok": True, "worker_id": data.get("worker_id") or data.get("id"),
         "runtime_kind": "cli", "provider": data.get("provider"), "active_plan": plan},
        ensure_ascii=False, indent=2,
    ))


#: ccdaemon /cc/sessions/resume-provider 支持 CLI 续接的 provider 白名单(与 pty._resume_command 同源)。
_PTY_RESUMABLE = ["claude_code", "codex", "kimi", "opencode", "codebuddy", "kiro", "qoder"]


def _origin_cwd(session_id: str) -> str | None:
    """回原会话的 cwd。claude/codex 的历史按目录分桶存, resume 必须回到原目录才找得到。"""
    for s in _scan():
        if s.get("session_id") == session_id:
            return s.get("cwd") or None
    return None


@cmd_convos.command("resume")
@click.argument("provider", type=click.Choice(_PTY_RESUMABLE))
@click.argument("session_id")
@click.option("--cwd", default=None, help="工作目录(默认沿用原会话所在目录)")
@click.option("--cols", default=None, type=int, help="终端列数(默认走 daemon 默认值)")
@click.option("--rows", default=None, type=int, help="终端行数(默认走 daemon 默认值)")
@click.option("--json", "as_json", is_flag=True)
@any_caller
def cmd_convos_resume(
    provider: str, session_id: str, cwd: str | None, cols: int | None, rows: int | None, as_json: bool
) -> None:
    """resume 某条已有对话到真实 pty CLI 页签(人直接接着敲, 不是 subagent)。"""
    base = _daemon_base()
    body: dict = {"provider": provider, "provider_session_id": session_id}
    target_cwd = cwd or _origin_cwd(session_id)
    if target_cwd:
        body["cwd"] = target_cwd
    if cols:
        body["cols"] = cols
    if rows:
        body["rows"] = rows
    try:
        data = _http("POST", f"{base}/cc/sessions/resume-provider", body, timeout=120)
    except Exception as e:  # noqa: BLE001
        raise click.ClickException(f"resume 失败: {e}") from e
    out = {
        "ok": True,
        "pty_id": data.get("id"),
        "provider": provider,
        "provider_session_id": session_id,
        "cwd": data.get("cwd"),
        # daemon 发现同一条对话已有活着的 pty 时直接复用, 不重复开页签。
        "reused_existing": bool(data.get("resumed_existing")),
    }
    if as_json:
        click.echo(json.dumps(out, ensure_ascii=False, indent=2))
        return
    tag = "复用已有页签" if out["reused_existing"] else "新开页签"
    click.echo(f"  [{provider}] {session_id[:14]} → pty {out['pty_id']}  ({tag})  {out['cwd']}")


__all__ = ["cmd_convos"]
