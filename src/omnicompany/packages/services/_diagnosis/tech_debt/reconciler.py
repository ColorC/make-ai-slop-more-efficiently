# [OMNI] origin=kimi-code domain=services/tech_debt ts=2026-07-26T00:00:00Z
# [OMNI] material_id="material:diagnosis.tech_debt.registry_reconciler.py"
"""tech_debt.reconciler — REGISTRY §活跃违规 的自动对账（批量销账闭环）。

背景（2026-07-26）：registry_updater 只写不销，open 积压 6 万条。
本模块实现三类自动销账判据（仅处理 §活跃违规 中 status=open 且
rule_id 以 OMNI- 开头的条目；OVERSEER 等人工条目不碰）：

  1. path_gone           条目路径已不存在于磁盘
  2. not_hit_in_N_scans  连续 N 轮巡逻未再命中（判据：guardian/registry_updater
                         维护的 var/tech_debt/scan_state.json 里
                         scan_round - last_hit >= N，N 默认 3）
  3. domain_whitelisted  域级白名单裁决（见 RESOLVE_WHITELIST，如 OMNI-030 ×
                         bilibili_publish episodes 版本化工作流裁决 2026-07-26）

销账动作对齐 `omni debt resolve` 的既有协议：移到 §已解决 + 记 ARCH-CHANGES
事件；批量时聚合为一条 violation-resolved 事件（数量+样本进 payload，全量明细
落 docs/tech_debt/reconcile-runs/reconcile-<ts>.json）。

§已解决 只保留最近 30 条正文的既有约定：批量移动时溢出的最老明细归档到
docs/tech_debt/resolved-archive-<YYYYMM>.md，REGISTRY 不再膨胀。

性能约定：REGISTRY 可达 6 万行，读写按行流式处理（逐行写临时文件再 replace），
不做全文件字符串拼接。

不做：
  - 扫描（guardian 的职责）
  - 自动修复违规本身
"""
from __future__ import annotations

import json
import logging
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .events import append_event
from .registry_io import (
    SECTION_SPECS,
    _RESOLVED_COLUMNS,
    _find_section_table,
    _format_row_generic,
    _parse_row_generic,
)

logger = logging.getLogger(__name__)

_REGISTRY_RELPATH = "docs/tech_debt/REGISTRY.md"
_RESOLVED_SECTION = "## §已解决"
_BACKLOG_SUMMARY_PREFIX = "> **积压统计"
_SEVERITY_ORDER = ("CRITICAL", "HIGH", "MEDIUM", "LOW", "INFO")

# §已解决 正文只保留最近 N 条（REGISTRY 节标题「## §已解决（最近 30 条）」的既有约定）
DEFAULT_KEEP_RESOLVED = 30
# not_hit_in_N_scans 默认轮数
DEFAULT_NOT_HIT_ROUNDS = 3

