import type { ElkExtendedEdge, ElkNode, ElkPoint } from "elkjs/lib/elk-api";
import { canvasSpacing, type CanvasSpacing, type CanvasSpacingProfile } from "./materialCanvasModel.ts";

export interface MaterialLayoutBox {
  id: string;
  parentId?: string;
  kind: "material" | "text" | "group";
  x: number;
  y: number;
  width: number;
  height: number;
  locked?: boolean;
}

export interface MaterialLayoutLink {
  id: string;
  source: string;
  target: string;
  labels?: string[];
  /**
   * 端点已被切片钉住（"从这张界面图里的这个按钮出发"）。布局**不得**改写它。
   * 此前布局对所有边一律返回 sourceHandle:"right" / targetHandle:"left"，
   * 所以跑一次智能排布就把切片端点退化成"卡片右边"，切片与自动布局互斥。
   */
  sliceBoundSource?: boolean;
  sliceBoundTarget?: boolean;
}

export interface MaterialLayoutPlacement {
  x: number;
  y: number;
  width: number;
  height: number;
}

export interface MaterialLayoutRoute {
  points: ElkPoint[];
  labelPositions: ElkPoint[];
  /** 缺省 = 布局不表态，调用方保留现有端点（切片钉住的边就是这种情况）。 */
  sourceHandle?: "right";
  targetHandle?: "left";
}

export interface MaterialLayoutResult {
  placements: Map<string, MaterialLayoutPlacement>;
  routes: Map<string, MaterialLayoutRoute>;
}

export interface MaterialLayoutObstacle extends MaterialLayoutPlacement {
  id: string;
}

const rectanglesOverlap = (
  left: MaterialLayoutPlacement,
  right: MaterialLayoutPlacement,
  gap = 0,
) => left.x < right.x + right.width + gap
  && left.x + left.width + gap > right.x
  && left.y < right.y + right.height + gap
  && left.y + left.height + gap > right.y;

/**
 * ELK 负责给出默认布局；锁定节点保留人工坐标，并作为不可穿越的固定障碍。
 * 这里只移动顶层未锁节点，保持 React Flow 子流的相对坐标语义不变。
 *
 * gap 必须由调用方按当前间距档传入。**默认值 48 是 bug 的来源**：ELK 用档内的
 * nodeNode（紧凑档 12）压紧之后，这一步再用 48 把它们重新推开，于是"调了间距却没变紧凑"。
 */
export function avoidLockedPlacementCollisions(
  boxes: MaterialLayoutBox[],
  placements: Map<string, MaterialLayoutPlacement>,
  gap = canvasSpacing("comfortable").nodeNode,
): Map<string, MaterialLayoutPlacement> {
  const next = new Map(placements);
  const roots = boxes.filter((box) => !box.parentId);
  const occupied: MaterialLayoutPlacement[] = [];

  for (const box of roots.filter((item) => item.locked)) {
    const fixed = { x: box.x, y: box.y, width: box.width, height: box.height };
    next.set(box.id, fixed);
    occupied.push(fixed);
  }

  const movable = roots
    .filter((box) => !box.locked)
    .sort((left, right) => {
      const a = next.get(left.id) || left;
      const b = next.get(right.id) || right;
      return a.x - b.x || a.y - b.y || left.id.localeCompare(right.id);
    });
  for (const box of movable) {
    const proposed = next.get(box.id) || { x: box.x, y: box.y, width: box.width, height: box.height };
    const placed = { ...proposed };
    let guard = 0;
    while (guard++ < roots.length * 4) {
      const collision = occupied.find((obstacle) => rectanglesOverlap(placed, obstacle, gap));
      if (!collision) break;
      // 四个方向都试，取位移最小的那个。此前只会往下推（placed.y = 障碍底 + gap），
      // 于是一排本该并列的界面被拉成一条竖列，紧凑度全丢在这一步。
      const candidates = [
        { x: placed.x, y: collision.y + collision.height + gap },
        { x: placed.x, y: collision.y - placed.height - gap },
        { x: collision.x + collision.width + gap, y: placed.y },
        { x: collision.x - placed.width - gap, y: placed.y },
      ];
      const best = candidates
        .map((candidate) => ({
          candidate,
          cost: Math.abs(candidate.x - placed.x) + Math.abs(candidate.y - placed.y),
        }))
        .sort((left, right) => left.cost - right.cost)[0].candidate;
      placed.x = best.x;
      placed.y = best.y;
    }
    next.set(box.id, placed);
    occupied.push(placed);
  }
  return next;
}

