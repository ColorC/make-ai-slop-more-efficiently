"""N1 投影残差实机运行器：每场战斗只冻结一个语义部署锚点。

运行器严格区分三件事：

* MAA 只接收 ``(关卡, 干员, 格子, 朝向)`` 语义动作；
* 观测器在整个战场 ROI 内独立寻找动作后持续出现的新物体；
* 只有观测完成后，才将像素位置与 Tile-Pos 格心做残差比较。

每个锚点使用一场新战斗，证据目录保存前后新鲜帧、动作期间帧、MAA 事件和完整 JSON
判定。测不到的锚点保留为 ``observed=null``，不会被静默剔除。
"""

from __future__ import annotations

import argparse
import io
import json
import os
import threading
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Sequence

import numpy as np
from PIL import Image

from .maa_operator import MaaEvent, MaaOperator, TouchMode
from .operations import ActionType, ArknightsOperations, Direction, MaaTask
from .projection_check import (
    DeployAnchor,
    frames_share_display_state,
    measure_residuals,
    persistent_change_centroid,
    roi_luminance,
)
from .tile_grid import TileProjector, load_stage_grid

__all__ = [
    "BATTLE_ROI",
    "FrozenAnchor",
    "Observation",
    "observe_deployment",
    "parse_anchor",
    "run_measurements",
]

BATTLE_ROI = (0, 95, 1280, 470)


@dataclass(frozen=True, slots=True)
class FrozenAnchor:
    """运行前冻结的一个部署命令。"""

    name: str
    col: int
    row: int
    direction: Direction = Direction.RIGHT


@dataclass(frozen=True, slots=True)
class Observation:
    """三帧观测的独立有效性判定。"""

    anchor: DeployAnchor
    same_display_state: bool
    luminance: tuple[float, float, float]

    @property
    def valid(self) -> bool:
        return self.same_display_state and self.anchor.residual is not None

    def as_dict(self) -> dict[str, Any]:
        return {
            "same_display_state": self.same_display_state,
            "luminance": [round(value, 2) for value in self.luminance],
            "valid": self.valid,
            "anchor": self.anchor.as_dict(),
        }


def parse_anchor(value: str) -> FrozenAnchor:
    """解析 ``干员:列:行[:朝向]``。"""
    parts = value.split(":")
    if len(parts) not in {3, 4} or not parts[0]:
        raise argparse.ArgumentTypeError("锚点格式必须为 干员:列:行[:朝向]")
    try:
        col, row = int(parts[1]), int(parts[2])
        direction = Direction(parts[3]) if len(parts) == 4 else Direction.RIGHT
    except (ValueError, KeyError) as exc:
        choices = ", ".join(item.value for item in Direction)
        raise argparse.ArgumentTypeError(f"锚点坐标或朝向无效；朝向可选 {choices}") from exc
    if col < 0 or row < 0:
        raise argparse.ArgumentTypeError("格子坐标不能为负数")
    return FrozenAnchor(parts[0], col, row, direction)


def _decode_png(png: bytes) -> np.ndarray:
    with Image.open(io.BytesIO(png)) as image:
        return np.asarray(image.convert("RGB"))


def observe_deployment(
    spec: FrozenAnchor,
    expected: tuple[float, float],
    before: object,
    after_a: object,
    after_b: object,
    *,
    roi: tuple[int, int, int, int] = BATTLE_ROI,
) -> Observation:
    """由三帧产生一个部署观测；期望坐标不参与像素目标搜索。"""
    frames = (before, after_a, after_b)
    same_state = frames_share_display_state(frames, roi=roi)
    luminance = tuple(roi_luminance(frame, roi) for frame in frames)
    if same_state:
        cx, cy, changed = persistent_change_centroid(
            before, after_a, after_b, roi=roi
        )
    else:
        cx, cy, changed = None, None, 0
    anchor = DeployAnchor(
        name=spec.name,
        col=spec.col,
        row=spec.row,
        expected_x=expected[0],
        expected_y=expected[1],
        observed_x=cx,
        observed_y=cy,
        changed_pixels=changed,
    )
    return Observation(anchor, same_state, luminance)


class _RunLog:
    def __init__(self) -> None:
        self.started = time.monotonic()
        self.lines: list[str] = []

    def add(self, message: str) -> None:
        elapsed = time.monotonic() - self.started
        line = f"[{elapsed:8.2f}s] {message}"
        self.lines.append(line)
        print(line, flush=True)


