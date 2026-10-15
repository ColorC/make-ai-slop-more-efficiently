# [OMNI] origin=codex domain=cli/commands ts=2026-09-01 type=cli status=active
# [OMNI] summary="omni tbus — 个人任务总线桥: 原生树形列表(list), runtime 可达性+生命周期聚合(status), CLI 信封透传(call), 任意产物提单入口(intake)。"
# [OMNI] why="用户 2026-09-01: 唯一短长期 todo 总线, 全命令行不上 MCP; 消费方只准依赖 LiTe CLI 信封, 不读宿主内部。"
# [OMNI] material_id="material:cli.commands.tbus"
"""omni tbus — 个人任务总线桥（task-core）。

总线本体是 LiTe local-tasks 的 CLI 信封（task-core.md §7）。本桥只做三件事：
list（原生树形列表视图）、status（可达性 + 生命周期聚合）、call（信封透传）
与 intake（提单入口：任意产物把问题存进收件箱，事后一起解决）。
不读宿主 SQLite、不直连宿主 RPC —— 宿主可换，信封不变则桥不重写。
"""
from __future__ import annotations

import json
import os
import subprocess
import threading
import time
from typing import Any

import click

DEFAULT_LITEWORK_DIR = (
    r"C:/workspace/LiWork\workspaces"
    r"\liwork-litework-web-runtime-hardening\litework-main-6769"
)

# task-core.md §2: 每个 stateKey 映射到核心生命周期相位（未知键按 open 计）。
LIFECYCLE_PHASES = ("open", "paused", "done", "dropped")
STATE_TO_PHASE = {
    "todo": "open",
    "in-progress": "open",
    "blocked": "open",
    "in-review": "open",
    "paused": "paused",
    "completed": "done",
    "cancelled": "dropped",
}

# ─── 任务列表短缓存 ────────────────────────────────────────────────────
# 每次信封调用都是一次 node 冷启(~340ms)。开一次面板, board 与收件箱会各查一遍
# 同一份列表 —— 那就是白白多花一次冷启。缓存放在桥里而不是 dashboard 里, 是因为
# 两边都合法依赖桥, 反过来不成立(arch_gate R1/R2)。
#
# TTL 取 1.5s: 短到看得见别处的写入, 又能吃掉同一次开页里的重复查。写操作必须
# 显式 invalidate —— 缓存宁可短命, 也不能让人看到自己刚改的东西没变。
_LIST_TTL_S = 1.5
_list_cache: dict[str, tuple[float, list[dict[str, Any]], dict[str, Any]]] = {}
_list_lock = threading.Lock()


def cached_task_list(
    directory: str, archived: bool = False,
) -> tuple[list[dict[str, Any]], dict[str, Any], str | None, str | None]:
    """→ (tasks, meta, code, message)。meta 带 runtimeId —— 面板靠它判在线, 加缓存
    不能把它弄丢。"""
    key = "archived" if archived else "live"
    now = time.monotonic()
    with _list_lock:
        hit = _list_cache.get(key)
        if hit and now - hit[0] < _LIST_TTL_S:
            return hit[1], hit[2], None, None
    args = ["task", "list", "--json"] + (["--include-archived"] if archived else [])
    code, payload, stderr = _run_cli(directory, args)
    failure = _envelope_error(payload)
    if failure or code != 0 or not payload:
        message = failure[1] if failure else ((stderr or "").strip() or f"exit={code}")
        return [], {}, (failure[0] if failure else "runtime_error"), message
    result = payload.get("result") if isinstance(payload.get("result"), dict) else {}
    tasks = [dict(t) for t in ((result or {}).get("tasks") or []) if isinstance(t, dict)]
    meta = payload.get("_meta") if isinstance(payload.get("_meta"), dict) else {}
    with _list_lock:
        _list_cache[key] = (now, tasks, meta)
    return tasks, meta, None, None


def invalidate_task_list() -> None:
    with _list_lock:
        _list_cache.clear()


# ─── 提单（intake）信封 ────────────────────────────────────────────────
# 「暴露一个入口, 各个产物自行处理」: 总线只定信封, 每个产物自己决定怎么触发
# (快捷键 / 右键 / 摇一摇 / 崩溃钩子都行)。落点固定 —— 收件箱、不定级、不指派、
# 不归项目, 因为提单的价值就是"存着一起解决", 当场分类等于当场打断。
#
# 这里是唯一实现: HTTP 面(/api/tbus/intake)与 CLI 面(omni tbus intake)都调它,
# 免得两处各写一份落点口径。
INTAKE_STATE = "inbox"


