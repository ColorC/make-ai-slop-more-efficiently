// 提单子（intake）与优先度阶梯的界面级验收 —— 真 DOM、真组件、真 fetch 桩。
//
// 单测（taskBus.test.ts）只保证分区口径对；这里保证「用户点得到、看得见」：
//   · **没有专门的提单子按钮**（用户 2026-09-03 要求）——收件箱和归档同一套规矩：
//     有货就出面板，没货不摆空壳；记单子走面板组头的 `+`，和别的面板同一条路径；
//   · 点开的是**只要标题**的捕获框（没有描述/属性格子）——每多一格就少记一条；
//   · 记下去的是 stateKey=inbox、无优先级、无项目；
//   · 单子不混进泳道与主体列表；
//   · 阶梯名字来自服务端词表，不是前端写死的。

import React from 'react'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
// @ts-expect-error 仅测试用, 不进浏览器。
import { readFileSync } from 'node:fs'
// @ts-expect-error 仅测试用, 不进浏览器。
import { resolve } from 'node:path'
import TaskPanel from './TaskPanel'
import { setTaskProfile } from './taskBus'

declare const process: { cwd(): string }

const PROFILE = JSON.parse(
  readFileSync(resolve(process.cwd(), '../../../../data/task_core_profile.json'), 'utf8'),
)

const BOARD = {
  ok: true,
  runtimeId: 'rt-1',
  lanes: [],
  projects: [{
    projectId: 'alpha',
    title: 'alpha',
    tasks: [
      {
        id: 't-hot', title: '救火单', projectId: 'alpha', parentTaskId: null, orderKey: null,
        stateKey: 'todo', priority: 5, archivedAt: null, revision: 1, locationLinks: [],
      },
      {
        id: 't-note', title: '看板拖不动', projectId: 'alpha', parentTaskId: null, orderKey: null,
        stateKey: 'inbox', priority: null, archivedAt: null, revision: 1, locationLinks: [],
      },
      {
        id: 't-plat', title: '把 webtwin 联调走通', projectId: 'alpha', parentTaskId: null,
        orderKey: null, stateKey: 'todo', priority: 2, archivedAt: null, revision: 1,
        locationLinks: [],
      },
    ],
  }],
}

const posted: { url: string; body: any }[] = []

beforeEach(() => {
  posted.length = 0
  setTaskProfile(null)
  vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => {
    if (String(url).startsWith('/api/tbus/profile')) {
      return { json: async () => ({ ok: true, profile: PROFILE }) } as Response
    }
    if (String(url).startsWith('/api/tbus/board')) {
      return { json: async () => BOARD } as Response
    }
    if (init?.method === 'POST') {
      posted.push({ url: String(url), body: JSON.parse(String(init.body)) })
      return { json: async () => ({ ok: true }) } as Response
    }
    return { json: async () => ({ ok: true, notes: [], bindings: [], links: [] }) } as Response
  }))
})

describe('提单子（intake）', () => {
  it('没有专门的提单子按钮 —— 不为一个动作单开一条路径', async () => {
    render(<TaskPanel />)
    await screen.findByTestId('tk-body')
    expect(screen.queryByTestId('tk-intake')).toBeNull()
    expect(screen.queryByText('提单子')).toBeNull()
  })

  it('收件箱有货就出面板, 摆在最前 —— 未分诊的东西先看见', async () => {
    render(<TaskPanel />)
    const group = await screen.findByTestId('tk-group-inbox')
    expect(group.textContent).toContain('收件箱')
    expect(screen.getByText('看板拖不动')).toBeTruthy()
    // 摆在第一个
    const keys = [...document.querySelectorAll('[data-testid^="tk-group-"]')]
      .map((e) => e.getAttribute('data-testid'))
    expect(keys[0]).toBe('tk-group-inbox')
  })

  it('单子不混进泳道与主体 —— 换成按状态分组也不出现在待办里', async () => {
    render(<TaskPanel />)
    await screen.findByTestId('tk-group-inbox')
    const todo = screen.queryByTestId('tk-group-todo')
    if (todo) expect(todo.textContent).not.toContain('看板拖不动')
  })

  it('走面板组头的 + 开出的是只要标题的捕获框', async () => {
    render(<TaskPanel />)
    fireEvent.click(await screen.findByTestId('tk-add-inbox'))
    await screen.findByTestId('tk-composer')
    expect(screen.getByTestId('tk-composer-input')).toBeTruthy()
    expect(screen.queryByTestId('tk-composer-desc')).toBeNull()
    expect(screen.queryByTestId('tk-composer-project')).toBeNull()
    expect(screen.queryByTestId('tk-composer-priority')).toBeNull()
  })

  it('记下的单子: state=inbox, 不定级, 不归项目', async () => {
    render(<TaskPanel />)
    fireEvent.click(await screen.findByTestId('tk-add-inbox'))
    const input = await screen.findByTestId('tk-composer-input')
    fireEvent.change(input, { target: { value: '导出按钮点了没反应' } })
    fireEvent.keyDown(input, { key: 'Enter' })
    await waitFor(() => expect(posted.length).toBe(1))
    expect(posted[0].url).toBe('/api/tbus/task')
    expect(posted[0].body.title).toBe('导出按钮点了没反应')
    expect(posted[0].body.stateKey).toBe('inbox')
    expect(posted[0].body.priority).toBeUndefined()
    expect(posted[0].body.projectId).toBeUndefined()
  })

  it('快捷键 i 直接开捕获框 —— 快捷键不占版面, 留着', async () => {
    render(<TaskPanel />)
    const root = await screen.findByTestId('tbus-panel')
    fireEvent.keyDown(root, { key: 'i' })
    await screen.findByTestId('tk-composer')
    expect(screen.queryByTestId('tk-composer-desc')).toBeNull()
  })
})

describe('优先度阶梯上屏', () => {
  it('阶梯记号不写数字, 只留刻度 + 名字与判据（用户 2026-09-03）', async () => {
    render(<TaskPanel />)
    await screen.findByTestId('tk-body')
    const tier = await screen.findByLabelText('优先度 紧急处理单')
    expect(tier.textContent).toBe('')                  // 不写数字
    expect(tier.getAttribute('data-tier')).toBe('5')   // 等级只留给 CSS 画高度
    const title = tier.getAttribute('title') || ''
    expect(title).toContain('紧急处理单')
    expect(title).toContain(PROFILE.priorityLadder[0].criterion)
    expect(title).not.toContain('5 ·')
  })

  it('消息清空时不渲染完成卡片，也不渲染待办段标', async () => {
    render(<TaskPanel />)
    await screen.findByText('救火单')
    expect(screen.queryByTestId('tk-line')).toBeNull()
    expect(screen.queryByTestId('tk-msg-clear')).toBeNull()
  })

  it('账页仍按项目聚合, 只是项目之间按优先级排（用户 2026-09-04 更正）', async () => {
    render(<TaskPanel />)
    await screen.findByTestId('tk-body')
    const keys = [...document.querySelectorAll('[data-testid^="tk-group-"]')]
      .map((e) => (e.getAttribute('data-testid') || '').replace('tk-group-', ''))
    // 收件箱恒在最前, 其后是项目面板 —— 不是阶梯面板
    expect(keys[0]).toBe('inbox')
    expect(keys).toContain('alpha')
    expect(keys).not.toContain('hotfix')
  })
})
