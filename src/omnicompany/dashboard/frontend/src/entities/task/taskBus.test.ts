// @ts-expect-error 本用例在 Node 下跑源码级断言; 浏览器 bundle 不含 node 类型声明。
import { readFileSync } from 'node:fs'
// @ts-expect-error 仅测试用, 不进浏览器。
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'
import {
  type BusTask, type Board, type GroupBy, type Panel,
  type TaskProfile,
  EMPTY_FILTERS, INBOX_KEY, buildPanels, buildTree, dropWriteFor, dueBucketOf, dueLabel, dueState,
  inboxPanel, isInbox, laneOf, matchesFilters, orderKeyForIndex, parseDueToken, parseQuickAdd,
  reorderTasks, retiredPanel,
  setTaskProfile, sortTasks, subtaskProgress, tierLabel, tiers,
} from './taskBus'

declare const process: { cwd(): string }

function task(over: Partial<BusTask> & { id: string }): BusTask {
  return {
    title: over.id,
    stateKey: 'todo',
    projectId: null,
    parentTaskId: null,
    orderKey: null,
    archivedAt: null,
    revision: 1,
    locationLinks: [],
    ...over,
  }
}

describe('泳道映射', () => {
  it('paused / blocked 合并进挂起道', () => {
    expect(laneOf(task({ id: 'a', stateKey: 'paused' }))).toBe('hold')
    expect(laneOf(task({ id: 'b', stateKey: 'blocked' }))).toBe('hold')
  })

  it('未知 stateKey 按 todo 聚合（与后端 open 兜底同语义）', () => {
    expect(laneOf(task({ id: 'c', stateKey: 'wat' }))).toBe('todo')
    expect(laneOf(task({ id: 'd', stateKey: 'cancelled' }))).toBe('todo')
  })
})

describe('排序', () => {
  it('手动序: orderKey 字典序在前, NULL 最后, 回退 createdAt', () => {
    const out = sortTasks([
      task({ id: 'none-later', createdAt: '2026-02-01' }),
      task({ id: 'b', orderKey: 'b' }),
      task({ id: 'none-early', createdAt: '2026-01-01' }),
      task({ id: 'a', orderKey: 'a' }),
    ], 'manual')
    expect(out.map((t) => t.id)).toEqual(['a', 'b', 'none-early', 'none-later'])
  })

  it('截止序: 无截止的排最后', () => {
    const out = sortTasks([
      task({ id: 'no-due' }),
      task({ id: 'late', dueAt: '2026-09-30T00:00:00.000Z' }),
      task({ id: 'soon', dueAt: '2026-09-02T00:00:00.000Z' }),
    ], 'due')
    expect(out.map((t) => t.id)).toEqual(['soon', 'late', 'no-due'])
  })

  it('优先级序: 数字越大越靠前, null 最后', () => {
    const out = sortTasks([
      task({ id: 'p0', priority: 0 }),
      task({ id: 'none' }),
      task({ id: 'p3', priority: 3 }),
    ], 'priority')
    expect(out.map((t) => t.id)).toEqual(['p3', 'p0', 'none'])
  })

  it('组内拖拽支持目标前/后插入，并生成可持久化的稀疏顺序键', () => {
    const tasks = [task({ id: 'a' }), task({ id: 'b' }), task({ id: 'c' })]
    expect(reorderTasks(tasks, 'c', 'a', 'before').map((item) => item.id)).toEqual(['c', 'a', 'b'])
    expect(reorderTasks(tasks, 'a', 'c', 'after').map((item) => item.id)).toEqual(['b', 'c', 'a'])
    expect(reorderTasks(tasks, 'missing', 'b')).toEqual(tasks)
    expect(orderKeyForIndex(0)).toBe('ui-00000000')
    expect(orderKeyForIndex(2) < orderKeyForIndex(3)).toBe(true)
  })
})

