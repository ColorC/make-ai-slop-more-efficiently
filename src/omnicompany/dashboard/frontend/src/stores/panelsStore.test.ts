import { beforeEach, describe, expect, it } from 'vitest'
import {
  CONTROLLER_TAB_ID,
  PROJECT_BOARD_TAB_ID,
  QUEST_BOARD_TAB_ID,
  TAB_SNAPSHOT_KEY,
  loadTabSnapshot,
  saveTabSnapshot,
  usePanels,
  withDefaultTabs,
} from './panelsStore'
import { useReviewQueueFocus } from './reviewQueueFocusStore'

describe('panelsStore default tabs', () => {
  const boot = { tabs: usePanels.getState().tabs.map((t) => t.id), activeId: usePanels.getState().activeId }

  beforeEach(() => {
    usePanels.setState({ tabs: withDefaultTabs([]), activeId: CONTROLLER_TAB_ID })
  })

  it('starts with project, quests, and controller but does not pin them', () => {
    expect(boot.tabs).toEqual([PROJECT_BOARD_TAB_ID, QUEST_BOARD_TAB_ID, CONTROLLER_TAB_ID])
    expect(usePanels.getState().tabs.every((t) => !t.pinned)).toBe(true)
    expect(boot.activeId).toBe(PROJECT_BOARD_TAB_ID)
  })

  it.each([PROJECT_BOARD_TAB_ID, QUEST_BOARD_TAB_ID, CONTROLLER_TAB_ID])('allows closing %s', (id) => {
    usePanels.getState().activate(id)
    usePanels.getState().closeTab(id)
    expect(usePanels.getState().tabs.map((t) => t.id)).not.toContain(id)
    expect(usePanels.getState().activeId).not.toBe(id)
  })

  it('allows an intentionally empty workspace', () => {
    usePanels.getState().setTabs([], null)
    expect(usePanels.getState().tabs).toEqual([])
    expect(usePanels.getState().activeId).toBeNull()
  })

  it('restores exactly the supplied tabs instead of reinserting defaults', () => {
    usePanels.getState().setTabs([
      { id: 'cc_session:chat-2', ref: { type: 'cc_session', id: 'chat-2' }, title: 'chat 2' },
    ], 'cc_session:chat-2')

    expect(usePanels.getState().tabs.map((t) => t.id)).toEqual(['cc_session:chat-2'])
    expect(usePanels.getState().activeId).toBe('cc_session:chat-2')
  })

  it('replaces a temporary short-id title after entity metadata resolves', () => {
    const id = usePanels.getState().openTab(
      { type: 'cc_session', id: '862bb496' },
      '862bb496',
    )
    usePanels.getState().renameTab(id, '系统组 Wiki 归档旧业务')
    expect(usePanels.getState().tabs.find((tab) => tab.id === id)?.title)
      .toBe('系统组 Wiki 归档旧业务')
  })

  it('keeps explicit dock placement requests for split monitoring', () => {
    usePanels.getState().openTab(
      { type: 'material', id: 'mat-1' },
      'material 1',
      undefined,
      { direction: 'right', referenceTabId: CONTROLLER_TAB_ID },
    )
    expect(usePanels.getState().tabs.find((t) => t.id === 'material:mat-1')?.placement).toEqual({
      direction: 'right',
      referenceTabId: CONTROLLER_TAB_ID,
    })

    usePanels.getState().requestDockPlacement('material:mat-1', {
      direction: 'below',
      referenceTabId: CONTROLLER_TAB_ID,
    })
    expect(usePanels.getState().tabs.find((t) => t.id === 'material:mat-1')?.placement?.direction).toBe('below')

    usePanels.getState().clearDockPlacement('material:mat-1')
    expect(usePanels.getState().tabs.find((t) => t.id === 'material:mat-1')?.placement).toBeUndefined()
  })

  it('keeps review materials multi-instance and re-focuses an existing material', () => {
    const { openTab } = usePanels.getState()
    const a = openTab({ type: 'review_material', id: 'mat_a' }, 'A 材料')
    const b = openTab({ type: 'review_material', id: 'mat_b' }, 'B 材料')
    expect([a, b]).toEqual(['review_material:mat_a', 'review_material:mat_b'])
    expect(usePanels.getState().tabs.filter((t) => t.ref.type === 'review_material')).toHaveLength(2)

    expect(openTab({ type: 'review_material', id: 'mat_a' }, 'A 材料')).toBe(a)
    expect(usePanels.getState().activeId).toBe(a)
    expect(usePanels.getState().tabs.filter((t) => t.ref.type === 'review_material')).toHaveLength(2)
  })

  it('keeps review queue single-instance while updating its focused material', () => {
    useReviewQueueFocus.setState({ focusedId: null, nonce: 0 })
    const { openTab } = usePanels.getState()
    expect(openTab({ type: 'review_queue', id: 'main' }, '审阅', 'mat_a')).toBe('review_queue:main')
    expect(openTab({ type: 'review_queue', id: 'main' }, '审阅', 'mat_b')).toBe('review_queue:main')
    expect(usePanels.getState().tabs.filter((t) => t.ref.type === 'review_queue')).toHaveLength(1)
    expect(useReviewQueueFocus.getState().focusedId).toBe('mat_b')
  })
})

