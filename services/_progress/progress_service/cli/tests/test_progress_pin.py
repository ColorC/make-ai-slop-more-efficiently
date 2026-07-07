# -*- coding: utf-8 -*-
"""progress CLI 置顶三命令(pin/unpin/pins)的测试。

先写测试再实现（TDD）。覆盖：
  - 正常流程：pin 存在的任务/任务线、unpin、pins 列表（含 --json）
  - 错误样本①：服务未起 → 人话错误，非 0 退出，不吐 Python 堆栈
  - 错误样本②：pin 一个不存在的任务号 → CLI 侧兜底拒绝（不打 /api/pin），人话报错
  - 错误样本③：unpin 一个本没置顶的 → 幂等成功（服务端本来就幂等，不因 CLI 校验而变得更严格）

绝大多数测试用 monkeypatch 注入假的 urlopen，不依赖真实服务。
另有一个 @pytest.mark.live 的演练测试，仅在真服务(127.0.0.1:8230)可达时才跑，
会真实 pin 一个已存在的旧任务再 unpin 恢复现场，不改变置顶总数。
"""
from __future__ import annotations

import importlib.util
import json
import sys
import urllib.error
from pathlib import Path

import pytest

MODULE_PATH = Path(__file__).resolve().parents[1] / "progress.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("progress_cli_under_test", MODULE_PATH)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture()
def wn():
    """每个测试拿一份新鲜模块实例，避免 monkeypatch 互相污染。"""
    return _load_module()


FAKE_BOARD = {
    "counts": {"clusters": 1, "goals": 1, "tasks": 2},
    "pins": [
        {"subject_kind": "task", "subject_id": "t1", "title": "已置顶的任务", "completion": 50, "channel": "local"},
    ],
    "clusters": [
        {
            "id": "c1",
            "title": "域1",
            "goals": [
                {
                    "id": "g1",
                    "title": "任务线1",
                    "line": "main",
                    "tasks": [
                        {"id": "t1", "title": "已置顶的任务", "completion": 50, "status": "in_progress", "channel": "local"},
                        {"id": "t2", "title": "未置顶的任务", "completion": 0, "status": "todo", "channel": "local"},
                    ],
                }
            ],
        }
    ],
    "loose_tasks": [
        {"id": "t3", "title": "游离任务", "completion": 10, "status": "todo", "channel": "meego"},
    ],
}


class _FakeResponse:
    def __init__(self, payload: dict):
        self._raw = json.dumps(payload).encode("utf-8")

    def read(self):
        return self._raw

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _install_fake_transport(monkeypatch, wn, board=FAKE_BOARD, recorder=None):
    """monkeypatch urllib.request.urlopen：GET /api/board 回 board；POST /api/pin 回 ok。"""

    def fake_urlopen(req, timeout=60):
        method = req.get_method()
        url = req.full_url
        if recorder is not None:
            body = req.data.decode("utf-8") if req.data else None
            recorder.append((method, url, json.loads(body) if body else None))
        if url.endswith("/api/board") and method == "GET":
            return _FakeResponse(board)
        if url.endswith("/api/pin") and method == "POST":
            payload = json.loads(req.data.decode("utf-8"))
            return _FakeResponse({"ok": True, "pinned": payload.get("pinned", True)})
        raise AssertionError(f"unexpected request: {method} {url}")

    monkeypatch.setattr(wn.urllib.request, "urlopen", fake_urlopen)


def _run(wn, argv):
    parser = wn.build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


# ── 正常流程 ─────────────────────────────────────────────────────────────────

def test_pins_lists_existing_pins_json(wn, monkeypatch, capsys):
    _install_fake_transport(monkeypatch, wn)
    _run(wn, ["pins", "--json"])
    out = json.loads(capsys.readouterr().out)
    assert out == FAKE_BOARD["pins"]


def test_pin_existing_task_succeeds(wn, monkeypatch, capsys):
    recorder = []
    _install_fake_transport(monkeypatch, wn, recorder=recorder)
    _run(wn, ["pin", "t2"])
    out = capsys.readouterr().out
    assert "已置顶" in out
    posts = [r for r in recorder if r[0] == "POST"]
    assert len(posts) == 1
    _, url, body = posts[0]
    assert url.endswith("/api/pin")
    assert body == {"subject_kind": "task", "subject_id": "t2", "note": "", "pinned": True}


def test_pin_existing_goal_succeeds_with_goal_flag(wn, monkeypatch):
    recorder = []
    _install_fake_transport(monkeypatch, wn, recorder=recorder)
    _run(wn, ["pin", "g1", "--goal"])
    posts = [r for r in recorder if r[0] == "POST"]
    assert len(posts) == 1
    _, _, body = posts[0]
    assert body["subject_kind"] == "goal"
    assert body["subject_id"] == "g1"


