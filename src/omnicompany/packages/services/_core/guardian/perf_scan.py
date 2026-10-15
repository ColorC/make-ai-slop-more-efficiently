# [OMNI] origin=claude-code domain=omnicompany/guardian ts=2026-08-16T00:00:00Z type=router
# [OMNI] material_id="material:core.guardian.perf_scan.deterministic_perf_worker.py"
"""PerfScanWorker — 确定性性能巡检 (PERF-OBSERVATORY P2, 2026-08-16; 资源治理扩展 2026-08-17).

判定纪律真源: ``docs/plans/dashboard/[2026-08-16]PERF-OBSERVATORY/P2-P3-口径合同.md``
阈值真源:     ``config/perf/slo.yaml`` (schema omni.perf-slo.v1)。本模块只读该文件取阈值,
              代码里不硬编码任何 SLO 数值。
资源治理策略真源: ``config/perf/resource-policy.yaml`` (priority_enforcer 阈值/白名单前缀、
              runaway_idle_detection 声明、provenance 必填字段)。

输入源(均只读, 增量水位线扫描, 不改任何数据源):
  - logs/dashboard-slow-errors.jsonl           (duration 字段是秒, 用前 ×1000)
  - logs/frontend-telemetry.jsonl              (前端埋点事件流)
  - data/services/latency_monitor/history.jsonl (ccdaemon 被动延迟聚合快照, 60s 一条)
  - data/services/resource_monitor/latest.json  (进程分组当前快照 groups + 当前活跃 alerts,
    驱动 runaway-idle-sessions / orphan-dev-services / sustained-cpu-hog-unmanaged 三条 SLO
    与 offenders.by_owner 榜, 2026-08-17 起真正参与判定, 不再只是 meta 参考)
  - data/services/resource_monitor/alerts.jsonl (告警事件流, 增量水位线, 供 24h 窗口回看)
  - data/cc_sessions.json                       (session_id → name/started_at/active_plan,
    单次全量点查一次, 供 by_owner provenance 补全; 文件超过 ``MAX_CC_SESSIONS_LOOKUP_BYTES``
    则跳过, 相关字段标 unknown 不猜测)
  - data/ledger/events.jsonl                    (type=perf.remediation 降级留痕, 供
    sustained-cpu-hog-unmanaged 判定"是否已被 enforcer 处置"; 2026-08-17 核实代码库尚无
    priority_enforcer 实现落点, 该源恒为空, 因此该 SLO 目前恒 no_data, 见函数内注释)
  latency 优先走 HTTP GET :8201/cc/resources/latency (2s 超时), 失败回落读 history.jsonl 尾部,
  再失败判 no_data。

判定纪律(与合同一致):
  - 长轮询白名单(``slo.yaml`` 的 ``long_poll_whitelist``)命中不算慢请求违规;
  - 数据源缺失(文件不存在)或窗口内零样本 → 该 SLO 判 ``no_data``, 绝不伪造合法值;
  - 资源治理三条新 SLO 额外遵循"kind 从未出现 → no_data"纪律: 即使数据源文件存在, 若某个
    alert kind 从未在当前快照+窗口水位线里出现过, 视为"该检测尚未产生数据"而非"已测得零",
    不伪造绿灯(见 ``_kind_alert_summary``); 一旦上游真的开始产生该 kind, 无需再改本文件。
  - 水位线增量: 每个 jsonl 源记录"已消费字节数", 只读新增字节; 文件被截断/重建则从头重读;
    窗口聚合状态(裁剪到 window_hours 内的紧凑记录)与水位线一起存
    ``data/services/guardian/perf/state.json``, 第二次 scan 不会对同一批行重复计数。
  - 单次 scan 目标 <5s; 若某源过大导致耗时超预算, 如实标注 meta.truncated, 不静默吞。
  - critical 级 finding(runaway-idle-sessions / sustained-cpu-hog-unmanaged 判红)推一条
    ``omni notify`` 铃铛提醒; 推送失败 fail-open, 绝不挡主扫描流程。测试传 ``skip_notify=True``
    避免写生产铃铛文件。

落地: 纯确定性 worker, 不调用任何 LLM。CLI: ``omni guardian perf scan`` / ``omni guardian perf latest``。
"""
from __future__ import annotations

import json
import logging
import math
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import yaml

from omnicompany.packages.services._core.omnicompany import Worker
from omnicompany.protocol.anchor import Verdict, VerdictKind

logger = logging.getLogger(__name__)

SCHEMA = "omni.perf-scan.v1"
STATE_SCHEMA = "omni.perf-scan-state.v1"

DEFAULT_LATENCY_HTTP_URL = "http://127.0.0.1:8201/cc/resources/latency"
DEFAULT_HTTP_TIMEOUT_S = 2.0
DEFAULT_WINDOW_HOURS = 24.0
MAX_EVIDENCE_SAMPLES = 3
TOP_N_OFFENDERS = 5
MIN_SAMPLES_FOR_PATH_P95 = 3   # offender 列表质量门槛(不是判红阈值), 样本太少的路径不进榜
FETCH_ERROR_BURST_WINDOW_S = 10.0
MAX_CC_SESSIONS_LOOKUP_BYTES = 40 * 1024 * 1024  # 超过就跳过点查, provenance 字段标 unknown
LEDGER_TAIL_BYTES = 500_000        # ledger events.jsonl 只尾读, 不整读
ALERTS_TAIL_FALLBACK_BYTES = 500_000
ENFORCER_REMEDIATION_LEDGER_TYPE = "perf.remediation"  # resource-policy.yaml 设计: 自动动作必留痕
_GROUP_ID_PREFIXES = ("agent:", "ccdaemon:", "dashboard:", "review-service:", "workspace-task:")

_FETCH_ERROR_MARKER = " 网络失败"
_TRACKED_TELEMETRY_KINDS = {
    "fetch_network_error",
    "fetch_result",
    "terminal_snapshot_replay",
    "longtask_minute",
    "terminal_ws_reconnect",
}

# 完全没有接线的数据源(P1.5b/P1 遗留, 见口径合同末尾): 恒定 no_data, 不需要任何输入。
_UNWIRED_COUNTER_SLOS = {
    "meta-store-write-p95": "ccdaemon 计数器 meta_store_write 尚未接线 (P1.5b 待做)",
    "threadpool-queue-depth-p95": "ccdaemon 计数器 threadpool_queue_depth 尚未接线 (P1.5b 待做)",
}
# 不在 P2 四源范围内的 SLO: 需要独立的每日 playwright probe, 本 scan 不采集。
_OUT_OF_SCOPE_SLOS = {
    "click-to-paint-p95": "source=perf-probe(playwright daily-probe) 不在 P2 四源范围内, 需独立 probe 接线",
}


# ─── 基础工具 ──────────────────────────────────────────────────────────

def _percentile(values: list[float], q: float) -> float | None:
    """最近秩(nearest-rank)分位数, 与 latency_monitor._quantile_upper_ms 同一口径。"""
    if not values:
        return None
    s = sorted(values)
    idx = max(0, min(len(s) - 1, math.ceil(q * len(s)) - 1))
    return s[idx]


