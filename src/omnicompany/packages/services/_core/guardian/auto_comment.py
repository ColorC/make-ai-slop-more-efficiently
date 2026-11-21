# [OMNI] origin=claude-code domain=omnicompany/services/guardian ts=2026-04-08T05:15:00Z
# [OMNI] material_id="material:core.guardian.violation_disposition_engine.implementation.py"
"""auto_comment — 备注化软修复（S3c.2）

对 patrol 发现的违规文件,根据 OmniMark 头里的 origin 字段,分情况处置:

  origin == internal-pipeline (sw-implement / workflow-factory / lang-rewrite /
                                sw-tdd / skill-import / omnicompany)
    → write_fix_queue_entry()  写一份 patch 草稿到 .omni/fix-queue/<date>/
       不动文件本身,等 omni guardian apply-fixes --confirm 才真改

  origin == external-agent (claude-code / external-agent / 其他非 human)
    → apply_comment_out_inline()  立即原地把整个文件加 # 前缀 + 贴告示牌
       原文件备份到 .omni/quarantine/<date>/<ticket>_<basename>
       后果:下次 import 这个文件会立即报 NameError,逼 agent 注意

  origin == human (人类直接编辑)
    → 只警告,不动文件 (尊重人类判断)

  origin == unknown (没有 OmniMark 头或解析失败)
    → 按 external-agent 处理 (保险)

只对 archmap.auto_comment_pilot_rules 列出的规则生效。其他规则的违规
继续走 OmniTow 原本的 disposition (warn / stamp / quarantine / evolve-signal)。
"""

from __future__ import annotations

import json
import logging
import shutil
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Literal, Optional

logger = logging.getLogger(__name__)


# ─── G2 · Disposition 三段式 (2026-04-10) ───────────────────
# 参见 docs/plans/[2026-04-10]GUARDIAN-SENTINEL-ACTIVITY-GATED/plan.md §2
#
# 对 "wrong-location" 类规则 (目前只有 OMNI-015), 不再首次就原地备注化,
# 而是走三段式:
#   stage 1 (首次检测): warn + ticket, 不改文件
#   stage 2 (2+次检测): mv 到 logs/stray/<date>/ + 原位置写 [OMNI-MOVED] 告示牌
#   stage 3 (告示牌 > T2): rm 告示牌, 原文件仍留在 logs/stray/
#
# 计数来源: .omni/quarantine/index.json (tow_truck 先于 auto_comment 写入,
#            所以 count==1 时当前 ticket 已在 index 里, 是首次检测).
_MOVED_PLACARD_MARKER = "[OMNI-MOVED]"
_STAGE2_TARGET_DIR_REL = "logs/stray"
_T2_PLACARD_EXPIRY_HOURS = 24.0
# 走 G2 三段式的规则 id 集合. 其他 pilot rules 仍走原 inline-comment 路径.
_G2_STAGE_RULES = frozenset(["OMNI-015"])


# ─── 来源分类 ────────────────────────────────────────────────

OriginClass = Literal["internal-pipeline", "external-agent", "human", "unknown"]


def determine_origin_class(file_path: str | Path,
                            internal_origins: Optional[list[str]] = None) -> tuple[OriginClass, str]:
    """读文件 OmniMark 头,返回 (class, raw_origin)。

    Args:
        file_path: 违规文件
        internal_origins: 内部管线 origin 枚举(从 archmap 读),不传则用默认

    Returns:
        (class, raw_origin)  例如 ('external-agent', 'claude-code')
    """
    if internal_origins is None:
        internal_origins = [
            "workflow-factory", "sw-implement", "sw-tdd",
            "lang-rewrite", "skill-import", "omnicompany",
        ]

    try:
        from omnicompany.core.omnimark import parse_omnimark
        fields = parse_omnimark(Path(file_path))
        if fields is None:
            return "unknown", ""
        raw = (fields.origin or "").strip()
    except Exception as e:
        logger.debug("auto_comment: parse_omnimark failed for %s: %s", file_path, e)
        return "unknown", ""

    if not raw:
        return "unknown", ""
    if raw == "human":
        return "human", raw
    if raw in internal_origins:
        return "internal-pipeline", raw
    # claude-code / external-agent / 任何别的非空值
    return "external-agent", raw


