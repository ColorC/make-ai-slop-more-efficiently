# [OMNI] origin=claude-code domain=services/guardian/workers ts=2026-04-25T00:00:00Z type=router
# [OMNI] material_id="material:guardian.human_readable_report.generator.worker.py"
"""GuardianReportWorker · 人类一手观察接口 · LLM 翻译产中文 markdown.

**用户 2026-04-25 反馈**: v1 骨架版"极为抽象, 看不懂代号"; "需要 LLM 处理 — 把规则代号 +
原始 evidence + patrol 标签翻译成易懂中文; 不要假设用户熟悉 guardian 机制".

**v2 设计**:
- Worker 内部仍读多源**一手数据** (规则扫描 / patrol 报告 / audit 判定 / docauthor 队列)
- 把 60 条 GuardianRule 原文定义喂给 LLM 作"规则字典"
- LLM 用中文把每条违规翻译为: 规则是什么 + 这次哪里违规 + 为什么严重 + 怎么改
- 不保留 OMNI-049 / Stage3-OK / dirty 等代号给用户读 (仍在 evidence 段保留供溯源)
- 中文为主, 仅文件路径 / 命令 / 类名保留原英文

输出: `data/services/guardian/reports/report-<ts>.md` + `latest.md`.

**铁律遵守**:
- 不打分 (LLM 不算 health_score)
- 不二手转述 — 每节给"翻译" + "原始证据" 两段, 让用户能溯源
- 不编造 — LLM 必须基于喂入的原始 evidence, 不生造路径/规则
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from omnicompany.packages.services._core.omnicompany import Worker, call_llm_json
from omnicompany.protocol.anchor import Verdict, VerdictKind


def _default_repo_root() -> Path:
    return Path(__file__).resolve().parents[6]


class GuardianReportWorker(Worker):
    """LLM 翻译版守护一手观察报告 · 单命令 · 非常驻 · qwen-3.6-plus."""

    DESCRIPTION = (
        "聚合 guardian 多源一手数据 (规则扫描 + LLM patrol + audit 判定 + docauthor 队列) → "
        "喂规则字典给 qwen-3.6-plus 翻译为中文人类可读 markdown · 落 "
        "data/services/guardian/reports/<ts>.md + latest.md. 不保留 OMNI-XXX/Stage3-OK/dirty "
        "等代号给用户读 · 每节'翻译 + 原始证据'双层 · 不打分."
    )
    FORMAT_IN = "guardian.report-request"
    FORMAT_OUT = "guardian.report-output"

    def __init__(
        self,
        *,
        repo_root: Path | None = None,
        recent_patrol_count: int = 3,
        recent_audit_count: int = 30,
        web_bus: Any = None,
    ) -> None:
        self._repo_root = (repo_root or _default_repo_root()).resolve()
        self._recent_patrol_count = recent_patrol_count
        self._recent_audit_count = recent_audit_count
        self._web_bus = web_bus

    # ─────────────────────────────────────────────────────────────

    def run(self, input_data: dict[str, Any]) -> Verdict:
        repo = self._repo_root

        # 1. 规则扫描 (原始数据)
        from omnicompany.packages.services._core.guardian.rules.runtime_hygiene import (
            scan_data_subdir_violations, scan_aging_items, scan_volume_alerts,
            scan_empty_dirs,
        )
        try:
            rule_scan = {
                "OMNI-051a_undeclared_subdirs": list(scan_data_subdir_violations(repo)),
                "OMNI-049_aged_files":          list(scan_aging_items(repo)),
                "OMNI-050_oversize_files":      list(scan_volume_alerts(repo)),
                "empty_dirs":                   list(scan_empty_dirs(repo)),
            }
        except Exception as e:
            return Verdict(kind=VerdictKind.FAIL,
                           diagnosis=f"规则扫描失败: {type(e).__name__}: {e}")
        rule_violation_total = sum(len(v) for v in rule_scan.values())

        # 2. LLM patrol 一手 (取最近 N · 全文喂 LLM)
        patrol_dir = repo / "data/services/guardian/patrol"
        patrol_reports: list[dict] = []
        if patrol_dir.exists():
            md_files = sorted(patrol_dir.glob("*.md"),
                              key=lambda p: p.stat().st_mtime, reverse=True)
            for p in md_files[: self._recent_patrol_count]:
                try:
                    text = p.read_text(encoding="utf-8", errors="replace")
                except OSError:
                    text = "(读不到)"
                patrol_reports.append({
                    "rel_path": p.relative_to(repo).as_posix(),
                    "ts": datetime.fromtimestamp(p.stat().st_mtime, tz=timezone.utc).isoformat(),
                    "size_bytes": p.stat().st_size,
                    "content": text,
                })

        # 3. GuardianAuditStore 一手
        audit_path = repo / "data/services/guardian/audit/records.jsonl"
        audit_records: list[dict] = []
        verdict_counts = {"confirmed": 0, "dismissed": 0, "uncertain": 0, "_other": 0}
        if audit_path.exists():
            try:
                lines = [l.strip() for l in audit_path.read_text(encoding="utf-8", errors="replace").splitlines() if l.strip()]
                for line in lines:
                    try:
                        rec = json.loads(line)
                        v = (rec.get("verdict") or "_other").lower()
                        verdict_counts[v if v in verdict_counts else "_other"] += 1
                    except json.JSONDecodeError:
                        verdict_counts["_other"] += 1
                for line in lines[-self._recent_audit_count:][::-1]:
                    try:
                        audit_records.append(json.loads(line))
                    except json.JSONDecodeError:
                        pass
            except OSError:
                pass

        # 4. DocAuthor 工作队列
        quarantine_dir = repo / "data/services/docauthor/drafts/_quarantine"
        quarantine_queue: list[dict] = []
        if quarantine_dir.exists():
            for slug_dir in sorted(quarantine_dir.iterdir()):
                if not slug_dir.is_dir():
                    continue
                issues_file = slug_dir / "issues.json"
                if issues_file.exists():
                    try:
                        q_data = json.loads(issues_file.read_text(encoding="utf-8"))
                        quarantine_queue.append({
                            "slug": slug_dir.name,
                            "target_path": q_data.get("target_path"),
                            "target_type": q_data.get("target_type"),
                            "counts": q_data.get("counts", {}),
                            "iter": q_data.get("iter"),
                            "issues_json_path": str(issues_file.relative_to(repo).as_posix()),
                        })
                    except (OSError, json.JSONDecodeError):
                        pass
        skeleton_designs, missing_manifests = _scan_docauthor_deficits(repo)

        # 5. 规则字典 (从 RULES 实时取定义喂 LLM)
        rule_dict = _build_rule_dictionary(rule_scan, audit_records)

        source_counts = {
            "rule_scan_violations": rule_violation_total,
            "patrol_reports": len(patrol_reports),
            "audit_records_recent": len(audit_records),
            "audit_records_total": sum(verdict_counts.values()),
            "docauthor_quarantine": len(quarantine_queue),
            "docauthor_skeleton_design": len(skeleton_designs),
            "docauthor_missing_manifest": len(missing_manifests),
        }

        # 6. LLM 翻译写中文 markdown
        ts_iso = datetime.now(timezone.utc).isoformat()
        try:
            md = _llm_render_markdown(
                ts_iso=ts_iso,
                rule_scan=rule_scan,
                patrol_reports=patrol_reports,
                audit_records=audit_records,
                audit_verdict_counts=verdict_counts,
                quarantine_queue=quarantine_queue,
                skeleton_designs=skeleton_designs,
                missing_manifests=missing_manifests,
                rule_dict=rule_dict,
                source_counts=source_counts,
                web_bus=self._web_bus,
            )
        except Exception as e:
            return Verdict(kind=VerdictKind.FAIL,
                           diagnosis=f"LLM 渲染失败: {type(e).__name__}: {e}")

        # 7. 落盘
        report_dir = repo / "data/services/guardian/reports"
        report_dir.mkdir(parents=True, exist_ok=True)
        ts_filename = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        report_path = report_dir / f"report-{ts_filename}.md"
        latest_path = report_dir / "latest.md"
        try:
            report_path.write_text(md, encoding="utf-8")
            latest_path.write_text(md, encoding="utf-8")
        except OSError as e:
            return Verdict(kind=VerdictKind.FAIL,
                           diagnosis=f"写报告失败: {type(e).__name__}: {e}")

        return Verdict(
            kind=VerdictKind.PASS,
            output={
                "report_path": str(report_path.relative_to(repo).as_posix()),
                "report_md": md,
                "source_counts": source_counts,
                "ts": ts_iso,
            },
            diagnosis=f"GuardianReport: {report_path.name} · {source_counts}",
        )


# ═══════════════════════════════════════════════════════════════════
# 规则字典 (从 RULES 实时构造)
# ═══════════════════════════════════════════════════════════════════

def _build_rule_dictionary(rule_scan: dict, audit_records: list[dict]) -> dict[str, dict]:
    """构造 {rule_id: {name, severity, description}} · 只含本次实际涉及的规则.

    数据源:
    - rule_scan key 里的 OMNI-XXX
    - audit_records 里出现的 rule_id
    - 加上 GuardianRule 原始定义的 description
    """
    from omnicompany.packages.services._core.guardian import RULES

    rule_lookup = {r.id: r for r in RULES}

    used_ids: set[str] = set()
    for key in rule_scan:
        # key 形如 "OMNI-051a_undeclared_subdirs" 或 "empty_dirs"
        if key.startswith("OMNI-"):
            used_ids.add(key.split("_", 1)[0])
    for rec in audit_records:
        rid = rec.get("rule_id")
        if rid:
            used_ids.add(rid)

    out: dict[str, dict] = {}
    for rid in used_ids:
        rule = rule_lookup.get(rid)
        if rule:
            out[rid] = {
                "id": rule.id,
                "name": rule.name,
                "severity": rule.severity,
                "description": rule.description,
                "certainty": rule.certainty,
            }
        else:
            out[rid] = {"id": rid, "description": "(规则定义未找到)"}
    return out


def _scan_docauthor_deficits(repo: Path) -> tuple[list[dict], list[dict]]:
    try:
        from omnicompany.cli.commands.docauthor import (
            _scan_skeleton_designs, _scan_missing_manifests,
        )
        return _scan_skeleton_designs(repo), _scan_missing_manifests(repo)
    except Exception:
        return [], []


# ═══════════════════════════════════════════════════════════════════
# LLM 渲染 markdown (qwen-3.6-plus · call_llm_json)
# ═══════════════════════════════════════════════════════════════════

_SYSTEM_PROMPT = """\
你是 omnicompany 守护层的报告翻译者. 你的任务: 把多源原始数据翻译成**给中文使用者**的人类可读 markdown 报告.

