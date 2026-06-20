# [OMNI] origin=claude-code domain=tests ts=2026-06-20T00:00:00Z type=test status=active
"""平台冒烟测试 —— CLI 出口 + 域插件契约 + 管线构建, 全离线(不打任何 LLM API)。

覆盖此前零单测的接缝: CLI 命令出口、域 SKILL.md 契约、Team 管线拓扑构建。
在源仓和发布平台上都应全绿(只校验"有 SKILL.md 的域", 不强求每个域都有)。
"""

from __future__ import annotations

import os

os.environ.setdefault("OMNICOMPANY_SKIP_GUARDIAN_PRECHECK", "1")

import re
from pathlib import Path

import pytest
from click.testing import CliRunner

from omnicompany.cli.main import cli

runner = CliRunner()

DOMAINS_DIR = Path(__file__).resolve().parents[2] / "src" / "omnicompany" / "packages" / "domains"


# ── CLI 出口 ──────────────────────────────────────────────────────
def test_cli_help_lists_commands():
    r = runner.invoke(cli, ["--help"])
    assert r.exit_code == 0
    assert "Commands:" in r.output


@pytest.mark.parametrize("cmd", [
    ["health"],
    ["research", "--help"],
    ["decisions", "--help"],
    ["guardian", "--help"],
    ["refs", "--help"],
])
def test_cli_subcommands_exit_zero(cmd):
    r = runner.invoke(cli, cmd)
    assert r.exit_code == 0, f"omni {' '.join(cmd)} 退出码={r.exit_code}\n{r.output}"


def test_cli_unknown_command_fails_cleanly():
    r = runner.invoke(cli, ["definitely-not-a-command"])
    assert r.exit_code != 0
    assert "Error" in r.output or "No such command" in r.output


# ── 管线构建(Team 拓扑) ──────────────────────────────────────────
def test_example_domain_pipeline_builds():
    from omnicompany.packages.domains.example_domain.team import build_example_pipeline

    spec = build_example_pipeline()
    assert spec.entry == "intake"
    assert {n.id for n in spec.nodes} == {"intake", "process"}


def test_research_pipeline_topology_valid():
    from omnicompany.packages.domains.research.team import build_research_pipeline

    spec = build_research_pipeline()
    ids = {n.id for n in spec.nodes}
    assert spec.entry in ids
    assert len(spec.nodes) >= 5
    # 所有边的端点都必须是已声明的节点(拓扑自洽)
    for e in spec.edges:
        assert e.source in ids, f"边 source {e.source} 不在节点集"
        assert e.target in ids, f"边 target {e.target} 不在节点集"


# ── 域插件契约(agentskills.io: SKILL.md name == 目录名) ──────────
def test_domains_with_skill_have_matching_name():
    """有 SKILL.md 的域, 其 frontmatter name 必须等于目录名。"""
    checked = 0
    for d in sorted(DOMAINS_DIR.iterdir()):
        if not d.is_dir() or d.name.startswith(("_", ".")):
            continue
        skill = d / "SKILL.md"
        if not skill.is_file():
            continue
        checked += 1
        text = skill.read_text(encoding="utf-8")
        m = re.search(r"^name:\s*(.+?)\s*$", text, re.M)
        assert m, f"{d.name}/SKILL.md frontmatter 缺 name"
        assert m.group(1) == d.name, f"{d.name}/SKILL.md name={m.group(1)!r} != 目录名"
        desc = re.search(r"^description:\s*(.+?)\s*$", text, re.M)
        assert desc and len(desc.group(1)) >= 20, f"{d.name}/SKILL.md description 过短/缺失"
    assert checked >= 1, "至少应有一个带 SKILL.md 的域"