# ─── 备注化数据结构 ─────────────────────────────────────────

@dataclass
class AutoCommentPlan:
    """对单个违规的处置计划。"""

    ticket_id: str
    rule_id: str
    rule_name: str
    rule_message: str
    violation_path: str
    origin_class: OriginClass
    origin_raw: str
    detected_at: str
    action: str       # 'fix-queue' | 'inline-comment' | 'warn-only' | 'skip-already-disabled'

    def to_dict(self) -> dict:
        return asdict(self)


# ─── 告示牌模板 ─────────────────────────────────────────────

_DISABLED_MARKER = "[OMNI-DISABLED]"

_PLACARD_TEMPLATE = """\
# +========================================================================+
# | {marker} 此路径被 OmniGuardian 三段式移动 (G2 stage 2)                 |
# +========================================================================+
# |
# | 原文件现在位置:  {moved_to}
# | 规则:            {rule_id} ({rule_name})
# | 移动时间:        {moved_at}
# | 首次检测:        {first_seen}
# | 累计检测次数:    {detection_count}
# |
# | 说明:
# |   此路径在仓库根是违规位置 (见 docs/archmap.yaml 的 forbidden_at_repo_root).
# |   创建者应当修改代码, 让文件写到 logs/ 或 .omni/tmp/ 而不是仓库根.
# |
# |   - 如果你是此文件的创建者 (pipeline / script / shell redirect),
# |     请修复代码, 然后手动 rm 此告示牌. 下次运行时不要再写到这个路径.
# |
# |   - 如果不修, 此告示牌 {expire_hours} 小时后会被 Guardian 自动清除 (stage 3),
# |     原文件仍会留在 logs/stray/ 作为证据.
# |
# |   - 如果你需要恢复原文件: omni guardian restore --ticket {ticket_id}
# |
# +========================================================================+
"""


_INLINE_HEADER_TEMPLATE = """\
# +========================================================================+
# | {marker} 此文件被 OmniGuardian 自动禁用 (S3c auto_comment)              |
# +========================================================================+
# |
# | 时间:    {detected_at}
# | 罚单:    {ticket_id}
# | 规则:    {rule_id} ({rule_name})
# | 来源:    {origin_raw}  (分类: {origin_class})
# | 原因:    {rule_message}
# |
# | 原文件备份:  .omni/quarantine/{date}/{ticket_id}_{basename}
# |
# | 修复方法 (任选其一):
# |   1. 把代码移到合法 drawer (见 docs/archmap.yaml 的 src_omnicompany 段)
# |      然后 git revert 此文件
# |   2. omni guardian apply-fixes --restore --ticket {ticket_id}
# |      从 quarantine 恢复并退出托管
# |   3. 修改 docs/archmap.yaml 增加新 drawer 定义 (需 human 直接审批)
# |
# | 此文件的 import 链已断裂——这是设计意图,目的是让你立即注意到。
# +========================================================================+
"""


def _is_already_disabled(content: str) -> bool:
    """检查文件是否已经被 inline-comment 备注化(避免重复处理)。

    注意: 此函数只认 [OMNI-DISABLED] 标记, 不认 [OMNI-MOVED] 告示牌.
    G2 三段式的告示牌由 _file_has_moved_placard 单独识别.
    """
    head = content[:1500]   # 只看前 1.5 KB
    # 排除 G2 告示牌 (它不含 [OMNI-DISABLED], 只含 [OMNI-MOVED])
    return _DISABLED_MARKER in head and _MOVED_PLACARD_MARKER not in head


def _comment_line(line: str) -> str:
    """把一行加 '# ' 前缀。空行加 '#' (不加空格,visual diff 更干净)。"""
    if not line.strip():
        return "#"
    return "# " + line