def intake_create_args(
    title: str,
    note: str | None = None,
    source: dict[str, Any] | None = None,
) -> list[str]:
    """提单 → `task create` 的信封参数。

    `source.extra` 是产物自己认识的上下文（场景 id / 选中对象 / 构建号…），
    宿主不解释也没有对应列，所以原样附在描述末尾：分诊时人要读的就是它，
    塞进结构化字段反而要先定 schema —— 那是 task-core §1.1 明确拒收的袋子。
    """
    body = (note or "").strip()
    extra = (source or {}).get("extra")
    if extra:
        dumped = json.dumps(extra, ensure_ascii=False, indent=2, sort_keys=True, default=str)
        body = (body + "\n\n" if body else "") + "来源上下文:\n" + dumped
    args = ["task", "create", "--title", title.strip(), "--state", INTAKE_STATE]
    if body:
        args += ["--description", body]
    return args + ["--json"]


def intake_binding_args(task_id: str, source: dict[str, Any] | None) -> list[str] | None:
    """提单来源 → 外部绑定（task-core §5）。没说来自哪个产物就不绑。

    §5 的语义是"行只记我看见过什么, 不承诺跟它保持一致"—— 提单来源正是这个：
    在哪个产物、哪一版、哪个画面看到的。永远不 refresh，cacheState 保持 unavailable。
    """
    src = source or {}
    app = str(src.get("app") or "").strip()
    if not app:
        return None
    view = str(src.get("view") or "").strip()
    url = str(src.get("url") or "").strip()
    args = [
        "task", "binding", "add", "--id", task_id,
        "--provider", app,
        "--provider-instance", str(src.get("version") or "-").strip() or "-",
        "--remote-kind", "view",
        "--remote-id", (view or url or "-")[:200],
    ]
    if url:
        args += ["--url", url]
    if view:
        args += ["--title", view[:200]]
    return args + ["--json"]


# 展示层标签（只影响 omni 侧渲染，不回写宿主）
STATE_LABELS = {
    "todo": "待办",
    "in-progress": "进行中",
    "paused": "已暂停",
    "blocked": "阻塞",
    "in-review": "待审",
    "completed": "完成",
    "cancelled": "已放弃",
}

# Dev 实例的 Electron userData 与 CLI 默认 (%APPDATA%\orca) 不同; 按元数据文件
# 存在性探测, 显式 ORCA_USER_DATA_PATH 永远优先。只影响"找得到宿主", 不改信封语义。
_USER_DATA_FALLBACKS = ("liwork-dev", "liwork")


def _runtime_env() -> dict[str, str]:
    if os.environ.get("ORCA_USER_DATA_PATH"):
        return {}
    appdata = os.environ.get("APPDATA")
    if not appdata:
        return {}
    default_meta = os.path.join(appdata, "orca", "orca-runtime.json")
    if os.path.isfile(default_meta):
        return {}
    for name in _USER_DATA_FALLBACKS:
        meta = os.path.join(appdata, name, "orca-runtime.json")
        if os.path.isfile(meta):
            return {"ORCA_USER_DATA_PATH": os.path.dirname(meta)}
    return {}


def _resolve_dir(flag_dir: str | None) -> str:
    directory = flag_dir or os.environ.get("OMNI_TBUS_LITEWORK_DIR") or DEFAULT_LITEWORK_DIR
    if not os.path.isfile(os.path.join(directory, "out", "cli", "index.js")):
        raise click.ClickException(
            f"LiTe CLI 不存在: {directory}\\out\\cli\\index.js\n"
            "用 --dir 或环境变量 OMNI_TBUS_LITEWORK_DIR 指向 litework 检出目录。"
        )
    return directory