# ─── 域级白名单裁决 ─────────────────────────────────────────────
# 每条：(rule_id, path_pattern, reason)。命中即批量 resolve，reason 注明裁决与日期。
# path_pattern 不含 "*" 时按前缀匹配；含 "*" 时按 fnmatch 全路径匹配；
# 空前缀 "" 表示该规则整规则销账（仅用于「未复核粗筛候选」类裁决）。
RESOLVE_WHITELIST: tuple[tuple[str, str, str], ...] = (
    (
        "OMNI-030",
        "src/omnicompany/packages/domains/bilibili_publish/episodes/",
        "domain_whitelisted: bilibili_publish episodes 版本化工作流裁决 2026-07-26",
    ),
    # ── 2026-07-26 技术债 5.8 万条积压裁决 ──
    # OMNI-055: 快照树/产物目录豁免（规则已修, 见 rules/location.py）
    ("OMNI-055", "data/_workspaces/",
     "rule_fixed: team_builder worktree 快照树规则已豁免, 存量过期误报 2026-07-26"),
    ("OMNI-055", "data/services/repo_exporter/jobs/",
     "rule_fixed: 导出暂存快照属数据产物, 规则 2026-07-26 补豁免"),
    ("OMNI-055", "data/domains/*/references/*",
     "rule_fixed: references/ 上游源码规则已豁免, 存量过期误报 2026-07-26"),
    ("OMNI-055", "*/scratch/*",
     "rule_fixed: scratch 自由区规则已豁免(/scratch/), 存量过期误报 2026-07-26"),
    ("OMNI-055", "data/_scratch/",
     "rule_fixed: scratch 自由区(下划线变体)规则 2026-07-26 补 _is_scratch 豁免"),
    ("OMNI-055", "data/services/doctor/repair/backups/",
     "rule_fixed: doctor 修复备份属数据产物, 规则 2026-07-26 补豁免"),
    ("OMNI-055", "data/services/workflow_factory/output/",
     "rule_fixed: workflow_factory 生成脚手架属数据产物, 规则 2026-07-26 补豁免"),
    # OMNI-001: 判法锚定 src/omnicompany/packages/ 后, data/ 快照树不再扫
    ("OMNI-001", "data/",
     "rule_fixed: OmniMark 规则锚定活包树, data/ 快照树误报存量 2026-07-26"),
    # OMNI-041: archmap 加载 off-by-one 修复后 domains/ 白名单生效
    ("OMNI-041", "data/domains/*/references/*",
     "rule_fixed: archmap 定位 off-by-one 致白名单失效, 修复后 domains/ 合法 2026-07-26"),
    # OMNI-007: i18n 资产 / SKILL.md / 就近 prompt 素材判法放行 + episodes 域白名单
    ("OMNI-007", "*/i18n/locales/*",
     "rule_fixed: 前端 i18n 语言资源系标准就近资产, 规则 2026-07-26 放行"),
    ("OMNI-007", "*SKILL.md",
     "rule_fixed: SKILL.md 技能包标准文档, 规则 2026-07-26 放行"),
    ("OMNI-007", "*_prompt.md",
     "rule_fixed: 就近 prompt 素材(native_agent_prompt.md 先例), 规则 2026-07-26 放行"),
    ("OMNI-007", "*/prompts/*.md",
     "rule_fixed: prompts/ 目录就近 prompt 素材, 规则 2026-07-26 放行"),
    ("OMNI-007", "*/_test_fixtures/*",
     "rule_fixed: 测试夹具就近存放, 规则 2026-07-26 放行"),
    ("OMNI-007", "*/fixtures/*",
     "rule_fixed: 测试夹具就近存放, 规则 2026-07-26 放行"),
    ("OMNI-007", "src/omnicompany/packages/domains/bilibili_publish/episodes/",
     "domain_whitelisted: 同 OMNI-030 episodes 版本化工作流裁决 2026-07-26"),
    # OMNI-030: node_modules 过期误报 + 快照/隔离区豁免
    ("OMNI-030", "src/omnicompany/dashboard/frontend/node_modules/",
     "rule_fixed: node_modules 规则已豁免, 存量过期误报 2026-07-26"),
    ("OMNI-030", "data/_workspaces/",
     "rule_fixed: worktree 快照树命名纪律不适用, 规则 2026-07-26 补豁免"),
    ("OMNI-030", "data/services/repo_exporter/jobs/",
     "rule_fixed: 导出快照命名纪律不适用, 规则 2026-07-26 补豁免"),
    ("OMNI-030", ".omni/quarantine/",
     "rule_fixed: 隔离区 holding zone 命名纪律不适用, 规则 2026-07-26 补豁免"),
    # OMNI-037/040: 锚定活包树后 data/ 快照树不再扫
    ("OMNI-037", "data/",
     "rule_fixed: Material kind 规则锚定活包树, data/ 快照树误报存量 2026-07-26"),
    ("OMNI-040", "data/",
     "rule_fixed: Stage3 规则锚定活 services 树, data/ 快照树误报存量 2026-07-26"),
    # 未复核粗筛候选整规则销账 (2026-07-26 管线裁决:
    # registry_updater 起 needs_judgment 候选未经 GuardianAgent 复核不再入账;
    # 以下存量全部未带 reviewed_by, 非已确认违规. 后续复核 confirmed 的会重新入账)
    ("OMNI-004", "",
     "unjudged_candidates: needs_judgment 未复核候选批量销账, 管线 2026-07-26 修"),
    ("OMNI-070", "",
     "unjudged_candidates: needs_judgment 未复核候选批量销账, 管线 2026-07-26 修"),
    ("OMNI-072", "",
     "unjudged_candidates: needs_judgment 未复核候选批量销账, 管线 2026-07-26 修"),
    ("OMNI-073", "",
     "unjudged_candidates: needs_judgment 未复核候选批量销账, 管线 2026-07-26 修"),
    ("OMNI-074", "",
     "unjudged_candidates: needs_judgment 未复核候选批量销账, 管线 2026-07-26 修"),
    ("OMNI-035f2", "",
     "unjudged_candidates: needs_judgment 未复核候选批量销账, 管线 2026-07-26 修"),
    ("OMNI-080", "",
     "unjudged_candidates: certainty 修正为 needs_judgment, 未复核候选批量销账 2026-07-26"),
)

_ACTIVITY_SPEC = next(s for s in SECTION_SPECS if s.name == "activity")