# ─── G2 · Escalation tracker (独立于 quarantine index) ─────
#
# 原因: patrol.py 的 ticket_id = f"TICKET-{date}-{counter:03d}" 中 counter 是
#       RuleEngine 实例属性, 每次 patrol 新建引擎从 0 开始. 相同扫描顺序下同一
#       (path, rule) 得到同一 ticket_id, tow_truck._update_index 的 dedup
#       (同 ticket_id 覆盖) 会掩盖多次检测, 使得"数 index 条目"永远返回 1.
#       所以 G2 自己维护一份独立的 escalation 计数文件.

_ESCALATION_FILE_REL = ".omni/g2_escalations.json"


def _load_escalations(project_root: Path) -> dict:
    path = project_root / _ESCALATION_FILE_REL
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save_escalations(project_root: Path, data: dict) -> None:
    path = project_root / _ESCALATION_FILE_REL
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except OSError as e:
        logger.warning("auto_comment: escalation save failed: %s", e)


def _escalation_key(violation_path: str, rule_id: str) -> str:
    return f"{violation_path}::{rule_id}"


def _bump_escalation(
    project_root: Path,
    violation_path: str,
    rule_id: str,
    detected_at: str,
) -> tuple[int, str]:
    """对 (path, rule) 的 seen_count +1, 返回 (new_count, first_seen_ts).

    如果这是首次出现, first_seen_ts == detected_at 且 new_count == 1.
    """
    data = _load_escalations(project_root)
    key = _escalation_key(violation_path, rule_id)
    entry = data.get(key)
    if entry is None:
        entry = {
            "path": violation_path,
            "rule_id": rule_id,
            "first_seen_ts": detected_at,
            "last_seen_ts": detected_at,
            "seen_count": 0,
            "first_moved_ts": None,
        }
    entry["seen_count"] = int(entry.get("seen_count", 0)) + 1
    entry["last_seen_ts"] = detected_at
    data[key] = entry
    _save_escalations(project_root, data)
    return entry["seen_count"], entry.get("first_seen_ts", detected_at)


def _mark_escalation_moved(
    project_root: Path,
    violation_path: str,
    rule_id: str,
    moved_at: str,
) -> None:
    data = _load_escalations(project_root)
    key = _escalation_key(violation_path, rule_id)
    if key in data:
        data[key]["first_moved_ts"] = moved_at
        _save_escalations(project_root, data)


def _clear_escalation(
    project_root: Path,
    violation_path: str,
    rule_id: str,
) -> None:
    data = _load_escalations(project_root)
    key = _escalation_key(violation_path, rule_id)
    if key in data:
        del data[key]
        _save_escalations(project_root, data)


def _file_has_moved_placard(abs_path: Path) -> bool:
    """判断文件是否是 G2 stage-2 写的 [OMNI-MOVED] 告示牌."""
    if not abs_path.exists():
        return False
    try:
        head = abs_path.read_text(encoding="utf-8", errors="replace")[:800]
    except OSError:
        return False
    return _MOVED_PLACARD_MARKER in head


def _parse_placard_moved_at(abs_path: Path) -> Optional[datetime]:
    """从 [OMNI-MOVED] 告示牌中解析 '移动时间' 字段, 返回 aware datetime."""
    if not abs_path.exists():
        return None
    try:
        head = abs_path.read_text(encoding="utf-8", errors="replace")[:2000]
    except OSError:
        return None
    for line in head.splitlines():
        if "移动时间:" in line:
            parts = line.split("移动时间:", 1)[-1].strip()
            # 取第一段(到空白为止)作为 ISO 时间戳
            ts_str = parts.split()[0] if parts else ""
            if not ts_str:
                return None
            try:
                return datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
            except ValueError:
                return None
    return None


# ─── 处置: external-agent / unknown → 立即原地备注化 ───────

