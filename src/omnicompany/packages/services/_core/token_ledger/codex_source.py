# [OMNI] origin=claude-code ts=2026-07-03T00:00:00Z type=module summary="token_ledger codex 会话收集器: 扫 ~/.codex/sessions/<年>/<月>/<日>/rollout-*.jsonl, token_count 事件按 total_token_usage 推进增量求和(回显停滞增量0跳过/基线重置开新段/单轮增量超阈值判脏轮次跳过并计数)" why="overnight-run.md 第六节'首轮验收打回后的硬化'㈠: 逐轮 last_token_usage 相加口径与真实数据不符(重复回显导致 6802 条样本 running_last_sum=2×total, 总量高估1.50倍, 独立复算 250亿 vs 真实167亿), 改口径为 total 推进增量求和" tags=token-ledger,codex,usage,total-advance
"""codex 会话用量收集器。

目录形状(侦察已核实): ~/.codex/sessions/<年>/<月>/<日>/rollout-<时间戳>-<uuid>.jsonl

用量事件形状: `{"type": "event_msg", "payload": {"type": "token_count", "info": {
    "total_token_usage": {...累计...}, "last_token_usage": {...本轮...},
}}}`。

聚合口径(首轮验收打回后硬化, 2026-07-03) = **total_token_usage 推进增量求和**,
不再用 last_token_usage 逐轮相加(真实数据里同一轮会以"total 停滞"的形式重复回显同一条
token_count 事件, 逐轮 last 相加会把回显的量再计一遍, 独立复算证实总 input 被高估 1.50 倍:
收集器口径给出 250 亿, 真实约 167 亿; 6802 条 mismatch 样本无一例外满足
running_last_sum == 2 * total, 就是"同一轮记两次"的实锤)。

新口径三态:
    ① 推进(total 相对本段基线真正变大): 把推进量(本次 total - 基线)计入累加, calls += 1。
    ② 回显/停滞(total 与上一条完全相同): 推进增量为 0, 自然跳过(不计入 calls, 不污染累加)。
    ③ 基线重置(total 相对上一条回落, 如 /compact 压缩上下文): 判定新纪元开始, 用这一条
       的 total 重设基线并从这里起继续求和(不倒扣, 不丢历史已累加的量)。
    ④ 脏轮次(单轮推进增量超过合理上限, 阈值取 5000 万 token/轮——正常一轮几千到十几万
       token, 5000 万已是三个数量级以上的异常, 只有解析错位或数据损坏才会出现): 跳过该轮
       增量(不计入累加, 不更新基线为这条脏值, calls 不 +1), 计入 skipped_dirty_rounds,
       不静默、不污染总账。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ._jsonl_util import parse_jsonl_lines, read_new_lines


def _empty_model_bucket() -> dict[str, Any]:
    return {
        "input_tokens": 0,
        "output_tokens": 0,
        "cache_read_tokens": 0,
        "cache_creation_tokens": 0,
        "reasoning_output_tokens": 0,
    }


@dataclass
class CodexSessionUsage:
    session_id: str
    source_path: str
    cwd: str = ""
    by_model: dict[str, dict[str, Any]] = field(default_factory=dict)
    calls: int = 0
    started: str | None = None
    ended: str | None = None
    provider: str = "codex"


@dataclass
class CodexCollectResult:
    sessions: list[CodexSessionUsage] = field(default_factory=list)
    new_watermark_state: dict[str, Any] = field(default_factory=dict)
    skipped_bad_lines: int = 0
    skipped_dirty_rounds: int = 0


def _iter_rollout_files(root: Path) -> list[Path]:
    if not root.is_dir():
        return []
    return sorted(root.glob("**/rollout-*.jsonl"))


# 单轮推进增量上限(判脏轮次阈值): 正常一轮几千到十几万 token, 5000万已是三个数量级以上
# 的异常, 只可能是解析错位/数据损坏——取值依据见模块 docstring。
_DIRTY_ROUND_TOKEN_THRESHOLD = 50_000_000

_TOTAL_FIELDS = ("input_tokens", "output_tokens", "cached_input_tokens", "reasoning_output_tokens")


def _apply_advance(bucket: dict[str, Any], advance: dict[str, int]) -> None:
    bucket["input_tokens"] += advance.get("input_tokens", 0)
    bucket["output_tokens"] += advance.get("output_tokens", 0)
    bucket["cache_read_tokens"] += advance.get("cached_input_tokens", 0)
    bucket["reasoning_output_tokens"] += advance.get("reasoning_output_tokens", 0)


def _process_records(
    records: list[dict[str, Any]],
    *,
    source_path: str,
    current_model: str | None,
    baseline_total: dict[str, int] | None,
) -> tuple[CodexSessionUsage | None, int, str | None, dict[str, int]]:
    """折叠一批(可能是增量)codex 记录成会话用量增量 + 脏轮次计数 + 结束时的当前 model/基线。

    口径 = total_token_usage 推进增量求和(见模块 docstring):
        - total 相对基线推进(变大): 累加推进量, calls += 1, 基线前移到这条 total。
        - total 与基线完全相同(回显/停滞): 推进增量为 0, 天然跳过, 不计入 calls。
        - total 相对基线回落(基线重置, 如 /compact): 该条 total 本身不算推进量(它是
          新纪元的起点, 不是"从旧基线推进而来"), 直接把基线重设为这条 total, 继续从
          下一条起求和; calls 不为这条 +1(它没有可归因的"推进量", 避免把重置前累积的
          大数值误记成这一轮的用量)。
        - 单轮推进增量的任一字段超过 _DIRTY_ROUND_TOKEN_THRESHOLD: 判脏轮次, 跳过(不
          累加、不更新基线为这条脏值、calls 不 +1), skipped_dirty_rounds += 1。

    current_model / baseline_total: 进入本批次之前已知的"当前模型"/"total 基线"
        (增量扫描时由调用方跨批次通过 watermark_state 传入延续, 让"推进"判断在多次
        collect() 调用之间保持连续, 不会因为水位线切割批次而被误判成"从 0 起跳的推进")。
    """
    session_id: str | None = None
    cwd = ""
    by_model: dict[str, dict[str, Any]] = {}
    calls = 0
    started: str | None = None
    ended: str | None = None
    skipped_dirty_rounds = 0

    baseline: dict[str, int] = dict(baseline_total) if baseline_total else {k: 0 for k in _TOTAL_FIELDS}

    for rec in records:
        rtype = rec.get("type")
        payload = rec.get("payload") or {}
        ts = rec.get("timestamp")
        if ts:
            if started is None:
                started = ts
            ended = ts

        if rtype == "session_meta":
            session_id = payload.get("session_id") or payload.get("id") or session_id
            cwd = payload.get("cwd") or cwd
            continue

        model_field = payload.get("model")
        if isinstance(model_field, str) and model_field:
            current_model = model_field

        if rtype != "event_msg":
            continue
        if payload.get("type") != "token_count":
            continue
        info = payload.get("info") or {}
        total = info.get("total_token_usage")
        if not isinstance(total, dict):
            continue

        total_vals = {k: int(total.get(k) or 0) for k in _TOTAL_FIELDS}

        # 基线重置: 任一字段相对基线回落, 判定"新纪元开始"(如 /compact 压缩上下文)。
        if any(total_vals[k] < baseline[k] for k in _TOTAL_FIELDS):
            baseline = total_vals
            continue

        advance = {k: total_vals[k] - baseline[k] for k in _TOTAL_FIELDS}

        # 停滞/回显: 推进量全为 0, 天然跳过, 不计入 calls, 不动基线。
        if all(v == 0 for v in advance.values()):
            continue

        # 脏轮次: 单轮推进量超过合理上限, 只可能是解析错位/数据损坏, 跳过不污染总账,
        # 也不把基线前移到这条脏 total(避免脏值把后续正常推进也带偏)。
        if any(v > _DIRTY_ROUND_TOKEN_THRESHOLD for v in advance.values()):
            skipped_dirty_rounds += 1
            continue

        baseline = total_vals
        model = current_model or "unknown"
        bucket = by_model.setdefault(model, _empty_model_bucket())
        _apply_advance(bucket, advance)
        calls += 1

    if calls == 0 and session_id is None:
        return None, skipped_dirty_rounds, current_model, baseline

    session = CodexSessionUsage(
        session_id=session_id or "unknown-session",
        source_path=source_path,
        cwd=cwd,
        by_model=by_model,
        calls=calls,
        started=started,
        ended=ended,
    )
    return session, skipped_dirty_rounds, current_model, baseline


def collect_codex_sessions(
    root: Path,
    *,
    watermark_state: dict[str, Any] | None = None,
) -> CodexCollectResult:
    """扫 root(默认应传 ~/.codex/sessions)下 rollout-*.jsonl, 按字节水位线增量聚合用量。

    Args:
        root: sessions 目录(测试传 tmp fixture, 生产传 Path.home()/".codex"/"sessions")。
        watermark_state: 上次 collect 返回的 new_watermark_state(每文件字节偏移 +
            当前模型延续状态 + total 基线延续状态), 省略则从头全量扫描。
    """
    watermark_state = watermark_state or {}
    new_state: dict[str, Any] = dict(watermark_state)
    result = CodexCollectResult(new_watermark_state=new_state)

    for rollout_path in _iter_rollout_files(root):
        key = str(rollout_path)
        prev_offset = int(watermark_state.get(key, 0) or 0)
        model_key = f"{key}::model"
        baseline_key = f"{key}::total_baseline"
        current_model = watermark_state.get(model_key)
        baseline_total = watermark_state.get(baseline_key)

        lines, new_offset, _start = read_new_lines(rollout_path, prev_offset=prev_offset)
        new_state[key] = new_offset
        if not lines:
            continue
        records, skipped = parse_jsonl_lines(lines)
        result.skipped_bad_lines += skipped
        session, skipped_dirty, current_model, baseline_total = _process_records(
            records, source_path=str(rollout_path),
            current_model=current_model, baseline_total=baseline_total,
        )
        new_state[model_key] = current_model
        new_state[baseline_key] = baseline_total
        result.skipped_dirty_rounds += skipped_dirty
        if session is None:
            continue
        result.sessions.append(session)

    return result


__all__ = ["CodexSessionUsage", "CodexCollectResult", "collect_codex_sessions"]