# CJK / 全角字符：path 列里出现即视为描述文本而非路径（如 OVERSEER 手工条目）
_CJK_RE = re.compile(r"[⺀-鿿豈-﫿＀-￿]")
_EXT_RE = re.compile(r"\.[A-Za-z0-9]{1,8}$")


def _normalize_path(p: str) -> str:
    return p.strip().strip('"').strip("'").strip().replace("\\", "/")


def _looks_like_relpath(p: str) -> bool:
    """path 列是否像仓库相对路径（保守：不像就不参与 path_gone 判定）。"""
    if not p or p in ("—", "-"):
        return False
    if any(ch.isspace() for ch in p):
        return False
    if _CJK_RE.search(p):
        return False
    return ("/" in p) or bool(_EXT_RE.search(p))


def _whitelist_reason(rule_id: str, norm_path: str) -> str | None:
    from fnmatch import fnmatch

    for w_rule, w_pattern, w_reason in RESOLVE_WHITELIST:
        if rule_id != w_rule:
            continue
        if "*" in w_pattern:
            if fnmatch(norm_path, w_pattern):
                return w_reason
        elif norm_path.startswith(w_pattern):
            return w_reason
    return None


def _decide(
    row: dict[str, str],
    root: Path,
    scan_state: dict,
    not_hit_rounds: int,
    path_exists_cache: dict[str, bool],
) -> str | None:
    """对一条 open OMNI 行给出销账 reason；不销返回 None。

    判据优先级：domain_whitelisted → path_gone → not_hit_in_N_scans。
    """
    from omnicompany.packages.services._core.guardian.registry_updater import (
        scan_state_key,
    )

    rule_id = row.get("rule_id", "")
    norm_path = _normalize_path(row.get("path", ""))

    reason = _whitelist_reason(rule_id, norm_path)
    if reason is not None:
        return reason

    # rename 对路径 (2026-07-26): git diff 扫描把改名记为 "old\tnew" 同格，
    # 含空白难过 _looks_like_relpath；取旧路径段做存在性检查 —
    # 旧路径已不在 = 该违规记录随改名失效，按 path_gone 销账
    exist_path = norm_path.split("\t", 1)[0].strip().strip('"').strip("'").strip()
    if exist_path and _looks_like_relpath(exist_path):
        if exist_path not in path_exists_cache:
            path_exists_cache[exist_path] = (root / exist_path).exists()
        if not path_exists_cache[exist_path]:
            return "path_gone"

    last_hit = scan_state.get("last_hit") or {}
    try:
        scan_round = int(scan_state.get("scan_round", 0))
    except (TypeError, ValueError):
        scan_round = 0
    key = scan_state_key(rule_id, row.get("path", ""))
    hit_round = last_hit.get(key)
    if hit_round is None:
        return None  # 无命中记录（状态文件建立前的存量）→ 保守不销
    try:
        if scan_round - int(hit_round) >= not_hit_rounds:
            return f"not_hit_in_{not_hit_rounds}_scans"
    except (TypeError, ValueError):
        return None
    return None


def _format_backlog_summary(open_rows: list[dict[str, str]]) -> str:
    """刷新积压统计行（与 registry_updater 同一行格式，标 reconcile 来源）。"""
    counts: dict[str, int] = {}
    for r in open_rows:
        sev = r.get("severity", "")
        counts[sev] = counts.get(sev, 0) + 1
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    parts = " · ".join(
        f"{sev}:{counts[sev]}" for sev in _SEVERITY_ORDER if counts.get(sev)
    )
    other = sum(v for k, v in counts.items() if k not in _SEVERITY_ORDER)
    if other:
        parts = f"{parts} · 其他:{other}" if parts else f"其他:{other}"
    detail = f" · {parts}" if parts else ""
    return (
        f"> **积压统计(reconcile 刷新 {today})**: open 共 {len(open_rows)} 条{detail}"
        " · 处置后把状态列改为 resolved 并按编辑协议移至 §已解决"
    )


