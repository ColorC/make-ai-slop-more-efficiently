# [OMNI] origin=ai-ide domain=cli/commands ts=2026-05-04T00:00:00Z type=router status=active agent=ai-ide
# [OMNI] summary="omni guide / omni reflect 指引组 (CLI-PHASE3 第五段). guide cat 模板向导 (跟 sandbox guide 同源); reflect 调 LLMClient 做语义反思 (kind+content+target 是否匹配)"
# [OMNI] why="PHASE3 plan §5.5 指引组. 给 AI IDE 拿规范向导 + 调 LLM 反思'这内容跟这位置匹配吗', 不让 agent 盲目落盘"
# [OMNI] tags=cli,guide,reflect,phase3,llm
# [OMNI] material_id="material:cli.commands.phase3_guide_reflect.implementation.py"
"""omni guide / omni reflect — CLI-PHASE3 第五段指引组.

`omni guide --kind=<kind>` — cat 对应模板向导 (templates/<kind>/向导.md), 等价
                              `omni sandbox guide --kind=<kind>` 但顶级命令更短.
`omni reflect --kind=<kind> --content=<file> --target=<path>` — LLM 反思
                              "这内容写到这位置合适吗", 输出判定 + 建议.

调用 LLMClient 走统一接口, 留痕事件总线.
"""
from __future__ import annotations

import json
from pathlib import Path

import click


def _project_root() -> Path:
    here = Path(__file__).resolve()
    for p in (here, *here.parents):
        if (p / "src" / "omnicompany").is_dir() and (p / "docs").is_dir():
            return p
    return here.parents[4]


KIND_CHOICES = [
    "agent", "hook", "tool", "material", "team", "worker",
    "data", "plan", "template", "header",
]


@click.command("guide")
@click.option("--kind", required=True, type=click.Choice(KIND_CHOICES),
              help="模板向导 kind (含 header 显示 OmniMark 头规范)")
def cmd_guide(kind: str) -> None:
    """显示某 kind 的填写向导 (templates/<kind>/向导.md).

    跟 omni sandbox guide --kind=<X> 同源, 但顶级命令调用更短:
        omni guide --kind=plan
    """
    proj = _project_root()
    if kind == "header":
        guide = proj / "docs" / "standards" / "cli" / "omni-header.md"
    else:
        guide = proj / "templates" / kind / "向导.md"
    if not guide.is_file():
        click.echo(f"向导文件不存在: {guide}", err=True)
        raise SystemExit(1)
    click.echo(guide.read_text(encoding="utf-8"))


_REFLECT_SYSTEM_PROMPT = """\
你帮 AI IDE 反思一份内容跟目标位置是否匹配.

# 输入
- kind: 内容的 omnicompany 概念类别 (agent / hook / tool / material / team / worker / data / plan / template)
- content_text: 内容全文 (可能是 Python 源 / Markdown / YAML)
- target_path: 计划写入的位置 (相对项目根)
- location_pattern: 该 kind 的位置规范模式 (来自模板)
- naming_pattern: 该 kind 的命名规范模式

# 你判断
对照 kind / location_pattern / naming_pattern 三件事, 判 content 写到 target_path:
- 是否合适 (verdict: "match" / "mismatch" / "uncertain")
- 不合适或 uncertain 时, 给一句话原因 (reason)
- 给一条改进建议 (suggestion)

# 输出
只输出一段 JSON, 不要别的:

```json
{
  "verdict": "match",
  "reason": "一句话(≤40字)",
  "suggestion": "怎么改(≤80字)"
}
```
"""


@click.command("reflect")
@click.option("--kind", required=True, type=click.Choice(KIND_CHOICES[:-1]),
              help="内容概念类别 (不含 header)")
@click.option("--content", required=True,
              type=click.Path(exists=True, dir_okay=False, file_okay=True),
              help="待反思内容文件路径")
@click.option("--target", required=True,
              help="计划写入的目标路径 (相对项目根)")
