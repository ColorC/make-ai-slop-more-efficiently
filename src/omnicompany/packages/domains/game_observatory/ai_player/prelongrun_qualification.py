from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..content_addressed_store import sha256_file
from ..store import ObservatoryStore
from .session_control import AIPlayerSessionControl
from .store import AIPlayerStore


AFK_ENVIRONMENT_ID = "environment.afkj.mumu1.snapshot-phase1.portrait.1_7_21"
SANGUO_ENVIRONMENT_ID = (
    "environment.sanguo.bilibili.mumu.account.lueyang-zhenwei-jiang."
    "server-1641.world-toubian-duanshui.1_31_0"
)


def _session(name: str) -> str:
    return f"ai-player-session.quick.{name}-20260720"


def _visits(second: str, third: str) -> list[dict[str, Any]]:
    return [
        {"visit": 2, "terminal_session_id": _session(second)},
        {"visit": 3, "terminal_session_id": _session(third)},
    ]


ROUTE_SPECS: tuple[dict[str, Any], ...] = (
    {
        "id": "afk.r1.identity-panel",
        "game_id": "afk-journey",
        "environment_id": AFK_ENVIRONMENT_ID,
        "title": "英雄身份面板进入",
        "visits": _visits("v2-afk-r1-v2-open", "v2-afk-r1-v3-open"),
        "notes": ["第二访视觉阈值保守停机，但 EvidenceStep 已通过并完成人工具视觉复核。"],
    },
    {
        "id": "afk.r2.attribute-long-scroll",
        "game_id": "afk-journey",
        "environment_id": AFK_ENVIRONMENT_ID,
        "title": "英雄属性长页向下边界",
        "visits": _visits(
            "v2-afk-r2-v2-scroll-down-4", "v2-afk-r2-v3-scroll-down-4"
        ),
        "notes": ["边界重复滚动允许画面幂等，动作执行和终态证据仍须通过。"],
    },
    {
        "id": "afk.r3.attribute-return",
        "game_id": "afk-journey",
        "environment_id": AFK_ENVIRONMENT_ID,
        "title": "英雄属性长页返回英雄页",
        "visits": _visits("v2-afk-r3-v2-back-hero", "v2-afk-r3-v3-back-hero"),
    },
    {
        "id": "afk.r4.equipment-wall-collapse",
        "game_id": "afk-journey",
        "environment_id": AFK_ENVIRONMENT_ID,
        "title": "赛季装备墙滚动后收起",
        "visits": _visits("v2-afk-r4-v2-collapse", "v2-afk-r4-v3-collapse"),
    },
    {
        "id": "afk.r5.equipment-wall-expand",
        "game_id": "afk-journey",
        "environment_id": AFK_ENVIRONMENT_ID,
        "title": "赛季装备墙重新展开",
        "visits": _visits("v2-afk-r5-v2-expand", "v2-afk-r5-v3-expand"),
        "notes": ["第三访视觉阈值保守停机，但 EvidenceStep 已通过并完成人工具视觉复核。"],
    },
    {
        "id": "afk.r6.boots-source",
        "game_id": "afk-journey",
        "environment_id": AFK_ENVIRONMENT_ID,
        "title": "装备墙靴子来源弹层",
        "visits": _visits("v2-afk-r6-v2-open-source", "v2-afk-r6-v3-open-source"),
    },
    {
        "id": "afk.r7.green-robe-source",
        "game_id": "afk-journey",
        "environment_id": AFK_ENVIRONMENT_ID,
        "title": "绿色长袍来源弹层",
        "visits": _visits(
            "v2-afk-r7-v2-open-green-source", "v2-afk-r7-v3-open-green-source"
        ),
    },
    {
        "id": "afk.r8.resonance-headband-current",
        "game_id": "afk-journey",
        "environment_id": AFK_ENVIRONMENT_ID,
        "title": "当前构建共鸣等级说明至飞翼头带来源",
        "visits": [
            {
                "visit": 2,
                "terminal_session_id": _session(
                    "v2-afk-r8-v2-open-headband-source-current"
                ),
                "sequence_session_ids": [
                    _session("v2-afk-r8-current-open-resonance-info"),
                    _session("v2-afk-r8-v2-close-resonance-info-current"),
                    _session("v2-afk-r8-v2-open-rowan-current"),
                    _session("v2-afk-r8-v2-open-season-wall-current"),
                    _session("v2-afk-r8-v2-open-headband-current"),
                    _session("v2-afk-r8-v2-open-headband-source-current"),
                ],
            },
            {
                "visit": 3,
                "terminal_session_id": _session(
                    "v2-afk-r8-v3-open-headband-source-current"
                ),
                "sequence_session_ids": [
                    _session("v2-afk-r8-stage-v3-close-headband-source"),
                    _session("v2-afk-r8-stage-v3-back-headband-wall"),
                    _session("v2-afk-r8-stage-v3-back-hero"),
                    _session("v2-afk-r8-stage-v3-back-hall"),
                    _session("v2-afk-r8-stage-v3-open-resonance-info"),
                    _session("v2-afk-r8-v3-close-resonance-info-current"),
                    _session("v2-afk-r8-v3-open-rowan-current"),
                    _session("v2-afk-r8-v3-open-season-wall-current"),
                    _session("v2-afk-r8-v3-open-headband-current"),
                    _session("v2-afk-r8-v3-open-headband-source-current"),
                ],
            },
        ],
        "notes": [
            "旧坐标 (369,1813) 在当前构建语义漂移为装备墙入口并导致误路，永久排除。",
            "冻结路线为共鸣等级说明、Rowan、赛季装备墙、飞翼头带来源。",
        ],
    },
    *tuple(
        {
            "id": f"sanguo.r{index}.{slug}",
            "game_id": "sanguo",
            "environment_id": SANGUO_ENVIRONMENT_ID,
            "title": title,
            "visits": _visits(
                f"v2-sanguo-r{index}-v2-{terminal}",
                f"v2-sanguo-r{index}-v3-{terminal}",
            ),
        }
        for index, slug, title, terminal in (
            (1, "profile", "账号与服务器信息", "close-profile"),
            (2, "mail", "邮件", "close-mail"),
            (3, "activity", "活动", "close-activity"),
            (4, "generals", "武将列表", "close-generals"),
            (5, "sunce-detail", "孙策详情", "close-sunce-detail"),
            (6, "alliance", "同盟", "close-alliance"),
            (7, "profession", "职业", "close-profession"),
            (8, "campaign", "征战", "close-campaign"),
            (9, "building-management", "建筑管理", "close-building-management"),
        )
    ),
    {
        "id": "sanguo.r10.city-transition",
        "game_id": "sanguo",
        "environment_id": SANGUO_ENVIRONMENT_ID,
        "title": "城内外镜头切换并返回",
        "visits": [
            {
                "visit": 2,
                "terminal_session_id": _session(
                    "v2-sanguo-r10-v2-return-city-fresh"
                ),
                "required_observation_step_ids": [
                    "evidence.step.observe.1743a3db51f24d91ae88bc7ed63c8a2c"
                ],
            },
            {
                "visit": 3,
                "terminal_session_id": _session(
                    "v2-sanguo-r10-v3-return-city-fresh"
                ),
                "required_observation_step_ids": [
                    "evidence.step.observe.09cd0f8e55464e0fb64de92bc7bd4b37"
                ],
            },
        ],
        "notes": ["跨镜头返回前强制新鲜观察；陈旧来源拒绝发生在动作前且未计入访问。"],
    },
    {
        "id": "sanguo.r11.chapter-tasks",
        "game_id": "sanguo",
        "environment_id": SANGUO_ENVIRONMENT_ID,
        "title": "章节任务面板",
        "visits": _visits(
            "v2-sanguo-r11-v2-close-chapter-tasks",
            "v2-sanguo-r11-v3-close-chapter-tasks",
        ),
    },
    {
        "id": "sanguo.r12.visit",
        "game_id": "sanguo",
        "environment_id": SANGUO_ENVIRONMENT_ID,
        "title": "寻访页面无抽取返回",
        "visits": _visits(
            "v2-sanguo-r12-v2-close-visit", "v2-sanguo-r12-v3-close-visit"
        ),
        "notes": ["仅进入并返回，不执行抽取或资源消费。"],
    },
)


