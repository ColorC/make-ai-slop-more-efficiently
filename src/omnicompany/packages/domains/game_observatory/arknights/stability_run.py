# [OMNI] origin=codex domain=game_observatory/arknights ts=2026-08-09T10:10:00Z type=stability-acceptance status=active
# [OMNI] summary="以冻结证据账本和单连接长窗口验收明日方舟视觉 IO 的持续稳定性。"
# [OMNI] why="完成回调不能证明通关，零散运行也不能证明连续稳定；需用掉落事件、新鲜帧和连接心跳共同闭环。"
# [OMNI] tags=game-observatory,arknights,maa,stability,evidence,acceptance
"""明日方舟代表性长链验收与冻结证据账本。

本模块刻意不做节点穷举。动态运行只覆盖一条高价值长链：在一个 MaaOperator 连接中完成
三次普通关卡、跨页面导航、回到主页，并维持视觉心跳直到连续窗口达标。战斗只下发一次，
随后心跳全部是只读识别，防止监控过程重放消耗型动作。

通关真值来自 ``StageDrops`` 的关卡代码与三星字段；任务链完成回调只作执行旁证。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence

from .maa_operator import MaaEvent, MaaOperator, TouchMode
from .operations import ArknightsOperations

__all__ = [
    "AcceptanceLedgerV1",
    "FightVictoryV1",
    "FrozenRunEvidenceV1",
    "RunVerdict",
    "build_acceptance_ledger",
    "build_frozen_matrix_ledger",
    "materialize_acceptance_ledger",
    "run_live_stability_window",
    "summarize_fight_victories",
]

_HOME = "Omni@Home-Detect"
_OPEN_TERMINAL = "Omni@Home-OpenTerminal"
_TERMINAL = "Omni@TerminalHub-Detect"
_TO_HOME = "QuickSwitch@ToHome"


class RunVerdict(str, Enum):
    """冻结运行的最终分类。"""

    PASS = "pass"
    RECOVERED = "recovered"
    FAIL = "fail"


@dataclass(frozen=True, slots=True)
class FightVictoryV1:
    """由结算掉落事件独立证明的一次三星胜利。"""

    stage: str
    stars: int
    drops: tuple[dict[str, Any], ...]


@dataclass(frozen=True, slots=True)
class FrozenRunEvidenceV1:
    """可计入验收的一段冻结实机证据。"""

    run_id: str
    duration_seconds: float
    verdict: RunVerdict
    evidence_refs: tuple[str, ...]
    continuous: bool = True
    normal_victories: int = 0
    navigation_chains: int = 0
    equivalent_long_segments: int = 0
    recovery_drills: int = 0

    def validate(self) -> None:
        if not self.run_id.strip():
            raise ValueError("run_id 不能为空")
        if self.duration_seconds <= 0:
            raise ValueError(f"{self.run_id}: duration_seconds 必须为正数")
        if not self.evidence_refs or any(not ref.strip() for ref in self.evidence_refs):
            raise ValueError(f"{self.run_id}: 至少需要一条非空 evidence_ref")
        for name in (
            "normal_victories",
            "navigation_chains",
            "equivalent_long_segments",
            "recovery_drills",
        ):
            if getattr(self, name) < 0:
                raise ValueError(f"{self.run_id}: {name} 不能为负数")


@dataclass(frozen=True, slots=True)
class AcceptanceLedgerV1:
    """目标 C 的机器可判定汇总。"""

    runs: tuple[FrozenRunEvidenceV1, ...]
    counted_duration_seconds: float
    max_continuous_seconds: float
    normal_victories: int
    navigation_chains: int
    equivalent_long_segments: int
    recovery_drills: int
    all_representative_runs_resolved: bool
    meets_duration: bool
    meets_continuous_window: bool
    meets_normal_victories: bool
    meets_navigation: bool
    meets_equivalent_long_segment: bool
    meets_recovery: bool

    @property
    def accepted(self) -> bool:
        return all(
            (
                self.all_representative_runs_resolved,
                self.meets_duration,
                self.meets_continuous_window,
                self.meets_normal_victories,
                self.meets_navigation,
                self.meets_equivalent_long_segment,
                self.meets_recovery,
            )
        )

    def as_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["runs"] = [
            {**asdict(run), "verdict": run.verdict.value} for run in self.runs
        ]
        payload["accepted"] = self.accepted
        return payload


def build_acceptance_ledger(
    runs: Iterable[FrozenRunEvidenceV1],
    *,
    minimum_total_seconds: float = 3600.0,
    minimum_continuous_seconds: float = 1800.0,
    minimum_normal_victories: int = 3,
    minimum_navigation_chains: int = 1,
    minimum_equivalent_long_segments: int = 1,
    minimum_recovery_drills: int = 1,
) -> AcceptanceLedgerV1:
    """汇总冻结证据，拒绝重复运行或同一证据被重复计算。"""

    frozen = tuple(runs)
    seen_ids: set[str] = set()
    seen_refs: set[str] = set()
    for run in frozen:
        run.validate()
        if run.run_id in seen_ids:
            raise ValueError(f"重复 run_id: {run.run_id}")
        duplicate_refs = seen_refs.intersection(run.evidence_refs)
        if duplicate_refs:
            raise ValueError(
                f"证据不能跨运行重复计时: {', '.join(sorted(duplicate_refs))}"
            )
        seen_ids.add(run.run_id)
        seen_refs.update(run.evidence_refs)

    counted = tuple(
        run for run in frozen if run.verdict in {RunVerdict.PASS, RunVerdict.RECOVERED}
    )
    duration = sum(run.duration_seconds for run in counted)
    continuous = max(
        (run.duration_seconds for run in counted if run.continuous), default=0.0
    )
    victories = sum(run.normal_victories for run in counted)
    navigation = sum(run.navigation_chains for run in counted)
    long_segments = sum(run.equivalent_long_segments for run in counted)
    recovery = sum(run.recovery_drills for run in counted)
    all_resolved = all(run.verdict is not RunVerdict.FAIL for run in frozen)
    return AcceptanceLedgerV1(
        runs=frozen,
        counted_duration_seconds=round(duration, 3),
        max_continuous_seconds=round(continuous, 3),
        normal_victories=victories,
        navigation_chains=navigation,
        equivalent_long_segments=long_segments,
        recovery_drills=recovery,
        all_representative_runs_resolved=all_resolved,
        meets_duration=duration >= minimum_total_seconds,
        meets_continuous_window=continuous >= minimum_continuous_seconds,
        meets_normal_victories=victories >= minimum_normal_victories,
        meets_navigation=navigation >= minimum_navigation_chains,
        meets_equivalent_long_segment=(
            long_segments >= minimum_equivalent_long_segments
        ),
        meets_recovery=recovery >= minimum_recovery_drills,
    )


def build_frozen_matrix_ledger(
    matrix: Mapping[str, Any], live_receipt: Mapping[str, Any]
) -> AcceptanceLedgerV1:
    """用预先冻结的矩阵和实机回执生成最终账本，拒绝事后放宽口径。"""

    thresholds = matrix.get("acceptance_thresholds")
    historical = matrix.get("selected_historical_runs")
    selected_live = matrix.get("selected_live_run")
    if not isinstance(thresholds, Mapping):
        raise ValueError("冻结矩阵缺少 acceptance_thresholds")
    if not isinstance(historical, list) or not isinstance(selected_live, Mapping):
        raise ValueError("冻结矩阵缺少代表运行")
    if not live_receipt.get("success"):
        raise ValueError("实机长窗口未成功，不能物化通过账本")

    expected_duration = float(selected_live["minimum_duration_seconds"])
    actual_duration = float(live_receipt.get("continuous_connected_duration_seconds", 0))
    if actual_duration < expected_duration:
        raise ValueError(
            f"连续时长不足: expected>={expected_duration}, actual={actual_duration}"
        )
    resource = live_receipt.get("resource_boundary")
    expected_fights = int(selected_live["normal_victories"])
    if not isinstance(resource, Mapping):
        raise ValueError("实机回执缺少 resource_boundary")
    zero_resource_fields = ("medicine", "expiring_medicine", "stone", "premium_currency")
    if any(resource.get(field) != 0 for field in zero_resource_fields):
        raise ValueError("实机回执越过零补充资源边界")
    if resource.get("fight_task_submissions") != 1:
        raise ValueError("消耗型 Fight 必须恰好提交一次")
    if resource.get("fight_times") != expected_fights:
        raise ValueError("Fight 次数与冻结矩阵不符")

    stage = str(matrix.get("frozen_environment", {}).get("stage", ""))
    victories = live_receipt.get("normal_victories")
    if not isinstance(victories, list) or len(victories) != expected_fights:
        raise ValueError("实机三星胜利数量与冻结矩阵不符")
    if any(item.get("stage") != stage or item.get("stars") != 3 for item in victories):
        raise ValueError("实机回执含错关或非三星胜利")
    navigation = live_receipt.get("navigation_chain")
    if not isinstance(navigation, Mapping) or not navigation.get("verified"):
        raise ValueError("实机跨页导航未独立验证")
    if not live_receipt.get("equivalent_long_segment"):
        raise ValueError("实机回执未通过等价长段")

    runs: list[FrozenRunEvidenceV1] = []
    for item in historical:
        if not isinstance(item, Mapping):
            raise ValueError("selected_historical_runs 项必须是对象")
        runs.append(
            FrozenRunEvidenceV1(
                run_id=str(item["run_id"]),
                duration_seconds=float(item["duration_seconds"]),
                verdict=RunVerdict(str(item["verdict"])),
                evidence_refs=(str(item["evidence"]),),
                normal_victories=int(item.get("normal_victories", 0)),
                navigation_chains=int(item.get("navigation_chains", 0)),
                equivalent_long_segments=int(item.get("equivalent_long_segments", 0)),
                recovery_drills=int(item.get("recovery_drills", 0)),
            )
        )
    runs.append(
        FrozenRunEvidenceV1(
            run_id=str(selected_live["run_id"]),
            duration_seconds=actual_duration,
            verdict=RunVerdict.PASS,
            evidence_refs=(str(selected_live["evidence"]),),
            normal_victories=len(victories),
            navigation_chains=1,
            equivalent_long_segments=1,
        )
    )
    return build_acceptance_ledger(
        runs,
        minimum_total_seconds=float(thresholds["minimum_total_runtime_seconds"]),
        minimum_continuous_seconds=float(
            thresholds["minimum_continuous_runtime_seconds"]
        ),
        minimum_normal_victories=int(thresholds["minimum_normal_stage_victories"]),
        minimum_navigation_chains=int(thresholds["minimum_navigation_chains"]),
        minimum_equivalent_long_segments=int(
            thresholds["minimum_equivalent_long_segments"]
        ),
        minimum_recovery_drills=int(thresholds["minimum_recovery_drills"]),
    )


def materialize_acceptance_ledger(
    matrix_path: Path | str,
    live_receipt_path: Path | str,
    output_path: Path | str,
) -> AcceptanceLedgerV1:
    """从两个冻结 JSON 真源生成可独立复核的最终账本。"""

    matrix = json.loads(Path(matrix_path).read_text(encoding="utf-8"))
    receipt = json.loads(Path(live_receipt_path).read_text(encoding="utf-8"))
    ledger = build_frozen_matrix_ledger(matrix, receipt)
    _write_json_atomic(
        Path(output_path),
        {
            "schema": "omnicompany.arknights.acceptance-ledger.v1",
            "matrix": Path(matrix_path).resolve().as_posix(),
            "live_receipt": Path(live_receipt_path).resolve().as_posix(),
            **ledger.as_dict(),
        },
    )
    return ledger


def summarize_fight_victories(
    events: Sequence[MaaEvent], *, stage: str, expected_victories: int
) -> tuple[FightVictoryV1, ...]:
    """严格解析三星结算事件；数量、关卡或星数不符均闭门失败。"""

    victories: list[FightVictoryV1] = []
    for event in events:
        if event.what != "StageDrops":
            continue
        details = event.details.get("details")
        if not isinstance(details, dict):
            raise ValueError("StageDrops.details 缺失或不是对象")
        stage_details = details.get("stage")
        code = stage_details.get("stageCode") if isinstance(stage_details, dict) else None
        stars = details.get("stars")
        if code != stage:
            raise ValueError(f"结算关卡不符: expected={stage!r}, actual={code!r}")
        if stars != 3:
            raise ValueError(f"{stage} 不是三星结算: stars={stars!r}")
        raw_drops = details.get("drops", [])
        if not isinstance(raw_drops, list):
            raise ValueError("StageDrops.drops 不是数组")
        victories.append(FightVictoryV1(stage, stars, tuple(raw_drops)))
    if len(victories) != expected_victories:
        raise ValueError(
            f"三星结算次数不符: expected={expected_victories}, actual={len(victories)}"
        )
    return tuple(victories)


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    temporary.replace(path)


def _event_dict(event: MaaEvent, connected_at: float) -> dict[str, Any]:
    return {
        "t": round(event.monotonic - connected_at, 3),
        "message": event.message.name,
        "what": event.what,
        "taskchain": event.taskchain,
        "details": event.details,
    }


def _capture_checkpoint(
    op: MaaOperator, output_dir: Path, label: str
) -> dict[str, Any]:
    png = op.capture_png(fresh=True)
    if not png:
        raise RuntimeError(f"无法获取新鲜帧: {label}")
    digest = hashlib.sha256(png).hexdigest()
    frame_dir = output_dir / "frames"
    frame_dir.mkdir(parents=True, exist_ok=True)
    frame_path = frame_dir / f"{label}-{digest[:12]}.png"
    frame_path.write_bytes(png)
    return {
        "label": label,
        "frame": frame_path.resolve().as_posix(),
        "sha256": digest,
        "captured_at": _iso_now(),
    }


def _probe(
    ops: ArknightsOperations,
    task: str,
    log: list[dict[str, Any]],
    *,
    timeout: float,
) -> bool:
    started = time.monotonic()
    try:
        ops.run_maa_tasks([task], timeout=timeout)
    except Exception as exc:  # noqa: BLE001 - live receipt must retain the exact miss
        log.append(
            {
                "task": task,
                "matched": False,
                "duration_seconds": round(time.monotonic() - started, 3),
                "error": f"{type(exc).__name__}: {exc}",
            }
        )
        return False
    log.append(
        {
            "task": task,
            "matched": True,
            "duration_seconds": round(time.monotonic() - started, 3),
        }
    )
    return True


def _ensure_home(
    ops: ArknightsOperations,
    log: list[dict[str, Any]],
    *,
    allow_recovery: bool,
) -> tuple[bool, bool]:
    """以视觉任务确认主页；恢复动作回调失败后仍以复查画面为准。"""

    if _probe(ops, _HOME, log, timeout=25.0):
        return True, False
    if not allow_recovery:
        return False, False
    _probe(ops, _TO_HOME, log, timeout=60.0)
    if _probe(ops, _HOME, log, timeout=25.0):
        return True, True
    try:
        ops.go_to_main_screen(launch_game=True)
        log.append({"task": "StartUp", "matched": True, "recovery": True})
    except Exception as exc:  # noqa: BLE001 - visual recheck decides the actual outcome
        log.append(
            {
                "task": "StartUp",
                "matched": False,
                "recovery": True,
                "error": f"{type(exc).__name__}: {exc}",
            }
        )
    return _probe(ops, _HOME, log, timeout=25.0), True


def _run_resource_safe_fights(
    ops: ArknightsOperations, *, stage: str, times: int, timeout: float
) -> Sequence[MaaEvent]:
    """唯一的消耗型动作；资源边界在设施内写死，调用方不能覆盖。"""

    return ops.fight(
        stage,
        times=times,
        medicine=0,
        stone=0,
        timeout=timeout,
    )


def run_live_stability_window(
    adb_path: Path | str,
    address: str,
    output_dir: Path | str,
    *,
    stage: str = "1-7",
    fight_times: int = 3,
    minimum_duration_seconds: float = 2160.0,
    heartbeat_interval_seconds: float = 60.0,
    fight_timeout_seconds: float = 1200.0,
    operator_factory: Callable[[], MaaOperator] = MaaOperator.create,
    sleep: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    """在一个连接中运行代表性长链，并持续落盘进度与最终回执。"""

    if fight_times <= 0:
        raise ValueError("fight_times 必须为正数")
    if minimum_duration_seconds <= 0 or heartbeat_interval_seconds <= 0:
        raise ValueError("运行时长与心跳间隔必须为正数")

    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    progress_path = out / "progress.json"
    receipt_path = out / "receipt.json"
    process_started_at = _iso_now()
    op: MaaOperator | None = None
    connected_at = 0.0
    connected_at_iso: str | None = None
    checkpoints: list[dict[str, Any]] = []
    probes: list[dict[str, Any]] = []
    heartbeat_log: list[dict[str, Any]] = []
    recoveries = 0
    fight_events: list[MaaEvent] = []
    victories: tuple[FightVictoryV1, ...] = ()
    phase = "initializing"
    error: str | None = None
    maa_version: str | None = None

    def elapsed() -> float:
        return time.monotonic() - connected_at if connected_at else 0.0

    def progress() -> None:
        _write_json_atomic(
            progress_path,
            {
                "schema": "omnicompany.arknights.stability-progress.v1",
                "phase": phase,
                "process_started_at": process_started_at,
                "connected_elapsed_seconds": round(elapsed(), 3),
                "minimum_duration_seconds": minimum_duration_seconds,
                "fight_times_requested": fight_times,
                "victories_verified": len(victories),
                "heartbeats_passed": sum(item["matched"] for item in heartbeat_log),
                "latest_heartbeat": heartbeat_log[-1] if heartbeat_log else None,
                "error": error,
            },
        )

    progress()
    try:
        op = operator_factory()
        op.set_touch_mode(TouchMode.MAATOUCH)
        if not op.connect(adb_path, address):
            raise RuntimeError(f"无法连接设备: {address}")
        connected_at = time.monotonic()
        connected_at_iso = _iso_now()
        maa_version = op.version()
        ops = ArknightsOperations(op)

        phase = "precondition-home"
        progress()
        home, recovered = _ensure_home(ops, probes, allow_recovery=True)
        recoveries += int(recovered)
        if not home:
            raise RuntimeError("战斗前无法独立确认主页")
        checkpoints.append(_capture_checkpoint(op, out, "home-before"))

        phase = "fight-once"
        progress()
        event_start = len(op.events)
        _run_resource_safe_fights(
            ops,
            stage=stage,
            times=fight_times,
            timeout=fight_timeout_seconds,
        )
        fight_events = op.events[event_start:]
        victories = summarize_fight_victories(
            fight_events, stage=stage, expected_victories=fight_times
        )
        checkpoints.append(_capture_checkpoint(op, out, "after-fights"))

        phase = "recover-home-after-fight"
        progress()
        home, recovered = _ensure_home(ops, probes, allow_recovery=True)
        recoveries += int(recovered)
        if not home:
            raise RuntimeError("战斗后无法恢复并确认主页")
        checkpoints.append(_capture_checkpoint(op, out, "home-after-fights"))

        phase = "cross-page-navigation"
        progress()
        if not _probe(ops, _OPEN_TERMINAL, probes, timeout=30.0):
            raise RuntimeError("主页到终端的语义跳转失败")
        if not _probe(ops, _TERMINAL, probes, timeout=25.0):
            raise RuntimeError("无法独立确认终端总览")
        checkpoints.append(_capture_checkpoint(op, out, "terminal"))
        home, recovered = _ensure_home(ops, probes, allow_recovery=True)
        recoveries += int(recovered)
        if not home:
            raise RuntimeError("终端导航后无法恢复并确认主页")
        checkpoints.append(_capture_checkpoint(op, out, "home-after-navigation"))

        phase = "home-visual-heartbeat"
        progress()
        heartbeat_index = 0
        while elapsed() < minimum_duration_seconds:
            remaining = minimum_duration_seconds - elapsed()
            sleep(min(heartbeat_interval_seconds, max(0.0, remaining)))
            heartbeat_index += 1
            connected = op.is_connected_now()
            matched, recovered = _ensure_home(
                ops, probes, allow_recovery=True
            )
            recoveries += int(recovered)
            heartbeat = {
                "index": heartbeat_index,
                "at": _iso_now(),
                "elapsed_seconds": round(elapsed(), 3),
                "connected": connected,
                "matched": matched,
                "recovered": recovered,
            }
            heartbeat_log.append(heartbeat)
            if not connected:
                raise RuntimeError("长窗口内 MaaCore 连接断开")
            if not matched:
                raise RuntimeError("长窗口主页视觉心跳未恢复")
            progress()

        phase = "final-check"
        progress()
        if not op.is_connected_now():
            raise RuntimeError("最终检查时 MaaCore 连接已断开")
        home, recovered = _ensure_home(ops, probes, allow_recovery=False)
        recoveries += int(recovered)
        if not home:
            raise RuntimeError("最终画面不是主页")
        checkpoints.append(_capture_checkpoint(op, out, "home-final"))
        phase = "complete"
    except Exception as exc:  # noqa: BLE001 - failure receipt is part of the contract
        error = f"{type(exc).__name__}: {exc}"
        phase = "failed"
        if op is not None and op.connected:
            try:
                checkpoints.append(_capture_checkpoint(op, out, "failure-final"))
            except Exception as capture_exc:  # noqa: BLE001
                probes.append(
                    {
                        "task": "failure-final-capture",
                        "matched": False,
                        "error": f"{type(capture_exc).__name__}: {capture_exc}",
                    }
                )
    finally:
        finished_at = _iso_now()
        duration = elapsed()
        try:
            connected_final = bool(op and op.connected and op.is_connected_now())
        except Exception:  # noqa: BLE001 - receipt must survive a broken final query
            connected_final = False
        if op is not None:
            op.stop()
        success = (
            error is None
            and phase == "complete"
            and len(victories) == fight_times
            and duration >= minimum_duration_seconds
            and connected_final
        )
        receipt = {
            "schema": "omnicompany.arknights.stability-run.v1",
            "success": success,
            "phase": phase,
            "stage": stage,
            "process_started_at": process_started_at,
            "connected_at": connected_at_iso,
            "finished_at": finished_at,
            "continuous_connected_duration_seconds": round(duration, 3),
            "minimum_duration_seconds": minimum_duration_seconds,
            "maa_version": maa_version,
            "device": {"adb_path": str(adb_path), "address": address},
            "resource_boundary": {
                "fight_task_submissions": 1,
                "fight_times": fight_times,
                "medicine": 0,
                "expiring_medicine": 0,
                "stone": 0,
                "premium_currency": 0,
            },
            "normal_victories": [asdict(item) for item in victories],
            "navigation_chain": {
                "from": "HOME",
                "via": "TERMINAL",
                "to": "HOME",
                "verified": any(
                    item["task"] == _TERMINAL and item["matched"] for item in probes
                ),
            },
            "equivalent_long_segment": success,
            "recoveries_observed": recoveries,
            "heartbeats": heartbeat_log,
            "checkpoints": checkpoints,
            "probes": probes,
            "fight_events": [
                _event_dict(event, connected_at) for event in fight_events
            ],
            "connected_final_before_stop": connected_final,
            "error": error,
        }
        _write_json_atomic(receipt_path, receipt)
        progress()
    return receipt


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--adb", required=True, type=Path)
    parser.add_argument("--address", required=True)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--stage", default="1-7")
    parser.add_argument("--fight-times", type=int, default=3)
    parser.add_argument("--min-duration", type=float, default=2160.0)
    parser.add_argument("--heartbeat-interval", type=float, default=60.0)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    receipt = run_live_stability_window(
        args.adb,
        args.address,
        args.output,
        stage=args.stage,
        fight_times=args.fight_times,
        minimum_duration_seconds=args.min_duration,
        heartbeat_interval_seconds=args.heartbeat_interval,
    )
    print(json.dumps(receipt, ensure_ascii=False, indent=2), flush=True)
    return 0 if receipt["success"] else 2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
