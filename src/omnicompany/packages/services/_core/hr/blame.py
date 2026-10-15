# [OMNI] origin=claude-code ts=2026-08-17T00:00:00+08:00 type=module
# [OMNI] summary="LLM 人事系统四档归因：direct/vcs/window/unknown，外加反向查询与客观计数聚合"
# [OMNI] why="P1-implementation-contract.md E 棒职责——追责系统冤枉一个模型后果严重，
#             必须让调用方拿到显式档位，绝不能把 window 档线索伪装成结论"
# [OMNI] tags=hr,blame,attribution,confidence-tier
"""LLM 人事系统 —— 四档归因层（纯确定性，不含任何 LLM 调用）。

权威依据: plan.md §2.3(含 §1.5 P0 修正)与 P1-implementation-contract.md。

四档置信度(严格按 plan.md §2.3，见 `blame()`):

    direct  —— transcript 中某 actor 的写事件命中该路径且 verdict=applied，可用于追责。
    vcs     —— git 提交命中该路径，可用但仅到文件级；**绝不反推到具体会话**
                (本仓一次 commit 可横扫数千文件、滞后超一天，同窗可能有几十个无关会话)。
    window  —— 仅会话活跃期与文件 mtime 交叠，只做线索，绝不得作为结论。
    unknown —— 无任何证据，如实报告不知道，宁可 unknown 也不编造。

`commit_index` 依赖注入形状(D 棒 vcs_source 尚未完成，本模块不 import 它):
    要么是可调用对象 `commit_index(normalized_path: str) -> list[dict]`，
    要么是字典 `{normalized_path: [dict, ...]}`。
    每条 dict 至少含:
        "commit": str          —— 提交哈希
        "day": str             —— ISO 日期 "YYYY-MM-DD"(P0 实测：本仓是攒批提交，
                                    时间窗必须按天设计，按小时会大量失配)
    可选:
        "files_changed": int   —— 该提交总共改了多少文件(用于让调用方自行判断
                                    "是不是那种横扫几千文件的攒批提交"，本模块不替
                                    调用方做这个判断，只如实透传)。
    传入的 path 参数与 dict key / callable 入参统一用 `_normalize()` 归一化后的字符串
    (`os.path.normcase(os.path.normpath(...))`)，避免 Windows 大小写/斜杠差异导致漏判。
"""

from __future__ import annotations

import os
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Iterable, Union

from .schema import HrEvent

# mtime 与会话活跃期比对时的容差：文件系统 mtime 落盘/同步与 transcript 记录的事件
# 时间戳之间存在几分钟量级的偏差是正常的(缓冲刷新、时钟粒度)，过窄会漏判、过宽会
# 把明显无关的会话也拉进 window 候选——5 分钟是保守折中，且 window 档本就只做线索
# 不做结论，宁可多列几个候选也不要漏掉真正相关的那个。
_WINDOW_PAD = timedelta(minutes=5)

CommitIndexFn = Callable[[str], list[dict[str, Any]]]
CommitIndex = Union[CommitIndexFn, dict, None]

TIERS: tuple[str, ...] = ("direct", "vcs", "window", "unknown")

AS_OF = "2026-08-17"
"""`DENIAL_SIGNAL_COMPLETENESS` 是一张**手工快照**，不是自动核算(team-lead 2026-08-17
明确裁决：从 events.jsonl 里数"某 provider 的 denial 数 > 0"来自动推导 verified 是
错误设计——0 条可能是"检测分支没被验证"，也可能是"这段时间真的没被拒绝过、说明它
守规矩"，自动核算分不出这两种，还会在第一条 denial 出现的瞬间把 provider 误判成
verified，而一个正例只证明 parser 能抓到某一种拒绝形态，不证明能抓全。所以verdict
永远归人判断，这张表只能手改。

`AS_OF` 记录这张快照对应哪次冒烟结果——本值来自 team-lead 2026-08-17 P2 追加约束
消息引用的 B 棒真实冒烟(claude 370 条/qoder 62 条/codebuddy·kiro 0 条真实 denial)。
表被人工更新时，`AS_OF` 必须跟着改，否则 `check_completeness_table_staleness()`
无法告诉调用方"这条提示对应的是哪次快照"。
"""

