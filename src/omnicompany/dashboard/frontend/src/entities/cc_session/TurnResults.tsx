// 会话侧栏「结果」页: 这个会话的关键产物画廊。
//
// 2026-11-20 业主:「结果页也可以有用来保存关键内容，但是应该让人集中注意力而非进一步分散」;
// 「确实不需要文字，也不需要什么准则提醒和问题选项」。
// 所以这里只有产物本身: 图、视频、画布卡、文件的缩略块, 点开全屏, Esc 回来。
import '../../i18n/config.js'
import React from 'react'
import { useTranslation } from 'react-i18next'
import { RefreshCw } from 'lucide-react'
import { ResultTile, ResultViewerHost } from './ResultView'
import { useResultViewer } from './resultItems'
import { useSessionResults } from './useSessionResults'

export function TurnResultsPage({ sessionId }: { sessionId: string }) {
  const { t } = useTranslation('results')
  const { items, loading, refresh, results } = useSessionResults(sessionId, 'session')
  const open = useResultViewer((state) => state.open)
  return (
    <div className="rv-gallery-root" data-testid="companion-results">
      <ResultViewerHost />
      <button type="button" className="rv-gallery-refresh cccp-icon-btn" title={t('refresh')} onClick={() => { void refresh() }} disabled={loading}>
        <RefreshCw size={14} className={loading ? 'cccp-spinning' : undefined} />
      </button>
      {items.length > 0
        ? (
          <div className="rv-gallery">
            {items.map((item, index) => (
              <ResultTile key={item.key} item={item} size="grid" onOpen={() => open(items, index)} />
            ))}
          </div>
        )
        : (
          <div className="rv-gallery-empty">
            {loading ? '' : results?.unavailable === 'transcript-missing' ? t('noTranscript') : t('empty')}
          </div>
        )}
    </div>
  )
}