describe('父子树', () => {
  it('按 parentTaskId 缩进, 深度封顶', () => {
    const nodes = buildTree([
      task({ id: 'root', orderKey: 'a' }),
      task({ id: 'kid', parentTaskId: 'root', orderKey: 'a' }),
      task({ id: 'grand', parentTaskId: 'kid', orderKey: 'a' }),
      task({ id: 'great', parentTaskId: 'grand', orderKey: 'a' }),
      task({ id: 'great2', parentTaskId: 'great', orderKey: 'a' }),
    ], 'manual')
    expect(nodes.map((n) => [n.task.id, n.depth])).toEqual([
      ['root', 0], ['kid', 1], ['grand', 2], ['great', 3], ['great2', 3],
    ])
  })

  it('父不在结果集里的孤儿按根渲染, 不被吞掉', () => {
    const nodes = buildTree([task({ id: 'orphan', parentTaskId: 'filtered-out' })], 'manual')
    expect(nodes).toEqual([{ task: expect.objectContaining({ id: 'orphan' }), depth: 0 }])
  })

  it('自指/成环不死循环', () => {
    const nodes = buildTree([
      task({ id: 'x', parentTaskId: 'y' }),
      task({ id: 'y', parentTaskId: 'x' }),
    ], 'manual')
    expect(nodes.length).toBe(2)
  })

  it('子任务进度只看直接子任务', () => {
    const all = [
      task({ id: 'p' }),
      task({ id: 'c1', parentTaskId: 'p', stateKey: 'completed' }),
      task({ id: 'c2', parentTaskId: 'p' }),
    ]
    expect(subtaskProgress(all[0], all)).toEqual({ done: 1, total: 2 })
    expect(subtaskProgress(all[1], all)).toBeNull()
  })
})

describe('通用分组引擎', () => {
  const board: Board = {
    ok: true,
    lanes: [],
    projects: [
      {
        projectId: 'alpha',
        title: 'alpha',
        tasks: [
          task({ id: 'a1', title: '修登录', projectId: 'alpha', priority: 3 }),
          task({ id: 'a2', title: '写文档', projectId: 'alpha', stateKey: 'completed' }),
          task({ id: 'a3', title: '弃掉的', projectId: 'alpha', stateKey: 'cancelled' }),
        ],
      },
      {
        projectId: 'beta',
        title: 'beta',
        tasks: [
          task({ id: 'b1', title: '归档的', projectId: 'beta', archivedAt: '2026-08-01' }),
          task({ id: 'b2', title: '阻塞的', projectId: 'beta', stateKey: 'blocked' }),
        ],
      },
    ],
  }
  const pick = (panels: Panel[], key: string): Panel => panels.find((p) => p.key === key)!
  const group = (by: GroupBy) => buildPanels(board, EMPTY_FILTERS, 'manual', by).panels

  it('cancelled 与 archived 退出主体, 进归档区', () => {
    const { panels, retired } = buildPanels(board, EMPTY_FILTERS, 'manual', 'project')
    expect(panels.map((p) => p.key)).toEqual(['alpha', 'beta'])
    expect(pick(panels, 'alpha').tasks.map((t) => t.id)).toEqual(['a1', 'a2'])
    expect(pick(panels, 'alpha').open).toBe(1)
    expect(retired.map((t) => t.id).sort()).toEqual(['a3', 'b1'])
  })

  it('同一份数据换分组维度就换一组面板（面板通用，渲染代码不变）', () => {
    const byState = group('state')
    expect(byState.map((p) => p.key)).toEqual(['todo', 'in-progress', 'in-review', 'hold', 'completed'])
    expect(pick(byState, 'hold').tasks.map((t) => t.id)).toEqual(['b2'])
    expect(pick(byState, 'completed').tasks.map((t) => t.id)).toEqual(['a2'])

    const byPriority = group('priority')
    expect(pick(byPriority, 'p3').tasks.map((t) => t.id)).toEqual(['a1'])
    expect(pick(byPriority, 'pnone').tasks.map((t) => t.id).sort()).toEqual(['a2', 'b2'])
  })

  it('状态/优先级/项目面板都可落; 只有截止分组只读并带一句话理由', () => {
    // 2026-09-01 更正：项目原本被判成只读是因为 RPC zod 与 CLI spec 两层壳没开口，
    // 宿主 LocalTaskPatch/PATCH_COLUMNS 一直带 project_id。壳补上后项目也可拖。
    expect(group('state').every((p) => p.droppable)).toBe(true)
    expect(group('priority').every((p) => p.droppable)).toBe(true)
    expect(group('project').every((p) => p.droppable)).toBe(true)
    expect(group('due').every((p) => !p.droppable && Boolean(p.readonlyReason))).toBe(true)
  })

  it('一个项目都没有时也要留一个面板 —— 否则连 + 都没得点', () => {
    const { panels } = buildPanels({ ok: true, lanes: [], projects: [] }, EMPTY_FILTERS, 'manual', 'project')
    expect(panels).toHaveLength(1)
    expect(panels[0].seed).toEqual({ projectId: null })
  })

  it('面板 seed = 在这个面板里新建时的落点', () => {
    const byState = group('state')
    expect(pick(byState, 'in-progress').seed).toEqual({ stateKey: 'in-progress' })
    // 挂起道默认落 paused：blocked 是「被外部挡住」，不该由拖拽/新建替用户断言。
    expect(pick(byState, 'hold').seed).toEqual({ stateKey: 'paused' })
    expect(pick(group('priority'), 'p2').seed).toEqual({ priority: 2 })
  })

  it('搜索命中标题/描述/id', () => {
    const t = task({ id: 'zz', title: '修登录', description: '跳转坏了' })
    expect(matchesFilters(t, { ...EMPTY_FILTERS, query: '登录' })).toBe(true)
    expect(matchesFilters(t, { ...EMPTY_FILTERS, query: '跳转' })).toBe(true)
    expect(matchesFilters(t, { ...EMPTY_FILTERS, query: 'ZZ' })).toBe(true)
    expect(matchesFilters(t, { ...EMPTY_FILTERS, query: '别的' })).toBe(false)
  })

  it('项目筛选把 null projectId 归到未分组', () => {
    const t = task({ id: 'n' })
    expect(matchesFilters(t, { ...EMPTY_FILTERS, projects: ['未分组'] })).toBe(true)
    expect(matchesFilters(t, { ...EMPTY_FILTERS, projects: ['alpha'] })).toBe(false)
  })
})

