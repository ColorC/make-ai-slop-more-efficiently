from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .models import utc_now
from .store import ObservatoryStore


CONTRACT_VERSION = "reverse-engineered-game-design-spec.v0.3"
AFK_REPORT_ID = "report.afk-journey.hero-upgrade.v1"
MINECRAFT_REPORT_ID = "report.minecraft.voxelcraft-fire-food.v2"
EXPECTED_REPORT_IDS = {AFK_REPORT_ID, MINECRAFT_REPORT_ID}


@dataclass(frozen=True)
class PhaseDefinition:
    number: int
    title: str
    target: str
    fixture: str
    evidence_files: tuple[str, ...]
    manual_work_displaced: str
    knowledge_gain: str
    downstream_consumer: str


PHASES = (
    PhaseDefinition(
        0,
        "语义合同与本地读取",
        "冻结 v0.3 逆向游戏设计案合同，并只发布通过完整度门的本地档案。",
        "AFK Journey 英雄厅与 Minecraft 第一夜两份已发布设计案",
        ("provenance-audit.json",),
        "逐页判断文章、截图集和设计案是否达到同一发布标准。",
        "发布单位被固定为可验证、可机器读取的逆向策划案，而非文章。",
        "Canonical store、编译器与 Gate 1–5",
    ),
    PhaseDefinition(
        1,
        "Canonical 设计对象与证据存储",
        "让来源、证据、页面、交互、机制、资源、反馈和修订成为可关联对象。",
        "两份 v0.3 报告的 design_spec、design_objects 与 design_relations",
        ("editorial-validation.json",),
        "在文章正文中重复抄写来源、截图、规则和玩家反馈关系。",
        "同一设计对象可追溯到真实画面、源码 oracle、玩家声音和修订历史。",
        "设计案编译器、Search/API/RAG 与编辑协作",
    ),
    PhaseDefinition(
        2,
        "设计案编译器与本地多用户网站",
        "从 canonical store 生成完整设计案阅读器，并通过真实浏览器、并发和归档门。",
        "8210 本地站首页与两份完整报告的桌面/移动浏览器证据",
        ("public-site-browser-evidence.json", "public-site-quality-validation.json"),
        "为每份拆解手工维护独立 HTML、目录、资产链接和移动端布局。",
        "同一语义真源稳定生成图文、交互示意、设计稿、来源和机器可读布局。",
        "本地多用户阅读、编辑与后续内容生产",
    ),
    PhaseDefinition(
        3,
        "AFK Journey MuMu 真实样本",
        "用本地 MuMu 实景与客户端源码反推英雄厅和赛季英雄升级设计案。",
        "AFK Journey CN 1.7.21；主世界至英雄详情的只读采集路径",
        ("afk-mumu-hero-upgrade-observation.json",),
        "手工截屏、OCR 抄值、对照源码并另写页面和流程说明。",
        "外部可见画面、数值、条件中断和源码规则能汇入同一客观样本。",
        "后续 AFK 系统拆解与移动端探索器",
    ),
    PhaseDefinition(
        4,
        "Minecraft 固定世界真实样本",
        "用可复位 ProtoWorld 固定世界验证生火、烹饪、身体与夜间火光设计案。",
        "Minecraft Java 1.21.1 / ProtoWorld 第一夜；world snapshot eba1aee",
        ("minecraft-first-night-fire-food.json",),
        "人工复位世界、逐条记结果、截取画面并反推规则和状态图。",
        "真实客户端输入、24 个客观门、源码 oracle 和世界复位形成可重复闭环。",
        "PC 游戏探索器、机制伪代码与固定世界 benchmark",
    ),
    PhaseDefinition(
        5,
        "设施硬化与审阅就绪",
        "证明本地设施可重复读取、并发访问、监控、备份恢复，并提交人类有效性审阅。",
        "两份已发布档案、真实浏览器、当前 monitor 与恢复演练",
        ("public-site-quality-validation.json", "monitor.json", "recovery-drill.json"),
        "靠口头说明判断网站是否可用、数据是否完整以及备份是否真能恢复。",
        "技术闭环可由当前证据自动裁决；设计案是否真正帮助非开发者理解留给审阅台。",
        "Omnicompany 审阅台与下一轮单系统扩展",
    ),
)