const segmentHits = (start: ElkPoint, end: ElkPoint, obstacle: MaterialLayoutObstacle, padding: number) => {
  const left = obstacle.x - padding;
  const right = obstacle.x + obstacle.width + padding;
  const top = obstacle.y - padding;
  const bottom = obstacle.y + obstacle.height + padding;
  if (start.x === end.x) {
    return start.x > left && start.x < right
      && Math.max(start.y, end.y) > top && Math.min(start.y, end.y) < bottom;
  }
  if (start.y === end.y) {
    return start.y > top && start.y < bottom
      && Math.max(start.x, end.x) > left && Math.min(start.x, end.x) < right;
  }
  return false;
};

const routeHits = (points: ElkPoint[], obstacles: MaterialLayoutObstacle[], padding: number) =>
  points.slice(1).some((point, index) => obstacles.some((obstacle) => segmentHits(points[index], point, obstacle, padding)));

/** Deterministic Manhattan fallback used after fixed obstacles displace ELK's proposal. */
export function routeOrthogonalAroundObstacles(
  source: ElkPoint,
  target: ElkPoint,
  obstacles: MaterialLayoutObstacle[],
  padding = 28,
): ElkPoint[] {
  const middleX = source.x + (target.x - source.x) / 2;
  const middleY = source.y + (target.y - source.y) / 2;
  const candidates: ElkPoint[][] = [
    [source, { x: middleX, y: source.y }, { x: middleX, y: target.y }, target],
    [source, { x: source.x, y: middleY }, { x: target.x, y: middleY }, target],
  ];
  if (obstacles.length) {
    const top = Math.min(...obstacles.map((item) => item.y)) - padding;
    const bottom = Math.max(...obstacles.map((item) => item.y + item.height)) + padding;
    const left = Math.min(...obstacles.map((item) => item.x)) - padding;
    const right = Math.max(...obstacles.map((item) => item.x + item.width)) + padding;
    candidates.push(
      [source, { x: source.x, y: top }, { x: target.x, y: top }, target],
      [source, { x: source.x, y: bottom }, { x: target.x, y: bottom }, target],
      [source, { x: left, y: source.y }, { x: left, y: target.y }, target],
      [source, { x: right, y: source.y }, { x: right, y: target.y }, target],
    );
  }
  const compact = (points: ElkPoint[]) => {
    const deduplicated = points.filter((point, index) => {
      const previous = points[index - 1];
      return !previous || point.x !== previous.x || point.y !== previous.y;
    });
    const compacted: ElkPoint[] = [];
    for (const point of deduplicated) {
      const previous = compacted[compacted.length - 1];
      const beforePrevious = compacted[compacted.length - 2];
      if (previous && beforePrevious && (
        (beforePrevious.x === previous.x && previous.x === point.x)
        || (beforePrevious.y === previous.y && previous.y === point.y)
      )) {
        compacted[compacted.length - 1] = point;
      } else {
        compacted.push(point);
      }
    }
    return compacted;
  };
  const valid = candidates.map(compact).filter((route) => !routeHits(route, obstacles, padding));
  const length = (route: ElkPoint[]) => route.slice(1).reduce((sum, point, index) => (
    sum + Math.abs(point.x - route[index].x) + Math.abs(point.y - route[index].y)
  ), 0);
  return (valid.length ? valid : candidates.map(compact)).sort((leftRoute, rightRoute) => (
    length(leftRoute) - length(rightRoute) || leftRoute.length - rightRoute.length
  ))[0];
}

let enginePromise: Promise<InstanceType<(typeof import("elkjs/lib/elk.bundled.js"))["default"]>> | null = null;

async function layoutEngine() {
  if (!enginePromise) {
    enginePromise = import("elkjs/lib/elk.bundled.js").then(({ default: ELK }) => new ELK());
  }
  return enginePromise;
}