describe('拖到面板要写什么', () => {
  const emptyBoard: Board = { ok: true, lanes: [], projects: [{ projectId: 'p', title: 'p', tasks: [] }] }
  const pick = (by: GroupBy, key: string): Panel => (
    buildPanels(emptyBoard, EMPTY_FILTERS, 'manual', by).panels.find((p) => p.key === key)!
  )

  it('拖进状态道 = status set', () => {
    expect(dropWriteFor(pick('state', 'in-progress'), task({ id: 'x' })))
      .toEqual({ kind: 'status', stateKey: 'in-progress' })
  })

  it('已经在这条道里就不写 —— blocked 拖回挂起道不该被悄悄降级成 paused', () => {
    expect(dropWriteFor(pick('state', 'hold'), task({ id: 'x', stateKey: 'blocked' }))).toBeNull()
  })

  it('拖进优先级面板 = update priority; 同级不写', () => {
    expect(dropWriteFor(pick('priority', 'p2'), task({ id: 'x' })))
      .toEqual({ kind: 'update', priority: 2 })
    expect(dropWriteFor(pick('priority', 'p2'), task({ id: 'x', priority: 2 }))).toBeNull()
  })

  it('拖进项目面板 = update projectId; 同项目不写', () => {
    expect(dropWriteFor(pick('project', 'p'), task({ id: 'x', projectId: 'other' })))
      .toEqual({ kind: 'update', projectId: 'p' })
    expect(dropWriteFor(pick('project', 'p'), task({ id: 'x', projectId: 'p' }))).toBeNull()
  })

  it('截止分组仍然只读（落到桶里的哪一天是猜的）', () => {
    const due = buildPanels(emptyBoard, EMPTY_FILTERS, 'manual', 'due').panels.find((p) => p.key === 'today')!
    expect(dropWriteFor(due, task({ id: 'x' }))).toBeNull()
  })

  it('拖进归档面板 = archive; 已归档的再拖进去不写', () => {
    const archive = retiredPanel([], 'manual')
    expect(dropWriteFor(archive, task({ id: 'x' }))).toEqual({ kind: 'archive' })
    expect(dropWriteFor(archive, task({ id: 'x', archivedAt: '2026-08-01' }))).toBeNull()
  })

  it('从归档往正常面板拖 = restore（恢复后再落目标维度）', () => {
    expect(dropWriteFor(pick('state', 'todo'), task({ id: 'x', archivedAt: '2026-08-01' })))
      .toEqual({ kind: 'restore' })
    expect(dropWriteFor(pick('state', 'todo'), task({ id: 'x', stateKey: 'cancelled' })))
      .toEqual({ kind: 'restore' })
  })
})

