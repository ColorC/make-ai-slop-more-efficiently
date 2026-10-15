"""Run a CLI once and deliver one terminal signal to the original session.

The supervisor blocks in the OS process wait. No model participates in waiting,
status checks, log collection, or notification delivery. All receipts are local
to the explicitly selected working project, including on failure.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
from datetime import datetime, timezone
from typing import Any, Callable
from uuid import uuid4


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _write(path: Path, data: dict[str, Any]) -> None:
    temporary = path.with_suffix(f'.{os.getpid()}.tmp')
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
    os.replace(temporary, path)


def read_receipt(path: str | Path) -> dict[str, Any]:
    return json.loads(Path(path).resolve().read_text(encoding='utf-8'))


def _notify(owner: str, message: str, *, job_id: str) -> dict[str, Any]:
    # Ordinary AGENT MESSAGE envelopes are intentionally quiet. Completion is
    # an explicitly requested terminal wake, using the same durable wake tokens
    # and exact PTY transport as external-worker terminal notifications.
    from omnicompany.core.session_messages import resolve_target
    from omnicompany.core.quiet_coordination import QuietCoordinationStore
    from omnicompany.packages.services._core.agent.control_plane import send_agent_input
    target = resolve_target(owner)
    transport = str(target.get('pty_id') or '')
    session = str(target.get('session_id') or '')
    if not transport or not session:
        raise RuntimeError('original session has no exact completion transport')
    message_id = f'{job_id}:terminal'
    store = QuietCoordinationStore()
    store.put(message_id=message_id, event_type='command_terminal', actor_type='system',
              origin_provider='process', source_session_id=session, target_session_id=session,
              source_project=str(target.get('project') or ''),
              target_project=str(target.get('project') or ''), transport_target_id=transport,
              run_id=job_id, idempotency_key=message_id, wake_policy='terminal_once',
              payload={'request': message, 'job_id': job_id})
    if not store.claim_wake(message_id):
        return {'id': message_id, 'delivery': {'mode': 'already_claimed'}}
    result = send_agent_input(
        transport, f'[OMNI COMMAND TERMINAL WAKE · {message_id}]\n'
        f"原会话：{session}；project={target.get('project') or ''}\n{message}", submit=True,
    )
    if result.get('quiet') or result.get('wake_policy') == 'never' or result.get('submission') == 'stored':
        raise RuntimeError(f'completion was stored without waking the original session: {result}')
    return {'id': message_id, 'delivery': {
        'mode': 'unconfirmed' if result.get('ok') is False else 'terminal_wake', **result,
    }}


def _stop_child_tree(process: subprocess.Popen) -> None:
    # Only this command's descendants: never kill by executable name.
    try:
        import psutil
        try:
            children = psutil.Process(process.pid).children(recursive=True)
        except psutil.NoSuchProcess:
            # The child can exit between the timeout and descendant lookup.
            children = []
        for child in reversed(children):
            try:
                child.kill()
            except psutil.NoSuchProcess:
                pass
    except (ImportError, OSError):
        pass
    if process.poll() is None:
        process.kill()
    process.wait()


def execute_receipt(path: str | Path, *, notifier: Callable | None = None) -> dict[str, Any]:
    """Supervisor entry point; lock prevents duplicate execution/notification."""
    path = Path(path).resolve()
    lock = path.parent / 'supervisor.lock'
    try:
        lock_fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        return read_receipt(path)
    # Keep the marker after completion. A command must never replay just because
    # its supervisor was invoked a second time or notification failed.
    os.close(lock_fd)
    receipt = read_receipt(path)
    if receipt['state'] != 'queued':
        return receipt
    receipt.update(state='running', started_at=_now(), supervisor_pid=os.getpid())
    _write(path, receipt)
    process = None
    try:
        with open(receipt['paths']['stdout'], 'wb') as stdout, open(receipt['paths']['stderr'], 'wb') as stderr:
            options: dict[str, Any] = dict(cwd=receipt['cwd'], stdin=subprocess.DEVNULL,
                                          stdout=stdout, stderr=stderr, shell=False)
            if os.name == 'nt':
                options['creationflags'] = subprocess.CREATE_NO_WINDOW
            process = subprocess.Popen(receipt['argv'], **options)
            receipt['command_pid'] = process.pid
            _write(path, receipt)
            try:
                code = process.wait(timeout=receipt.get('timeout_seconds'))
                receipt.update(state='succeeded' if code == 0 else 'failed', exit_code=code)
            except subprocess.TimeoutExpired:
                _stop_child_tree(process)
                receipt.update(state='timed_out', exit_code=process.returncode,
                               error=f"command exceeded {receipt['timeout_seconds']} seconds")
    except Exception as exc:
        if process is not None and process.poll() is None:
            _stop_child_tree(process)
        receipt.update(state='failed', exit_code=None, error=f'{type(exc).__name__}: {exc}')
    receipt['finished_at'] = _now()
    # Flush and publish terminal evidence BEFORE sending the completion signal.
    _write(path, receipt)
    owner = receipt.get('notify_session')
    if owner:
        receipt['notification'] = {'state': 'sending', 'attempts': 1}
        _write(path, receipt)
        message = (
            f"命令已结束：{receipt['job_id']}；状态={receipt['state']}；exit_code={receipt.get('exit_code')}。\n"
            f"工作目录：{receipt['cwd']}\n回执：{path}\n"
            f"stdout：{receipt['paths']['stdout']}\nstderr：{receipt['paths']['stderr']}\n"
            f"supervisor：{receipt['paths']['supervisor_log']}\n"
            '请读取这次结果并继续原任务；不要轮询，不要仅因收到此通知重新执行命令。'
        )
        try:
            result = (notifier(owner, message) if notifier is not None
                      else _notify(owner, message, job_id=receipt['job_id']))
            delivery = result.get('delivery') or {}
            receipt['notification'].update(
                state='unconfirmed' if delivery.get('mode') == 'unconfirmed' else 'delivered',
                message_id=result.get('id'), delivery=delivery,
            )
        except Exception as exc:
            receipt['notification'].update(state='failed', error=f'{type(exc).__name__}: {exc}')
        _write(path, receipt)
    return receipt


def start_command(argv: list[str], *, cwd: str | Path, notify_session: str = '',
                  output_dir: str | Path | None = None, timeout_seconds: float | None = None,
                  foreground: bool = False, forced_poll_count: int = 0) -> dict[str, Any]:
    root = Path(cwd).expanduser().resolve()
    if not root.is_dir():
        raise ValueError(f'working directory does not exist: {root}')
    if not argv or not str(argv[0]).strip():
        raise ValueError('command argv must not be empty')
    if timeout_seconds is not None and timeout_seconds <= 0:
        raise ValueError('timeout must be positive')
    if not foreground and not notify_session:
        raise ValueError('background execution requires the original notify_session')
    if notify_session and (type(forced_poll_count) is not int or forced_poll_count < 2):
        raise ValueError('terminal notification is a fallback only after at least 2 actual forced polls '
                         'of the same task; use native waiting first, never poll to meet this threshold')
    job_id = 'command-' + uuid4().hex[:16]
    directory = Path(output_dir).expanduser() if output_dir else root / '.omni' / 'tmp' / 'command-runs' / job_id
    if not directory.is_absolute():
        directory = root / directory
    directory = directory.resolve()
    directory.mkdir(parents=True, exist_ok=False)
    paths = {key: str(directory / name) for key, name in {
        'receipt': 'receipt.json', 'stdout': 'stdout.log', 'stderr': 'stderr.log',
        'supervisor_log': 'supervisor.log',
    }.items()}
    for key in ('stdout', 'stderr', 'supervisor_log'):
        Path(paths[key]).touch()
    receipt = dict(schema='omni.command-completion/v1', job_id=job_id, state='queued',
                   argv=list(argv), cwd=str(root), notify_session=notify_session,
                   timeout_seconds=timeout_seconds, paths=paths, created_at=_now(),
                   notification_eligibility={'forced_poll_count': forced_poll_count,
                                             'basis': 'caller_declared_actual_wait_receipts'},
                   notification={'state': 'pending' if notify_session else 'not_requested', 'attempts': 0})
    path = Path(paths['receipt'])
    _write(path, receipt)
    if foreground:
        return execute_receipt(path)
    options: dict[str, Any] = {}
    if os.name == 'nt':
        options['creationflags'] = subprocess.CREATE_NO_WINDOW | subprocess.DETACHED_PROCESS
    else:
        options['start_new_session'] = True
    environment = dict(os.environ)
    source_root = str(Path(__file__).resolve().parents[5])
    environment['PYTHONPATH'] = source_root + os.pathsep + environment.get('PYTHONPATH', '')
    try:
        with open(paths['supervisor_log'], 'ab') as supervisor_log:
            supervisor = subprocess.Popen(
                [sys.executable, str(Path(__file__).resolve()), str(path)], cwd=str(root),
                stdin=subprocess.DEVNULL, stdout=supervisor_log, stderr=supervisor_log,
                env=environment, **options,
            )
        # Do not overwrite the receipt here: the child may already have finished.
        return {**receipt, 'supervisor_pid': supervisor.pid,
                'next_action': 'Wait for the completion signal; do not poll from the LLM.'}
    except Exception as exc:
        receipt.update(state='failed', error=f'cannot start supervisor: {exc}', finished_at=_now())
        _write(path, receipt)
        return receipt


if __name__ == '__main__':
    execute_receipt(sys.argv[1])
