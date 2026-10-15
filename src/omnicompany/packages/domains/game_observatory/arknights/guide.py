"""新手引导推进设施。

**存在理由**：MAA 全部 869 个任务中没有任何一个处理新手引导（已核对真源，无任何任务提到
引导／新手／高亮）。而引导是强制模态：它给全屏加压暗遮罩，使 MAA 赖以工作的模板匹配全部
失配（实测 ``StartUp`` 在遮罩下 180s 超时且一次点击都没发出）。引导又不是一次性的——后续
功能解锁仍会弹。因此按分流规则，这属于「MAA 做不到 → 必须自建成设施」。

**不使用硬编码坐标**：每一步的点击目标都由 MAA 的 OCR 在增量任务里定位（``Omni@Guide-*``），
本模块只负责「判断是否还在引导」与「哪一步该试」。

**推进判据靠像素变化量**，而非「任务返回成功」。原因是实测发现 OCR 任务返回成功并不等于
点击产生了效果，必须用真实前后帧对比。空转基线（不点击）的画面漂移约 8% 像素、平均绝对差
约 2.7（界面有立绘动画），而一次生效的引导点击会带来约 98% 像素变化、平均绝对差约 90 ——
两者相差一个数量级，判据稳健。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Sequence

__all__ = [
    "FrameStats",
    "GuideStep",
    "GuideWalker",
    "WalkOutcome",
    "WalkReport",
    "DEFAULT_GUIDE_STEPS",
    "MASKED_LUMINANCE",
    "find_dialogue_center",
    "find_highlight",
    "find_highlight_box",
    "frame_change",
    "has_guide_dialogue",
    "mean_luminance",
]

#: 空转时的画面漂移上限（变化像素占比）。实测基线 7.9%~9.4%，留出余量。
IDLE_DRIFT_RATIO = 0.20

#: 判定「点击确实生效」的变化像素占比下限。实测生效时为 97.7%。
PROGRESS_RATIO = 0.35


@dataclass(frozen=True, slots=True)
class GuideStep:
    """一个引导步骤的点击目标。``task`` 是增量资源里的 OCR 任务名。"""

    task: str
    label: str


#: 已知引导步骤。按顺序尝试；新遇到的步骤往这里加一条即可，无需改代码。
DEFAULT_GUIDE_STEPS: tuple[GuideStep, ...] = (
    GuideStep("Omni@GuideOverlay-ClickTarget", "前往上一次作战"),
    GuideStep("Omni@Guide-ClickMainThemeNav", "底部导航·曲谱"),
    GuideStep("Omni@Guide-ClickTerminalNav", "底部导航·终端"),
    GuideStep("Omni@GuideOverlay-ClickStageName", "关卡名"),
    GuideStep("Omni@GuideOverlay-ClickStageCode", "关卡编号"),
    GuideStep("Omni@Guide-ClickAnyConfirm", "确认/继续类按钮"),
)


@dataclass(frozen=True, slots=True)
class FrameStats:
    """两帧之间的差异度量。"""

    mean_abs_diff: float
    changed_ratio: float

    @property
    def looks_like_progress(self) -> bool:
        return self.changed_ratio >= PROGRESS_RATIO

    @property
    def looks_idle(self) -> bool:
        return self.changed_ratio <= IDLE_DRIFT_RATIO


def frame_change(before: object, after: object, threshold: int = 12) -> FrameStats:
    """计算两帧差异。参数为 HxWx3 的 uint8 数组（numpy）。

    :param threshold: 单通道差异超过该值才算「该像素变了」，用于滤掉编码噪声。
    """
    import numpy as np

    a = np.asarray(before).astype(np.int16)
    b = np.asarray(after).astype(np.int16)
    if a.shape != b.shape:
        raise ValueError(f"帧尺寸不一致: {a.shape} vs {b.shape}")
    d = np.abs(a - b)
    return FrameStats(
        mean_abs_diff=float(d.mean()),
        changed_ratio=float((d.max(axis=2) > threshold).mean()),
    )


def has_guide_dialogue(
    frame: object,
    *,
    darkness: int = 70,
    min_band_height: int = 30,
    min_width_ratio: float = 0.45,
    edge_margin_ratio: float = 0.08,
) -> bool:
    """判断画面上是否存在引导对话框。

    引导对话框的稳定特征是一条**很宽的近黑横条**（半透明黑底承载白字），且位置随步骤变化
    （实测见过顶部与底部两种）。因此按「是否存在足够高、足够宽的近黑横带」来判定，而不是
    绑定某一步的文案——绑文案会导致每加一步就要改检测器。

    **排除画面上下边缘**：模拟器与部分界面存在黑色 letterbox 边条，会造成假阳性
    （实测在游戏标题页 lum=181.9 的亮画面上被误判为有引导）。引导对话框不会紧贴屏幕边缘，
    故上下各留出 ``edge_margin_ratio`` 的余量。

    :param darkness: 亮度低于此值算「近黑」。
    :param min_band_height: 连续满足条件的行数下限（按帧高等比缩放）。
    :param min_width_ratio: 一行中近黑像素需占据的最小宽度比例。
    :param edge_margin_ratio: 上下各忽略的高度比例，用于排除 letterbox。
    """
    import numpy as np

    arr = np.asarray(frame)
    if arr.ndim != 3:
        raise ValueError("需要 HxWx3 帧")
    h = arr.shape[0]
    m = int(h * edge_margin_ratio)
    core = arr[m : h - m] if h - 2 * m > 0 else arr

    lum = core.astype(np.uint16).sum(axis=2) // 3
    wide = (lum < darkness).mean(axis=1) >= min_width_ratio
    # 阈值按帧高等比缩放：30 行是以 720p 为基准定的
    need = max(8, int(min_band_height * core.shape[0] / 720))

    run = 0
    for is_wide in wide:
        run = run + 1 if is_wide else 0
        if run >= need:
            return True
    return False


def mean_luminance(frame: object) -> float:
    """画面平均亮度。用于区分引导的两类步骤。

    实测：**对话步**（只有立绘＋文字条，无遮罩）约 146；**目标步**（全屏压暗、
    只有指定元素保持正常亮度）约 51~62。二者相差悬殊，阈值稳健。
    """
    import numpy as np

    arr = np.asarray(frame).astype(np.uint16)
    return float(arr.sum(axis=2).mean() / 3)


#: 低于此平均亮度即认为处于「带遮罩的目标步」。实测目标步 51~62、对话步 146。
MASKED_LUMINANCE = 100.0


def _run_extents(mask: object, axis: int) -> tuple[int, int, int, int] | None:
    """找出最长的一段连续 True，返回 ``(line_index, start, end, length)``。

    ``axis=1`` 沿行扫（找横向亮线），``axis=0`` 沿列扫（找纵向亮线）。向量化实现。
    """
    import numpy as np

    arr = np.asarray(mask)
    arr = arr if axis == 1 else arr.T
    n, m = arr.shape
    cur = np.zeros(n, dtype=np.int32)
    best = np.zeros(n, dtype=np.int32)
    best_end = np.zeros(n, dtype=np.int32)
    for j in range(m):
        cur = np.where(arr[:, j], cur + 1, 0)
        upd = cur > best
        best = np.where(upd, cur, best)
        best_end = np.where(upd, j + 1, best_end)
    i = int(np.argmax(best))
    length = int(best[i])
    if length <= 0:
        return None
    end = int(best_end[i])
    return i, end - length, end, length


def find_highlight_box(
    frame: object,
    *,
    bright: int = 180,
    min_edge: int = 50,
) -> tuple[int, int] | None:
    """定位引导高亮框的中心。

    引导给目标元素画一圈**白色矩形描边**。这比「最亮聚集区」可靠——实测在描边式高亮上，
    亮块质心会锁到旁边的装饰性闪光图标，算出的点落在框外约 25px 导致点击无效。

    实现要点（由真实帧扫描确定）：**不能**用「超过阈值的行/列的包围盒」，因为高亮框可能
    有边缘贴屏（左边缘在 x=0，于是没有左竖线），也可能某条边被遮挡，包围盒会退化。
    正确做法是取**最长横向亮线的起止**作为 x 范围、**最长纵向亮线的起止**作为 y 范围，
    这样缺边也能重建。实测该帧上横线 363px（框上边，x 从 0 起）、竖线 y[154,281]（框右边），
    重建中心落在高亮元素内部。

    :param bright: 亮度阈值。实测 180~210 结果稳定，取 180。
    :param min_edge: 认定为「边」所需的最小连续长度。
    """
    import numpy as np

    lum = np.asarray(frame).astype(np.uint16).sum(axis=2) // 3
    b = lum >= bright
    if not b.any():
        return None

    h = _run_extents(b, 1)  # 横向亮线
    v = _run_extents(b, 0)  # 纵向亮线
    if h is None or v is None:
        return None
    _, x0, x1, hlen = h
    _, y0, y1, vlen = v
    if hlen < min_edge or vlen < min_edge:
        return None
    return (x0 + x1) // 2, (y0 + y1) // 2



def find_highlight(frame: object, *, thresh: int = 175) -> tuple[int, int] | None:
    """在带遮罩的引导步里定位被高亮的目标元素（亮块质心法）。

    原理：遮罩把全屏压暗，**唯独指定元素保持正常亮度**，因此最亮的聚集区就是目标。

    实测有效于**填充式**高亮（如底部导航图标）：定位到 (347, 652)，点击后画面变化 85.8%。
    实测**失效于描边式**高亮：会锁到旁边的闪光装饰。因此本函数作为
    :func:`find_highlight_box` 的兜底，而非首选。
    """
    import numpy as np

    arr = np.asarray(frame)
    lum = arr.astype(np.uint16).sum(axis=2) // 3
    mask = lum >= thresh
    if not mask.any():
        return None
    h, w = mask.shape
    gh, gw = 16, 16
    ph, pw = h // gh, w // gw
    if ph == 0 or pw == 0:
        return None
    grid = mask[: ph * gh, : pw * gw].reshape(gh, ph, gw, pw).sum(axis=(1, 3))
    gy, gx = np.unravel_index(int(np.argmax(grid)), grid.shape)
    sub = mask[gy * ph : (gy + 1) * ph, gx * pw : (gx + 1) * pw]
    ys, xs = np.nonzero(sub)
    if ys.size == 0:
        return None
    return int(gx * pw + xs.mean()), int(gy * ph + ys.mean())



def find_dialogue_center(
    frame: object, *, darkness: int = 70, min_width_ratio: float = 0.45
) -> tuple[int, int] | None:
    """定位对话框（最宽的近黑横带）中心，用于推进无遮罩的纯对话步。

    **注意**：在带遮罩的步骤上这个函数会退化——遮罩本身就是一大片暗区，算出的中心没有
    意义（实测点该中心 12 次全部无效，变化量均在噪声地板以下）。因此必须先用
    :func:`mean_luminance` 判定步骤类型，只在无遮罩时使用本函数。
    """
    import numpy as np

    lum = np.asarray(frame).astype(np.uint16).sum(axis=2) // 3
    wide = (lum < darkness).mean(axis=1) >= min_width_ratio
    best = (0, -1, -1)
    start = None
    for i, w in enumerate(list(wide) + [False]):
        if w and start is None:
            start = i
        elif not w and start is not None:
            if i - start > best[0]:
                best = (i - start, start, i)
            start = None
    if best[0] <= 0:
        return None
    _, y0, y1 = best
    cols = (lum[y0:y1] < darkness).mean(axis=0) >= 0.5
    xs = np.nonzero(cols)[0]
    if xs.size == 0:
        return None
    return int((xs[0] + xs[-1]) // 2), int((y0 + y1) // 2)


class WalkOutcome:
    CLEARED = "cleared"
    STUCK = "stuck"
    EXHAUSTED = "exhausted"
    BLOCKED = "blocked"


@dataclass
class WalkReport:
    outcome: str
    iterations: int
    advanced: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.outcome == WalkOutcome.CLEARED

    def summary(self) -> str:
        return (
            f"{self.outcome} after {self.iterations} iter, "
            f"advanced={len(self.advanced)} step(s)"
        )


class GuideWalker:
    """走引导。**监督下使用的工具，不是自主 agent。**

    安全模型（由一次真实事故确立）
    ------------------------------
    早期版本仅以「画面上有宽暗带」判定处于引导态，结果在正常界面盲点，把游戏点进了带
    「x1解锁」按钮的回想页 —— 差一步就消耗掉不可逆的凭证。

    随后尝试过四种视觉启发式作为安全门，**全部在正常界面误报**：宽暗带、亮块质心、
    矩形描边、亮度分区。原因是任何 UI 都有暗带和长亮线，「界面身份」本就不该靠这些猜。

    因此改为**针对危害本身**：``spend_guard`` 用 OCR 否定清单检查屏上是否存在会花费资源或
    造成不可逆选择的控件（解锁／购买／招募／碎石／放弃……）。命中即立刻停止，
    outcome 为 ``BLOCKED``。理由是这类危害总是伴随明确文字，直接盯文字比猜界面可靠。

    其余约束：
      * ``max_iterations`` 默认收紧到 8，限制单次调用能走多远。
      * 每次点击都以**新鲜帧的像素变化量**验证，仅凭任务返回成功不算（实测存在任务成功而
        点击未生效的情形）。
      * 调用方须显式声明已确认处于引导态（``confirmed_in_guide=True``），不允许在未确认
        状态下自动开跑。

    :param tap: 在 MAA 坐标空间点击一个点（由感知算出，非目测）。
    :param grab: 取一张**新鲜**帧。不可传入返回缓存帧的实现。
    :param click_task: 执行一个增量 OCR 点击任务，作为兜底手段。
    :param spend_guard: 返回 ``True`` 表示屏上存在消耗类控件。强烈建议提供。
    """

    def __init__(
        self,
        tap: Callable[[int, int], bool],
        grab: Callable[[], object],
        click_task: Callable[[str], object] | None = None,
        steps: Sequence[GuideStep] = DEFAULT_GUIDE_STEPS,
        settle: Callable[[], None] | None = None,
        spend_guard: Callable[[], bool] | None = None,
    ) -> None:
        self._tap = tap
        self._grab = grab
        self._click_task = click_task
        self._steps = tuple(steps)
        self._settle = settle or (lambda: None)
        self._spend_guard = spend_guard


    def guide_active(self, frame: object | None = None) -> bool:
        return has_guide_dialogue(frame if frame is not None else self._grab())

    def _try_point(self, pt: tuple[int, int] | None, before: object) -> FrameStats | None:
        if pt is None:
            return None
        self._tap(*pt)
        self._settle()
        return frame_change(before, self._grab())

    def _candidates(self, frame: object) -> list[tuple[str, tuple[int, int] | None]]:
        """按可靠性排序的候选点位。

        顺序依据实测：描边框中心最可靠；亮块质心只在填充式高亮上有效；对话框中心只在
        无遮罩的纯对话步有效（在遮罩步上会退化为遮罩本身的中心，实测 12 次全部无效）。
        """
        lum = mean_luminance(frame)
        box = find_highlight_box(frame)
        blob = find_highlight(frame)
        dlg = find_dialogue_center(frame)
        if lum < MASKED_LUMINANCE:
            return [("box", box), ("blob", blob), ("dialogue", dlg)]
        return [("dialogue", dlg), ("box", box), ("blob", blob)]

    def walk(
        self,
        max_iterations: int = 30,
        *,
        confirmed_in_guide: bool = False,
        enforce_spend_guard: bool = False,
    ) -> WalkReport:
        """推进引导，最多 ``max_iterations`` 轮。

        :param confirmed_in_guide: 调用方须显式确认当前确实处于引导态。默认 ``False`` 会
            直接拒绝——因为没有任何可靠的纯视觉判据能识别引导态（四种都试过、都误报），
            所以这一确认只能来自外部（人工看帧或上层流程），不能由本模块自行断言。
        :param enforce_spend_guard: 是否启用消耗防护。**默认关闭**：当前用的是专用测试
            账号，用户明示资源可随意消耗，此时防护只会拖慢推进（它在主界面等正常界面必然
            命中，因为那里本就有「采购中心」「招募」等字样）。在**非测试账号**上应显式
            打开。
        """
        report = WalkReport(outcome=WalkOutcome.EXHAUSTED, iterations=0)
        if not confirmed_in_guide:
            report.outcome = WalkOutcome.BLOCKED
            report.notes.append(
                "拒绝执行：未确认处于引导态。纯视觉判据不可靠（宽暗带／亮块／描边／亮度分区"
                "均在正常界面误报过），须由调用方确认后传 confirmed_in_guide=True。"
            )
            return report

        for i in range(1, max_iterations + 1):
            report.iterations = i

            if enforce_spend_guard and self._spend_guard is not None:
                try:
                    if self._spend_guard():
                        report.outcome = WalkOutcome.BLOCKED
                        report.notes.append(
                            f"iter{i}: 屏上出现消耗类控件，已停止以免造成不可逆消耗。"
                        )
                        return report
                except Exception as exc:  # 守卫本身失败时按不安全处理
                    report.outcome = WalkOutcome.BLOCKED
                    report.notes.append(
                        f"iter{i}: 消耗防护检查失败（{type(exc).__name__}），"
                        "按不安全处理并停止。"
                    )
                    return report

            frame = self._grab()
            if not has_guide_dialogue(frame):
                report.outcome = WalkOutcome.CLEARED
                return report

            lum = mean_luminance(frame)
            progressed = False
            tried: list[str] = []
            for kind, pt in self._candidates(frame):
                if pt is None:
                    continue
                tried.append(f"{kind}{pt}")
                stats = self._try_point(pt, self._grab())
                if stats and stats.looks_like_progress:
                    report.advanced.append(f"{kind}@{pt}")
                    report.notes.append(
                        f"iter{i} lum={lum:.0f} {kind} tap{pt} "
                        f"changed={stats.changed_ratio:.1%}"
                    )
                    progressed = True
                    break
            if progressed:
                continue

            if self._click_task is not None:
                for step in self._steps:
                    before = self._grab()
                    try:
                        self._click_task(step.task)
                    except Exception:
                        continue
                    self._settle()
                    st = frame_change(before, self._grab())
                    if st.looks_like_progress:
                        report.advanced.append(f"ocr:{step.label}")
                        report.notes.append(
                            f"iter{i} ocr {step.label} changed={st.changed_ratio:.1%}"
                        )
                        progressed = True
                        break
            if not progressed:
                report.outcome = WalkOutcome.STUCK
                report.notes.append(
                    f"iter{i} lum={lum:.0f}: 全部策略未推进；tried={tried}"
                )
                return report
        return report
