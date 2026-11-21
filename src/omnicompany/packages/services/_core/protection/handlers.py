# [OMNI] origin=ai-ide domain=services/_core/protection ts=2026-05-02T04:00:00Z type=service status=active agent=ai-ide-current
# [OMNI] summary="protection handlers - 违规处理 (内部错位留 notice / 外部直接写移除留指导)"
# [OMNI] why="用户硬规则: 内部错位 → 源头注释引用规范; 外部直接 → 移除原地留指导. 这里是处理函数, 跟 scanner 分开"
# [OMNI] tags=protection,handlers,notice,evict,quarantine
# [OMNI] material_id="material:core.protection.remediation_handlers.enforcement_engine.py"
"""protection 违规处理.

`handle_internal_misplace(violation)`: 内部错位
  - 文件还在 (不删)
  - 在文件头加 OMNI-LOCK-VIOLATION 注释行 + 引用规范文档教正确写法
  - 返回处理报告

`handle_external_write(violation)`: 外部直接写入
  - 文件移到 quarantine/<YYYY-MM-DD-HHMM>/ 隔离区
  - 原地留 .OMNI-EVICTED.md 指导文件 (注册身份 + 合法方式)
  - 返回处理报告
"""
from __future__ import annotations

import shutil
import time
from pathlib import Path

from omnicompany.packages.services._core.protection.policy import _project_root
from omnicompany.packages.services._core.protection.scanner import Violation


def quarantine_dir() -> Path:
    """隔离区路径 .omni/quarantine/."""
    return _project_root() / ".omni" / "quarantine"


def _comment_prefix_for(file_path: Path) -> str:
    """按文件类型决定注释前缀."""
    suffix = file_path.suffix.lower()
    if suffix in (".py", ".yaml", ".yml", ".sh", ".toml"):
        return "#"
    if suffix in (".md", ".html"):
        return "<!--"
    if suffix in (".js", ".ts", ".jsx", ".tsx", ".css"):
        return "//"
    return "#"  # default


def _comment_suffix_for(file_path: Path) -> str:
    suffix = file_path.suffix.lower()
    if suffix in (".md", ".html"):
        return " -->"
    return ""


def handle_internal_misplace(violation: Violation, *, dry_run: bool = False) -> dict:
    """内部错位处理: 在文件头加 OMNI-LOCK-VIOLATION 注释 + 引用规范.

    不删文件, 不改业务代码, 只在头加一段 notice 让作 session 的 AI IDE 看到, 自己改.
    """
    p = Path(violation.file_path)
    if not p.is_file():
        return {"action": "skipped", "reason": "file vanished", "path": violation.rel_path}

    pref = _comment_prefix_for(p)
    suff = _comment_suffix_for(p)
    notice_lines = [
        f"{pref} ════════════════════════════════════════════════════════════════════════{suff}",
        f"{pref} OMNI-LOCK-VIOLATION · 这份文件是 internal_misplace 违规.{suff}",
        f"{pref} 写入工具: {violation.tool}  trace_id: {violation.trace_id}  时间: {violation.timestamp}{suff}",
        f"{pref} 路径不在 omnicompany 注册中心, 也不在白名单. 修法 (按规范文档):{suff}",
        f"{pref}   1. 读 docs/standards/cli/sandbox.md 跟 docs/standards/concepts/<对应 kind>.md{suff}",
        f"{pref}   2. 走 omni new --kind=<kind> --name=<name> 立沙盒草稿,{suff}",
        f"{pref}   3. 沙盒里反复改, omni sandbox check 自检通过,{suff}",
        f"{pref}   4. omni sandbox promote --content=<草稿> --target=<正式区> --kind=<kind>{suff}",
        f"{pref}      让 promote 自动调 register 注册到中心 (跟 trace_id 绑定){suff}",
        f"{pref} 直接 Edit/Write 写到 watched 路径不进注册中心, 守护扫不到管理边界, 故视为违规.{suff}",
        f"{pref} 留这段注释让你看到改正路径. 改完 (走 promote 流程注册) 后这段会自动消除.{suff}",
        f"{pref} ════════════════════════════════════════════════════════════════════════{suff}",
        "",
    ]
    notice_block = "\n".join(notice_lines)

    if dry_run:
        return {"action": "dry_run", "would_inject_lines": len(notice_lines), "path": violation.rel_path}

    try:
        text = p.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as e:
        return {"action": "failed", "reason": str(e), "path": violation.rel_path}

    # 已经有 notice 了不重复加
    if "OMNI-LOCK-VIOLATION" in text[:2000]:
        return {"action": "already_marked", "path": violation.rel_path}

    # OmniMark 头之后插入 notice (头是文件最前几行 [OMNI] 标记)
    lines = text.splitlines(keepends=True)
    insert_after = 0
    for i, line in enumerate(lines[:20]):
        if "[OMNI]" in line:
            insert_after = i + 1
        elif insert_after > 0 and "[OMNI]" not in line:
            break

    new_text = "".join(lines[:insert_after]) + notice_block + "".join(lines[insert_after:])
    p.write_text(new_text, encoding="utf-8")

    return {
        "action": "noticed",
        "path": violation.rel_path,
        "notice_lines": len(notice_lines),
        "insert_after_line": insert_after,
    }