def apply_comment_out_inline(plan: AutoCommentPlan, project_root: Path) -> bool:
    """对外部 agent / unknown 来源的违规文件:立即原地备注化。

    步骤:
      1. 检测文件是否已经备注化 → 跳过
      2. 备份原文件到 .omni/quarantine/<date>/<ticket>_<basename>
      3. 读全文 → 每行加 # 前缀
      4. 顶部插告示牌
      5. 写回原路径

    Returns:
        True 成功,False 失败/跳过
    """
    abs_path = project_root / plan.violation_path
    if not abs_path.exists():
        logger.debug("auto_comment: file not found %s", abs_path)
        return False

    try:
        original = abs_path.read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        logger.warning("auto_comment: read failed %s: %s", abs_path, e)
        return False

    if _is_already_disabled(original):
        logger.info("[auto_comment] %s 已经被备注化,跳过", plan.violation_path)
        return False

    # 1. 备份
    date_str = plan.detected_at[:10]
    quarantine_dir = project_root / ".omni" / "quarantine" / date_str
    quarantine_dir.mkdir(parents=True, exist_ok=True)
    backup_name = f"{plan.ticket_id}_{abs_path.name}"
    backup_path = quarantine_dir / backup_name
    try:
        shutil.copy2(str(abs_path), str(backup_path))
    except OSError as e:
        logger.warning("auto_comment: backup failed %s: %s", abs_path, e)
        return False

    # 2. 构造告示牌
    header = _INLINE_HEADER_TEMPLATE.format(
        marker=_DISABLED_MARKER,
        detected_at=plan.detected_at,
        ticket_id=plan.ticket_id,
        rule_id=plan.rule_id,
        rule_name=plan.rule_name,
        origin_raw=plan.origin_raw or "(empty)",
        origin_class=plan.origin_class,
        rule_message=plan.rule_message[:200],
        date=date_str,
        basename=abs_path.name,
    )

    # 3. 全文注释化
    commented_body = "\n".join(_comment_line(ln) for ln in original.splitlines())

    new_content = header + "\n" + commented_body + "\n"

    # 4. 写回
    try:
        abs_path.write_text(new_content, encoding="utf-8")
    except OSError as e:
        logger.warning("auto_comment: write failed %s: %s", abs_path, e)
        return False

    logger.warning(
        "[auto_comment INLINE] %s  rule=%s  origin=%s  备份→%s",
        plan.violation_path, plan.rule_id, plan.origin_raw,
        backup_path.relative_to(project_root),
    )
    return True


# ─── G2 · 处置: stage 2 mv + 告示牌 ────────────────────────

def apply_move_with_placard(
    plan: AutoCommentPlan,
    project_root: Path,
    detection_count: int = 0,
    first_seen_ts: str = "",
) -> bool:
    """G2 Stage 2: 把违规文件 mv 到 logs/stray/<date>/, 原位置写 [OMNI-MOVED] 告示牌.

    失败时 (mv 成功但告示牌写不下去) 会尝试把文件 mv 回原位置.
    """
    abs_path = project_root / plan.violation_path
    if not abs_path.exists():
        logger.debug("auto_comment: stage 2 target not found %s", abs_path)
        return False

    date_str = plan.detected_at[:10]
    stray_dir = project_root / _STAGE2_TARGET_DIR_REL / date_str
    try:
        stray_dir.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        logger.warning("auto_comment: stray dir create failed: %s", e)
        return False

    dest = stray_dir / abs_path.name
    # 避免覆盖: 目标已存在时加 ticket_id 前缀
    if dest.exists():
        dest = stray_dir / f"{plan.ticket_id}_{abs_path.name}"

    try:
        shutil.move(str(abs_path), str(dest))
    except OSError as e:
        logger.warning("auto_comment: move failed %s → %s: %s", abs_path, dest, e)
        return False

    # 在原位置写告示牌
    rel_dest = str(dest.relative_to(project_root)).replace("\\", "/")
    try:
        placard = _PLACARD_TEMPLATE.format(
            marker=_MOVED_PLACARD_MARKER,
            moved_to=rel_dest,
            rule_id=plan.rule_id,
            rule_name=plan.rule_name,
            moved_at=plan.detected_at,
            first_seen=first_seen_ts or plan.detected_at,
            detection_count=detection_count,
            expire_hours=int(_T2_PLACARD_EXPIRY_HOURS),
            ticket_id=plan.ticket_id,
        )
        abs_path.write_text(placard, encoding="utf-8")
    except (OSError, KeyError) as e:
        logger.warning("auto_comment: placard write failed %s: %s", abs_path, e)
        # 尝试回滚
        try:
            shutil.move(str(dest), str(abs_path))
        except OSError:
            logger.error("auto_comment: rollback failed, file stranded at %s", dest)
        return False

    logger.warning(
        "[auto_comment STAGE-2 MOVE-PLACARD] %s  rule=%s  → %s  (count=%d)",
        plan.violation_path, plan.rule_id, rel_dest, detection_count,
    )
    return True


