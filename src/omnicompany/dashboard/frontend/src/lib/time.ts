// 相对时间格式化 — 全 dashboard 唯一实现。
// 此前 5 个组件各自复制 relTime（ProjectBoard/ProjectDetail/ProjectsPanel 的 ISO 中文版,
// ThreadMonitorPanel/cc_session 的 unix 秒英文版）, 2026-06-13 收束至此。

function diffSeconds(t: number): number {
  return Math.max(0, (Date.now() - t) / 1000)
}

/** ISO 时间串 → 中文相对时间（分钟前/小时前/天前）。空值/非法返回 ''，调用点自定占位符。 */
export function relTimeZh(iso?: string | null): string {
  if (!iso) return ''
  const t = new Date(iso).getTime()
  if (Number.isNaN(t)) return ''
  const d = diffSeconds(t)
  if (d < 3600) return `${Math.round(d / 60)}分钟前`
  if (d < 86400) return `${Math.round(d / 3600)}小时前`
  return `${Math.round(d / 86400)}天前`
}

/** unix 秒 → 紧凑英文相对时间（s/m/h/d/mo）。0/空返回 ''。 */
export function relTimeEn(unixSec?: number | null): string {
  if (!unixSec) return ''
  const diff = diffSeconds(unixSec * 1000)
  if (diff < 60) return `${Math.round(diff)}s`
  if (diff < 3600) return `${Math.round(diff / 60)}m`
  if (diff < 86400) return `${Math.round(diff / 3600)}h`
  if (diff < 86400 * 30) return `${Math.round(diff / 86400)}d`
  return `${Math.round(diff / (86400 * 30))}mo`
}
