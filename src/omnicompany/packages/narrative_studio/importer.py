"""vilo → Project 导入器(v2,2026-06-27 彻查后重写)。

⚠ 真源纠正(上一轮全错):
- 当前有效设计在仓内 `wiki/` 顶层(00-故事大纲 / 01-时间系统 / 02-编辑规则 / 03-开局recipe),
  `seeds/` 是历史草稿(00-12 已被多世界线版取代)。
- 用户最终认可的只有"讨论/方向"四项:主旨 / 大纲 / 受众 / 文风讨论。
- 具体文字 / 叙事结构(开头流程)/ 具体文风 全是否决案,至今无认可版本 → 进 rejected_archive,绝不当真源。
- 世界设定:虚拟世界版(镜子宫殿/BCI/王杨奇点/碳基vs电子流/11结局/共通线)已被平行宇宙(Alters)取代。
- 游戏内 wiki(wiki/cards + wiki/events)= 游戏本身内容 → 落地层 game_texts(照常发挥作用)。

导入结果形态:
- 叙事指导层:premise(平行宇宙核心句,wiki/00)+ beats(四幕方向,wiki/00)+ audience(13§8)+ background(理念/待定)+ 文风讨论原则(note)。
- 设定:world(青沙+镜世界方向)+ characters(仅名字与方向,虚拟世界具体落地不进)。
- 落地层:game_texts = wiki/cards + wiki/events 全量(游戏内容)。
- 否决案归档:被取代/被否决的设计草稿(开局流程/文风规约/枫钟线/七日切片/旧世界观/11结局…)。
- 具体情节 / 结构图 / 具体文风矩阵 / 场景成文:留空(无认可版本)。
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import yaml

from . import models as m


# --------------------------------------------------------------------------- #
# 小工具
# --------------------------------------------------------------------------- #
def _prov(source: str, note: Optional[str] = None) -> m.Provenance:
    return m.Provenance(source=source, note=note)


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


def _split_frontmatter(text: str) -> Tuple[dict, str]:
    """拆 markdown 的 YAML frontmatter,返回 (meta, body)。"""
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end != -1:
            raw = text[3:end].strip()
            body = text[end + 4:]
            try:
                meta = yaml.safe_load(raw) or {}
            except yaml.YAMLError:
                meta = {}
            return (meta if isinstance(meta, dict) else {}), body
    return {}, text


def _sections(body: str) -> Dict[str, str]:
    """按 '## 标题' 切段,返回 {标题: 内容}。一级 '# ' 标题单列 _title。"""
    out: Dict[str, str] = {}
    cur = "_pre"
    buf: List[str] = []
    for line in body.splitlines():
        h2 = re.match(r"^##\s+(.+)$", line)
        h1 = re.match(r"^#\s+(.+)$", line)
        if h1:
            out["_title"] = h1.group(1).strip()
            continue
        if h2:
            out[cur] = "\n".join(buf).strip()
            cur = h2.group(1).strip()
            buf = []
            continue
        buf.append(line)
    out[cur] = "\n".join(buf).strip()
    return out


# --------------------------------------------------------------------------- #
# 落地层:解析 wiki/cards + wiki/events 为 game_texts(游戏内容)
# --------------------------------------------------------------------------- #
def _parse_game_text(path: Path, text_type: str) -> Optional[m.GameText]:
    text = _read(path)
    if not text.strip():
        return None
    meta, body = _split_frontmatter(text)
    secs = _sections(body)
    gid = str(meta.get("id") or path.stem)
    title = secs.get("_title") or path.stem

    # 文案(card)或正文(event)
    art_block = secs.get("卡图", "")
    art = None
    art_status = None
    mm = re.search(r"art:\s*`?([^`\n]+)`?", art_block)
    if mm:
        art = mm.group(1).strip()
    ms = re.search(r"status:\s*`?([^`\n]+)`?", art_block)
    if ms:
        art_status = ms.group(1).strip()

    # 选择(event)
    choices: List[m.GameTextChoice] = []
    sel = secs.get("选择", "")
    if sel:
        for blk in re.split(r"^###\s+", sel, flags=re.MULTILINE):
            blk = blk.strip()
            if not blk:
                continue
            label = blk.splitlines()[0].strip()
            cid = None
            cm = re.search(r"id:\s*`?([\w\-]+)`?", blk)
            if cm:
                cid = cm.group(1)
            choices.append(m.GameTextChoice(id=cid, label=label))

    body_text = secs.get("文案") or secs.get("正文") or ""
    annotations = secs.get("创作者批注") or None
    status = m.Status.todo
    if art_status and "unreviewed" not in art_status:
        status = m.Status.tocomplete

    return m.GameText(
        id=gid,
        text_type=text_type,
        title=title,
        category=str(meta.get("category")) if meta.get("category") else None,
        host=str(meta.get("host")) if meta.get("host") else None,
        body=body_text or None,
        choices=choices,
        art=art,
        art_status=art_status,
        annotations=annotations,
        status=status,
        provenance=_prov(f"wiki/{path.parent.name}/{path.name}", "游戏内容(游戏内 wiki 显示)"),
    )


def _import_game_texts(wiki: Path) -> List[m.GameText]:
    out: List[m.GameText] = []
    # 正式游戏内容
    for sub, ttype in (("cards", "card"), ("events", "event")):
        d = wiki / sub
        if not d.exists():
            continue
        for f in sorted(d.glob("*.md")):
            if "索引" in f.stem or f.stem.startswith("_"):
                continue
            gt = _parse_game_text(f, ttype)
            if gt:
                out.append(gt)
    # 草稿(wiki/drafts/cards|events) → is_draft=True
    for sub, ttype in (("cards", "card"), ("events", "event")):
        d = wiki / "drafts" / sub
        if not d.exists():
            continue
        for f in sorted(d.glob("*.md")):
            if "索引" in f.stem or f.stem.startswith("_"):
                continue
            gt = _parse_game_text(f, ttype)
            if gt:
                gt.is_draft = True
                out.append(gt)
    return out


def _import_comments(wiki: Path) -> List[m.Comment]:
    """读 wiki/comments/圈选评论收件箱(当前为空模板,仅占位;后续圈选反馈写这里)。"""
    # 当前收件箱是模板(待处理=TODO),无结构化条目;返回空,由 UI 收集新评论。
    return []


# --------------------------------------------------------------------------- #
# 叙事指导层(确认方向)
# --------------------------------------------------------------------------- #
def _build_premise() -> m.Premise:
    """立意(中心命题)= 用户口述的真源,故事无关,不可从大纲核心句自动推导。

    2026-07-02 洁净版:海筛全部留存会话后由用户原话逐句重建,唯一权威=
    vilo 仓 `wiki/10-立意-洁净版.md`(逐句溯源/排除清单/盲区声明)。
    本函数按该洁净版填 proposition,使重导入不再清空立意;
    修改立意必须先经作者确认并更新 wiki/10,再同步这里。
    禁止出现(用户否认或查无原话,见 wiki/10 第三节):想被看见/照亮/发光/变亮/
    碳基vs电子流/经验机器/镜子宫殿/渴望被理解/无条件的爱自己涌现。
    """
    return m.Premise(
        proposition=(
            "中心立意与具体故事无关,不是游戏或情节概述。五个母题(作者 2026-06-27 钉定):"
            "关于爱而不得;关于乌托邦的梦想;关于人是否要因为接近他人改变自己;"
            "关于完美现实与虚无主义;关于爱的本质。\n\n"
            "内核是\"爱而不得\"的谜题(作者 2026-06-06 原话):为何人世间总有缺憾?"
            "为何我爱的人会不爱我?浅层是对一夫一妻、一对一配偶观念的思考;"
            "深层是对永不完美的现世的\"痛恨\"——如果遗憾和不幸是注定,而幸福和美好又是相对的,"
            "那么人生来就是为了感受痛苦吗?这个游戏就是为了做给所有爱而不得的\"失败者\"的游戏。\n\n"
            "乌托邦的梦想(作者原话展开):Vilo 觉得\"如果世界上不能所有人都在爱这件事情上幸福,"
            "至少我要幸福\",她更深层的渴望是一个所有人都注定幸福的世界、所有人都陷入永恒的梦境——"
            "\"梦境即是真实\",她想构造这样的世界;但即使再看上去完美的乌托邦也无法解决唯一性问题,"
            "\"爱有所得\"的需求就是按需分配解决不了的乌托邦问题;一个连感情都完美的乌托邦会让你爱上"
            "\"正确的错误的人\",而操控爱本身这件事就让人本能地难以接受。\n\n"
            "人是否要因为接近他人改变自己(作者 2026-07-02 补话):很可能是剧情的第一层可见主旨;"
            "雷区=绝不能把女主写得看上去卑微(显得主动、计谋深邃、心机重重都可以);"
            "衡量基准=伴侣的幸福普世意义上应该是两人的互相成就。\n\n"
            "完美现实与虚无主义(作者 2026-07-02 确认+补话):就是一个责问——苦难比幸福更为确定,"
            "苦难可以百花齐放,但苦难的存在极为确定;Vilo 在物质与世俗成就上(成绩/家庭财富/衣食住行/外貌)"
            "感受不到太多苦难,她的苦难在较严重的关爱缺乏与心理创伤,较强的抑郁使其难以感受感官刺激。"
            "元问题是\"为什么某个人不能和我在一起\"的\"任性问题\":对绝对没办法的事情拒绝接受\"现实就是这样\","
            "萌生出\"我要找到一个他和我在一起的宇宙\"\"我想要一个所有人都得到真爱的世界……如果有真爱的话\""
            "——背后是对幸福的排他性的思考(此处也是与乌托邦、平行宇宙召唤设定的接缝)。\n\n"
            "爱的本质(作者 2026-07-02 定位):值得思考但游戏不必解答它;游戏里的 Vilo 追求比较传统的爱,"
            "畸形的爱(或对抗,或亏欠,或折磨和拥有)由 Alter 们承载。\n\n"
            "与标题挂钩的悲剧轴(作者 2026-06-29):是\"病态的理解\"(符合标题,Vilo想知道)"
            "还是\"病态的控制\",这是是否是悲剧的差异。"
        ),
        controlling_ideas=[],   # "由结局证成的理念";具体结局未认可,无高潮可证成 → 留空
        stance=None,            # 隐含立场(理论概念),vilo 未给定 → 留空
        locked=False,
        provenance=_prov(
            "wiki/10-立意-洁净版.md(2026-07-02,用户原话逐句重建)",
            "唯一权威=wiki/10;修改立意须经作者确认并先改 wiki/10 再同步此处",
        ),
    )


def _build_storylines() -> List[m.StoryLine]:
    """故事线 = 大纲散卡矩阵的"线"轴:共通 + 首批三线(首批人选属 AI 建议,作者未终拍)。"""
    return [
        m.StoryLine(id="sl-common", title="共通 / 全局", color="#8b94a3"),
        m.StoryLine(id="sl-liang", title="梁奕笙线", color="#6ea8fe", character_id="c-liangyisheng"),
        m.StoryLine(id="sl-qiu", title="邱明鹭线", color="#4ec98a", character_id="c-qiuminglu"),
        m.StoryLine(id="sl-mo", title="墨成线", color="#c08bff", character_id="c-mocheng"),
    ]


def _build_beats() -> List[m.Beat]:
    """初版大纲(2026-07-04):作者手稿"大纲草稿#2"七段骨架 + 6-28 开局方向 + 首批三线接口。

    历史:旧 wiki/00 四幕(含"想被认真看见")2026-06-28 被作者判"完全错误",此处曾长期留空;
    2026-07-04 作者亲笔大纲草稿#2 落定共通骨架,按正式记录结构(models.Beat 注释)录入。
    约定:顶层=段(edges 串主链);子 beat 按 lane 落进 段×线 的格;
    authority="author"=作者手稿/口述(局势句),"ai-draft"=AI 铺开待认(节奏意图/三线接口)。
    真源=vilo 仓 wiki/00 + wiki/drafts/草稿-初版大纲.md;重导入不回退、不回灌旧四幕。
    """
    ms = "作者手稿『大纲草稿#2』(2026-07-04,原文照录于 vilo 仓 wiki/drafts)+ wiki/00"
    dr = "wiki/drafts/草稿-初版大纲.md(AI 铺开,待作者逐段过)"

    def stage(id_: str, pos: int, title: str, sentence: str, function: str,
              note: str, edge: str | None = None) -> m.Beat:
        return m.Beat(
            id=id_, position=pos, title=title,
            summary=m.Summary(sentence=sentence), function=function,
            status=m.Status.tocomplete, authority="author",
            edges=[edge] if edge else [],
            provenance=_prov(ms, note),
        )

    def card(id_: str, parent: str, lane: str, pos: int, title: str, sentence: str,
             authority: str = "ai-draft", src: str = "", note: str = "") -> m.Beat:
        return m.Beat(
            id=id_, parent=parent, lane=lane, position=pos, title=title,
            summary=m.Summary(sentence=sentence),
            status=m.Status.todo if authority == "ai-draft" else m.Status.tocomplete,
            authority=authority,
            provenance=_prov(src or dr, note or ("三线接口=AI 拟,待作者认" if authority == "ai-draft" else "")),
        )

    u628 = "用户口述(2026-06-28)"

    return [
        # ── 七段主链(段=作者手稿;function 里的节奏意图=AI 铺开,句首标"(拟)") ──
        stage("b-s0-common", 0, "〇 · 共通线(简短)",
              "开局枯燥日常后 Alter 第一批出现,随后带来一些男主的轮廓;玩家加入=她的行为变得比较正常,基调较轻松,会吐槽平行宇宙类设定,真实说出贴近玩家视角的话",
              "(拟)日常压到最短;第一批 Alter 到来是第一个『世界打开』的时刻;设定靠她的吐槽消化,不靠讲解",
              "分段+主控效果=作者(7-04);节奏意图=(拟)", edge="b-s1-know"),
        stage("b-s1-know", 1, "一 · 单方面认识",
              "男主以身份模糊的身影经间接渠道渗入(Alter 传达/梦境/线上/道听途说/传闻);Alter 同时分享她们世界的男主见闻——男主轮廓和 Alter 们的失败爱情故事一起到来",
              "(拟)远观期:信息按碎片供给;她和玩家在『拼』一个人,不是在见一个人",
              "分段+间接渠道+失败爱情故事=作者;节奏意图=(拟)", edge="b-s2-meet"),
        stage("b-s2-meet", 2, "二 · 制造相识",
              "从『知道他是谁』到『他知道我存在』;『留下印象』是她第一次动用见不得光的手段",
              "(拟)游戏感最强的一段,轻松基调主场;病态感只以危机事件形式间歇刺出(类密教的痴迷/恐惧=作者)",
              "分段=作者;危机事件形态=作者手稿;节奏意图=(拟)", edge="b-s3-secrets"),
        stage("b-s3-secrets", 3, "三 · 进一步发现秘密",
              "每条线=表面身份+一个或一系列个人秘密;找到别人的秘密(或者说探查别人的个人信息本身)就是有乐趣的",
              "(拟)各线中段引擎:秘密由浅入深,浅层给爽感,深层改写她对这个人的认知、埋结局分岔的因",
              "分段=作者;线路结构原则=作者(7-02);节奏意图=(拟)", edge="b-s4-mainline"),
        stage("b-s4-mainline", 4, "四 · 进入男主主线",
              "各男主在时间线上自行经历一些事情、共同经历事件、或在事件背后操作;她已进入他的生活",
              "(拟)从『她单方面行动』切换为『两个人的事+世界的事』;她做的事开始与某个 Alter 当年的事重叠——或警示或诱导",
              "分段+共同事件=作者;节奏意图与 Alter 回响=(拟)", edge="b-s5-fate"),
        stage("b-s5-fate", 5, "五 · 决定命运的时刻 → 结局",
              "各男主都有决定自己命运的时刻(不一定是特定时刻),根据其对 Vilo 的印象和情绪(好感/信任/尊敬……)和该线自身情况,迎来结局",
              "(拟)四型结局在此挂接(释怀/真坏/阴影/悲剧,作者 6-29);病态的理解还是病态的控制决定是否悲剧;偏好『强扭出甜的瓜』的抗争型",
              "分段+命运时刻=作者;四型谱系挂接=(拟,谱系本身为作者 6-29)", edge="b-s6-epilogue"),
        stage("b-s6-epilogue", 6, "六 · 结局后的延展",
              "Vilo 身份很可能大幅转变——指与结局绑定的生活状态(与男主同居/成为战友/坏结局里每日望着某处发呆、再也见不到想见的人);可以继续玩,一段延展的游戏过程用于替代",
              "(拟)对应:释怀=同居/战友类日常;阴影=表面宁静背后巨大负担;悲剧=空洞的日常",
              "分段+身份转变澄清=作者(7-04 第八笔);型-状态对应=(拟)"),

        # ── 6-28 开局方向(作者口述,原三卡挂进对应段) ──
        card("b-open", "b-s0-common", "sl-common", 0, "开局 · 极简日常(要短)",
             "几个极简的日常动作(类比密教模拟器的「工作」),快速立起循环与她独居、黯淡的处境;开头日常保持短,不冗长",
             authority="author", src=u628, note="开局=极简日常且要短"),
        card("b-traces", "b-s1-know", "sl-common", 0, "模糊的身影 · 间接渗入",
             "几个身份模糊的男主,经 Alter 传达 / 梦境 / 线上 / 道听途说 / 传闻 等间接渠道陆续渗入她的世界,以淡印象为主;有些人本就很难直接遇到,正好靠这些间接渠道先到",
             authority="author", src=u628, note="间接渠道 + 淡印象 + 身份模糊"),
        card("b-find", "b-s1-know", "sl-common", 1, "辨认与寻找(=玩法)",
             "辨认这些模糊身影到底是谁、把他们找出来,本身就是剧情和玩法的一部分;每个男主的模糊度/难找度不同(=接触面难度);「在他身上留下印象」是她第一次动用见不得光的手段",
             authority="author", src=u628, note="找到男主本身=剧情/玩法"),

        # ── 首批三线接口(AI 拟,待作者认;首批人选本身也待终拍) ──
        card("b-i-liang-know", "b-s1-know", "sl-liang", 0, "梁:最易辨认,只有公开面",
             "校园里『人人都说好』的名字,几乎立刻可辨认;可见的只有完美会长的公开面"),
        card("b-i-qiu-know", "b-s1-know", "sl-qiu", 0, "邱:全场最难辨认",
             "只有一桩不留名的街头救人传闻;辨认本身=高难解谜"),
        card("b-i-mo-know", "b-s1-know", "sl-mo", 0, "墨:好找",
             "Alter 情报指到具体的人——一个普通程序员"),
        card("b-i-liang-meet", "b-s2-meet", "sl-liang", 0, "梁:接触面最难",
             "身边人多/自尊雷区/不信任无来由的好意;表演式接近的主场"),
        card("b-i-qiu-meet", "b-s2-meet", "sl-qiu", 0, "邱:偶遇即解谜",
             "卧底行程无规律,制造偶遇本身是解谜"),
        card("b-i-mo-meet", "b-s2-meet", "sl-mo", 0, "墨:难在无法图谋",
             "相识容易;凡带所图气味的接近即被读出并本能排斥——她从这里开始经营『看上去需求很奇怪』(攻略方向=作者 7-03)"),
        card("b-i-liang-secrets", "b-s3-secrets", "sl-liang", 0, "梁的秘密序列",
             "校外公寓学画 → 神秘朋友群 → 藏着的欲望 → 吴女士的过去(种子档素材,未逐条确权)"),
        card("b-i-qiu-secrets", "b-s3-secrets", "sl-qiu", 0, "邱的秘密序列",
             "表面身份 → 警察身份 → 罗福内幕(种子档素材,未逐条确权)"),
        card("b-i-mo-secrets", "b-s3-secrets", "sl-mo", 0, "墨的秘密序列",
             "权限不对 → 钱不对 → 江成英 → 公司是他的 → 终层:他一直读得出每个人想要什么,包括她"),
        card("b-i-liang-fate", "b-s5-fate", "sl-liang", 0, "梁:理解/控制分岔",
             "病态的理解=互相接住最不敢示人的部分(释怀);病态的控制=踩碎自尊(真坏/悲剧)"),
        card("b-i-qiu-fate", "b-s5-fate", "sl-qiu", 0, "邱:理解/控制分岔",
             "理解=信仰与情感在『能救一个是一个』上和解(释怀,代价惨重);控制=被他亲手逮捕/他因她卷入的危机身亡(悲剧)"),
        card("b-i-mo-fate", "b-s5-fate", "sl-mo", 0, "墨:理解/控制分岔",
             "理解=摊牌『你要的到底是什么』,她只能给出真的;控制=被读出『演』的那层,排斥引爆(残局)"),
    ]


def _build_audience() -> m.Audience:
    """受众与预期管理(13§1.2 + §8,确认方向)。"""
    src = "seeds/13-script-version-alters.md"
    return m.Audience(
        segments=[
            m.AudienceSegment(name="泛用户", note="尤其女性用户友好"),
            m.AudienceSegment(name="女性用户", note="能代入光鲜/强大/温柔/危险的多种女性自我,而非只代入阴暗失败者"),
        ],
        stance="不惩罚女性欲望、不羞辱'想被爱';承认欲望强烈/越界/不正当,但呈现'当欲望无法被正常承认时如何寻找不正当出口'",
        expectations=[
            "Alter 不是堕落版本,而是多种女性可能性(光鲜/强大/温柔/危险/空洞/失败/成功)",
            "男主不是奖品,而是能反照 Vilo 某种需求的人",
        ],
        resonance_targets=[
            "玩家喜欢 Alter 不因其道德正确,而因其有魅力/能力/伤口",
        ],
        provenance=_prov(src, "受众方向已认可;文本出处=seeds/13§8(新版草案,仅作来源),当前真源在 wiki/00"),
    )


def _build_background() -> m.Background:
    """背景/思考:设计理念 + 待定问题(吸收旧 wiki drafts/思考)。"""
    return m.Background(
        thinking=(
            "方向转变:从'虚拟世界(镜子宫殿/BCI/数字 Vilo 邀请解读者)'转为'平行宇宙——召唤平行世界的自己(Alters)'。"
            "数字化母题未删除,降格为幕后'数字精灵 Vilo'承载;新旧两套(BCI 真结局 vs 数字精灵)尚未合流。"
            "文风讨论(已认可原则):先语义后文风——文本与生成解耦,文风只在末端层出现(见 wiki/05、06)。"
        ),
        world_notes="舞台'青沙'与当代大学背景沿用;'纯写实无超自然'被推翻(引入平行宇宙召唤,高概念)。",
        open_questions=[
            "新旧两套世界机制(BCI 真结局 vs 数字精灵 Vilo)如何合流(seeds/16 自陈未统一)",
            "王杨从'系统奇点/唯一真爱'改为'连续性锚点/最早察觉者',结构定位待定",
            "具体开头流程/叙事结构尚无认可版本(wiki/03 自标尚未定稿)",
            "具体文风落地尚无认可版本(wiki/04-v1、style-bank 均否决)",
        ],
        provenance=_prov("seeds/13,15,16 + wiki/05,06", "理念/待定的综合,非成品"),
    )


def _build_world() -> List[m.WorldNode]:
    return [
        m.WorldNode(
            id="w-qingsha", name="青沙",
            description="当代大学背景的城市(沿用);'纯写实无超自然'已被平行宇宙召唤推翻(高概念)。",
            provenance=_prov("wiki/00-故事大纲.md", "舞台沿用"),
            children=[
                m.WorldNode(id="w-mirror", name="镜世界",
                            description="平行世界的自己(Alter Vilo)所在;读取主世界近期记忆,用'体验'换介入权。",
                            provenance=_prov("wiki/00-故事大纲.md", "第二幕:镜群介入")),
            ],
        ),
    ]


def _build_characters() -> List[m.Character]:
    """人设:仅导入'名字 + 平行宇宙方向'(虚拟世界具体落地=投影/奇点/真爱锁定 已被取代,不进真源)。"""
    chars: List[m.Character] = []
    chars.append(m.Character(
        id="c-vilo", name="Vilo", importance="main",
        # "想被(认真)看见/被选择"为 AI 引入主题,用户从未承认(6-28 点名/7-02 确认,见 wiki/09 台账),不得回灌
        summary=m.Summary(sentence="主世界 Vilo:想要爱——一段把她放在心上的关系;召唤平行世界的自己(Alter)介入生活"),
        custom_fields={
            "人物来源": "seeds/01(虚拟世界版具体落地已否决/被取代,仅作来源)",
            "苦难面·抑郁(2026-07-02)": "物质上和世俗成就上感受不到太多苦难(成绩,家庭财富,日常衣食住行,甚至外貌),"
                                       "但有较为严重的关爱缺乏和心理创伤,较强的抑郁使其难以感受感官刺激(用户口述,呼应立意④苦难责问)",
            "设定弱化原则(2026-07-02)": "后续弱化设定到“做出什么样的选择都大体合理”(类似部分日式RPG:有个性和丰富设定但选择依旧自由);"
                                       "抑郁特性从卡牌上体现、文字上弱化,解释为数字 Alter 出手——外部力量干涉使她在崩溃前保持理性和自由指挥身体"
                                       "(消除躯体化抑郁但还是会崩溃)",
            "主控效果定调(2026-07-04)": "玩家加入=让她的行为变得比较正常;基调可能比较轻松,会吐槽一些平行宇宙类似的设定,"
                                       "也会真实说出一些贴近玩家视角的话;失忆/记忆混乱不再是必需解释件(作者口述)",
        },
        status=m.Status.todo,
        provenance=_prov("wiki/00-故事大纲.md", "方向;具体人设落地未定"),
    ))
    chars.append(m.Character(
        id="c-wangyang", name="王杨", importance="main",
        summary=m.Summary(sentence="平行宇宙方向:从'系统奇点/唯一真爱'改为'连续性锚点/最早察觉 Vilo 不对劲的人',不预设爱谁"),
        custom_fields={"方向变更": "奇点→连续性锚点(seeds/13,14;wiki/00 不预设真爱)", "人物来源": "seeds/04"},
        status=m.Status.todo,
        provenance=_prov("seeds/13,14", "王杨新方向(具体落地未定)"),
    ))
    chars.append(m.Character(
        id="c-mocheng", name="墨成", importance="main",
        summary=m.Summary(sentence="隐形总裁:藏在自己公司里当普通打工人的创始人(作者 7-02 拆分定性,参考《疑犯追踪》)"),
        custom_fields={
            "人物来源": "seeds/07(虚拟世界版;作者 7-02 判定系两角色融合,墨成取'隐形总裁'半边,'社恐黑帮之子'拆出另存 vilo 仓 wiki/drafts)",
            "拆分确权(2026-07-02)": "普通出身,不是出生就是富二代;'从未去过游乐园/电影院/堂食/演唱会'清单不成立;"
                                    "'随机便利店打工+深度社恐+黑帮家族出身'归拆出角色;表面身份'App 后端开发副组长'与作者定性吻合,保留候选",
            "隐形动机·与成功同源(2026-07-02)": "他会成功以及他会隐藏身份的动机都是:他很容易看穿人内心真正想要什么(产品设计师的梦想技能);"
                                              "这也是 Vilo 的挑战(作者口述)",
            "天赋写法校准(2026-07-03)": "不是读心术超能力;就是会了解人的实际需求、可以想象出他人的视角,从而很擅长做产品(作者口述)",
            "天赋副作用·隐形动机(2026-07-03)": "天赋关不掉:本能对'对他有所图的人'很排斥,他眼里大部分人飘荡着或多或少的虚伪,"
                                               "对他来说社交并不令人享受(作者口述;'暴露会毁天赋'候选层因与此矛盾作废)",
            "Vilo线挑战(2026-07-03)": "Vilo 要努力成为'看上去需求很奇怪的人'(作者口述)",
        },
        status=m.Status.todo,
        provenance=_prov("seeds/07 + 作者口述(2026-07-02)", "隐形总裁方向已确权;出身与公司细节待共建"),
    ))
    # 其余男主:仅名字 + 来源指针(虚拟世界具体人设已被取代/未确认)
    others = [
        ("c-liangyisheng", "梁奕笙", "seeds/05"),
        ("c-qiuminglu", "邱明鹭", "seeds/06"),
        ("c-halanyin", "哈兰隐", "seeds/08"),
        ("c-fengzhong", "枫钟", "seeds/09"),
        ("c-kujie", "库杰", "seeds/10"),
    ]
    for cid, name, src in others:
        chars.append(m.Character(
            id=cid, name=name, importance="main",
            custom_fields={"人物来源": f"{src}(虚拟世界版具体人设;方向待定,具体落地未认可)"},
            status=m.Status.todo,
            provenance=_prov(src, "人物来源;具体人设落地未确认"),
        ))
    # Alter:平行宇宙的自己(受众已认可方向:多种女性可能性);具体清单仍属草案 → todo
    chars.append(m.Character(
        id="c-alters", name="Alter Vilo(镜群)", importance="group",
        summary=m.Summary(sentence="召唤来的平行世界的自己:多种女性可能性(光鲜/强大/温柔/危险/空洞/失败/成功),各代表一种不正当方法"),
        custom_fields={
            "方向": "受众已认可;具体 Alter 清单(演员/工程师/幸福/黑帮/投资人/市长/犯罪心理学家 等)属草案 seeds/13,未逐一认可",
            "畸形爱四型(2026-07-02)": "Vilo 本体追求比较传统的爱;alter 们一般来说会得到另一种畸形的爱——或是对抗,或是亏欠,或是折磨和拥有(用户口述,见立意⑤)",
            "数字Vilo(2026-07-04)": "Alter 之一:造成这一切的近乎全知者;偶尔不称'Vilo'而以'外来者'称呼主控视角,"
                                     "会说'就算是为了原本的这个灵魂,试着去爱如何?'(作者手稿+口述澄清:非旧虚拟世界版设定重启)",
            "内容面·失败爱情故事(2026-07-04)": "主线之外要准备 Alter 们的失败爱情故事:可能甚至有一些相爱,但肯定没有最终在一起——"
                                               "得不到对方真正的信任/无法实现对方的要求/无法认可对方的某种作为(但依然觉得很有爱意)(作者手稿)",
        },
        status=m.Status.todo,
        provenance=_prov("seeds/13 + wiki/00", "Alter 方向(已认可);具体清单未定"),
    ))
    return chars


def _build_writing_principle_note() -> m.Note:
    return m.Note(
        id="note-writing-principle",
        text=("文风讨论(已认可原则,非具体落地):先语义后文风——文本与生成解耦,文风只在末端层渲染;"
              "6 维质量轴(D1-D6)作门禁/体检表(见 wiki/05、06)。"
              "⚠ 任何具体文风规约/风格矩阵/试样目前均无认可版本(wiki/04-v1、style-bank 已否决),见否决案归档。"),
    )


# --------------------------------------------------------------------------- #
# 否决案归档(被取代/被否决,记录但不作真源)
# --------------------------------------------------------------------------- #
def _build_rejected_archive() -> List[m.RejectedItem]:
    return [
        # —— 被取代(虚拟世界版)——
        m.RejectedItem(id="r-virtual-world", area="世界设定", verdict="superseded",
                       title="虚拟世界版(镜子宫殿/BCI/数字 Vilo 邀请解读者/打破第四面墙)",
                       reason="已被平行宇宙(Alters)版取代;数字化降格为幕后'数字精灵'", source="seeds/00-core-concept.md"),
        m.RejectedItem(id="r-wangyang-singularity", area="世界设定", verdict="superseded",
                       title="王杨=系统涌现奇点/无条件爱她/真结局唯一真爱",
                       reason="新版改为'连续性锚点/最早察觉者',不预设真爱", source="seeds/00,03"),
        m.RejectedItem(id="r-male-projection", area="主旨", verdict="superseded",
                       title="所有男主都是 Vilo 内心矛盾的投影 / 碳基vs电子流之爱",
                       reason="平行宇宙版不再采用该核心命题", source="seeds/00-core-concept.md"),
        m.RejectedItem(id="r-11-endings", area="结局体系", verdict="superseded",
                       title="11 个具名结局(深海/骄阳/溪水/残花/黄灯/绿灯/失踪/红灯/真/真正义)按数值组合触发",
                       reason="新版改为七日切片的方向性结算,不做完整结局系统(seeds/16,19)", source="seeds/03-narrative-structure.md"),
        m.RejectedItem(id="r-common-route", area="叙事结构", verdict="superseded",
                       title="共通线=王杨教程(尾随/偶遇/安装监控)→解锁6条男主线→真结局/真正义结局(25年冤案)",
                       reason="旧版叙事结构,随虚拟世界版一并被取代", source="seeds/03-narrative-structure.md"),
        m.RejectedItem(id="r-realistic-noSupernatural", area="世界设定", verdict="superseded",
                       title="纯写实当代中国/无超自然/BCI 仅作合理化手段",
                       reason="转向高魔/多世界召唤(青沙城市保留)", source="seeds/00-project-overview.md"),
        # —— 被否决(具体落地,至今无认可版本)——
        m.RejectedItem(id="r-opening-flow", area="叙事结构", verdict="rejected",
                       title="开头流程/开局第一小时多版起稿(又一天→灰白→镜窗→Alter交易;第一条开局链/12 recipe)",
                       reason="wiki/03 自标'尚未定稿/起稿大纲/待确认';用户点名'开头的流程'已否决", source="wiki/03-密教式开局与recipe骨架.md"),
        m.RejectedItem(id="r-fengzhong-route", area="情节", verdict="rejected",
                       title="枫钟路线 10 节点骨架(声音进来→找到→走出来;骄阳/残花分支)+ 全部 Hook",
                       reason="seeds/12 自标'草稿,待作者审阅';属被否决的具体情节", source="seeds/12-route-skeleton-fengzhong.md"),
        m.RejectedItem(id="r-7day-slice", area="情节", verdict="rejected",
                       title="7天/7+3天垂直切片玩法-剧情编排(Day1..Day10 节拍/Alter请求/风险系统)",
                       reason="施工/排期草案,非认可定稿;wiki/03 自评旧 demo'更像剧情卡片演示器'", source="seeds/14,16,19"),
        m.RejectedItem(id="r-style-spec", area="具体文风", verdict="rejected",
                       title="wiki/04-文风规约与试样-v1(五层文本格式/硬禁句式/AI味词表/试样A-E)",
                       reason="具体文风落地已否决;试样含被取代的镜窗/Alter 设定", source="wiki/04-文风规约与试样-v1.md"),
        m.RejectedItem(id="r-style-bank", area="具体文风", verdict="rejected",
                       title="vilo-style-example-bank(卡牌短文/方法卡/多声部场景 全套具体文本样例)",
                       reason="具体文字+具体文风双否决;通篇基于被取代世界观", source="references/text-style/vilo-style-example-bank.md"),
        m.RejectedItem(id="r-premise-v1-synthesis", area="主旨", verdict="rejected",
                       title="立意 v1(综括版,2026-06-27~28)",
                       reason="含用户未承认主题:『互相照亮的极端』、母题入口『因另一个人继续变亮』"
                              "(6-28 用户点名立不住并标待重建;7-02 用户确认'照亮/发光'从未承认,见 wiki/09 台账)。"
                              "立意待与用户重建,importer 不自动填。",
                       source="6-27/28 综括工作流(seeds/00·13·14·15·18 + pipeline v5 + wiki/00·03 + dossiers)",
                       excerpt="她太怕得不到爱,于是把爱当成可以用信息和手段堆出来的工程……命题落到几处互相照亮的极端"
                               "(全文见项目 .history/20260702T032053727170/premise.json)"),
    ]


# --------------------------------------------------------------------------- #
# 总装
# --------------------------------------------------------------------------- #
def import_vilo(repo_root: str | Path) -> m.Project:
    """从 vilo 仓库根导入。repo_root = 故事/vilo-wants-to-know。

    只装用户认可的方向 + 游戏内容(cards/events);被取代/被否决进归档;具体落地留空。
    缺文件不报错。
    """
    repo = Path(repo_root)
    wiki = repo / "wiki"

    project = m.Project(
        meta=m.ProjectMeta(
            id="vilo", name="Vilo想知道", version="v2-parallel-universe",
            description="平行宇宙(召唤平行世界的自己 Alters)版;当前有效设计入口=仓内 wiki/00-04。"
                        "只装已认可方向 + 游戏内容,具体情节/结构/文风未认可故留空。",
        ),
    )

    # 叙事指导层(确认方向)
    project.premise = _build_premise()
    project.beats = _build_beats()
    project.storylines = _build_storylines()
    project.audience = _build_audience()
    project.background = _build_background()

    # 设定(方向)
    project.world = _build_world()
    project.characters = _build_characters()

    # 落地层:游戏内容(cards/events)
    project.game_texts = _import_game_texts(wiki)

    # 否决案归档
    project.rejected_archive = _build_rejected_archive()

    # 圈选评论收件箱(创作者反馈)
    project.comments = _import_comments(wiki)

    # 文风讨论原则(note);具体文风矩阵/voices/registers 留空(无认可版本)
    project.notes = [_build_writing_principle_note()]

    # 具体情节/结构图/场景成文/数值/压力:无认可版本 → 留空(scenes/nodes/connections/endings/
    # prose_lines/style_matrix/voices/registers/variables/pressures/failure_levels 全默认空)
    return project
