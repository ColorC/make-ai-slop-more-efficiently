// HTML 消毒统一入口 —— 剪贴板富文本(HTML)和 markdown 渲染出的 HTML 塞进 innerHTML 前都要过这层。
// 用 DOMPurify 默认配置(它默认就保留 style 属性、剥 script/事件属性), 不额外加自定义白名单,
// 否则会把剪贴板富文本(Word/网页复制常见的 style/span 嵌套)洗坏。
import DOMPurify from "dompurify";
import { micromark } from "micromark";
import { gfm, gfmHtml } from "micromark-extension-gfm";

/** 用 DOMPurify 默认配置清洗一段 HTML(剥 script/事件属性, 保留 style 等常规标签属性)。 */
export function sanitizeHtml(html: string): string {
  return DOMPurify.sanitize(html);
}

/** markdown → HTML(micromark + GFM: 表格/删除线/任务列表/自动链接/脚注) → 消毒, 供渲染到 innerHTML / srcDoc 的场景统一使用。
 *  2026-09-01: 原先只有 CommonMark, 审阅报告里的表格整段退化成竖线文本(用户反馈"审阅台 markdown 没按预览显示")。 */
export function renderMarkdownSafe(md: string): string {
  return sanitizeHtml(micromark(md, { extensions: [gfm()], htmlExtensions: [gfmHtml()] }));
}