def _run_cli(directory: str, args: list[str]) -> tuple[int, dict[str, Any] | None, str]:
    """跑一次信封命令，返回 (exit_code, parsed_stdout, stderr)。"""
    cli = os.path.join(directory, "out", "cli", "index.js")
    proc = subprocess.run(
        ["node", cli, *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env={**os.environ, **_runtime_env()},
        timeout=60,
    )
    payload: dict[str, Any] | None = None
    if proc.stdout.strip():
        try:
            parsed = json.loads(proc.stdout)
            if isinstance(parsed, dict):
                payload = parsed
        except json.JSONDecodeError:
            payload = None
    return proc.returncode, payload, proc.stderr


def _envelope_error(payload: dict[str, Any] | None) -> tuple[str, str] | None:
    error = payload.get("error") if payload and payload.get("ok") is False else None
    if isinstance(error, dict):
        return str(error.get("code", "unknown")), str(error.get("message", ""))
    return None


def _lifecycle_counts(tasks: list[dict[str, Any]]) -> dict[str, dict[str, int]]:
    phases: dict[str, dict[str, int]] = {phase: {} for phase in LIFECYCLE_PHASES}
    for task in tasks:
        state = str(task.get("stateKey", "todo"))
        phase = STATE_TO_PHASE.get(state, "open")
        phases[phase][state] = phases[phase].get(state, 0) + 1
    return phases


def _require_tasks(directory: str, extra_args: list[str]) -> tuple[list[dict[str, Any]], Any]:
    """跑一次 task list 信封并解包; runtime 不在线时给出指路。"""
    code, payload, stderr = _run_cli(directory, ["task", "list", "--json", *extra_args])
    failure = _envelope_error(payload)
    if code != 0 or failure or not payload:
        detail = failure[1] if failure else (stderr.strip() or f"exit={code}")
        if failure and failure[0] == "runtime_unavailable":
            raise click.ClickException(
                "总线宿主不在线 (runtime_unavailable): 先启动 LiWork/Orca 再看。\n"
                f"宿主: {directory}"
            )
        raise click.ClickException(f"信封调用失败: {detail}")
    result = payload.get("result") if isinstance(payload.get("result"), dict) else {}
    tasks = result.get("tasks") if isinstance(result.get("tasks"), list) else []
    return [t for t in tasks if isinstance(t, dict)], payload


def _sort_tasks(tasks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """与宿主同语义: orderKey 字典序在前、NULL 最后、回退 createdAt/id。
    视图侧再排一次 —— 旧宿主（未重启）返回 createdAt 序时列表依然正确。"""
    return sorted(
        tasks,
        key=lambda t: (
            t.get("orderKey") is None,
            str(t.get("orderKey") or ""),
            str(t.get("createdAt") or ""),
            str(t.get("id") or ""),
        ),
    )


def _render_tree(tasks: list[dict[str, Any]]) -> list[str]:
    by_id = {str(t.get("id")): t for t in tasks}
    children: dict[str, list[dict[str, Any]]] = {}
    roots: list[dict[str, Any]] = []
    for task in tasks:
        parent = task.get("parentTaskId")
        # 孤儿（父不在结果集里，如被 --state 过滤）按根渲染；防环由宿主保证。
        if parent and parent in by_id and parent != task.get("id"):
            children.setdefault(str(parent), []).append(task)
        else:
            roots.append(task)

    lines: list[str] = []

    def walk(task: dict[str, Any], depth: int) -> None:
        state = str(task.get("stateKey", "") or "?")
        label = STATE_LABELS.get(state, state)
        if task.get("archivedAt"):
            label += "/归档"
        lines.append(f"{'  ' * depth}[{label}] {task.get('id')} {task.get('title')}")
        for child in _sort_tasks(children.get(str(task.get("id")), [])):
            walk(child, depth + 1)

    for root in _sort_tasks(roots):
        walk(root, 0)
    return lines


@click.group("tbus")
def cmd_tbus() -> None:
    """个人任务总线 (task-core) — LiTe local-tasks 唯一真源, CLI 信封直连。

    子命令:
      list     原生列表: 树形(父子+顺序)+状态徽标
      status   总线健康: runtime 可达性 + 任务生命周期聚合
      call     信封透传: 原样转发 LiTe task 子命令 (--json 进出)
    """


@cmd_tbus.command("status")
@click.option("--dir", "litework_dir", default=None, help="litework 检出目录 (默认内置路径)")
@click.option("--json", "as_json", is_flag=True, help="输出 JSON")
def cmd_status(litework_dir: str | None, as_json: bool) -> None:
    """总线健康: runtime 可达性 + open/paused/done/dropped 聚合。"""
    directory = _resolve_dir(litework_dir)
    tasks, payload = _require_tasks(directory, [])
    phases = _lifecycle_counts(tasks)
    if as_json:
        click.echo(
            json.dumps(
                {
                    "host": directory,
                    "runtimeId": payload.get("_meta", {}).get("runtimeId"),
                    "total": len(tasks),
                    "phases": phases,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return
    click.echo(f"任务总线 @ {directory}")
    runtime_id = payload.get("_meta", {}).get("runtimeId")
    click.echo(f"runtime: 在线 ({runtime_id or 'local'})   任务: {len(tasks)}")
    for phase in LIFECYCLE_PHASES:
        states = phases[phase]
        if not states:
            continue
        breakdown = " · ".join(f"{k} {v}" for k, v in sorted(states.items()))
        click.echo(f"{phase:<8} {sum(states.values()):>4}   ({breakdown})")


@cmd_tbus.command("list")
@click.option("--state", "state_key", default=None, help="按 stateKey 过滤 (信封侧过滤)")
@click.option("--archived", "include_archived", is_flag=True, help="含归档")
@click.option("--dir", "litework_dir", default=None, help="litework 检出目录 (默认内置路径)")
@click.option("--json", "as_json", is_flag=True, help="输出信封原始 tasks JSON")
def cmd_list(
    state_key: str | None,
    include_archived: bool,
    litework_dir: str | None,
    as_json: bool,
) -> None:
    """原生列表: 树形 (父子 + orderKey 顺序) + 状态徽标。"""
    directory = _resolve_dir(litework_dir)
    extra: list[str] = []
    if state_key:
        extra += ["--state", state_key]
    if include_archived:
        extra.append("--include-archived")
    tasks, _payload = _require_tasks(directory, extra)

    if as_json:
        click.echo(json.dumps(tasks, ensure_ascii=False, indent=2))
        return

    phases = _lifecycle_counts(tasks)
    counts = " · ".join(f"{phase} {sum(states.values())}" for phase, states in phases.items())
    click.echo(f"任务总线 · {len(tasks)} 条 · {counts}")
    if not tasks:
        click.echo('(空) 开记: omni tbus call task create --title "..."')
        return
    for line in _render_tree(tasks):
        click.echo(line)


@cmd_tbus.command("intake")
@click.argument("title")
@click.option("--note", default=None, help="补充说明 (落描述)")
@click.option("--app", default=None, help="哪个产物看到的 (webtwin / spark-workshop / …)")
@click.option("--view", default=None, help="哪个画面/路由")
@click.option("--url", default=None, help="能点回去的深链")
@click.option("--app-version", "app_version", default=None, help="产物版本, 复现要")
@click.option("--dir", "litework_dir", default=None, help="litework 检出目录 (默认内置路径)")
@click.option("--json", "as_json", is_flag=True, help="输出 JSON")
def cmd_intake(
    title: str,
    note: str | None,
    app: str | None,
    view: str | None,
    url: str | None,
    app_version: str | None,
    litework_dir: str | None,
    as_json: bool,
) -> None:
    """提单: 把问题存进收件箱, 事后一起解决 (不定级 / 不指派 / 不进待办)。"""
    if not title.strip():
        raise click.ClickException("标题不能为空")
    directory = _resolve_dir(litework_dir)
    source = {"app": app, "view": view, "url": url, "version": app_version}
    code, payload, stderr = _run_cli(directory, intake_create_args(title, note, source))
    failure = _envelope_error(payload)
    if failure or code != 0 or not payload:
        message = failure[1] if failure else ((stderr or "").strip() or f"exit={code}")
        raise click.ClickException(message)
    task = ((payload.get("result") or {}).get("task") or {})
    task_id = str(task.get("id") or "")
    bound = False
    binding = intake_binding_args(task_id, source) if task_id else None
    if binding:
        # 绑不上不算失败: 单子本身已经存下了, 来源只是分诊时的便利。
        bcode, bpayload, _bstderr = _run_cli(directory, binding)
        bound = bcode == 0 and not _envelope_error(bpayload)
    if as_json:
        click.echo(json.dumps({"ok": True, "id": task_id, "sourceBound": bound}, ensure_ascii=False))
        return
    click.echo(f"已存入收件箱 {task_id}" + ("" if bound else " (来源未绑上)"))


@cmd_tbus.command("call", context_settings={"ignore_unknown_options": True})
@click.argument("args", nargs=-1, type=click.UNPROCESSED)
@click.option("--dir", "litework_dir", default=None, help="litework 检出目录 (默认内置路径)")
def cmd_call(args: tuple[str, ...], litework_dir: str | None) -> None:
    """信封透传: omni tbus call task show --id xxx --json"""
    if not args:
        raise click.ClickException("缺信封参数, 例: omni tbus call task list --json")
    directory = _resolve_dir(litework_dir)
    # 继承 stdio: --json 输出原样给人/给管道, 退出码原样转发。
    raise SystemExit(_call_stream(directory, list(args)))


def _call_stream(directory: str, args: list[str]) -> int:
    """透传版: 不捕获输出, 直接继承 stdio (给人/给管道用)。"""
    cli = os.path.join(directory, "out", "cli", "index.js")
    proc = subprocess.run(["node", cli, *args], env={**os.environ, **_runtime_env()})
    return proc.returncode