DENIAL_SIGNAL_COMPLETENESS: dict[str, str] = {
    # 值域:
    #   "verified"   —— 该 provider 的 denial(拒绝)通道已被真实语料的正例验证过
    #   "unverified" —— 拒绝检测已实现，但真实语料里 0 命中，只在合成 fixture 里验过
    #   "pending"    —— 采集器尚未交付，完备度未知
    #
    # 来源: team-lead 2026-08-17 P2 追加约束消息，引用 B 棒真实冒烟数字——
    # claude 370 条真实 denial、qoder 62 条真实 denial；codebuddy/kiro 的 denial 分支
    # 虽已实现，真实语料 0 命中(B 棒如实报告：防御性实现，未被真实正例验证)；
    # codex 待 C 棒交付后确定。
    # 这是"我们对这个 provider 观测得怎么样"的元信息，不是"这个 actor 做得怎么样"——
    # 两者一旦混进同一个数字，跨 provider 排名就会把"测量能力差异"读成"模型表现差异"。
    # 集成阶段如需按新的冒烟结果更新，由主会话改这里，本模块不自行推断。
    "claude": "verified",
    "qoder": "verified",
    "codebuddy": "unverified",
    "kiro": "unverified",
    "codex": "pending",
}


def _signal_completeness_for(provider: str) -> str:
    return DENIAL_SIGNAL_COMPLETENESS.get(provider, "unknown")


def check_completeness_table_staleness(events: Iterable[HrEvent]) -> list[str]:
    """扫真实事件，找出"`DENIAL_SIGNAL_COMPLETENESS` 这张表可能该复核了"的信号。

    **只发现，绝不判断、绝不改表**——这是 team-lead 裁决"verdict 归人判断"之后
    追加的自动化部分：机器负责发现表过期，人负责判断通道是否可信。

    逻辑: 对标为 `"unverified"` 或 `"pending"` 的 provider，如果这批真实事件里
    出现了它的 denial(`kind=="denial"` 或 `verdict=="denied"`)，说明拒绝通道
    至少抓到了一个真实正例——这跟表里"从未验证/尚不确定"的假设不一致，值得人工
    复核。**但一个正例不足以自动升级为 verified**(见 `AS_OF` 的 docstring)，
    本函数只把"该复核了"这件事翻出来，不做那个升级判断，也不修改
    `DENIAL_SIGNAL_COMPLETENESS` 本身(纯函数，无副作用)。

    Returns:
        提示字符串列表，每条对应一个"标注与实测不一致"的 provider；一致时(即
        `unverified`/`pending` 的 provider 在这批事件里确实 0 命中，或所有
        provider 都已是 `verified`)返回空列表。
    """
    events = list(events)
    denial_counts: dict[str, int] = {}
    for ev in events:
        if ev.kind == "denial" or ev.verdict == "denied":
            denial_counts[ev.provider] = denial_counts.get(ev.provider, 0) + 1

    warnings: list[str] = []
    for provider, count in sorted(denial_counts.items()):
        completeness = DENIAL_SIGNAL_COMPLETENESS.get(provider)
        if completeness in ("unverified", "pending") and count > 0:
            warnings.append(
                f"{provider} 现有 {count} 条真实 denial，完备度表(AS_OF={AS_OF})仍标 "
                f"'{completeness}'，请人工复核后更新表与 AS_OF。"
            )
    return warnings


CROSS_PROVIDER_WARNING = (
    "跨 provider 缺陷率不可比：各 provider 的拒绝(denial)信号采集完备度不同"
    "(claude/qoder 已被真实语料验证有真实命中；codebuddy/kiro 目前真实语料 0 命中、"
    "只在合成 fixture 里验过；codex 待交付后确定)。采集越不完备的 provider，缺陷率"
    "会被系统性低估——这是测量能力的差异，不是模型表现的差异，用来横向排名等于用"
    "'我们查得不够仔细' 去洗白模型。默认视图按 provider 分组，就是为了不让这种差异"
    "被误读成排名；确实需要跨 provider 看时，必须带着这条警告一起看。"
)