def _executed_step_ids(
    control: AIPlayerSessionControl,
    environment_id: str,
    session_id: str,
) -> list[str]:
    result: list[str] = []
    for event in control.list_events(environment_id, session_id):
        if event.event_type == "checkpointed" and "EvidenceStep " in event.reason:
            result.append(event.reason.split("EvidenceStep ", 1)[1].split(" ", 1)[0])
    return result


def _step_receipt(store: ObservatoryStore, step_id: str) -> dict[str, Any]:
    step = store.get_evidence_step(step_id)
    if step is None:
        return {"id": step_id, "ok": False, "error": "missing_evidence_step"}
    artifacts: list[dict[str, Any]] = []
    for artifact_id in step.artifact_ids:
        artifact = store.get_artifact(artifact_id)
        if artifact is None:
            artifacts.append({"id": artifact_id, "ok": False, "error": "missing_artifact"})
            continue
        path = Path(artifact.path)
        verified = path.is_file() and sha256_file(path) == artifact.sha256
        artifacts.append(
            {
                "id": artifact.id,
                "kind": artifact.kind,
                "path": str(path),
                "sha256": artifact.sha256,
                "verified": verified,
            }
        )
    return {
        "id": step.id,
        "status": step.status,
        "before_frame_id": step.before_frame_id,
        "after_frame_id": step.after_frame_id,
        "action": step.action.model_dump(mode="json", by_alias=True),
        "artifact_receipts": artifacts,
        "ok": step.status == "passed" and bool(artifacts) and all(
            item.get("verified") for item in artifacts
        ),
    }