# ─── 处置: internal-pipeline → 写 fix-queue ────────────────

def write_fix_queue_entry(plan: AutoCommentPlan, project_root: Path) -> Path:
    """对内部管线产生的违规:写一份 patch 草稿到 fix-queue,不动文件本身。

    Returns:
        写入的 fix-queue 条目路径
    """
    date_str = plan.detected_at[:10]
    fix_dir = project_root / ".omni" / "fix-queue" / date_str
    fix_dir.mkdir(parents=True, exist_ok=True)
    entry_file = fix_dir / f"{plan.ticket_id}.json"

    abs_path = project_root / plan.violation_path
    preview_lines: list[str] = []
    if abs_path.exists():
        try:
            content = abs_path.read_text(encoding="utf-8", errors="replace")
            preview_lines = [_comment_line(ln) for ln in content.splitlines()[:30]]
        except OSError:
            pass

    entry = {
        **plan.to_dict(),
        "status": "pending",
        "proposed_patch": {
            "kind": "comment_out_inline",
            "preview_first_30_lines": preview_lines,
        },
        "created_at": plan.detected_at,
        "applied_at": None,
        "applied_by": None,
    }

    try:
        entry_file.write_text(
            json.dumps(entry, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except OSError as e:
        logger.warning("auto_comment: fix-queue write failed %s: %s", entry_file, e)

    logger.info(
        "[auto_comment FIX-QUEUE] %s  rule=%s  origin=%s  → %s",
        plan.violation_path, plan.rule_id, plan.origin_raw,
        entry_file.relative_to(project_root),
    )
    return entry_file


# ─── 处置: human → 只警告 ───────────────────────────────────

def warn_human_violation(plan: AutoCommentPlan) -> None:
    logger.warning(
        "[auto_comment HUMAN-WARN] %s  rule=%s  (人类直接编辑,不自动处置,请人工审查)",
        plan.violation_path, plan.rule_id,
    )


# ─── 主入口 ──────────────────────────────────────────────────

@dataclass
class AutoCommentResult:
    total_violations: int = 0
    inline_commented: int = 0
    fix_queued: int = 0
    human_warned: int = 0
    skipped_already_disabled: int = 0
    failed: int = 0
    # G2 三段式计数 (2026-04-10, 对 _G2_STAGE_RULES 里的规则生效)
    stage1_warned: int = 0
    stage2_moved: int = 0
    stage3_cleaned_up: int = 0
    placard_active: int = 0
    plans: Optional[list[AutoCommentPlan]] = None

    def to_dict(self) -> dict:
        d = asdict(self)
        if self.plans is not None:
            d["plans"] = [p.to_dict() if hasattr(p, "to_dict") else p for p in self.plans]
        return d


def process_for_auto_comment(
    violations: list[dict],
    project_root: Path | str,
    archmap=None,
) -> AutoCommentResult:
    """对一组违规跑一遍备注化处置。

    被 patrol_runner 在 OmniTow 之后调用。

    Args:
        violations: run_patrol() 返回的 violations 列表(dict 形式)
        project_root: 项目根
        archmap: 已加载的 ArchMap 实例(不传则自动加载)

    Returns:
        AutoCommentResult 汇总
    """
    root = Path(project_root)
    result = AutoCommentResult(plans=[])

    if not violations:
        return result

    if archmap is None:
        try:
            from omnicompany.core.archmap import load_archmap
            archmap = load_archmap()
        except Exception as e:
            logger.warning("auto_comment: archmap load failed, skipping: %s", e)
            return result

    pilot_rules = frozenset(archmap.auto_comment_pilot_rules)
    if not pilot_rules:
        logger.debug("auto_comment: 无试点规则,跳过")
        return result

    internal_origins = list(archmap.internal_pipeline_origins)
    targets = [v for v in violations if v.get("rule_id") in pilot_rules]
    result.total_violations = len(targets)

    if not targets:
        return result

    for v in targets:
        path = v.get("path", "")
        rule_id = v.get("rule_id", "")
        if not path:
            continue

        abs_path = root / path
        origin_class, origin_raw = determine_origin_class(abs_path, internal_origins)

        plan = AutoCommentPlan(
            ticket_id=v.get("ticket_id", "TICKET-UNKNOWN"),
            rule_id=rule_id,
            rule_name=v.get("rule_name", rule_id),
            rule_message=v.get("message", ""),
            violation_path=path,
            origin_class=origin_class,
            origin_raw=origin_raw,
            detected_at=v.get("detected_at") or datetime.now(timezone.utc).isoformat(),
            action="",  # filled in below
        )

        # ─── G2 · 三段式派发 (OMNI-015 等 wrong-location 规则) ───
        #
        # 在 _is_already_disabled 之前先判断: 如果文件已经是 [OMNI-MOVED] 告示牌,
        # 根据 age 决定 stage 3 cleanup / skip-placard-active. 这一路径对所有
        # origin class 统一处置 (即使是 human, 因为告示牌不是人类写的, 不怕误删).
        if rule_id in _G2_STAGE_RULES:
            if _file_has_moved_placard(abs_path):
                moved_at = _parse_placard_moved_at(abs_path)
                if moved_at is not None:
                    now_utc = datetime.now(timezone.utc)
                    age = now_utc - moved_at
                    if age > timedelta(hours=_T2_PLACARD_EXPIRY_HOURS):
                        # Stage 3: 告示牌到期, rm 清除 + 从 escalation tracker 移除
                        plan.action = "stage3-cleanup"
                        try:
                            abs_path.unlink()
                            _clear_escalation(root, path, rule_id)
                            result.stage3_cleaned_up += 1
                            logger.warning(
                                "[auto_comment STAGE-3 CLEANUP] %s  rule=%s  age=%.1fh",
                                path, rule_id, age.total_seconds() / 3600.0,
                            )
                        except OSError as e:
                            logger.warning("stage3 unlink failed %s: %s", path, e)
                            result.failed += 1
                    else:
                        # 告示牌仍在生命周期内, 跳过
                        plan.action = "skip-placard-active"
                        result.placard_active += 1
                else:
                    # 告示牌存在但 moved_at 解析失败, 保守跳过
                    plan.action = "skip-placard-unparseable"
                    result.placard_active += 1
                result.plans.append(plan)
                continue

            # 文件不是告示牌 → 按 origin 决定是否走三段式
            # internal-pipeline 仍走 fix-queue; human 仍只警告. 只有 external-agent
            # 和 unknown 走三段式 (因为这两类是"业务代码/shell 重定向"的泄漏).
            if origin_class in ("external-agent", "unknown"):
                count, first_seen = _bump_escalation(
                    root, path, rule_id, plan.detected_at,
                )
                if count <= 1:
                    # Stage 1: 只 warn, 不改文件 (ticket 已由 tow_truck 写入 index)
                    plan.action = "stage1-warn"
                    logger.warning(
                        "[auto_comment STAGE-1 WARN] %s  rule=%s  origin=%s  count=%d",
                        path, rule_id, origin_raw or "unknown", count,
                    )
                    result.stage1_warned += 1
                else:
                    # Stage 2: mv + 告示牌
                    plan.action = "stage2-move-placard"
                    try:
                        ok = apply_move_with_placard(
                            plan, root,
                            detection_count=count,
                            first_seen_ts=first_seen,
                        )
                        if ok:
                            _mark_escalation_moved(
                                root, path, rule_id, plan.detected_at,
                            )
                            result.stage2_moved += 1
                        else:
                            result.failed += 1
                    except Exception as e:
                        logger.warning("stage2 failed %s: %s", path, e)
                        result.failed += 1
                result.plans.append(plan)
                continue
            # 其他 origin (internal-pipeline / human) → fall through 到原逻辑

        # 检测是否已经备注化(任何来源都先检查这个,避免重复触发)
        if abs_path.exists():
            try:
                if _is_already_disabled(abs_path.read_text(encoding="utf-8", errors="replace")):
                    plan.action = "skip-already-disabled"
                    result.skipped_already_disabled += 1
                    result.plans.append(plan)
                    continue
            except OSError:
                pass

        # 分情况派发
        if origin_class == "internal-pipeline":
            plan.action = "fix-queue"
            try:
                write_fix_queue_entry(plan, root)
                result.fix_queued += 1
            except Exception as e:
                logger.warning("auto_comment: fix-queue failed %s: %s", path, e)
                result.failed += 1

        elif origin_class == "human":
            plan.action = "warn-only"
            warn_human_violation(plan)
            result.human_warned += 1

        else:
            # external-agent / unknown
            plan.action = "inline-comment"
            try:
                ok = apply_comment_out_inline(plan, root)
                if ok:
                    result.inline_commented += 1
                else:
                    result.failed += 1
            except Exception as e:
                logger.warning("auto_comment: inline failed %s: %s", path, e)
                result.failed += 1

        result.plans.append(plan)

    return result


# ─── restore (从 quarantine 恢复) ────────────────────────────

def restore_from_quarantine(ticket_id: str, project_root: Path | str) -> Optional[Path]:
    """根据 ticket_id 从 .omni/quarantine/ 找到备份并恢复到原位。

    Returns:
        恢复后的目标路径,失败 None
    """
    root = Path(project_root)
    quarantine_root = root / ".omni" / "quarantine"
    if not quarantine_root.is_dir():
        logger.warning("restore: quarantine dir not found")
        return None

    # 在所有 date 目录里找文件名前缀 = ticket_id_
    candidates: list[Path] = []
    for date_dir in quarantine_root.iterdir():
        if not date_dir.is_dir():
            continue
        for f in date_dir.iterdir():
            if f.is_file() and f.name.startswith(f"{ticket_id}_"):
                candidates.append(f)

    if not candidates:
        logger.warning("restore: no backup found for ticket %s", ticket_id)
        return None
    if len(candidates) > 1:
        logger.warning("restore: multiple backups for ticket %s, using latest", ticket_id)
        candidates.sort(key=lambda p: p.stat().st_mtime, reverse=True)

    backup = candidates[0]

    # 找原 ticket json 来确定 violation_path
    ticket_path = backup.parent / f"{ticket_id}.json"
    target_path: Optional[Path] = None
    if ticket_path.exists():
        try:
            ticket_data = json.loads(ticket_path.read_text(encoding="utf-8"))
            rel = ticket_data.get("original_path") or ticket_data.get("violation_path")
            if rel:
                target_path = root / rel
        except Exception:
            pass

    if target_path is None:
        # 兜底:从备份名后半段重建,假设结构是 <ticket>_<basename>
        basename = backup.name[len(ticket_id) + 1:]
        logger.warning(
            "restore: no ticket json,using basename %s — 你可能需要手动指定原路径",
            basename,
        )
        return None

    try:
        target_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(str(backup), str(target_path))
        logger.info("[auto_comment RESTORE] %s ← %s", target_path.relative_to(root), backup.relative_to(root))
        return target_path
    except OSError as e:
        logger.warning("restore: copy failed: %s", e)
        return None