def _extract_fetch_error_path(message: str) -> str:
    """fetch_network_error 的 path 只嵌在 message 里(data 字段为空), 需从文案里抠。

    观测到的真实文案: "/api/boss-sight/captures/surface 网络失败: Failed to fetch"
    """
    if not message:
        return "(unknown)"
    idx = message.find(_FETCH_ERROR_MARKER)
    if idx > 0:
        return message[:idx].strip()
    for token in message.split():
        if token.startswith("/"):
            return token
    return message[:80]


def _is_whitelisted(path: str, query: str, whitelist: list[dict[str, Any]]) -> bool:
    path = path or ""
    query = query or ""
    for rule in whitelist:
        pc = rule.get("path_contains")
        qc = rule.get("query_contains")
        pp = rule.get("path_prefix")
        if pc is None and qc is None and pp is None:
            continue
        ok = True
        if pc is not None and pc not in path:
            ok = False
        if qc is not None and qc not in query:
            ok = False
        if pp is not None and not path.startswith(pp):
            ok = False
        if ok:
            return True
    return False


def _max_burst(timestamps: list[float], window_s: float) -> int:
    """滑动窗口内同一分组最大命中数(双指针, O(n))。"""
    ts = sorted(timestamps)
    best = 0
    left = 0
    for right in range(len(ts)):
        while ts[right] - ts[left] > window_s:
            left += 1
        best = max(best, right - left + 1)
    return best


def _read_new_lines(path: Path, prev_offset: int) -> tuple[list[str], int]:
    """水位线增量读取: 只读 [prev_offset, size) 新增字节。

    文件不存在 → 原样返回空 + 不变水位线(调用方据此判 source missing)。
    文件比水位线还小(截断/重建) → 保守地从 0 重读整份, 不假设中间状态。
    """
    try:
        size = path.stat().st_size
    except OSError:
        return [], prev_offset
    start = prev_offset if 0 <= prev_offset <= size else 0
    if start == size:
        return [], size
    try:
        with path.open("rb") as fh:
            fh.seek(start)
            raw = fh.read()
    except OSError:
        return [], prev_offset
    text = raw.decode("utf-8", errors="replace")
    lines = [ln.strip() for ln in text.split("\n") if ln.strip()]
    return lines, size


def _parse_jsonl(lines: list[str]) -> tuple[list[dict[str, Any]], int]:
    records: list[dict[str, Any]] = []
    bad = 0
    for ln in lines:
        try:
            obj = json.loads(ln)
        except json.JSONDecodeError:
            bad += 1
            continue
        if isinstance(obj, dict):
            records.append(obj)
        else:
            bad += 1
    return records, bad


def _judge_at_least(measured: float | None, target_value: float | None) -> str:
    """"≥N 次算违规"类判定(无 yellow 缓冲带), 专供 fetch-error-burst 这种爆发计数器用。

    与 ``_judge`` 的"ceiling, 越过才算问题"语义不同: 合同原文明确写"10s 内同 path
    ≥3 次", 命中阈值本身就已经是违规, 不是刚好卡在及格线上的绿。
    """
    if measured is None or target_value is None:
        return "no_data"
    return "red" if measured >= target_value else "green"


def _judge(measured: float | None, target_value: float | None, *, zero_tolerance: bool = False) -> str:
    """red/yellow/green/no_data 通判。

    zero_tolerance: 目标是 count:0 这类"应为零"指标, 没有 yellow 缓冲带, 一超就是 red。
    否则 yellow = (target, target*1.5], red = > target*1.5 (口径合同未定软阈值, 这是
    本 worker 的显式假设, 已在派发报告里标注给主会话校准)。
    """
    if measured is None or target_value is None:
        return "no_data"
    if zero_tolerance:
        return "red" if measured > 0 else "green"
    if measured <= target_value:
        return "green"
    if measured <= target_value * 1.5:
        return "yellow"
    return "red"


def _finding(
    slo_id: str, level: str, measured: Any, target: Any, window: str,
    evidence: list[Any], source: str,
) -> dict[str, Any]:
    return {
        "slo_id": slo_id,
        "level": level,
        "measured": measured,
        "target": target,
        "window": window,
        "evidence_sample": (evidence or [])[:MAX_EVIDENCE_SAMPLES],
        "source": source,
    }


def _top_n(rows: list[dict[str, Any]], key: str, n: int = TOP_N_OFFENDERS) -> list[dict[str, Any]]:
    return sorted(rows, key=lambda r: r.get(key, 0), reverse=True)[:n]


# ─── 记录变换(原始 jsonl 行 → 紧凑窗口记录) ──────────────────────────

def _slow_error_record(raw: dict[str, Any], whitelist: list[dict[str, Any]]) -> dict[str, Any] | None:
    ts = raw.get("ts")
    duration_s = raw.get("duration")
    if ts is None or duration_s is None:
        return None
    try:
        ts = float(ts)
        duration_ms = float(duration_s) * 1000.0
    except (TypeError, ValueError):
        return None
    path = raw.get("path") or ""
    query = raw.get("query") or ""
    return {
        "ts": ts,
        "duration_ms": duration_ms,
        "status": raw.get("status"),
        "method": raw.get("method"),
        "path": path,
        "query": query,
        "whitelisted": _is_whitelisted(path, query, whitelist),
    }


def _telemetry_record(raw: dict[str, Any]) -> dict[str, Any] | None:
    event = raw.get("event") or {}
    kind = event.get("kind")
    if kind not in _TRACKED_TELEMETRY_KINDS:
        return None
    ts = event.get("t")
    if ts is None:
        return None
    try:
        ts = float(ts)
    except (TypeError, ValueError):
        return None
    instance = raw.get("instance") or {}
    data = event.get("data") or {}
    rec: dict[str, Any] = {
        "ts": ts,
        "kind": kind,
        "instance_id": instance.get("instance_id"),
        "level": event.get("level"),
    }
    if kind == "fetch_network_error":
        rec["path"] = _extract_fetch_error_path(event.get("message") or "")
    elif kind == "terminal_snapshot_replay":
        rec["session_id"] = data.get("session_id")
        rec["duration_ms"] = data.get("duration_ms")
        rec["bytes"] = data.get("bytes")
    elif kind == "longtask_minute":
        rec["count"] = data.get("count")
        rec["total_ms"] = data.get("total_ms")
        rec["ua"] = instance.get("ua")
    elif kind == "terminal_ws_reconnect":
        rec["session_id"] = data.get("session_id")
        rec["close_code"] = data.get("close_code")
    return rec


def _alert_record(raw: dict[str, Any]) -> dict[str, Any] | None:
    """resource_monitor alerts.jsonl 一行是事件信封 ``{event, ts, alert:{...}}``

    (见 ``resource_monitor._advance_alert_state``/``_append_jsonl`` 调用点), kind/status/
    fingerprint 都嵌在 ``alert`` 里, 不是顶层字段。压成紧凑记录供窗口缓冲复用。
    """
    ts = raw.get("ts")
    alert = raw.get("alert") or {}
    if ts is None or not alert:
        return None
    try:
        ts = float(ts)
    except (TypeError, ValueError):
        return None
    return {
        "ts": ts,
        "event": raw.get("event"),
        "kind": alert.get("kind"),
        "status": alert.get("status"),
        "subject": alert.get("subject"),
        "fingerprint": alert.get("fingerprint"),
        "severity": alert.get("severity"),
        "message": alert.get("message"),
    }