def _append_archive(root: Path, overflow_lines: list[str], today: str) -> str | None:
    """把 §已解决 溢出的最老明细追加到 resolved-archive-<YYYYMM>.md。"""
    if not overflow_lines:
        return None
    yyyymm = today[:7].replace("-", "")
    archive_path = root / "docs" / "tech_debt" / f"resolved-archive-{yyyymm}.md"
    try:
        if not archive_path.exists():
            archive_path.write_text(
                "<!-- [OMNI] origin=tech_debt domain=docs/tech_debt "
                f"ts={today}T00:00:00Z type=doc status=active -->\n\n"
                f"# §已解决 归档（{today[:7]}）\n\n"
                "> REGISTRY.md §已解决 只保留最近 "
                f"{DEFAULT_KEEP_RESOLVED} 条，溢出的最老明细按月归档于此。\n\n"
                "| ID | 类型 | 解决日期 | 解决方式 |\n"
                "|---|---|---|---|\n",
                encoding="utf-8",
            )
        with archive_path.open("a", encoding="utf-8") as fh:
            for line in overflow_lines:
                fh.write(line.rstrip("\n") + "\n")
        return str(archive_path.relative_to(root)).replace("\\", "/")
    except OSError as e:
        logger.warning("reconciler: 归档写入失败 %s: %s", archive_path, e)
        return None


