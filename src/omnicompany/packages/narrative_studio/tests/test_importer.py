"""importer.import_vilo v2 契约测试(2026-06-27 重写)。

v2 真源纠正:只装已认可方向(主旨/大纲/受众/文风讨论)+ 游戏内容(cards/events);
被取代/被否决进 rejected_archive;具体情节/结构/文风/场景成文留空。
"""

from __future__ import annotations

from pathlib import Path

from omnicompany.packages.narrative_studio import importer, models as m, storage

_REPO = Path(r"E:/WindowsWorkspace/故事/vilo-wants-to-know")
_PROJECT_ROOT = Path(r"E:/WindowsWorkspace/omnicompany/data/narrative_studio/projects/vilo")


def _imported() -> m.Project:
    return importer.import_vilo(_REPO)


def test_import_meta():
    p = _imported()
    assert p.meta.id == "vilo"
    assert p.meta.name == "Vilo想知道"
    assert "parallel-universe" in (p.meta.version or "")
    assert "平行宇宙" in (p.meta.description or "")


def test_premise_clean_version_and_outline_not_autofilled():
    """立意=2026-07-02 洁净版(wiki/10,用户原话逐句重建);大纲仍不从旧 wiki/00 推导。

    用户 2026-06-27:中心命题是【故事无关的哲学命题】,≠游戏/情节概述(五母题钉定)。
    用户 2026-06-28:旧四幕大纲"完全错误"(含违反中文习惯的"想被认真看见"),清空待重整。
    用户 2026-07-02:"想被看见/照亮/发光"从未承认;立意由海筛找回的用户原话重建。
    """
    p = _imported()
    # 立意=洁净版,含五母题与爱而不得内核;禁用词不得回灌
    assert p.premise.proposition and "爱而不得" in p.premise.proposition
    assert "乌托邦" in p.premise.proposition
    for banned in ("想被看见", "想被选择", "照亮", "发光", "变亮", "碳基", "电子流",
                   "经验机器", "镜子宫殿", "渴望被理解"):
        assert banned not in p.premise.proposition, banned
    # 旧四幕大纲已弃;2026-07-04 起 beats=初版大纲(作者手稿"大纲草稿#2"骨架),禁用词同样不得出现
    for b in p.beats:
        text = " ".join(filter(None, [b.title, b.function, b.summary.sentence if b.summary else None]))
        for banned in ("想被看见", "想被选择", "照亮", "发光", "变亮", "碳基", "电子流",
                       "经验机器", "镜子宫殿", "渴望被理解"):
            assert banned not in text, f"{b.id}: {banned}"
    # 未认可的方向/理念不当真源填入
    assert p.premise.controlling_ideas == []
    assert p.premise.stance is None
    # 出处指向唯一权威 wiki/10
    assert p.premise.provenance is not None and "wiki/10" in p.premise.provenance.source


def test_outline_first_version_from_author_manuscript():
    """初版大纲(2026-07-04):七段主链 + 6-28 开局卡挂段 + 三线接口,认可状态直接标在内容上。"""
    p = _imported()
    stages = sorted((b for b in p.beats if b.parent is None), key=lambda b: b.position)
    assert [b.id for b in stages] == [
        "b-s0-common", "b-s1-know", "b-s2-meet", "b-s3-secrets",
        "b-s4-mainline", "b-s5-fate", "b-s6-epilogue",
    ]
    # 主链 edges 首尾相接;段一律 authority=author(手稿分段)
    for cur, nxt in zip(stages, stages[1:]):
        assert cur.edges == [nxt.id], cur.id
        assert cur.authority == "author"
    assert stages[-1].edges == []
    # 6-28 三卡保留且挂进对应段(共通 lane)
    by_id = {b.id: b for b in p.beats}
    assert by_id["b-open"].parent == "b-s0-common" and by_id["b-open"].authority == "author"
    assert by_id["b-traces"].parent == "b-s1-know" and by_id["b-traces"].lane == "sl-common"
    assert by_id["b-find"].parent == "b-s1-know"
    # 三线接口一律 ai-draft(未认前不冒充定稿)
    for b in p.beats:
        if b.id.startswith("b-i-"):
            assert b.authority == "ai-draft", b.id
    # 故事线轴齐备(散卡矩阵的行)
    assert {sl.id for sl in p.storylines} == {"sl-common", "sl-liang", "sl-qiu", "sl-mo"}


def test_audience_confirmed():
    p = _imported()
    assert p.audience.segments  # 受众分层非空
    assert p.audience.stance and "羞辱" in p.audience.stance  # 不羞辱"想被爱"
    assert p.audience.provenance and "13" in (p.audience.provenance.source or "")


def test_background_open_questions():
    p = _imported()
    assert p.background.open_questions  # 待定问题非空(新旧合流/开头流程等)
    assert p.background.thinking and "平行宇宙" in p.background.thinking


def test_characters_directions_only_no_superseded_specifics():
    p = _imported()
    names = {c.name for c in p.characters}
    assert "王杨" in names and "Vilo" in names
    assert len([c for c in p.characters if c.importance == "main"]) >= 7
    wy = next(c for c in p.characters if c.id == "c-wangyang")
    # 王杨:新方向(连续性锚点),不带虚拟世界 secret(奇点/真爱锁定)
    assert wy.secret is None
    assert "连续性锚点" in (wy.summary.sentence or "") + str(wy.custom_fields)


def test_game_texts_loaded_from_wiki():
    p = _imported()
    assert len(p.game_texts) > 0  # cards + events 游戏内容
    types = {g.text_type for g in p.game_texts}
    assert "card" in types
    # 至少一条卡有正文 + 来源指向 wiki
    carded = [g for g in p.game_texts if g.text_type == "card"]
    assert any(g.body for g in carded)
    assert all(g.provenance and "wiki/" in (g.provenance.source or "") for g in p.game_texts)


def test_rejected_archive_has_superseded_and_rejected():
    p = _imported()
    assert len(p.rejected_archive) > 0
    verdicts = {r.verdict for r in p.rejected_archive}
    assert "superseded" in verdicts and "rejected" in verdicts
    titles = " ".join(r.title for r in p.rejected_archive)
    assert "镜子宫殿" in titles  # 虚拟世界版被归档
    assert "枫钟" in titles      # 被否决的具体情节被归档


def test_concrete_artifacts_empty():
    """具体情节/结构/场景成文/具体文风=无认可版本 → 留空,绝不当真源。"""
    p = _imported()
    assert p.scenes == []
    assert p.endings == []
    assert p.relationships == []
    assert p.nodes == []
    assert p.prose_lines == []
    assert p.style_matrix == []


def test_save_load_roundtrip(tmp_path):
    # ⚠ 必须存到 tmp_path,绝不能存进真实生产目录(_PROJECT_ROOT)——
    # 否则每次跑测试都会拿 importer 产物覆盖线上 vilo 数据,抹掉用户在工作室里的编辑。
    p = _imported()
    root = tmp_path / "vilo"
    storage.save_project(p, root)
    assert storage.project_exists(root)
    loaded = storage.load_project(root)
    assert loaded.meta.id == p.meta.id
    assert loaded.premise.proposition == p.premise.proposition
    assert len(loaded.game_texts) == len(p.game_texts)
    assert len(loaded.rejected_archive) == len(p.rejected_archive)
    assert len(loaded.beats) == len(p.beats)
    assert loaded.audience.stance == p.audience.stance