class PhaseProofBuilder:
    def __init__(self, store: ObservatoryStore) -> None:
        self.store = store

    def _load(self, name: str) -> dict[str, Any] | None:
        path = self.store.export_root / name
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None

    def _evidence(self, name: str) -> dict[str, Any]:
        path = self.store.export_root / name
        payload = self._load(name)
        return {
            "name": name,
            "path": str(path),
            "present": path.is_file(),
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None,
            "schema": (payload or {}).get("schema"),
            "generated_at": (payload or {}).get("generated_at")
            or (payload or {}).get("created_at")
            or (payload or {}).get("checked_at"),
            "reported_ok": (payload or {}).get("ok"),
        }

    def _published_reports(self) -> list[Any]:
        return self.store.list_reports()

    @staticmethod
    def _check(payload: dict[str, Any], check_id: str) -> dict[str, Any] | None:
        return next((item for item in payload.get("checks", []) if item.get("id") == check_id), None)

    @staticmethod
    def _visual_count(report: Any) -> int:
        return sum(item.kind in {"screenshot", "video_frame"} for item in report.artifacts)

    def _technical_status(self, phase: int) -> tuple[str, list[str], list[str]]:
        failures: list[str] = []
        boundaries: list[str] = []
        reports = self._published_reports()
        report_by_id = {report.id: report for report in reports}
        counts = self.store.counts()

        if phase == 0:
            if set(report_by_id) != EXPECTED_REPORT_IDS:
                failures.append("本轮必须且只能发布 AFK Journey 与 Minecraft 两份当前样本")
            for report in reports:
                if report.contract_version != CONTRACT_VERSION:
                    failures.append(f"{report.id} 未采用 v0.3 设计案合同")
                issues = report.publication_issues()
                if issues:
                    failures.append(f"{report.id} 有 {len(issues)} 个发布完整度问题")
            audit = self._load("provenance-audit.json") or {}
            audited = {item.get("id"): item for item in audit.get("reports", [])}
            for report_id in EXPECTED_REPORT_IDS:
                if report_id not in audited or audited[report_id].get("issues"):
                    failures.append(f"{report_id} 缺少当前 provenance 审计或仍有问题")
            public = self.store.export_root / "public"
            if not (public / "catalog.json").is_file() or not (public / "sitemap.xml").is_file():
                failures.append("本地 catalog 或 sitemap 编译输出缺失")
            boundaries.append("当前只验收本地设施，不把公网部署、USB 真机或远程 ADB 计入本 Gate")
            return ("passed" if not failures else "partial", failures, boundaries)

        if phase == 1:
            editorial = self._load("editorial-validation.json") or {}
            if editorial.get("ok") is not True:
                failures.append("对象级编辑、修订或增量编译证据未通过")
            if counts["design_objects"] <= 0 or counts["design_relations"] <= 0:
                failures.append("Canonical design_objects 或 design_relations 为空")
            for report in reports:
                if report.design_spec is None:
                    failures.append(f"{report.id} 缺少 ReverseEngineeredGameDesignSpec")
                if not report.sources or not report.artifacts:
                    failures.append(f"{report.id} 缺少来源或证据资产")
                if report.publication_issues():
                    failures.append(f"{report.id} 的对象关系未通过发布门")
            return ("passed" if not failures else "partial", failures, boundaries)

        if phase == 2:
            quality = self._load("public-site-quality-validation.json") or {}
            browser = self._load("public-site-browser-evidence.json") or {}
            if not all(quality.get(key) is True for key in ("ok", "site_shell_ready", "archive_complete")):
                failures.append("本地站质量、站点外壳或档案完整度门未通过")
            if browser.get("schema") != "game-observatory.browser-quality-evidence.v2":
                failures.append("缺少当前全报告浏览器证据 v2")
            browser_slugs = set((browser.get("reports") or {}).keys())
            expected_slugs = {report.slug for report in reports}
            if browser_slugs != expected_slugs:
                failures.append("浏览器证据没有完整覆盖当前发布报告")
            if any(browser.get(key) for key in ("console_errors", "failed_requests", "http_errors")):
                failures.append("真实浏览器仍有控制台、请求或 HTTP 错误")
            return ("passed" if not failures else "partial", failures, boundaries)

        if phase == 3:
            report = report_by_id.get(AFK_REPORT_ID)
            evidence = self._load("afk-mumu-hero-upgrade-observation.json") or {}
            if report is None:
                return ("blocked", ["AFK Journey 真实设计案未发布"], boundaries)
            if len(report.surfaces) != 4:
                failures.append("AFK 设计案必须保留 4 个真实页面/状态")
            if report.design_spec is None or len(report.design_spec.design_artifacts) < 5:
                failures.append("AFK 设计案缺少 4 张反推页面设计稿与交互图")
            if self._visual_count(report) < 4:
                failures.append("AFK 设计案缺少 4 张真实截图/视频帧")
            if evidence.get("ok") is not True or (evidence.get("source_oracle") or {}).get("ok") is not True:
                failures.append("AFK MuMu 实景或源码 oracle 校验未通过")
            if not report.benchmark_task or any(
                check.passed is not True or check.actual is None
                for check in report.benchmark_task.checks
            ):
                failures.append("AFK canonical benchmark 未写入全部客观结果")
            no_mutation = self._check(evidence, "no-resource-mutation")
            if not no_mutation or no_mutation.get("passed") is not True or no_mutation.get("actual") is not True:
                failures.append("AFK 只读边界未被客观证明")
            boundaries.append("未执行英雄升级、购买或领奖；资源扣除型验证需独立可复位账号快照")
            return ("passed" if not failures else "blocked", failures, boundaries)

        if phase == 4:
            report = report_by_id.get(MINECRAFT_REPORT_ID)
            evidence = self._load("minecraft-first-night-fire-food.json") or {}
            if report is None:
                return ("blocked", ["Minecraft 固定世界真实设计案未发布"], boundaries)
            if len(report.surfaces) != 4:
                failures.append("Minecraft 设计案必须保留 4 个真实页面/状态")
            if report.design_spec is None or len(report.design_spec.design_artifacts) < 5:
                failures.append("Minecraft 设计案缺少 4 张反推页面设计稿与交互图")
            if self._visual_count(report) < 7:
                failures.append("Minecraft 设计案缺少 7 张真实客户端画面")
            gates = evidence.get("gates") or {}
            if evidence.get("ok") is not True or (evidence.get("source_oracle") or {}).get("ok") is not True:
                failures.append("Minecraft 实景或源码 oracle 校验未通过")
            if gates.get("passed") != 24 or gates.get("total") != 24:
                failures.append("Minecraft 第一夜客观门不是 24/24")
            if not report.benchmark_task or len(report.benchmark_task.checks) != 7 or any(
                check.passed is not True or check.actual is None
                for check in report.benchmark_task.checks
            ):
                failures.append("Minecraft canonical benchmark 的 7 个聚合检查未全部通过")
            for check_id in ("recipe-boundary-probed", "world-reset-after-run"):
                check = self._check(evidence, check_id)
                if not check or check.get("passed") is not True:
                    failures.append(f"Minecraft {check_id} 未通过")
            boundaries.append("该实例主动清空原版配方；石镐不是有效任务，当前验证的是同等客观的生火/食物/夜间系统")
            return ("passed" if not failures else "blocked", failures, boundaries)

        quality = self._load("public-site-quality-validation.json") or {}
        monitor = self._load("monitor.json") or {}
        recovery = self._load("recovery-drill.json") or {}
        browser = self._load("public-site-browser-evidence.json") or {}
        if quality.get("ok") is not True:
            failures.append("本地网站质量门未通过")
        if monitor.get("ok") is not True or monitor.get("database_integrity") != "ok":
            failures.append("当前数据库、资产或公开输出监控未通过")
        if recovery.get("ok") is not True or recovery.get("counts_match") is not True:
            failures.append("备份恢复演练未通过或对象计数不一致")
        backup = Path(str(recovery.get("backup") or ""))
        if not backup.is_dir() or not (backup / "backup.json").is_file():
            failures.append("恢复演练引用的备份清单不存在")
        if (recovery.get("monitor") or {}).get("ok") is not True:
            failures.append("恢复副本的 monitor 未通过")
        if browser.get("schema") != "game-observatory.browser-quality-evidence.v2":
            failures.append("最终浏览器证据不是 v2")
        boundaries.append("技术通过只代表可提交审阅；非开发者是否能把页面当作逆向策划案理解仍需审阅台裁决")
        return ("passed" if not failures else "partial", failures, boundaries)

    def _measurement(self, phase: int) -> dict[str, Any]:
        counts = self.store.counts()
        reports = self._published_reports()
        if phase == 0:
            return {
                "contract_version": CONTRACT_VERSION,
                "published_reports": len(reports),
                "public_files": sum(
                    (self.store.export_root / "public" / name).is_file()
                    for name in ("catalog.json", "sitemap.xml")
                ),
            }
        if phase == 1:
            return {
                "design_objects": counts["design_objects"],
                "design_relations": counts["design_relations"],
                "artifacts": counts["artifacts"],
                "report_revisions": counts["report_revisions"],
            }
        if phase == 2:
            quality = self._load("public-site-quality-validation.json") or {}
            measurements = quality.get("measurements") or {}
            return {
                "reports": len(reports),
                "browser_p95_ms": measurements.get("browser_p95_ms"),
                "concurrent_wall_ms": measurements.get("concurrent_wall_ms"),
                "archive_complete": quality.get("archive_complete"),
            }
        if phase == 3:
            report = self.store.get_report(AFK_REPORT_ID, include_drafts=False)
            return {
                "surfaces": len(report.surfaces) if report else 0,
                "real_frames": self._visual_count(report) if report else 0,
                "design_artifacts": len(report.design_spec.design_artifacts) if report and report.design_spec else 0,
                "benchmark_checks": len(report.benchmark_task.checks) if report and report.benchmark_task else 0,
            }
        if phase == 4:
            report = self.store.get_report(MINECRAFT_REPORT_ID, include_drafts=False)
            evidence = self._load("minecraft-first-night-fire-food.json") or {}
            return {
                "objective_gates": f"{(evidence.get('gates') or {}).get('passed', 0)}/{(evidence.get('gates') or {}).get('total', 0)}",
                "real_frames": self._visual_count(report) if report else 0,
                "design_artifacts": len(report.design_spec.design_artifacts) if report and report.design_spec else 0,
                "world_snapshot": (evidence.get("manifest") or {}).get("world_snapshot"),
            }
        monitor = self._load("monitor.json") or {}
        recovery = self._load("recovery-drill.json") or {}
        return {
            "artifacts_checked": monitor.get("artifacts_checked"),
            "restored_reports": (recovery.get("restored_counts") or {}).get("reports"),
            "counts_match": recovery.get("counts_match"),
            "backup": recovery.get("backup"),
        }

    def build(self) -> dict[str, Any]:
        output_root = self.store.export_root / "phase-proofs"
        output_root.mkdir(parents=True, exist_ok=True)
        phase_payloads: list[dict[str, Any]] = []
        for definition in PHASES:
            technical_status, failures, boundaries = self._technical_status(definition.number)
            if technical_status != "passed":
                effectiveness_status = "not_ready"
                overall_status = "blocked" if technical_status == "blocked" else "partial"
                decision = "repair"
            elif definition.number == 5:
                effectiveness_status = "pending_non_developer_review"
                overall_status = "review_pending"
                decision = "submit_for_review"
            else:
                effectiveness_status = "validated_by_objective_evidence"
                overall_status = "passed"
                decision = "advance"
            payload = {
                "schema": "game-observatory.phase-proof.v2",
                "generated_at": utc_now(),
                "phase": definition.number,
                "title": definition.title,
                "target": definition.target,
                "fixture": definition.fixture,
                "technical_status": technical_status,
                "effectiveness_status": effectiveness_status,
                "overall_status": overall_status,
                "evidence": [self._evidence(name) for name in definition.evidence_files],
                "failure_samples": failures,
                "effectiveness": {
                    "manual_work_displaced": definition.manual_work_displaced,
                    "measured_automation": self._measurement(definition.number),
                    "knowledge_gain": definition.knowledge_gain,
                    "maintainability": "Canonical store、证据 hash、失败现场与回归合同都保存在本地设施中。",
                    "downstream_consumer": definition.downstream_consumer,
                    "non_developer_review": {
                        "status": "pending" if definition.number == 5 else "not_required_for_objective_gate",
                        "evidence": (
                            "等待 Omnicompany 审阅台对逆向策划案可理解性和实际用途作出裁决。"
                            if definition.number == 5
                            else "本 Gate 由可重复的机器证据和客观 benchmark 裁决。"
                        ),
                    },
                },
                "boundaries": boundaries,
                "decision": decision,
            }
            path = output_root / f"phase-{definition.number}.md"
            lines = [
                f"# Gate {definition.number} · {definition.title}",
                "",
                f"- 技术状态：`{technical_status}`",
                f"- 有效性状态：`{effectiveness_status}`",
                f"- 总体裁决：`{overall_status}`",
                f"- 决策：`{decision}`",
                "",
                "## 目标",
                "",
                definition.target,
                "",
                "## Fixture",
                "",
                definition.fixture,
                "",
                "## 运行证据",
                "",
                *[
                    f"- [{'x' if item['present'] else ' '}] `{item['name']}` · `{item['sha256'] or 'missing'}`"
                    for item in payload["evidence"]
                ],
                "",
                "## 失败样本与未满足条件",
                "",
                *([f"- {item}" for item in failures] or ["- 当前技术门没有已知失败样本。"]),
                "",
                "## 人工节省与有效性",
                "",
                f"- 被替代的人工劳动：{definition.manual_work_displaced}",
                f"- 实测自动化计数：`{json.dumps(payload['effectiveness']['measured_automation'], ensure_ascii=False)}`",
                f"- 新增知识/复现能力：{definition.knowledge_gain}",
                f"- 下游消费者：{definition.downstream_consumer}",
                f"- 非开发者理解/使用：`{payload['effectiveness']['non_developer_review']['status']}`。",
                "",
                "## 边界",
                "",
                *([f"- {item}" for item in boundaries] or ["- 无新增边界。"]),
                "",
            ]
            path.write_text("\
".join(lines), encoding="utf-8")
            payload["markdown_path"] = str(path)
            phase_payloads.append(payload)

        review_ready = all(item["technical_status"] == "passed" for item in phase_payloads)
        result = {
            "schema": "game-observatory.phase-proof-index.v2",
            "generated_at": utc_now(),
            "ok": review_ready,
            "review_ready": review_ready,
            "review_pending": [
                item["phase"] for item in phase_payloads if item["overall_status"] == "review_pending"
            ],
            "technical_passed": [
                item["phase"] for item in phase_payloads if item["technical_status"] == "passed"
            ],
            "overall_passed": [
                item["phase"] for item in phase_payloads if item["overall_status"] == "passed"
            ],
            "phases": phase_payloads,
        }
        index_path = output_root / "index.json"
        index_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        result["path"] = str(index_path)
        return result