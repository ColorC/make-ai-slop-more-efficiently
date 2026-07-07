"""ops.py 与新 API 端点测试。"""

from __future__ import annotations

import os
import sys

_SRC = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", ".."))
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

from omnicompany.packages.narrative_studio import models as m, ops  # noqa: E402


def test_text_replace_dry_and_apply(sample_project):
    # 镜子宫殿命题里有"镜子宫殿"
    dry = ops.text_replace(sample_project, "镜子宫殿", "水晶宫", dry_run=True)
    assert dry["count"] >= 1
    assert dry["project"] is None
    applied = ops.text_replace(sample_project, "镜子宫殿", "水晶宫", dry_run=False)
    assert applied["count"] == dry["count"]
    assert "水晶宫" in applied["project"]["premise"]["proposition"]
    assert "镜子宫殿" not in applied["project"]["premise"]["proposition"]


def test_scene_split(sample_project):
    # s-start 有 2 条 objective_events,从第 1 条后切
    data, warnings = ops.scene_split(sample_project, "s-start", at=1)
    scenes = {s["id"]: s for s in data["scenes"]}
    assert "s-start-b" in scenes
    assert len(scenes["s-start"]["objective_events"]) == 1
    assert len(scenes["s-start-b"]["objective_events"]) == 1
    # 新节点存在 + 第一场→第二场 连接
    node_ids = {n["id"] for n in data["nodes"]}
    assert any(s.endswith("-b") for s in node_ids)


def test_scene_merge(sample_project):
    data, warnings = ops.scene_merge(sample_project, "s-start", "s-mirror")
    ids = {s["id"] for s in data["scenes"]}
    assert "s-mirror" not in ids
    a = next(s for s in data["scenes"] if s["id"] == "s-start")
    # s-mirror 的事件并入 s-start
    assert any("窄聊天窗" in e for e in a["objective_events"])


def test_project_diff(sample_project):
    a = sample_project
    b = sample_project.model_copy(deep=True)
    b.characters = b.characters[:-1]  # 删一个角色
    d = ops.project_diff(a, b)
    assert "characters" in d["carriers"]
    assert d["carriers"]["characters"]["removed"]


def test_new_endpoints_smoke(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from omnicompany.packages.narrative_studio import api as ns_api, importer, storage
    # ⚠ 必须在隔离 tmp 数据目录上跑,绝不能碰线上 vilo 数据(会抹掉用户在工作室里的编辑)。
    # PROJECTS_ROOT 是模块常量;monkeypatch 它 + 清空 _project 缓存,使 api 全程读写 tmp。
    proj_root = tmp_path / "projects"
    monkeypatch.setattr(ns_api, "PROJECTS_ROOT", proj_root)
    monkeypatch.setattr(ns_api, "_project", None)
    storage.save_project(importer.import_vilo(ns_api.VILO_REPO), proj_root / ns_api.ACTIVE_PROJECT)
    c = TestClient(ns_api.app)
    assert c.post("/api/replace", json={"find": "枫钟", "replace": "枫钟", "dry_run": True}).status_code == 200
    assert c.get("/api/versions").status_code == 200
    assert c.post("/api/versions/save", json={"name": "_test_v"}).status_code == 200
    assert c.get("/api/diff", params={"a": "_working", "b": "_test_v"}).status_code == 200
    r = c.post("/api/batch-update", json={"carrier": "scenes", "ids": ["s-fz-n1"], "patch": {"status": "done"}})
    assert r.status_code == 200