def test_pin_loose_task_succeeds(wn, monkeypatch):
    """loose_tasks（外部收件箱，未挂 goal）里的任务号也应通过存在性校验。"""
    recorder = []
    _install_fake_transport(monkeypatch, wn, recorder=recorder)
    _run(wn, ["pin", "t3"])
    posts = [r for r in recorder if r[0] == "POST"]
    assert len(posts) == 1


def test_pin_repeat_without_note_keeps_existing_note(wn, monkeypatch, capsys):
    """备注误伤回归：对已置顶主体重复置顶、且未显式给 --note 时，应沿用旧备注，不能被空串抹掉。"""
    board_with_note = json.loads(json.dumps(FAKE_BOARD))  # 深拷贝，避免污染其他测试共享的 FAKE_BOARD
    board_with_note["pins"][0]["note"] = "当前任务合集(用户2026-07-03置顶)"
    recorder = []
    _install_fake_transport(monkeypatch, wn, board=board_with_note, recorder=recorder)
    _run(wn, ["pin", "t1"])  # t1 已置顶，这次重复置顶不带 --note
    out = capsys.readouterr().out
    assert "已置顶" in out
    posts = [r for r in recorder if r[0] == "POST"]
    assert len(posts) == 1
    _, _, body = posts[0]
    assert body == {
        "subject_kind": "task",
        "subject_id": "t1",
        "note": "当前任务合集(用户2026-07-03置顶)",
        "pinned": True,
    }


def test_pin_repeat_with_explicit_empty_note_clears_it(wn, monkeypatch):
    """用户显式给 --note ""（显式空串）才允许清空旧备注，这是唯一合法的覆盖路径。"""
    board_with_note = json.loads(json.dumps(FAKE_BOARD))
    board_with_note["pins"][0]["note"] = "老备注"
    recorder = []
    _install_fake_transport(monkeypatch, wn, board=board_with_note, recorder=recorder)
    _run(wn, ["pin", "t1", "--note", ""])
    posts = [r for r in recorder if r[0] == "POST"]
    assert len(posts) == 1
    _, _, body = posts[0]
    assert body["note"] == ""


def test_unpin_succeeds(wn, monkeypatch, capsys):
    recorder = []
    _install_fake_transport(monkeypatch, wn, recorder=recorder)
    _run(wn, ["unpin", "t1"])
    out = capsys.readouterr().out
    assert "已取消置顶" in out
    posts = [r for r in recorder if r[0] == "POST"]
    _, _, body = posts[0]
    assert body == {"subject_kind": "task", "subject_id": "t1", "pinned": False}


# ── 错误样本① 服务未起 ───────────────────────────────────────────────────────

def test_service_down_gives_human_error_not_traceback(wn, monkeypatch, capsys):
    def fake_urlopen(req, timeout=60):
        raise urllib.error.URLError("Connection refused")

    monkeypatch.setattr(wn.urllib.request, "urlopen", fake_urlopen)

    with pytest.raises(SystemExit) as exc_info:
        _run(wn, ["pins"])
    assert exc_info.value.code != 0
    err = capsys.readouterr().err
    assert "连不上服务" in err
    assert "Traceback" not in err


def test_service_down_on_pin_gives_human_error(wn, monkeypatch, capsys):
    def fake_urlopen(req, timeout=60):
        raise urllib.error.URLError("Connection refused")

    monkeypatch.setattr(wn.urllib.request, "urlopen", fake_urlopen)

    with pytest.raises(SystemExit) as exc_info:
        _run(wn, ["pin", "t1"])
    assert exc_info.value.code != 0
    err = capsys.readouterr().err
    assert "连不上服务" in err
    assert "Traceback" not in err


# ── 错误样本② 任务号不存在 ───────────────────────────────────────────────────

def test_pin_nonexistent_task_id_rejected_without_posting(wn, monkeypatch, capsys):
    recorder = []
    _install_fake_transport(monkeypatch, wn, recorder=recorder)
    with pytest.raises(SystemExit) as exc_info:
        _run(wn, ["pin", "t-does-not-exist"])
    assert exc_info.value.code != 0
    err = capsys.readouterr().err
    assert "t-does-not-exist" in err
    assert "不存在" in err
    assert "Traceback" not in err
    # 关键：校验在本地拦下，压根没有发 POST /api/pin
    posts = [r for r in recorder if r[0] == "POST"]
    assert posts == []


def test_pin_nonexistent_goal_id_rejected(wn, monkeypatch, capsys):
    recorder = []
    _install_fake_transport(monkeypatch, wn, recorder=recorder)
    with pytest.raises(SystemExit):
        _run(wn, ["pin", "g-does-not-exist", "--goal"])
    posts = [r for r in recorder if r[0] == "POST"]
    assert posts == []