DEFAULT_MINIMUM_SAMPLES = 3
"""客观缺陷率的最小样本量门槛。与 `config/agent_control/catalog.json` 的
`policy.minimum_review_samples`(默认 3)取值保持一致，但不去 import 那份 catalog——
那是 agent 互评设施自己的配置面，本模块只是"参照同一个数字"，不建立跨棒依赖。
样本不足时 `compute_defect_rates()` 不产出比例，只给原始计数并标
`insufficient_samples=True`：那套互评设施就是因为长期卡在 2 条样本、又没有硬门槛去
提醒"这还不够"，导致回流通路空转了半年(见 plan.md §1.4 缺口 C)。这里宁可让调用方
看见"数据不够、暂时没有结论"，也不为了"有个数字看"而把门槛松掉。
"""


@dataclass
class BlameResult:
    """`blame()` 的返回值——四档归因必须显式带档位，调用方不可能拿到一个"看起来是
    结论"但其实是 window 档的东西(见模块 docstring 三条红线)。
    """

    path: str
    tier: str                      # TIERS 之一，必填
    usable_for_attribution: bool = False   # direct/vcs=True(vcs 仅到文件级); window/unknown=False

    actor_id: str = ""             # "provider:model:session_id"，仅 direct 档填充
    provider: str = ""
    model: str = ""
    session_id: str = ""           # 红线: 仅 direct 档可定位到具体会话；vcs/window/unknown 一律 ""
    ts: str = ""                   # direct 档：命中事件里最新一条的时间戳

    evidence_refs: list[str] = field(default_factory=list)     # direct 档：命中事件的 evidence_ref
    commits: list[dict[str, Any]] = field(default_factory=list)  # vcs 档：命中的提交(文件级)
    candidates: list[dict[str, Any]] = field(default_factory=list)  # window 档：会话时间窗线索，仅供参考

    note: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _normalize(path: str) -> str:
    """统一斜杠/大小写，并把 CLI 相对路径锚定到当前工作目录。"""
    value = os.path.abspath(str(path)) if not os.path.isabs(str(path)) else str(path)
    return os.path.normcase(os.path.normpath(value))


def _parse_ts(ts: str) -> datetime | None:
    if not ts:
        return None
    try:
        s = ts.replace("Z", "+00:00")
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except ValueError:
        return None


def _lookup_commits(commit_index: CommitIndex, norm_path: str) -> list[dict[str, Any]]:
    if commit_index is None:
        return []
    if callable(commit_index):
        result = commit_index(norm_path)
        return list(result) if result else []
    if isinstance(commit_index, dict):
        return list(commit_index.get(norm_path) or [])
    raise TypeError(f"commit_index 须为可调用对象或 dict，收到 {type(commit_index)!r}")


def _session_windows(events: list[HrEvent]) -> dict[str, tuple[datetime, datetime, HrEvent]]:
    """按 session_id 求活跃期 [min(ts), max(ts)]，附一条代表性事件(取 provider/model)。
    只统计能解析出时间戳的事件；session_id 为空的事件不参与(无法构成"某会话的窗口")。
    """
    acc: dict[str, list[Any]] = {}
    for ev in events:
        if not ev.session_id:
            continue
        dt = _parse_ts(ev.ts)
        if dt is None:
            continue
        if ev.session_id not in acc:
            acc[ev.session_id] = [dt, dt, ev]
        else:
            lo, hi, rep = acc[ev.session_id]
            if dt < lo:
                lo = dt
            if dt > hi:
                hi = dt
            acc[ev.session_id] = [lo, hi, rep]
    return {sid: (lo, hi, rep) for sid, (lo, hi, rep) in acc.items()}