## 硬性铁律

1. **不打分** — 绝不算 health_score / quality_score 等数字. 只列举 + 计数 (语义信号 vs 数字压缩).
2. **不保留代号给用户读**:
   - OMNI-049 / OMNI-051a → 用规则字典里的 description 翻译成"运行产物超期老化提醒"等中文白话
   - Stage3-OK / Stage2-Diamond / Hybrid → 转成"已完成清理迁移 / 部分完成 / 半旧半新过渡态"
   - dirty / clean → "目录有杂乱内容 / 目录整洁" + **具体哪里**
   - 真要保留代号时仅放在末尾"原始证据"段落供溯源
3. **不假设用户熟悉机制** — 第一次提到 guardian/manifest/Worker 等词要顺手解释 (一两句即可)
4. **每条问题必含**: 是什么规则 + 为什么这样的规则存在 + 这次具体哪里违规 + 怎么改
5. **不编造** — 只用喂给你的原始数据. 路径/规则 ID 不在数据里就不写
6. **中文为主** — 文件路径 / 类名 / shell 命令 / Worker/Material 等已嵌入代码命名的概念保留, 其他都中文

## 输出结构

返回 JSON:
```json
{
  "markdown": "<完整中文 markdown 报告原文 · 含 OmniMark 头 + 多节>"
}
```