def test_pin_task_id_that_is_actually_a_goal_id_rejected(wn, monkeypatch):
    """未加 --goal 时用 goal 的 id 去 pin 任务，应视为任务号不存在（kind 要匹配）。"""
    recorder = []
    _install_fake_transport(monkeypatch, wn, recorder=recorder)
    with pytest.raises(SystemExit):
        _run(wn, ["pin", "g1"])  # g1 是 goal id，不是 task id
    posts = [r for r in recorder if r[0] == "POST"]
    assert posts == []


def test_pin_nonexistent_task_id_json_mode_exits_nonzero(wn, monkeypatch, capsys):
    """退出码回归：--json 模式下任务号不存在，退出码也要非 0（此前曾是 0，只有 JSON 里 ok:false）。"""
    recorder = []
    _install_fake_transport(monkeypatch, wn, recorder=recorder)
    with pytest.raises(SystemExit) as exc_info:
        _run(wn, ["pin", "t-does-not-exist", "--json"])
    assert exc_info.value.code != 0
    out = json.loads(capsys.readouterr().out)
    assert out["ok"] is False
    posts = [r for r in recorder if r[0] == "POST"]
    assert posts == []


# ── 错误样本③ 取消一个本没置顶的：幂等成功 ───────────────────────────────────

def test_unpin_task_not_pinned_is_idempotent_success(wn, monkeypatch, capsys):
    """t2 存在但未置顶；unpin 应正常成功（不因为它本没置顶就报错）。"""
    recorder = []
    _install_fake_transport(monkeypatch, wn, recorder=recorder)
    _run(wn, ["unpin", "t2"])
    out = capsys.readouterr().out
    assert "已取消置顶" in out
    posts = [r for r in recorder if r[0] == "POST"]
    assert len(posts) == 1


def test_unpin_nonexistent_id_still_idempotent_no_crash(wn, monkeypatch, capsys):
    """unpin 一个压根不存在的号：不应该像 pin 那样硬拒绝（服务端本来就幂等 ok），
    CLI 顶多提示一句，但不能崩、不能非零退出（幂等语义）。"""
    recorder = []
    _install_fake_transport(monkeypatch, wn, recorder=recorder)
    _run(wn, ["unpin", "never-existed"])
    posts = [r for r in recorder if r[0] == "POST"]
    assert len(posts) == 1  # 仍然放行给服务端，服务端幂等 ok


# ── --help 契约 ──────────────────────────────────────────────────────────────

def test_help_does_not_crash(wn, capsys):
    parser = wn.build_parser()
    with pytest.raises(SystemExit) as exc_info:
        parser.parse_args(["--help"])
    assert exc_info.value.code == 0


def test_pin_help_does_not_crash(wn, capsys):
    parser = wn.build_parser()
    with pytest.raises(SystemExit) as exc_info:
        parser.parse_args(["pin", "--help"])
    assert exc_info.value.code == 0


# ── 真服务演练（可用时才跑） ──────────────────────────────────────────────────

def _live_service_up() -> bool:
    import urllib.request as real_urllib
    try:
        with real_urllib.urlopen("http://127.0.0.1:8230/api/board", timeout=2):
            return True
    except Exception:
        return False


@pytest.mark.skipif(not _live_service_up(), reason="真服务(127.0.0.1:8230)未起，跳过演练")
def test_live_pin_unpin_roundtrip_restores_state(wn):
    """挑一个真实存在、当前未置顶的旧任务，pin 再 unpin，验证置顶总数恢复原状。"""
    board = wn.get("/api/board")
    pinned_ids = {p["subject_id"] for p in board.get("pins", [])}
    candidate = None
    for cl in board.get("clusters", []):
        for g in cl.get("goals", []):
            for t in g.get("tasks", []):
                if t["id"] not in pinned_ids:
                    candidate = t["id"]
                    break
            if candidate:
                break
        if candidate:
            break
    if candidate is None:
        pytest.skip("没有可用的未置顶任务做演练")

    before_count = len(board.get("pins", []))

    parser = wn.build_parser()
    _run(wn, ["pin", candidate])
    after_pin = wn.get("/api/board")
    assert any(p["subject_id"] == candidate for p in after_pin.get("pins", []))
    assert len(after_pin.get("pins", [])) == before_count + 1

    _run(wn, ["unpin", candidate])
    after_unpin = wn.get("/api/board")
    assert not any(p["subject_id"] == candidate for p in after_unpin.get("pins", []))
    assert len(after_unpin.get("pins", [])) == before_count
