# [OMNI] origin=claude-code domain=project_atlas/routers ts=2026-06-21 type=router status=active
# [OMNI] summary="collect 节点(断点续跑): enumerate 出对象清单(持久化)→ 逐对象一个小 worker 写 grounded SKILL, staging 里有了就跳过。"
# [OMNI] why="根治不是往 prompt 堆约束, 是把活拆成可续的小单元:每个 worker 只列清单/写一个 SKILL(任务小→不触发 Plan-mode), 中断重跑自动接上(objects.json + staging 文件即 checkpoint)。"
# [OMNI] tags=project_atlas,router,collect,worker,resumable
"""project_atlas.run 的 collect 节点(断点续跑式带工具收集)。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from omnicompany.protocol.anchor import Verdict, VerdictKind
from omnicompany.runtime.routing.router import Router

from .. import writer
from .._paths import PLAN_DIR, STAGING_ROOT, repo_root
from .._worker import run_claude_worker

_STANDARD_REL = "docs/plans/[2026-06-22]OMNI-RESOURCE-CENTER/object-skill-standard.md"


def _rel(p: Path) -> str:
    return p.resolve().relative_to(repo_root()).as_posix()


def _omni_inventory() -> str:
    """确定性自省 omnicompany 的真实调用面:管线名 + CLI 命令树 + domain 列表。

    给 enumerate 当'归并原料'——清单已给全, worker 就不必去 explore 整仓(那会触发 Task 子委托、在 headless 下被中断)。
    """
    lines: list[str] = []
    try:
        from omnicompany.core import registry
        from omnicompany.core.pipelines import register_all
        register_all()
        lines.append("### omni run 管线 (omni run <name>):")
        for e in registry.list_all():
            head = (e.description or "").splitlines()[0][:80] if e.description else ""
            lines.append(f"- omni run {e.name} — {head} [domain={e.domain}]")
    except Exception as ex:  # noqa: BLE001
        lines.append(f"(管线自省失败: {ex})")
    try:
        from omnicompany.cli.main import cli
        lines.append("\n### omni CLI 命令组 (omni <group> <sub>):")
        for name, cmd in sorted(cli.commands.items()):
            subs = getattr(cmd, "commands", None)
            if subs:
                lines.append(f"- omni {name}: " + ", ".join(sorted(subs.keys())))
            else:
                lines.append(f"- omni {name}")
    except Exception as ex:  # noqa: BLE001
        lines.append(f"(CLI 自省失败: {ex})")
    return "\n".join(lines)


def _enumerate_spec(space: str, root: str, objects_rel: str, inventory: str) -> str:
    inv_block = (f"\n## 真实清单(你的归并原料 —— 基于它判断, 别再去 explore 整仓)\n\n{inventory}\n"
                 if inventory else
                 "\n(无现成清单: 看 survey 的 clues.md / 快速看顶层结构, 别深挖, 别用 Task)\n")
    return f"""# Spec: 把 {space} 的能力**归并**成对象清单(只写一个 JSON 文件)

把下面这份真实清单按"操作对象 / 生产对象"(lark-cli 粒度)**归并**成 object 清单。
**只做归并分类判断 —— 不要 explore 整仓、不要用 Task 子 agent**(清单已给全, 你的活是分类合并, 不是探索):
- 参照 lark-cli: 整个"collab platform"只切 ~6 个对象, 不是每个 API 各列一个。
- 一个"对象"背后通常是好几条命令/pipeline, 合并成一个:
  `decisions` 域 + `governance decisions-run` → `decision-record`;
  `guardian` + `protection` + `governance commit-run` → `repo-governance`;
  `research` 域 + `refs` → `research`。
- **目标总数 15~30 个**; 超过就是在罗列, 重并。
- 生产类(该空间产出的内容, 如出图/简历/作品集页/演示讲解)也要覆盖。跳过弃用件/纯内容仓。
{inv_block}
写成 **JSON 数组**到 `{objects_rel}`(UTF-8 无 BOM), 每项:
`{{"object_name": "<kebab>", "object_kind": "operation"|"production", "backed_by": ["<归并了清单里哪些项>"], "one_line": "<一句概述>"}}`

**只写 `{objects_rel}` 这一个文件, 别用 Task/Plan-mode, 直接写完。**
"""


def _author_spec(space: str, obj: dict, name: str, staging_rel: str) -> str:
    backed = ", ".join(obj.get("backed_by") or []) or "(自己查)"
    return f"""# Spec: 为对象 "{name}" 写一份 grounded object-SKILL(只写一个文件)

对象: `{name}`({obj.get('object_kind', '')})。候选设施: {backed}。概述: {obj.get('one_line', '')}

1. 先读标准 `{_STANDARD_REL}`。
2. 读候选设施对应真源(`cli/commands/*.py` 定义 `omni <group> <sub>`、`core/pipelines.py` 定义 `omni run X.run`、`packages/domains/*`)**核实真实调用入口**, 可跑 `venv/Scripts/omni.exe <cmd> --help`。**绝不编造** `npx`/`index.ts`/`runXxx()`; 核不出写"（entry 待人工确认, 见路径）"。
3. 写 `{staging_rel}/{name}/SKILL.md`(UTF-8 无 BOM): frontmatter `name: {name}` + `description`(一句"做什么+何时用", 含口语触发词); 正文三段(`## 何时用/不在范围` → `## 用哪个现成设施`[名+绝对路径+核实entry] → `## 铁律`[禁另搭])。

