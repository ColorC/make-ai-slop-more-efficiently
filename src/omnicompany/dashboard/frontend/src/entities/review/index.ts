/**
 * entities/review — 审阅台可复用组件库 (R2 从 standalone 审阅台抽出).
 *
 * R4 起 standalone (/review-stage) 已退役; 消费方为驾驶舱 review_queue 单例页签、
 * review_material 多实例页签, 以及 CockpitShell (streamStore 的 urgent 角标/推送 toast).
 */

export * from './shared'
export * from './MaterialViews'
export * from './AnnotationsAndComments'
export * from './MaterialSidebar'
export * from './MaterialDetail'
export * from './CommentsPanel'
export * from './ReviewQueueSidebar'