def handle_external_write(violation: Violation, *, dry_run: bool = False) -> dict:
    """外部直接写入: 移到 quarantine + 原地留指导文件.

    quarantine/<YYYY-MM-DD-HHMM>/<原 rel_path> 保留原始内容, 原地留
    `<原文件名>.OMNI-EVICTED.md` 指导文件含: 这个文件被收走的原因 + 注册身份 +
    合法写入方式.
    """
    p = Path(violation.file_path)
    if not p.is_file():
        return {"action": "skipped", "reason": "file vanished", "path": violation.rel_path}

    if dry_run:
        return {"action": "dry_run", "would_evict": violation.rel_path}

    # 隔离区路径
    ts = time.strftime("%Y-%m-%d-%H%M")
    qdir = quarantine_dir() / ts
    qfile = qdir / violation.rel_path
    qfile.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(p), str(qfile))

    # 原地指导文件
    guide_path = p.with_name(p.name + ".OMNI-EVICTED.md")
    guide_lines = [
        "<!-- [OMNI] origin=protection-handler ts={} type=evicted-notice status=active agent=ai-ide -->".format(
            time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        ),
        "<!-- [OMNI] summary=\"原文件被守护移除, 因为外部直接写入未经 omnicompany 注册\" -->",
        "<!-- [OMNI] why=\"留这份指导给写入者, 教正确注册身份 + 合法写入方式\" -->",
        "<!-- [OMNI] tags=evicted,protection,external-write -->",
        "",
        f"# 原文件 `{p.name}` 已被 omnicompany 守护移除",
        "",
        "## 这是什么",
        "",
        f"原本在这里的文件 (`{p.name}`) 被 omnicompany 主动防御 (G4 锁组) 移除了.",
        "原因: 这份内容**不是通过 omnicompany 体系写入**的 (event bus 找不到对应的",
        "`agent.tool.call` 事件), 也不在白名单里, 也不在注册中心已记录的实体清单里.",
        "",
        "原文件已**完整保留**在隔离区:",
        f"  `{qfile}`",
        "",
        "## 为什么会发生",
        "",
        "你 (或你的工具) 直接对 omnicompany 治理范围内的目录写入了, 没经过下面任一合法流程:",
        "",
        "- 通过 Claude Code session (cc_wrapper hook 自动追踪)",
        "- 通过 omni CLI (`omni new` / `omni sandbox promote`)",
        "- 通过 omni register 显式注册",
        "",
        "守护扫描时找不到这份文件的来源 trace_id, 故视为外部直接写入并移除.",
        "",
        "## 怎么改正",
        "",
        "**步骤 1 · 注册身份**:",
        "",
        "```bash",
        "omni who                    # 看当前 session 身份",
        "omni session bind --trace-id=<your_id>  # 没身份时显式绑",
        "```",
        "",
        "**步骤 2 · 走合法写入流程** (以新立内容为例):",
        "",
        "```bash",
        "omni new --kind=<material/worker/team/agent/hook/tool/data/plan> --name=<name>",
        "# 这会在 .omni/sandbox/drafts/<kind>/<name>/ 立草稿",
        "",
        "# 编辑草稿内容",
        "",
        "omni sandbox check --content=.omni/sandbox/drafts/<kind>/<name>/   # 自检",
        f"omni sandbox promote --content=.omni/sandbox/drafts/<kind>/<name>/ --target={violation.rel_path} --kind=<kind>",
        "# promote 自动 check + move + register, 一条龙",
        "```",
        "",
        "**步骤 3 · 验证** (注册成功后):",
        "",
        "```bash",
        "omni lookup --kind=<kind> --id=<entity_id>",
        "```",
        "",
        "## 如果原文件内容很重要不想丢",
        "",
        f"从隔离区 `{qfile}` 把内容拷出来, 走上面合法流程重新写.",
        "",
        "## 删除这份指导",
        "",
        "走完上面流程, 新文件已经合法注册到 omnicompany 注册中心后, 删这份 `.OMNI-EVICTED.md`",
        "守护下次扫描就不会再警告了.",
        "",
        "## 联系",
        "",
        "如果你觉得这份移除是误判 (例如这文件应该豁免), 改",
        "`.omni/protection_policy.json` 的 whitelist_patterns 加路径模式, 然后跑",
        "`omni lock scan` 验证.",
        "",
        "守护规范: `docs/standards/cli/sandbox.md` + `docs/standards/concepts/<kind>.md`",
    ]
    guide_path.write_text("\n".join(guide_lines), encoding="utf-8")

    return {
        "action": "evicted",
        "path": violation.rel_path,
        "quarantine": str(qfile.relative_to(_project_root())),
        "guide": str(guide_path.relative_to(_project_root())),
    }