describe('window-scoped tab snapshot v4', () => {
  beforeEach(() => {
    sessionStorage.removeItem(TAB_SNAPSHOT_KEY)
    localStorage.removeItem(TAB_SNAPSHOT_KEY)
    localStorage.removeItem('omni.cockpit.tabSnapshot.v3')
    usePanels.setState({ tabs: withDefaultTabs([]), activeId: PROJECT_BOARD_TAB_ID })
  })

  it('saves every open tab and its active id', () => {
    const tabs = withDefaultTabs([
      { id: 'review_material:m1', ref: { type: 'review_material', id: 'm1' }, title: '材料一' },
    ])
    saveTabSnapshot(tabs, 'review_material:m1')
    const snap = loadTabSnapshot()
    expect(snap.exists).toBe(true)
    expect(snap.activeId).toBe('review_material:m1')
    expect(snap.tabs.map((t) => t.id)).toEqual([
      PROJECT_BOARD_TAB_ID,
      QUEST_BOARD_TAB_ID,
      CONTROLLER_TAB_ID,
      'review_material:m1',
    ])
  })

  it('distinguishes no snapshot from an intentionally empty workspace', () => {
    expect(loadTabSnapshot()).toEqual({ exists: false, activeId: null, tabs: [] })
    saveTabSnapshot([], null)
    expect(loadTabSnapshot()).toEqual({ exists: true, activeId: null, tabs: [] })
  })

  it('does not import older shared snapshots into a new window', () => {
    localStorage.setItem('omni.cockpit.tabSnapshot.v3', JSON.stringify([
      { id: 'note:a', ref: { type: 'note', id: 'a' }, title: 'A' },
    ]))
    expect(loadTabSnapshot()).toEqual({ exists: false, activeId: null, tabs: [] })
  })

  it('keeps two window storage contexts independent across refresh', () => {
    saveTabSnapshot([
      { id: 'cc_session:a', ref: { type: 'cc_session', id: 'a' }, title: 'A' },
    ], 'cc_session:a', sessionStorage)
    saveTabSnapshot([
      { id: 'cc_session:b', ref: { type: 'cc_session', id: 'b' }, title: 'B' },
    ], 'cc_session:b', localStorage)
    expect(loadTabSnapshot(sessionStorage).activeId).toBe('cc_session:a')
    expect(loadTabSnapshot(localStorage).activeId).toBe('cc_session:b')
  })

  it('uses the last restored tab for an invalid active id and null for no tabs', () => {
    usePanels.getState().setTabs([
      { id: 'note:x', ref: { type: 'note', id: 'x' }, title: 'X' },
    ], 'note:gone')
    expect(usePanels.getState().activeId).toBe('note:x')
    usePanels.getState().setTabs([], 'note:gone')
    expect(usePanels.getState().activeId).toBeNull()
  })
})
