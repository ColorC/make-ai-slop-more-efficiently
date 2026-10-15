"""投影残差测量设施：用语义动作与独立像素观测取得非循环真值。

原理
----
命令是**语义的**（格子坐标），观测是**像素的**（干员渲染位置）。前后帧必须处于同一
显示状态，并用 MAA 的逐动作事件限定时间窗口；跨越暂停压暗、场景切换或结算的样本必须拒绝。
从有效差分中观测干员渲染位置，再与 ``tile_center()`` 输出相减，即得**客观残差**。

``deployment_with_pause`` 实测表示部署拖拽期间的慢动作流程，并不表示可以在游戏的 PAUSE
界面直接部署。MAA 在一次部署内部会切换暂停状态，因此不能把跨暂停态的帧直接相减。

为什么不用目视
--------------
目视只能得出「看着挺准」，而验收要求的是误差分布（均值／p95／最大）。此外把大量截图送进
上下文会导致请求载荷超限（实测：累积 14 张 1280x720 截图后请求被拒）。

判定阈值
--------
残差须小于**最小格心间距的一半**——超过该值时反查会落到邻格。1-7 在 1280x720 下最小
格心间距实测 53.0px，故阈值为 ±26.5px。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Sequence

__all__ = [
    "AlignmentEvidence",
    "DeployAnchor",
    "ResidualReport",
    "alignment_evidence",
    "change_centroid",
    "frames_share_display_state",
    "measure_residuals",
    "nearest_tile_distance",
    "null_nearest_distance",
    "persistent_change_centroid",
    "roi_luminance",
]


@dataclass(frozen=True, slots=True)
class DeployAnchor:
    """一次「命令格 → 观测像素」的配对。"""

    name: str
    col: int
    row: int
    expected_x: float
    expected_y: float
    observed_x: float | None
    observed_y: float | None
    changed_pixels: int

    @property
    def residual(self) -> float | None:
        if self.observed_x is None or self.observed_y is None:
            return None
        dx = self.observed_x - self.expected_x
        dy = self.observed_y - self.expected_y
        return (dx * dx + dy * dy) ** 0.5

    @property
    def dx(self) -> float | None:
        return None if self.observed_x is None else self.observed_x - self.expected_x

    @property
    def dy(self) -> float | None:
        return None if self.observed_y is None else self.observed_y - self.expected_y

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "tile": [self.col, self.row],
            "expected": [round(self.expected_x, 1), round(self.expected_y, 1)],
            "observed": (
                None
                if self.observed_x is None
                else [round(self.observed_x, 1), round(self.observed_y, 1)]
            ),
            "dx": None if self.dx is None else round(self.dx, 1),
            "dy": None if self.dy is None else round(self.dy, 1),
            "residual": None if self.residual is None else round(self.residual, 1),
            "changed_pixels": self.changed_pixels,
        }


@dataclass(frozen=True, slots=True)
class ResidualReport:
    anchors: tuple[DeployAnchor, ...]
    tolerance: float

    @property
    def measured(self) -> tuple[DeployAnchor, ...]:
        return tuple(a for a in self.anchors if a.residual is not None)

    @property
    def residuals(self) -> tuple[float, ...]:
        return tuple(a.residual for a in self.measured)  # type: ignore[misc]

    @property
    def mean(self) -> float | None:
        rs = self.residuals
        return sum(rs) / len(rs) if rs else None

    @property
    def p95(self) -> float | None:
        rs = sorted(self.residuals)
        if not rs:
            return None
        # 小样本用「最接近的秩」而非插值，避免制造虚假精度
        idx = min(len(rs) - 1, int(round(0.95 * (len(rs) - 1))))
        return rs[idx]

    @property
    def worst(self) -> float | None:
        rs = self.residuals
        return max(rs) if rs else None

    @property
    def passes(self) -> bool:
        """全部冻结锚点都测到且残差在容差内才算通过。"""
        rs = self.residuals
        complete = bool(self.anchors) and len(self.measured) == len(self.anchors)
        return complete and all(r <= self.tolerance for r in rs)

    def as_dict(self) -> dict[str, Any]:
        return {
            "tolerance_px": round(self.tolerance, 1),
            "measured": len(self.measured),
            "total": len(self.anchors),
            "mean": None if self.mean is None else round(self.mean, 1),
            "p95": None if self.p95 is None else round(self.p95, 1),
            "max": None if self.worst is None else round(self.worst, 1),
            "passes": self.passes,
            "anchors": [a.as_dict() for a in self.anchors],
        }


def change_centroid(
    before: object,
    after: object,
    *,
    threshold: int = 28,
    min_pixels: int = 120,
    roi: tuple[int, int, int, int] | None = None,
    max_change_ratio: float = 0.25,
    cells: int = 24,
) -> tuple[float | None, float | None, int]:
    """求两帧之间**新出现物体**的位置。

    估计器：先把变化掩膜降采样成 ``cells x cells`` 的密度网格，取密度最高的格子，
    再在其邻域内做强度加权质心。**不用全局质心**——全局质心在存在多处变化时会被拉到画面
    中央，给出一个看似精确实则无意义的坐标（实测踩过：暂停压暗消失导致 78% 像素变化，
    全局质心恰好落在 ROI 正中）。

    有效性闸门：变化比例超过 ``max_change_ratio`` 即判为**全局状态改变**（暂停压暗、
    场景切换、结算弹出），返回 ``None`` 而不是硬算一个坐标。宁可承认测不到，
    也不能产出假数据。

    :param threshold: 单通道差异超过该值才计入，滤掉编码噪声。
    :param min_pixels: 变化像素数下限。
    :param roi: ``(x, y, w, h)`` 限定搜索范围，排除 HUD 干扰。
    :param max_change_ratio: 变化像素占 ROI 面积的上限。
    :param cells: 密度网格边长。
    :return: ``(cx, cy, 变化像素数)``；无效时前两项为 ``None``。
    """
    import numpy as np

    a = np.asarray(before).astype(np.int16)
    b = np.asarray(after).astype(np.int16)
    if a.shape != b.shape:
        raise ValueError(f"帧尺寸不一致: {a.shape} vs {b.shape}")

    diff = np.abs(a - b).max(axis=2)
    mask = diff > threshold

    if roi is not None:
        x, y, w, h = roi
        keep = np.zeros_like(mask)
        keep[y : y + h, x : x + w] = True
        mask &= keep
        area = max(1, w * h)
    else:
        area = mask.size

    n = int(mask.sum())
    if n < min_pixels:
        return None, None, n
    if n / area > max_change_ratio:
        # 全局状态改变，不是单个物体出现
        return None, None, n

    H, W = mask.shape
    ch, cw = max(1, H // cells), max(1, W // cells)
    gh, gw = H // ch, W // cw
    grid = mask[: gh * ch, : gw * cw].reshape(gh, ch, gw, cw).sum(axis=(1, 3))
    gy, gx = np.unravel_index(int(np.argmax(grid)), grid.shape)

    # 在最密格子的 3x3 邻域内做强度加权质心
    y0 = max(0, (gy - 1) * ch)
    y1 = min(H, (gy + 2) * ch)
    x0 = max(0, (gx - 1) * cw)
    x1 = min(W, (gx + 2) * cw)
    sub_mask = mask[y0:y1, x0:x1]
    sub_diff = diff[y0:y1, x0:x1] * sub_mask
    total = float(sub_diff.sum())
    if total <= 0:
        return None, None, n
    ys, xs = np.nonzero(sub_mask)
    wsel = sub_diff[ys, xs].astype(float)
    cx = float(x0 + (xs * wsel).sum() / total)
    cy = float(y0 + (ys * wsel).sum() / total)
    return cx, cy, n


def roi_luminance(
    frame: object,
    roi: tuple[int, int, int, int] | None = None,
) -> float:
    """返回帧或 ROI 的平均亮度。

    这里只用于识别暂停压暗等**显示状态**，不参与目标定位。把状态判定与目标搜索分开，
    可以避免用预期格心反过来挑选观测样本。
    """
    import numpy as np

    a = np.asarray(frame)
    if a.ndim != 3 or a.shape[2] < 3:
        raise ValueError(f"帧必须是 HxWxC 彩色图像，实际 {a.shape}")
    if roi is not None:
        x, y, w, h = roi
        a = a[y : y + h, x : x + w]
    if a.size == 0:
        raise ValueError("ROI 不能为空")
    return float(a[..., :3].astype(np.float32).mean())


def frames_share_display_state(
    frames: Sequence[object],
    *,
    roi: tuple[int, int, int, int] | None = None,
    max_relative_luminance_delta: float = 0.18,
) -> bool:
    """判断一组帧是否处于同一显示状态。

    实机暂停压暗会使战场亮度下降约 45%，远大于普通动画波动。这里使用相对亮度跨度做
    独立闸门；后续像素差分仍会再以全局变化比例复核。少于两帧没有比较意义，返回
    ``False``。
    """
    if len(frames) < 2:
        return False
    values = [roi_luminance(frame, roi) for frame in frames]
    high = max(values)
    if high <= 0:
        return False
    return (high - min(values)) / high <= max_relative_luminance_delta


def persistent_change_centroid(
    before: object,
    after_a: object,
    after_b: object,
    *,
    threshold: int = 28,
    post_threshold: int = 24,
    min_pixels: int = 80,
    roi: tuple[int, int, int, int] | None = None,
    max_change_ratio: float = 0.25,
    cells: int = 24,
) -> tuple[float | None, float | None, int]:
    """定位动作后持续存在、且在两张后置帧间基本稳定的新物体。

    与 :func:`change_centroid` 相比，本估计器要求像素同时满足：

    1. 相对动作前帧，在两张动作后帧中都发生了明显变化；
    2. 两张动作后帧之间的变化不大。

    因而沿路径移动的敌人和一次性特效会被大幅削弱，而落地后持续存在的干员仍会形成
    高密度区域。目标搜索覆盖整个 ROI，**不会使用预期格心裁剪候选区**；预期坐标只在
    观测完成后用于计算残差。
    """
    import numpy as np

    a = np.asarray(before).astype(np.int16)
    b = np.asarray(after_a).astype(np.int16)
    c = np.asarray(after_b).astype(np.int16)
    if a.shape != b.shape or a.shape != c.shape:
        raise ValueError(f"帧尺寸不一致: {a.shape}, {b.shape}, {c.shape}")

    d1 = np.abs(a - b).max(axis=2)
    d2 = np.abs(a - c).max(axis=2)
    post = np.abs(b - c).max(axis=2)
    changed1 = d1 > threshold
    changed2 = d2 > threshold
    mask = changed1 & changed2 & (post <= post_threshold)

    if roi is not None:
        x, y, w, h = roi
        keep = np.zeros_like(mask)
        keep[y : y + h, x : x + w] = True
        changed1 &= keep
        changed2 &= keep
        mask &= keep
        area = max(1, w * h)
    else:
        area = mask.size

    # 任一前后对发生全局改变，说明跨越了暂停压暗、切场或结算；即使交集看起来局部，
    # 也不能把它作为部署观测。
    if int(changed1.sum()) / area > max_change_ratio:
        return None, None, int(mask.sum())
    if int(changed2.sum()) / area > max_change_ratio:
        return None, None, int(mask.sum())

    n = int(mask.sum())
    if n < min_pixels:
        return None, None, n

    h, w = mask.shape
    cell_h, cell_w = max(1, h // cells), max(1, w // cells)
    grid_h, grid_w = h // cell_h, w // cell_w
    grid = (
        mask[: grid_h * cell_h, : grid_w * cell_w]
        .reshape(grid_h, cell_h, grid_w, cell_w)
        .sum(axis=(1, 3))
    )
    # 干员是跨多个小格的纵向精灵；短时间内近似静止的敌人可能在单个小格里更紧凑。
    # 若只取单格峰值，实机曾以 717:695 的微弱差距错误选择敌人；按 3x3 邻域总密度
    # 选择后，干员区域为 3955，且仍完全不使用预期格心。
    padded = np.pad(grid, 1)
    neighbourhood = sum(
        padded[dy : dy + grid_h, dx : dx + grid_w]
        for dy in range(3)
        for dx in range(3)
    )
    grid_y, grid_x = np.unravel_index(
        int(np.argmax(neighbourhood)), neighbourhood.shape
    )

    y0 = max(0, (grid_y - 1) * cell_h)
    y1 = min(h, (grid_y + 2) * cell_h)
    x0 = max(0, (grid_x - 1) * cell_w)
    x1 = min(w, (grid_x + 2) * cell_w)
    local_mask = mask[y0:y1, x0:x1]
    # 两张后置帧都与基线不同的最小强度，是“持续出现”的保守权重。
    weights = np.minimum(d1, d2)[y0:y1, x0:x1] * local_mask
    total = float(weights.sum())
    if total <= 0:
        return None, None, n
    ys, xs = np.nonzero(local_mask)
    selected = weights[ys, xs].astype(float)
    cx = float(x0 + (xs * selected).sum() / total)
    cy = float(y0 + (ys * selected).sum() / total)
    return cx, cy, n


def measure_residuals(
    anchors: Sequence[DeployAnchor], tolerance: float
) -> ResidualReport:
    """汇总命令格与独立像素观测之间的残差。"""
    return ResidualReport(anchors=tuple(anchors), tolerance=tolerance)


def nearest_tile_distance(
    point: tuple[float, float],
    centers: Sequence[tuple[float, float]],
) -> tuple[float, int]:
    """点到最近格心的距离，以及该格心在序列中的下标。"""
    best = (float("inf"), -1)
    px, py = point
    for i, (cx, cy) in enumerate(centers):
        d = ((cx - px) ** 2 + (cy - py) ** 2) ** 0.5
        if d < best[0]:
            best = (d, i)
    return best


@dataclass(frozen=True, slots=True)
class AlignmentEvidence:
    """「观测到的单位位置是否贴合投影格心」的统计证据。

    为什么需要零假设
    ----------------
    「离最近格心很近」单看没有意义——任何点都有最近格心。只有与**随机点的期望距离**
    对比，才构成可证伪的证据。若投影存在偏移或缩放错误，观测距离会退化到零假设水平。
    """

    observed: tuple[float, ...]
    null_mean: float
    null_p50: float
    tile_count: int
    roi: tuple[int, int, int, int]

    @property
    def obs_mean(self) -> float | None:
        return sum(self.observed) / len(self.observed) if self.observed else None

    @property
    def obs_p95(self) -> float | None:
        s = sorted(self.observed)
        if not s:
            return None
        return s[min(len(s) - 1, int(round(0.95 * (len(s) - 1))))]

    @property
    def obs_max(self) -> float | None:
        return max(self.observed) if self.observed else None

    @property
    def improvement(self) -> float | None:
        """观测均值相对零假设均值的倍数。>1 表示显著优于随机。"""
        m = self.obs_mean
        if m is None or m <= 0:
            return None
        return self.null_mean / m

    def as_dict(self) -> dict[str, Any]:
        return {
            "samples": len(self.observed),
            "tile_count": self.tile_count,
            "roi": list(self.roi),
            "observed_mean": None if self.obs_mean is None else round(self.obs_mean, 1),
            "observed_p95": None if self.obs_p95 is None else round(self.obs_p95, 1),
            "observed_max": None if self.obs_max is None else round(self.obs_max, 1),
            "null_mean": round(self.null_mean, 1),
            "null_p50": round(self.null_p50, 1),
            "improvement_vs_null": (
                None if self.improvement is None else round(self.improvement, 2)
            ),
        }


def null_nearest_distance(
    centers: Sequence[tuple[float, float]],
    roi: tuple[int, int, int, int],
    *,
    samples: int = 4000,
    seed: int = 12345,
) -> tuple[float, float]:
    """零假设：ROI 内均匀随机点到最近格心的距离分布。

    :return: ``(均值, 中位数)``
    """
    import numpy as np

    if not centers:
        return 0.0, 0.0
    rng = np.random.default_rng(seed)
    x, y, w, h = roi
    pts = np.column_stack(
        [rng.uniform(x, x + w, samples), rng.uniform(y, y + h, samples)]
    )
    cs = np.asarray(centers, dtype=float)
    d = np.sqrt(
        ((pts[:, None, 0] - cs[None, :, 0]) ** 2)
        + ((pts[:, None, 1] - cs[None, :, 1]) ** 2)
    ).min(axis=1)
    return float(d.mean()), float(np.median(d))


def alignment_evidence(
    observations: Sequence[tuple[float, float]],
    centers: Sequence[tuple[float, float]],
    roi: tuple[int, int, int, int],
    *,
    samples: int = 4000,
    seed: int = 12345,
) -> AlignmentEvidence:
    """把一批观测到的单位位置与投影格心比对，产出统计证据。"""
    obs = tuple(nearest_tile_distance(p, centers)[0] for p in observations)
    nm, np50 = null_nearest_distance(centers, roi, samples=samples, seed=seed)
    return AlignmentEvidence(
        observed=obs,
        null_mean=nm,
        null_p50=np50,
        tile_count=len(centers),
        roi=roi,
    )