def blame(
    path: str,
    *,
    events: Iterable[HrEvent],
    commit_index: CommitIndex = None,
    file_mtime: float | None = None,
) -> BlameResult:
    """回答"这个文件什么时候被哪个 actor 改的"，四档按 direct > vcs > window > unknown
    的优先级依次尝试，命中即返回，不越级、不降档凑结论。

    Args:
        path: 待查询的文件路径(不要求真实存在于当前机器——`window` 档以外的判定都
              不依赖文件是否存在)。
        events: 已落盘的 HrEvent 全集(或调用方已过滤好的子集)，通常来自
                `store.read_events(...)`。
        commit_index: 见模块 docstring——可调用对象或 dict，D 棒 vcs_source 落地前
                传 None(vcs 档天然跳过，直接降到 window/unknown，不编造)。
        file_mtime: 可选覆盖文件 mtime(unix epoch 秒)，用于测试注入而不依赖真实
                文件系统；省略时(且需要用到 mtime 时)才调用 `os.path.getmtime`，
                取不到(文件不存在/被删)则 window 档自然判不出候选，降到 unknown。
    """
    events = list(events)
    norm_path = _normalize(path)

    # ── direct: 写事件命中该路径且 verdict=applied ──────────────────────
    direct_hits = [
        ev for ev in events
        if ev.kind == "write"
        and ev.verdict == "applied"
        and ev.target_confidence in ("direct", "parsed")
        and ev.target
        and _normalize(ev.target) == norm_path
    ]
    if direct_hits:
        best = max(direct_hits, key=lambda e: e.ts)
        return BlameResult(
            path=path,
            tier="direct",
            usable_for_attribution=True,
            actor_id=f"{best.provider}:{best.model or 'unknown'}:{best.session_id}",
            provider=best.provider,
            model=best.model,
            session_id=best.session_id,
            ts=best.ts,
            evidence_refs=[h.evidence_ref for h in direct_hits],
            note=f"命中 {len(direct_hits)} 条写入生效(verdict=applied)事件，取时间最新一条为代表 actor。",
        )

    # ── vcs: git 交叉命中，仅文件级，红线：不得反推到具体会话 ──────────────
    commits = _lookup_commits(commit_index, norm_path)
    if commits:
        return BlameResult(
            path=path,
            tier="vcs",
            usable_for_attribution=True,   # 可用，但仅到文件级
            session_id="",                  # 红线: vcs 档不得反推到具体会话
            commits=commits,
            note=(
                "git 提交命中该文件，但仅能定位到文件级，不可定位到具体会话/actor —— "
                "本仓 git 是攒批 checkpoint 提交(P0 实测一次 commit 横扫 7604 个文件、"
                "滞后约 1.5 天)，同一时间窗内可能有几十个无关会话，不得当作接近 "
                "direct 档的强证据使用。"
            ),
        )

    # ── window: 仅会话活跃期与文件 mtime 交叠，只做线索 ────────────────────
    mtime = file_mtime
    if mtime is None:
        try:
            mtime = os.path.getmtime(norm_path)
        except OSError:
            mtime = None

    candidates: list[dict[str, Any]] = []
    if mtime is not None:
        mtime_dt = datetime.fromtimestamp(mtime, tz=timezone.utc)
        for sid, (lo, hi, rep) in _session_windows(events).items():
            if (lo - _WINDOW_PAD) <= mtime_dt <= (hi + _WINDOW_PAD):
                candidates.append({
                    "actor_id": f"{rep.provider}:{rep.model or 'unknown'}:{sid}",
                    "provider": rep.provider,
                    "session_id": sid,
                    "session_window": [lo.isoformat(), hi.isoformat()],
                    "evidence_ref": rep.evidence_ref,
                })
        candidates.sort(key=lambda c: c["session_window"][0])

    if candidates:
        return BlameResult(
            path=path,
            tier="window",
            usable_for_attribution=False,
            candidates=candidates,
            note=(
                f"仅会话活跃期与文件 mtime(容差 ±{int(_WINDOW_PAD.total_seconds())}s)交叠，"
                "这是线索不是结论，绝不能当作追责依据；同窗可能有多个无关候选。"
            ),
        )

    # ── unknown: 无任何证据 ────────────────────────────────────────────
    return BlameResult(
        path=path,
        tier="unknown",
        usable_for_attribution=False,
        note="无 direct / vcs / window 证据，如实报告不知道，不编造归属。",
    )


