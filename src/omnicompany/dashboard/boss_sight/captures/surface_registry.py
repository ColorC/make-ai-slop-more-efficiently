# [OMNI] origin=claude-code domain=dashboard/boss_sight ts=2026-06-27 type=infra status=active
# [OMNI] summary="统一捕获目标解析器: omni 网页表面信标登记 + 屏幕矩形→实体几何相交。"
# [OMNI] why="poof 截图任意位置时, 把屏幕矩形翻译成它压在哪个 omni 实体上(material/plan/note/project/task)。"
# [OMNI] tags=universal-capture,resolver,surface
"""统一捕获 · 目标解析器(第一层 · omni 表面信标)。

机制(见 plan docs/plans/dashboard/[2026-06-27]UNIVERSAL-CAPTURE):
- 每个 omni 网页(驾驶舱 / vilo demo / narrative_studio …)内嵌信标 JS, 周期性 upsert 自己:
  url / title / devicePixelRatio / 视口尺寸 / 当前可见实体们的视口矩形(CSS px, 取自 getBoundingClientRect)。
- poof 截图后给出: 屏幕矩形(物理像素) + 该浏览器内容区屏幕原点 O(物理像素, poof 从 UIA 取) + 主机 url/title 提示。
- 本模块: 用 O 和 dpr 把屏幕矩形换算回视口 CSS 坐标, 跟信标上报的实体矩形做几何相交, 返回重叠最大的实体 omni_uri。

坐标换算: 视口CSS = (屏幕物理 - O) / devicePixelRatio。

纯函数(map/overlap/best)单测友好; 不触盘、不依赖框架。
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Any

# 信标多久没上报就算下线(秒)。页面每 ~1.5s 上报一次, 给足容差。
SURFACE_TTL_SEC = 12.0


@dataclass
class _Surface:
    surface_id: str
    url: str
    title: str
    dpr: float
    viewport: dict[str, float]                       # {"w":..,"h":..} CSS px
    entities: list[dict[str, Any]] = field(default_factory=list)  # [{omni_uri,kind,title,x,y,w,h(CSS px)}]
    hover: dict[str, Any] | None = None              # 光标下最新元素(通用解析, 不靠埋点): {tag,id,cls,text,selector,x,y,w,h,omni_*}
    content_els: list[dict[str, Any]] = field(default_factory=list)  # 视口内内容原子(CSS px), 供"选区里有哪些元素"几何切分
    updated_at: float = 0.0


# ── 纯几何(单测入口) ──────────────────────────────────────────────

def map_screen_rect_to_viewport(
    screen_rect: list[float], content_origin: list[float], dpr: float
) -> list[float]:
    """屏幕物理矩形 [l,t,r,b] → 视口 CSS 矩形 [l,t,r,b]。

    content_origin = 该浏览器视口左上角的物理像素坐标 [ox, oy](poof 从 UIA 取内容区原点)。
    dpr = 页面 devicePixelRatio(物理/CSS)。
    """
    d = dpr if dpr and dpr > 0 else 1.0
    ox, oy = content_origin[0], content_origin[1]
    l, t, r, b = screen_rect
    return [(l - ox) / d, (t - oy) / d, (r - ox) / d, (b - oy) / d]


def rect_overlap_area(a: list[float], b: list[float]) -> float:
    """两个 [l,t,r,b] 矩形的重叠面积(<=0 表示不相交)。"""
    iw = min(a[2], b[2]) - max(a[0], b[0])
    ih = min(a[3], b[3]) - max(a[1], b[1])
    if iw <= 0 or ih <= 0:
        return 0.0
    return iw * ih


def _entity_rect(e: dict[str, Any]) -> list[float]:
    x, y = float(e.get("x", 0)), float(e.get("y", 0))
    w, h = float(e.get("w", 0)), float(e.get("h", 0))
    return [x, y, x + w, y + h]


def overlapping_entities(viewport_rect: list[float], entities: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """视口矩形压到的所有实体(按重叠面积降序), 每个带 overlap_fraction。
    回答"这张截图里有哪些材料/计划/笔记/项目/任务", 不只一个。"""
    cap_area = max(1.0, (viewport_rect[2] - viewport_rect[0]) * (viewport_rect[3] - viewport_rect[1]))
    out: list[dict[str, Any]] = []
    for e in entities:
        area = rect_overlap_area(viewport_rect, _entity_rect(e))
        if area <= 0:
            continue
        out.append({**e, "overlap_fraction": round(area / cap_area, 4)})
    out.sort(key=lambda e: -e["overlap_fraction"])
    return out


def entity_at_point(viewport_point: list[float], entities: list[dict[str, Any]]) -> dict[str, Any] | None:
    """视口某个点(CSS px)落在哪个实体上 —— 取包含该点的最小实体(最具体)。
    用于"这条评论写在哪个材料/元素旁边"。包含不到时返回 None。"""
    px, py = viewport_point[0], viewport_point[1]
    best: dict[str, Any] | None = None
    best_area = float("inf")
    for e in entities:
        l, t, r, b = _entity_rect(e)
        if l <= px <= r and t <= py <= b:
            area = max(1.0, (r - l) * (b - t))
            if area < best_area:
                best, best_area = e, area
    return best


def split_elements_by_selection(
    viewport_rect: list[float], els: list[dict[str, Any]], cap: int = 80,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """把内容原子按选区分两类: 完全在选区内(contained) / 与选区重叠但不完全在内(overlapping)。
    各按阅读顺序(上→下, 左→右)排序, 各截断到 cap(超出量会在调用处如实标注)。"""
    l, t, r, b = viewport_rect[0], viewport_rect[1], viewport_rect[2], viewport_rect[3]
    contained: list[dict[str, Any]] = []
    overlapping: list[dict[str, Any]] = []
    for e in els:
        er = _entity_rect(e)
        inside = er[0] >= l and er[1] >= t and er[2] <= r and er[3] <= b
        if inside and er[2] > er[0] and er[3] > er[1]:
            contained.append(e)
        elif rect_overlap_area(viewport_rect, er) > 0:
            overlapping.append(e)
    key = lambda e: (round(float(e.get("y", 0)) / 8), float(e.get("x", 0)))  # 阅读顺序(容差 8px 行)
    contained.sort(key=key)
    overlapping.sort(key=key)
    return contained[:cap], overlapping[:cap]


def best_entity(viewport_rect: list[float], entities: list[dict[str, Any]]) -> dict[str, Any] | None:
    """与视口矩形重叠最大的实体。平手取面积最小的(最具体的那个)。返回 None 表示没有任何实体被压到。"""
    best: dict[str, Any] | None = None
    best_area = 0.0
    best_entity_area = float("inf")
    for e in entities:
        er = _entity_rect(e)
        area = rect_overlap_area(viewport_rect, er)
        if area <= 0:
            continue
        ea = max(1.0, (er[2] - er[0]) * (er[3] - er[1]))
        if area > best_area or (area == best_area and ea < best_entity_area):
            best, best_area, best_entity_area = e, area, ea
    if best is None:
        return None
    cap_area = max(1.0, (viewport_rect[2] - viewport_rect[0]) * (viewport_rect[3] - viewport_rect[1]))
    return {**best, "overlap_fraction": round(best_area / cap_area, 4)}


# ── 表面登记表(进程内, 带 TTL) ──────────────────────────────────

class SurfaceRegistry:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._surfaces: dict[str, _Surface] = {}

    def upsert(
        self, *, surface_id: str, url: str, title: str, dpr: float,
        viewport: dict[str, float], entities: list[dict[str, Any]],
        hover: dict[str, Any] | None = None, content_els: list[dict[str, Any]] | None = None,
        now: float | None = None,
    ) -> None:
        with self._lock:
            self._surfaces[surface_id] = _Surface(
                surface_id=surface_id, url=url or "", title=title or "",
                dpr=float(dpr or 1.0), viewport=viewport or {},
                entities=list(entities or []), hover=hover,
                content_els=list(content_els or []),
                updated_at=now if now is not None else time.time(),
            )

    def _live(self, now: float) -> list[_Surface]:
        return [s for s in self._surfaces.values() if (now - s.updated_at) <= SURFACE_TTL_SEC]

    def list_live(self, now: float | None = None) -> list[dict[str, Any]]:
        n = now if now is not None else time.time()
        with self._lock:
            return [
                {"surface_id": s.surface_id, "url": s.url, "title": s.title,
                 "dpr": s.dpr, "entity_count": len(s.entities), "age": round(n - s.updated_at, 2)}
                for s in self._live(n)
            ]

    def _pick_surface(
        self, surfaces: list[_Surface], url_hint: str, title_hint: str
    ) -> _Surface | None:
        if not surfaces:
            return None
        url_hint = (url_hint or "").strip()
        title_hint = (title_hint or "").strip()
        # 1) url 精确/包含匹配优先
        if url_hint:
            exact = [s for s in surfaces if s.url == url_hint]
            if exact:
                return max(exact, key=lambda s: s.updated_at)
            part = [s for s in surfaces if s.url and (s.url in url_hint or url_hint in s.url)]
            if part:
                return max(part, key=lambda s: s.updated_at)
        # 2) 标题包含匹配
        if title_hint:
            tmatch = [s for s in surfaces if s.title and (s.title in title_hint or title_hint in s.title)]
            if tmatch:
                return max(tmatch, key=lambda s: s.updated_at)
        # 3) 只有一个活表面 → 直接用(单 omni 标签页的常见情形)
        if len(surfaces) == 1:
            return surfaces[0]
        # 4) 多个无匹配 → 取最近上报的(通常是前台那个)
        return max(surfaces, key=lambda s: s.updated_at)

    def resolve(
        self, *, screen_rect: list[float], content_origin: list[float] | None,
        dpr_hint: float | None = None, url_hint: str = "", title_hint: str = "",
        now: float | None = None,
    ) -> dict[str, Any] | None:
        """屏幕矩形 → omni 实体。返回 {omni_uri,kind,title,overlap_fraction,surface_id} 或 None。

        content_origin 缺失(poof 拿不到内容区原点)→ 无法换算 → 返回 None(交给像素兜底)。
        """
        n = now if now is not None else time.time()
        with self._lock:
            surfaces = self._live(n)
            surf = self._pick_surface(surfaces, url_hint, title_hint)
        if surf is None or not content_origin:
            return None
        dpr = surf.dpr or (dpr_hint or 1.0)
        vrect = map_screen_rect_to_viewport(list(screen_rect), list(content_origin), dpr)
        hit = best_entity(vrect, surf.entities)
        contained = overlapping_entities(vrect, surf.entities)
        # 通用 DOM 元素(信标报的光标下元素, 不靠埋点): 若它的矩形落在选区内, 标记 in_selection —— 这才是"指的哪个元素"。
        hover = surf.hover
        element = None
        if hover and hover.get("tag"):
            hx, hy = float(hover.get("x", 0)), float(hover.get("y", 0))
            hr = [hx, hy, hx + float(hover.get("w", 0)), hy + float(hover.get("h", 0))]
            element = {**hover, "in_selection": rect_overlap_area(vrect, hr) > 0}
        # 选区里的元素: 完全在内 + 重叠(通用 DOM, 不靠埋点)。这是"我圈了哪些东西"的正解, 取代单个悬停近似。
        el_in, el_over = split_elements_by_selection(vrect, surf.content_els)
        cap = 80
        # 在 omni 页面上就回 page + 页内材料(哪怕没压在具体卡上 → omni_uri=None, 但页面/材料清单 + 通用元素仍有用)。
        return {
            "omni_uri": hit.get("omni_uri") if hit else None,
            "kind": hit.get("kind") if hit else None,
            "title": hit.get("title") if hit else None,
            "overlap_fraction": hit.get("overlap_fraction") if hit else None,
            "surface_id": surf.surface_id,
            "surface_url": surf.url,
            "page": {"url": surf.url, "title": surf.title},
            "entities": contained,
            "element": element,
            "elements_contained": el_in,
            "elements_overlapping": el_over,
            "elements_truncated": len(surf.content_els) > 2 * cap,  # 如实标注是否有截断
        }

    def resolve_points(
        self, *, points: list[list[float]], content_origin: list[float] | None,
        dpr_hint: float | None = None, url_hint: str = "", title_hint: str = "",
        now: float | None = None,
    ) -> list[dict[str, Any] | None]:
        """一批屏幕点(物理像素)→ 各自落在哪个实体上。与 resolve 同一个表面/换算。
        每个点回 {omni_uri,kind,title} 或 None(没压在实体上)。用于"每条评论挂哪条材料"。"""
        if not points:
            return []
        n = now if now is not None else time.time()
        with self._lock:
            surfaces = self._live(n)
            surf = self._pick_surface(surfaces, url_hint, title_hint)
        if surf is None or not content_origin:
            return [None for _ in points]
        dpr = surf.dpr or (dpr_hint or 1.0)
        out: list[dict[str, Any] | None] = []
        for p in points:
            vp = [(p[0] - content_origin[0]) / dpr, (p[1] - content_origin[1]) / dpr]
            ent = entity_at_point(vp, surf.entities)
            out.append(
                {"omni_uri": ent.get("omni_uri"), "kind": ent.get("kind"), "title": ent.get("title")}
                if ent else None
            )
        return out


_REGISTRY: SurfaceRegistry | None = None


def get_surface_registry() -> SurfaceRegistry:
    global _REGISTRY
    if _REGISTRY is None:
        _REGISTRY = SurfaceRegistry()
    return _REGISTRY


__all__ = [
    "SurfaceRegistry", "get_surface_registry",
    "map_screen_rect_to_viewport", "rect_overlap_area", "best_entity", "overlapping_entities",
    "entity_at_point", "SURFACE_TTL_SEC",
]