def _event_dict(event: MaaEvent, started: float) -> dict[str, Any]:
    return {
        "t": round(event.monotonic - started, 3),
        "message": event.message.name,
        "what": event.what,
        "taskchain": event.taskchain,
        "details": event.details,
    }


def _hit(
    ops: ArknightsOperations,
    task: str,
    log: _RunLog,
    *,
    timeout: float = 20.0,
) -> bool:
    try:
        ops.run_maa_tasks([task], timeout=timeout)
    except Exception as exc:
        log.add(f"anchor miss {task}: {type(exc).__name__}")
        return False
    log.add(f"anchor hit {task}")
    return True


def _open_stage_from_chapter(
    ops: ArknightsOperations,
    stage: str,
    log: _RunLog,
) -> bool:
    """若当前在章节地图，从语义节点进入目标详情页。"""
    if not _hit(ops, "Omni@ChapterScreen-Detect", log, timeout=16.0):
        return False
    task_by_stage = {"1-7": "Omni@Chapter-GoStage-1-7"}
    task = task_by_stage.get(stage)
    if task is None:
        raise RuntimeError(f"章节地图尚无 {stage} 的语义入口任务")
    if not _hit(ops, task, log, timeout=20.0):
        raise RuntimeError(f"章节地图无法打开 {stage}")
    if not _confirm_stage_detail(ops, stage, log):
        raise RuntimeError(f"点击 {stage} 后无法确认关卡详情")
    return True


def _confirm_stage_detail(
    ops: ArknightsOperations,
    stage: str,
    log: _RunLog,
) -> bool:
    if not _hit(ops, "Omni@StageDetail-Detect", log, timeout=20.0):
        return False
    task_by_stage = {"1-7": "Omni@StageDetail-1-7-Detect"}
    task = task_by_stage.get(stage)
    if task is None:
        raise RuntimeError(f"尚无 {stage} 的关卡代码确认任务")
    return _hit(ops, task, log, timeout=16.0)


def _open_last_stage_from_terminal(
    ops: ArknightsOperations,
    stage: str,
    log: _RunLog,
) -> bool:
    if not _hit(ops, "Omni@TerminalHub-Detect", log, timeout=16.0):
        return False
    if not _hit(ops, "Omni@Home-GoLastStage", log, timeout=20.0):
        raise RuntimeError("终端总览无法打开上次作战")
    if not _confirm_stage_detail(ops, stage, log):
        raise RuntimeError(f"上次作战卡片没有进入 {stage}")
    return True


def _finish_current_battle(
    ops: ArknightsOperations,
    log: _RunLog,
    *,
    paused: bool,
) -> None:
    """把当前战斗推进到终态，并关闭胜利／失败结果页。"""
    if paused:
        try:
            ops.resume_battle()
            log.add("恢复暂停战斗")
        except Exception as exc:
            log.add(f"恢复暂停未完成: {type(exc).__name__}: {exc}")
    try:
        ops.speed_up_battle()
        log.add("战斗切换速度一次以加快收尾")
    except Exception as exc:
        log.add(f"切换战斗速度未完成: {type(exc).__name__}: {exc}")
    try:
        ops.idle_until_end()
        log.add("SkillDaemon 已推进到战斗终态")
    except Exception as exc:
        log.add(f"SkillDaemon 收尾未完成: {type(exc).__name__}: {exc}")

    if _hit(ops, "Omni@Settlement-Detect", log, timeout=18.0):
        _hit(ops, "Omni@Settlement-Dismiss", log, timeout=18.0)
        return
    # 仅在胜利结算未命中后尝试 MAA 自带失败页任务；它由「任务失败」OCR 定位并点击。
    _hit(ops, "FightMissionFailed", log, timeout=30.0)