def sessions_touching(actor_id: str, *, events: Iterable[HrEvent]) -> list[str]:
    """反向查询：给定 `actor_id`("provider:model:session_id")，列出该 actor 写入
    生效(kind=write 且 verdict=applied)过的全部路径，排序去重返回。"""
    parts = actor_id.split(":", 2)
    if len(parts) != 3:
        raise ValueError(f"actor_id 须形如 'provider:model:session_id'，收到 {actor_id!r}")
    provider, model, session_id = parts

    targets = {
        ev.target
        for ev in events
        if ev.kind == "write"
        and ev.verdict == "applied"
        and ev.target_confidence in ("direct", "parsed")
        and ev.target
        and ev.provider == provider
        and (ev.model or "unknown") == model
        and ev.session_id == session_id
    }
    return sorted(targets)


def compute_actor_stats(events: Iterable[HrEvent]) -> dict[str, dict[str, int]]:
    """按 actor_id 聚合客观计数：写入生效数 / 被拒数 / stray 数 / 报错数。

    纯计数，不做任何 LLM 评价(plan.md §2.4 第 3 条："客观缺陷率零 LLM")。
    `session_id` 为空的事件(如 fs 巡检发现的 stray，尚无法归到具体会话)归入
    `"<provider>:unknown:unknown"`，不编造归属，也不因此丢弃计数。
    """
    out: dict[str, dict[str, int]] = {}
    for ev in events:
        provider = ev.provider or "unknown"
        model = ev.model or "unknown"
        session_id = ev.session_id or "unknown"
        actor_id = f"{provider}:{model}:{session_id}"
        row = out.setdefault(actor_id, {"writes": 0, "denied": 0, "stray": 0, "errors": 0})
        if ev.kind == "write" and ev.verdict == "applied":
            row["writes"] += 1
        if ev.kind == "denial" or ev.verdict == "denied":
            row["denied"] += 1
        if ev.kind == "stray":
            row["stray"] += 1
        if ev.verdict == "error":
            row["errors"] += 1
    return out


def _rework_counts(events: list[HrEvent]) -> dict[str, int]:
    """按目标文件求返工计数(P2-2 新增信号)。

    定义: 对某个 target 路径，按 `ts` 排好它全部"写入生效"(`kind=="write"` 且
    `verdict=="applied"`)事件的时间序；每当接手写这个文件的 actor 与"上一次写
    这个文件"的 actor 不同，就给**这次接手的 actor**记一次返工。同一 actor 连续
    两次写同一文件(继续自己此前的工作)不计——那不是"返工"，是同一人分两段会话
    干同一件事。

    这个计数天然只会命中 `compute_actor_stats()` 里已经存在 `writes` 计数的
    actor(两者统计的是同一批 write-applied 事件)，调用方不需要再额外 setdefault。
    """
    by_target: dict[str, list[HrEvent]] = {}
    for ev in events:
        if ev.kind == "write" and ev.verdict == "applied" and ev.target:
            by_target.setdefault(ev.target, []).append(ev)

    rework: dict[str, int] = {}
    for target_events in by_target.values():
        ordered = sorted(target_events, key=lambda e: e.ts)
        prev_actor: str | None = None
        for ev in ordered:
            actor_id = f"{ev.provider}:{ev.model or 'unknown'}:{ev.session_id}"
            if prev_actor is not None and actor_id != prev_actor:
                rework[actor_id] = rework.get(actor_id, 0) + 1
            prev_actor = actor_id
    return rework


