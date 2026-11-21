# [OMNI] origin=claude-code domain=omnicompany/core ts=2026-04-08T05:20:00Z
# [OMNI] material_id="material:omnicompany.core.guarded_write.file_write_guard.engine.py"
"""OmniGuardian 统一文件写入入口（S3c.4 重写为纯规则门禁）。

参考: docs/plans/[2026-04-05]OMNIGUARDIAN-DESIGN/02-OMNISHIELD.md
       docs/archmap.yaml (S3a 起的权威 drawer 定义)

所有 LLM agent tool 的 write_file / Router 业务代码的文件创建都应该走这里:

    from omnicompany.core.guarded_write import write_file
    write_file("packages/domains/demogame/produce/foo.py", content,
               origin="claude-code", trace=trace_id, node=node_id,
               purpose="补充新字段提取器")

S3c.4 起的算法（纯规则、零 LLM、零网络）：

  1. 紧急逃生:  OMNIGUARDIAN_DISABLE=1 环境变量 → 直接 Path.write_text
  2. 加载 archmap (cached + mtime aware)
  3. 路径归一化为相对仓库根的 posix 路径
  4. 调 archmap.is_writable(rel_path, writer) 做纯规则判定
  5. 三档处置:
     a. allowed=True → 放行（always_green / writer 在 writable_by /
                              agent_free_fire 兜底）
     b. 绝对违规（forbidden_at_repo_root glob 命中）→ 永远硬抛 ShieldViolation
        无论 enforce_mode 是 true 还是 false
     c. 软违规（drawer 不允许该 writer 等）→
        archmap.enforce_mode=true → 抛 ShieldViolation
        archmap.enforce_mode=false → 只 logger.warning + 写 audit + 放行
  6. 写文件:  Path.write_text
  7. 贴 OmniMark 头:  stamp_file
  8. 永远写一条 audit log:  .omni/shield_audit.jsonl

OMNIGUARDIAN_DISABLE=1 仍然是紧急 bypass。

老的 OmniShield 类已退役并搬到 _graveyard/core_omni_shield.py (S3d.7,
2026-04-08)。本模块只用 archmap + audit log,零依赖 omni_shield。
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from omnicompany.core.archmap import ArchMap, ArchMapError, WriteCheck, load_archmap
from omnicompany.core.omnimark import stamp_file

logger = logging.getLogger(__name__)


# ─── 异常 ────────────────────────────────────────────────────

class ShieldViolation(Exception):
    """写入被 OmniGuardian 阻断。

    rule_id: 'FORBIDDEN-ROOT-GLOB' / 'NON-WRITABLE' / 'UNKNOWN-DRAWER' / ...
    path: 归一化后的目标路径
    reason: 人类可读的原因 + 修复建议
    audit_id: 对应 .omni/shield_audit.jsonl 的记录 id
    """

    def __init__(self, rule_id: str, path: str, reason: str, audit_id: str = ""):
        self.rule_id = rule_id
        self.path = path
        self.reason = reason
        self.audit_id = audit_id
        msg = f"[{rule_id}] {path}\n  原因: {reason}"
        if audit_id:
            msg += f"\n  审计 ID: {audit_id}"
        super().__init__(msg)


# ─── 项目根定位 ──────────────────────────────────────────────

def _find_project_root() -> Path:
    p = Path(__file__).resolve()
    while p.parent != p:
        if (p / "pyproject.toml").exists():
            return p
        p = p.parent
    return Path(__file__).resolve().parents[3]


_PROJECT_ROOT = _find_project_root()
_AUDIT_LOG = _PROJECT_ROOT / ".omni" / "shield_audit.jsonl"
_GUARDIAN_ALERT = _PROJECT_ROOT / ".omni" / "GUARDIAN_ALERT.md"
_BYPASS_SENTINEL = _PROJECT_ROOT / ".omni" / "DISABLE_BYPASS"


# ─── S3e.2 跳闸 / 告警 ─────────────────────────────────────

def _check_bypass_active() -> str:
    """检查 Guardian bypass 是否启用。

    两种通道,按优先级:
      1. 文件哨兵 .omni/DISABLE_BYPASS — 必须存在且含 'human_signature: <name>' 行
         (human 显式创建, 防程序化投毒)
      2. env var OMNIGUARDIAN_DISABLE=1 — 向后兼容,但会被标记为可疑来源

    返回:
        '' = 未 bypass / 'file-sentinel' / 'env-var-legacy'
    """
    if _BYPASS_SENTINEL.exists():
        try:
            content = _BYPASS_SENTINEL.read_text(encoding="utf-8", errors="replace")
            if "human_signature:" in content:
                return "file-sentinel"
        except OSError:
            pass
    if os.environ.get("OMNIGUARDIAN_DISABLE") == "1":
        return "env-var-legacy"
    return ""


def _raise_guardian_alert(reason: Exception | str) -> None:
    """当 archmap 不可加载等严重故障时,把一条告警写到 .omni/GUARDIAN_ALERT.md。

    这个文件是**对下一个 agent / human 的信号**:
    - 不自动删
    - 任何 agent 读 .omni/ 目录都会看到
    - 内容里明确写"请修复 Guardian"
    """
    try:
        _GUARDIAN_ALERT.parent.mkdir(parents=True, exist_ok=True)
        banner = f"""# 🚨 OmniGuardian ALERT — 架构门禁失效