def _prepare_fresh_battle(
    ops: ArknightsOperations,
    stage: str,
    log: _RunLog,
) -> None:
    """经主界面最后关卡入口开始一场新战斗，并独立核验关键状态。"""
    # SingleStep 的 deployment_with_pause 流程可能在异常收尾时留下暂停战斗。先处理这个
    # 最具体状态，避免让 StartUp 在战斗里空等 180 秒。
    if _hit(ops, "Omni@Battle-Paused", log, timeout=8.0):
        _finish_current_battle(ops, log, paused=True)

    try:
        ops.go_to_main_screen(launch_game=True)
        log.add("StartUp 已返回")
    except Exception as exc:
        log.add(f"StartUp 未完成: {type(exc).__name__}: {exc}")

    # Formation/StageDetail 比 Home 更具体，必须优先。实机曾在编队页误命中 Home OCR；
    # 若先信宽泛锚点，就会把“开始行动”页当主页并下发错误导航。
    at_formation = _hit(ops, "Omni@Formation-Detect", log, timeout=14.0)
    at_stage_detail = False
    if not at_formation:
        at_stage_detail = _confirm_stage_detail(ops, stage, log)
    ready_to_start = at_formation or at_stage_detail
    if not ready_to_start:
        at_stage_detail = _open_stage_from_chapter(ops, stage, log)
        ready_to_start = at_stage_detail
    if not ready_to_start:
        at_stage_detail = _open_last_stage_from_terminal(ops, stage, log)
        ready_to_start = at_stage_detail
    at_home = False
    if not ready_to_start:
        at_home = _hit(ops, "Omni@Home-Detect", log, timeout=25.0)

    if not at_home and not ready_to_start:
        # StartUp 在战斗内可能报告完成但画面不变。只探测高概率状态，避免把一整套慢 OCR
        # 扫描塞进短关卡。
        in_battle = _hit(ops, "Omni@Battle-Paused", log, timeout=14.0)
        in_battle = in_battle or _hit(
            ops, "Omni@Battle-Detect", log, timeout=14.0
        )
        if in_battle:
            _finish_current_battle(ops, log, paused=False)
        at_settlement = _hit(ops, "Omni@Settlement-Detect", log, timeout=14.0)
        dismissed = at_settlement and _hit(
            ops, "Omni@Settlement-Dismiss", log, timeout=14.0
        )
        if in_battle or dismissed:
            at_formation = _hit(
                ops, "Omni@Formation-Detect", log, timeout=18.0
            )
            at_stage_detail = not at_formation and _confirm_stage_detail(
                ops, stage, log
            )
            ready_to_start = at_formation or at_stage_detail
        if not ready_to_start:
            ops.go_to_main_screen(launch_game=True)
            # StartUp 的界面迁移可能晚于任务链失败回调；先复查具体入口，再看宽泛主页。
            at_formation = _hit(
                ops, "Omni@Formation-Detect", log, timeout=18.0
            )
            at_stage_detail = not at_formation and _confirm_stage_detail(
                ops, stage, log
            )
            ready_to_start = at_formation or at_stage_detail
            if not ready_to_start:
                at_stage_detail = _open_stage_from_chapter(ops, stage, log)
                ready_to_start = at_stage_detail
            if not ready_to_start:
                at_stage_detail = _open_last_stage_from_terminal(
                    ops, stage, log
                )
                ready_to_start = at_stage_detail
            if not ready_to_start:
                at_home = _hit(ops, "Omni@Home-Detect", log, timeout=25.0)
            if not at_home and not ready_to_start:
                raise RuntimeError("无法独立确认主页、关卡详情或编队界面")

    if at_home:
        used_shortcut = _hit(
            ops, "Omni@Home-GoLastStage", log, timeout=20.0
        )
        if used_shortcut:
            if not _confirm_stage_detail(ops, stage, log):
                raise RuntimeError("上次作战快捷入口未落到关卡详情")
        else:
            if not _hit(ops, "Omni@Home-OpenTerminal", log, timeout=20.0):
                raise RuntimeError("主页无法打开终端")
            opened = _open_last_stage_from_terminal(ops, stage, log)
            opened = opened or _open_stage_from_chapter(ops, stage, log)
            if not opened:
                raise RuntimeError("打开终端后未进入总览或章节地图")
        at_stage_detail = True

    if at_formation:
        ops.go_back()
        log.add("从编队页返回关卡详情，以便关闭代理")
        if not _confirm_stage_detail(ops, stage, log):
            raise RuntimeError(f"从编队页返回后无法确认 {stage} 详情")
        at_stage_detail = True

    if at_stage_detail and _hit(
        ops, "Omni@StageDetail-ProxyEnabled", log, timeout=12.0
    ):
        if not _hit(ops, "Omni@StageDetail-DisableProxy", log, timeout=15.0):
            raise RuntimeError("代理开关已启用但无法关闭")
        if _hit(ops, "Omni@StageDetail-ProxyEnabled", log, timeout=12.0):
            raise RuntimeError("点击后代理开关仍处于启用状态")
        log.add("已独立确认代理指挥关闭")

    for start_attempt in range(1, 3):
        ops.battle_begin()
        log.add(f"BattleStartAll 第 {start_attempt} 次已返回")
        if _hit(ops, "Omni@Battle-Detect", log, timeout=25.0):
            break
        if start_attempt == 1 and _hit(
            ops, "Omni@Formation-Detect", log, timeout=18.0
        ):
            log.add("已确认编队页，继续执行开始行动")
            continue
        raise RuntimeError("无法确认编队页或战斗界面")

    # 已通关关卡会默认开代理；SingleStep 与代理并行会互相争夺触控权。
    if _hit(ops, "Omni@Battle-ProxyRunning", log, timeout=10.0):
        if not _hit(ops, "Omni@Battle-TakeOver", log, timeout=20.0):
            raise RuntimeError("代理运行中但接管按钮未命中")
        if _hit(ops, "Omni@Battle-TakeOverConfirm-Detect", log, timeout=14.0):
            if not _hit(
                ops, "Omni@Battle-TakeOverConfirm", log, timeout=14.0
            ):
                raise RuntimeError("接管确认弹窗无法关闭")
    if not _hit(ops, "Omni@Battle-Detect", log, timeout=20.0):
        raise RuntimeError("接管后无法确认战斗仍在进行")


