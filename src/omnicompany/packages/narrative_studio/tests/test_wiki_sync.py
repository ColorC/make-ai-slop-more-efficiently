"""落地层 ↔ wiki 保留式写回测试(临时目录,不碰真 wiki)。"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

_SRC = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", ".."))
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

from omnicompany.packages.narrative_studio import models as m, wiki_sync, importer  # noqa: E402


def test_write_new_card_creates_file():
    with tempfile.TemporaryDirectory() as d:
        repo = Path(d)
        gt = m.GameText(id="t-card", text_type="card", title="测试卡", body="第一版正文。",
                        annotations="批注一")
        rel = wiki_sync.write_game_text(repo, gt)
        path = repo / rel
        assert path.exists()
        back = importer._parse_game_text(path, "card")
        assert back is not None
        assert "第一版正文" in (back.body or "")
        assert "批注一" in (back.annotations or "")


def test_write_preserves_unmodeled_sections():
    with tempfile.TemporaryDirectory() as d:
        repo = Path(d)
        cards = repo / "wiki" / "cards"
        cards.mkdir(parents=True)
        f = cards / "原卡.md"
        f.write_text(
            "---\nid: \"orig\"\ntype: card\ncategory: \"event.news\"\n---\n\n"
            "# 原卡\n\n## 基础\n\n- id: `orig`\n\n## 文案\n\n旧正文。\n\n"
            "## 元素\n\n保留:水杯\n\n## 卡图\n\n- art: `x.png`\n- status: `generated-unreviewed`\n\n"
            "## 关联事件\n\n保留:某事件\n\n## 创作者批注\n\n旧批注\n",
            encoding="utf-8",
        )
        gt = m.GameText(
            id="orig", text_type="card", title="原卡", body="新正文!", annotations="新批注!",
            provenance=m.Provenance(source="wiki/cards/原卡.md"),
        )
        wiki_sync.write_game_text(repo, gt)
        text = f.read_text(encoding="utf-8")
        # 建模段被更新
        assert "新正文!" in text and "旧正文" not in text
        assert "新批注!" in text and "旧批注" not in text
        # 未建模段被保留
        assert "保留:水杯" in text          # 元素
        assert "x.png" in text                 # 卡图块
        assert "保留:某事件" in text        # 关联事件
        assert 'category: "event.news"' in text  # frontmatter


def test_write_is_idempotent():
    """反复编辑既有文件不得累积空行/破坏结构(replace→replace 逐字节相等)。"""
    with tempfile.TemporaryDirectory() as d:
        repo = Path(d)
        gt = m.GameText(id="idem", text_type="card", title="幂等卡", body="正文体",
                        annotations="批注体",
                        provenance=m.Provenance(source="wiki/cards/idem.md"))
        rel = wiki_sync.write_game_text(repo, gt)   # 1 创建(模板)
        wiki_sync.write_game_text(repo, gt)         # 2 replace 既有
        twice = (repo / rel).read_text(encoding="utf-8")
        wiki_sync.write_game_text(repo, gt)         # 3 replace 既有
        thrice = (repo / rel).read_text(encoding="utf-8")
        assert twice == thrice  # replace→replace 幂等,不累积空行


def test_target_path_rejects_traversal():
    with tempfile.TemporaryDirectory() as d:
        repo = Path(d)
        bad = m.GameText(id="x", text_type="card", title="x", body="x",
                         provenance=m.Provenance(source="../../../outside.md"))
        try:
            wiki_sync.write_game_text(repo, bad)
            assert False, "应拒绝路径穿越"
        except ValueError:
            pass


def test_delete_game_text():
    with tempfile.TemporaryDirectory() as d:
        repo = Path(d)
        gt = m.GameText(id="t", text_type="event", title="待删事件", body="x")
        rel = wiki_sync.write_game_text(repo, gt)
        assert (repo / rel).exists()
        assert wiki_sync.delete_game_text(repo, gt) is True
        assert not (repo / rel).exists()
