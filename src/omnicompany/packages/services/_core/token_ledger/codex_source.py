# [OMNI] origin=claude-code ts=2026-07-03T00:00:00Z type=module summary="token_ledger codex 会话收集器: 扫 ~/.codex/sessions/<年>/<月>/<日>/rollout-*.jsonl, token_count 事件按 total_token_usage 推进增量求和(回显停滞增量0跳过/基线重置开新段/单轮增量超阈值判脏轮次跳过并计数)" why="overnight-run.md 第六节'首轮验收打回后的硬化'㈠: 逐轮 last_token_usage 相加口径与真实数据不符(重复回显导致 6802 条样本 running_last_sum=2×total, 总量高估1.50倍, 独立复算 250亿 vs 真实167亿), 改口径为 total 推进增量求和" tags=token-ledger,codex,usage,total-advance
# [OMNI] origin=claude-code ts=2026-09-25T00:00:00Z type=module summary="四点硬化: ①会话id一律取自rollout文件名末尾UUID(不再因增量从中段读起漏掉session_meta而记成unknown-session) ②子代理rollout记自己的thread id, session_meta/token_usage_record声明的不同session_id记parent_session_id(不再并入父会话) ③优先读token_usage_record逐行(thread_id,response_id)去重求和, 按usage字段直接映射, 文件里完全没有token_usage_record才回退旧total advance口径 ④model取同turn的turn_context.model" why="真实数据核实: 同一rollout逐条token_usage_record累加与该行thread_token_usage一致, 而event_msg/token_count的total口径会漏掉压缩(/compact)时的调用(同一文件264.0M vs 266.2M); 子代理rollout的payload.session_id是父会话id, 旧逻辑据此把子代理并入父会话名下" tags=token-ledger,codex,usage,token-usage-record,parent-session,subagent
"""codex 会话用量收集器。

目录形状(侦察已核实): ~/.codex/sessions/<年>/<月>/<日>/rollout-<时间戳>-<uuid>.jsonl,
文件名末尾就是这条 rollout 自己的 thread id(该会话/子代理自己的唯一 id, 不管文件里
session_meta 首行有没有被这次增量读取到都能拿到)。

会话 id 口径(2026-09-25 硬化): 一律取文件名末尾 UUID, session_meta 只作 cwd 等补充信息
来源, 不再是会话 id 的权威(增量扫描从文件中段读起时常常读不到首行 session_meta, 旧逻辑
会把这种会话误记成 "unknown-session")。

子代理归属(2026-09-25 硬化): 子代理 rollout 里 session_meta.payload.session_id(或
token_usage_record.payload.session_id)是父会话的 id, 不是这条 rollout 自己的 id —— 旧逻辑
直接拿它当 session_id, 把所有子代理都并入父会话名下。新逻辑: session_id 永远是这条 rollout
自己的文件名 UUID; 只有当声明的 session_id 与文件名 UUID 不同时, 才记进
CodexSessionUsage.parent_session_id(可选字段, 默认 None)。

用量口径(2026-09-25 硬化, 双源二选一):
    优先源 = `{"type": "token_usage_record", "payload": {"thread_id", "response_id",
        "session_id", "usage": {"input_tokens", "cached_input_tokens",
        "cache_write_input_tokens", "output_tokens", "reasoning_output_tokens"}}}`。
        每条 token_usage_record 就是一次真实 API 调用的用量增量(不是累计), 按
        (thread_id, response_id) 去重后逐条累加, model 取同 turn 的 turn_context.payload.model。
        实测: 逐条累加 == 该行的 thread_token_usage(累计口径), 两者一致; 而旧口径
        event_msg/token_count 的 total 会漏掉压缩(/compact)时发生的调用(同一份文件下
        264.0M vs 266.2M, 差值正好是压缩调用的量), 所以 token_usage_record 存在时优先用它,
        完全不看 event_msg/token_count(避免同一次调用两条口径都算一遍)。
    回退源(旧版 codex, 整份文件完全没有 token_usage_record 时才用) = 原 total_token_usage
        推进增量求和口径(见下, 2026-07-03 硬化):

用量事件形状(回退源): `{"type": "event_msg", "payload": {"type": "token_count", "info": {
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

import re
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
    # 子代理归属(2026-09-25 新增, 默认 None 不破坏现有调用方): 当这条 rollout 的
    # session_meta/token_usage_record 声明的 session_id 与文件名 UUID(这条 rollout 自己的
    # thread id)不同时, 记为父会话 id —— 说明这是一条子代理 rollout, 不应并入父会话名下。
    parent_session_id: str | None = None


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


# rollout 文件名末尾的 UUID(该 rollout 自己的 thread id), 形如
# rollout-2026-09-24T13-03-04-01a0d1cb-b94b-7a63-99d9-c528b708df69.jsonl 里的
# 01a0d1cb-b94b-7a63-99d9-c528b708df69。时间戳段(2026-09-24T13-03-04)本身不满足
# 8-4-4-4-12 的十六进制分段形状, 不会被误匹配。
_ROLLOUT_UUID_RE = re.compile(
    r"([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})"
)


def _session_id_from_filename(source_path: str) -> str | None:
    """从 rollout 文件名提取该 rollout 自己的 thread id, 不依赖文件内容。

    增量扫描从文件中段读起时(水位线跳过了首行 session_meta)内容里可能完全没有
    session_meta, 但文件名从来不会变——这是唯一不受"读到哪一段"影响的会话 id 来源。
    """
    match = _ROLLOUT_UUID_RE.search(Path(source_path).name)
    return match.group(1) if match else None


# 单轮推进增量上限(判脏轮次阈值): 正常一轮几千到十几万 token, 5000万已是三个数量级以上
# 的异常, 只可能是解析错位/数据损坏——取值依据见模块 docstring。
_DIRTY_ROUND_TOKEN_THRESHOLD = 50_000_000

_TOTAL_FIELDS = ("input_tokens", "output_tokens", "cached_input_tokens", "reasoning_output_tokens")

# token_usage_record.payload.usage 字段 -> by_model bucket 字段的映射(实测字段形状,
# 与旧 total_token_usage 回退口径共用同一套 bucket 键名, 便于两条口径产出同构的会话行)。
_USAGE_RECORD_FIELD_MAP = {
    "input_tokens": "input_tokens",
    "cached_input_tokens": "cache_read_tokens",
    "cache_write_input_tokens": "cache_creation_tokens",
    "output_tokens": "output_tokens",
    "reasoning_output_tokens": "reasoning_output_tokens",
}


def _apply_advance(bucket: dict[str, Any], advance: dict[str, int]) -> None:
    bucket["input_tokens"] += advance.get("input_tokens", 0)
    bucket["output_tokens"] += advance.get("output_tokens", 0)
    bucket["cache_read_tokens"] += advance.get("cached_input_tokens", 0)
    bucket["reasoning_output_tokens"] += advance.get("reasoning_output_tokens", 0)


def _apply_usage_record(bucket: dict[str, Any], usage: dict[str, Any]) -> None:
    for src_key, dest_key in _USAGE_RECORD_FIELD_MAP.items():
        bucket[dest_key] += int(usage.get(src_key) or 0)


def _process_records(
    records: list[dict[str, Any]],
    *,
    source_path: str,
    current_model: str | None,
    baseline_total: dict[str, int] | None,
    usage_mode: str | None = None,
    declared_parent: str | None = None,
) -> tuple[CodexSessionUsage | None, int, str | None, dict[str, int], str | None, str | None]:
    """折叠一批(可能是增量)codex 记录成会话用量增量 + 脏轮次计数 + 跨批次延续状态。

    双源口径(见模块 docstring, 2026-09-25 硬化):
        - 本批次只要出现过一次 type=="token_usage_record", 整批就按"新口径"处理:
          只认 token_usage_record(按 (thread_id, response_id) 去重逐条累加 usage),
          完全跳过 event_msg/token_count(避免同一次调用两条口径都算一遍)。
        - usage_mode 一旦被判定为 "record"(本批或此前任一批出现过 token_usage_record),
          经由 watermark_state 跨批次延续、永不切回旧口径——同一份文件的用量口径必须
          全程一致, 不能这一批按新口径那一批按旧口径各算一半。
        - usage_mode 仍是 None(至今没见过 token_usage_record)时, 按 2026-07-03 硬化的
          total_token_usage 推进增量求和口径处理(旧版 codex 没有 token_usage_record)。

    current_model / baseline_total: 进入本批次之前已知的"当前模型"/"total 基线"
        (增量扫描时由调用方跨批次通过 watermark_state 传入延续, 让"推进"判断在多次
        collect() 调用之间保持连续, 不会因为水位线切割批次而被误判成"从 0 起跳的推进")。
    declared_parent: 跨批次延续的"这条 rollout 声明的父会话 id"(一旦从 session_meta 或
        token_usage_record 里读到过, 后续批次继续携带, 不因批次切割而丢失)。

    Returns:
        (session_or_None, skipped_dirty_rounds, current_model, baseline_total,
         usage_mode, declared_parent)
    """
    file_session_id = _session_id_from_filename(source_path)
    session_id: str | None = None  # session_meta 里的 id, 仅在文件名解不出 UUID 时兜底用
    cwd = ""
    by_model: dict[str, dict[str, Any]] = {}
    calls = 0
    started: str | None = None
    ended: str | None = None
    skipped_dirty_rounds = 0

    baseline: dict[str, int] = dict(baseline_total) if baseline_total else {k: 0 for k in _TOTAL_FIELDS}

    batch_has_record = any(rec.get("type") == "token_usage_record" for rec in records)
    mode = "record" if (usage_mode == "record" or batch_has_record) else usage_mode

    # (thread_id, response_id) 去重集合: token_usage_record 每行一次调用, 同一对
    # (thread_id, response_id) 只计一次(防重复写入把同一次调用算两遍)。
    seen_calls: set[tuple[Any, Any]] = set()

    for rec in records:
        rtype = rec.get("type")
        payload = rec.get("payload") or {}
        ts = rec.get("timestamp")
        if ts:
            if started is None:
                started = ts
            ended = ts

        if rtype == "session_meta":
            declared_id = payload.get("session_id")
            own_id = payload.get("id") or session_id
            session_id = own_id or session_id
            effective_own_id = file_session_id or own_id
            if declared_id and effective_own_id and declared_id != effective_own_id:
                declared_parent = declared_id
            cwd = payload.get("cwd") or cwd
            continue

        model_field = payload.get("model")
        if isinstance(model_field, str) and model_field:
            current_model = model_field

        if rtype == "token_usage_record":
            thread_id = payload.get("thread_id")
            response_id = payload.get("response_id")
            rec_session_id = payload.get("session_id")
            effective_own_id = file_session_id or thread_id
            if rec_session_id and effective_own_id and rec_session_id != effective_own_id:
                declared_parent = declared_parent or rec_session_id
            usage = payload.get("usage")
            if not isinstance(usage, dict):
                continue
            call_key = (thread_id, response_id)
            if call_key in seen_calls:
                continue
            seen_calls.add(call_key)
            model = current_model or "unknown"
            bucket = by_model.setdefault(model, _empty_model_bucket())
            _apply_usage_record(bucket, usage)
            calls += 1
            continue

        if mode == "record":
            # 本文件已确定用新口径(token_usage_record), 旧 event_msg/token_count 完全
            # 跳过, 不再参与累加——避免同一次调用被两条口径各算一遍。
            continue

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
        return None, skipped_dirty_rounds, current_model, baseline, mode, declared_parent

    resolved_session_id = file_session_id or session_id or "unknown-session"
    parent_session_id = declared_parent if declared_parent and declared_parent != resolved_session_id else None

    session = CodexSessionUsage(
        session_id=resolved_session_id,
        source_path=source_path,
        cwd=cwd,
        by_model=by_model,
        calls=calls,
        started=started,
        ended=ended,
        parent_session_id=parent_session_id,
    )
    return session, skipped_dirty_rounds, current_model, baseline, mode, declared_parent


def collect_codex_sessions(
    root: Path,
    *,
    watermark_state: dict[str, Any] | None = None,
) -> CodexCollectResult:
    """扫 root(默认应传 ~/.codex/sessions)下 rollout-*.jsonl, 按字节水位线增量聚合用量。

    Args:
        root: sessions 目录(测试传 tmp fixture, 生产传 Path.home()/".codex"/"sessions")。
        watermark_state: 上次 collect 返回的 new_watermark_state(每文件字节偏移 +
            当前模型延续状态 + total 基线延续状态 + 用量口径延续状态 + 声明父会话延续状态),
            省略则从头全量扫描。
    """
    watermark_state = watermark_state or {}
    new_state: dict[str, Any] = dict(watermark_state)
    result = CodexCollectResult(new_watermark_state=new_state)

    for rollout_path in _iter_rollout_files(root):
        key = str(rollout_path)
        prev_offset = int(watermark_state.get(key, 0) or 0)
        model_key = f"{key}::model"
        baseline_key = f"{key}::total_baseline"
        mode_key = f"{key}::usage_mode"
        parent_key = f"{key}::declared_parent"
        current_model = watermark_state.get(model_key)
        baseline_total = watermark_state.get(baseline_key)
        usage_mode = watermark_state.get(mode_key)
        declared_parent = watermark_state.get(parent_key)

        lines, new_offset, _start = read_new_lines(rollout_path, prev_offset=prev_offset)
        new_state[key] = new_offset
        if not lines:
            continue
        records, skipped = parse_jsonl_lines(lines)
        result.skipped_bad_lines += skipped
        session, skipped_dirty, current_model, baseline_total, usage_mode, declared_parent = _process_records(
            records, source_path=str(rollout_path),
            current_model=current_model, baseline_total=baseline_total,
            usage_mode=usage_mode, declared_parent=declared_parent,
        )
        new_state[model_key] = current_model
        new_state[baseline_key] = baseline_total
        new_state[mode_key] = usage_mode
        new_state[parent_key] = declared_parent
        result.skipped_dirty_rounds += skipped_dirty
        if session is None:
            continue
        result.sessions.append(session)

    return result


__all__ = ["CodexSessionUsage", "CodexCollectResult", "collect_codex_sessions"]
