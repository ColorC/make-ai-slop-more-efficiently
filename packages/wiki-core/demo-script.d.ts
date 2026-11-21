export type TourAction =
  | { type: "click"; target: string; timeoutMs?: number }
  | { type: "clickCell"; q: number; r: number }
  | { type: "waitFor"; target: string; timeoutMs?: number }
  | { type: "waitMs"; ms: number }
  | { type: "eval"; ref: string; [k: string]: unknown };

export interface TourLink {
  rel: "doc_paragraph" | "material" | "demo_step";
  href: string;
  label?: string;
}

export interface TourStep {
  /** 稳定步骤 id（评论锚点）。 */
  id: string;
  title?: string;
  /** 覆盖层叙述（中文，受中文-only gate）。 */
  narration: string;
  /** 高亮目标 CSS 选择器（优先复用已有 data-testid）。 */
  target?: string;
  /** 到达本步状态的声明式动作（纯 JSON）。 */
  action?: TourAction;
  /** autoplay 模式下停留毫秒，默认 1800。 */
  autoplayMs?: number;
  /** 录制回退帧基名（与 ops beat 命名对齐）。 */
  screenshotRef?: string;
  links?: TourLink[];
}

export interface TourScript {
  id: string;
  title?: string;
  app?: string;
  version?: number;
  steps: TourStep[];
}

export interface DemoActionHooks {
  clickCell?: (q: number, r: number) => void | Promise<void>;
  [name: string]: ((action: any) => void | Promise<void>) | undefined;
}

export interface DemoStepAnchor {
  kind: "demo_step";
  tour_id: string;
  step_id: string;
  step_index: number;
  title: string;
}

export declare function validateTour(tour: TourScript): string[];
export declare function stepAnchor(tour: TourScript, step: TourStep, index?: number): DemoStepAnchor;
export declare function executeAction(
  action: TourAction | undefined,
  ctx?: { appRoot?: ParentNode; hooks?: DemoActionHooks },
): Promise<void>;