describe('截止判定与分桶', () => {
  const now = Date.parse('2026-09-01T10:00:00.000Z')

  it('逾期 / 三天内 / 更远 / 未设', () => {
    expect(dueState('2026-08-31T00:00:00.000Z', now)).toBe('overdue')
    expect(dueState('2026-09-02T00:00:00.000Z', now)).toBe('soon')
    expect(dueState('2026-09-20T00:00:00.000Z', now)).toBe('later')
    expect(dueState(null, now)).toBe('none')
    expect(dueState('不是日期', now)).toBe('none')
  })

  it('标注是短句', () => {
    // dueLabel 按"本地自然日"读, 故基准也用本地时间构造(UTC 字面量会跨日)。
    expect(dueLabel(new Date(2026, 8, 1, 23, 59).toISOString(), now)).toBe('今天')
    expect(dueLabel(new Date(2026, 8, 2, 23, 59).toISOString(), now)).toBe('明天')
    expect(dueLabel(new Date(2026, 7, 30, 23, 59).toISOString(), now)).toBe('逾期 2d')
    expect(dueLabel(null, now)).toBe('')
  })

  it('分桶: 逾期 / 今天 / 7 天内 / 以后 / 无截止', () => {
    expect(dueBucketOf(task({ id: 'a' }), now)).toBe('none')
    expect(dueBucketOf(task({ id: 'b', dueAt: '2026-08-20T00:00:00.000Z' }), now)).toBe('overdue')
    expect(dueBucketOf(task({ id: 'c', dueAt: new Date(2026, 8, 1, 23, 59).toISOString() }), now)).toBe('today')
    expect(dueBucketOf(task({ id: 'd', dueAt: new Date(2026, 8, 5, 23, 59).toISOString() }), now)).toBe('week')
    expect(dueBucketOf(task({ id: 'e', dueAt: new Date(2026, 9, 20, 23, 59).toISOString() }), now)).toBe('later')
  })
})

describe('新建行内联语法', () => {
  const projects = [{ id: 'omnicompany', label: 'omnicompany' }, { id: 'litework', label: 'litework' }]
  const now = Date.parse('2026-09-01T10:00:00.000Z')

  it('抽出项目 / 优先级 / 截止, 标题干净', () => {
    const out = parseQuickAdd('修好登录跳转 #omni !2 @明天', projects, now)
    expect(out.title).toBe('修好登录跳转')
    expect(out.projectId).toBe('omnicompany')
    expect(out.priority).toBe(2)
    expect(out.dueAt).not.toBeNull()
  })

  it('匹配不到的 #项目 原样留在标题里, 绝不静默吞字', () => {
    const out = parseQuickAdd('查一下 #不存在的项目', projects, now)
    expect(out.title).toBe('查一下 #不存在的项目')
    expect(out.projectId).toBeNull()
    expect(out.unmatchedProject).toBe('不存在的项目')
  })

  it('解释不了的 @token 也留在标题里', () => {
    const out = parseQuickAdd('讨论 @某人', projects, now)
    expect(out.title).toBe('讨论 @某人')
    expect(out.dueAt).toBeNull()
  })

  it('!超范围 不当优先级处理', () => {
    const out = parseQuickAdd('紧急 !9', projects, now)
    expect(out.priority).toBeNull()
    expect(out.title).toBe('紧急 !9')
  })

  it('日期 token: 今天/明天/周几/MM-DD/YYYY-MM-DD', () => {
    expect(parseDueToken('今天', now)).not.toBeNull()
    expect(parseDueToken('tomorrow', now)).not.toBeNull()
    expect(parseDueToken('周三', now)).not.toBeNull()
    expect(parseDueToken('12-25', now)).not.toBeNull()
    expect(parseDueToken('2027-01-05', now)).not.toBeNull()
    expect(parseDueToken('2-31', now)).toBeNull()
    expect(parseDueToken('随便', now)).toBeNull()
  })
})

/* ── 样式闸门: 蓝图口径靠机器盯着, 不靠人自觉（spec §10.3） ───────────────── */