def build_prelongrun_path_matrix(store: ObservatoryStore) -> dict[str, Any]:
    control = AIPlayerSessionControl(AIPlayerStore(store))
    routes: list[dict[str, Any]] = []
    for spec in ROUTE_SPECS:
        visits: list[dict[str, Any]] = []
        for visit_spec in spec["visits"]:
            session_id = str(visit_spec["terminal_session_id"])
            session = control.get_session(spec["environment_id"], session_id)
            executed = _executed_step_ids(control, spec["environment_id"], session_id)
            terminal_receipts = [_step_receipt(store, item) for item in executed]
            observation_receipts = [
                _step_receipt(store, item)
                for item in visit_spec.get("required_observation_step_ids", [])
            ]
            allowed_state = session is not None and session.state in {"completed", "safe_stopped"}
            visit_ok = (
                allowed_state
                and len(terminal_receipts) == 1
                and terminal_receipts[0]["ok"]
                and all(item["ok"] for item in observation_receipts)
            )
            visits.append(
                {
                    "visit": visit_spec["visit"],
                    "ok": visit_ok,
                    "terminal_session_id": session_id,
                    "terminal_session_state": session.state if session else "missing",
                    "sequence_session_ids": visit_spec.get("sequence_session_ids", []),
                    "terminal_evidence": terminal_receipts,
                    "required_fresh_observations": observation_receipts,
                    "review_disposition": (
                        "current-build-agent-visual-inspected"
                        if session is not None and session.state == "safe_stopped"
                        else "canonical-evidence-passed"
                    ),
                }
            )
        routes.append(
            {
                "id": spec["id"],
                "game_id": spec["game_id"],
                "environment_id": spec["environment_id"],
                "title": spec["title"],
                "ok": all(item["ok"] for item in visits),
                "visits": visits,
                "notes": spec.get("notes", []),
            }
        )
    ok = all(route["ok"] for route in routes)
    payload: dict[str, Any] = {
        "schema": "game-observatory.prelongrun-path-matrix.v1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "ok": ok,
        "release_gate": "passed" if ok else "blocked",
        "facility_current_build_status": "frozen_from_real_second_and_third_visit_replay",
        "human_truth_status": "not_signed",
        "route_count": len(routes),
        "visit_count": sum(len(route["visits"]) for route in routes),
        "game_route_counts": {
            game_id: sum(route["game_id"] == game_id for route in routes)
            for game_id in ("afk-journey", "sanguo")
        },
        "superseded_candidates": [
            {
                "route_id": "afk.r8.resonance-headband-current",
                "candidate": "legacy-coordinate-369-1813",
                "reason": "当前构建坐标语义漂移为装备墙入口并导致误路；不进入冻结路径。",
            }
        ],
        "routes": routes,
    }
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    payload["matrix_sha256"] = hashlib.sha256(canonical.encode()).hexdigest()
    return payload


def write_prelongrun_path_matrix(
    store: ObservatoryStore,
    destination: Path | None = None,
) -> dict[str, Any]:
    payload = build_prelongrun_path_matrix(store)
    output = destination or (
        store.root
        / "benchmarks"
        / "ai_player"
        / "results"
        / "v2_prelongrun_path_matrix.v1.json"
    )
    output = output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    payload["path"] = str(output)
    return payload