/**
 * ELK normally gives the best crossing minimisation, but its browser worker can
 * occasionally never settle on a mixed compound graph. Keep the command useful
 * with a bounded, deterministic relation-layered fallback instead of leaving
 * the toolbar in "正在排布" forever.
 */
export function fallbackMaterialCanvasLayout(
  boxes: MaterialLayoutBox[],
  links: MaterialLayoutLink[],
  profile?: CanvasSpacingProfile,
): MaterialLayoutResult {
  const spacing = canvasSpacing(profile);
  const placements = new Map<string, MaterialLayoutPlacement>();
  const routes = new Map<string, MaterialLayoutRoute>();
  const byId = new Map(boxes.map((box) => [box.id, box]));
  const groupIds = new Set(boxes.filter((box) => box.kind === "group").map((box) => box.id));
  const roots = boxes.filter((box) => !box.parentId || !groupIds.has(box.parentId));
  const rootIds = new Set(roots.map((box) => box.id));
  const ownerOf = (id: string): string | null => {
    const box = byId.get(id);
    if (!box) return null;
    return box.parentId && groupIds.has(box.parentId) ? box.parentId : box.id;
  };

  // Child coordinates stay relative to their group. This preserves authored
  // screenshot/annotation composition while the group participates as one root.
  for (const box of boxes) {
    if (!box.parentId || !groupIds.has(box.parentId)) continue;
    placements.set(box.id, { x: box.x, y: box.y, width: box.width, height: box.height });
  }

  const adjacency = new Map(roots.map((box) => [box.id, new Set<string>()]));
  const directed = new Map(roots.map((box) => [box.id, new Set<string>()]));
  for (const link of links) {
    const source = ownerOf(link.source);
    const target = ownerOf(link.target);
    if (!source || !target || source === target || !rootIds.has(source) || !rootIds.has(target)) continue;
    adjacency.get(source)?.add(target);
    adjacency.get(target)?.add(source);
    directed.get(source)?.add(target);
  }

  const sourceOrder = (left: string, right: string) => {
    const a = byId.get(left)!;
    const b = byId.get(right)!;
    return a.y - b.y || a.x - b.x || left.localeCompare(right);
  };
  const components: string[][] = [];
  const seen = new Set<string>();
  for (const root of [...roots].sort((a, b) => sourceOrder(a.id, b.id))) {
    if (seen.has(root.id)) continue;
    const component: string[] = [];
    const pending = [root.id];
    seen.add(root.id);
    while (pending.length) {
      const id = pending.shift()!;
      component.push(id);
      for (const neighbor of [...(adjacency.get(id) || [])].sort(sourceOrder)) {
        if (seen.has(neighbor)) continue;
        seen.add(neighbor);
        pending.push(neighbor);
      }
    }
    components.push(component);
  }

  const originX = roots.length ? Math.min(...roots.map((box) => box.x)) : 0;
  let componentY = roots.length ? Math.min(...roots.map((box) => box.y)) : 0;
  for (const component of components) {
    const componentSet = new Set(component);
    const indegree = new Map(component.map((id) => [id, 0]));
    for (const source of component) {
      for (const target of directed.get(source) || []) {
        if (componentSet.has(target)) indegree.set(target, (indegree.get(target) || 0) + 1);
      }
    }
    const queue = component.filter((id) => indegree.get(id) === 0).sort(sourceOrder);
    const rank = new Map(component.map((id) => [id, 0]));
    const visited = new Set<string>();
    while (queue.length) {
      const source = queue.shift()!;
      visited.add(source);
      for (const target of directed.get(source) || []) {
        if (!componentSet.has(target)) continue;
        rank.set(target, Math.max(rank.get(target) || 0, (rank.get(source) || 0) + 1));
        const nextDegree = (indegree.get(target) || 0) - 1;
        indegree.set(target, nextDegree);
        if (nextDegree === 0) queue.push(target);
      }
      queue.sort(sourceOrder);
    }
    // Cycles are rare but valid on exploratory canvases. Place the remaining
    // roots after the DAG portion in authored order instead of failing.
    let cycleRank = Math.max(0, ...rank.values()) + 1;
    for (const id of component.filter((candidate) => !visited.has(candidate)).sort(sourceOrder)) {
      rank.set(id, cycleRank++);
    }

    const columns = new Map<number, string[]>();
    for (const id of component) {
      const value = rank.get(id) || 0;
      const column = columns.get(value) || [];
      column.push(id);
      columns.set(value, column);
    }
    let x = originX;
    let componentBottom = componentY;
    for (const value of [...columns.keys()].sort((a, b) => a - b)) {
      const ids = columns.get(value)!.sort(sourceOrder);
      const columnWidth = Math.max(...ids.map((id) => byId.get(id)!.width));
      let y = componentY;
      for (const id of ids) {
        const box = byId.get(id)!;
        const placement = box.locked
          ? { x: box.x, y: box.y, width: box.width, height: box.height }
          : { x, y, width: box.width, height: box.height };
        placements.set(id, placement);
        y += box.height + spacing.nodeNode;
        componentBottom = Math.max(componentBottom, placement.y + placement.height);
      }
      x += columnWidth + spacing.betweenLayers;
    }
    componentY = componentBottom + spacing.componentComponent;
  }

  return {
    placements: avoidLockedPlacementCollisions(boxes, placements, spacing.nodeNode),
    routes,
  };
}