markdown 必须含:
- OmniMark 头 (`<!-- [OMNI] origin=claude-code domain=services/guardian/reports ts=<ts_iso> type=doc status=active -->`)
- 标题 `# omnicompany 守护一手观察 · <ts_iso>`
- 一段 1-3 句的导语 (这报告是什么 / 怎么读 / 数据来源)
- 顶层概览 (中文表格)
- 各节: 规则扫描 → LLM 巡查 → audit 判定 → docauthor 队列, 每节"翻译说明 + 具体清单 + 原始证据"
- 末尾 "下一步建议" (具体动作, 含命令)

## 各节具体要求

### 规则扫描节
对每条违规:
- 第一行讲规则是什么 (一两句白话)
- 第二行讲这次违的具体细节: 哪个文件 / 哪个目录 / 多大 / 多旧
- 第三行说"为什么这是问题" + "建议怎么改"
- 末尾原始证据缩进列出文件路径 + 关键数字

### LLM 巡查节
patrol 报告里的标签 (Stage3-OK / Diamond / Hybrid / dirty 等) 必须翻译:
- Stage3-OK = "清理迁移已完成 (Worker 拆分独立, 不依赖 _archive)"
- Stage2-Diamond = "中间态 · 还在用 _archive 兼容层"
- Skeleton = "骨架阶段, 没真实内容"
- Hybrid = "新旧混合, 过渡期"
- aligned = "DESIGN 描述与实际代码一致"
- drift = "DESIGN 跟代码漂移"
- dirty / clean = "有杂物 / 干净" — 必须列**哪里有什么具体杂物**, 不能光说"dirty"

### audit 节
audit 是 LLM 复核结果:
- confirmed = "确实是问题, 已确认"
- dismissed = "复核认为不是问题, 已驳回 (不需处理)"
- uncertain = "判不准, 需人工"
- 每条记录翻译: 哪条规则 + 哪个文件 + 这条规则是关于什么 + 为什么 confirmed/dismissed