def _capture_fresh(op: MaaOperator) -> bytes:
    png = op.capture_png(fresh=True)
    if not png:
        raise RuntimeError("MAA 未返回新鲜 PNG 帧")
    return png


def _copilot_action_matches(event: MaaEvent, spec: FrozenAnchor) -> bool:
    if event.what != "CopilotAction":
        return False
    details = event.details.get("details", {})
    if not isinstance(details, dict):
        return False
    return details.get("action") == "Deploy" and details.get("target") == spec.name


def _measure_one(
    op: MaaOperator,
    ops: ArknightsOperations,
    projector: TileProjector,
    stage: str,
    spec: FrozenAnchor,
    sample_dir: Path,
    log: _RunLog,
    *,
    post_gap: float,
) -> dict[str, Any]:
    _prepare_fresh_battle(ops, stage, log)
    ops.battle_bind_stage(stage)
    log.add(f"绑定关卡 {stage}")

    before_png = _capture_fresh(op)
    before = _decode_png(before_png)
    during: list[tuple[float, bytes]] = []
    stop = threading.Event()
    action_started = time.monotonic()

    def collect() -> None:
        previous: bytes | None = None
        while not stop.is_set():
            try:
                png = op.capture_png()
                if png and png != previous:
                    during.append((time.monotonic(), png))
                    previous = png
            except Exception:
                pass
            stop.wait(0.05)

    thread = threading.Thread(target=collect, name="projection-frame-capture", daemon=True)
    thread.start()
    seen = len(op.events)
    action = ArknightsOperations.make_action(
        ActionType.DEPLOY,
        name=spec.name,
        col=spec.col,
        row=spec.row,
        direction=spec.direction,
        doc=f"N1 anchor {spec.col},{spec.row}",
    )
    try:
        events = op.run(
            [
                (
                    MaaTask.SINGLE_STEP,
                    {
                        "enable": True,
                        "type": "copilot",
                        "subtype": "action",
                        "details": {"actions": [action]},
                    },
                )
            ],
            timeout=180.0,
        )[seen:]
    finally:
        stop.set()
        thread.join(timeout=5.0)
    log.add(f"单动作返回：{spec.name}@({spec.col},{spec.row})，期间帧 {len(during)}")

    after_a_png = _capture_fresh(op)
    time.sleep(post_gap)
    after_b_png = _capture_fresh(op)
    after_a, after_b = _decode_png(after_a_png), _decode_png(after_b_png)
    expected = projector.tile_center(spec.col, spec.row)
    observation = observe_deployment(spec, expected, before, after_a, after_b)
    action_seen = any(_copilot_action_matches(event, spec) for event in events)

    sample_dir.mkdir(parents=True, exist_ok=True)
    (sample_dir / "before.png").write_bytes(before_png)
    (sample_dir / "after-a.png").write_bytes(after_a_png)
    (sample_dir / "after-b.png").write_bytes(after_b_png)
    during_dir = sample_dir / "during"
    during_dir.mkdir(exist_ok=True)
    for index, (_, png) in enumerate(during, start=1):
        (during_dir / f"frame-{index:04d}.png").write_bytes(png)

    event_rows = [_event_dict(event, action_started) for event in events]
    (sample_dir / "events.json").write_text(
        json.dumps(event_rows, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    result = {
        "spec": {
            **asdict(spec),
            "direction": spec.direction.value,
        },
        "copilot_action_seen": action_seen,
        "during_frame_count": len(during),
        "observation": observation.as_dict(),
    }
    (sample_dir / "result.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    residual = observation.anchor.residual
    log.add(
        "观测结果："
        f"event={action_seen} same_state={observation.same_display_state} "
        f"residual={'null' if residual is None else f'{residual:.1f}px'}"
    )
    return {**result, "_anchor": observation.anchor}


def run_measurements(
    *,
    adb_path: Path,
    address: str,
    stage: str,
    anchors: Sequence[FrozenAnchor],
    output_dir: Path,
    maa_root: Path | None = None,
    post_gap: float = 0.35,
) -> dict[str, Any]:
    """执行预先冻结的锚点集合并返回聚合证据。"""
    if not anchors:
        raise ValueError("至少需要一个冻结锚点")
    output_dir.mkdir(parents=True, exist_ok=True)
    log = _RunLog()
    op = MaaOperator.create(root=maa_root)
    op.set_touch_mode(TouchMode.MAATOUCH)
    op.set_deployment_with_pause(True)
    if not op.connect(adb_path, address, "General"):
        raise RuntimeError(f"无法连接设备 {address}")
    log.add(f"MaaCore {op.version()}，设备 {address}")

    grid = load_stage_grid(op.paths.tile_pos_dir, code=stage)
    projector = TileProjector(1280, 720, grid)
    tolerance = projector.min_center_spacing() / 2
    samples: list[dict[str, Any]] = []
    observed: list[DeployAnchor] = []
    for index, spec in enumerate(anchors, start=1):
        sample_dir = output_dir / f"sample-{index:02d}-{spec.col}-{spec.row}"
        try:
            try:
                sample = _measure_one(
                    op,
                    ArknightsOperations(op),
                    projector,
                    stage,
                    spec,
                    sample_dir,
                    log,
                    post_gap=post_gap,
                )
                anchor = sample.pop("_anchor")
                samples.append(sample)
                observed.append(anchor)
            except Exception as exc:
                sample_dir.mkdir(parents=True, exist_ok=True)
                failure: dict[str, Any] = {
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                    "log": log.lines,
                }
                try:
                    (sample_dir / "failure.png").write_bytes(_capture_fresh(op))
                    failure["failure_frame"] = "failure.png"
                except Exception as frame_exc:
                    failure["frame_error"] = (
                        f"{type(frame_exc).__name__}: {frame_exc}"
                    )
                (sample_dir / "failure.json").write_text(
                    json.dumps(failure, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
                raise
        finally:
            cleanup_ops = ArknightsOperations(op)
            paused = _hit(
                cleanup_ops, "Omni@Battle-Paused", log, timeout=8.0
            )
            active = paused or _hit(
                cleanup_ops, "Omni@Battle-Detect", log, timeout=8.0
            )
            if active:
                _finish_current_battle(cleanup_ops, log, paused=paused)
            else:
                log.add("清理阶段未检测到活动战斗")

    report = measure_residuals(observed, tolerance)
    result = {
        "schema": "omnicompany.arknights.projection-residual.v1",
        "stage": stage,
        "device": address,
        "maa_version": op.version(),
        "tolerance_basis": "half_minimum_tile_center_spacing",
        "minimum_tile_center_spacing_px": round(tolerance * 2, 2),
        "samples": samples,
        "report": report.as_dict(),
        "log": log.lines,
    }
    (output_dir / "projection-residual.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (output_dir / "run.log").write_text("\n".join(log.lines), encoding="utf-8")
    return result


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--adb", type=Path, default=os.environ.get("ARKNIGHTS_ADB"))
    parser.add_argument("--address", default="emulator-5554")
    parser.add_argument("--stage", default="1-7")
    parser.add_argument(
        "--anchor",
        type=parse_anchor,
        action="append",
        required=True,
        help="预先冻结的部署锚点：干员:列:行[:Right|Left|Up|Down|None]；可重复",
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--maa-root", type=Path)
    parser.add_argument("--post-gap", type=float, default=0.35)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.adb is None:
        raise SystemExit("必须通过 --adb 或 ARKNIGHTS_ADB 指定 adb 可执行文件")
    result = run_measurements(
        adb_path=args.adb,
        address=args.address,
        stage=args.stage,
        anchors=args.anchor,
        output_dir=args.output,
        maa_root=args.maa_root,
        post_gap=args.post_gap,
    )
    print(json.dumps(result["report"], ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