@click.option("--json", "as_json", is_flag=True, help="JSON 格式输出")
def cmd_reflect(kind: str, content: str, target: str, as_json: bool) -> None:
    """LLM 语义反思: 这内容写到这位置合适吗.

    流程:
      1. 加载 templates/<kind>/注册件.yaml 拿 instance_location/naming pattern
      2. 读 content 文件全文
      3. 调 LLMClient.call(qwen-3.6-plus) 给反思判定
      4. 输出 verdict (match/mismatch/uncertain) + reason + suggestion
    """
    proj = _project_root()
    content_path = Path(content).resolve()
    try:
        content_text = content_path.read_text(encoding="utf-8")
    except OSError as e:
        click.echo(f"错误: 读 content 失败 {e}", err=True)
        raise SystemExit(1)

    # 加载模板 pattern (复用 registration._load_kind_template)
    from .registration import _load_kind_template
    template = _load_kind_template(kind, proj) or {}
    location_pattern = (template.get("instance_location") or {}).get("pattern", "")
    naming_pattern = (template.get("instance_naming") or {}).get("pattern", "")

    user_msg = (
        f"kind: {kind}\n"
        f"target_path: {target}\n"
        f"location_pattern: {location_pattern or '(无)'}\n"
        f"naming_pattern: {naming_pattern or '(无)'}\n\n"
        f"--- content_text 开始 ---\n{content_text[:6000]}\n--- content_text 结束 ---\n\n"
        f"按 system 指令的 JSON 格式输出反思."
    )

    try:
        from omnicompany.runtime.llm.llm import LLMClient
        client = LLMClient(role="runtime_main")
        response = client.call(
            messages=[{"role": "user", "content": user_msg}],
            system=_REFLECT_SYSTEM_PROMPT,
        )
    except Exception as e:
        click.echo(f"错误: LLM 调用失败 {type(e).__name__}: {e}", err=True)
        raise SystemExit(1)

    raw = "".join(
        getattr(b, "text", "")
        for b in response.content
        if getattr(b, "type", "text") == "text"
    )

    # 抠 JSON (跟 prompt_antipattern_scanner 同模式)
    import re
    parsed = None
    m = re.search(r"```json\s*(\{.*?\})\s*```", raw, re.DOTALL)
    if m:
        try:
            parsed = json.loads(m.group(1))
        except json.JSONDecodeError:
            pass
    if parsed is None:
        # 兜底: 找含 verdict 的裸 JSON
        for i, c in enumerate(raw):
            if c != "{":
                continue
            depth, in_str, escape = 0, False, False
            end = -1
            for j in range(i, len(raw)):
                ch = raw[j]
                if in_str:
                    if escape: escape = False
                    elif ch == "\\": escape = True
                    elif ch == '"': in_str = False
                else:
                    if ch == '"': in_str = True
                    elif ch == "{": depth += 1
                    elif ch == "}":
                        depth -= 1
                        if depth == 0:
                            end = j
                            break
            if end > i and '"verdict"' in raw[i:end + 1]:
                try:
                    parsed = json.loads(raw[i:end + 1])
                    break
                except json.JSONDecodeError:
                    continue

    if parsed is None:
        click.echo(f"错误: LLM 返回无法解析 JSON. raw 前 300 字: {raw[:300]}", err=True)
        raise SystemExit(2)

    out = {
        "kind": kind,
        "content_path": content_path.name,
        "target_path": target,
        "verdict": parsed.get("verdict", "uncertain"),
        "reason": parsed.get("reason", ""),
        "suggestion": parsed.get("suggestion", ""),
    }

    if as_json:
        click.echo(json.dumps(out, ensure_ascii=False, indent=2))
    else:
        verdict = out["verdict"]
        color = {"match": "green", "mismatch": "red", "uncertain": "yellow"}.get(verdict, "white")
        click.echo(click.style(f"verdict: {verdict}", fg=color, bold=True))
        if out["reason"]:
            click.echo(f"  reason     : {out['reason']}")
        if out["suggestion"]:
            click.echo(f"  suggestion : {out['suggestion']}")
