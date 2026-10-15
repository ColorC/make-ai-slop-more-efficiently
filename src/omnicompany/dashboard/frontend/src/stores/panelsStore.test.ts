import { beforeEach, describe, expect, it } from 'vitest'
import {
  CONTROLLER_TAB_ID,
  PROJECT_BOARD_TAB_ID,
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

  it('starts with project and controller (quest_board 已于 M4 退役出默认页签) but does not pin them', () => {
    expect(boot.tabs).toEqual([PROJECT_BOARD_TAB_ID, CONTROLLER_TAB_ID])
    expect(usePanels.getState().tabs.every((t) => !t.pinned)).toBe(true)
    expect(boot.activeId).toBe(PROJECT_BOARD_TAB_ID)
  })

  it.each([PROJECT_BOARD_TAB_ID, CONTROLLER_TAB_ID])('allows closing %s', (id) => {
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

  it('merges the retired Walker reader into the canonical project wiki tab', () => {
    const id = usePanels.getState().openTab(
      { type: 'studio_reader', id: 'walker' },
      'walker 阅读',
    )

    expect(id).toBe('project:walker')
    expect(usePanels.getState().tabs.find((tab) => tab.id === id)).toMatchObject({
      ref: { type: 'project', id: 'walker' },
      title: 'Walker（行者无乡）',
    })
    expect(usePanels.getState().tabs.some((tab) => tab.id === 'studio_reader:walker')).toBe(false)
  })

  it('translates old Walker material facets into project Wiki document facets', () => {
    const id = usePanels.getState().openTab(
      { type: 'studio_reader', id: 'walker' },
      'walker 阅读',
      'material-wiki:walker.card.chiyanque-bloodthirst-slash',
    )

    expect(id).toBe('project:walker#wiki:walker.card.chiyanque-bloodthirst-slash')
    expect(usePanels.getState().tabs.find((tab) => tab.id === id)?.facet)
      .toBe('wiki:walker.card.chiyanque-bloodthirst-slash')
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

  it('uses the active tab as the default parent and inserts the child beside it', () => {
    usePanels.getState().setTabs([
      { id: 'note:source', ref: { type: 'note', id: 'source' }, title: 'Source' },
      { id: 'note:later', ref: { type: 'note', id: 'later' }, title: 'Later' },
    ], 'note:source')

    const child = usePanels.getState().openTab({ type: 'note', id: 'child' }, 'Child')

    expect(usePanels.getState().tabs.map((tab) => tab.id)).toEqual([
      'note:source',
      child,
      'note:later',
    ])
    expect(usePanels.getState().tabs.find((tab) => tab.id === child)?.returnTo?.id).toBe('note:source')
  })

  it('allows callers to explicitly open a root tab without a parent', () => {
    usePanels.getState().setTabs([
      { id: 'note:source', ref: { type: 'note', id: 'source' }, title: 'Source' },
      { id: 'note:later', ref: { type: 'note', id: 'later' }, title: 'Later' },
    ], 'note:source')

    const root = usePanels.getState().openTab({ type: 'note', id: 'root' }, 'Root', undefined, undefined, null)

    expect(usePanels.getState().tabs.map((tab) => tab.id)).toEqual(['note:source', 'note:later', root])
    expect(usePanels.getState().tabs.find((tab) => tab.id === root)?.returnTo).toBeUndefined()
  })

  it('keeps siblings in creation order and puts grandchildren inside the parent branch', () => {
    usePanels.getState().setTabs([
      { id: 'note:parent', ref: { type: 'note', id: 'parent' }, title: 'Parent' },
      { id: 'note:later', ref: { type: 'note', id: 'later' }, title: 'Later' },
    ], 'note:parent')
    const first = usePanels.getState().openTab(
      { type: 'note', id: 'first' },
      'First',
      undefined,
      undefined,
      'note:parent',
    )
    const grandchild = usePanels.getState().openTab({ type: 'note', id: 'grandchild' }, 'Grandchild')
    const second = usePanels.getState().openTab(
      { type: 'note', id: 'second' },
      'Second',
      undefined,
      undefined,
      'note:parent',
    )

    expect(usePanels.getState().tabs.map((tab) => tab.id)).toEqual([
      'note:parent',
      first,
      grandchild,
      second,
      'note:later',
    ])
  })

  it('returns to the parent when closing the active child', () => {
    usePanels.getState().setTabs([
      { id: 'note:parent', ref: { type: 'note', id: 'parent' }, title: 'Parent' },
    ], 'note:parent')
    const child = usePanels.getState().openTab({ type: 'note', id: 'child' }, 'Child')

    usePanels.getState().closeTab(child)

    expect(usePanels.getState().activeId).toBe('note:parent')
  })

  it('reconnects descendants to the grandparent when an intermediate parent closes', () => {
    usePanels.getState().setTabs([
      { id: 'note:root', ref: { type: 'note', id: 'root' }, title: 'Root' },
    ], 'note:root')
    const parent = usePanels.getState().openTab({ type: 'note', id: 'parent' }, 'Parent')
    const child = usePanels.getState().openTab({ type: 'note', id: 'child' }, 'Child')

    usePanels.getState().closeTab(parent)

    expect(usePanels.getState().tabs.find((tab) => tab.id === child)?.returnTo?.id).toBe('note:root')
    expect(usePanels.getState().activeId).toBe(child)
  })

  it('returns to an existing source tab', () => {
    const source = usePanels.getState().openTab({ type: 'cc_session', id: 'chat-1' }, '会话一')
    const wiki = usePanels.getState().openTab(
      { type: 'studio_reader', id: 'omnicompany' },
      'omnicompany 阅读',
      undefined,
      undefined,
      source,
    )

    expect(usePanels.getState().tabs.find((tab) => tab.id === wiki)?.returnTo?.id).toBe(source)
    expect(usePanels.getState().returnFromTab(wiki)).toBe(source)
    expect(usePanels.getState().activeId).toBe(source)
  })

  it('reopens a closed source tab when returning', () => {
    const source = usePanels.getState().openTab(
      { type: 'project', id: 'walker' },
      'Walker',
      undefined,
      undefined,
      null,
    )
    const target = usePanels.getState().openTab(
      { type: 'review_material', id: 'mat-1' },
      '材料一',
      undefined,
      undefined,
      source,
    )
    usePanels.getState().closeTab(source)

    expect(usePanels.getState().returnFromTab(target)).toBe(source)
    expect(usePanels.getState().tabs.find((tab) => tab.id === source)?.title).toBe('Walker')
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

  it('closes every other tab and keeps only the requested one', () => {
    const { openTab, closeOtherTabs } = usePanels.getState()
    const keep = openTab({ type: 'cc_session', id: 'chat-keep' }, 'keep')
    openTab({ type: 'review_material', id: 'mat-extra' }, 'extra')
    openTab({ type: 'cc_session', id: 'chat-keep' }, 'keep')

    closeOtherTabs(keep)

    expect(usePanels.getState().tabs.map((tab) => tab.id)).toEqual([keep])
    expect(usePanels.getState().activeId).toBe(keep)
  })

  it('defaults closeOtherTabs to the active tab', () => {
    const { openTab, closeOtherTabs } = usePanels.getState()
    openTab({ type: 'project_board', id: 'main' }, '项目')
    const keep = openTab({ type: 'cc_session', id: 'chat-active' }, 'active')
    openTab({ type: 'controller', id: 'main' }, '总控')
    usePanels.getState().activate(keep)

    closeOtherTabs()

    expect(usePanels.getState().tabs.map((tab) => tab.id)).toEqual([keep])
    expect(usePanels.getState().activeId).toBe(keep)
  })

  it('does not invent default tabs when closing others', () => {
    usePanels.getState().setTabs([
      { id: 'cc_session:only', ref: { type: 'cc_session', id: 'only' }, title: 'only' },
      { id: 'review_material:extra', ref: { type: 'review_material', id: 'extra' }, title: 'extra' },
    ], 'cc_session:only')

    usePanels.getState().closeOtherTabs('cc_session:only')

    expect(usePanels.getState().tabs.map((tab) => tab.id)).toEqual(['cc_session:only'])
    expect(usePanels.getState().tabs.some((tab) => tab.id === PROJECT_BOARD_TAB_ID)).toBe(false)
    expect(usePanels.getState().tabs.some((tab) => tab.id === CONTROLLER_TAB_ID)).toBe(false)
  })

  it('leaves the workspace unchanged when closeOtherTabs has no keep target', () => {
    usePanels.getState().setTabs([
      { id: 'cc_session:a', ref: { type: 'cc_session', id: 'a' }, title: 'a' },
    ], 'cc_session:a')
    const before = usePanels.getState()

    usePanels.getState().closeOtherTabs('missing:tab')

    expect(usePanels.getState().tabs).toEqual(before.tabs)
    expect(usePanels.getState().activeId).toBe(before.activeId)
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
      CONTROLLER_TAB_ID,
      'review_material:m1',
    ])
  })

  it('migrates a saved Walker reader tab into the single project wiki tab', () => {
    const tabs = [
      { id: 'project:walker', ref: { type: 'project' as const, id: 'walker' }, title: 'Walker' },
      {
        id: 'studio_reader:walker',
        ref: { type: 'studio_reader' as const, id: 'walker' },
        title: 'Walker 阅读',
        returnTo: { id: 'project:walker', ref: { type: 'project' as const, id: 'walker' }, title: 'Walker' },
      },
    ]
    saveTabSnapshot(tabs, 'studio_reader:walker')
    const snapshot = loadTabSnapshot()
    usePanels.getState().setTabs(snapshot.tabs, snapshot.activeId)
    expect(usePanels.getState().tabs).toEqual([expect.objectContaining({
      id: 'project:walker',
      ref: { type: 'project', id: 'walker' },
      title: 'Walker（行者无乡）',
      returnTo: undefined,
    })])
    expect(usePanels.getState().activeId).toBe('project:walker')
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