**只写这一个 SKILL.md, 禁改其它, 直接写(headless, 别走 Plan-mode/问问题)。**
"""


class Collect(Router):
    DESCRIPTION = "收集(断点续跑): enumerate 对象清单 → 逐对象 worker 起草, 已有跳过"
    FORMAT_IN = "project_atlas.surveyed"
    FORMAT_OUT = "project_atlas.collected"
    REQUIRED_CONTEXT = ["space", "root", "run_dir"]

    def run(self, input_data: Any) -> Verdict:
        ctx = input_data if isinstance(input_data, dict) else {}
        space = ctx["space"]
        root = ctx["root"]
        run_dir = Path(ctx["run_dir"])
        staging = STAGING_ROOT / space
        staging.mkdir(parents=True, exist_ok=True)
        PLAN_DIR.mkdir(parents=True, exist_ok=True)
        objects_path = PLAN_DIR / f"{space}.objects.json"
        omni_root = repo_root()
        staging_rel = _rel(staging)
        rounds: list[dict] = []

        if ctx.get("dry_run"):
            writer.write_skill(space, {
                "name": "example-object",
                "description": "dry_run 占位(跳过 worker)。Use when ...",
                "body": "## 何时用/不在范围\n(dry_run)\n\n## 用哪个现成设施\n(dry_run)\n\n## 铁律\n禁另搭。",
            })
            return Verdict(kind=VerdictKind.PASS, output={**ctx, "worker_status": "skipped(dry_run)"},
                           diagnosis="dry_run: 跳过 worker, 落 1 个占位",
                           granted_tags=["domain.project_atlas", "stage.collected"])

        # 1) 对象清单(断点续跑真源):没有才 enumerate(有就直接复用)
        if not objects_path.exists():
            inventory = _omni_inventory() if space == "omnicompany" else ""
            if inventory:
                (run_dir / "inventory.md").write_text(inventory, encoding="utf-8")
            sp = run_dir / "enumerate_spec.md"
            sp.write_text(_enumerate_spec(space, root, _rel(objects_path), inventory), encoding="utf-8")
            res = run_claude_worker(spec_path=sp, cwd=omni_root, run_root=run_dir / "w_enumerate",
                                    permission="workspace-write", watch_rel=_rel(PLAN_DIR), timeout_s=600.0)
            rounds.append({"step": "enumerate", "status": res.get("status")})
        if not objects_path.exists():
            return Verdict(kind=VerdictKind.FAIL, output={**ctx, "worker_status": "enumerate_failed", "rounds": rounds},
                           diagnosis="enumerate 未产出对象清单, 重跑续上",
                           granted_tags=["domain.project_atlas", "stage.collected"])

        try:
            data = json.loads(objects_path.read_text(encoding="utf-8"))
        except Exception:
            return Verdict(kind=VerdictKind.FAIL, output={**ctx, "worker_status": "objects_parse_error"},
                           diagnosis=f"对象清单解析失败: {objects_path}",
                           granted_tags=["domain.project_atlas", "stage.collected"])
        objects = data.get("objects") if isinstance(data, dict) else data
        objects = [o for o in (objects or []) if isinstance(o, dict) and o.get("object_name")]
        if len(objects) > 40:
            return Verdict(
                kind=VerdictKind.FAIL,
                output={**ctx, "worker_status": f"too_granular({len(objects)})", "objects_total": len(objects)},
                diagnosis=(f"enumerate 出 {len(objects)} 个对象, 粒度过细(应 15~30 归并对象), 已中止; "
                           "删 objects.json 调 enumerate spec 后重跑"),
                granted_tags=["domain.project_atlas", "stage.collected"],
            )

        def _done(o: dict) -> bool:
            return (staging / writer.slug(o["object_name"]) / "SKILL.md").exists()

        # 2) 逐对象起草(断点续跑):已有 staging SKILL 的跳过
        todo = [o for o in objects if not _done(o)]
        authored = 0
        for o in todo:
            name = writer.slug(o["object_name"])
            sp = run_dir / f"author_{name}_spec.md"
            sp.write_text(_author_spec(space, o, name, staging_rel), encoding="utf-8")
            res = run_claude_worker(spec_path=sp, cwd=omni_root, run_root=run_dir / f"w_{name}",
                                    permission="workspace-write", watch_rel=f"{staging_rel}/{name}", timeout_s=600.0)
            rounds.append({"step": name, "status": res.get("status")})
            if _done(o):
                authored += 1

        total_done = sum(1 for o in objects if _done(o))
        remaining = len(objects) - total_done
        status = "complete" if remaining == 0 else f"partial({total_done}/{len(objects)})"
        return Verdict(
            kind=VerdictKind.PASS if remaining == 0 else VerdictKind.FAIL,  # 没写完→FAIL, 提示重跑续上
            output={**ctx, "worker_status": status, "objects_total": len(objects),
                    "done": total_done, "remaining": remaining, "authored_this_run": authored},
            diagnosis=(f"对象 {len(objects)} · 本轮起草 {authored} · 累计 {total_done} · 剩 {remaining}"
                       + ("(重跑续上)" if remaining else " ✓全完成")),
            granted_tags=["domain.project_atlas", "stage.collected"],
        )