/**
 * 用 ELK 的分层布局处理真实卡片尺寸、连通分量、交叉最小化与编组层级。
 * 返回的子卡坐标相对父组，正好对应 React Flow subflow 的坐标约定。
 */
export async function layoutMaterialCanvas(
  boxes: MaterialLayoutBox[],
  links: MaterialLayoutLink[],
  profile?: CanvasSpacingProfile,
): Promise<MaterialLayoutResult> {
  const spacing: CanvasSpacing = canvasSpacing(profile);
  const groups = new Set(boxes.filter((box) => box.kind === "group").map((box) => box.id));
  const childrenByParent = new Map<string, MaterialLayoutBox[]>();
  const roots: MaterialLayoutBox[] = [];

  for (const box of boxes) {
    if (box.parentId && groups.has(box.parentId)) {
      const siblings = childrenByParent.get(box.parentId) || [];
      siblings.push(box);
      childrenByParent.set(box.parentId, siblings);
    } else {
      roots.push(box);
    }
  }

  const asElkNode = (box: MaterialLayoutBox): ElkNode => {
    const children = childrenByParent.get(box.id) || [];
    if (!children.length) {
      return {
        id: box.id,
        width: box.width,
        height: box.height,
      };
    }
    return {
      id: box.id,
      width: Math.max(260, box.width),
      height: Math.max(180, box.height),
      children: children.map(asElkNode),
      layoutOptions: {
        "elk.padding": `[top=${26 + spacing.nodeNode},left=${spacing.nodeNode},bottom=${spacing.nodeNode},right=${spacing.nodeNode}]`,
        "elk.spacing.nodeNode": String(spacing.nodeNode),
        "elk.layered.spacing.nodeNodeBetweenLayers": String(spacing.betweenLayers),
      },
    };
  };

  const knownIds = new Set(boxes.map((box) => box.id));
  const graph: ElkNode = {
    id: "material-canvas-root",
    children: roots.map(asElkNode),
    edges: links
      .filter((link) => knownIds.has(link.source) && knownIds.has(link.target) && link.source !== link.target)
      .map((link): ElkExtendedEdge => ({
        id: link.id,
        sources: [link.source],
        targets: [link.target],
        // Relation labels are overlays on the editable curve. Letting ELK reserve
        // a full label column makes an otherwise compact layout visibly loose.
      })),
    layoutOptions: {
      "elk.algorithm": "layered",
      "elk.direction": "RIGHT",
      "elk.hierarchyHandling": "INCLUDE_CHILDREN",
      // ELK only chooses compact node positions. Relations remain editable curves
      // in React Flow; spline-aware geometry avoids orthogonal grid bias here.
      // 曲线不因为紧凑而牺牲：SPLINES 在两个档下都保留（业主 2026-08-20 明确要求
      // "保持用曲线的情况下保持高度紧凑"），收缩只发生在间距与卡片形状上。
      "elk.edgeRouting": "SPLINES",
      "elk.padding": `[top=${spacing.nodeNode},left=${spacing.nodeNode},bottom=${spacing.nodeNode},right=${spacing.nodeNode}]`,
      "elk.spacing.nodeNode": String(spacing.nodeNode),
      "elk.spacing.edgeNode": String(spacing.edgeNode),
      "elk.spacing.edgeEdge": String(Math.max(4, Math.round(spacing.edgeNode * 0.7))),
      "elk.spacing.edgeLabel": String(Math.max(4, Math.round(spacing.edgeNode * 0.6))),
      "elk.spacing.componentComponent": String(spacing.componentComponent),
      "elk.layered.spacing.nodeNodeBetweenLayers": String(spacing.betweenLayers),
      "elk.layered.spacing.edgeNodeBetweenLayers": String(Math.max(6, Math.round(spacing.edgeNode * 1.2))),
      "elk.layered.crossingMinimization.strategy": "LAYER_SWEEP",
      "elk.layered.crossingMinimization.greedySwitch.type": "TWO_SIDED",
      "elk.layered.crossingMinimization.greedySwitchHierarchical.type": "TWO_SIDED",
      "elk.layered.nodePlacement.strategy": "NETWORK_SIMPLEX",
      "elk.layered.nodePlacement.favorStraightEdges": "false",
      "elk.layered.compaction.connectedComponents": "true",
      "elk.separateConnectedComponents": "true",
    },
  };

  const timeout = Symbol("elk-layout-timeout");
  let timeoutId: ReturnType<typeof setTimeout> | undefined;
  const laidOut = await Promise.race([
    (await layoutEngine()).layout(graph),
    new Promise<typeof timeout>((resolve) => {
      timeoutId = setTimeout(() => resolve(timeout), 6_000);
    }),
  ]).finally(() => { if (timeoutId) clearTimeout(timeoutId); });
  if (laidOut === timeout) {
    // Do not reuse an engine whose worker stopped responding.
    enginePromise = null;
    return fallbackMaterialCanvasLayout(boxes, links, profile);
  }
  let placements = new Map<string, MaterialLayoutPlacement>();
  const routes = new Map<string, MaterialLayoutRoute>();
  const compactPoints = (points: ElkPoint[]) => points.filter((point, index) => {
    const previous = points[index - 1];
    return !previous || previous.x !== point.x || previous.y !== point.y;
  });
  const linkById = new Map(links.map((link) => [link.id, link]));
  const collect = (node: ElkNode, offsetX = 0, offsetY = 0) => {
    for (const edge of node.edges || []) {
      const points = compactPoints((edge.sections || []).flatMap((section) => [
        section.startPoint,
        ...(section.bendPoints || []),
        section.endPoint,
      ].map((point) => ({ x: point.x + offsetX, y: point.y + offsetY }))));
      if (points.length >= 2) {
        const link = linkById.get(edge.id);
        routes.set(edge.id, {
          points,
          labelPositions: (edge.labels || []).map((label) => ({
            x: (label.x || 0) + (label.width || 0) / 2 + offsetX,
            y: (label.y || 0) + (label.height || 0) / 2 + offsetY,
          })),
          // 切片钉住的端点不表态：让调用方保留"从这个按钮出发"。
          ...(link?.sliceBoundSource ? {} : { sourceHandle: "right" as const }),
          ...(link?.sliceBoundTarget ? {} : { targetHandle: "left" as const }),
        });
      }
    }
    const items = node.children || [];
    for (const item of items || []) {
      const original = boxes.find((box) => box.id === item.id);
      if (original) {
        placements.set(item.id, {
          x: item.x || 0,
          y: item.y || 0,
          width: item.width || original.width,
          height: item.height || original.height,
        });
      }
      collect(item, offsetX + (item.x || 0), offsetY + (item.y || 0));
    }
  };
  collect(laidOut);
  // 与 ELK 同档，否则这一步会把刚压紧的结果重新撑开。
  placements = avoidLockedPlacementCollisions(boxes, placements, spacing.nodeNode);
  return { placements, routes };
}