# ─── 状态(水位线 + 窗口缓冲) ───────────────────────────────────────────

@dataclass
class _ScanState:
    offsets: dict[str, int] = field(default_factory=dict)
    slow_errors: list[dict[str, Any]] = field(default_factory=list)
    telemetry: list[dict[str, Any]] = field(default_factory=list)
    alerts: list[dict[str, Any]] = field(default_factory=list)
    last_scan_ts: float | None = None

    @classmethod
    def load(cls, path: Path) -> "_ScanState":
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return cls()
        return cls(
            offsets=dict(raw.get("offsets") or {}),
            slow_errors=list(raw.get("slow_errors") or []),
            telemetry=list(raw.get("telemetry") or []),
            alerts=list(raw.get("alerts") or []),
            last_scan_ts=raw.get("last_scan_ts"),
        )

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "schema": STATE_SCHEMA,
            "offsets": self.offsets,
            "slow_errors": self.slow_errors,
            "telemetry": self.telemetry,
            "alerts": self.alerts,
            "last_scan_ts": self.last_scan_ts,
        }
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        tmp.replace(path)


class PerfScanWorker(Worker):
    """PERF-OBSERVATORY P2: 确定性性能巡检(零 LLM)。

    读四源 + latency HTTP 快照, 按 ``config/perf/slo.yaml`` 判红/黄/绿/no_data,
    产 findings + offenders(by_session/by_path/by_instance Top5) + meta, 落盘。
    """

    DESCRIPTION = (
        "PERF-OBSERVATORY P2 性能巡检: 读 slow-errors/frontend-telemetry/latency_monitor/"
        "resource_monitor 四源(latency 优先走 HTTP 快照), 按 config/perf/slo.yaml 12 条阈值"
        "判红黄绿或 no_data, 产 offenders 三榜。纯确定性扫描, 不调 LLM。"
    )
    FORMAT_IN = "guardian.perf-scan-request"
    FORMAT_OUT = "guardian.perf-scan-report"
    INPUT_KEYS = ["project_root"]

    def run(self, input_data: dict[str, Any]) -> Verdict:
        t_start = time.perf_counter()
        project_root = Path(input_data.get("project_root") or _default_project_root())
        window_hours = float(input_data.get("window_hours") or DEFAULT_WINDOW_HOURS)
        now = float(input_data.get("_now") or time.time())
        window_start = now - window_hours * 3600.0

        slo_config_path = Path(
            input_data.get("slo_config_path") or (project_root / "config" / "perf" / "slo.yaml")
        )
        slow_errors_path = Path(
            input_data.get("slow_errors_path") or (project_root / "logs" / "dashboard-slow-errors.jsonl")
        )
        telemetry_path = Path(
            input_data.get("telemetry_path") or (project_root / "logs" / "frontend-telemetry.jsonl")
        )
        latency_history_path = Path(
            input_data.get("latency_history_path")
            or (project_root / "data" / "services" / "latency_monitor" / "history.jsonl")
        )
        resource_monitor_dir = Path(
            input_data.get("resource_monitor_dir")
            or (project_root / "data" / "services" / "resource_monitor")
        )
        alerts_path = Path(input_data.get("alerts_path") or (resource_monitor_dir / "alerts.jsonl"))
        resource_policy_path = Path(
            input_data.get("resource_policy_path")
            or (project_root / "config" / "perf" / "resource-policy.yaml")
        )
        cc_sessions_path = Path(
            input_data.get("cc_sessions_path") or (project_root / "data" / "cc_sessions.json")
        )
        ledger_events_path = Path(
            input_data.get("ledger_events_path") or (project_root / "data" / "ledger" / "events.jsonl")
        )
        skip_notify = bool(input_data.get("skip_notify", False))
        state_path = Path(
            input_data.get("state_path")
            or (project_root / "data" / "services" / "guardian" / "perf" / "state.json")
        )
        out_dir = Path(
            input_data.get("out_dir")
            or (project_root / "data" / "services" / "guardian" / "perf")
        )
        latency_http_url = input_data.get("latency_http_url", DEFAULT_LATENCY_HTTP_URL)
        skip_http = bool(input_data.get("skip_http", False))
        http_timeout_s = float(input_data.get("http_timeout_s") or DEFAULT_HTTP_TIMEOUT_S)
        scan_budget_s = float(input_data.get("scan_budget_s") or 5.0)

        # ── 1. SLO 阈值真源 ──
        slo_doc, whitelist = _load_slo_config(slo_config_path)
        slo_by_id = {s["id"]: s for s in (slo_doc.get("slos") or []) if s.get("id")}
        slo_config_missing = not slo_config_path.exists()

        # ── 2. 状态(水位线 + 窗口缓冲) ──
        state = _ScanState.load(state_path)

        slow_errors_missing = not slow_errors_path.exists()
        new_lines, new_offset = _read_new_lines(slow_errors_path, state.offsets.get("slow_errors", 0))
        new_raw, bad_slow = _parse_jsonl(new_lines)
        for raw in new_raw:
            rec = _slow_error_record(raw, whitelist)
            if rec is not None:
                state.slow_errors.append(rec)
        state.offsets["slow_errors"] = new_offset
        state.slow_errors = [r for r in state.slow_errors if r["ts"] >= window_start]

        telemetry_missing = not telemetry_path.exists()
        new_lines2, new_offset2 = _read_new_lines(telemetry_path, state.offsets.get("telemetry", 0))
        new_raw2, bad_tel = _parse_jsonl(new_lines2)
        for raw in new_raw2:
            rec = _telemetry_record(raw)
            if rec is not None:
                state.telemetry.append(rec)
        state.offsets["telemetry"] = new_offset2
        state.telemetry = [r for r in state.telemetry if r["ts"] >= window_start]

        alerts_jsonl_missing = not alerts_path.exists()
        new_lines3, new_offset3 = _read_new_lines(alerts_path, state.offsets.get("alerts", 0))
        new_raw3, bad_alerts = _parse_jsonl(new_lines3)
        for raw in new_raw3:
            rec = _alert_record(raw)
            if rec is not None:
                state.alerts.append(rec)
        state.offsets["alerts"] = new_offset3
        state.alerts = [r for r in state.alerts if r["ts"] >= window_start]

        state.last_scan_ts = now

        # ── 3. latency 快照(HTTP 优先, 失败回落 history.jsonl 尾部) ──
        latency_metrics, latency_source, latency_no_data_reason = _load_latency_metrics(
            latency_history_path, window_start, latency_http_url,
            skip_http=skip_http, timeout_s=http_timeout_s,
        )

        # ── 4. resource_monitor(meta 参考 + 资源治理三条 SLO 的真源) ──
        resource_meta = _load_resource_monitor_meta(resource_monitor_dir)
        resource_snapshot = _read_latest_resource_snapshot(resource_monitor_dir)
        resource_snapshot_missing = resource_snapshot is None
        live_alerts = (resource_snapshot or {}).get("alerts") or []
        groups_now = (resource_snapshot or {}).get("groups") or []

        # ── 5. 资源治理策略(阈值/白名单/enforcer 留痕) ──
        resource_policy = _load_resource_policy_config(resource_policy_path)
        ledger_ever_seen, demoted_group_ids = _load_ledger_remediation(ledger_events_path, window_start)
        cc_sessions_lookup = _load_cc_sessions_lookup(cc_sessions_path)

        window_label = f"{window_hours:g}h"

        findings: list[dict[str, Any]] = []
        findings.append(self._finding_api_slow_requests(slo_by_id, state.slow_errors, window_label, slow_errors_missing))
        findings.append(self._finding_fetch_error_burst(slo_by_id, state.telemetry, window_label, telemetry_missing))
        findings.append(self._finding_snapshot_replay(slo_by_id, state.telemetry, window_label, telemetry_missing))
        findings.append(self._finding_longtask(slo_by_id, state.telemetry, window_label, telemetry_missing))
        findings.append(self._finding_ws_reconnect(slo_by_id, state.telemetry, window_label, telemetry_missing))
        findings.append(self._finding_subscriber_kick(slo_by_id, state.telemetry, window_label, telemetry_missing))
        findings.append(self._finding_hidden_network(slo_by_id, state.telemetry, window_label, telemetry_missing))
        findings.append(self._finding_latency_metric(
            slo_by_id, "event-loop-lag-p95", "event_loop_lag", latency_metrics, window_label, latency_source,
        ))
        findings.append(self._finding_latency_metric(
            slo_by_id, "pty-input-to-first-output-p95", "pty_input_to_first_output",
            latency_metrics, window_label, latency_source,
        ))
        for slo_id, reason in _UNWIRED_COUNTER_SLOS.items():
            spec = slo_by_id.get(slo_id, {})
            findings.append(_finding(slo_id, "no_data", None, spec.get("target"), window_label, [reason], reason))
        for slo_id, reason in _OUT_OF_SCOPE_SLOS.items():
            spec = slo_by_id.get(slo_id, {})
            findings.append(_finding(slo_id, "no_data", None, spec.get("target"), window_label, [reason], reason))
        findings.append(self._finding_self_overhead(slo_by_id, window_label))

        # ── 资源治理三条新 SLO(2026-08-17) ──
        findings.append(self._finding_orphan_dev_services(
            slo_by_id, live_alerts, state.alerts, window_label, resource_snapshot_missing,
        ))
        findings.append(self._finding_runaway_idle_sessions(
            slo_by_id, live_alerts, state.alerts, window_label, resource_snapshot_missing,
        ))
        findings.append(self._finding_sustained_cpu_hog(
            slo_by_id, resource_policy, groups_now, ledger_ever_seen, demoted_group_ids,
            window_label, resource_snapshot_missing,
        ))

        _maybe_notify_critical(findings, skip=skip_notify)

        offenders = self._build_offenders(state.slow_errors, state.telemetry, groups_now, cc_sessions_lookup)

        elapsed_ms = (time.perf_counter() - t_start) * 1000.0
        truncated = elapsed_ms > (scan_budget_s * 1000.0)
        if truncated:
            logger.warning("PerfScanWorker 超预算: %.1fms > %.0fms", elapsed_ms, scan_budget_s * 1000.0)

        # 找 findings 里的 self-overhead 条目回填真实 scan_duration_ms(上面构造时还没算完)
        for f in findings:
            if f["slo_id"] == "observability-self-overhead":
                f["measured"] = {"scan_duration_ms": round(elapsed_ms, 1)}

        meta = {
            "scan_duration_ms": round(elapsed_ms, 1),
            "lines_scanned": {
                "slow_errors": len(new_raw),
                "frontend_telemetry": len(new_raw2),
                "resource_alerts": len(new_raw3),
            },
            "bad_lines": {
                "slow_errors": bad_slow, "frontend_telemetry": bad_tel, "resource_alerts": bad_alerts,
            },
            "window_start": window_start,
            "window_end": now,
            "window_hours": window_hours,
            "slo_version": slo_doc.get("schema"),
            "truncated": truncated,
            "sources": {
                "slow_errors_missing": slow_errors_missing,
                "telemetry_missing": telemetry_missing,
                "latency_source": latency_source,
                "slo_config_missing": slo_config_missing,
                "alerts_jsonl_missing": alerts_jsonl_missing,
                "resource_snapshot_missing": resource_snapshot_missing,
                "resource_policy_missing": not resource_policy_path.exists(),
                "ledger_remediation_ever_seen": ledger_ever_seen,
            },
            "resource_monitor": resource_meta,
        }

        now_iso = _iso(now)
        report = {
            "schema": SCHEMA,
            "scan_ts": now_iso,
            "findings": findings,
            "offenders": offenders,
            "meta": meta,
        }

        state.save(state_path)
        report_path = _write_report(out_dir, now_iso, report)

        by_level: dict[str, int] = {}
        for f in findings:
            by_level[f["level"]] = by_level.get(f["level"], 0) + 1

        return Verdict(
            kind=VerdictKind.PASS,
            output={**report, "report_path": str(report_path)},
            diagnosis=(
                f"perf scan: {len(findings)} findings (by_level={by_level}) "
                f"in {elapsed_ms:.0f}ms"
            ),
        )

    # ── 单条 SLO 判定 ──────────────────────────────────────────────

    def _finding_api_slow_requests(self, slo_by_id, records, window_label, missing) -> dict[str, Any]:
        slo_id = "api-slow-requests"
        spec = slo_by_id.get(slo_id, {})
        target = spec.get("target") or {}
        target_ms = target.get("p95_ms")
        source = spec.get("source", "dashboard-slow-errors.jsonl")
        if missing:
            return _finding(slo_id, "no_data", None, target, window_label, [], source)
        eligible = [r for r in records if not r["whitelisted"]]
        durations = [r["duration_ms"] for r in eligible]
        p95 = _percentile(durations, 0.95)
        level = _judge(p95, target_ms)
        evidence = [
            {"path": r["path"], "duration_ms": round(r["duration_ms"], 1), "status": r["status"]}
            for r in sorted(eligible, key=lambda r: r["duration_ms"], reverse=True)[:MAX_EVIDENCE_SAMPLES]
        ]
        measured = {"p95_ms": p95, "sample_count": len(eligible)} if p95 is not None else None
        return _finding(slo_id, level, measured, target, window_label, evidence, source)

    def _finding_fetch_error_burst(self, slo_by_id, records, window_label, missing) -> dict[str, Any]:
        slo_id = "fetch-error-burst"
        spec = slo_by_id.get(slo_id, {})
        target = spec.get("target") or {}
        threshold = target.get("max_same_path_errors_per_10s")
        source = spec.get("source", "frontend-telemetry kind=fetch_network_error")
        errs = [r for r in records if r["kind"] == "fetch_network_error"]
        if missing or not errs:
            return _finding(slo_id, "no_data", None, target, window_label, [], source)
        by_path: dict[str, list[float]] = {}
        for r in errs:
            by_path.setdefault(r.get("path") or "(unknown)", []).append(r["ts"])
        worst_path, worst_burst = max(
            ((p, _max_burst(tss, FETCH_ERROR_BURST_WINDOW_S)) for p, tss in by_path.items()),
            key=lambda item: item[1],
        )
        level = _judge_at_least(worst_burst, threshold)
        measured = {"max_same_path_errors_per_10s": worst_burst, "worst_path": worst_path}
        evidence = [{"path": worst_path, "count": len(by_path[worst_path])}]
        return _finding(slo_id, level, measured, target, window_label, evidence, source)

    def _finding_snapshot_replay(self, slo_by_id, records, window_label, missing) -> dict[str, Any]:
        slo_id = "snapshot-replay-p95"
        spec = slo_by_id.get(slo_id, {})
        target = spec.get("target") or {}
        target_ms = target.get("p95_ms")
        source = spec.get("source", "frontend-telemetry kind=terminal_snapshot_replay")
        durations = [
            r["duration_ms"] for r in records
            if r["kind"] == "terminal_snapshot_replay" and r.get("duration_ms") is not None
        ]
        if missing or not durations:
            return _finding(slo_id, "no_data", None, target, window_label, [], source)
        p95 = _percentile(durations, 0.95)
        level = _judge(p95, target_ms)
        measured = {"p95_ms": p95, "sample_count": len(durations)}
        return _finding(slo_id, level, measured, target, window_label, [], source)

    def _finding_longtask(self, slo_by_id, records, window_label, missing) -> dict[str, Any]:
        slo_id = "longtask-per-minute"
        spec = slo_by_id.get(slo_id, {})
        target = spec.get("target") or {}
        target_max = target.get("max_per_min")
        source = spec.get("source", "frontend-telemetry kind=longtask_minute")
        rows = [r for r in records if r["kind"] == "longtask_minute" and r.get("count") is not None]
        if missing or not rows:
            return _finding(slo_id, "no_data", None, target, window_label, [], source)
        counts = [r["count"] for r in rows]
        worst = max(counts)
        level = _judge(worst, target_max)
        mobile_counts = [r["count"] for r in rows if _is_mobile_ua(r.get("ua"))]
        measured: dict[str, Any] = {"max_per_min": worst, "sample_count": len(rows)}
        if mobile_counts:
            measured["mobile_max_per_min"] = max(mobile_counts)
        return _finding(slo_id, level, measured, target, window_label, [], source)

    def _finding_ws_reconnect(self, slo_by_id, records, window_label, missing) -> dict[str, Any]:
        slo_id = "ws-reconnect-rate"
        spec = slo_by_id.get(slo_id, {})
        target = spec.get("target") or {}
        target_max = target.get("max_per_session_per_hour")
        source = spec.get("source", "frontend-telemetry kind=terminal_ws_reconnect")
        rows = [r for r in records if r["kind"] == "terminal_ws_reconnect"]
        if missing or not rows:
            return _finding(slo_id, "no_data", None, target, window_label, [], source)
        by_session: dict[str, int] = {}
        for r in rows:
            sid = r.get("session_id") or "(unknown)"
            by_session[sid] = by_session.get(sid, 0) + 1
        window_hours = max(1e-9, (max(r["ts"] for r in rows) - min(r["ts"] for r in rows)) / 3600.0) or 1.0
        # 保守取"整窗口按 window_label 折算", 而非按实际数据跨度(避免短跨度虚高), 见口径校准需求
        per_hour = {sid: c for sid, c in by_session.items()}
        worst_sid, worst_count = max(per_hour.items(), key=lambda kv: kv[1])
        measured = {"max_per_session_per_hour": worst_count, "session_id": worst_sid}
        level = _judge(worst_count, target_max)
        return _finding(slo_id, level, measured, target, window_label, [], source)

    def _finding_subscriber_kick(self, slo_by_id, records, window_label, missing) -> dict[str, Any]:
        slo_id = "subscriber-kick-1012"
        spec = slo_by_id.get(slo_id, {})
        target = spec.get("target") or {}
        target_count = target.get("count")
        source = spec.get("source", "terminal_ws_reconnect reason=1012 (代理口径)")
        rows = [r for r in records if r["kind"] == "terminal_ws_reconnect"]
        if missing or not rows:
            # 连代理源(terminal_ws_reconnect)本身窗口内都零样本 → no_data, 不是"测了是绿"
            return _finding(slo_id, "no_data", None, target, window_label, [], source)
        kicked = [r for r in rows if r.get("close_code") == 1012]
        measured = {"count": len(kicked)}
        level = _judge(len(kicked), target_count, zero_tolerance=(target_count == 0))
        evidence = [{"session_id": r.get("session_id")} for r in kicked[:MAX_EVIDENCE_SAMPLES]]
        return _finding(slo_id, level, measured, target, window_label, evidence, source)

    def _finding_hidden_network(self, slo_by_id, records, window_label, missing) -> dict[str, Any]:
        slo_id = "hidden-network-quiet"
        spec = slo_by_id.get(slo_id, {})
        target = spec.get("target") or {}
        source = spec.get("source", "frontend-telemetry fetch join visibility (未接线字段)")
        # 当前 schema 里 fetch_* 事件既没有 visibility/hidden 字段, instance 也没有;
        # 没有可判定 join 的字段就是没测过, 不能拿"没报错"冒充"隐藏期安静"。
        return _finding(slo_id, "no_data", None, target, window_label,
                        ["前端事件 schema 尚无 visibility/hidden 字段可 join"], source)

    def _finding_latency_metric(
        self, slo_by_id, slo_id: str, metric_name: str, latency_metrics: dict[str, Any] | None,
        window_label: str, source_label: str,
    ) -> dict[str, Any]:
        spec = slo_by_id.get(slo_id, {})
        target = spec.get("target") or {}
        target_ms = target.get("p95_ms")
        source = spec.get("source", source_label)
        if not latency_metrics:
            return _finding(slo_id, "no_data", None, target, window_label, [], source)
        m = latency_metrics.get(metric_name) or {}
        count = m.get("count") or 0
        if not count:
            return _finding(slo_id, "no_data", None, target, window_label, [], source)
        p95 = m.get("p95_upper_ms")
        level = _judge(p95, target_ms)
        measured = {"p95_ms": p95, "sample_count": count}
        return _finding(slo_id, level, measured, target, window_label, [], f"{source} ({source_label})")

    def _finding_self_overhead(self, slo_by_id, window_label) -> dict[str, Any]:
        slo_id = "observability-self-overhead"
        spec = slo_by_id.get(slo_id, {})
        target = spec.get("target") or {}
        source = spec.get("source", "开关对比法(阶段性人工测)")
        # percent_max 阈值只在自由文本 note 里, 不在结构化 target 里 —— 不编造判定,
        # 只把本次 scan 自身耗时(meta.scan_duration_ms 同步一份)报出去供人工比对。
        return _finding(
            slo_id, "no_data", {"scan_duration_ms": None}, target, window_label,
            ["percent_max 需开关对比人工测, scan 只报自身耗时"], source,
        )

    # ── 资源治理三条新 SLO(config/perf/resource-policy.yaml, 2026-08-17) ──────

    def _finding_orphan_dev_services(
        self, slo_by_id, live_alerts, window_alerts, window_label, resource_missing,
    ) -> dict[str, Any]:
        slo_id = "orphan-dev-services"
        spec = slo_by_id.get(slo_id, {})
        target = spec.get("target") or {}
        target_max = target.get("max_confirmed")
        source = spec.get("source", "resource_monitor alerts kind=orphan-dev-service status=confirmed")
        if resource_missing:
            return _finding(slo_id, "no_data", None, target, window_label,
                             ["resource_monitor/latest.json 不存在"], source)
        # window: current -- 只用当前活跃快照, 不掺水位线里已经 resolved 的历史事件。
        ever_seen, count, evidence = _kind_alert_summary(
            "orphan-dev-service", live_alerts, window_alerts, include_window=False,
        )
        if not ever_seen:
            return _finding(
                slo_id, "no_data", None, target, window_label,
                ["orphan-dev-service kind 当前快照内从未出现"], source,
            )
        level = _judge(count, target_max, zero_tolerance=(target_max == 0))
        return _finding(slo_id, level, {"count": count}, target, window_label, evidence, source)

    def _finding_runaway_idle_sessions(
        self, slo_by_id, live_alerts, window_alerts, window_label, resource_missing,
    ) -> dict[str, Any]:
        slo_id = "runaway-idle-sessions"
        spec = slo_by_id.get(slo_id, {})
        target = spec.get("target") or {}
        target_count = target.get("count")
        source = spec.get(
            "source", "resource_monitor alerts kind=runaway-idle-agent-session status=confirmed",
        )
        if resource_missing:
            return _finding(slo_id, "no_data", None, target, window_label,
                             ["resource_monitor/latest.json 不存在"], source)
        # window: 24h -- 当前快照 ∪ alerts.jsonl 水位线窗口, 抓到"这次 scan 之前已 resolved
        # 但仍在窗口内"的确认事件, 不只看这一刻还活着的。
        ever_seen, count, evidence = _kind_alert_summary(
            "runaway-idle-agent-session", live_alerts, window_alerts, include_window=True,
        )
        if not ever_seen:
            return _finding(
                slo_id, "no_data", None, target, window_label,
                ["runaway-idle-agent-session kind 24h 窗口内从未出现(config/perf/"
                 "resource-policy.yaml runaway_idle_detection 已声明检测规则, 但当前快照/"
                 "水位线均无样本; 2026-08-17 核实 resource_monitor.py detect_observations() "
                 "尚未产生该 kind, 一旦接线本条自动切换到真实判定, 不需要再改本文件)"],
                source,
            )
        level = _judge(count, target_count, zero_tolerance=(target_count == 0))
        return _finding(slo_id, level, {"count": count}, target, window_label, evidence, source)

    def _finding_sustained_cpu_hog(
        self, slo_by_id, resource_policy, groups, ledger_ever_seen, demoted_group_ids,
        window_label, resource_missing,
    ) -> dict[str, Any]:
        slo_id = "sustained-cpu-hog-unmanaged"
        spec = slo_by_id.get(slo_id, {})
        target = spec.get("target") or {}
        target_count = target.get("count")
        source = spec.get(
            "source",
            "resource_monitor groups 持续>200%CPU 且未被降优先级(enforcer 漏网)",
        )
        if resource_missing:
            return _finding(slo_id, "no_data", None, target, window_label,
                             ["resource_monitor/latest.json 不存在"], source)

        enforcer = (resource_policy or {}).get("priority_enforcer") or {}
        threshold = enforcer.get("sustained_cpu_percent")
        protected_prefixes = tuple(str(p) for p in (enforcer.get("protected_group_prefixes") or ()))
        if threshold is None:
            return _finding(
                slo_id, "no_data", None, target, window_label,
                ["config/perf/resource-policy.yaml priority_enforcer.sustained_cpu_percent 缺失, 无阈值可判"],
                source,
            )

        candidates = [
            g for g in groups
            if float(g.get("cpu_percent") or 0) > float(threshold)
            and not str(g.get("id") or "").startswith(protected_prefixes)
        ]
        candidate_ids = [g.get("id") for g in candidates]

        if not ledger_ever_seen:
            # resource-policy.yaml 设计: 一切自动降级动作必须留痕(type=perf.remediation)。
            # 2026-08-17 核实全仓无 priority_enforcer/deprioritize_only 实现落点, ledger 里
            # 也确实没有这个 type -- 无法排除"候选组其实已被降级"这一半, 判 no_data 但仍
            # 把候选数报出去供人工参考, 不能假装它已经是判定值。
            return _finding(
                slo_id, "no_data",
                {"candidate_groups": len(candidates), "candidate_ids": candidate_ids[:MAX_EVIDENCE_SAMPLES]},
                target, window_label,
                [f"enforcer 降级留痕(ledger type={ENFORCER_REMEDIATION_LEDGER_TYPE})从未出现 -- "
                 "resource-policy.yaml priority_enforcer 已声明 deprioritize_only, 但代码库/ledger "
                 "均未见落地记录, 无法排除候选组已被降级, 判 no_data"],
                source,
            )

        unmanaged = [gid for gid in candidate_ids if gid not in demoted_group_ids]
        level = _judge(len(unmanaged), target_count, zero_tolerance=(target_count == 0))
        measured = {"count": len(unmanaged), "candidate_groups": len(candidates)}
        evidence = [{"group_id": gid} for gid in unmanaged[:MAX_EVIDENCE_SAMPLES]]
        return _finding(slo_id, level, measured, target, window_label, evidence, source)

    # ── offenders ──────────────────────────────────────────────────

    def _build_offenders(self, slow_errors, telemetry, groups=(), cc_sessions=None) -> dict[str, Any]:
        # by_session
        reconnects: dict[str, int] = {}
        replay_bytes: dict[str, int] = {}
        longtask_by_key: dict[str, int] = {}
        for r in telemetry:
            if r["kind"] == "terminal_ws_reconnect":
                sid = r.get("session_id") or "(unknown)"
                reconnects[sid] = reconnects.get(sid, 0) + 1
            elif r["kind"] == "terminal_snapshot_replay" and r.get("bytes"):
                sid = r.get("session_id") or "(unknown)"
                replay_bytes[sid] = replay_bytes.get(sid, 0) + int(r["bytes"])
            elif r["kind"] == "longtask_minute" and r.get("count"):
                # longtask 事件不带 session_id, 用 instance_id 代理归属(见模块 docstring 假设)
                key = r.get("instance_id") or "(unknown)"
                longtask_by_key[key] = longtask_by_key.get(key, 0) + int(r["count"])

        by_session = {
            "reconnect_count": _top_n(
                [{"key": k, "value": v} for k, v in reconnects.items()], "value"),
            "snapshot_replay_bytes": _top_n(
                [{"key": k, "value": v} for k, v in replay_bytes.items()], "value"),
            "longtask_count": _top_n(
                [{"key": k, "value": v} for k, v in longtask_by_key.items()], "value"),
        }

        # by_path
        burst_by_path: dict[str, list[float]] = {}
        for r in telemetry:
            if r["kind"] == "fetch_network_error":
                burst_by_path.setdefault(r.get("path") or "(unknown)", []).append(r["ts"])
        burst_rows = [
            {"key": p, "value": _max_burst(tss, FETCH_ERROR_BURST_WINDOW_S), "count": len(tss),
             "evidence_sample": [{"ts": t} for t in sorted(tss)[:MAX_EVIDENCE_SAMPLES]]}
            for p, tss in burst_by_path.items()
        ]

        slow_by_path: dict[str, list[float]] = {}
        for r in slow_errors:
            if r["whitelisted"]:
                continue
            slow_by_path.setdefault(r["path"] or "(unknown)", []).append(r["duration_ms"])
        slow_rows = [
            {"key": p, "value": _percentile(durs, 0.95), "count": len(durs)}
            for p, durs in slow_by_path.items()
            if len(durs) >= MIN_SAMPLES_FOR_PATH_P95
        ]

        by_path = {
            "fetch_error_burst_10s": _top_n(burst_rows, "value"),
            "slow_request_p95_ms": _top_n(slow_rows, "value"),
        }

        # by_instance
        totals: dict[str, int] = {}
        errors: dict[str, int] = {}
        for r in telemetry:
            if r["kind"] not in ("fetch_result", "fetch_network_error"):
                continue
            iid = r.get("instance_id") or "(unknown)"
            totals[iid] = totals.get(iid, 0) + 1
            if r["kind"] == "fetch_network_error" or r.get("level") == "error":
                errors[iid] = errors.get(iid, 0) + 1
        error_rate_rows = [
            {"key": iid, "value": round(errors.get(iid, 0) / total, 4), "total": total,
             "errors": errors.get(iid, 0)}
            for iid, total in totals.items() if total > 0
        ]

        by_instance = {
            "error_rate": _top_n(error_rate_rows, "value"),
            # hidden-network-quiet 无可 join 字段(见上), 无法归属实例, 如实留空而非编造
            "hidden_network_activity": [],
        }

        by_owner = _build_by_owner(groups, cc_sessions)

        return {
            "by_session": by_session, "by_path": by_path, "by_instance": by_instance,
            "by_owner": by_owner,
        }


