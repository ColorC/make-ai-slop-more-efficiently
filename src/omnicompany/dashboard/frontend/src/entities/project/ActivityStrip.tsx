/** 近 7 天逐日活跃格(旧→新, 末位=今天)。
 *  2026-07 首屏拆包: 从 ProjectBoard 抽出 —— ProjectsPanel 侧栏也消费本组件,
 *  从 ProjectBoard 引它会把整块项目工作板钉回首屏静态图(懒加载失效)。
 *  2026-07-19 蓝图 G(G.3②): 7 圆点升级为 9×9 方格 hatch 阵(活跃=青白密纹/非活跃=暗纹,
 *  GitHub 贡献格气质的蓝图语言表达;样式= blueprint.css .v2-fresh)。 */
export function ActivityStrip({ days }: { days?: boolean[] }) {
  if (!days || days.length === 0) return null
  const activeCount = days.filter(Boolean).length
  return (
    <span
      data-testid="project-activity-strip"
      title={`近 7 天活跃 ${activeCount} 天(左旧右新, 最右=今天)`}
      className="v2-fresh"
    >
      {days.map((on, i) => (
        <i key={i} className={on ? 'on' : ''} />
      ))}
    </span>
  )
}