def reconcile_registry(
    project_root: str | Path,
    *,
    apply: bool = False,
    not_hit_rounds: int = DEFAULT_NOT_HIT_ROUNDS,
    keep_resolved: int = DEFAULT_KEEP_RESOLVED,
    sample_n: int = 10,
    registry_relpath: str = _REGISTRY_RELPATH,
) -> dict[str, Any]:
    """对账 REGISTRY §活跃违规：批量销账 path_gone / not_hit / 白名单条目。

    dry-run（apply=False，默认）：只统计 + 采样，不改任何文件、不记事件。

    Returns: 统计 dict（open_before/open_after/by_reason/samples/事件与明细路径）。
    """
    from omnicompany.packages.services._core.guardian.registry_updater import (
        load_scan_state,
    )

    root = Path(project_root)
    registry_path = root / registry_relpath
    result: dict[str, Any] = {
        "apply": apply,
        "not_hit_rounds": not_hit_rounds,
        "open_before": 0,
        "open_after": 0,
        "resolved_total": 0,
        "by_reason": {},
        "archived_overflow": 0,
        "archive_file": None,
        "detail_file": None,
        "arch_event_id": None,
        "error": None,
    }
    if not registry_path.exists():
        result["error"] = f"REGISTRY.md 不存在: {registry_path}"
        return result

    try:
        with registry_path.open("r", encoding="utf-8") as fh:
            lines = fh.readlines()
    except (OSError, UnicodeError) as e:
        result["error"] = f"读 REGISTRY.md 失败: {e}"
        return result

    # ── 第一遍：定位表格 + 逐行判定（只存行号与行 dict，不拼字符串） ──
    active_span = _find_section_table(lines, _ACTIVITY_SPEC.header)
    resolved_span = _find_section_table(lines, _RESOLVED_SECTION)
    if active_span is None or resolved_span is None:
        result["error"] = "§活跃违规 或 §已解决 表格未找到"
        return result
    act_header, act_end = active_span
    res_header, res_end = resolved_span
    act_data_start = act_header + 2
    res_data_start = res_header + 2

    summary_idx = next(
        (i for i, ln in enumerate(lines[:act_header])
         if ln.strip().startswith(_BACKLOG_SUMMARY_PREFIX)),
        None,
    )

    scan_state = load_scan_state(root)
    path_exists_cache: dict[str, bool] = {}

    open_rows: list[dict[str, str]] = []          # 销账后仍 open 的行（刷统计用）
    to_resolve: list[tuple[int, dict[str, str], str]] = []  # (行号, row, reason)
    for i in range(act_data_start, act_end):
        row = _parse_row_generic(lines[i], _ACTIVITY_SPEC.columns)
        if row is None:
            continue
        if row.get("status") != "open":
            continue
        if row.get("rule_id", "").startswith("OMNI-"):
            reason = _decide(row, root, scan_state, not_hit_rounds, path_exists_cache)
            if reason is not None:
                to_resolve.append((i, row, reason))
                continue
        open_rows.append(row)

    result["open_before"] = len(open_rows) + len(to_resolve)
    result["open_after"] = len(open_rows)
    result["resolved_total"] = len(to_resolve)

    # 统计 + 样本
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    by_reason: dict[str, dict[str, Any]] = {}
    for _, row, reason in to_resolve:
        bucket = by_reason.setdefault(reason, {"count": 0, "samples": []})
        bucket["count"] += 1
        if len(bucket["samples"]) < sample_n:
            bucket["samples"].append({
                "id": row.get("id", ""),
                "rule_id": row.get("rule_id", ""),
                "path": row.get("path", ""),
            })
    result["by_reason"] = by_reason

    if not apply or not to_resolve:
        return result

    # ── 第二遍：流式写临时文件（跳销账行、刷统计行、追加 §已解决、溢出归档） ──
    skip_lines = {i for i, _, _ in to_resolve}

    existing_resolved: list[tuple[int, str]] = []  # (行号, 原行文本)
    for i in range(res_data_start, res_end):
        if _parse_row_generic(lines[i], _RESOLVED_COLUMNS) is not None:
            existing_resolved.append((i, lines[i]))

    new_resolved_lines = [
        _format_row_generic(
            {
                "id": row.get("id", ""),
                "kind": "activity",
                "resolved_date": today,
                "how": f"{reason} — {_normalize_path(row.get('path', ''))}",
            },
            _RESOLVED_COLUMNS,
        )
        for _, row, reason in to_resolve
    ]

    # §已解决 只留最近 keep_resolved 条：既有行 + 新行合并后保留末尾 N 条
    total_resolved = len(existing_resolved) + len(new_resolved_lines)
    overflow_n = max(0, total_resolved - keep_resolved)
    overflow_lines: list[str] = []
    kept_existing = existing_resolved
    kept_new = new_resolved_lines
    if overflow_n > 0:
        all_lines = [text for _, text in existing_resolved] + new_resolved_lines
        overflow_lines = all_lines[:overflow_n]
        kept_lines = all_lines[overflow_n:]
        # kept_lines 前段来自既有行（已在文件里），后段是新行（待插入）
        kept_existing_n = len(existing_resolved) - min(overflow_n, len(existing_resolved))
        drop_existing = {i for i, _ in existing_resolved[: len(existing_resolved) - kept_existing_n]}
        skip_lines |= drop_existing
        kept_existing = [p for p in existing_resolved if p[0] not in drop_existing]
        kept_new = kept_lines[len(kept_existing):]
        result["archived_overflow"] = len(overflow_lines)

    tmp_path = registry_path.with_name(registry_path.name + ".reconcile-tmp")
    try:
        with tmp_path.open("w", encoding="utf-8", newline="") as fout:
            for idx, line in enumerate(lines):
                if idx in skip_lines:
                    continue
                if idx == summary_idx:
                    fout.write(_format_backlog_summary(open_rows) + "\n")
                    continue
                if idx == res_end:
                    # 在 §已解决 表格末尾（第一个非表格行之前）插入新行
                    for nl in kept_new:
                        fout.write(nl + "\n")
                fout.write(line)
            if res_end >= len(lines):
                # §已解决 表格在文件末尾
                if lines and not lines[-1].endswith("\n"):
                    fout.write("\n")
                for nl in kept_new:
                    fout.write(nl + "\n")
        os.replace(tmp_path, registry_path)
    except OSError as e:
        result["error"] = f"写 REGISTRY.md 失败: {e}"
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            pass
        return result

    # 溢出归档
    result["archive_file"] = _append_archive(root, overflow_lines, today)

    # 全量明细落盘（事件里只带数量+样本）
    detail_rel = None
    try:
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
        detail_path = root / "docs" / "tech_debt" / "reconcile-runs" / f"reconcile-{ts}.json"
        detail_path.parent.mkdir(parents=True, exist_ok=True)
        detail_path.write_text(
            json.dumps(
                {
                    "ts": ts,
                    "resolved_total": len(to_resolve),
                    "rows": [
                        {
                            "id": row.get("id", ""),
                            "rule_id": row.get("rule_id", ""),
                            "path": row.get("path", ""),
                            "reason": reason,
                        }
                        for _, row, reason in to_resolve
                    ],
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        detail_rel = str(detail_path.relative_to(root)).replace("\\", "/")
        result["detail_file"] = detail_rel
    except OSError as e:
        logger.warning("reconciler: 明细落盘失败: %s", e)

    # 聚合 ARCH-CHANGES 事件（一条 violation-resolved 带数量+样本，明细落 detail_file）
    reason_summary = " ".join(f"{r}={b['count']}" for r, b in sorted(by_reason.items()))
    ev = append_event(
        root,
        event_type="violation-resolved",
        initiator="tech_debt",
        drawer="services/tech_debt",
        related_pipeline="",
        change=(
            f"reconcile 批量销账 {len(to_resolve)} 条（{reason_summary}）"
            f" open {result['open_before']} → {result['open_after']}"
        ),
        payload={
            "reconcile": True,
            "resolved_total": len(to_resolve),
            "by_reason": {r: b["count"] for r, b in sorted(by_reason.items())},
            "samples": {
                r: b["samples"][:5] for r, b in sorted(by_reason.items())
            },
            "detail_file": detail_rel,
            "archive_file": result["archive_file"],
            "archived_overflow": result["archived_overflow"],
        },
    )
    if ev is not None:
        result["arch_event_id"] = ev.change_id

    return result