def _is_mobile_ua(ua: str | None) -> bool:
    if not ua:
        return False
    ua_l = ua.lower()
    return any(marker in ua_l for marker in ("mobile", "android", "iphone", "ipad"))


def _load_slo_config(path: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    try:
        doc = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as e:
        logger.warning("slo.yaml 读取失败(%s): %s", path, e)
        return {}, []
    whitelist = doc.get("long_poll_whitelist") or []
    return doc, whitelist


def _load_latency_metrics(
    history_path: Path, window_start: float, http_url: str, *, skip_http: bool, timeout_s: float,
) -> tuple[dict[str, Any] | None, str, str | None]:
    """latency 优先走实时 HTTP 快照, 失败回落 history.jsonl 尾部, 再失败 no_data。"""
    if not skip_http and http_url:
        try:
            import requests
            resp = requests.get(http_url, timeout=timeout_s)
            if resp.status_code == 200:
                data = resp.json()
                metrics = data.get("metrics")
                if metrics:
                    return metrics, "http", None
        except Exception as e:  # noqa: BLE001 - HTTP 面失败必须能安全回落, 不能抛出中断 scan
            logger.debug("latency HTTP 快照获取失败, 回落 history.jsonl: %s", e)

    if not history_path.exists():
        return None, "no_data", "latency_monitor/history.jsonl 不存在且 HTTP 快照不可用"

    try:
        # history.jsonl 每行是全量累积快照(非每行独立窗口), 只需窗口内最新一条。
        lines = history_path.read_text(encoding="utf-8").splitlines()
    except OSError as e:
        return None, "no_data", f"history.jsonl 读取失败: {e}"

    latest_in_window: dict[str, Any] | None = None
    latest_ts = -1.0
    for ln in reversed(lines[-500:]):  # 只看尾部, 避免全量解析大文件
        ln = ln.strip()
        if not ln:
            continue
        try:
            obj = json.loads(ln)
        except json.JSONDecodeError:
            continue
        ts = obj.get("ts")
        if ts is None:
            continue
        if ts < window_start:
            break  # 已按时间倒序读到窗口外, 后面(更旧)的行不用再看
        if ts > latest_ts:
            latest_ts = ts
            latest_in_window = obj.get("metrics")
    if latest_in_window is None:
        return None, "no_data", "history.jsonl 窗口内零样本"
    return latest_in_window, "history.jsonl", None


def _kind_alert_summary(
    kind: str,
    live_alerts: list[dict[str, Any]],
    window_alerts: list[dict[str, Any]],
    *,
    include_window: bool,
) -> tuple[bool, int, list[dict[str, Any]]]:
    """某个 alert kind 的"是否出现过 + confirmed 计数(去重 fingerprint) + 证据样本"。

    ``ever_seen=False`` 是"该 kind 从未出现过一次(哪怕是 observed)"的信号, 用来区分
    "真测过、结果是零"(该判 green)与"这个检测根本没在产数据"(该判 no_data), 呼应本文件
    既有的 ``_finding_subscriber_kick`` 同款纪律。

    ``include_window=False`` 时只看 ``live_alerts``(latest.json 当前活跃快照, 对应
    "window: current" 语义); ``True`` 时并入 ``window_alerts``(alerts.jsonl 水位线窗口
    缓冲, 对应"window: 24h" 语义, 能抓到扫描间隙里已经 resolved 的确认事件)。
    """
    live_matches = [a for a in live_alerts if a.get("kind") == kind]
    window_matches = [r for r in window_alerts if r.get("kind") == kind] if include_window else []
    ever_seen = bool(live_matches) or bool(window_matches)

    confirmed_fps: set[str] = set()
    evidence: list[dict[str, Any]] = []
    for a in live_matches:
        if a.get("status") != "confirmed":
            continue
        fp = a.get("fingerprint") or a.get("subject")
        if fp:
            confirmed_fps.add(fp)
        evidence.append({"subject": a.get("subject"), "status": a.get("status"), "message": a.get("message")})
    for r in window_matches:
        if r.get("status") != "confirmed":
            continue
        fp = r.get("fingerprint") or r.get("subject")
        if fp:
            confirmed_fps.add(fp)

    return ever_seen, len(confirmed_fps), evidence[:MAX_EVIDENCE_SAMPLES]


def _read_latest_resource_snapshot(data_dir: Path) -> dict[str, Any] | None:
    """全量读 resource_monitor/latest.json(77KB 量级, 每 scan 一次可接受)。"""
    path = data_dir / "latest.json"
    if not path.exists():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _load_resource_policy_config(path: Path) -> dict[str, Any]:
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as e:
        logger.warning("resource-policy.yaml 读取失败(%s): %s", path, e)
        return {}


def _load_ledger_remediation(ledger_events_path: Path, window_start: float) -> tuple[bool, set[str]]:
    """只尾读 ledger events.jsonl, 找 type=perf.remediation 的降级留痕。

    resource-policy.yaml 设计: 一切 priority_enforcer 自动动作必须记一笔
    ``type=perf.remediation`` 到 ``data/ledger/events.jsonl``。2026-08-17 核实全仓
    grep 无 priority_enforcer/deprioritize_only/below_normal 实现落点, 该函数几乎总是
    返回 ``(False, set())`` -- 如实反映"无法排除已降级样本", 不编造已核实的 0。
    一旦 enforcer 真的落地并开始记账, 这里自动切到真实判定, 不需要再改本文件。
    事件 schema 未定, meta/inputs/outputs 里但凡出现形如 ``agent:<id>`` 等前缀的字符串
    都当作候选组 id 收进 demoted 集合, 这是保守的最佳猜测, 已在函数文档里标注。
    """
    if not ledger_events_path.exists():
        return False, set()
    try:
        with ledger_events_path.open("rb") as fh:
            fh.seek(0, 2)
            size = fh.tell()
            fh.seek(max(0, size - LEDGER_TAIL_BYTES))
            tail = fh.read().decode("utf-8", errors="replace")
    except OSError:
        return False, set()

    ever_seen = False
    demoted: set[str] = set()
    for ln in tail.splitlines():
        ln = ln.strip()
        if not ln:
            continue
        try:
            obj = json.loads(ln)
        except json.JSONDecodeError:
            continue
        if not isinstance(obj, dict) or obj.get("type") != ENFORCER_REMEDIATION_LEDGER_TYPE:
            continue
        ever_seen = True
        meta = obj.get("meta") or {}
        for key in ("group_id", "gid", "target_group_id"):
            value = meta.get(key)
            if isinstance(value, str):
                demoted.add(value)
        for value in list(obj.get("inputs") or []) + list(obj.get("outputs") or []):
            if isinstance(value, str) and value.startswith(_GROUP_ID_PREFIXES):
                demoted.add(value)
    return ever_seen, demoted


def _load_cc_sessions_lookup(path: Path) -> dict[str, Any] | None:
    """cc_sessions.json 是扁平 dict(session_id → {name, started_at, active_plan, ...})。

    单次全量点查(见模块 docstring), 超过 ``MAX_CC_SESSIONS_LOOKUP_BYTES`` 直接跳过而不是
    硬扛一次超大 parse -- 届时 provenance 相关字段统一标 unknown, 不猜测。
    """
    try:
        if not path.exists() or path.stat().st_size > MAX_CC_SESSIONS_LOOKUP_BYTES:
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _provenance_for_group(group: dict[str, Any], cc_sessions: dict[str, Any] | None) -> dict[str, Any]:
    """resource-policy.yaml provenance.required_fields: provider/session_id/session_name/

    started_at/active_plan/root_pid。采不到就填 "unknown", 绝不编造。
    """
    owner = group.get("owner") or {}
    session_id = owner.get("session_id")
    prov: dict[str, Any] = {
        "provider": owner.get("provider") or "unknown",
        "session_id": session_id or "unknown",
        "root_pid": owner.get("root_pid") if owner.get("root_pid") is not None else "unknown",
        "session_name": "unknown",
        "started_at": "unknown",
        "active_plan": "unknown",
    }
    record = (cc_sessions or {}).get(session_id) if session_id else None
    if isinstance(record, dict):
        prov["session_name"] = record.get("name") or "unknown"
        started_at = record.get("started_at")
        prov["started_at"] = started_at if started_at is not None else "unknown"
        prov["active_plan"] = record.get("active_plan") or "unknown"
    return prov


def _build_by_owner(groups: list[dict[str, Any]], cc_sessions: dict[str, Any] | None) -> dict[str, Any]:
    """top CPU / top RSS 的 agent-session 组, 每行带 provenance(见 resource-policy.yaml)。"""
    agent_groups = [g for g in (groups or []) if g.get("role") == "agent-session"]
    top_cpu = _top_n(
        [
            {"key": g.get("id"), "value": g.get("cpu_percent") or 0,
             "provenance": _provenance_for_group(g, cc_sessions)}
            for g in agent_groups
        ],
        "value",
    )
    top_rss = _top_n(
        [
            {"key": g.get("id"), "value": g.get("rss_bytes") or 0,
             "provenance": _provenance_for_group(g, cc_sessions)}
            for g in agent_groups
        ],
        "value",
    )
    return {"top_cpu": top_cpu, "top_rss": top_rss}


_NOTIFY_CRITICAL_SLO_IDS = {"runaway-idle-sessions", "sustained-cpu-hog-unmanaged"}


def _maybe_notify_critical(findings: list[dict[str, Any]], *, skip: bool) -> None:
    """runaway-idle-sessions / sustained-cpu-hog-unmanaged 判红时推驾驶舱铃铛。

    orphan-dev-services 有意不在此列(resource-policy.yaml 里它的告警 severity 是
    warning, 不是 runaway_idle_detection 那条声明的 critical)。推送失败 fail-open,
    绝不能挡 scan 主流程; 测试传 ``skip=True`` 避免写生产铃铛文件。
    """
    if skip:
        return
    reds = [f for f in findings if f["slo_id"] in _NOTIFY_CRITICAL_SLO_IDS and f["level"] == "red"]
    if not reds:
        return
    try:
        from omnicompany.packages.services.boss_sight.services import agent_notify
        titles = ", ".join(f["slo_id"] for f in reds)
        agent_notify.push_notice(
            f"性能巡检判红: {titles}",
            open_ref={"type": "project", "id": "perf-observatory"},
            body="资源治理 SLO 违规, 详见 omni guardian perf latest",
        )
    except Exception as e:  # noqa: BLE001 - 推送铃铛失败不能挡巡检主流程(fail-open)
        logger.debug("perf scan critical notify 推送失败(非致命): %s", e)


def _load_resource_monitor_meta(data_dir: Path) -> dict[str, Any]:
    """只读汇总, 不驱动任何 SLO 判定(见模块 docstring)。失败要 fail-open, 不影响 scan。"""
    result: dict[str, Any] = {"available": False}
    try:
        latest_path = data_dir / "latest.json"
        if latest_path.exists():
            latest = json.loads(latest_path.read_text(encoding="utf-8"))
            system = latest.get("system") or {}
            result["available"] = True
            result["latest_sample"] = {
                "process_count": system.get("process_count"),
                "memory_percent": system.get("memory_percent"),
                "cpu_percent": system.get("cpu_percent"),
            }
        alerts_path = data_dir / "alerts.jsonl"
        if alerts_path.exists():
            # 只尾读最后 200 行统计确认告警数, 不整读大文件
            with alerts_path.open("rb") as fh:
                fh.seek(0, 2)
                size = fh.tell()
                fh.seek(max(0, size - 200_000))
                tail = fh.read().decode("utf-8", errors="replace")
            confirmed = 0
            for ln in tail.splitlines():
                ln = ln.strip()
                if not ln:
                    continue
                try:
                    obj = json.loads(ln)
                except json.JSONDecodeError:
                    continue
                if obj.get("status") == "confirmed":
                    confirmed += 1
            result["recent_confirmed_alerts_tail"] = confirmed
    except Exception as e:  # noqa: BLE001 - 辅助 meta, 任何异常都不能影响主判定
        logger.debug("resource_monitor meta 读取失败(非致命): %s", e)
    return result


def _write_report(out_dir: Path, ts_iso: str, report: dict[str, Any]) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    report_path = out_dir / f"perf-scan-{ts_iso.replace(':', '-')}.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    try:
        from omnicompany.core.omnimark import write_data_sidecar
        write_data_sidecar(
            report_path,
            written_by=f"{PerfScanWorker.__module__}.{PerfScanWorker.__name__}",
            source_path=__file__,
            ttl_days=14,
        )
    except Exception as e:  # noqa: BLE001 - sidecar 是附加身份标记, 不能挡主流程
        logger.debug("perf-scan report sidecar 写入失败(非致命): %s", e)
    latest_path = out_dir / "latest.json"
    try:
        latest_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError as e:
        logger.warning("perf-scan latest.json 写入失败: %s", e)
    return report_path


def _iso(ts: float) -> str:
    from datetime import datetime, timezone
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()


def _default_project_root() -> Path:
    from omnicompany.core.config import _project_root
    return _project_root()


__all__ = ["PerfScanWorker"]