**触发时间**: {datetime.now(timezone.utc).isoformat()}
**原因**: {reason}

## 这条告警意味着什么

OmniGuardian 的元配置 `docs/archmap.yaml` 无法加载。可能的原因:
- 文件被删除或移动
- YAML 语法损坏
- 文件权限问题

当前 guarded_write 处于**降级 fallback 模式**:
- 只放行硬编码的框架核心 drawer (core/bus/protocol/primitives/runtime, .omni/)
- 其他任何路径的写入都会抛 `ShieldViolation`
- 整个门禁实际上**不在完整工作**

## 请下一个 agent / human 立刻做

1. 检查 `docs/archmap.yaml` 是否存在、是否可读、YAML 是否合法
2. 如果文件丢了,从 git 恢复: `git checkout HEAD -- docs/archmap.yaml`
3. 如果是语法错误,修复到 `omni guardian archmap validate` 通过
4. 恢复后删除本 `.omni/GUARDIAN_ALERT.md` 文件
5. 跑一遍 `omni guardian patrol --full` 确认无异常

## 绝对不要做

- 不要假装"门禁不重要"绕过这个告警
- 不要把这个文件加进 .gitignore
- 不要在修好前继续新架构改动
"""
        _GUARDIAN_ALERT.write_text(banner, encoding="utf-8")
    except OSError as e:
        logger.error("无法写 GUARDIAN_ALERT.md: %s", e)
    logger.error(
        "\n\n🚨🚨🚨 OmniGuardian 元配置失效 🚨🚨🚨\n"
        "archmap.yaml 不可加载: %s\n"
        "已写 .omni/GUARDIAN_ALERT.md,请立刻修复!\n\n",
        reason,
    )


# ─── writer identity 归一化 ─────────────────────────────────

def _origin_to_writer(origin: str, archmap: ArchMap) -> str:
    """把 OmniMark origin 字段映射到 writer identity。

      sw-implement / workflow-factory / ... → internal-pipeline
      claude-code → claude-code (本身就是合法 identity)
      human → human
      其他未知值 → unknown
    """
    if not origin:
        return "unknown"
    if origin in archmap.writer_identities:
        return origin
    if archmap.is_internal_pipeline_origin(origin):
        return "internal-pipeline"
    return "unknown"


def _normalize_to_repo_relative(path: str | Path) -> str:
    """归一化路径为相对仓库根的 posix 字符串。

    S3e.2 (2026-04-08) 起必须做真 resolve,否则 '..' 变体能绕过 drawer 判定:
      'src/omnicompany/packages/x/../../../../../config/y.yaml'
    字符串前缀看是 packages/,OS 写入却落到 config/。
    这里用 Path.resolve() 先实际 resolve,再相对于仓库根判定。

    resolve 后若跳出仓库根 → 返回原样 posix(后续 drawer 判定会失配拦截)。
    """
    p = Path(path)
    # 1. 非绝对 → 拼到仓库根后 resolve
    if not p.is_absolute():
        p = (_PROJECT_ROOT / p)
    # 2. resolve 实际路径(处理 .. 和符号链接)
    try:
        resolved = p.resolve(strict=False)
    except (OSError, RuntimeError):
        # resolve 失败(路径怪异) → 退回原始字符串模式
        return Path(path).as_posix().lstrip("./")
    # 3. 相对仓库根
    try:
        rel = resolved.relative_to(_PROJECT_ROOT)
        return rel.as_posix()
    except ValueError:
        # 跳出仓库根 → 返回绝对路径原样,后续 is_writable 会判不在任何 drawer 里
        return resolved.as_posix()


# ─── 审计 ────────────────────────────────────────────────────

_audit_counter = 0


def _next_audit_id() -> str:
    global _audit_counter
    _audit_counter += 1
    ts = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    return f"SHD-{ts}-{_audit_counter:04d}"


def _record_audit(
    audit_id: str,
    rel_path: str,
    origin: str,
    writer: str,
    trace: str,
    node: str,
    purpose: str,
    check: WriteCheck,
    verdict: str,
    enforce_mode: bool,
    archmap: Optional[ArchMap] = None,
) -> None:
    """写一条 .omni/shield_audit.jsonl 记录。永远写,不论 verdict。

    S3e.3: 如果 path 是 protected_key_file, 在 record 里加 audit_label
    字段, sentinel/trace-violation 可以特殊渲染。
    """
    record = {
        "audit_id": audit_id,
        "ts": datetime.now(timezone.utc).isoformat(),
        "path": rel_path,
        "origin": origin,
        "writer": writer,
        "trace_id": trace or None,
        "node_id": node or None,
        "purpose": purpose or "",
        "verdict": verdict,
        "drawer": f"{check.drawer_layer}.{check.drawer}" if check.drawer else "(none)",
        "always_green": check.always_green,
        "agent_free_fire": check.agent_free_fire,
        "reason": check.reason,
        "enforce_mode": enforce_mode,
    }
    # key_file 的额外审计标签
    if archmap is not None and rel_path in archmap.protected_key_files:
        spec = archmap.protected_key_files[rel_path]
        record["audit_label"] = spec.get("audit_label", "META_CONFIG_CHANGE")
        record["meta_config_change"] = True
    try:
        _AUDIT_LOG.parent.mkdir(parents=True, exist_ok=True)
        with _AUDIT_LOG.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except OSError as e:
        logger.debug("guarded_write: audit log write failed: %s", e)


# ─── 状态查询 ────────────────────────────────────────────────

def shield_status() -> dict:
    """CLI helper: 返回当前 Shield 状态(audit log 行数 + enforce mode)。"""
    try:
        archmap = load_archmap()
        enforce = archmap.enforce_mode
        version = archmap.version
    except Exception as e:
        return {"error": f"archmap load failed: {e}"}

    audit_count = 0
    if _AUDIT_LOG.exists():
        try:
            with _AUDIT_LOG.open("r", encoding="utf-8") as f:
                for _ in f:
                    audit_count += 1
        except OSError:
            pass

    return {
        "mode": "enforce" if enforce else "audit",
        "audit_only": not enforce,
        "enforce_mode": enforce,
        "archmap_version": version,
        "total_audited": audit_count,
        "audit_log": str(_AUDIT_LOG),
        "project_root": str(_PROJECT_ROOT),
    }


# ─── 主入口 ──────────────────────────────────────────────────

@dataclass
class WriteResult:
    path: str
    audit_id: str
    verdict: str            # 'allowed' | 'audit_only_warn' | 'denied' | 'bypassed'
    drawer: str
    always_green: bool
    stamped: bool
    bypassed: bool


def write_file(
    path: str | Path,
    content: str,
    *,
    origin: str = "unknown",
    domain: str = "",
    trace: str = "",
    node: str = "",
    purpose: str = "",
    agent_name: str = "",
    is_temp: bool = False,
    overwrite_stamp: bool = False,
    writer: Optional[str] = None,
) -> WriteResult:
    """统一文件写入入口（纯规则门禁版,S3c.4）。

    Args:
        path:            目标路径(相对项目根或绝对)
        content:         写入内容
        origin:          OmniMark origin 字段(human / claude-code / sw-implement / ...)
        domain:          OmniMark domain 字段
        trace:           产生该写入的 trace_id
        node:            产生该写入的 node_id
        purpose:         业务原因(进审计)
        agent_name:      LLM 模型名
        is_temp:         临时文件标记
        overwrite_stamp: 已有 OmniMark 头时是否覆盖
        writer:          显式 writer identity(覆盖 origin → writer 自动映射)

    Returns:
        WriteResult

    Raises:
        ShieldViolation: 100% 违规时(forbidden_root_glob 永远抛;
                         其他违规仅在 archmap.enforce_mode=true 时抛)
    """
    p = Path(path)

    # 1. 紧急 bypass (S3e.2: 两种路径, env var 向后兼容 + 文件哨兵)
    bypass_source = _check_bypass_active()
    if bypass_source:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        # bypass 模式每次都大声 audit(不仅记日志)
        audit_id = _next_audit_id()
        try:
            _AUDIT_LOG.parent.mkdir(parents=True, exist_ok=True)
            with _AUDIT_LOG.open("a", encoding="utf-8") as f:
                f.write(json.dumps({
                    "audit_id": audit_id,
                    "ts": datetime.now(timezone.utc).isoformat(),
                    "path": str(p),
                    "origin": origin,
                    "writer": writer or "unknown",
                    "verdict": f"bypassed:{bypass_source}",
                    "drawer": "(bypassed)",
                    "purpose": purpose,
                    "trace_id": trace or None,
                    "node_id": node or None,
                    "WARNING": "OmniGuardian bypass active — architecture protection disabled",
                }, ensure_ascii=False) + "\n")
        except OSError:
            pass
        logger.warning(
            "[guarded_write BYPASS active:%s] %s origin=%s — Guardian 关闭中,写入未经审计",
            bypass_source, p, origin,
        )
        return WriteResult(
            path=str(p), audit_id=audit_id, verdict=f"bypassed:{bypass_source}",
            drawer="(bypassed)", always_green=False, stamped=False, bypassed=True,
        )

    # 2. 加载 archmap
    try:
        archmap = load_archmap()
    except ArchMapError as e:
        # S3e.2 起 archmap 加载失败不再默默全放行。
        # 做法:
        #   - 尖叫一声 (logger.error + 写 .omni/GUARDIAN_ALERT.md 跳闸文件)
        #   - 只放行 always_green 核心 drawer 的已知路径(包内核心必须能写)
        #   - 其他路径返回 verdict=archmap-unavailable,但 RAISE ShieldViolation
        #     而不是静默放行,让调用方感知到门禁不在
        _raise_guardian_alert(e)
        rel = _normalize_to_repo_relative(p)
        # 硬编码的 always_green 兜底(和 archmap 的 always_green 列表一致)
        _FALLBACK_ALWAYS_GREEN = (
            "src/omnicompany/core/",
            "src/omnicompany/bus/",
            "src/omnicompany/protocol/",
            "src/omnicompany/primitives/",
            "src/omnicompany/runtime/",
            ".omni/",
        )
        if any(rel.startswith(g) for g in _FALLBACK_ALWAYS_GREEN):
            logger.warning(
                "guarded_write: archmap 不可用,但 %s 在硬编码兜底白名单内,放行",
                rel,
            )
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content, encoding="utf-8")
            return WriteResult(
                path=str(p), audit_id="(archmap-unavailable-fallback)",
                verdict="archmap-unavailable-fallback", drawer="(fallback)",
                always_green=True, stamped=False, bypassed=False,
            )
        raise ShieldViolation(
            "ARCHMAP-UNAVAILABLE", str(p),
            f"OmniGuardian 元配置 archmap.yaml 无法加载: {e}\n"
            f"Guardian 处于降级状态,只放行框架内核心 drawer。"
            f"请让 human 检查 docs/archmap.yaml 是否被删除或损坏,"
            f"并查看 .omni/GUARDIAN_ALERT.md 获取详细告警。",
            audit_id="(archmap-unavailable)",
        )

    # 3. 路径归一化 + writer identity 映射
    rel_path = _normalize_to_repo_relative(p)
    resolved_writer = writer or _origin_to_writer(origin or "unknown", archmap)

    # 4. 纯规则判定 (S3e.3: 传 has_purpose, key_file 校验用)
    check: WriteCheck = archmap.is_writable(
        rel_path, resolved_writer, has_purpose=bool(purpose and purpose.strip()),
    )
    audit_id = _next_audit_id()

    # 5. 三档处置
    if check.allowed:
        verdict = "allowed"
        _record_audit(audit_id, rel_path, origin, resolved_writer,
                      trace, node, purpose, check, verdict, archmap.enforce_mode, archmap=archmap)
        # 落到下面的实际写入

    else:
        # 区分绝对违规 vs 软违规
        is_absolute = (
            check.drawer == "(root forbidden)"
            or check.drawer_layer == "none"
        )

        if is_absolute or archmap.enforce_mode:
            # 硬抛
            verdict = "denied"
            _record_audit(audit_id, rel_path, origin, resolved_writer,
                          trace, node, purpose, check, verdict, archmap.enforce_mode, archmap=archmap)
            rule_id = "FORBIDDEN-ROOT-GLOB" if check.drawer == "(root forbidden)" \
                       else "NON-WRITABLE-PATH"
            logger.error(
                "[guarded_write DENY] %s %s writer=%s → %s",
                audit_id, rel_path, resolved_writer, check.reason,
            )
            raise ShieldViolation(rule_id, rel_path, check.reason, audit_id=audit_id)
        else:
            # 软违规 + 观察期 → 警告 + 放行
            verdict = "audit_only_warn"
            _record_audit(audit_id, rel_path, origin, resolved_writer,
                          trace, node, purpose, check, verdict, archmap.enforce_mode, archmap=archmap)
            logger.warning(
                "[guarded_write SOFT-DENY %s] %s writer=%s → %s (audit_only,放行)",
                audit_id, rel_path, resolved_writer, check.reason,
            )

    # 6. 实写
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")

    # 7. OmniMark 贴头(适合的文件类型)
    stamped = False
    if _stampable(p):
        try:
            stamped = stamp_file(
                p,
                origin=origin or "unknown",
                domain=domain,
                agent=agent_name,
                trace=trace,
                node=node,
                overwrite=overwrite_stamp,
            )
        except Exception as e:
            logger.debug("guarded_write: stamp failed for %s: %s", p, e)

    logger.debug(
        "[guarded_write %s] %s verdict=%s drawer=%s.%s origin=%s writer=%s stamped=%s",
        audit_id, rel_path, verdict, check.drawer_layer, check.drawer,
        origin, resolved_writer, stamped,
    )

    return WriteResult(
        path=str(p),
        audit_id=audit_id,
        verdict=verdict,
        drawer=f"{check.drawer_layer}.{check.drawer}",
        always_green=check.always_green,
        stamped=stamped,
        bypassed=False,
    )


# ─── S3e.2 删除 / 改名 包装 ────────────────────────────────
#
# 内部代码主动走这俩 API 就能享受门禁保护。bash 侧已经在
# tool_executor._bash_write_path_check 抓大头(rm/mv),这里是 Python 侧入口。
# 外部攻击者仍然可以 os.remove 绕过——这是君子协定,但:
#   - patrol 会在下次扫发现 archmap 丢失(GUARDIAN_ALERT 跳闸)
#   - 写入路径仍受 write_file 保护
#   - 删除后要重新写入同一路径时也会被拦

def guarded_remove(
    path: str | Path, *,
    origin: str = "unknown",
    trace: str = "",
    node: str = "",
    purpose: str = "",
    writer: Optional[str] = None,
) -> bool:
    """删除文件(走 archmap 门禁 + 审计)。

    判定策略: 删除需要写权限(同 drawer 判定)。key_files 永远不能删。
    Returns: True=已删 / False=bypass 下放行
    Raises: ShieldViolation / FileNotFoundError
    """
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(str(p))

    # bypass
    if _check_bypass_active():
        p.unlink()
        logger.warning("[guarded_remove BYPASS] %s 已删", p)
        return False

    try:
        archmap = load_archmap()
    except ArchMapError as e:
        _raise_guardian_alert(e)
        raise ShieldViolation(
            "ARCHMAP-UNAVAILABLE", str(p),
            f"archmap 不可用,guarded_remove 拒绝操作: {e}",
        )

    rel_path = _normalize_to_repo_relative(p)
    resolved_writer = writer or _origin_to_writer(origin or "unknown", archmap)
    check = archmap.is_writable(rel_path, resolved_writer,
                                 has_purpose=bool(purpose and purpose.strip()))
    audit_id = _next_audit_id()

    if not check.allowed:
        _record_audit(audit_id, rel_path, origin, resolved_writer,
                      trace, node, purpose + " [REMOVE]", check, "denied-remove",
                      archmap.enforce_mode, archmap=archmap)
        raise ShieldViolation("NON-REMOVABLE-PATH", rel_path, check.reason, audit_id)

    _record_audit(audit_id, rel_path, origin, resolved_writer,
                  trace, node, purpose + " [REMOVE]", check, "allowed-remove",
                  archmap.enforce_mode)
    p.unlink()
    logger.info("[guarded_remove %s] %s writer=%s 已删", audit_id, rel_path, resolved_writer)
    return True


def guarded_rename(
    src: str | Path, dst: str | Path, *,
    origin: str = "unknown",
    trace: str = "",
    node: str = "",
    purpose: str = "",
    writer: Optional[str] = None,
) -> bool:
    """改名/移动(走 archmap 门禁 + 审计)。

    判定策略: src 和 dst 都必须有写权限。任一 key_file 直接拒。
    """
    src_p = Path(src)
    dst_p = Path(dst)
    if not src_p.exists():
        raise FileNotFoundError(str(src_p))

    if _check_bypass_active():
        src_p.rename(dst_p)
        logger.warning("[guarded_rename BYPASS] %s -> %s", src_p, dst_p)
        return False

    try:
        archmap = load_archmap()
    except ArchMapError as e:
        _raise_guardian_alert(e)
        raise ShieldViolation(
            "ARCHMAP-UNAVAILABLE", str(src_p),
            f"archmap 不可用,guarded_rename 拒绝操作: {e}",
        )

    src_rel = _normalize_to_repo_relative(src_p)
    dst_rel = _normalize_to_repo_relative(dst_p)
    resolved_writer = writer or _origin_to_writer(origin or "unknown", archmap)
    src_check = archmap.is_writable(src_rel, resolved_writer)
    dst_check = archmap.is_writable(dst_rel, resolved_writer)
    audit_id = _next_audit_id()

    if not src_check.allowed or not dst_check.allowed:
        reason = (
            f"src: {src_check.reason if not src_check.allowed else 'ok'} | "
            f"dst: {dst_check.reason if not dst_check.allowed else 'ok'}"
        )
        _record_audit(audit_id, f"{src_rel} -> {dst_rel}", origin, resolved_writer,
                      trace, node, purpose + " [RENAME]", src_check, "denied-rename",
                      archmap.enforce_mode, archmap=archmap)
        raise ShieldViolation("NON-MOVABLE-PATH", f"{src_rel} -> {dst_rel}", reason, audit_id)

    _record_audit(audit_id, f"{src_rel} -> {dst_rel}", origin, resolved_writer,
                  trace, node, purpose + " [RENAME]", src_check, "allowed-rename",
                  archmap.enforce_mode, archmap=archmap)
    dst_p.parent.mkdir(parents=True, exist_ok=True)
    src_p.rename(dst_p)
    logger.info("[guarded_rename %s] %s -> %s", audit_id, src_rel, dst_rel)
    return True


# ─── S3e.2 sentinel 完整性检查 ─────────────────────────────

def sentinel_check_guardian_integrity() -> dict:
    """quick health check — sentinel / 定时任务调用。

    检查:
      - docs/archmap.yaml 存在 + 可 load + validate 通过
      - enforce_mode 是 true (任何人偷偷翻了就警告)
      - 没有异常的 .omni/DISABLE_BYPASS 文件(除非 human 签名)
      - .omni/GUARDIAN_ALERT.md 不存在(有就意味着有未处理告警)

    任一项异常,除了返回结构之外还会写 GUARDIAN_ALERT.md。
    """
    report: dict = {
        "ok": True,
        "archmap_loadable": False,
        "archmap_valid": False,
        "enforce_mode": None,
        "bypass_active": _check_bypass_active() or None,
        "alert_present": _GUARDIAN_ALERT.exists(),
        "issues": [],
    }
    try:
        m = load_archmap(force_reload=True)
        report["archmap_loadable"] = True
        errors = m.validate()
        report["archmap_valid"] = not errors
        report["enforce_mode"] = m.enforce_mode
        if errors:
            report["issues"].append(f"archmap.validate failed: {errors}")
        if not m.enforce_mode:
            report["issues"].append("enforce_mode=false (观察期模式,软违规不拦)")
    except Exception as e:
        report["issues"].append(f"archmap load failed: {e}")
        _raise_guardian_alert(e)

    if report["bypass_active"]:
        report["issues"].append(f"bypass active via {report['bypass_active']}")
    if report["alert_present"]:
        report["issues"].append("存在未处理的 GUARDIAN_ALERT.md")

    report["ok"] = not report["issues"]
    return report


# ─── 文件类型判定 ──────────────────────────────────────────

_STAMPABLE_SUFFIXES = frozenset({
    ".py", ".pyi", ".sh", ".bash", ".yaml", ".yml", ".toml", ".ini",
    ".md", ".html", ".htm", ".css", ".js", ".ts", ".tsx", ".jsx",
    ".json",   # JSON 不能注释,stamp_file 内部会跳过
})


def _stampable(p: Path) -> bool:
    if p.suffix.lower() not in _STAMPABLE_SUFFIXES:
        return False
    parts = [pt.replace("\\", "/") for pt in p.parts]
    for bad in ("__pycache__", "node_modules", ".git", ".omni/quarantine"):
        if any(bad in pt for pt in parts):
            return False
    return True
