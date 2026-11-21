# [OMNI] origin=ai-ide domain=dashboard ts=2026-05-02T10:30:00Z type=helper status=active agent=ai-ide-current
# [OMNI] summary="Native IDE agent 上下文注入器 — 读项目状态文件拼一段 markdown 嵌入 prompt"
# [OMNI] why="2026-04-18 立 router 化铁律后旧 assistant_context_builder 已搬到 _legacy 不可用. 新轻量版只做最有用的: 读 PROGRESS.md 头部让 agent 知道项目当前主轴 / 焦点 / 下一步. 不复活 assistant_db SQL 设施."
# [OMNI] tags=agent,context,native,prompt
# [OMNI] material_id="material:dashboard.native_agent.prompt_context_builder.py"
"""Native IDE agent 上下文段构造器.

设计原则:
- **轻量**: 不依赖 sqlite / assistant_db / 旧 _legacy 设施
- **从文件读**: 直接读 `<cwd>/docs/PROGRESS.md` (项目状态权威)
- **turn=0 一次性**: 跟新 AgentNodeLoop "system_prompt 一次构造" 精神一致
  跑完 session 中途改 PROGRESS.md agent 看不到 — 跟新架构妥协
- **失败静默 (返回空字符串)**: PROGRESS.md 不在 / 读失败不阻塞 agent 跑

未来 (round 4+) 可扩:
- active plan 显式标记 (用户通过 SessionContextPanel work_type / 当前 plan)
- standards 用户挑 → 注入对应规范文件头
- TodoWrite 上次 session 残留 todos 接进新 session
"""

from __future__ import annotations

from pathlib import Path


_PROGRESS_HEAD_LINES = 80  # PROGRESS.md 头部行数 (覆盖主轴 / 焦点 / 下一步段)


def build_context_section(cwd: str, active_plan: str | None = None) -> str:
    """构造 # Live state markdown section, 拼到 system prompt 末尾.

    包含 (按存在与否拼):
      1. 项目主轴 (PROGRESS.md 头)
      2. active project (project.md 内容, 立于 plan 之上 — vision + 退出条件)
      3. active plan (plan.md 头部 — 当前阶段任务)

    返回空字符串当三者都不存在.
    """
    cwd_path = Path(cwd)
    if not cwd_path.exists():
        return ""

    parts: list[str] = []

    # (1) PROGRESS.md 头
    progress_md = cwd_path / "docs" / "PROGRESS.md"
    if progress_md.exists():
        try:
            text = progress_md.read_text(encoding="utf-8")
            head = "\n".join(text.splitlines()[:_PROGRESS_HEAD_LINES]).rstrip()
            if head:
                parts.append(
                    "## Project main thread (`docs/PROGRESS.md` head)\n\n"
                    f"```markdown\n{head}\n```\n"
                )
        except Exception:
            pass

    # (2) project.md (active plan 所属 project, 立于 plan 之上)
    # (3) plan.md (active plan 自己)
    if active_plan:
        try:
            from omnicompany.dashboard.controlplane.plans import find_project_for_plan, _plans_root
            plans_root = _plans_root()
            # project.md
            found = find_project_for_plan(active_plan)
            if found:
                _, project_md = found
                try:
                    txt = project_md.read_text(encoding="utf-8")
                    parts.append(
                        f"## Active project context (`{project_md.relative_to(cwd_path)}`)\n\n"
                        "Project-level vision + exit_criteria — 立于 plan 之上, 跨 plan 共享:\n\n"
                        f"```markdown\n{txt.rstrip()}\n```\n"
                    )
                except Exception:
                    pass
            # plan.md
            plan_md = plans_root / active_plan / "plan.md"
            if plan_md.is_file():
                try:
                    txt = plan_md.read_text(encoding="utf-8")
                    head = "\n".join(txt.splitlines()[:120]).rstrip()
                    parts.append(
                        f"## Active plan (`{plan_md.relative_to(cwd_path)}` head)\n\n"
                        f"```markdown\n{head}\n```\n"
                    )
                except Exception:
                    pass
        except Exception:
            pass

    if not parts:
        return ""

    return (
        "\n# Live state\n"
        "Below are the project's authoritative state files. Read these before reporting progress "
        "or making suggestions — they're the truth source for project main thread, active project "
        "(vision + exit_criteria) and active plan (current phase tasks).\n\n"
        + "\n".join(parts)
    )


__all__ = ["build_context_section"]
