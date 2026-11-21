import type { TourScript, TourStep, DemoActionHooks } from "./demo-script";
import type { DemoCommentStore } from "./comments";

export interface MountDemoTourOptions {
  tour: TourScript;
  /** action 选择器/高亮的解析根，默认 document。 */
  appRoot?: ParentNode;
  /** 每步"评论"入口的存储；省略则不显示评论按钮。 */
  comments?: DemoCommentStore | null;
  /** app 专属动作钩子（如 clickCell）。 */
  hooks?: DemoActionHooks;
  /** 挂载后立即自动播放（引导动画）。 */
  autoplay?: boolean;
  /** 尊重 prefers-reduced-motion：跳过平滑滚动。 */
  reducedMotion?: boolean;
  onStep?: (s: { index: number; step: TourStep }) => void;
}

export interface DemoTourHandle {
  goTo(index: number): void;
  next(): Promise<void>;
  back(): Promise<void>;
  play(): void;
  pause(): void;
  getCurrent(): { index: number; step: TourStep } | null;
  stepCount(): number;
  destroy(): void;
}

export declare function mountDemoTour(rootEl: Element, opts: MountDemoTourOptions): DemoTourHandle;
