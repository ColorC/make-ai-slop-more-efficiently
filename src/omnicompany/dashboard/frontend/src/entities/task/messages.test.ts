// 消息队列的排序口径 —— 用户 2026-09-04：「消息不要聚合，就是按照各自优先级排序，
// 后续允许精密排序。」守的就是这两句。

import { describe, expect, it } from 'vitest'
import { type InboxProject, buildMessageList, sessionRank } from './messages'

// 2026-09-05 收件箱改实时取源: 行的键是 dedupeKey, 会话那一类也由服务端适配器出,
// 前端不再本地拼一份 —— 所以这里的样本全走同一条 InboxProject 通道。
function item(
  key: string,
  priority: number | null,
  over: Partial<{ orderKey: string; createdAt: string; source: string }> = {},
) {
  return {
    key, title: key, priority, projectId: 'p', sessionId: null,
    source: 'review', kind: 'pending', url: '', ...over,
  }
}

function wrap(items: ReturnType<typeof item>[]): InboxProject[] {
  return [{ projectId: 'p', top: 0, count: items.length, sessions: [{ sessionId: null, items }] }]
}

const session = (id: string, priority: number) =>
  item(`session:${id}`, priority, { source: 'session' })

describe('消息队列排序', () => {
  it('是一条平铺队列, 不按项目分组', () => {
    const rows = buildMessageList(wrap([item('a', 1), item('b', 3)]))
    expect(Array.isArray(rows)).toBe(true)
    expect(rows.map((r) => r.id)).toEqual(['b', 'a'])
  })

  it('按各自位次降序, 未定级排最后', () => {
    const rows = buildMessageList(wrap([item('none', null), item('low', 1), item('high', 5)]))
    expect(rows.map((r) => r.id)).toEqual(['high', 'low', 'none'])
  })

  it('会话的位次来自订阅矩阵那条规则 —— 调一行就能把它挪位置', () => {
    const top = buildMessageList(wrap([item('p4', 4), session('s', 5)]))
    expect(top[0].id).toBe('session:s')
    const demoted = buildMessageList(wrap([item('p4', 4), session('s', 1)]))
    expect(demoted[0].id).toBe('p4')
    expect(sessionRank({ subscriptions: { rules: [{ source: 'session', priority: 2 }] } })).toBe(2)
    expect(sessionRank(null)).toBe(5)
  })

  it('人手排过的压过阶梯 —— 这是「精密排序」的落点', () => {
    const rows = buildMessageList(
      wrap([item('high', 5), item('pinned', 0, { orderKey: 'a' }), session('s', 5)]),
    )
    expect(rows[0].id).toBe('pinned')
  })

  it('同位次按先来后到, 顺序稳定', () => {
    const rows = buildMessageList(
      wrap([item('earlier', 3), item('later', 3)]),
    )
    expect(rows.map((r) => r.id)).toEqual(['earlier', 'later'])
  })
})

describe('行的来源判定', () => {
  it('看 rawSource 而不是给人读的短标 —— 认错了就点不开会话', () => {
    // 2026-09-05 实拍回归: source 是「会话」这种短标, 拿它比对永远不等于 'session',
    // 于是会话行被当成总线行, 点击走了跳深链分支, 而会话行没有 url —— 点了没反应。
    const [row] = buildMessageList([
      {
        projectId: 'p',
        top: 5,
        count: 1,
        sessions: [
          {
            sessionId: 's1',
            items: [
              {
                key: 'session:s1@fp',
                title: '跑完了',
                priority: 5,
                projectId: 'p',
                sessionId: 's1',
                source: '会话',
                rawSource: 'session',
                kind: 'session-ended',
                url: '',
              },
            ],
          },
        ],
      },
    ])
    expect(row.kind).toBe('session')
    expect(row.sessionId).toBe('s1')
  })
})