def compute_defect_rates(
    events: Iterable[HrEvent], *, minimum_samples: int = DEFAULT_MINIMUM_SAMPLES
) -> dict[str, dict[str, Any]]:
    """在 `compute_actor_stats()` 的原始计数之上，产出**可拆解**的客观缺陷率。

    公式(分子分母都显式可见，禁止给一个没法拆解的复合分数):
        defect_rate = denied / (writes + denied)
    即"被拒次数占全部尝试写入次数(含被拒)的比例"。`stray`/`errors`/`rework` 单独
    作为计数项透出，不并进这个比例的分母——它们衡量的是不同性质的问题(游离产物/
    工具报错/返工)，混进同一个分母会把"缺陷率"变成一个说不清楚在算什么的复合数。

    样本量门槛: `sample_size = writes + denied` 小于 `minimum_samples` 时**不产出
    `defect_rate`**(置 `None`)，只给原始计数并标 `insufficient_samples=True`——
    分子分母仍然透出(`defect_rate_numerator`/`defect_rate_denominator`)，供调用方
    自行判断，但不by本函数计算出一个比例来。

    每条记录都带 `provider`(从 actor_id 拆出)与 `signal_completeness`
    (`DENIAL_SIGNAL_COMPLETENESS` 表)——这是"我们对这个 provider 观测得怎么样"的
    元信息，刻意放在每一行里，逼着任何下游消费者(CLI/agent/以后的 digest)都绕不开
    "这行数字的可信度取决于采集完备度"这件事。**本函数本身仍是扁平的跨 actor
    结构**——如果要做"默认不跨 provider 排名"这条约束，请用
    `group_defect_rates_by_provider()` 或 `compute_defect_rates_cross_provider()`，
    不要直接把这个扁平结果当成一张可以横向比较的表来用。

    纯确定性、零 LLM(plan.md §2.4 第 3 条)。
    """
    events = list(events)
    stats = compute_actor_stats(events)
    rework = _rework_counts(events)

    out: dict[str, dict[str, Any]] = {}
    for actor_id, row in stats.items():
        provider = actor_id.split(":", 1)[0]
        writes = row["writes"]
        denied = row["denied"]
        denom = writes + denied
        insufficient = denom < minimum_samples
        out[actor_id] = {
            "provider": provider,
            "signal_completeness": _signal_completeness_for(provider),
            "writes": writes,
            "denied": denied,
            "stray": row["stray"],
            "errors": row["errors"],
            "rework": rework.get(actor_id, 0),
            "sample_size": denom,
            "minimum_samples": minimum_samples,
            "insufficient_samples": insufficient,
            "defect_rate": None if insufficient else round(denied / denom, 4),
            "defect_rate_numerator": denied,
            "defect_rate_denominator": denom,
        }
    return out


def group_defect_rates_by_provider(
    rates: dict[str, dict[str, Any]]
) -> dict[str, dict[str, dict[str, Any]]]:
    """把 `compute_defect_rates()` 的扁平结果按 provider 重新分组——**这是默认应该
    展示给人看的形状**：约束(2)要求"缺陷率默认只在同一 provider 内比较"，分组本身
    就是这条约束的实现，不需要在分组之外另加一次比较逻辑。

    分组键直接取 `actor_id.split(":", 1)[0]`(actor_id 形如
    "provider:model:session_id")，与生成 `rates` 时的分组口径完全一致，不重新扫
    一遍 events。
    """
    grouped: dict[str, dict[str, dict[str, Any]]] = {}
    for actor_id, row in rates.items():
        grouped.setdefault(row.get("provider") or actor_id.split(":", 1)[0], {})[actor_id] = row
    return grouped


def compute_defect_rates_cross_provider(
    events: Iterable[HrEvent], *, minimum_samples: int = DEFAULT_MINIMUM_SAMPLES
) -> dict[str, Any]:
    """调用方**显式**要求跨 provider 视图时用这个，而不是直接把
    `compute_defect_rates()` 的扁平结果当成一张可比较的表。

    返回值强制带 `warning` 字段(`CROSS_PROVIDER_WARNING`)——不允许在没有警告的
    情况下产出一张看起来可以直接横向排名的表(team-lead 2026-08-17 追加约束)。
    """
    return {
        "warning": CROSS_PROVIDER_WARNING,
        "by_actor": compute_defect_rates(events, minimum_samples=minimum_samples),
    }


__all__ = [
    "TIERS",
    "DEFAULT_MINIMUM_SAMPLES",
    "DENIAL_SIGNAL_COMPLETENESS",
    "AS_OF",
    "CROSS_PROVIDER_WARNING",
    "BlameResult",
    "CommitIndex",
    "CommitIndexFn",
    "blame",
    "sessions_touching",
    "compute_actor_stats",
    "compute_defect_rates",
    "group_defect_rates_by_provider",
    "compute_defect_rates_cross_provider",
    "check_completeness_table_staleness",
]