describe('task.css 蓝图口径', () => {
  const css = readFileSync(resolve(process.cwd(), 'src/entities/task/task.css'), 'utf8')
  const declarations = (prop: string): string[] => (
    [...css.matchAll(new RegExp(`(?<![-\\w])${prop}\\s*:\\s*([^;}]+)`, 'g'))].map((m) => m[1].trim())
  )

  it('圆角只走 token（制图感 = 锐利, 不出现 4/6/8px 网页圆角）', () => {
    const bad = declarations('border-radius').filter((v) => !/var\(--fp-/.test(v) && !/^(0|50%)$/.test(v))
    expect(bad).toEqual([])
  })

  it('字号只走 --fp-fs-*（12px 是地板, 不写字面量）', () => {
    const bad = declarations('font-size').filter((v) => !/var\(--fp-fs-/.test(v))
    expect(bad).toEqual([])
  })

  it('零硬编码色值（一切颜色走 --fp-* token）', () => {
    expect(css.match(/#[0-9a-fA-F]{3,8}\b/g)).toBeNull()
    expect(css.match(/\brgba?\(/g)).toBeNull()
  })

  it('字重封顶 500（层级靠字号与字距, 不靠加粗）', () => {
    const bad = declarations('font-weight').filter((v) => {
      const n = Number(v)
      return /bold/i.test(v) || (Number.isFinite(n) && n > 500)
    })
    expect(bad).toEqual([])
  })
})

/* ── 个人附加层：词表只有服务端一份 ──────────────────────────────────── */

const PROFILE_PATH = resolve(process.cwd(), '../../../../data/task_core_profile.json')
const PROFILE = JSON.parse(readFileSync(PROFILE_PATH, 'utf8')) as TaskProfile

describe('优先度阶梯（个人附加层词表）', () => {
  it('没注入词表时退化成裸数字, 不编造名字', () => {
    setTaskProfile(null)
    expect(tierLabel(5)).toBe('P5')
    expect(tierLabel(null)).toBe('未定级')
    expect(tiers().map((t) => t.priority)).toEqual([5, 4, 3, 2, 1, 0])
  })

  it('注入后名字与判据来自词表, 且高在前', () => {
    setTaskProfile(PROFILE)
    expect(tiers().map((t) => t.priority)).toEqual([5, 4, 3, 2, 1, 0])
    expect(tierLabel(5)).toBe('紧急处理单')
    expect(tierLabel(4)).toBe('公司排期单')
    expect(tiers()[0].criterion).not.toBe('')
    setTaskProfile(null)
  })

  it('分组桶跟着词表走 —— 换词表不用改前端', () => {
    setTaskProfile(PROFILE)
    const board: Board = {
      ok: true,
      lanes: [],
      projects: [{ projectId: 'p', title: 'p', tasks: [task({ id: 'hot', priority: 5 })] }],
    }
    const panels = buildPanels(board, EMPTY_FILTERS, 'manual', 'priority').panels
    expect(panels.map((p) => p.key)).toEqual(['hotfix', 'company-scheduled', 'capability-fast',
      'platform-longrun', 'big-interest', 'long-tail', 'pnone'])
    // 组头用**短标**: 长名(「长远升级·重兴趣」那种)是判据不是档位名, 不该当标签摆
    // (用户 2026-09-04)。数字也不显示(2026-09-03)。
    expect(panels[0].title).toBe('紧急')
    expect(panels[0].tasks.map((t) => t.id)).toEqual(['hot'])
    setTaskProfile(null)
  })

  it('前端源码里不得出现第二份阶梯词表（真源只有 data/task_core_profile.json）', () => {
    // 注释里举例子提到阶梯名不算"抄词表"—— 门禁只看真会上屏的那部分, 所以先剥注释行。
    const stripComments = (text: string) => text
      .split(String.fromCharCode(10))
      .filter((line) => !/^\s*(\/\/|\*|\/\*)/.test(line))
      .join(String.fromCharCode(10))
    const sources = ['taskBus.ts', 'TaskPanel.tsx', 'TaskItem.tsx', 'TaskSheet.tsx', 'TaskComposer.tsx']
      .map((f) => stripComments(readFileSync(resolve(process.cwd(), 'src/entities/task', f), 'utf8')))
      .join(String.fromCharCode(10))
    const leaked = PROFILE.priorityLadder.map((t) => t.name).filter((name) => sources.includes(name))
    expect(leaked).toEqual([])
  })

  it('速记语法认到 !5（阶梯占 0–5, 不是旧的 0–3）', () => {
    expect(parseQuickAdd('救火 !5', []).priority).toBe(5)
    expect(parseQuickAdd('排期 !4', []).priority).toBe(4)
    expect(parseQuickAdd('不是优先级 !9', []).title).toBe('不是优先级 !9')
  })
})

describe('项目之间按整体优先级排（用户 2026-09-04：聚合仍是项目, 只是项目按优先级排）', () => {
  const board: Board = {
    ok: true,
    lanes: [],
    projects: [
      { projectId: 'low', title: 'low', tasks: [task({ id: 'l1', projectId: 'low', priority: 1 })] },
      {
        projectId: 'hot',
        title: 'hot',
        tasks: [
          task({ id: 'h1', projectId: 'hot', priority: 5 }),
          task({ id: 'h0', projectId: 'hot', priority: 0 }),
        ],
      },
      { projectId: 'mid', title: 'mid', tasks: [task({ id: 'm1', projectId: 'mid', priority: 3 })] },
      { projectId: 'none', title: 'none', tasks: [task({ id: 'n1', projectId: 'none' })] },
    ],
  }

  it('项目排位 = 它里面还欠着的最高一级', () => {
    const { panels } = buildPanels(board, EMPTY_FILTERS, 'manual', 'project')
    expect(panels.map((p) => p.key)).toEqual(['hot', 'mid', 'low', 'none'])
  })

  it('已完成的高优不把项目顶上去 —— 只看还欠着的', () => {
    const done: Board = {
      ok: true,
      lanes: [],
      projects: [
        {
          projectId: 'finished',
          title: 'finished',
          tasks: [task({ id: 'f1', projectId: 'finished', priority: 5, stateKey: 'completed' })],
        },
        { projectId: 'live', title: 'live', tasks: [task({ id: 'v1', projectId: 'live', priority: 2 })] },
      ],
    }
    expect(buildPanels(done, EMPTY_FILTERS, 'manual', 'project').panels.map((p) => p.key))
      .toEqual(['live', 'finished'])
  })

  it('同级项目保持宿主给的原顺序 —— 不无谓重排', () => {
    const tie: Board = {
      ok: true,
      lanes: [],
      projects: [
        { projectId: 'b', title: 'b', tasks: [task({ id: 'b1', projectId: 'b', priority: 2 })] },
        { projectId: 'a', title: 'a', tasks: [task({ id: 'a1', projectId: 'a', priority: 2 })] },
      ],
    }
    expect(buildPanels(tie, EMPTY_FILTERS, 'manual', 'project').panels.map((p) => p.key))
      .toEqual(['b', 'a'])
  })
})

describe('收件箱（提单子）', () => {
  const board: Board = {
    ok: true,
    lanes: [],
    projects: [{
      projectId: 'p',
      title: 'p',
      tasks: [
        task({ id: 'note1', stateKey: 'inbox' }),
        task({ id: 'note2', stateKey: 'inbox' }),
        task({ id: 'gone', stateKey: 'inbox', archivedAt: '2026-09-01' }),
        task({ id: 'real', stateKey: 'todo' }),
      ],
    }],
  }

  it('单子不进主视野, 也不进 active —— 记下来不等于催办', () => {
    const { panels, inbox, active } = buildPanels(board, EMPTY_FILTERS, 'manual', 'project')
    expect(inbox.map((t) => t.id)).toEqual(['note1', 'note2'])
    expect(active.map((t) => t.id)).toEqual(['real'])
    expect(panels.flatMap((p) => p.tasks.map((t) => t.id))).toEqual(['real'])
  })

  it('归档优先于收件箱 —— 归档掉的单子算归档, 不算待分诊', () => {
    const { inbox, retired } = buildPanels(board, EMPTY_FILTERS, 'manual', 'project')
    expect(isInbox(task({ id: 'x', stateKey: 'inbox', archivedAt: '2026-09-01' }))).toBe(false)
    expect(inbox.map((t) => t.id)).not.toContain('gone')
    expect(retired.map((t) => t.id)).toContain('gone')
  })

  it('按状态分组时收件箱也不混进泳道', () => {
    const byState = buildPanels(board, EMPTY_FILTERS, 'manual', 'state').panels
    expect(byState.map((p) => p.key)).toEqual(['todo', 'in-progress', 'in-review', 'hold', 'completed'])
    expect(byState.flatMap((p) => p.tasks.map((t) => t.id))).toEqual(['real'])
  })

  it('收件箱面板: 拖进去 = 撤销分诊, 拖出来 = 分诊', () => {
    const { inbox } = buildPanels(board, EMPTY_FILTERS, 'manual', 'project')
    const panel = inboxPanel(inbox, 'manual')
    expect(panel.key).toBe(INBOX_KEY)
    expect(panel.addable).toBe(true)
    // 已经在收件箱里的不重复写
    expect(dropWriteFor(panel, task({ id: 'note1', stateKey: 'inbox' }))).toBeNull()
    expect(dropWriteFor(panel, task({ id: 'real', stateKey: 'todo' })))
      .toEqual({ kind: 'status', stateKey: 'inbox' })
  })
})