### docauthor 队列
- quarantine: "Worker 自动写文档但没通过质量检查, 隔离起来等人审"
- 列出哪些文档 + 卡在什么 critical 问题
- skeleton DESIGN: "设计文档还是骨架状态待填实"
- 缺 manifest: "service 包还没声明 data 目录布局"

### 下一步建议
对每个建议给具体命令:
- "跑 `omni docauthor run-all --kind manifest`"
- "审视 quarantine: 看 `data/services/docauthor/drafts/_quarantine/<slug>/issues.json`"

不打勾打叉. 只用人话讲清楚.
"""


_USER_PROMPT_TEMPLATE = """\
## 报告时间戳 (用在 OmniMark 头 + 标题)
{ts_iso}

## 顶层数据源计数 (供你做顶层概览表)
{source_counts_json}

## 规则字典 (本次涉及的所有 OMNI-XXX 规则原始定义 · 翻译时引用)
{rule_dict_json}

## 1. 规则扫描原始数据 (4 类)
{rule_scan_json}

## 2. LLM Patrol 报告 (最近 {patrol_count} 份 · 全文)
{patrol_reports_section}

## 3. GuardianAuditStore 判定 (最近 {audit_count} 条原始 + 总分布)
总分布: {audit_verdict_counts_json}

最近条目:
{audit_records_json}

## 4. DocAuthor 工作队列
quarantine ({quarantine_count} 条):
{quarantine_json}

skeleton DESIGN backlog ({skeleton_count} 条):
{skeleton_json}

缺 manifest 的包 ({missing_manifest_count} 条):
{missing_manifest_json}

## 任务

按 system_prompt 把以上数据翻译成中文 markdown 报告. 严格不打分, 不保留代号给用户读 (代号放末尾原始证据段供溯源), 每条问题给"规则是什么 + 哪里违规 + 怎么改".

返回 JSON 含 markdown 字段 (完整 markdown 文本, 含 OmniMark 头).
"""


def _llm_render_markdown(
    *,
    ts_iso: str,
    rule_scan: dict,
    patrol_reports: list[dict],
    audit_records: list[dict],
    audit_verdict_counts: dict,
    quarantine_queue: list[dict],
    skeleton_designs: list[dict],
    missing_manifests: list[dict],
    rule_dict: dict,
    source_counts: dict,
    web_bus: Any = None,
) -> str:
    # 组装 patrol 节: 每份独立块 + 全文 (但截到 4000 字防 context 爆)
    if patrol_reports:
        patrol_section_parts = []
        for p in patrol_reports:
            content = p["content"]
            if len(content) > 4000:
                content = content[:4000] + "\n\n...(下略)"
            patrol_section_parts.append(
                f"### 报告 {p['rel_path']} (ts={p['ts']})\n\n```\n{content}\n```"
            )
        patrol_section = "\n\n".join(patrol_section_parts)
    else:
        patrol_section = "(无 patrol 报告)"

    user_prompt = _USER_PROMPT_TEMPLATE.format(
        ts_iso=ts_iso,
        source_counts_json=json.dumps(source_counts, ensure_ascii=False, indent=2),
        rule_dict_json=json.dumps(rule_dict, ensure_ascii=False, indent=2),
        rule_scan_json=json.dumps(rule_scan, ensure_ascii=False, indent=2, default=str),
        patrol_count=len(patrol_reports),
        patrol_reports_section=patrol_section,
        audit_count=len(audit_records),
        audit_verdict_counts_json=json.dumps(audit_verdict_counts, ensure_ascii=False),
        audit_records_json=json.dumps(audit_records, ensure_ascii=False, indent=2, default=str),
        quarantine_count=len(quarantine_queue),
        quarantine_json=json.dumps(quarantine_queue, ensure_ascii=False, indent=2, default=str),
        skeleton_count=len(skeleton_designs),
        skeleton_json=json.dumps(skeleton_designs, ensure_ascii=False, indent=2, default=str),
        missing_manifest_count=len(missing_manifests),
        missing_manifest_json=json.dumps(missing_manifests, ensure_ascii=False, indent=2, default=str),
    )

    result = call_llm_json(
        system=_SYSTEM_PROMPT,
        user=user_prompt,
        web_bus=web_bus,
        caller="guardian.report_writer",
        role="runtime_main",
        max_tokens=16000,
    )
    if "_parse_error" in result:
        # LLM 没产合规 JSON · 把原文当 markdown 兜底
        raw = result.get("_raw", "")
        if raw.strip():
            return raw
        raise RuntimeError(f"LLM 返回非 JSON 且无原文 fallback: {result.get('_parse_error')}")

    md = result.get("markdown") or result.get("report_md") or ""
    if not md.strip():
        raise RuntimeError(f"LLM 返回 JSON 无 markdown 字段: {list(result.keys())}")
    return md


__all__ = ["GuardianReportWorker"]
