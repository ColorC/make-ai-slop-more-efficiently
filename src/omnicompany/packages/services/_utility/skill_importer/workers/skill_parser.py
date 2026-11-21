# [OMNI] origin=claude-code domain=services/skill_importer ts=2026-04-22T00:00:00Z type=worker
# [OMNI] material_id="material:utility.skill_importer.skill_parser_implementation.py"
"""SkillParserWorker — 确定性 SKILL.md 解析 (HARD, Stage 3 Clean Migration 2026-04-22)."""
from __future__ import annotations

from pathlib import Path

from omnicompany.packages.services._core.omnicompany import Worker
from omnicompany.protocol.anchor import Verdict, VerdictKind


class SkillParserWorker(Worker):
    DESCRIPTION = (
        "解析一个 Claude Code Skill 目录 (含 SKILL.md / references/ / scripts/) "
        "为结构化 sections 列表。输出含标题层级 + 正文, 供下游做语义归纳。"
    )
    FORMAT_IN = "skill_importer.raw"
    FORMAT_OUT = "skill_importer.parsed_sections"

    def run(self, data: dict) -> Verdict:
        if not isinstance(data, dict) or "skill_dir" not in data:
            return Verdict(
                kind=VerdictKind.FAIL, output=data,
                diagnosis="input 必须含 skill_dir 字段",
            )

        skill_dir = Path(data["skill_dir"])
        skill_md = skill_dir / "SKILL.md"

        if not skill_md.exists():
            return Verdict(
                kind=VerdictKind.FAIL, output=data,
                diagnosis=f"SKILL.md not found at {skill_md}",
            )

        content = skill_md.read_text(encoding="utf-8")

        sections: list[dict] = []
        for line in content.split("\n"):
            if line.startswith("#"):
                level = len(line) - len(line.lstrip("#"))
                sections.append(
                    {"title": line.strip("# ").strip(), "level": level, "body": ""}
                )
            elif sections:
                sections[-1]["body"] += line + "\n"

        reference_contents: dict[str, str] = {}
        ref_dir = skill_dir / "references"
        if ref_dir.exists():
            for ref in ref_dir.rglob("*.md"):
                rel = ref.relative_to(ref_dir)
                try:
                    reference_contents[str(rel).replace("\\", "/")] = ref.read_text(encoding="utf-8")
                except OSError:
                    continue

        scripts_contents: dict[str, str] = {}
        scripts_dir = skill_dir / "scripts"
        if scripts_dir.exists():
            for script in scripts_dir.iterdir():
                if script.is_file():
                    try:
                        scripts_contents[script.name] = script.read_text(encoding="utf-8")
                    except OSError:
                        continue

        skill_name = skill_dir.name

        return Verdict(
            kind=VerdictKind.PASS,
            output={
                "skill_name": skill_name,
                "skill_dir": str(skill_dir),
                "out_dir": data.get("out_dir", ""),
                "sections": sections,
                "reference_contents": reference_contents,
                "scripts_contents": scripts_contents,
                "total_skill_chars": len(content)
                + sum(len(v) for v in reference_contents.values())
                + sum(len(v) for v in scripts_contents.values()),
            },
            confidence=1.0,
            diagnosis=(
                f"parsed {len(sections)} sections, "
                f"{len(reference_contents)} reference files, "
                f"{len(scripts_contents)} scripts"
            ),
            granted_tags=["domain.skill_importer", "stage.parsed"],
        )
