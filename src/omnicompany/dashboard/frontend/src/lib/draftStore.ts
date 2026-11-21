// 草稿仓 —— 写了一半的东西不许丢。
//
// 用户 2026-09-03 原话:「最令人痛苦的就是写了一半的方案丢了」。所以这不是某个
// 组件的实现细节, 是一层设施, 三条纪律写死在这里:
//
//   1. **本地先落**。每次输入 debounce 落 IndexedDB, 不等服务端。
//   2. **服务端确认前不清本地**。只有调用方明确 markSynced() 才删。请求失败、
//      页面被关、进程被杀 —— 本地那份都还在。
//   3. **恢复要问过人**。重开时发现本地比服务端新, 摆出来让人选恢复还是丢弃,
//      绝不自动覆盖当前内容(自动覆盖会把"刚从别处改好的"反向冲掉)。
//
// 诚实的边界: 硬杀进程没有任何钩子能跑, 所以**最坏会丢最近一次 debounce 窗口内
// 的输入**(默认 600ms)。页面正常关闭/切后台时会额外同步刷一次 localStorage 镜像,
// 因为 IndexedDB 是异步的, unload 阶段不保证写得完。
//
// 为什么 IndexedDB 而不是纯 localStorage: 画布与长文可能几 MB, localStorage 的
// 5MB 配额是整站共享的, 挤爆之后是静默失败 —— 恰恰是最不能静默失败的地方。

const DB_NAME = 'omni-drafts'
const DB_VERSION = 1
const STORE = 'drafts'
/** localStorage 镜像前缀。IndexedDB 不可用时是主存储, 可用时只做 unload 兜底。 */
const LS_PREFIX = 'omni.draft.v1:'

export interface DraftRecord<T = unknown> {
  key: string
  /** 人读的落点名字, 用在「有未提交草稿」的提示里。 */
  label: string
  value: T
  updatedAt: number
}

function lsKey(key: string): string {
  return LS_PREFIX + key
}

function browserStorage(): Storage | null {
  if (typeof window === 'undefined') return null
  try {
    return window.localStorage
  } catch {
    return null
  }
}

let dbPromise: Promise<IDBDatabase | null> | null = null

function openDb(): Promise<IDBDatabase | null> {
  if (dbPromise) return dbPromise
  dbPromise = new Promise((resolve) => {
    if (typeof indexedDB === 'undefined') { resolve(null); return }
    let req: IDBOpenDBRequest
    try {
      req = indexedDB.open(DB_NAME, DB_VERSION)
    } catch {
      resolve(null)
      return
    }
    req.onupgradeneeded = () => {
      const db = req.result
      if (!db.objectStoreNames.contains(STORE)) db.createObjectStore(STORE, { keyPath: 'key' })
    }
    req.onsuccess = () => resolve(req.result)
    // 隐私模式 / 磁盘满 / 被策略禁用: 退回 localStorage, 不让草稿功能整个失效。
    req.onerror = () => resolve(null)
    req.onblocked = () => resolve(null)
  })
  return dbPromise
}

function tx<T>(mode: IDBTransactionMode, run: (store: IDBObjectStore) => IDBRequest): Promise<T | null> {
  return openDb().then((db) => {
    if (!db) return null
    return new Promise<T | null>((resolve) => {
      let request: IDBRequest
      try {
        request = run(db.transaction(STORE, mode).objectStore(STORE))
      } catch {
        resolve(null)
        return
      }
      request.onsuccess = () => resolve(request.result as T)
      request.onerror = () => resolve(null)
    })
  })
}

/** localStorage 镜像 —— IndexedDB 不可用时是主存储, 可用时是 unload 兜底。 */
function writeMirror(rec: DraftRecord): void {
  try {
    browserStorage()?.setItem(lsKey(rec.key), JSON.stringify(rec))
  } catch {
    // 配额满就算了: IndexedDB 那份才是主力, 镜像丢了不影响恢复。
  }
}

function readMirror(key: string): DraftRecord | null {
  try {
    const raw = browserStorage()?.getItem(lsKey(key))
    if (!raw) return null
    const parsed = JSON.parse(raw)
    return parsed && typeof parsed === 'object' && 'value' in parsed ? (parsed as DraftRecord) : null
  } catch {
    return null
  }
}

function dropMirror(key: string): void {
  try {
    browserStorage()?.removeItem(lsKey(key))
  } catch {
    /* 读不到就当没有 */
  }
}

export async function putDraft<T>(key: string, label: string, value: T): Promise<DraftRecord<T>> {
  const rec: DraftRecord<T> = { key, label, value, updatedAt: Date.now() }
  writeMirror(rec as DraftRecord)
  await tx('readwrite', (store) => store.put(rec))
  return rec
}

/** 同步写。只给 pagehide / visibilitychange 用 —— 那时候异步写不保证跑得完。 */
export function putDraftSync<T>(key: string, label: string, value: T): void {
  writeMirror({ key, label, value, updatedAt: Date.now() })
}

export async function getDraft<T>(key: string): Promise<DraftRecord<T> | null> {
  const fromDb = await tx<DraftRecord<T>>('readonly', (store) => store.get(key))
  const mirror = readMirror(key) as DraftRecord<T> | null
  if (!fromDb) return mirror
  if (!mirror) return fromDb
  // 两边都有就取新的: 镜像是 unload 时刷的, 通常比库里那份更新。
  return mirror.updatedAt > fromDb.updatedAt ? mirror : fromDb
}

export async function deleteDraft(key: string): Promise<void> {
  dropMirror(key)
  await tx('readwrite', (store) => store.delete(key))
}

/** 列出所有未清掉的草稿 —— 给「你还有 N 份没提交」用。 */
export async function listDrafts(): Promise<DraftRecord[]> {
  const all = (await tx<DraftRecord[]>('readonly', (store) => store.getAll())) || []
  const seen = new Set(all.map((r) => r.key))
  const storage = browserStorage()
  if (storage) {
    for (let i = 0; i < storage.length; i += 1) {
      const k = storage.key(i)
      if (!k || !k.startsWith(LS_PREFIX)) continue
      const rec = readMirror(k.slice(LS_PREFIX.length))
      if (rec && !seen.has(rec.key)) all.push(rec)
    }
  }
  return all.sort((a, b) => b.updatedAt - a.updatedAt)
}

/* ── 未同步登记簿 ────────────────────────────────────────────────────────
 * 关页面前要拦一下。浏览器只认"有没有 beforeunload 且 preventDefault", 拦不拦
 * 由这里的计数说了算; 组件卸载必须注销, 否则会留下永远拦人的幽灵条目。 */

const unsynced = new Map<string, string>()
let guardInstalled = false

function onBeforeUnload(event: BeforeUnloadEvent): void {
  if (unsynced.size === 0) return
  event.preventDefault()
  // 现代浏览器忽略自定义文案, 但赋值仍是触发确认框的必要动作。
  event.returnValue = ''
}

function ensureGuard(): void {
  if (guardInstalled || typeof window === 'undefined') return
  window.addEventListener('beforeunload', onBeforeUnload)
  guardInstalled = true
}

export function markUnsynced(key: string, label: string): void {
  ensureGuard()
  unsynced.set(key, label)
}

export function clearUnsynced(key: string): void {
  unsynced.delete(key)
}

export function unsyncedDrafts(): { key: string; label: string }[] {
  return [...unsynced.entries()].map(([key, label]) => ({ key, label }))
}

/** 仅测试用: 把模块级状态清干净, 免得用例之间互相串。 */
export function __resetDraftGuardForTests(): void {
  unsynced.clear()
}
